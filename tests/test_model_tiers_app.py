"""App-level tests for model tiers: the settings, the usage endpoint, and how the agent and playbooks use them."""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import pathlib
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from aiba.server import main as agent_ui
from aiba.llm import tiers as mt
from aiba.playbooks import model as pbk
from fake_llm import FakeLLM

KEYS = {"LLM_PROVIDER": "claude", "ANTHROPIC_API_KEY": "sk-ant-xxxxxxxx", "OPENAI_API_KEY": "sk-oai-xxxxxxxx"}
TIERED = {**KEYS, "LLM_FAST_PROVIDER": "claude", "LLM_FAST_MODEL": "claude-haiku-4-5-20251001",
          "LLM_STRONG_PROVIDER": "claude", "LLM_STRONG_MODEL": "claude-opus-5-5"}


class Chat(FakeLLM):
    """A model that reports its own name, so tiers are told apart."""
    def __init__(self, name):
        super().__init__([])
        self.model = name


def fresh_router(tmp, factory=None):
    ledger = mt.Ledger(tmp / "usage.json")
    router = mt.Router(ledger=ledger, factory=factory or (lambda p, m: Chat(m)))
    return router, ledger


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(_tf.mkdtemp())
        self.router, self.ledger = fresh_router(self.tmp)
        for target, value in (("_router", self.router), ("_ledger", self.ledger)):
            p = mock.patch.object(agent_ui, target, value)
            p.start()
            self.addCleanup(p.stop)

    def env(self, **kw):
        p = mock.patch.dict(_os.environ, {**kw}, clear=False)
        p.start()
        self.addCleanup(p.stop)
        for k in [k for k in ("LLM_FAST_PROVIDER", "LLM_FAST_MODEL", "LLM_STRONG_PROVIDER", "LLM_STRONG_MODEL", "LLM_ROUTER") if k not in kw]:
            if k in _os.environ:
                q = mock.patch.dict(_os.environ, {}, clear=False)
                q.start(); self.addCleanup(q.stop)
                del _os.environ[k]


