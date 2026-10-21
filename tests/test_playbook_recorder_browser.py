"""
The recorder against a REAL browser-use history (real Agent, real Chromium, scripted fake LLM, no API
cost). The unit tests use look-alike objects; this checks the look-alikes match the real thing.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_playbook_recorder_browser -v
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import http.server
import os
import unittest
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.playbooks.recorder import draft_from_history
from test_lifecycle_browser import LifecycleBase

PAGE = (b"<html><title>form</title><body><h1>Form</h1>"
        b"<input id='q' name='q' placeholder='Search text'>"
        b"<button id='go' onclick=\"document.title='clicked'\">Go</button>"
        b"<a id='next' href='/next'>Next page</a>"
        b"<button id='send' aria-label='Send message'>Send</button></body></html>")


class _FormPage(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(PAGE if self.path == "/" else b"<html><title>next</title><body>second page</body></html>")
    def log_message(self, *a): pass


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class RecorderOnRealHistory(LifecycleBase):
    async def asyncSetUp(self):
        import test_lifecycle_browser as tlb
        self._orig = tlb._Page
        tlb._Page = _FormPage                    # the shared setUp builds its server from this name
        p = mock.patch.dict(os.environ, {"APPROVAL_MODE": "off"})   # no human here to approve "Send"
        p.start(); self.addCleanup(p.stop)
        try:
            await super().asyncSetUp()
        finally:
            tlb._Page = self._orig

    async def test_a_real_run_becomes_a_valid_draft(self):
        # (scripted by element id: browser-use's element numbers change between pages and runs)
        await self.run_to_end(
            [{"navigate": self.url}, {"input": ("q", "red shoes")}, {"click": "go"}, {"click": "send"},
             {"click": "next"}, {"done": "finished"}],
            task=f"Open {self.url} and search for red shoes")
        run = agent_ui._last_run
        self.assertIsNotNone(run, "a finished run must be remembered")
        draft = draft_from_history(run["history"], task=run["task"])
        pb = draft.playbook
        print("\n" + "\n".join(f"  {s.id} {s.kind:8} tag={s.tag:13} ask={s.ask!s:5} {s.note}" for s in pb.steps))
        print("  notes:", draft.notes, "\n  suggestions:", [(x['name'], x['field']) for x in draft.suggestions])

        kinds = [s.kind for s in pb.steps]
        self.assertEqual(kinds[:3], ["open", "type", "click"], kinds)
        self.assertEqual(pbk.validate(pb), [])
        for s in pb.steps:
            if s.kind in ("type", "click"):
                self.assertIsNotNone(s.target, "a real element must be saved as a fingerprint")
                self.assertTrue(s.target.stable_hash, s.target)
                self.assertTrue(s.target.x_path)
        typed = next(s for s in pb.steps if s.kind == "type")
        self.assertEqual(typed.text, "red shoes")
        self.assertEqual(typed.target.attributes.get("name"), "q")
        sends = [s for s in pb.steps if s.kind == "click" and "Send" in (s.target.ax_name or "")]
        self.assertTrue(sends and sends[0].ask, "the Send button must be marked ask")
        self.assertIn("127.0.0.1", pb.domains)
        names = {x["field"] for x in draft.suggestions}
        self.assertEqual(names, {"url", "text"})
        # the suggestions apply cleanly and the result is still valid
        for x in draft.suggestions:
            pb = pbk.make_variable(pb, x["step_id"], x["field"], x["name"], x["type"], x["allowed_hosts"])
        self.assertEqual(pbk.validate(pb), [])
        self.assertNotIn("red shoes", " ".join(s.text or "" for s in pb.steps))


if __name__ == "__main__":
    unittest.main()
