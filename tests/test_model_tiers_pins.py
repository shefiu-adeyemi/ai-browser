"""A model tier chosen by the user: for one playbook step, or for one chat task.

The choice is a TIER (Fast / Standard / Strong), never a model name, so the models behind the tiers can be changed
in Settings at any time and every saved playbook follows. A chosen tier is used exactly: no climbing on failure.
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import hashlib
import json
import pathlib
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from pydantic import BaseModel

from aiba.server import main as agent_ui
from aiba.llm import tiers as mt
from aiba.playbooks import model as pbk
from fake_llm import FakeLLM
from aiba.playbooks.model import Playbook, Step
from aiba.playbooks.player import PlaybookPlayer, SessionDriver, StepApprover
from aiba.playbooks.store import PlaybookStore
from test_model_tiers_app import KEYS, TIERED, Base, Chat
from test_playbook_player import Clock, FakePage, Inner, S, make_pb

ONLY_FAST = {**KEYS, "LLM_FAST_PROVIDER": "claude", "LLM_FAST_MODEL": "claude-haiku-4-5-20251001"}


class Picking(unittest.TestCase):
    def test_a_chosen_tier_is_the_only_one_tried(self):
        for tier in mt.TIERS:
            chain = mt.chain("playbook_extract", TIERED, tier=tier)
            self.assertEqual([p.slot.tier for p in chain], [tier])

    def test_without_a_choice_the_job_climbs_as_before(self):
        self.assertEqual([p.slot.tier for p in mt.chain("playbook_extract", TIERED)], ["fast", "standard", "strong"])

    def test_a_chosen_tier_that_is_not_set_up_uses_the_standard_model(self):
        chain = mt.chain("agent", ONLY_FAST, tier="strong")
        self.assertEqual(len(chain), 1)
        self.assertTrue(chain[0].slot.collapsed)
        self.assertEqual(chain[0].model, mt.read_slots(ONLY_FAST)["standard"].model)

    def test_with_the_router_off_a_chosen_tier_is_ignored(self):
        env = {**TIERED, "LLM_ROUTER": "off"}
        self.assertEqual([p.slot.tier for p in mt.chain("agent", env, tier="strong")], ["standard"])
        self.assertEqual(mt.pick("agent", env, tier="strong").slot.tier, "standard")

    def test_pick_with_a_tier_overrides_the_table(self):
        self.assertEqual(mt.pick("extract", TIERED).slot.tier, "fast")
        self.assertEqual(mt.pick("extract", TIERED, tier="strong").slot.tier, "strong")

    def test_an_unknown_tier_is_the_same_as_none(self):
        self.assertEqual(mt.pick("extract", TIERED, tier="bogus").slot.tier, "fast")
        self.assertEqual([p.slot.tier for p in mt.chain("extract", TIERED, tier="bogus")], ["fast", "standard", "strong"])

    def test_the_wrapper_says_which_tier_answered(self):
        llm = mt.Router(factory=lambda p, m: Chat(m), env=TIERED).escalating("agent", tier="strong")
        self.assertEqual((llm.tier, llm.model), ("strong", "claude-opus-5-5"))
        collapsed = mt.Router(factory=lambda p, m: Chat(m), env=KEYS).escalating("agent", tier="strong")
        self.assertEqual(collapsed.tier, "standard")                     # honest: it is the Standard model that answers


class CountedHonestly(unittest.IsolatedAsyncioTestCase):
    async def test_a_tier_that_is_not_set_up_is_counted_as_the_standard_model(self):
        ledger = mt.Ledger(pathlib.Path(_tf.mkdtemp()) / "u.json")
        answer = NS(usage=NS(prompt_tokens=10, completion_tokens=5))
        model = Chat("x")
        model.ainvoke = mock.AsyncMock(return_value=answer)
        router = mt.Router(ledger=ledger, factory=lambda p, m: model, env=KEYS)             # Fast and Strong are not set
        await router.escalating("playbook_extract").ainvoke([])
        await router.escalating("playbook_extract", tier="strong").ainvoke([])
        totals = ledger.summary()["totals"]
        self.assertEqual((totals["standard"]["calls"], totals["fast"]["calls"], totals["strong"]["calls"]), (2, 0, 0))


class TokensPerStep(unittest.IsolatedAsyncioTestCase):
    async def test_the_wrapper_adds_up_what_its_calls_used_and_can_start_over(self):
        model = Chat("x")
        model.ainvoke = mock.AsyncMock(return_value=NS(usage=NS(prompt_tokens=10, completion_tokens=5)))
        llm = mt.Router(factory=lambda p, m: model, env=KEYS).escalating("agent")
        await llm.ainvoke([])
        await llm.ainvoke([])
        self.assertEqual(llm.tokens, 30)
        llm.reset_tokens()
        self.assertEqual(llm.tokens, 0)

    async def test_a_failed_call_and_an_answer_without_numbers_add_nothing(self):
        answers = [RuntimeError("down"), NS(usage=None)]

        async def ainvoke(*a, **k):
            r = answers.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        model = Chat("x")
        model.ainvoke = ainvoke
        llm = mt.Router(factory=lambda p, m: model, env=TIERED).escalating("playbook_extract")
        await llm.ainvoke([])                                  # Fast fails, Standard answers without numbers
        self.assertEqual(llm.tokens, 0)


class ChosenTierDoesNotClimb(unittest.IsolatedAsyncioTestCase):
    async def test_a_failing_chosen_tier_fails_instead_of_spending_more(self):
        class Down(Chat):
            async def ainvoke(self, *a, **k):
                raise RuntimeError("provider down")
        made = {}

        def factory(p, m):
            made[m] = Down(m) if "haiku" in m else Chat(m)
            return made[m]
        router = mt.Router(factory=factory, env=TIERED)
        with self.assertRaises(RuntimeError):
            await router.escalating("playbook_extract", tier="fast").ainvoke([])
        self.assertNotIn("claude-opus-5-5", made)
        self.assertEqual(list(made), ["claude-haiku-4-5-20251001"])

    async def test_while_an_automatic_choice_does_climb(self):
        class Down(Chat):
            async def ainvoke(self, *a, **k):
                raise RuntimeError("provider down")
        answered = Chat("x")
        answered.ainvoke = mock.AsyncMock(return_value=NS(usage=None))
        router = mt.Router(factory=lambda p, m: Down(m) if "haiku" in m else answered, env=TIERED)
        await router.escalating("playbook_extract").ainvoke([])
        answered.ainvoke.assert_awaited()


class InThePlaybook(unittest.TestCase):
    def test_the_tier_is_kept_on_a_model_step(self):
        pb = Playbook(name="x", steps=[Step(id="s1", kind="extract", tag="ai", query="q", tier="strong"),
                                       Step(id="s2", kind="ai", tag="ai", query="q", tier="fast")])
        self.assertEqual(pbk.validate(pb), [])

    def test_a_step_that_never_uses_a_model_cannot_carry_one(self):
        pb = Playbook(name="x", steps=[Step(id="s1", kind="open", url="https://a.com/", tier="fast")])
        self.assertTrue(any("has no 'tier'" in p for p in pbk.validate(pb)), pbk.validate(pb))

    def test_only_the_three_tiers_exist(self):
        with self.assertRaises(ValueError):
            Step(id="s1", kind="extract", tag="ai", query="q", tier="claude-opus-5-5")   # a tier, never a model name

    def test_choosing_a_tier_keeps_the_approval(self):
        pb = pbk.with_approval(Playbook(name="x", steps=[Step(id="s1", kind="extract", tag="ai", query="q")]))
        for tier in ("fast", "standard", "strong", None):
            changed = pb.model_copy(update={"steps": [pb.steps[0].model_copy(update={"tier": tier})]})
            self.assertTrue(pbk.is_approved(changed), tier)
            self.assertIsNotNone(pbk.refresh(changed).approval, tier)

    def test_an_approval_made_before_tiers_existed_still_holds(self):
        """The hash must not mention the tier at all, or every playbook approved earlier would need approving again."""
        pb = Playbook(name="x", steps=[Step(id="s1", kind="extract", tag="ai", query="q")])
        late = ("timeout_minutes", "on_timeout", "variable", "fields", "row_selector", "max_rows", "source", "column", "body", "on_failure")
        legacy_steps = [{k: v for k, v in s.model_dump(mode="json", exclude={"note"}).items() if k != "tier" and k not in late} for s in pb.steps]
        body = {"steps": legacy_steps, "variables": [], "domains": pbk.compute_domains(pb)}
        legacy = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        self.assertEqual(pbk.approval_hash(pb), legacy)

    def test_other_edits_still_cost_the_approval(self):
        pb = pbk.with_approval(Playbook(name="x", steps=[Step(id="s1", kind="extract", tag="ai", query="q")]))
        edited = pb.model_copy(update={"steps": [pb.steps[0].model_copy(update={"query": "something else", "tier": "fast"})]})
        self.assertFalse(pbk.is_approved(edited))

    def test_it_survives_saving_and_an_old_file_without_it_loads(self):
        store = PlaybookStore(pathlib.Path(_tf.mkdtemp()))
        saved = store.save(Playbook(name="x", steps=[Step(id="s1", kind="extract", tag="ai", query="q", tier="strong")]))
        self.assertEqual(store.get(saved.id).steps[0].tier, "strong")
        path = store._path(saved.id)
        data = json.loads(path.read_text(encoding="utf-8"))
        del data["steps"][0]["tier"]
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(store.get(saved.id).steps[0].tier)

    def test_saving_a_tier_change_alone_keeps_the_approval(self):
        store = PlaybookStore(pathlib.Path(_tf.mkdtemp()))
        saved = store.save(Playbook(name="x", steps=[Step(id="s1", kind="extract", tag="ai", query="q")]), approve=True)
        again = store.save(saved.model_copy(update={"steps": [saved.steps[0].model_copy(update={"tier": "fast"})]}),
                           expected_version=saved.version)
        self.assertTrue(pbk.is_approved(again))


class TierAwarePage(FakePage):
    """A driver that knows about tiers: it records which tier each model call was asked for."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.tiers, self.last_model, self.last_tokens = [], None, None

    async def act(self, name, params, tier=None):
        self.tiers.append(tier)
        self.last_model = f"model-for-{tier or 'auto'} (Fast)" if name == "extract" else None
        self.last_tokens = 42 if name == "extract" else None
        return await super().act(name, params)


