"""Regression tests for credential scoping and browser hardening.  Run: python -m unittest -v"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import os
import unittest
from unittest import mock

from pydantic import BaseModel
from browser_use.tools.registry.service import Registry

import agent_ui
import site_credentials as sc

CREDS = {"LINKEDIN_EMAIL": "me@example.com", "LINKEDIN_PASSWORD": "pw-123"}


class _P(BaseModel):
    text: str


def _substitute(url, text="<secret>linkedin_password</secret>"):
    with mock.patch.dict(os.environ, CREDS):
        return Registry()._replace_sensitive_data(_P(text=text), sc.linkedin_sensitive_data(), url).text


class CredentialScoping(unittest.TestCase):
    def test_shape_is_domain_scoped(self):
        with mock.patch.dict(os.environ, CREDS):
            sd = sc.linkedin_sensitive_data()
        self.assertEqual(list(sd), [sc.LINKEDIN_DOMAIN])
        self.assertTrue(all(isinstance(v, dict) for v in sd.values()))  # never a flat dict

    def test_none_without_credentials(self):
        with mock.patch.dict(os.environ, {"LINKEDIN_EMAIL": "", "LINKEDIN_PASSWORD": "x"}):
            self.assertIsNone(sc.linkedin_sensitive_data())

    def test_substituted_on_linkedin(self):
        for url in ("https://www.linkedin.com/login", "https://linkedin.com/checkpoint"):
            self.assertEqual(_substitute(url), "pw-123", url)

    def test_never_substituted_elsewhere(self):
        for url in ("https://accounts.google.com/", "https://linkedin.com.evil.com/",
                    "https://evillinkedin.com/", "https://evil.com/?linkedin.com",
                    "https://www.linkedin.com@evil.com/", "http://www.linkedin.com/",
                    "about:blank", None):
            self.assertNotEqual(_substitute(url), "pw-123", url)

    def test_prompt_uses_secret_tag_syntax(self):
        with mock.patch.dict(os.environ, CREDS):
            text = sc.linkedin_login_instructions()
        self.assertIn("<secret>linkedin_password</secret>", text)
        self.assertNotIn("<linkedin_password>", text)   # bare form is never substituted


class PromptGating(unittest.TestCase):
    def test_credentials_only_mentioned_for_linkedin_tasks(self):
        with mock.patch.dict(os.environ, CREDS):
            self.assertIn("linkedin_password", agent_ui._augment_task("Find jobs on LinkedIn"))
            self.assertNotIn("linkedin_password", agent_ui._augment_task("What time is it?"))

    def test_other_sites_wait_for_user(self):
        with mock.patch.dict(os.environ, CREDS):
            self.assertIn("any other site", agent_ui._augment_task("Check LinkedIn"))
        self.assertIn("any login", agent_ui._augment_task("Open example.com"))


class BrowserHardening(unittest.TestCase):
    def test_flags(self):
        prof = agent_ui._make_session().browser_profile
        self.assertFalse(prof.disable_security)
        self.assertNotIn("--no-sandbox", prof.args)
        self.assertTrue(prof.chromium_sandbox)

    def test_agent_browser_cannot_reach_the_control_ui(self):
        rules = [a for a in agent_ui._make_session().browser_profile.args if a.startswith("--host-resolver-rules=")]
        self.assertEqual(len(rules), 1)
        for host in agent_ui._ALLOWED_HOSTS:          # exactly the hosts the server accepts
            self.assertIn(f"MAP {host} ~NOTFOUND", rules[0])


if __name__ == "__main__":
    unittest.main()
