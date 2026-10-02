"""Tests for llm_config (no network, no real API keys)."""
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import llm_config
import providers

KEYS = {"ANTHROPIC_API_KEY": "sk-ant-test1234567", "OPENAI_API_KEY": "sk-test1234567",
        "GEMINI_API_KEY": "AIzaTest1234567", "DEEPSEEK_API_KEY": "sk-deep1234567", "QWEN_API_KEY": "sk-qwen1234567"}
ROOT = pathlib.Path(__file__).parent


def build(provider="", model="", **extra):
    env = {**KEYS, "LLM_PROVIDER": provider, "LLM_MODEL": model, **extra}
    with mock.patch.dict(os.environ, env):
        return llm_config.get_llm()


class Clients(unittest.TestCase):
    def test_each_provider_builds_its_native_client_with_the_table_default(self):
        expected = {"claude": "ChatAnthropic", "openai": "ChatOpenAI", "gemini": "ChatGoogle", "deepseek": "ChatDeepSeek", "qwen": "ChatOpenAI"}
        for p in providers.PROVIDERS:
            llm = build(p.id)
            self.assertEqual(type(llm).__name__, expected[p.id], p.id)
            self.assertEqual(llm.model, p.default_model, p.id)          # the default comes from providers.py

    def test_default_provider_is_claude_and_its_default_is_current(self):
        llm = build("")
        self.assertEqual((type(llm).__name__, llm.model), ("ChatAnthropic", "claude-sonnet-5-5"))

    def test_aliases_and_old_style_model_names_still_work(self):
        self.assertEqual(type(build("anthropic")).__name__, "ChatAnthropic")     # what the old UI saved
        self.assertEqual(build("deepseek", "deepseek/deepseek-v4-pro").model, "deepseek-v4-pro")
        self.assertEqual(build("openai", "OpenAI/gpt-4.1").model, "gpt-4.1")

    def test_an_explicit_model_overrides_the_default(self):
        self.assertEqual(build("claude", "claude-opus-5-5").model, "claude-opus-5-5")

    def test_the_wrong_keys_are_not_used(self):
        """Each provider must read ITS key: DeepSeek must not pick up the Anthropic key."""
        self.assertEqual(build("deepseek").api_key, KEYS["DEEPSEEK_API_KEY"])
        self.assertEqual(build("claude").api_key, KEYS["ANTHROPIC_API_KEY"])


