"""Unit tests for env_store (the settings file) and providers (no browser, no app)."""
import os
import pathlib
import random
import tempfile
import unittest
from unittest import mock

import env_store
import providers


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.path = self.dir / ".env"

    def write(self, updates):
        env_store.write(self.path, updates)

    def read(self):
        return env_store.read(self.path)


NASTY = ["abc\\", "\\", "\\\\", "a\\'", "x #y", "# not a comment", "'", '"', "'single'", '"double"',
         "it's", 'say "hi"', "pa$$word", "${HOME}", "a=b=c", "==", " leading", "trailing ", "  both  ",
         "tab\tinside", "üñí€ 密码", "a" * 500, "back\\slash and 'quote' and \"dq\" and # and $",
         "ends with backslash \\", "C:\\Users\\someone\\file", "\\n", "\\u0041", "{\"json\": \"yes\"}"]


class RoundTrip(Base):
    def test_awkward_values_survive_exactly(self):
        for value in NASTY:
            self.write({"SECRET": value})
            self.assertEqual(self.read()["SECRET"], value, repr(value))

    def test_fuzz_random_values_survive_exactly(self):
        """Thousands of random awkward passwords: the file must never corrupt or change one."""
        rnd = random.Random(20260930)
        alphabet = list("abcXYZ019 #'\"\\$=\t;,.:/{}[]()!@%^&*~`|<>?-_+") + ["é", "ß", "密", "€", "😀"]
        def random_value():
            return "".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 40)))
        for _ in range(20000):                         # volume: encode -> parse, in memory
            value = random_value()
            self.assertEqual(env_store.parse("K=" + env_store.encode_value(value))["K"], value, repr(value))
        for _ in range(150):                           # and through the real file, with neighbours
            value = random_value()
            self.write({"KEY_A": "keep me", "SECRET": value, "KEY_B": "and me"})
            got = self.read()
            self.assertEqual((got["SECRET"], got["KEY_A"], got["KEY_B"]), (value, "keep me", "and me"), repr(value))

    def test_one_bad_value_cannot_damage_its_neighbours(self):
        """A value ending in a backslash used to swallow the closing quote and corrupt the rest of the file."""
        self.write({"FIRST": "one", "PASSWORD": "abc\\", "LAST": "three"})
        self.assertEqual(self.read(), {"FIRST": "one", "PASSWORD": "abc\\", "LAST": "three"})
        self.write({"MIDDLE": "x #y"})
        self.assertEqual(self.read(), {"FIRST": "one", "PASSWORD": "abc\\", "LAST": "three", "MIDDLE": "x #y"})


