"""
Model tiers with the REAL agent and a REAL Chromium (scripted fake models, no API cost):
the first attempt runs on the Standard model, the reviewer says it was incomplete, and the retry runs on the
Strong model. Also: a cheap Fast model reads the page, and an invisible tier setup changes nothing.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_model_tiers_browser -v
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import os
import pathlib
import unittest
from unittest import mock

from aiba.server import main as agent_ui
from aiba.llm import tiers as mt
from fake_llm import FakeLLM
from test_lifecycle_browser import LifecycleBase, until

TIERED = {"LLM_PROVIDER": "claude", "ANTHROPIC_API_KEY": "sk-ant-xxxxxxxx", "LLM_STRONG_PROVIDER": "claude",
          "LLM_STRONG_MODEL": "claude-opus-5-5", "LLM_FAST_PROVIDER": "claude", "LLM_FAST_MODEL": "claude-haiku-4-5-20251001"}


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class TiersWithTheRealAgent(LifecycleBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.made: dict[str, FakeLLM] = {}

        def factory(provider, model):
            llm = FakeLLM(self.scripts_by_model.get(model, []), judge=self.judge_by_model.get(model))
            llm.model = model
            self.made[model] = llm
            return llm
        self.scripts_by_model = {"claude-opus-5-5": [{"done": "second try"}]}
        self.judge_by_model: dict[str, list] = {}
        router = mt.Router(ledger=mt.Ledger(pathlib.Path(_tf.mkdtemp()) / "u.json"), factory=factory)
        for target, value in (("_router", router),):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)

    async def test_a_retry_after_an_incomplete_attempt_runs_on_the_strong_model(self):
        with mock.patch.dict(os.environ, TIERED):
            await self.run_to_end([{"navigate": self.url}, {"done": "first try"}], task="do the thing",
                                  judge=[{"verdict": False, "failure_reason": "missed it", "reasoning": "x"}])
        standard = self.llms[0]
        strong = self.made.get("claude-opus-5-5")
        self.assertIsNotNone(strong, "the Strong model was never built")
        self.assertGreaterEqual(strong.calls, 1, "the retry did not use the Strong model")
        self.assertEqual(standard.calls, 2, "the Standard model should have done only the first attempt")
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "second try")
        self.assertEqual(self.ws.statuses()[-1], "done")
        said = [m["text"] for m in self.ws.msgs if m["type"] == "action"]
        self.assertTrue(any("Trying again on the Strong model (claude-opus-5-5)" in t for t in said), said)

    async def test_without_a_strong_model_the_retry_stays_on_the_standard_one(self):
        only_standard = {k: v for k, v in TIERED.items() if "STRONG" not in k and "FAST" not in k}
        with mock.patch.dict(os.environ, only_standard):
            for k in ("LLM_STRONG_PROVIDER", "LLM_STRONG_MODEL", "LLM_FAST_PROVIDER", "LLM_FAST_MODEL"):
                os.environ.pop(k, None)
            await self.run_to_end([{"navigate": self.url}, {"done": "first try"}, {"done": "second try"}], task="do the thing",
                                  judge=[{"verdict": False, "failure_reason": "missed it", "reasoning": "x"}])
        self.assertNotIn("claude-opus-5-5", self.made)
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "second try")

    async def test_router_off_keeps_the_retry_on_the_standard_model(self):
        with mock.patch.dict(os.environ, {**TIERED, "LLM_ROUTER": "off"}):
            await self.run_to_end([{"navigate": self.url}, {"done": "first try"}, {"done": "second try"}], task="do the thing",
                                  judge=[{"verdict": False, "failure_reason": "missed it", "reasoning": "x"}])
        self.assertNotIn("claude-opus-5-5", self.made)

    async def test_a_tier_chosen_for_the_task_runs_every_step_on_that_tier_and_never_climbs(self):
        fast, standard = "claude-haiku-4-5-20251001", mt.read_slots({**TIERED})["standard"].model
        self.scripts_by_model[fast] = [{"navigate": self.url}, {"done": "first try"}, {"done": "second try"}]
        self.judge_by_model[standard] = [{"verdict": False, "failure_reason": "missed it", "reasoning": "x"}]
        with mock.patch.dict(os.environ, TIERED):
            self.scripts.append([])                                  # get_llm() (Standard) must not be asked for the steps
            r = await agent_ui.start_task({"task": "do the thing", "tier": "fast"})
            self.assertTrue(r["ok"], r)
            await until(lambda: not agent_ui._ctl.running, what="run to finish")
        self.assertEqual(self.llms, [], "the Settings model was built although a tier was chosen")
        self.assertEqual(self.made[fast].calls, 3, "the steps (and the retry) should all run on the Fast model")
        self.assertNotIn("claude-opus-5-5", self.made, "a chosen tier must not climb to Strong on a retry")
        self.assertGreaterEqual(self.made[standard].judge_calls, 1, "the reviewer stays on the Standard model")
        self.assertEqual(self.made[standard].calls, 0)
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "second try")
        said = [m["text"] for m in self.ws.msgs if m["type"] == "action"]
        self.assertTrue(any(t == f"Model for this task: Fast ({fast})" for t in said), said)
        self.assertFalse(any("Trying again on the Strong model" in t for t in said), said)

    async def test_a_fast_model_is_given_the_pages_to_read_and_the_agent_still_finishes(self):
        with mock.patch.dict(os.environ, TIERED):
            await self.run_to_end([{"navigate": self.url}, {"done": "all good"}], task="read the page")
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "all good")
        self.assertEqual(self.ws.statuses()[-1], "done")


if __name__ == "__main__":
    unittest.main()
