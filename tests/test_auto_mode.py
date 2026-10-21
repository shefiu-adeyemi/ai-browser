"""Tests for the 'auto' approval mode (agent running while you're away)."""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import pathlib
import unittest
from types import SimpleNamespace
from unittest import mock

from browser_use.agent.views import ActionResult
from browser_use.tools.service import Tools

from aiba.agent.action_guard import GuardedTools, classify, site_named

SECRETS = {"linkedin_email", "linkedin_password"}
LI = "https://www.linkedin.com/in/jane"


def node(tag="button", text="", **attrs):
    return SimpleNamespace(tag_name=tag, attributes=attrs, get_all_children_text=lambda: text)


class Approver:
    """Records prompts. In auto mode it must never be asked."""
    def __init__(self):
        self.asked = []

    async def ask(self, kind, title, detail="", url=""):
        self.asked.append(title)
        return True


class Session:
    def __init__(self, n=None, url=LI):
        self.n, self.url = n, url

    async def get_element_by_index(self, i):
        return self.n

    async def get_current_page_url(self):
        return self.url


class Notes:
    def __init__(self):
        self.lines = []

    async def __call__(self, msg):
        self.lines.append(msg["text"])


def run(tools, payload, session):
    am = tools.registry.create_action_model()
    return asyncio.run(tools.act(am.model_validate(payload), session))


class SiteNamed(unittest.TestCase):
    def test_matches_the_site_the_task_names(self):
        self.assertTrue(site_named("Connect with Jane on LinkedIn", "https://www.linkedin.com/in/jane"))
        self.assertTrue(site_named("order it from amazon", "https://www.amazon.co.uk/checkout"))
        self.assertTrue(site_named("check GitHub issues", "https://github.com/x/y"))

    def test_other_sites_do_not_match(self):
        self.assertFalse(site_named("Connect with Jane on LinkedIn", "https://evil.example/post"))
        self.assertFalse(site_named("Connect with Jane on LinkedIn", "https://linkedin.com.evil.example/"))
        self.assertFalse(site_named("Connect with Jane on LinkedIn", ""))
        self.assertFalse(site_named("do it", "https://localhost:3000/"))