class PlayerPassesTheTier(unittest.IsolatedAsyncioTestCase):
    def build(self, page_cls):
        clock = Clock()
        approver = StepApprover(Inner(True), True, clock=clock)
        page = page_cls(approver, [], "about:blank")
        player = PlaybookPlayer(page, approver, settle=0, clock=clock, sleep=clock.sleep)
        return page, player

    async def test_the_step_tier_reaches_the_driver(self):
        page, player = self.build(TierAwarePage)
        r = await player.run(make_pb([S(1, "extract", query="q", tier="strong"), S(2, "extract", query="q")]))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.tiers, ["strong", None])

    async def test_the_run_record_says_which_model_read_the_page(self):
        page, player = self.build(TierAwarePage)
        r = await player.run(make_pb([S(1, "extract", query="q", tier="fast"), S(2, "wait", seconds=1)]))
        self.assertEqual(r.steps[0].model, "model-for-fast (Fast)")
        self.assertIsNone(r.steps[1].model)
        self.assertEqual((r.steps[0].tokens, r.steps[1].tokens), (42, None))
        self.assertEqual(r.to_dict()["steps"][0]["model"], "model-for-fast (Fast)")

    async def test_a_driver_written_before_tiers_still_works(self):
        page, player = self.build(FakePage)                  # act(name, params) only: it must not be handed a tier
        r = await player.run(make_pb([S(1, "extract", query="q"), S(2, "wait", seconds=1)]))
        self.assertEqual(r.status, "done", r.error)