class Settings(Base):
    def setUp(self):
        super().setUp()
        self.env_path = self.tmp / ".env"
        for target, value in (("ENV_PATH", self.env_path),):
            p = mock.patch.object(agent_ui, target, value)
            p.start()
            self.addCleanup(p.stop)
        from fastapi.testclient import TestClient
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")
        self.env(**KEYS)
        for k in ("LLM_FAST_PROVIDER", "LLM_FAST_MODEL", "LLM_STRONG_PROVIDER", "LLM_STRONG_MODEL", "LLM_ROUTER"):
            _os.environ.pop(k, None)

    def test_defaults_describe_a_single_model_setup(self):
        d = self.c.get("/settings").json()
        self.assertEqual((d["LLM_FAST_MODEL"], d["LLM_STRONG_MODEL"], d["LLM_ROUTER"]), ("", "", "auto"))
        t = d["TIERS"]
        self.assertEqual(t["router"], "auto")
        self.assertTrue(t["slots"]["fast"]["collapsed"] and t["slots"]["strong"]["collapsed"])
        self.assertEqual(t["slots"]["standard"]["model"], "claude-sonnet-5-5")

    def test_the_slots_can_be_saved_and_apply_at_once(self):
        r = self.c.post("/settings", json={"LLM_FAST_PROVIDER": "Anthropic", "LLM_FAST_MODEL": "claude/claude-haiku-4-5-20251001",
                                           "LLM_STRONG_PROVIDER": "claude", "LLM_STRONG_MODEL": "claude-opus-5-5", "LLM_ROUTER": "AUTO"})
        self.assertEqual(r.status_code, 200, r.text)
        d = self.c.get("/settings").json()
        self.assertEqual((d["LLM_FAST_PROVIDER"], d["LLM_FAST_MODEL"]), ("claude", "claude-haiku-4-5-20251001"))
        self.assertEqual(d["TIERS"]["slots"]["fast"]["model"], "claude-haiku-4-5-20251001")
        self.assertFalse(d["TIERS"]["slots"]["fast"]["collapsed"])
        self.assertEqual(mt.pick("extract").model, "claude-haiku-4-5-20251001")          # the live router sees it now

    def test_the_router_can_be_switched_off(self):
        self.c.post("/settings", json={"LLM_FAST_MODEL": "claude-haiku-4-5-20251001", "LLM_ROUTER": "off"})
        d = self.c.get("/settings").json()
        self.assertEqual((d["LLM_ROUTER"], d["TIERS"]["router"]), ("off", "off"))
        self.assertEqual(mt.pick("extract").model, "claude-sonnet-5-5")

    def test_clearing_a_slot_goes_back_to_the_standard_model(self):
        self.c.post("/settings", json={"LLM_FAST_MODEL": "claude-haiku-4-5-20251001"})
        self.c.post("/settings", json={"LLM_FAST_MODEL": "", "LLM_FAST_PROVIDER": ""})
        d = self.c.get("/settings").json()
        self.assertEqual(d["LLM_FAST_MODEL"], "")
        self.assertTrue(d["TIERS"]["slots"]["fast"]["collapsed"])

    def test_bad_values_are_refused_all_or_nothing(self):
        for body, key in (({"LLM_FAST_PROVIDER": "skynet"}, "LLM_FAST_PROVIDER"), ({"LLM_STRONG_PROVIDER": "x y"}, "LLM_STRONG_PROVIDER"),
                          ({"LLM_FAST_MODEL": "has spaces"}, "LLM_FAST_MODEL"), ({"LLM_STRONG_MODEL": "a;b"}, "LLM_STRONG_MODEL"),
                          ({"LLM_ROUTER": "maybe"}, "LLM_ROUTER")):
            r = self.c.post("/settings", json={**body, "MAX_STEPS": 77})
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(key, r.json()["errors"])
        self.assertNotEqual(self.c.get("/settings").json()["MAX_STEPS"], 77)

    def test_a_slot_on_a_provider_without_a_key_is_explained(self):
        _os.environ.pop("GEMINI_API_KEY", None)
        self.c.post("/settings", json={"LLM_FAST_PROVIDER": "gemini", "LLM_FAST_MODEL": "gemini-2.5-flash"})
        s = self.c.get("/settings").json()["TIERS"]["slots"]["fast"]
        self.assertTrue(s["collapsed"] and not s["usable"])
        self.assertIn("Gemini", s["reason"])

    def test_usage_endpoint(self):
        self.ledger.add("fast", "claude-haiku-4-5-20251001", 1000, 100)
        r = self.c.get("/models/usage?days=3").json()
        self.assertEqual(r["usage"]["totals"]["fast"], {"calls": 1, "prompt": 1000, "completion": 100})
        self.assertEqual(r["usage"]["range_days"], 3)
        self.assertEqual(self.c.get("/models/usage?days=999").json()["usage"]["range_days"], 35)
        self.assertIn("slots", r["tiers"])

    def test_the_tiers_endpoint_is_light(self):
        self.ledger.add("fast", "claude-haiku-4-5-20251001", 1000, 100)
        r = self.c.get("/models/tiers").json()
        self.assertTrue(r["ok"])
        self.assertIn("slots", r["tiers"])
        self.assertNotIn("usage", r)

    def test_the_endpoints_need_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.get("/models/usage").status_code, (401, 403))
        self.assertIn(stranger.get("/models/tiers").status_code, (401, 403))