class AutoModeFlow(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(Tools, "act", new=mock.AsyncMock(return_value=ActionResult(extracted_content="RAN")))
        self.real = p.start()
        self.addCleanup(p.stop)
        self.ap, self.notes = Approver(), Notes()

    def tools(self, task="Connect with Jane on LinkedIn", mode="auto"):
        return GuardedTools(self.ap, mode=mode, secret_names=SECRETS, notify=self.notes, task=task)

    def test_send_on_the_named_site_runs_without_asking_and_is_logged(self):
        r = run(self.tools(), {"click": {"index": 1}}, Session(node(text="Send")))
        self.assertEqual(r.extracted_content, "RAN")
        self.assertEqual(self.ap.asked, [])                                   # nobody was bothered
        self.assertTrue(any("Auto-approved" in ln and "Send" in ln for ln in self.notes.lines))

    def test_what_was_typed_is_in_the_audit_line(self):
        t = self.tools()
        s = Session(node("div", role="textbox", contenteditable="true"))
        run(t, {"input": {"index": 1, "text": "Hi Jane, great to meet you"}}, s)
        run(t, {"send_keys": {"keys": "Enter"}}, s)
        self.assertTrue(any("Hi Jane, great to meet you" in ln for ln in self.notes.lines))

    def test_same_click_on_a_site_the_task_did_not_name_is_declined_at_once(self):
        r = run(self.tools(), {"click": {"index": 1}}, Session(node(text="Send"), url="https://evil.example/"))
        self.assertIn("Not allowed while running unattended", r.error)
        self.assertEqual(self.ap.asked, [])                                   # no 3-minute wait
        self.real.assert_not_called()
        self.assertTrue(any("declined" in ln for ln in self.notes.lines))

    def test_javascript_is_never_auto_approved(self):
        r = run(self.tools(), {"evaluate": {"code": "fetch('/x')"}}, Session())
        self.assertIn("Not allowed while running unattended", r.error)
        self.real.assert_not_called()

    def test_long_query_link_and_uninspectable_click_are_never_auto_approved(self):
        r = run(self.tools(), {"navigate": {"url": "https://www.linkedin.com/x?d=" + "A" * 400}}, Session())
        self.assertIn("unattended", r.error)
        r = run(self.tools(), {"click": {"index": 9}}, Session(None))
        self.assertIn("unattended", r.error)

    def test_hard_blocks_still_apply(self):
        r = run(self.tools(), {"input": {"index": 1, "text": "<secret>linkedin_password</secret>"}},
                Session(node("textarea")))
        self.assertIn("Blocked", r.error)
        r = run(self.tools(), {"navigate": {"url": "file:///C:/Windows/win.ini"}}, Session())
        self.assertIn("Blocked", r.error)

    def test_ordinary_actions_are_untouched(self):
        r = run(self.tools(), {"click": {"index": 1}}, Session(node(text="Next")))
        self.assertEqual(r.extracted_content, "RAN")
        self.assertEqual(self.notes.lines, [])                                # nothing to report

    def test_ask_mode_still_asks(self):
        run(self.tools(mode="ask"), {"click": {"index": 1}}, Session(node(text="Send")))
        self.assertEqual(len(self.ap.asked), 1)


class ModeParsing(unittest.TestCase):
    def test_typo_or_garbage_means_ask_never_off(self):
        for bad in ("of", "OFF ", "none", "false", "0", "", "auto-approve"):
            m = GuardedTools(Approver(), mode=bad).mode
            self.assertIn(m, ("ask", "off"), bad)
        self.assertEqual(GuardedTools(Approver(), mode="of").mode, "ask")
        self.assertEqual(GuardedTools(Approver(), mode="none").mode, "ask")
        self.assertEqual(GuardedTools(Approver(), mode=" AUTO ").mode, "auto")
        self.assertEqual(GuardedTools(Approver(), mode="off").mode, "off")

    def test_env_var_is_used_and_defaults_to_ask(self):
        with mock.patch.dict(_os.environ, {"APPROVAL_MODE": "auto"}):
            self.assertEqual(GuardedTools(Approver()).mode, "auto")
        with mock.patch.dict(_os.environ, {}, clear=False):
            _os.environ.pop("APPROVAL_MODE", None)
            self.assertEqual(GuardedTools(Approver()).mode, "ask")


class SettingsEndpoint(unittest.TestCase):
    def setUp(self):
        from aiba.server import main as agent_ui
        from fastapi.testclient import TestClient
        self.agent_ui = agent_ui
        self.env = pathlib.Path(_tf.mkdtemp()) / ".env"
        self.env.write_text("LLM_PROVIDER=claude\n")
        p = mock.patch.object(agent_ui, "ENV_PATH", self.env)
        p.start()
        self.addCleanup(p.stop)
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")

    def get_mode(self):
        return self.c.get("/settings").json()["APPROVAL_MODE"]

    def test_defaults_to_ask(self):
        self.assertEqual(self.get_mode(), "ask")

    def test_ui_can_choose_auto_and_back(self):
        self.c.post("/settings", json={"APPROVAL_MODE": "auto"})
        self.assertEqual(self.get_mode(), "auto")
        self.c.post("/settings", json={"APPROVAL_MODE": "ask"})
        self.assertEqual(self.get_mode(), "ask")

    def test_ui_cannot_switch_the_safety_layer_off(self):
        self.c.post("/settings", json={"APPROVAL_MODE": "auto"})
        self.c.post("/settings", json={"APPROVAL_MODE": "off"})
        self.assertEqual(self.get_mode(), "auto")                             # rejected, unchanged
        self.c.post("/settings", json={"APPROVAL_MODE": "whatever"})
        self.assertEqual(self.get_mode(), "auto")

    def test_hand_edited_off_is_reported_not_hidden(self):
        self.env.write_text("APPROVAL_MODE=off\n")
        self.assertEqual(self.get_mode(), "off")


if __name__ == "__main__":
    unittest.main()