class FileHandling(Base):
    def test_comments_order_unknown_lines_and_crlf_are_preserved(self):
        self.path.write_bytes(b"# my notes\r\nKEEP=1\r\n\r\nNAME=old\r\nnot an assignment\r\nOTHER=2\r\n")
        self.write({"NAME": "new"})
        raw = self.path.read_bytes().decode()
        self.assertEqual(raw.split("\r\n")[0], "# my notes")
        self.assertIn("not an assignment", raw)
        self.assertEqual(raw.index("KEEP"), raw.index("KEEP"))          # order kept: KEEP before NAME before OTHER
        self.assertTrue(raw.index("KEEP") < raw.index("NAME") < raw.index("OTHER"))
        self.assertIn('NAME="new"\r\n', raw)                            # the line ending style is kept too
        self.assertEqual(self.read(), {"KEEP": "1", "NAME": "new", "OTHER": "2"})

    def test_an_empty_or_none_value_clears_the_key(self):
        """#31: a stored API key or password could never be removed from the UI."""
        self.write({"A": "1", "B": "2", "C": "3"})
        self.write({"B": "", "C": None})
        self.assertEqual(self.read(), {"A": "1"})
        self.assertNotIn("B=", self.path.read_text())

    def test_clearing_something_that_is_not_there_is_harmless(self):
        self.write({"A": "1"})
        self.write({"NOPE": ""})
        self.assertEqual(self.read(), {"A": "1"})

    def test_duplicates_of_an_updated_key_collapse_to_one(self):
        self.path.write_text("A=1\nA=2\nB=3\nA=4\n")
        self.write({"A": "new"})
        self.assertEqual(self.path.read_text().count("A="), 1)
        self.assertEqual(self.read(), {"A": "new", "B": "3"})

    def test_creates_the_file_and_folder_when_missing(self):
        deep = self.dir / "a" / "b" / ".env"
        env_store.write(deep, {"X": "1"})
        self.assertEqual(env_store.read(deep), {"X": "1"})

    def test_a_missing_file_reads_as_empty(self):
        self.assertEqual(self.read(), {})

    def test_a_byte_order_mark_from_notepad_is_tolerated(self):
        self.path.write_bytes(b"\xef\xbb\xbfFIRST=1\nSECOND=2\n")
        self.assertEqual(self.read(), {"FIRST": "1", "SECOND": "2"})       # the BOM must not turn into part of the key
        self.write({"THIRD": "3"})
        self.assertEqual(self.read(), {"FIRST": "1", "SECOND": "2", "THIRD": "3"})

    def test_a_failed_write_leaves_the_original_intact_and_no_debris(self):
        self.write({"A": "original"})
        with mock.patch.object(env_store.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.write({"A": "changed"})
        self.assertEqual(self.read(), {"A": "original"})
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


class HandWrittenFiles(Base):
    """People edit the file by hand in ordinary dotenv style; that must keep working."""

    def test_common_forms(self):
        self.path.write_text(
            "PLAIN=value\n"
            "SPACED = spaced value   \n"
            "COMMENTED=abc # a trailing comment\n"
            "HASH_INSIDE=abc#notacomment\n"
            "export EXPORTED=yes\n"
            "SINGLE='sing le'\n"
            'DOUBLE="dou ble"\n'
            'ESC_DQ="say \\"hi\\""\n'
            "ESC_SQ='it\\'s'\n"
            "TRAILING_BS='ends\\\\'\n"
            "EMPTY=\n"
            "# COMMENT=ignored\n")
        got = self.read()
        self.assertEqual(got["PLAIN"], "value")
        self.assertEqual(got["SPACED"], "spaced value")
        self.assertEqual(got["COMMENTED"], "abc")
        self.assertEqual(got["HASH_INSIDE"], "abc#notacomment")
        self.assertEqual(got["EXPORTED"], "yes")
        self.assertEqual(got["SINGLE"], "sing le")
        self.assertEqual(got["DOUBLE"], "dou ble")
        self.assertEqual(got["ESC_DQ"], 'say "hi"')
        self.assertEqual(got["ESC_SQ"], "it's")
        self.assertEqual(got["TRAILING_BS"], "ends\\")          # python-dotenv cannot read this at all
        self.assertEqual(got["EMPTY"], "")
        self.assertNotIn("COMMENT", got)

    def test_an_unterminated_quote_does_not_crash(self):
        self.path.write_text('BROKEN="never closed\nOK=1\n')
        got = self.read()
        self.assertEqual(got["OK"], "1")
        self.assertIn("BROKEN", got)


class Environment(Base):
    def test_the_file_wins_over_a_stale_shell_variable_but_empty_values_do_not(self):
        """#36: get_llm() used to prefer whatever the shell happened to have."""
        self.write({"LLM_PROVIDER": "deepseek"})
        self.path.write_text(self.path.read_text() + "BLANK=\n")
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "claude", "BLANK": "shell-value", "ONLY_SHELL": "kept"}):
            applied = env_store.apply_to_environ(self.path)
            self.assertEqual(os.environ["LLM_PROVIDER"], "deepseek")
            self.assertEqual(os.environ["BLANK"], "shell-value")
            self.assertEqual(os.environ["ONLY_SHELL"], "kept")
            self.assertEqual(applied, ["LLM_PROVIDER"])

    def test_forget_removes_keys_from_the_process(self):
        with mock.patch.dict(os.environ, {"GONE": "1", "STAYS": "2"}):
            env_store.forget(["GONE", "NEVER_SET"])
            self.assertNotIn("GONE", os.environ)
            self.assertEqual(os.environ["STAYS"], "2")


ALL = ["LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "QWEN_API_KEY",
       "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD", "MAX_STEPS", "CONTEXT_TURNS", "APPROVAL_MODE"]


def check(**body):
    return env_store.validate_settings(body, ALL)


