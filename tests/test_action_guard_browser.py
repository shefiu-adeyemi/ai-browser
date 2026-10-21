"""Real-Chromium test: the guard reads real DOM elements and really stops/allows actions.
Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_action_guard_browser -v
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import http.server
import os
import pathlib
import tempfile
import threading
import unittest

from aiba.server import main as agent_ui
from aiba.agent.action_guard import GuardedTools

PAGE = b"""<html><body style="margin:20px">
<button id=send onclick="document.title='SENT'">Send</button>
<button id=next onclick="document.title='NEXT'">Next</button>
<input id=pw type=password placeholder=password>
<textarea id=ta placeholder="write a post"></textarea>
<div id=box contenteditable=true role=textbox style="border:1px solid #000;height:40px"
     onkeydown="if(event.key==='Enter'){document.title='ENTER-SENT';event.preventDefault()}"></div>
</body></html>"""


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(PAGE)
    def log_message(self, *a): pass


class Approver:
    def __init__(self): self.answer, self.asked, self.details = False, [], []
    async def ask(self, kind, title, detail="", url=""):
        self.asked.append(title)
        self.details.append(detail)
        return self.answer


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class GuardOnRealPage(unittest.IsolatedAsyncioTestCase):
    async def test_guard(self):
        asyncio.get_running_loop().set_debug(False)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}/"
        s = agent_ui._make_session()
        s.browser_profile.user_data_dir = pathlib.Path(tempfile.mkdtemp())
        ap = Approver()
        tools = GuardedTools(ap, secret_names={"linkedin_password"})
        AM = tools.registry.create_action_model()
        try:
            await s.start(); await s.navigate_to(base); await asyncio.sleep(1.0)
            page = await s.get_current_page()

            async def index_of(elem_id):
                state = await s.get_browser_state_summary()          # what the agent does every step
                for idx, n in state.dom_state.selector_map.items():
                    if (n.attributes or {}).get("id") == elem_id:
                        return idx
                self.fail(f"element #{elem_id} not in the agent's view")

            async def act(payload):
                return await tools.act(AM.model_validate(payload), s)

            title = lambda: page.evaluate("() => document.title")
            value = lambda i: page.evaluate(f"() => document.getElementById('{i}').value")

            # 1. Send is risky: declined -> the page must NOT be clicked
            r = await act({"click": {"index": await index_of("send")}})
            self.assertIn("declined", r.error or "")
            self.assertEqual(ap.asked, ['Click "Send" on 127.0.0.1?'])
            self.assertNotEqual(await title(), "SENT")

            # 2. approved -> really clicked
            ap.answer = True
            r = await act({"click": {"index": await index_of("send")}})
            self.assertFalse(r.error)
            self.assertEqual(await title(), "SENT")

            # 3. a harmless button needs no prompt
            ap.asked.clear()
            r = await act({"click": {"index": await index_of("next")}})
            self.assertFalse(r.error)
            self.assertEqual(ap.asked, [])
            self.assertEqual(await title(), "NEXT")

            # 4. stored credential into a post box: hard block, no prompt, nothing typed
            r = await act({"input": {"index": await index_of("ta"), "text": "<secret>linkedin_password</secret>"}})
            self.assertIn("Blocked", r.error or "")
            self.assertEqual(await value("ta"), "")
            self.assertEqual(ap.asked, [])

            # 5. ...but the real password field is allowed through the guard
            r = await act({"input": {"index": await index_of("pw"), "text": "<secret>linkedin_password</secret>"}})
            self.assertNotIn("Blocked", r.error or "")

            # 6. typing into the message box is fine; pressing Enter there is a "send" -> asks
            r = await act({"input": {"index": await index_of("box"), "text": "hello Jane"}})
            self.assertFalse(r.error)
            ap.answer = False
            r = await act({"send_keys": {"keys": "Enter"}})
            self.assertIn("declined", r.error or "")
            self.assertNotEqual(await title(), "ENTER-SENT")
            self.assertIn("hello Jane", ap.details[-1])      # the approver is shown what would be sent

            # 7. file:// is blocked outright
            url_before = await s.get_current_page_url()
            r = await act({"navigate": {"url": "file:///C:/Windows/win.ini"}})
            self.assertIn("Blocked", r.error or "")
            self.assertEqual(await s.get_current_page_url(), url_before)
        finally:
            await s.kill()
            srv.shutdown()


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class AgentBrowserCannotReachControlUi(unittest.IsolatedAsyncioTestCase):
    """The agent must not be able to open the control UI (it could approve its own prompts),
    while other local ports (the user's dev sites) stay reachable."""

    async def test_own_port_blocked_others_open(self):
        from unittest import mock
        asyncio.get_running_loop().set_debug(False)

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/html")
                self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
                self.wfile.write(b"<html><title>up</title></html>")
            def log_message(self, *a): pass

        own = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)      # stands in for the control UI
        dev = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)      # a user's dev site
        for srv in (own, dev):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        p_own, p_dev = own.server_port, dev.server_port
        hosts = {f"localhost:{p_own}", f"127.0.0.1:{p_own}"}
        with mock.patch.object(agent_ui, "_ALLOWED_HOSTS", hosts):
            s = agent_ui._make_session()                                 # the real production config
        s.browser_profile.user_data_dir = pathlib.Path(tempfile.mkdtemp())
        try:
            await s.start()
            await s.navigate_to(f"http://127.0.0.1:{p_dev}/")
            await asyncio.sleep(1.0)
            page = await s.get_current_page()
            probe = ("async (u) => { try { const r = await fetch(u); return 'REACHED' } "
                     "catch (e) { return 'BLOCKED' } }")
            for host in ("127.0.0.1", "localhost"):
                self.assertEqual(await page.evaluate(f"() => ({probe})('http://{host}:{p_dev}/')"), "REACHED", host)
                self.assertEqual(await page.evaluate(f"() => ({probe})('http://{host}:{p_own}/')"), "BLOCKED", host)
            with self.assertRaises(Exception):                            # top-level navigation too
                await s.navigate_to(f"http://localhost:{p_own}/")
        finally:
            await s.kill()
            own.shutdown(); dev.shutdown()


if __name__ == "__main__":
    unittest.main()