class AgentSetup(Base):
    def test_no_fast_model_means_nothing_changes(self):
        self.env(**KEYS)
        self.assertEqual(agent_ui._agent_tier_kwargs(), {})

    def test_a_fast_model_reads_pages_and_shortens_history(self):
        self.env(**TIERED)
        kw = agent_ui._agent_tier_kwargs()
        self.assertIsInstance(kw["page_extraction_llm"], mt.TieredLLM)
        self.assertEqual(kw["page_extraction_llm"].model, "claude-haiku-4-5-20251001")
        self.assertIsInstance(kw["message_compaction"].compaction_llm, mt.TieredLLM)

    def test_router_off_changes_nothing(self):
        self.env(**TIERED, LLM_ROUTER="off")
        self.assertEqual(agent_ui._agent_tier_kwargs(), {})

    def test_the_agents_helper_models_are_not_counted_twice(self):
        self.env(**TIERED)
        kw = agent_ui._agent_tier_kwargs()
        self.assertIsNone(kw["page_extraction_llm"]._ledger)                     # the run summary already counts them
        self.assertIsNone(kw["message_compaction"].compaction_llm._ledger)

    def test_a_setup_problem_never_blocks_a_run(self):
        self.env(**TIERED)
        with mock.patch.object(self.router, "escalating", side_effect=RuntimeError("boom")):
            self.assertEqual(agent_ui._agent_tier_kwargs(), {})

    def test_a_real_agent_gets_them(self):
        self.env(**TIERED)
        session = mock.MagicMock()
        agent = agent_ui._new_agent("do it", "do it", FakeLLM([]), session, None)
        self.assertIsInstance(agent.settings.page_extraction_llm, mt.TieredLLM)
        self.assertIsInstance(agent.settings.message_compaction.compaction_llm, mt.TieredLLM)
        self.env(**KEYS)
        plain = agent_ui._new_agent("do it", "do it", FakeLLM([]), session, None)
        self.assertNotIsInstance(plain.settings.page_extraction_llm, mt.TieredLLM)


class Retry(Base):
    def agent(self, name="claude-sonnet-5-5"):
        a = NS(llm=Chat(name), registered=[])
        a.token_cost_service = NS(register_llm=lambda l: a.registered.append(l))
        return a

    def test_a_retry_moves_to_the_strong_model(self):
        self.env(**TIERED)
        a = self.agent()
        agent_ui._escalate_for_retry(a)
        self.assertEqual(a.llm.model, "claude-opus-5-5")
        self.assertEqual(a.registered, [a.llm])                                  # its usage is tracked

    def test_it_says_which_model_it_switched_to(self):
        self.env(**TIERED)
        self.assertEqual(agent_ui._escalate_for_retry(self.agent()), "claude-opus-5-5")

    def test_no_strong_model_no_change(self):
        self.env(**KEYS)
        a = self.agent()
        before = a.llm
        self.assertIsNone(agent_ui._escalate_for_retry(a))
        self.assertIs(a.llm, before)

    def test_a_strong_slot_that_is_not_set_never_swaps_the_model(self):
        self.env(**KEYS)
        a = self.agent("some-other-model")                       # e.g. an agent started before the settings changed
        before = a.llm
        self.assertIsNone(agent_ui._escalate_for_retry(a))
        self.assertIs(a.llm, before)

    def test_router_off_no_change(self):
        self.env(**TIERED, LLM_ROUTER="off")
        a = self.agent()
        before = a.llm
        agent_ui._escalate_for_retry(a)
        self.assertIs(a.llm, before)

    def test_already_on_the_strong_model_no_change(self):
        self.env(**TIERED)
        a = self.agent("claude-opus-5-5")
        before = a.llm
        agent_ui._escalate_for_retry(a)
        self.assertIs(a.llm, before)

    def test_the_strong_model_may_be_from_another_provider(self):
        self.env(**KEYS, LLM_STRONG_PROVIDER="openai", LLM_STRONG_MODEL="gpt-5")
        a = self.agent()
        self.assertEqual(agent_ui._escalate_for_retry(a), "gpt-5")
        self.assertEqual(a.llm.model, "gpt-5")
        self.assertEqual(a.registered, [a.llm])

    def test_a_run_is_never_moved_onto_deepseek(self):
        """DeepSeek can't see screenshots or give structured answers: a run that began on another model would break."""
        self.env(**KEYS, DEEPSEEK_API_KEY="sk-ds-xxxxxxxx", LLM_STRONG_PROVIDER="deepseek", LLM_STRONG_MODEL="deepseek-v4-pro")
        a = self.agent()
        before = a.llm
        self.assertIsNone(agent_ui._escalate_for_retry(a))
        self.assertIs(a.llm, before)

    def test_a_failure_to_switch_is_not_fatal(self):
        self.env(**TIERED)
        a = self.agent()
        with mock.patch.object(self.router, "llm", side_effect=RuntimeError("no key")):
            agent_ui._escalate_for_retry(a)                                      # must not raise
        self.assertEqual(a.llm.model, "claude-sonnet-5-5")


