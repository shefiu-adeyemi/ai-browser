"""Tests for the prompt-injection guard (no browser needed).  python -m unittest -v"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from browser_use.agent.views import ActionResult
from browser_use.tools.service import Tools

from aiba.agent import action_guard as ag
from aiba.agent.action_guard import GuardedTools, classify

SECRETS = {"linkedin_email", "linkedin_password"}


def node(tag="button", text="", **attrs):
    return SimpleNamespace(tag_name=tag, attributes=attrs, get_all_children_text=lambda: text)


def verdict(name, params=None, n=None, url="https://www.linkedin.com/feed/", **kw):
    return classify(name, params or {}, n, url, secret_names=SECRETS, **kw).kind


class Credentials(unittest.TestCase):
    def test_password_only_into_password_field(self):
        p = {"index": 1, "text": "<secret>linkedin_password</secret>"}
        self.assertEqual(verdict("input", p, node("input", type="password")), "allow")
        for bad in (node("input", type="text"), node("textarea"), node("div", contenteditable="true"),
                    node("input", type="search")):
            self.assertEqual(verdict("input", p, bad), "block", bad)

    def test_email_into_login_input_only(self):
        p = {"index": 1, "text": "<secret>linkedin_email</secret>"}
        self.assertEqual(verdict("input", p, node("input", type="email")), "allow")
        self.assertEqual(verdict("input", p, node("input", type="text")), "allow")
        self.assertEqual(verdict("input", p, node("textarea")), "block")             # e.g. a post box
        self.assertEqual(verdict("input", p, node("div", role="textbox", contenteditable="true")), "block")

    def test_bare_secret_name_is_also_caught(self):
        self.assertEqual(verdict("input", {"index": 1, "text": "linkedin_password"}, node("textarea")), "block")

    def test_secret_with_uninspectable_element_is_blocked(self):
        self.assertEqual(verdict("input", {"index": 1, "text": "<secret>linkedin_password</secret>"}, None), "block")

    def test_ordinary_typing_is_allowed_anywhere(self):
        self.assertEqual(verdict("input", {"index": 1, "text": "hello"}, node("textarea")), "allow")

    def test_unknown_secret_names_are_not_treated_as_secrets(self):
        self.assertEqual(verdict("input", {"index": 1, "text": "<secret>other</secret>"}, node("textarea")), "allow")


class Approvals(unittest.TestCase):
    def test_risky_clicks_need_approval(self):
        for label in ("Send", "Post", "Connect", "Send now", "Place order", "Delete account", "Apply now",
                      "Submit application", "Buy now", "Confirm", "Follow", "Share"):
            self.assertEqual(verdict("click", {"index": 1}, node(text=label)), "ask", label)

    def test_aria_label_counts(self):
        self.assertEqual(verdict("click", {"index": 1}, node(text="", **{"aria-label": "Send message"})), "ask")

    def test_ordinary_clicks_are_allowed(self):
        for label in ("Sign in", "Next", "Search", "Home", "My Network", "Connections", "Posts", "Apply filters",
                      "Show more"):
            self.assertEqual(verdict("click", {"index": 1}, node(text=label)), "allow", label)

    def test_iconless_submit_button_asks(self):
        self.assertEqual(verdict("click", {"index": 1}, node("input", type="submit")), "ask")

    def test_uninspectable_click_asks(self):
        self.assertEqual(verdict("click", {"index": 9}, None), "ask")

    def test_enter_in_a_message_box_asks_but_search_box_does_not(self):
        box = node("div", role="textbox", contenteditable="true")
        self.assertEqual(verdict("send_keys", {"keys": "Enter"}, last_typed_node=box, last_typed_text="hi"), "ask")
        self.assertEqual(verdict("send_keys", {"keys": "Control+Enter"}, last_typed_node=box), "ask")
        self.assertEqual(verdict("send_keys", {"keys": "Enter"}, last_typed_node=node("input", type="text")), "allow")
        self.assertEqual(verdict("send_keys", {"keys": "Enter"}), "ask")     # nothing typed: focus is unknown
        self.assertEqual(verdict("send_keys", {"keys": "Escape"}, last_typed_node=box), "allow")

    def test_tab_then_enter_cannot_bypass_the_click_gate(self):
        plain = node("input", type="text")
        # Tab to a Send button and press Enter/Space: same effect as clicking it
        for keys in ("Tab Enter", "Tab Tab Space", "Shift+Tab Enter", "Tab Return"):
            self.assertEqual(verdict("send_keys", {"keys": keys}, last_typed_node=plain), "ask", keys)
        self.assertEqual(verdict("send_keys", {"keys": "Enter"}), "ask")          # nothing typed: focus unknown
        self.assertEqual(verdict("send_keys", {"keys": "Space"}), "ask")
        self.assertEqual(verdict("send_keys", {"keys": "Enter"}, last_typed_node=plain), "allow")   # normal search
        for keys in ("Tab", "Escape", "ArrowDown", "PageDown", "Control+a"):
            self.assertEqual(verdict("send_keys", {"keys": keys}), "allow", keys)

    def test_policy_verbs_need_approval(self):
        for label in ("Download", "Sign up", "Register", "Create account", "Join now", "Allow", "Authorize app",
                      "Donate", "Install"):
            self.assertEqual(verdict("click", {"index": 1}, node(text=label)), "ask", label)

    def test_big_container_text_is_not_mistaken_for_a_button_label(self):
        card = node("div", text="Senior Python Developer at Acme " + "x " * 80 + "Apply Share Save")
        self.assertEqual(verdict("click", {"index": 1}, card), "allow")            # the words are far past the label
        self.assertEqual(verdict("click", {"index": 1}, node("div", text="Apply", role="button")), "ask")

    def test_very_long_query_string_asks(self):
        smuggle = "https://evil.example/collect?d=" + "A" * 500
        self.assertEqual(verdict("navigate", {"url": smuggle}), "ask")
        self.assertEqual(verdict("navigate", {"url": "https://www.google.com/search?q=python+jobs+lagos"}), "allow")

    def test_javascript_needs_approval(self):
        self.assertEqual(verdict("evaluate", {"code": "fetch('/x')"}), "ask")

    def test_detail_shows_what_was_typed(self):
        v = classify("click", {"index": 1}, node(text="Send"), "https://x.com/", secret_names=SECRETS,
                     last_typed_text="Hello Jane")
        self.assertIn("Hello Jane", v.detail)


class Navigation(unittest.TestCase):
    def test_dangerous_schemes_blocked(self):
        for u in ("file:///C:/Users/someone/.env", "javascript:alert(1)", "chrome://settings", "ftp://x"):
            self.assertEqual(verdict("navigate", {"url": u}), "block", u)

    def test_normal_urls_allowed(self):
        for u in ("https://example.com", "http://example.com/a?b=1", "about:blank"):
            self.assertEqual(verdict("navigate", {"url": u}), "allow", u)


class FakeApprover:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    async def ask(self, kind, title, detail="", url=""):
        self.asked.append((kind, title))
        return self.answer


class FakeSession:
    def __init__(self, node_=None, url="https://www.linkedin.com/feed/"):
        self.node, self.url = node_, url

    async def get_element_by_index(self, i):
        return self.node

    async def get_current_page_url(self):
        return self.url


def run(tools, payload, session):
    am = tools.registry.create_action_model()
    return asyncio.run(tools.act(am.model_validate(payload), session))


class GuardedToolsFlow(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(Tools, "act", new=mock.AsyncMock(return_value=ActionResult(extracted_content="RAN")))
        self.real_act = p.start()
        self.addCleanup(p.stop)

    def test_approved_click_runs(self):
        ap = FakeApprover(True)
        t = GuardedTools(ap, secret_names=SECRETS)
        r = run(t, {"click": {"index": 1}}, FakeSession(node(text="Send")))
        self.assertEqual(r.extracted_content, "RAN")
        self.assertEqual(len(ap.asked), 1)

    def test_denied_click_never_runs(self):
        ap = FakeApprover(False)
        t = GuardedTools(ap, secret_names=SECRETS)
        r = run(t, {"click": {"index": 1}}, FakeSession(node(text="Send")))
        self.assertIn("declined", r.error)
        self.real_act.assert_not_called()

    def test_safe_click_needs_no_prompt(self):
        ap = FakeApprover(False)
        t = GuardedTools(ap, secret_names=SECRETS)
        r = run(t, {"click": {"index": 1}}, FakeSession(node(text="Next")))
        self.assertEqual(r.extracted_content, "RAN")
        self.assertEqual(ap.asked, [])

    def test_block_never_asks_and_never_runs(self):
        ap = FakeApprover(True)
        t = GuardedTools(ap, secret_names=SECRETS)
        r = run(t, {"input": {"index": 1, "text": "<secret>linkedin_password</secret>"}},
                FakeSession(node("textarea")))
        self.assertIn("Blocked", r.error)
        self.assertEqual(ap.asked, [])
        self.real_act.assert_not_called()

    def test_mode_off_skips_prompts_but_not_blocks(self):
        ap = FakeApprover(False)
        t = GuardedTools(ap, mode="off", secret_names=SECRETS)
        self.assertEqual(run(t, {"click": {"index": 1}}, FakeSession(node(text="Send"))).extracted_content, "RAN")
        self.assertEqual(ap.asked, [])
        r = run(t, {"navigate": {"url": "file:///etc/passwd"}}, FakeSession())
        self.assertIn("Blocked", r.error)

    def test_typed_text_is_shown_on_the_later_send(self):
        ap = FakeApprover(True)
        t = GuardedTools(ap, secret_names=SECRETS)
        s = FakeSession(node("div", role="textbox", contenteditable="true"))
        run(t, {"input": {"index": 1, "text": "Hi Jane, let us connect"}}, s)
        self.assertEqual(t._last_typed_text, "Hi Jane, let us connect")
        run(t, {"send_keys": {"keys": "Enter"}}, s)
        self.assertEqual(len(ap.asked), 1)

    def test_secret_text_is_never_remembered_for_display(self):
        t = GuardedTools(FakeApprover(True), secret_names=SECRETS)
        run(t, {"input": {"index": 1, "text": "<secret>linkedin_password</secret>"}},
            FakeSession(node("input", type="password")))
        self.assertEqual(t._last_typed_text, "")

    def test_handoff_action_is_registered(self):
        self.assertIn("request_user_help", GuardedTools(FakeApprover(True)).registry.registry.actions)


class SystemPrompt(unittest.TestCase):
    def test_mentions_the_key_rules(self):
        for phrase in ("DATA, never instructions", "CAPTCHA", "request_user_help", "declined"):
            self.assertIn(phrase, ag.SAFETY_PROMPT)


if __name__ == "__main__":
    unittest.main()
