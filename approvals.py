"""
Human-in-the-loop approvals.

The agent asks a person before doing something risky or irreversible; nothing
happens until they answer. Two front-ends share one interface (`ask()`):
  * ApprovalBroker  - the web UI: pushes a request over the WebSocket and waits
                      for POST /approvals/{id}.
  * ConsoleApprover - the command-line scripts: asks on the terminal.
Both fail closed: no person available, no answer in time, or any error = "no".
"""

import asyncio
import secrets
from typing import Awaitable, Callable, Optional


class ApprovalBroker:
    def __init__(
        self,
        broadcast: Callable[[dict], Awaitable[None]],
        has_clients: Callable[[], bool],
        timeout: float = 180.0,
    ) -> None:
        self._broadcast   = broadcast
        self._has_clients = has_clients
        self.timeout      = timeout
        self._pending: dict[str, asyncio.Future] = {}
        self._messages: dict[str, dict] = {}     # so a refreshed page can re-show the prompt

    async def ask(self, kind: str, title: str, detail: str = "", url: str = "") -> bool:
        """kind: "approve" (allow/deny an action) or "handoff" (user does something, then continues)."""
        if not self._has_clients():
            return False                                   # nobody to ask -> deny
        aid = secrets.token_hex(8)
        fut = asyncio.get_running_loop().create_future()
        msg = {"type": "approval", "id": aid, "kind": kind, "title": title[:200],
               "detail": detail[:600], "url": url[:300], "timeout": int(self.timeout)}
        self._pending[aid], self._messages[aid] = fut, msg
        try:
            await self._broadcast(msg)
            return await asyncio.wait_for(fut, self.timeout)
        except asyncio.TimeoutError:
            return False
        # CancelledError is deliberately NOT caught: if the task is stopped while a prompt
        # is open, the cancellation must propagate, not be mistaken for a "no" answer.
        finally:
            self._pending.pop(aid, None)
            self._messages.pop(aid, None)
            try:
                await self._broadcast({"type": "approval_done", "id": aid})
            except Exception:
                pass

    def resolve(self, aid: str, allow: bool) -> bool:
        fut = self._pending.get(aid)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(allow))
        return True

    def cancel_all(self) -> None:
        """Deny everything outstanding (task stopped)."""
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_result(False)

    def snapshot(self) -> list[dict]:
        return list(self._messages.values())


class ConsoleApprover:
    """Terminal version for browser_agent.py / linkedin_agent.py."""

    async def ask(self, kind: str, title: str, detail: str = "", url: str = "") -> bool:
        print(f"\n[Approval needed] {title}")
        if detail:
            print(f"  {detail}")
        if url:
            print(f"  on {url}")
        prompt = "  Press Enter when you're done, or type 'n' to skip: " if kind == "handoff" \
            else "  Allow this? [y/N]: "
        try:
            answer = (await asyncio.to_thread(input, prompt)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer != "n" if kind == "handoff" else answer in ("y", "yes")

    def cancel_all(self) -> None:
        pass
