"""Tests for ui_feed.py: what reaches the chat from the log stream, and that secrets never do."""
import logging
import unittest

import ui_feed
from ui_feed import UIFeed, clean, redact


def rec(msg, name="browser_use.Agent", level=logging.INFO):
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"


class Feed(unittest.TestCase):
    def setUp(self):
        self.feed = UIFeed(secrets=lambda: ["hunter2-password", "me@example.com"])

    def test_agent_and_tool_lines_pass(self):
        for name in ("browser_use.Agent", "browser_use.agent.service", "browser_use.tools", "browser_use.tools.service"):
            self.assertEqual(self.feed.prepare(rec("clicked", name))["type"], "action")

    def test_browser_internals_and_other_libraries_stay_out(self):
        for name in ("browser_use.BrowserSession", "browser_use.browser.session", "bubus", "httpx", "uvicorn.error"):
            self.assertIsNone(self.feed.prepare(rec("x", name)))

    def test_problems_from_anywhere_in_browser_use_are_shown(self):
        self.assertEqual(self.feed.prepare(rec("crashed", "browser_use.BrowserSession", logging.ERROR))["text"], "crashed")
        self.assertIsNone(self.feed.prepare(rec("crashed", "httpx", logging.ERROR)))       # other libraries: never

    def test_a_secret_containing_spaces_is_redacted(self):
        feed = UIFeed(secrets=lambda: ["two  spaces  inside"])
        self.assertNotIn("spaces", feed.prepare(rec("typed two  spaces  inside now"))["text"])

    def test_debug_lines_stay_out(self):
        self.assertIsNone(self.feed.prepare(rec("x", level=logging.DEBUG)))

    def test_stored_secrets_are_redacted(self):
        out = self.feed.prepare(rec("typed hunter2-password into field for me@example.com"))["text"]
        self.assertNotIn("hunter2-password", out)
        self.assertNotIn("me@example.com", out)

    def test_key_shaped_strings_are_redacted_even_if_not_stored(self):
        for leak in (KEY, "AIza" + "a" * 30, "ghp_" + "b" * 30, "Bearer " + "c" * 30):
            self.assertNotIn(leak, redact(f"header {leak} end", []))

    def test_short_secrets_are_not_blanked_everywhere(self):
        self.assertEqual(redact("a cat sat", ["cat"]), "a cat sat")

    def test_long_page_text_is_capped_and_flattened(self):
        out = clean("line one" + chr(10) + "line two " + "x" * 2000)
        self.assertLessEqual(len(out), ui_feed.MAX_LINE)
        self.assertNotIn(chr(10), out)
        self.assertTrue(out.endswith("…"))

    def test_ansi_colour_codes_are_removed(self):
        self.assertEqual(clean(chr(27) + "[32mgreen" + chr(27) + "[0m"), "green")

    def test_step_lines_carry_the_step_number(self):
        self.assertEqual(self.feed.prepare(rec("📍 Step 3: Evaluating page"))["step"], 3)
        self.assertEqual(self.feed.prepare(rec("Step 12"))["step"], 12)
        self.assertNotIn("step", self.feed.prepare(rec("Clicked Step 3 button")))

    def test_blank_lines_are_dropped(self):
        self.assertIsNone(self.feed.prepare(rec("   ")))

    def test_env_secrets_reads_the_environment(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": KEY, "LINKEDIN_EMAIL": "ab"}, clear=False):
            got = ui_feed.env_secrets()
        self.assertIn(KEY, got)
        self.assertNotIn("ab", got)              # too short to be worth blanking


if __name__ == "__main__":
    unittest.main()