class Params(BaseModel):
    query: str = ""


class RealDriverPicksTheModel(unittest.IsolatedAsyncioTestCase):
    def driver(self, **kw):
        seen = []

        async def act(action, session, page_extraction_llm=None, **k):
            seen.append(page_extraction_llm)
            return NS(error=None, extracted_content="ok")
        tools = NS(registry=NS(registry=NS(actions={"extract": NS(param_model=Params)})), act=act)
        return SessionDriver(object(), tools, **kw), seen

    async def test_a_step_with_a_tier_gets_that_tiers_model_and_others_the_default(self):
        built = []

        def llm_for(tier):
            built.append(tier)
            return NS(model=f"{tier}-model", tier=tier)
        default = NS(model="auto-model", tier="fast")
        driver, seen = self.driver(llm=default, llm_for=llm_for)
        await driver.act("extract", {"query": "q"}, tier="strong")
        self.assertEqual(driver.last_model, "strong-model (Strong)")
        await driver.act("extract", {"query": "q"})
        self.assertEqual(driver.last_model, "auto-model (Fast)")
        self.assertEqual([s.model for s in seen], ["strong-model", "auto-model"])
        await driver.act("extract", {"query": "q"}, tier="strong")
        self.assertEqual(built, ["strong"])                                   # built once, then reused

    async def test_without_a_way_to_build_one_the_default_is_used(self):
        default = NS(model="auto-model", tier="standard")
        driver, seen = self.driver(llm=default)
        await driver.act("extract", {"query": "q"}, tier="fast")
        self.assertIs(seen[0], default)

    async def test_what_the_step_used_is_reported(self):
        class Counting:
            model, tier, tokens = "m", "fast", 0

            def reset_tokens(self):
                self.tokens = 0
        llm = Counting()
        seen = []

        async def act(action, session, page_extraction_llm=None, **k):
            page_extraction_llm.tokens += 1234
            seen.append(1)
            return NS(error=None, extracted_content="ok")
        tools = NS(registry=NS(registry=NS(actions={"extract": NS(param_model=Params)})), act=act)
        driver = SessionDriver(object(), tools, llm=llm)
        await driver.act("extract", {"query": "q"})
        await driver.act("extract", {"query": "q"})
        self.assertEqual(driver.last_tokens, 1234)                 # this step's own use, not the running total

    async def test_a_model_that_reports_no_use_leaves_no_token_figure(self):
        class Silent:
            model, tier, tokens = "m", "fast", 0

            def reset_tokens(self):
                self.tokens = 0

        async def act(action, session, page_extraction_llm=None, **k):
            return NS(error=None, extracted_content="ok")
        tools = NS(registry=NS(registry=NS(actions={"extract": NS(param_model=Params)})), act=act)
        driver = SessionDriver(object(), tools, llm=Silent())
        await driver.act("extract", {"query": "q"})
        self.assertIsNone(driver.last_tokens)                       # not "0 tokens": nothing was reported

    async def test_no_model_name_is_invented(self):
        driver, _ = self.driver(llm=object())
        await driver.act("extract", {"query": "q"})
        self.assertIsNone(driver.last_model)
        self.assertIsNone(driver.last_tokens)


