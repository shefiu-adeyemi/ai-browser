"""How the agent is set up for each model: does it use screenshots, and is the end-of-run check on? No browser,
no model: browser-use's Agent is replaced by a recorder, so this only tests OUR decision."""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())

import unittest
from types import SimpleNamespace
from unittest import mock

from aiba.server import main as agent_ui
def setup_for(provider, model):
    seen = {}
    with mock.patch.object(agent_ui, "Agent", side_effect=lambda **kw: seen.update(kw)), \
            mock.patch.object(agent_ui, "linkedin_login_instructions", create=True, return_value=""), \
            mock.patch.dict(_os.environ, {"USE_JUDGE": "1"}):
        agent_ui._new_agent("do it", "do it", SimpleNamespace(model=model), session=mock.Mock(), sensitive_data=None,
                            provider=provider)
    return seen


class Vision(unittest.TestCase):
    def test_a_model_that_sees_gets_screenshots_and_the_end_of_run_check(self):
        for provider, model in (("qwen", "qwen3.7-plus"), ("qwen", "qwen3-vl-flash"), ("claude", "claude-sonnet-5-5"),
                                ("openai", "gpt-4o"), ("gemini", "gemini-2.5-flash")):
            kw = setup_for(provider, model)
            self.assertTrue(kw["use_vision"], (provider, model))
            self.assertTrue(kw["use_judge"], (provider, model))

    def test_a_text_only_model_gets_neither(self):
        for provider, model in (("qwen", "qwen3.7-max"), ("qwen", "qwen-plus"), ("deepseek", "deepseek-v4-pro")):
            kw = setup_for(provider, model)
            self.assertFalse(kw["use_vision"], (provider, model))
            self.assertFalse(kw["use_judge"], (provider, model))            # the reviewer sends screenshots too

    def test_the_users_tick_box_decides_for_the_standard_model(self):
        with mock.patch.dict(_os.environ, {"LLM_PROVIDER": "qwen", "LLM_MODEL": "qwen3.7-plus", "LLM_VISION": "0"}):
            kw = setup_for("qwen", "qwen3.7-plus")
        self.assertFalse(kw["use_vision"])
        self.assertFalse(kw["use_judge"])                                   # no screenshots, so no reviewer either
        with mock.patch.dict(_os.environ, {"LLM_PROVIDER": "deepseek", "LLM_VISION": "1"}):
            kw = setup_for("deepseek", "deepseek-v4-pro")
        self.assertTrue(kw["use_vision"])                                   # "my model can see" is believed

    def test_the_judge_switch_still_works(self):
        with mock.patch.dict(_os.environ, {"USE_JUDGE": "0"}):
            self.assertFalse(agent_ui._judge_enabled("qwen", True))
        self.assertFalse(agent_ui._judge_enabled("qwen", False))
        self.assertTrue(agent_ui._judge_enabled("qwen", True))


class RetryEscalation(unittest.TestCase):
    """A retry may move the run to the Strong model, but never a screenshot-using run to a model that can't see."""
    def escalate(self, uses_vision, strong_provider, strong_model):
        pick = SimpleNamespace(slot=SimpleNamespace(collapsed=False, model=strong_model), provider_id=strong_provider)
        stronger = SimpleNamespace(model=strong_model)
        agent = SimpleNamespace(llm=SimpleNamespace(model="other"), settings=SimpleNamespace(use_vision=uses_vision),
                                token_cost_service=mock.Mock())
        router = mock.Mock(pick=mock.Mock(return_value=pick), llm=mock.Mock(return_value=stronger))
        with mock.patch.object(agent_ui.model_tiers, "router_on", return_value=True), mock.patch.object(agent_ui, "_router", router):
            return agent_ui._escalate_for_retry(agent), agent

    def test_a_text_only_strong_model_is_not_used_by_a_run_that_looks_at_screenshots(self):
        switched, agent = self.escalate(True, "qwen", "qwen3.7-max")
        self.assertIsNone(switched)
        self.assertEqual(agent.llm.model, "other")

    def test_a_strong_model_that_sees_is_used(self):
        switched, agent = self.escalate(True, "qwen", "qwen3.7-plus")
        self.assertEqual((switched, agent.llm.model), ("qwen3.7-plus", "qwen3.7-plus"))

    def test_a_run_without_screenshots_may_use_any_strong_model(self):
        switched, _ = self.escalate(False, "qwen", "qwen3.7-max")
        self.assertEqual(switched, "qwen3.7-max")


if __name__ == "__main__":
    unittest.main()
