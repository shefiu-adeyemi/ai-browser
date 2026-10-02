"""The settings endpoints end to end (#31, #32, #33, #34, #35, #36): file, environment and the AI client."""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import os
import pathlib
import unittest
from unittest import mock

import agent_ui
import env_store
import llm_config
import providers


class SettingsCase(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.env_path = pathlib.Path(_tf.mkdtemp()) / ".env"
        for p in (mock.patch.object(agent_ui, "ENV_PATH", self.env_path),
                  mock.patch.dict(os.environ)):                      # snapshot + restore os.environ
            p.start()
            self.addCleanup(p.stop)
        for key in agent_ui.SETTINGS_KEYS:                           # start from a clean process environment
            os.environ.pop(key, None)
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")

    def get(self):
        return self.c.get("/settings").json()

    def post(self, **body):
        return self.c.post("/settings", json=body)

    def file(self):
        return env_store.read(self.env_path)


class Clearing(SettingsCase):
    def test_a_stored_key_and_password_can_be_removed_from_the_ui(self):
        """#31: an empty value used to be dropped, so nothing could ever be cleared."""
        self.post(DEEPSEEK_API_KEY="sk-deep1234567", LINKEDIN_PASSWORD="hunter2hunter2", LINKEDIN_EMAIL="me@example.com")
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "sk-deep1234567")
        self.assertEqual(self.post(DEEPSEEK_API_KEY="", LINKEDIN_PASSWORD="").status_code, 200)
        self.assertEqual(self.file(), {"LINKEDIN_EMAIL": "me@example.com"})                 # gone from the file ...
        self.assertNotIn("DEEPSEEK_API_KEY", os.environ)                                    # ... and from the running app
        self.assertNotIn("LINKEDIN_PASSWORD", os.environ)
        self.assertEqual(self.get()["DEEPSEEK_API_KEY"], "")

    def test_a_cleared_key_is_really_not_used_any_more(self):
        self.post(LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="sk-deep1234567")
        self.assertEqual(type(llm_config.get_llm()).__name__, "ChatDeepSeek")
        self.post(DEEPSEEK_API_KEY="")
        with self.assertRaises(llm_config.LLMConfigError):                                  # the stale value did not linger
            llm_config.get_llm()

    def test_the_masked_echo_does_not_overwrite_or_clear_a_secret(self):
        self.post(ANTHROPIC_API_KEY="sk-ant-abcdefgh1234")
        echoed = self.get()                                                                 # what the UI holds and sends back
        self.assertTrue(echoed["ANTHROPIC_API_KEY"].startswith("•"))
        self.assertEqual(self.post(**echoed).status_code, 200)
        self.assertEqual(self.file()["ANTHROPIC_API_KEY"], "sk-ant-abcdefgh1234")

    def test_clearing_an_option_returns_to_the_default(self):
        self.post(LLM_MODEL="claude-opus-5-5", MAX_STEPS=30)
        self.post(LLM_MODEL="", MAX_STEPS="")
        self.assertEqual(self.get()["MAX_STEPS"], 60)
        self.assertEqual(self.get()["LLM_MODEL"], "")


class AwkwardValues(SettingsCase):
    def test_passwords_with_quotes_hashes_spaces_and_a_trailing_backslash_survive_the_whole_stack(self):
        """#32: such passwords used to be cut off at ' #', mangled by quotes, or corrupt the file."""
        for password in ["pa ss #1", "it's \"quoted\"", "ends-with-backslash\\", "C:\\path\\x", "a=b=c", "$HOME ${X}",
                         "üñí€密码", "  spaced  "]:
            self.post(LINKEDIN_PASSWORD=password, LINKEDIN_EMAIL="me@example.com")
            self.assertEqual(self.file()["LINKEDIN_PASSWORD"], password, repr(password))
            self.assertEqual(os.environ["LINKEDIN_PASSWORD"], password, repr(password))      # what the agent will type
            self.assertEqual(self.file()["LINKEDIN_EMAIL"], "me@example.com")                # neighbours undamaged

    def test_line_breaks_cannot_inject_extra_settings(self):
        r = self.post(LINKEDIN_PASSWORD="x" + chr(10) + "APPROVAL_MODE=off")
        self.assertEqual(r.status_code, 400)
        self.assertIn("LINKEDIN_PASSWORD", r.json()["errors"])
        self.assertEqual(self.file(), {})

    def test_hand_edited_files_in_dotenv_style_still_load(self):
        self.env_path.write_text("# my notes\nLLM_PROVIDER=openai # chosen by hand\nMAX_STEPS = 45\nOPENAI_API_KEY='sk-hand1234567'\n")
        got = self.get()
        self.assertEqual((got["LLM_PROVIDER"], got["MAX_STEPS"]), ("openai", 45))
        self.assertTrue(got["OPENAI_API_KEY"].endswith("4567"))
        self.post(MAX_STEPS=50)                                                              # saving keeps the comment
        self.assertIn("# my notes", self.env_path.read_text())