class BuildingThePlayer(Base, unittest.IsolatedAsyncioTestCase):
    async def player(self, steps):
        pb = Playbook(name="Read", steps=steps)
        with mock.patch.object(agent_ui, "_ensure_session", new=mock.AsyncMock(return_value=object())), \
                mock.patch.object(agent_ui, "trim_tabs", new=mock.AsyncMock()):
            return await agent_ui._build_player(pb, attended=False)

    async def test_a_pinned_step_gets_exactly_its_tier(self):
        self.env(**TIERED)
        player = await self.player([Step(id="s1", kind="extract", tag="ai", query="q", tier="strong")])
        pinned = player.driver.llm_for("strong")
        self.assertEqual([p.slot.tier for p in pinned.picks], ["strong"])
        self.assertEqual(pinned.model, "claude-opus-5-5")
        self.assertEqual([p.slot.tier for p in player.driver.llm.picks], ["fast", "standard", "strong"])   # automatic still climbs

    async def test_a_swapped_model_applies_to_the_next_run_without_editing_the_playbook(self):
        self.env(**TIERED)
        steps = [Step(id="s1", kind="extract", tag="ai", query="q", tier="fast")]
        first = (await self.player(steps)).driver.llm_for("fast").model
        self.env(**{**TIERED, "LLM_FAST_MODEL": "claude-haiku-5-0"})
        second = (await self.player(steps)).driver.llm_for("fast").model
        self.assertEqual((first, second), ("claude-haiku-4-5-20251001", "claude-haiku-5-0"))

    async def test_no_key_at_all_still_gives_the_plain_message(self):
        self.env(LLM_PROVIDER="claude", ANTHROPIC_API_KEY="")
        _os.environ.pop("ANTHROPIC_API_KEY", None)
        player = await self.player([Step(id="s1", kind="extract", tag="ai", query="q", tier="fast")])
        self.assertIsNone(player.driver.llm)
        self.assertIsNone(player.driver.llm_for)