class Qwen(unittest.TestCase):
    def test_it_talks_to_alibabas_endpoint_with_its_own_key(self):
        llm = build("qwen")
        self.assertEqual((llm.model, llm.api_key), ("qwen3.7-plus", KEYS["QWEN_API_KEY"]))
        self.assertEqual(str(llm.base_url), llm_config.QWEN_BASE_URL)
        self.assertIn("dashscope-intl", str(llm.base_url))                  # international region by default
        self.assertIsNone(llm.frequency_penalty)                            # OpenAI-only guard, not sent to Alibaba

    def test_a_mainland_key_can_point_at_the_mainland_endpoint(self):
        llm = build("qwen", QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(str(llm.base_url), "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_names_people_type_resolve(self):
        for alias in ("Alibaba", "dashscope", " QWEN "):
            self.assertEqual(providers.resolve_provider_id(alias), "qwen", alias)
        self.assertEqual(providers.strip_model_prefix("qwen/qwen3.7-plus"), "qwen3.7-plus")

    def test_the_default_model_can_see_screenshots(self):
        self.assertTrue(llm_config.supports_vision("qwen", providers.BY_ID["qwen"].default_model))

    def test_which_models_can_see(self):
        for model in ("qwen3.7-plus", "qwen3-vl-plus", "qwen3-vl-flash", "qwen/qwen3.7-plus", "Qwen3.7-Plus", "qwen-omni-turbo", None, ""):
            self.assertTrue(llm_config.supports_vision("qwen", model), model)
        for model in ("qwen3.7-max", "qwen-plus", "qwen3-coder-plus", "something-new"):
            self.assertFalse(llm_config.supports_vision("qwen", model), model)     # unknown = no: a text-only model refuses images

    def test_other_providers_are_unchanged(self):
        self.assertFalse(llm_config.supports_vision("deepseek", "deepseek-v4-pro"))
        for p in ("claude", "openai", "gemini"):
            self.assertTrue(llm_config.supports_vision(p, "anything"), p)

    def test_a_missing_key_says_so(self):
        with mock.patch.dict(os.environ, {"QWEN_API_KEY": ""}):
            with self.assertRaises(llm_config.LLMConfigError) as cm:
                llm_config.build_llm("qwen")
        self.assertIn("Qwen", str(cm.exception))
        self.assertIn("QWEN_API_KEY", str(cm.exception))


class Errors(unittest.TestCase):
    def test_unknown_provider_lists_the_valid_ones(self):
        with mock.patch.dict(os.environ, {**KEYS, "LLM_PROVIDER": "skynet"}):
            with self.assertRaises(llm_config.LLMConfigError) as cm:
                llm_config.get_llm()
        self.assertIn("skynet", str(cm.exception))
        for p in providers.PROVIDERS:
            self.assertIn(p.id, str(cm.exception))

    def test_a_missing_key_says_where_to_add_it(self):
        for p in providers.PROVIDERS:
            env = {**KEYS, "LLM_PROVIDER": p.id, p.key_env: "   "}
            with mock.patch.dict(os.environ, env):
                with self.assertRaises(llm_config.LLMConfigError) as cm:
                    llm_config.get_llm()
            self.assertIn(p.label, str(cm.exception))
            self.assertIn("Settings", str(cm.exception))
            self.assertIn(p.key_env, str(cm.exception))


class NoLiteLLM(unittest.TestCase):
    def test_the_default_paths_never_import_litellm(self):
        """#37/#38: the global monkeypatch and the extra dependency are gone from the normal route."""
        code = ("import os, sys; os.environ.update(%r); import llm_config\n"
                "for p in ('claude','openai','gemini','deepseek'):\n"
                "    os.environ['LLM_PROVIDER'] = p; llm_config.get_llm()\n"
                "print('litellm' in sys.modules)" % KEYS)
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.stdout.strip().splitlines()[-1], "False", out.stderr[-500:])


class NativeDeepSeekPipeline(unittest.IsolatedAsyncioTestCase):
    """What the native client actually sends and how it parses the reply, against the REAL agent
    schema, with the network replaced by a fake. (This shows the request is well-formed; it cannot
    prove how a live DeepSeek model behaves.)"""

    def output_model(self):
        from browser_use.agent.views import AgentOutput
        from browser_use.tools.service import Tools
        return AgentOutput.type_with_custom_actions(Tools().registry.create_action_model())

    def fake_client(self, reply_args):
        calls = []

        async def create(**kw):
            calls.append(kw)
            call = SimpleNamespace(function=SimpleNamespace(arguments=json.dumps(reply_args)))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return client, calls

    async def test_sends_a_forced_function_call_and_no_response_format(self):
        from browser_use.llm.deepseek.chat import ChatDeepSeek
        from browser_use.llm.messages import UserMessage
        Out = self.output_model()
        reply = {"thinking": "t", "evaluation_previous_goal": "e", "memory": "m", "next_goal": "n",
                 "action": [{"click": {"index": 3}}]}
        client, calls = self.fake_client(reply)
        llm = ChatDeepSeek(model="deepseek-v4-pro", api_key="sk-deep1234567")
        with mock.patch.object(ChatDeepSeek, "_client", lambda self: client):
            result = await llm.ainvoke([UserMessage(content="click the button")], output_format=Out)
        (sent,) = calls
        self.assertNotIn("response_format", sent)                  # the thing DeepSeek rejected via LiteLLM
        self.assertEqual(sent["tool_choice"], {"type": "function", "function": {"name": Out.__name__}})
        schema = json.dumps(sent["tools"][0]["function"]["parameters"])
        self.assertIn('"index"', schema)                           # the model is TOLD the field is `index`
        self.assertNotIn("element_index", schema)                  # so the old renaming hack has nothing to fix
        self.assertEqual(sent["model"], "deepseek-v4-pro")
        self.assertIsInstance(result.completion, Out)
        self.assertEqual(result.completion.action[0].model_dump(exclude_unset=True), {"click": {"index": 3}})

    async def test_a_reply_that_breaks_the_schema_is_a_clean_provider_error(self):
        from browser_use.llm.deepseek.chat import ChatDeepSeek
        from browser_use.llm.exceptions import ModelProviderError
        from browser_use.llm.messages import UserMessage
        client, _ = self.fake_client({"action": [{"click": {"element_index": 3}}]})    # the old quirk
        llm = ChatDeepSeek(model="deepseek-v4-pro", api_key="sk-deep1234567")
        with mock.patch.object(ChatDeepSeek, "_client", lambda self: client):
            with self.assertRaises(ModelProviderError):
                await llm.ainvoke([UserMessage(content="x")], output_format=self.output_model())


if __name__ == "__main__":
    unittest.main()
