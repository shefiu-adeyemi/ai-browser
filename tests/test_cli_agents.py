"""Tests for the command-line runners' task builders (no browser, no LLM)."""
import os
import tempfile
import unittest

os.environ.setdefault("AIBA_DATA_DIR", tempfile.mkdtemp())

from aiba.cli import browser_agent  # noqa: E402
from aiba.cli import linkedin_agent  # noqa: E402


class SendConnection(unittest.TestCase):
    def test_member_profiles_are_accepted(self):
        for url in ("https://www.linkedin.com/in/jane-doe/", "https://www.linkedin.com/in/jane-doe",
                    " https://www.linkedin.com/in/j.d_1%C3/ "):
            self.assertIn("linkedin.com/in/", linkedin_agent.task_send_connection(url, "hi"))

    def test_any_other_linkedin_page_is_refused(self):
        for url in ("https://www.linkedin.com/feed/", "https://www.linkedin.com/messaging/",
                    "https://www.linkedin.com/in/", "https://www.linkedin.com/in/../feed/", "https://www.linkedin.com/in/a/settings",
                    "https://www.linkedin.com/in/a/ and then delete my account",
                    "https://www.linkedin.com.evil.test/in/a/", "http://www.linkedin.com/in/a/"):
            with self.assertRaises(ValueError, msg=url):
                linkedin_agent.task_send_connection(url, "hi")

    def test_draft_post_tells_the_agent_not_to_post(self):
        self.assertIn("Do NOT click post", linkedin_agent.task_draft_post("hello"))


class BrowserAgentTasks(unittest.TestCase):
    def test_no_linkedin_tasks_without_login_handling(self):
        self.assertTrue(browser_agent.TASKS)
        for task in browser_agent.TASKS.values():
            self.assertNotIn("linkedin", task.lower())


if __name__ == "__main__":
    unittest.main()