class Usage(Base):
    def test_a_runs_usage_is_counted_by_tier(self):
        self.env(**TIERED)
        by_model = {"claude-sonnet-5-5": NS(prompt_tokens=5000, completion_tokens=400, invocations=12),
                    "claude-haiku-4-5-20251001": NS(prompt_tokens=800, completion_tokens=60, invocations=3)}
        agent = NS(history=NS(usage=NS(by_model=by_model)))
        agent_ui._record_agent_usage(agent)
        t = self.ledger.summary()["totals"]
        self.assertEqual((t["standard"]["calls"], t["fast"]["calls"]), (12, 3))

    def test_continuing_a_paused_task_does_not_count_its_first_half_again(self):
        self.env(**TIERED)
        agent = NS(history=NS(usage=NS(by_model={"claude-sonnet-5-5": NS(prompt_tokens=1000, completion_tokens=100, invocations=4)})))
        agent_ui._record_agent_usage(agent)
        agent.history.usage.by_model = {"claude-sonnet-5-5": NS(prompt_tokens=1600, completion_tokens=180, invocations=7)}
        agent_ui._record_agent_usage(agent)                                      # a Continue: the totals now include the first run
        agent_ui._record_agent_usage(agent)                                      # and nothing new after that
        t = self.ledger.summary()["totals"]["standard"]
        self.assertEqual((t["calls"], t["prompt"], t["completion"]), (7, 1600, 180))

    def test_two_different_agents_are_counted_separately(self):
        self.env(**TIERED)
        for _ in range(2):
            agent_ui._record_agent_usage(NS(history=NS(usage=NS(by_model={"claude-sonnet-5-5": NS(prompt_tokens=100, completion_tokens=10, invocations=1)}))))
        self.assertEqual(self.ledger.summary()["totals"]["standard"]["prompt"], 200)

    def test_a_run_without_usage_numbers_or_a_broken_history_is_fine(self):
        agent_ui._record_agent_usage(NS(history=NS(usage=None)))
        agent_ui._record_agent_usage(NS(history=None))
        agent_ui._record_agent_usage(NS())
        self.assertEqual(sum(t["calls"] for t in self.ledger.summary()["totals"].values()), 0)


class PlaybookReading(Base, unittest.IsolatedAsyncioTestCase):
    async def test_a_playbook_that_reads_a_page_uses_the_fast_model_first(self):
        self.env(**TIERED)
        pb = pbk.Playbook(name="Read", steps=[pbk.Step(id="s1", kind="extract", tag="ai", query="the title")])
        with mock.patch.object(agent_ui, "_ensure_session", new=mock.AsyncMock(return_value=object())), \
                mock.patch.object(agent_ui, "trim_tabs", new=mock.AsyncMock()):
            player = await agent_ui._build_player(pb, attended=False)
        llm = player.driver.llm
        self.assertIsInstance(llm, mt.TieredLLM)
        self.assertEqual([p.slot.tier for p in llm.picks], ["fast", "standard", "strong"])

    async def test_with_no_api_key_at_all_the_step_gets_no_model_and_says_so(self):
        self.env(LLM_PROVIDER="claude", ANTHROPIC_API_KEY="")
        _os.environ.pop("ANTHROPIC_API_KEY", None)
        pb = pbk.Playbook(name="Read", steps=[pbk.Step(id="s1", kind="extract", tag="ai", query="the title")])
        with mock.patch.object(agent_ui, "_ensure_session", new=mock.AsyncMock(return_value=object())), \
                mock.patch.object(agent_ui, "trim_tabs", new=mock.AsyncMock()):
            player = await agent_ui._build_player(pb, attended=False)
        self.assertIsNone(player.driver.llm)

    async def test_a_playbook_without_a_reading_step_builds_no_model(self):
        self.env(**TIERED)
        pb = pbk.Playbook(name="Open", steps=[pbk.Step(id="s1", kind="open", url="https://a.com/")])
        with mock.patch.object(agent_ui, "_ensure_session", new=mock.AsyncMock(return_value=object())), \
                mock.patch.object(agent_ui, "trim_tabs", new=mock.AsyncMock()), \
                mock.patch.object(self.router, "escalating", side_effect=AssertionError("no model needed")):
            player = await agent_ui._build_player(pb, attended=False)
        self.assertIsNone(player.driver.llm)


if __name__ == "__main__":
    unittest.main()