class Validation(unittest.TestCase):
    def test_good_values_are_accepted_and_normalised(self):
        updates, errors = check(LLM_PROVIDER=" Anthropic ", LLM_MODEL="deepseek/deepseek-v4-pro",
                                MAX_STEPS="45", CONTEXT_TURNS=6, APPROVAL_MODE="AUTO",
                                ANTHROPIC_API_KEY="  sk-ant-abcdefgh1234  ", LINKEDIN_EMAIL=" me@example.com ")
        self.assertEqual(errors, {})
        self.assertEqual(updates, {"LLM_PROVIDER": "claude", "LLM_MODEL": "deepseek-v4-pro", "MAX_STEPS": "45",
                                   "CONTEXT_TURNS": "6", "APPROVAL_MODE": "auto",
                                   "ANTHROPIC_API_KEY": "sk-ant-abcdefgh1234", "LINKEDIN_EMAIL": "me@example.com"})

    def test_junk_numbers_are_rejected_not_stored(self):
        """#33: a junk MAX_STEPS crashed a run."""
        for key, bad in (("MAX_STEPS", "abc"), ("MAX_STEPS", "0"), ("MAX_STEPS", "9999"), ("MAX_STEPS", "1.5"),
                         ("MAX_STEPS", True), ("CONTEXT_TURNS", "-1"), ("CONTEXT_TURNS", "21")):
            updates, errors = check(**{key: bad})
            self.assertIn(key, errors, (key, bad))
            self.assertNotIn(key, updates)

    def test_empty_means_clear(self):
        updates, errors = check(ANTHROPIC_API_KEY="", LINKEDIN_PASSWORD="", LLM_MODEL="  ", MAX_STEPS=None)
        self.assertEqual(errors, {})
        self.assertEqual(updates, {"ANTHROPIC_API_KEY": None, "LINKEDIN_PASSWORD": None, "LLM_MODEL": None, "MAX_STEPS": None})

    def test_line_breaks_and_control_characters_are_rejected_everywhere(self):
        """The old writer let a newline inject extra lines into the file."""
        for key in ALL:
            for evil in ("abc" + chr(10) + "INJECTED=1", "abc" + chr(13) + "x", "a" + chr(0) + "b"):
                _, errors = check(**{key: evil})
                self.assertIn(key, errors, (key, repr(evil)))

    def test_bad_provider_model_key_and_email(self):
        self.assertIn("LLM_PROVIDER", check(LLM_PROVIDER="skynet")[1])
        self.assertIn("LLM_MODEL", check(LLM_MODEL="bad model; rm -rf /")[1])
        self.assertIn("ANTHROPIC_API_KEY", check(ANTHROPIC_API_KEY="short")[1])
        self.assertIn("ANTHROPIC_API_KEY", check(ANTHROPIC_API_KEY="has some spaces inside key")[1])
        self.assertIn("LINKEDIN_EMAIL", check(LINKEDIN_EMAIL="not-an-email")[1])
        self.assertIn("APPROVAL_MODE", check(APPROVAL_MODE="off")[1])          # the UI can never switch safety off

    def test_the_linkedin_password_keeps_awkward_characters_and_inner_spaces(self):
        updates, errors = check(LINKEDIN_PASSWORD="pa ss #1 'q' \"d\" \\")
        self.assertEqual(errors, {})
        self.assertEqual(updates["LINKEDIN_PASSWORD"], "pa ss #1 'q' \"d\" \\")

    def test_one_bad_field_reports_all_the_bad_fields_and_unknown_keys_are_ignored(self):
        updates, errors = env_store.validate_settings(
            {"MAX_STEPS": "x", "CONTEXT_TURNS": "y", "LLM_MODEL": "ok-model", "SOMETHING_ELSE": "z"}, ALL)
        self.assertEqual(set(errors), {"MAX_STEPS", "CONTEXT_TURNS"})
        self.assertNotIn("SOMETHING_ELSE", updates)


class Providers(unittest.TestCase):
    def test_aliases_and_defaults(self):
        self.assertEqual(providers.resolve_provider_id("anthropic"), "claude")
        self.assertEqual(providers.resolve_provider_id(" Google "), "gemini")
        self.assertEqual(providers.resolve_provider_id(""), "claude")
        self.assertEqual(providers.resolve_provider_id(None), "claude")
        self.assertIsNone(providers.resolve_provider_id("skynet"))

    def test_litellm_style_model_prefixes_are_stripped(self):
        self.assertEqual(providers.strip_model_prefix("deepseek/deepseek-v4-pro"), "deepseek-v4-pro")
        self.assertEqual(providers.strip_model_prefix("OpenAI/gpt-4o"), "gpt-4o")
        self.assertEqual(providers.strip_model_prefix("claude-sonnet-5-5"), "claude-sonnet-5-5")
        self.assertEqual(providers.strip_model_prefix(""), "")

    def test_every_default_is_one_of_its_own_suggestions(self):
        for p in providers.PROVIDERS:
            self.assertIn(p.default_model, p.suggestions, p.id)

    def test_the_claude_default_is_current(self):
        self.assertEqual(providers.BY_ID["claude"].default_model, "claude-sonnet-5-5")


if __name__ == "__main__":
    unittest.main()
