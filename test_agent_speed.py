"""Fast mode: how much the agent asks of the model each step. No browser, no model."""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())

import pathlib
import unittest
from types import SimpleNamespace
from unittest import mock

import agent_ui
import env_store
import llm_config


def agent_kwargs(provider, **env):
    seen = {}
    with mock.patch.object(agent_ui, "Agent", side_effect=lambda **kw: seen.update(kw)), \
            mock.patch.dict(_os.environ, {"AGENT_SPEED": "", **env}):
        agent_ui._new_agent("do it", "do it", SimpleNamespace(model="m"), session=mock.Mock(), sensitive_data=None,
                            provider=provider)
    return seen


class Automatic(unittest.TestCase):
    def test_deepseek_runs_fast_and_the_others_run_careful(self):
        self.assertTrue(llm_config.fast_mode("deepseek"))
        for provider in ("claude", "openai", "gemini", "qwen"):
            self.assertFalse(llm_config.fast_mode(provider), provider)

    def test_the_users_choice_wins_either_way(self):
        with mock.patch.dict(_os.environ, {"AGENT_SPEED": "careful"}):
            self.assertFalse(llm_config.fast_mode("deepseek"))
        with mock.patch.dict(_os.environ, {"AGENT_SPEED": "fast"}):
            self.assertTrue(llm_config.fast_mode("claude"))
        with mock.patch.dict(_os.environ, {"AGENT_SPEED": "banana"}):
            self.assertTrue(llm_config.fast_mode("deepseek"))                  # junk = automatic
            self.assertFalse(llm_config.fast_mode("claude"))


class WhatTheAgentGets(unittest.TestCase):
    def test_fast_asks_the_model_for_less_and_allows_more_actions_per_step(self):
        kw = agent_kwargs("deepseek")
        self.assertEqual((kw["flash_mode"], kw["max_actions_per_step"], kw["enable_planning"]), (True, 3, False))

    def test_careful_is_what_it_always_was(self):
        kw = agent_kwargs("claude")
        self.assertEqual((kw["flash_mode"], kw["max_actions_per_step"], kw["enable_planning"]), (False, 1, True))

    def test_the_setting_changes_it_for_any_provider(self):
        self.assertTrue(agent_kwargs("claude", AGENT_SPEED="fast")["flash_mode"])
        self.assertFalse(agent_kwargs("deepseek", AGENT_SPEED="careful")["flash_mode"])

    def test_the_safety_layer_is_not_loosened_by_speed(self):
        for provider in ("deepseek", "claude"):
            kw = agent_kwargs(provider)
            self.assertEqual(type(kw["tools"]).__name__, "GuardedTools")
            self.assertIn("extend_system_message", kw)


class Saving(unittest.TestCase):
    def test_values(self):
        check = lambda v: env_store.validate_settings({"AGENT_SPEED": v}, ["AGENT_SPEED"])
        self.assertEqual(check("Fast")[0]["AGENT_SPEED"], "fast")
        self.assertEqual(check("careful")[0]["AGENT_SPEED"], "careful")
        self.assertIsNone(check("auto")[0]["AGENT_SPEED"])
        self.assertIsNone(check("")[0]["AGENT_SPEED"])
        self.assertIn("AGENT_SPEED", check("turbo")[1])


class OverHttp(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.path = pathlib.Path(_tf.mkdtemp()) / ".env"
        for p in (mock.patch.object(agent_ui, "ENV_PATH", self.path), mock.patch.dict(_os.environ)):
            p.start()
            self.addCleanup(p.stop)
        _os.environ.pop("AGENT_SPEED", None)
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")

    def test_it_is_saved_and_comes_back_and_takes_effect_at_once(self):
        self.assertEqual(self.c.get("/settings").json()["AGENT_SPEED"], "")
        self.assertEqual(self.c.post("/settings", json={"AGENT_SPEED": "fast"}).status_code, 200)
        self.assertEqual(self.c.get("/settings").json()["AGENT_SPEED"], "fast")
        self.assertEqual(_os.environ["AGENT_SPEED"], "fast")
        self.c.post("/settings", json={"AGENT_SPEED": ""})
        self.assertEqual(self.c.get("/settings").json()["AGENT_SPEED"], "")
        self.assertNotIn("AGENT_SPEED", _os.environ)
        self.assertEqual(self.c.post("/settings", json={"AGENT_SPEED": "turbo"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