class Validation(SettingsCase):
    def test_bad_values_are_refused_with_a_reason_per_field_and_nothing_is_written(self):
        """#33: a junk MAX_STEPS crashed a run; one bad field must not leave the file half-updated."""
        self.post(LLM_MODEL="keep-me", MAX_STEPS=40)
        before = self.env_path.read_text()
        r = self.post(MAX_STEPS="abc", CONTEXT_TURNS=99, LLM_MODEL="new-model", LINKEDIN_EMAIL="nope")
        self.assertEqual(r.status_code, 400)
        errors = r.json()["errors"]
        self.assertEqual(set(errors), {"MAX_STEPS", "CONTEXT_TURNS", "LINKEDIN_EMAIL"})
        self.assertIn("whole number", errors["MAX_STEPS"])
        self.assertEqual(self.env_path.read_text(), before)                                  # untouched, incl. LLM_MODEL

    def test_junk_already_in_the_file_cannot_break_the_page_or_a_run(self):
        self.env_path.write_text("MAX_STEPS=lots\nCONTEXT_TURNS=-5\n")
        got = self.get()
        self.assertEqual((got["MAX_STEPS"], got["CONTEXT_TURNS"]), (60, 0))                  # clamped/defaulted, no crash

    def test_off_and_unknown_keys_are_ignored_not_errors(self):
        self.env_path.write_text("APPROVAL_MODE=off\n")
        self.assertEqual(self.post(APPROVAL_MODE="off", NOT_A_SETTING="x").status_code, 200)
        self.assertEqual(self.file()["APPROVAL_MODE"], "off")                                # still hand-set, untouched
        self.assertNotIn("NOT_A_SETTING", self.file())


class ProvidersAndDefaults(SettingsCase):
    def test_the_server_tells_the_ui_which_providers_and_models_exist(self):
        """The UI had its own list with 'anthropic' where the server only knows 'claude'."""
        info = self.get()["PROVIDERS"]
        self.assertEqual([p["id"] for p in info], ["claude", "openai", "gemini", "deepseek", "qwen"])
        claude = info[0]
        self.assertEqual((claude["default_model"], claude["key_env"]), ("claude-sonnet-5-5", "ANTHROPIC_API_KEY"))
        self.assertIn("claude-sonnet-5-5", claude["models"])

    def test_an_old_alias_is_shown_and_saved_as_the_real_provider(self):
        self.env_path.write_text("LLM_PROVIDER=anthropic\nLLM_MODEL=deepseek/deepseek-v4-pro\n")
        got = self.get()
        self.assertEqual((got["LLM_PROVIDER"], got["LLM_MODEL"]), ("claude", "deepseek-v4-pro"))
        self.post(LLM_PROVIDER="Anthropic")
        self.assertEqual(self.file()["LLM_PROVIDER"], "claude")

    def test_an_unknown_provider_is_refused(self):
        r = self.post(LLM_PROVIDER="skynet")
        self.assertEqual(r.status_code, 400)
        self.assertIn("claude", r.json()["errors"]["LLM_PROVIDER"])

    def test_defaults_when_nothing_is_set(self):
        got = self.get()
        self.assertEqual((got["LLM_PROVIDER"], got["MAX_STEPS"], got["CONTEXT_TURNS"], got["APPROVAL_MODE"]),
                         ("claude", 60, 3, "ask"))


class TheFileBeatsStaleEnvironment(SettingsCase):
    def test_saving_wins_over_a_stale_shell_variable(self):
        """#36: get_llm() used to prefer whatever key the shell happened to have."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-STALE-shell-value"
        os.environ["LLM_PROVIDER"] = "openai"
        self.post(LLM_PROVIDER="claude", ANTHROPIC_API_KEY="sk-ant-fresh-from-settings")
        llm = llm_config.get_llm()
        self.assertEqual((type(llm).__name__, llm.api_key), ("ChatAnthropic", "sk-ant-fresh-from-settings"))


class DocsMatchCode(unittest.TestCase):
    def test_env_example_lists_the_real_defaults(self):
        """#34: .env.example named a different DeepSeek default than the code used."""
        text = (pathlib.Path(__file__).parent / ".env.example").read_text(encoding="utf-8")
        for p in providers.PROVIDERS:
            self.assertIn(p.default_model, text, f"{p.id}: .env.example does not mention {p.default_model}")
        for stale in ("claude-sonnet-4-5", "deepseek/deepseek-chat", "deepseek-v4-pro,"):
            self.assertNotIn(stale, text)


if __name__ == "__main__":
    unittest.main()
