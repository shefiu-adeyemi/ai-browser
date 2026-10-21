"""Tests for encrypting stored secrets (secret_box.py + its use in env_store.py)."""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from aiba.storage import env as env_store
from aiba.storage import secret_box
WIN = unittest.skipUnless(secret_box.available(), "Windows DPAPI only")
KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"
BACKSLASH = chr(92)


def tmp_env():
    return pathlib.Path(tempfile.mkdtemp()) / ".env"


@WIN
class Box(unittest.TestCase):
    def test_round_trip_including_awkward_characters(self):
        for text in (KEY, "pä ss wörd 😀", "abc" + BACKSLASH, "a" * 1500):
            sealed = secret_box.protect(text)
            self.assertTrue(sealed.startswith("dpapi:"))
            self.assertNotIn(text, sealed)
            self.assertEqual(secret_box.unprotect(sealed), text)

    def test_plain_and_undecryptable_values(self):
        self.assertEqual(secret_box.unprotect("plain"), "plain")
        self.assertEqual(secret_box.unprotect("dpapi:AAAA"), "")          # garbage -> 'not set'
        self.assertEqual(secret_box.unprotect("dpapi:***"), "")
        self.assertEqual(secret_box.protect(""), "")
        sealed = secret_box.protect("x" * 10)
        self.assertEqual(secret_box.protect(sealed), sealed)              # never double-encrypted


class InTheFile(unittest.TestCase):
    @WIN
    def test_secrets_are_not_plain_text_on_disk_but_read_back(self):
        path = tmp_env()
        env_store.write(path, {"OPENAI_API_KEY": KEY, "LINKEDIN_PASSWORD": "hunter2-pass",
                               "LINKEDIN_EMAIL": "me@example.com", "LLM_MODEL": "gpt-4o"})
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(KEY, text)
        self.assertNotIn("hunter2-pass", text)
        self.assertIn("me@example.com", text)                             # not a secret
        got = env_store.read(path)
        self.assertEqual((got["OPENAI_API_KEY"], got["LINKEDIN_PASSWORD"]), (KEY, "hunter2-pass"))

    @WIN
    def test_an_old_plain_text_file_is_encrypted_when_applied(self):
        path = tmp_env()
        path.write_text(f'# mine{chr(10)}OPENAI_API_KEY="{KEY}"{chr(10)}LLM_MODEL=gpt-4o{chr(10)}', encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            env_store.apply_to_environ(path)
            self.assertEqual(os.environ["OPENAI_API_KEY"], KEY)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(KEY, text)
        self.assertIn("# mine", text)
        self.assertIn("LLM_MODEL=gpt-4o", text)

    @WIN
    def test_clearing_a_secret_removes_it(self):
        path = tmp_env()
        env_store.write(path, {"OPENAI_API_KEY": KEY})
        env_store.write(path, {"OPENAI_API_KEY": None})
        self.assertEqual(env_store.read(path), {})

    @WIN
    def test_a_secret_this_account_cannot_decrypt_is_reported(self):
        path = tmp_env()
        path.write_text('OPENAI_API_KEY="dpapi:AAAA"' + chr(10) + 'ANTHROPIC_API_KEY="plain-key-123456"' + chr(10),
                        encoding="utf-8")
        self.assertEqual(env_store.unreadable_secrets(path), ["OPENAI_API_KEY"])
        self.assertEqual(env_store.unreadable_secrets(tmp_env()), [])            # no file: nothing to report

    def test_without_dpapi_values_stay_plain_and_work(self):
        path = tmp_env()
        with mock.patch.object(secret_box, "available", return_value=False):
            env_store.write(path, {"OPENAI_API_KEY": KEY})
            self.assertIn(KEY, path.read_text(encoding="utf-8"))
            self.assertEqual(env_store.read(path)["OPENAI_API_KEY"], KEY)


if __name__ == "__main__":
    unittest.main()
