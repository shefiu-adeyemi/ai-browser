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


class Typed(str):
    """What the user typed, and whether they asked for it to be remembered for later runs."""
    remember: bool = False


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
        return bool(await self._ask(kind, title, detail, url))

    async def ask_text(self, title: str, detail: str = "", url: str = "") -> Optional[str]:
        """Ask the user to TYPE something. None means no answer (declined, timed out, nobody there)."""
        answer = await self._ask("input", title, detail, url)
        return answer if isinstance(answer, str) and answer.strip() else None

    async def _ask(self, kind: str, title: str, detail: str, url: str):
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

    def resolve(self, aid: str, allow: bool, text: Optional[str] = None, remember: bool = False) -> bool:
        fut = self._pending.get(aid)
        if fut is None or fut.done():
            return False
        if self._messages.get(aid, {}).get("kind") == "input":     # a typed answer, or nothing
            if allow and isinstance(text, str):
                typed = Typed(text)
                typed.remember = remember is True
                fut.set_result(typed)
            else:
                fut.set_result(None)
        else:
            fut.set_result(bool(allow))
        return True

    def cancel_all(self) -> None:
        """Deny everything outstanding (task stopped)."""
        for aid, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_result(None if self._messages.get(aid, {}).get("kind") == "input" else False)

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

    async def ask_text(self, title: str, detail: str = "", url: str = "") -> Optional[str]:
        print(f"\n[Question] {title}")
        if detail:
            print(f"  {detail}")
        try:
            answer = (await asyncio.to_thread(input, "  Your answer (empty to skip): ")).strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return answer or None

    def cancel_all(self) -> None:
        pass