class TheChatTasksModel(Base):
    def test_automatic_and_standard_are_the_settings_model_as_always(self):
        self.env(**TIERED)
        with mock.patch.object(agent_ui, "get_llm", return_value=Chat("settings-model")):
            for tier in (None, "standard"):
                llm, provider = agent_ui._agent_llm(tier)
                self.assertEqual((llm.model, provider), ("settings-model", "claude"))

    def test_a_chosen_tier_gives_that_tiers_model_and_provider(self):
        self.env(**TIERED)
        llm, provider = agent_ui._agent_llm("fast")
        self.assertEqual((llm.model, provider), ("claude-haiku-4-5-20251001", "claude"))
        llm, _ = agent_ui._agent_llm("strong")
        self.assertEqual(llm.model, "claude-opus-5-5")

    def test_a_tier_on_another_provider_reports_that_provider(self):
        self.env(**KEYS, LLM_STRONG_PROVIDER="openai", LLM_STRONG_MODEL="gpt-5")
        _llm, provider = agent_ui._agent_llm("strong")
        self.assertEqual(provider, "openai")                 # vision and the reviewer depend on the model's own provider

    def test_a_tier_that_is_not_set_up_runs_on_standard(self):
        self.env(**ONLY_FAST)
        with mock.patch.object(agent_ui, "get_llm", return_value=Chat("settings-model")):
            llm, _ = agent_ui._agent_llm("strong")
        self.assertEqual(llm.model, "settings-model")

    def test_router_off_runs_everything_on_standard(self):
        self.env(**TIERED, LLM_ROUTER="off")
        with mock.patch.object(agent_ui, "get_llm", return_value=Chat("settings-model")):
            self.assertEqual(agent_ui._agent_llm("strong")[0].model, "settings-model")

    def test_the_chat_says_what_will_answer(self):
        self.env(**TIERED)
        self.assertEqual(agent_ui._tier_note(None), "")
        self.assertEqual(agent_ui._tier_note("fast"), "Model for this task: Fast (claude-haiku-4-5-20251001)")
        self.env(**ONLY_FAST)
        note = agent_ui._tier_note("strong")
        self.assertIn("Strong isn't set up (not set)", note)
        self.assertIn("runs on the Standard model", note)
        self.env(**TIERED, LLM_ROUTER="off")
        self.assertIn("switched off in Settings", agent_ui._tier_note("fast"))


