"""Real-Chromium test of the live view.  Slow (launches a browser), so opt-in:
    RUN_BROWSER_TESTS=1 python -m unittest test_live_view_browser -v
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import base64
import http.server
import os
import pathlib
import tempfile
import threading
import unittest

import agent_ui
from live_view import LiveView

PAGE = b"""<html><body style="margin:0;height:5000px">
<div id=spin style="position:fixed;right:20px;top:20px;width:50px;height:50px;background:red"></div>
<script>let n=0;setInterval(()=>{document.getElementById('spin').style.left=(n++%300)+'px'},16)</script>
<button onclick="document.title='clicked'" style="position:absolute;left:100px;top:100px;width:200px;height:80px">Go</button>
<form onsubmit="document.title='submitted';return false"><input id=i style="position:absolute;left:100px;top:250px;width:300px;height:40px"></form>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(PAGE)
    def log_message(self, *a): pass


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class LiveViewBrowser(unittest.IsolatedAsyncioTestCase):
    async def _until(self, js, expected, timeout=5.0):
        """Poll a page expression until it equals `expected` (input is async, so don't guess sleeps)."""
        page = await self.s.get_current_page()
        got = None
        for _ in range(int(timeout / 0.1)):
            got = await page.evaluate(js)
            if got == expected:
                return
            await asyncio.sleep(0.1)
        self.fail(f"{js} -> {got!r}, expected {expected!r}")

    async def test_end_to_end(self):
        asyncio.get_running_loop().set_debug(False)   # unittest's debug loop is ~10x slower
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}/"
        frames, urls = [], []
        async def broadcast(m): (frames if m["type"] == "screenshot" else urls).append(m)

        s = self.s = agent_ui._make_session()
        s.browser_profile.user_data_dir = pathlib.Path(tempfile.mkdtemp())
        watching = {"v": False}
        lv = LiveView(lambda: s, broadcast, lambda: watching["v"])
        run = asyncio.create_task(lv.run())
        try:
            await s.start(); await s.navigate_to(base); await asyncio.sleep(1.5)
            self.assertEqual(frames, [], "must not stream while nobody watches")
            self.assertTrue(any(base in u["data"] for u in urls), "URL tracking works without viewers")

            watching["v"] = True; await asyncio.sleep(3.0)
            self.assertGreater(len(frames), 8)   # proves streaming; unittest runs asyncio in slow debug mode
            f = frames[-1]
            self.assertEqual(base64.b64decode(f["data"])[:2], b"\xff\xd8")
            page = await s.get_current_page()
            ev = page.evaluate
            self.assertEqual(await ev("() => innerWidth + 'x' + innerHeight"), f"{f['w']}x{f['h']}")

            # the agent's own per-step capture must keep working while we stream
            state = await s.get_browser_state_summary(include_screenshot=True)
            self.assertTrue(state.screenshot)
            n = len(frames); await asyncio.sleep(1.0)
            self.assertGreater(len(frames), n + 5, "still streaming after agent state capture")

            # input goes through the ordered queue, exactly as the websocket does
            async def send(**m): lv.enqueue_input({"type": "input", **m}); await asyncio.sleep(0.05)
            for e in ("mousePressed", "mouseReleased"):
                await send(kind="mouse", event=e, x=150, y=140, button="left")
            await self._until("() => document.title", "clicked")

            for e in ("mousePressed", "mouseReleased"):
                await send(kind="mouse", event=e, x=200, y=270, button="left")
            for k, code, kc in (("h", "KeyH", 72), ("i", "KeyI", 73)):
                await send(kind="key", event="keyDown", key=k, code=code, keyCode=kc, modifiers=0)
                await send(kind="key", event="char", key=k, text=k, modifiers=0)
                await send(kind="key", event="keyUp", key=k, code=code, keyCode=kc, modifiers=0)
            await self._until("() => document.getElementById('i').value", "hi")
            await send(kind="key", event="keyDown", key="a", code="KeyA", keyCode=65, modifiers=2)
            await send(kind="key", event="char", key="a", text="a", modifiers=2)
            await send(kind="key", event="keyDown", key="x", code="KeyX", keyCode=88, modifiers=0)
            await send(kind="key", event="char", key="x", text="x", modifiers=0)
            await self._until("() => document.getElementById('i').value", "x")   # Ctrl+A selected all
            await send(kind="key", event="keyDown", key="Enter", code="Enter", keyCode=13, modifiers=0)
            await self._until("() => document.title", "submitted")
            await send(kind="mouse", event="mouseWheel", x=300, y=300, deltaX=0, deltaY=400, button="none")
            await self._until("() => String(scrollY)", "400")

            watching["v"] = False; await asyncio.sleep(1.2); before = len(frames); await asyncio.sleep(1.5)
            self.assertLessEqual(len(frames) - before, 1, "stops streaming when nobody watches")

            watching["v"] = True; await asyncio.sleep(1.2)
            await s.navigate_to(base + "?tab=2", new_tab=True); await asyncio.sleep(2.0)
            self.assertTrue(any("tab=2" in u["data"] for u in urls), "follows the agent to a new tab")
            n = len(frames); await asyncio.sleep(1.5)
            self.assertGreater(len(frames), n + 5, "still streaming after tab switch")
        finally:
            run.cancel()
            await s.kill()
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
