"""Real-Chromium test of the browser toolbar (address, back / forward / reload, tabs).  Slow, so opt-in:
    RUN_BROWSER_TESTS=1 python -m unittest test_browser_nav_browser -v
"""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())

import asyncio
import http.server
import os
import pathlib
import tempfile
import threading
import unittest

from aiba.server import main as agent_ui
from aiba.agent import browser_nav as bn
class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        name = self.path.strip("/") or "home"
        self.wfile.write(f"<html><head><title>page-{name}</title></head><body>{name} {os.urandom(4).hex()}</body></html>".encode())
    def log_message(self, *a): pass


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class ToolbarBrowser(unittest.IsolatedAsyncioTestCase):
    async def _until(self, check, what, timeout=8.0):
        for _ in range(int(timeout / 0.2)):
            v = await check()
            if v:
                return v
            await asyncio.sleep(0.2)
        self.fail(what)

    async def test_toolbar_end_to_end(self):
        asyncio.get_running_loop().set_debug(False)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        s = agent_ui._make_session()
        s.browser_profile.user_data_dir = pathlib.Path(tempfile.mkdtemp())
        free = {"v": True}
        nav = bn.Navigator(lambda: s, frozenset({"localhost:8000", "127.0.0.1:8000"}), lambda: free["v"])
        try:
            await s.start()
            await s.navigate_to(base + "/a")

            async def title():
                return (await nav.state())["title"]

            async def titled(t):
                return (await title()) == t

            await self._until(lambda: titled("page-a"), "start page")
            self.assertEqual(await nav.go(f"127.0.0.1:{srv.server_port}/b"), base + "/b")
            await self._until(lambda: titled("page-b"), "typed address opened")
            st = await nav.state()
            self.assertTrue(st["can_back"])
            self.assertFalse(st["can_forward"])

            await nav.back()
            await self._until(lambda: titled("page-a"), "back")
            self.assertTrue((await nav.state())["can_forward"])
            await nav.forward()
            await self._until(lambda: titled("page-b"), "forward")

            page = await s.get_current_page()
            before = await page.evaluate("() => document.body.textContent")
            await nav.reload()
            async def reloaded():
                p = await s.get_current_page()
                return (await p.evaluate("() => document.body.textContent")) != before
            await self._until(reloaded, "reload fetched the page again")

            # tabs
            await nav.go(f"127.0.0.1:{srv.server_port}/c", new_tab=True)
            await self._until(lambda: titled("page-c"), "a new tab opened and has focus")
            tabs = (await nav.state())["tabs"]
            self.assertGreaterEqual(len(tabs), 2)
            other = next(t for t in tabs if t["title"] == "page-b")
            await nav.switch(other["id"])
            await self._until(lambda: titled("page-b"), "switched tab")
            c_tab = next(t for t in (await nav.state())["tabs"] if t["title"] == "page-c")
            n = len((await nav.state())["tabs"])
            await nav.close(c_tab["id"])
            await self._until(lambda: self._count_is(nav, n - 1), "tab closed")

            # refused things never reach the browser
            for bad in ("javascript:document.title='x'", "file:///C:/Windows/win.ini", "localhost:8000"):
                with self.assertRaises(bn.BadAddress):
                    await nav.go(bad)
            free["v"] = False
            with self.assertRaises(bn.Busy):
                await nav.go("example.com")
            self.assertEqual(await title(), "page-b")
        finally:
            srv.shutdown()
            try:
                await s.kill()
            except Exception:
                pass

    async def _count_is(self, nav, n):
        return len((await nav.state())["tabs"]) == n


if __name__ == "__main__":
    unittest.main()