class StartingATask(Base, unittest.IsolatedAsyncioTestCase):
    async def start(self, **body):
        with mock.patch.object(agent_ui._ctl, "start") as start:
            result = await agent_ui.start_task({"task": "do it", **body})
        return result, start

    async def test_a_tier_is_passed_on_and_remembered_for_continue(self):
        result, start = await self.start(tier="fast")
        self.assertTrue(result["ok"])
        self.assertEqual(start.call_args.kwargs["tier"], "fast")

    async def test_auto_missing_and_empty_all_mean_automatic(self):
        for body in ({"tier": "auto"}, {}, {"tier": ""}, {"tier": None}):
            result, start = await self.start(**body)
            self.assertTrue(result["ok"], body)
            self.assertIsNone(start.call_args.kwargs["tier"], body)

    async def test_a_model_name_or_nonsense_is_refused(self):
        for bad in ("claude-opus-5-5", "FAST", 3, ["fast"], "x" * 500):
            result, start = await self.start(tier=bad)
            self.assertFalse(result["ok"], bad)
            self.assertIn("not a model tier", result["reason"])
            start.assert_not_called()

    async def test_continue_keeps_the_tier(self):
        agent_ui._ctl.tier = "strong"
        self.addCleanup(setattr, agent_ui._ctl, "tier", None)
        seen = {}

        async def fake_run(*a, **k):
            seen.update(k)
        tasks = []
        with mock.patch.object(agent_ui, "run_task", fake_run), \
                mock.patch.object(agent_ui._ctl, "resume", side_effect=lambda f: tasks.append(asyncio.ensure_future(f()))):
            self.assertTrue((await agent_ui.continue_task({}))["ok"])
            await asyncio.gather(*tasks)
        self.assertEqual(seen.get("tier"), "strong")
        self.assertTrue(seen.get("resume"))


class TheController(unittest.IsolatedAsyncioTestCase):
    async def test_it_remembers_the_tier_of_the_task_it_started(self):
        from aiba.server.task_control import TaskController
        ctl = TaskController()

        async def run():
            return None
        ctl.start(run, origin="do it", fmt="auto", tier="fast")
        self.assertEqual(ctl.tier, "fast")
        await ctl.task
        ctl2 = TaskController()
        self.assertIsNone(ctl2.tier)
        ctl2.start(run, origin="do it", fmt="auto")
        self.assertIsNone(ctl2.tier)                                   # automatic is the default
        await ctl2.task


class TheAgent(Base):
    def make(self, **kw):
        return agent_ui._new_agent("do it", "do it", FakeLLM([]), mock.MagicMock(), None, **kw)

    def test_the_reviewer_stays_on_the_standard_model_when_a_tier_is_chosen(self):
        self.env(**TIERED)
        agent = self.make(pinned=True)
        self.assertEqual(agent.judge_llm.model, mt.read_slots(TIERED)["standard"].model)
        self.assertIsNot(agent.judge_llm, agent.llm)

    def test_a_standard_model_that_cannot_review_means_no_review_even_with_a_chosen_tier(self):
        self.env(**{**TIERED, "LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "sk-ds-xxxxxxxx"})
        agent = self.make(provider="claude", pinned=True)            # the task runs on a Claude tier, Standard is DeepSeek
        self.assertFalse(agent.settings.use_judge)

    def test_without_a_choice_nothing_changes(self):
        self.env(**TIERED)
        agent = self.make()
        self.assertIs(agent.judge_llm, agent.llm)

    def test_a_models_own_provider_decides_vision(self):
        self.env(**TIERED)
        self.assertFalse(self.make(provider="deepseek", pinned=True).settings.use_vision)
        self.assertTrue(self.make(provider="claude", pinned=True).settings.use_vision)

    def test_a_failure_to_build_the_reviewer_does_not_stop_the_run(self):
        self.env(**TIERED)
        with mock.patch.object(self.router, "llm", side_effect=RuntimeError("no key")):
            agent = self.make(pinned=True)
        self.assertIs(agent.judge_llm, agent.llm)


if __name__ == "__main__":
    unittest.main()
