"""
Live view of the browser the agent is driving.

Instead of polling screenshots, this subscribes to Chromium's own screencast
(CDP Page.startScreencast): the browser pushes a compressed frame whenever the
page changes, and nothing while it is static. Frames are forwarded to the UI
untouched (already base64 JPEG), so there is no per-frame encode/decode here.

The same CDP session is used to forward the user's mouse and keyboard back into
the page (Input.dispatch*), so the pane is an interactive view of the real tab.

Frame message sent to clients:
  {"type": "screenshot", "data": "<base64 jpeg>", "w": <viewport css px>, "h": ...}
"w"/"h" define the coordinate space for input events coming back from the UI.
"""

import asyncio
import logging
import math
from collections import deque
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

_POLL_S        = 0.4     # how often we re-check focus / clients / URL
_MIN_FRAME_GAP = 0.033   # cap forwarding at ~30 fps
_MAX_QUEUED_INPUT = 256
_BUTTON_BITS   = {"left": 1, "right": 2, "middle": 4}
_CTRL, _META   = 2, 4    # CDP modifier bits
_EDIT_COMMANDS = {"a": "selectAll", "c": "copy", "x": "cut",
                  "v": "paste", "z": "undo", "y": "redo"}


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


class LiveView:
    def __init__(
        self,
        get_session: Callable[[], Any],
        broadcast: Callable[[dict], Awaitable[None]],
        has_clients: Callable[[], bool],
        on_url: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._get_session = get_session
        self._broadcast   = broadcast
        self._has_clients = has_clients
        self._on_url      = on_url

        self.latest: Optional[dict] = None     # last frame message, for late joiners
        self._session: Any = None              # BrowserSession we are attached to
        self._client: Any = None               # its CDP client (handler registered once)
        self._sid: Optional[str] = None        # CDP session id of the tab being streamed
        self._streaming = False
        self._pending: Optional[dict] = None   # newest unsent frame (older ones are dropped)
        self._frame_ready = asyncio.Event()
        self._last_url = ""
        self._buttons = 0
        self._warned: set[str] = set()
        self._inputs: deque = deque()
        self._input_ready = asyncio.Event()

    # ------------------------------------------------------------------ frames
    def _on_frame(self, event: dict, session_id: Optional[str]) -> None:
        # Runs inside the CDP receive loop: must not await CDP calls (deadlock).
        if session_id != self._sid:
            return
        meta = event.get("metadata") or {}
        self._pending = {
            "type": "screenshot",
            "data": event["data"],
            "w": int(_num(meta.get("deviceWidth"), 1280)) or 1280,
            "h": int(_num(meta.get("deviceHeight"), 800)) or 800,
        }
        self._frame_ready.set()
        # Ack right away so Chromium keeps producing frames; the sender below
        # forwards only the newest one, so a slow client can't back frames up.
        asyncio.get_running_loop().create_task(self._ack(event.get("sessionId"), session_id))

    async def _ack(self, frame_no: Any, sid: Optional[str]) -> None:
        try:
            await self._client.send.Page.screencastFrameAck(
                params={"sessionId": frame_no}, session_id=sid)
        except Exception:
            pass  # tab closed / session gone; the run loop will re-attach

    async def _sender(self) -> None:
        while True:
            await self._frame_ready.wait()
            self._frame_ready.clear()
            msg, self._pending = self._pending, None
            if msg is None:
                continue
            self.latest = msg
            try:
                await self._broadcast(msg)
            except Exception as e:   # never let one bad send freeze the feed for good
                log.warning("live view broadcast failed: %s", e)
            await asyncio.sleep(_MIN_FRAME_GAP)

    # --------------------------------------------------------------- lifecycle
    async def _start(self, session: Any, cdp: Any) -> None:
        if self._client is not session.cdp_client:
            self._client = session.cdp_client
            self._client.register.Page.screencastFrame(self._on_frame)
        if session is not self._session:
            self.latest = None          # don't show a new client a frame from a dead browser
        self._session = session
        self._sid = cdp.session_id
        self._buttons = 0               # a lost mouse-up must not leave "drag" stuck on
        await self._client.send.Page.startScreencast(
            params={"format": "jpeg", "quality": 70, "maxWidth": 1600,
                    "maxHeight": 1000, "everyNthFrame": 1},
            session_id=self._sid)
        self._streaming = True

    async def _stop(self) -> None:
        if self._streaming and self._client is not None and self._sid:
            try:
                await self._client.send.Page.stopScreencast(session_id=self._sid)
            except Exception:
                pass
        self._streaming = False
        self._sid = None

    async def _current_cdp(self, session: Any) -> Any:
        """The CDP session of the tab the agent is focused on, or None if the
        browser isn't up yet. focus=False: we observe, we never steal focus."""
        if not getattr(session, "agent_focus_target_id", None):
            return None
        return await session.get_or_create_cdp_session(focus=False)

    async def _publish_url(self, session: Any) -> None:
        try:
            url = await session.get_current_page_url()
        except Exception:
            return
        if url and url != self._last_url and url.startswith("http"):
            self._last_url = url
            await self._broadcast({"type": "url", "data": url})
            if self._on_url:
                self._on_url(url)

    async def run(self) -> None:
        sender = asyncio.create_task(self._sender())
        worker = asyncio.create_task(self._input_worker())
        try:
            while True:
                await asyncio.sleep(_POLL_S)
                session = self._get_session()
                try:
                    # Touch CDP only while someone is watching and the browser is fully
                    # connected: idle polling would waste work and race browser startup.
                    connected = session is not None and session.is_cdp_connected
                    if connected:
                        await self._publish_url(session)   # cached data; keeps resume-URL fresh
                    watching = connected and self._has_clients()
                    cdp = await self._current_cdp(session) if watching else None
                    if cdp is None:
                        if self._streaming:
                            await self._stop()
                        continue
                    if (not self._streaming or session is not self._session
                            or cdp.session_id != self._sid):
                        await self._stop()
                        await self._start(session, cdp)
                except Exception as e:
                    self._streaming = False   # force a clean re-attach next tick
                    self._sid = None
                    log.debug("live view tick failed: %s", e)
        finally:
            sender.cancel()
            worker.cancel()

    # ------------------------------------------------------------------- input
    def _warn_once(self, what: str, err: Exception) -> None:
        if what not in self._warned:
            self._warned.add(what)
            log.warning("live view input (%s) failed: %s", what, err)

    def enqueue_input(self, msg: dict) -> None:
        """Queue an event from the UI. Events are applied strictly in order; if the
        page is busy and the queue is full the newest event is dropped, not buffered."""
        if len(self._inputs) < _MAX_QUEUED_INPUT:
            self._inputs.append(msg)
            self._input_ready.set()

    async def _input_worker(self) -> None:
        while True:
            await self._input_ready.wait()
            self._input_ready.clear()
            while self._inputs:
                msg = self._inputs.popleft()
                # Collapse a burst of mouse moves into the last one; clicks/keys are never dropped.
                while (msg.get("event") == "mouseMoved" and self._inputs
                       and self._inputs[0].get("event") == "mouseMoved"):
                    msg = self._inputs.popleft()
                await self.dispatch_input(msg)

    async def dispatch_input(self, msg: dict) -> None:
        """Forward a mouse/keyboard event from the UI into the streamed tab."""
        if not (self._client and self._sid):
            return
        try:
            if msg.get("kind") == "mouse":
                await self._mouse(msg)
            elif msg.get("kind") == "key":
                await self._key(msg)
        except Exception as e:
            self._warn_once(str(msg.get("kind")), e)

    async def _mouse(self, msg: dict) -> None:
        kind = {"mousePressed": "mousePressed", "mouseReleased": "mouseReleased",
                "mouseMoved": "mouseMoved", "mouseWheel": "mouseWheel"}.get(msg.get("event"))
        if kind is None:
            return
        button = msg.get("button") if msg.get("button") in _BUTTON_BITS else "none"
        bit = _BUTTON_BITS.get(button, 0)
        if kind == "mousePressed":
            self._buttons |= bit
        elif kind == "mouseReleased":
            self._buttons &= ~bit
        params = {
            "type": kind, "x": _num(msg.get("x")), "y": _num(msg.get("y")),
            "button": button, "buttons": self._buttons,
            "modifiers": int(_num(msg.get("modifiers"))) & 15,
        }
        if kind in ("mousePressed", "mouseReleased"):
            params["clickCount"] = 1
        if kind == "mouseWheel":
            params["deltaX"] = _num(msg.get("deltaX"))
            params["deltaY"] = _num(msg.get("deltaY"))
        await self._client.send.Input.dispatchMouseEvent(params=params, session_id=self._sid)

    async def _key(self, msg: dict) -> None:
        event = msg.get("event")
        key   = str(msg.get("key", ""))[:32]
        mods  = int(_num(msg.get("modifiers"))) & 15
        send  = self._client.send.Input.dispatchKeyEvent
        if event == "keyDown":
            params = {"type": "rawKeyDown", "key": key, "code": str(msg.get("code", ""))[:32],
                      "windowsVirtualKeyCode": int(_num(msg.get("keyCode"))), "modifiers": mods}
            cmd = _EDIT_COMMANDS.get(key.lower()) if mods & (_CTRL | _META) else None
            if cmd:
                params["commands"] = [cmd]
            await send(params=params, session_id=self._sid)
            if key == "Enter":   # Enter only submits forms when a char event follows
                await send(params={"type": "char", "text": "\r", "modifiers": mods},
                           session_id=self._sid)
        elif event == "char":
            text = str(msg.get("text", ""))[:8]
            if text and not mods & (_CTRL | _META):   # Ctrl+A etc. are shortcuts, not text
                await send(params={"type": "char", "text": text, "modifiers": mods},
                           session_id=self._sid)
        elif event == "keyUp":
            await send(params={"type": "keyUp", "key": key,
                               "code": str(msg.get("code", ""))[:32],
                               "windowsVirtualKeyCode": int(_num(msg.get("keyCode"))),
                               "modifiers": mods}, session_id=self._sid)
