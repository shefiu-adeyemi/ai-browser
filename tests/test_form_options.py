"""Checkboxes and radio buttons the page listing can't show.
Unit tests always run; the real-Chromium test is opt-in:  RUN_BROWSER_TESTS=1 python -m unittest test_form_options -v
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

from aiba.agent import action_guard
from aiba.server import main as agent_ui
from aiba.agent import form_options
from aiba.agent.action_guard import GuardedTools


class FakePage:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def evaluate(self, js, *args):
        self.calls.append(args)
        return self.reply


class Describe(unittest.TestCase):
    def test_one_line_each_with_state(self):
        text = form_options.describe([
            {"type": "checkbox", "group": "f", "label": "Articles", "checked": True, "disabled": False},
            {"type": "radio", "group": "archiveType", "label": "Full archive", "checked": False, "disabled": True},
        ])
        self.assertEqual(text.splitlines(), ['[x] checkbox: Articles',
                                             '[ ] radio: Full archive (group "archiveType") (disabled)'])

    def test_nothing_found_says_so(self):
        self.assertIn("No checkboxes", form_options.describe([]))


class SetOptionMessages(unittest.IsolatedAsyncioTestCase):
    async def run_with(self, reply, label="Articles", checked=True):
        import json
        return await form_options.set_option(FakePage(json.dumps(reply)), label, checked)

    async def test_changed(self):
        ok, msg = await self.run_with({"ok": True, "changed": True, "label": "Articles", "type": "checkbox", "checked": True})
        self.assertTrue(ok)
        self.assertEqual(msg, 'checkbox "Articles" is now ticked.')

    async def test_already_in_that_state(self):
        ok, msg = await self.run_with({"ok": True, "changed": False, "label": "Articles", "type": "checkbox", "checked": True})
        self.assertTrue(ok)
        self.assertIn("was already ticked", msg)

    async def test_not_found_lists_what_is_there(self):
        ok, msg = await self.run_with({"error": "none", "labels": ["A", "B"]})
        self.assertFalse(ok)
        self.assertIn("A; B", msg)

    async def test_ambiguous_asks_for_the_full_label(self):
        ok, msg = await self.run_with({"error": "many", "labels": ["checkbox: Email", "checkbox: Email me"]})
        self.assertFalse(ok)
        self.assertIn("full label", msg)

    async def test_click_that_changed_nothing_is_reported_as_failure(self):
        ok, msg = await self.run_with({"ok": False, "changed": False, "label": "Articles", "type": "checkbox", "checked": False})
        self.assertFalse(ok)
        self.assertIn("still not ticked", msg)

    async def test_disabled_and_radio_untick(self):
        self.assertIn("disabled", (await self.run_with({"error": "disabled", "label": "X"}))[1])
        self.assertIn("radio button", (await self.run_with({"error": "radio", "label": "X"}, checked=False))[1])


class Guard(unittest.TestCase):
    def verdict(self, label, checked=True):
        return action_guard.classify("set_option", {"label": label, "checked": checked}, None, "https://x.test/",
                                     secret_names=set())

    def test_ordinary_options_are_allowed(self):
        self.assertEqual(self.verdict("Articles").kind, "allow")
        self.assertEqual(self.verdict("Want something in particular?").kind, "allow")

    def test_ticking_a_label_that_reaches_beyond_the_page_asks(self):
        for label in ("Subscribe to our newsletter", "Allow marketing emails", "I agree, share my data"):
            v = self.verdict(label)
            self.assertEqual(v.kind, "ask", label)
            self.assertTrue(v.auto)

    def test_unticking_is_always_fine(self):
        self.assertEqual(self.verdict("Subscribe to our newsletter", checked=False).kind, "allow")


PAGE = b"""<html><head><style>
/* the way LinkedIn draws them: the real input is invisible and cannot be clicked; the label is what a person clicks */
input.hide{position:absolute;opacity:0;pointer-events:none;width:24px;height:24px;appearance:none}
</style></head><body>
<div><input class=hide type=radio name=t id=full value=full><label for=full>Complete archive</label></div>
<div><input class=hide type=radio name=t id=fast value=fast><label for=fast>Want something in particular?</label></div>
<ul>
<li><input class=hide type=checkbox id=c1><label for=c1>Articles</label></li>
<li><input class=hide type=checkbox id=c2><label for=c2>Invitations</label></li>
<li><input class=hide type=checkbox id=c3 disabled><label for=c3>Registration</label></li>
</ul>
<label><input class=hide type=checkbox id=w1> Wrapped box</label>
<div role=checkbox aria-checked=false tabindex=0 id=aria1 onclick="this.setAttribute('aria-checked', this.getAttribute('aria-checked')==='true'?'false':'true')">ARIA box</div>
<label for=nl><input type=checkbox id=nl> Subscribe to the newsletter</label>
<input type=checkbox id=dead><label for=dead>Never changes</label>
<script>document.getElementById('dead').addEventListener('click', e => e.preventDefault())</script>
</body></html>"""


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(PAGE)
    def log_message(self, *a): pass


class Approver:
    def __init__(self): self.asked = []
    async def ask(self, kind, title, detail="", url=""):
        self.asked.append(title)
        return False


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class OnARealPage(unittest.IsolatedAsyncioTestCase):
    async def test_options_the_listing_cannot_show(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        s = agent_ui._make_session()
        s.browser_profile.user_data_dir = pathlib.Path(tempfile.mkdtemp())
        ap = Approver()
        tools = GuardedTools(ap)
        AM = tools.registry.create_action_model()
        try:
            await s.start(); await s.navigate_to(f"http://127.0.0.1:{srv.server_port}/"); await asyncio.sleep(1.0)
            page = await s.get_current_page()
            checked = lambda i: page.evaluate(f"() => String(document.getElementById('{i}').checked)")
            act = lambda payload: tools.act(AM.model_validate(payload), s)

            r = await act({"list_options": {}})
            for line in ("[ ] radio: Complete archive", "[ ] checkbox: Articles", "[ ] checkbox: Wrapped box",
                         "[ ] checkbox: ARIA box", "(disabled)"):
                self.assertIn(line, r.extracted_content)

            r = await act({"set_option": {"label": "Articles"}})            # the input is invisible: the label is clicked
            self.assertIsNone(r.error, r.error)
            self.assertEqual(await checked("c1"), "true")
            self.assertIn("is now ticked", r.extracted_content)

            r = await act({"set_option": {"label": "articles"}})            # asking again changes nothing and says so
            self.assertIn("already ticked", r.extracted_content)

            r = await act({"set_option": {"label": "Articles", "checked": False}})
            self.assertEqual(await checked("c1"), "false")

            r = await act({"set_option": {"label": "Want something in particular?"}})      # a radio button
            self.assertEqual(await checked("fast"), "true")
            await act({"set_option": {"label": "Complete archive"}})
            self.assertEqual((await checked("fast"), await checked("full")), ("false", "true"))   # one per group

            await act({"set_option": {"label": "Wrapped box"}})
            self.assertEqual(await checked("w1"), "true")

            r = await act({"set_option": {"label": "ARIA box"}})
            self.assertIn("is now ticked", r.extracted_content)

            r = await act({"set_option": {"label": "Registration"}})
            self.assertIn("disabled", r.error)
            r = await act({"set_option": {"label": "Never changes"}})
            self.assertIn("still not ticked", r.error)
            r = await act({"set_option": {"label": "Nothing like this"}})
            self.assertIn("Options on the page", r.error)

            r = await act({"set_option": {"label": "Subscribe to the newsletter"}})      # risky: needs a person, and was declined
            self.assertIn("declined", r.error)
            self.assertEqual(ap.asked, ['Tick "Subscribe to the newsletter" on 127.0.0.1?'])
            self.assertEqual(await checked("nl"), "false")
        finally:
            await s.kill()
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
