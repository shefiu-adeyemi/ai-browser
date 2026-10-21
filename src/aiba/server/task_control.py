"""
One place that owns "is the agent running, can I start/stop/continue it".

Before this, that state lived in loose globals mutated from several places, which caused:
  * two quick submits both starting an agent (the "running" flag was set inside the task,
    after it had been scheduled);
  * /continue tasks that /stop could not cancel (the handle was never stored);
  * "continue" prompts nesting inside each other (the original task was overwritten);
  * /stop returning before the run had actually ended.

Everything here is synchronous where it must be (claiming the slot) so the asyncio event
loop can never interleave two claims.
"""

import asyncio
import logging
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)


class Busy(Exception):
    """A run can't be started right now (already running, or paused and waiting)."""


class TaskController:
    def __init__(self, stop_timeout: float = 15.0) -> None:
        self.running = False
        self.paused = False
        self.origin_task = ""          # what the USER asked; never a "continue ..." wrapper
        self.fmt = "auto"
        self.tier: Optional[str] = None     # the model tier the user chose for this task (None = automatic)
        self._task: Optional[asyncio.Task] = None
        self._stop_timeout = stop_timeout
        self._stop_requested = False

    @property
    def task(self) -> Optional[asyncio.Task]:
        """The asyncio task of the current (or last) run, so a caller can be told when it ends however it ends."""
        return self._task

    @property
    def stop_requested(self) -> bool:
        """True from the moment Stop is pressed until the next run starts. A run uses this to
        tell "I was stopped" apart from "I failed": browser-use's own cleanup can raise an
        unrelated error while unwinding a cancel that arrives during start-up, and that error
        must not be reported as a failure."""
        return self._stop_requested

    # ------------------------------------------------------------------- start
    def start(self, run: Callable[[], Awaitable[None]], *, origin: str, fmt: str, tier: Optional[str] = None) -> None:
        """Claim the slot and launch `run()`. The claim happens NOW, before the task is
        scheduled, so two calls in a row cannot both succeed."""
        if self.running:
            raise Busy("agent already running")
        if self.paused:
            raise Busy("task paused — use Continue or Stop first")
        self.running, self.origin_task, self.fmt, self.tier = True, origin, fmt, tier
        self._launch(run)

    def resume(self, run: Callable[[], Awaitable[None]]) -> None:
        """Continue the paused task. Keeps the ORIGINAL task text (no nesting) and stores
        the new run's handle so Stop can cancel it."""
        if self.running:
            raise Busy("agent already running")
        if not (self.paused and self.origin_task):
            raise Busy("no paused task")
        self.running, self.paused = True, False
        self._launch(run)

    def _launch(self, run: Callable[[], Awaitable[None]]) -> None:
        self._stop_requested = False
        self._task = asyncio.get_running_loop().create_task(self._guard(run))

    async def _guard(self, run: Callable[[], Awaitable[None]]) -> None:
        try:
            await run()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("agent run crashed")
        finally:
            # Whatever happened (finished, failed, cancelled), the slot is freed.
            self.running = False

    # -------------------------------------------------------------------- state
    def pause(self) -> None:
        """Called by the run itself when it hit the step limit. The run is over as far as the
        user is concerned, so free the slot now: the UI is about to show "paused" and an
        immediate Continue click must not be refused as "already running"."""
        self.paused = True
        self.running = False

    def clear(self) -> None:
        """Forget any paused task (new chat, discard)."""
        self.paused = False

    # --------------------------------------------------------------------- stop
    async def stop(self) -> str:
        """Cancel the run and WAIT until it has really ended.
        Returns "stopped", "discarded" (it was only paused) or "idle" (nothing to stop)."""
        task = self._task
        if self.running and task is not None and not task.done():
            self._stop_requested = True
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=self._stop_timeout)
            if not done:
                log.error("agent run did not stop within %ss; releasing the slot anyway",
                          self._stop_timeout)
            self.running = False
            self.paused = False
            return "stopped"
        if self.paused:
            self.paused = False
            return "discarded"
        return "idle"
