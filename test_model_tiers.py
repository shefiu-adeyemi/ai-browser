"""Tests for model_tiers.py: slots, the router table, escalation, and the usage ledger."""

import asyncio
import json
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import model_tiers as mt
import providers

KEYS = {"ANTHROPIC_API_KEY": "sk-ant-xxxxxxxx", "OPENAI_API_KEY": "sk-oai-xxxxxxxx"}


def env(**kw):
    base = {"LLM_PROVIDER": "claude", **KEYS}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


FULL = env(LLM_FAST_PROVIDER="claude", LLM_FAST_MODEL="claude-haiku-4-5-20251001",
           LLM_STRONG_PROVIDER="claude", LLM_STRONG_MODEL="claude-opus-5-5")


class Slots(unittest.TestCase):
    def test_with_only_a_standard_model_everything_runs_on_it(self):
        slots = mt.read_slots(env())
        self.assertEqual((slots["standard"].provider_id, slots["standard"].model), ("claude", "claude-sonnet-5-5"))
        for tier in ("fast", "strong"):
            s = slots[tier]
            self.assertTrue(s.collapsed and not s.explicit, tier)
            self.assertEqual((s.provider_id, s.model), ("claude", "claude-sonnet-5-5"))
            self.assertIn("not set", s.reason)

    def test_chosen_slots_are_used(self):
        slots = mt.read_slots(FULL)
        self.assertEqual(slots["fast"].model, "claude-haiku-4-5-20251001")
        self.assertEqual(slots["strong"].model, "claude-opus-5-5")
        self.assertTrue(slots["fast"].explicit and not slots["fast"].collapsed)

    def test_a_slot_on_another_provider(self):
        slots = mt.read_slots(env(LLM_FAST_PROVIDER="openai", LLM_FAST_MODEL="gpt-5-mini"))
        self.assertEqual((slots["fast"].provider_id, slots["fast"].model), ("openai", "gpt-5-mini"))

    def test_a_slot_whose_provider_has_no_key_uses_standard_and_says_why(self):
        slots = mt.read_slots(env(OPENAI_API_KEY=None, LLM_FAST_PROVIDER="openai", LLM_FAST_MODEL="gpt-5-mini"))
        s = slots["fast"]
        self.assertTrue(s.collapsed and s.explicit and not s.usable)
        self.assertEqual(s.model, "claude-sonnet-5-5")
        self.assertIn("OpenAI", s.reason)
        self.assertIn("API key", s.reason)

    def test_an_unknown_provider_is_a_collapsed_slot_not_a_crash(self):
        s = mt.read_slots(env(LLM_STRONG_PROVIDER="skynet", LLM_STRONG_MODEL="x"))["strong"]
        self.assertTrue(s.collapsed)
        self.assertIn("skynet", s.reason)

    def test_only_a_model_means_the_standard_provider(self):
        s = mt.read_slots(env(LLM_FAST_MODEL="claude-haiku-4-5-20251001"))["fast"]
        self.assertEqual((s.provider_id, s.model, s.collapsed), ("claude", "claude-haiku-4-5-20251001", False))

    def test_only_a_provider_means_its_default_model(self):
        s = mt.read_slots(env(LLM_STRONG_PROVIDER="openai"))["strong"]
        self.assertEqual((s.provider_id, s.model), ("openai", "gpt-4o"))

    def test_old_style_names_are_understood(self):
        slots = mt.read_slots(env(LLM_PROVIDER="Anthropic", LLM_MODEL="anthropic/claude-opus-5-5",
                                  LLM_FAST_PROVIDER="anthropic", LLM_FAST_MODEL="claude/claude-haiku-4-5-20251001"))
        self.assertEqual((slots["standard"].provider_id, slots["standard"].model), ("claude", "claude-opus-5-5"))
        self.assertEqual(slots["fast"].model, "claude-haiku-4-5-20251001")

    def test_standard_without_a_key_is_reported_unusable(self):
        slots = mt.read_slots(env(ANTHROPIC_API_KEY=None))
        self.assertFalse(slots["standard"].usable)

    def test_an_unset_or_junk_standard_provider_falls_back_to_the_default(self):
        self.assertEqual(mt.read_slots({"LLM_PROVIDER": "nope"})["standard"].provider_id, providers.DEFAULT_PROVIDER)
        self.assertEqual(mt.read_slots({})["standard"].model, providers.BY_ID[providers.DEFAULT_PROVIDER].default_model)

    def test_every_suggestion_is_a_known_model_of_its_provider(self):
        self.assertEqual(set(mt.SUGGESTED), {p.id for p in providers.PROVIDERS})
        for pid, pair in mt.SUGGESTED.items():
            self.assertEqual(set(pair), {"fast", "strong"})
            for model in pair.values():
                self.assertIn(model, providers.BY_ID[pid].suggestions, (pid, model))


class Picking(unittest.TestCase):
    def test_the_table(self):
        for purpose, tier in (("agent", "standard"), ("judge", "standard"), ("extract", "fast"), ("compaction", "fast"),
                              ("playbook_extract", "fast"), ("repair", "fast"), ("retry", "strong"), ("draft", "strong")):
            p = mt.pick(purpose, FULL)
            self.assertEqual((p.tier, p.slot.tier), (tier, tier), purpose)
        self.assertEqual(mt.pick("extract", FULL).model, "claude-haiku-4-5-20251001")
        self.assertEqual(mt.pick("retry", FULL).model, "claude-opus-5-5")

    def test_an_unknown_job_is_standard(self):
        self.assertEqual(mt.pick("something new", FULL).slot.tier, "standard")

    def test_a_collapsed_slot_picks_the_standard_model(self):
        p = mt.pick("extract", env())
        self.assertEqual((p.tier, p.slot.collapsed, p.model), ("fast", True, "claude-sonnet-5-5"))

    def test_router_off_sends_everything_to_standard(self):
        for purpose in mt.PURPOSES:
            self.assertEqual(mt.pick(purpose, {**FULL, "LLM_ROUTER": "off"}).model, "claude-sonnet-5-5", purpose)
        for on in ("auto", "AUTO", "", "yes", "anything"):
            self.assertTrue(mt.router_on({"LLM_ROUTER": on}), on)
        self.assertFalse(mt.router_on({"LLM_ROUTER": " OFF "}))

    def test_the_chain_climbs_the_tiers(self):
        self.assertEqual([p.slot.tier for p in mt.chain("extract", FULL)], ["fast", "standard", "strong"])
        self.assertEqual([p.slot.tier for p in mt.chain("agent", FULL)], ["standard", "strong"])

    def test_a_job_that_starts_at_strong_can_fall_back_to_standard(self):
        self.assertEqual([p.slot.tier for p in mt.chain("draft", FULL)], ["strong", "standard"])

    def test_one_model_means_a_chain_of_one(self):
        for purpose in ("extract", "agent", "draft"):
            self.assertEqual(len(mt.chain(purpose, env())), 1, purpose)

    def test_models_that_repeat_are_listed_once(self):
        same = env(LLM_FAST_MODEL="claude-sonnet-5-5", LLM_STRONG_MODEL="claude-opus-5-5")
        self.assertEqual([p.model for p in mt.chain("extract", same)], ["claude-sonnet-5-5", "claude-opus-5-5"])

    def test_router_off_has_no_escalation(self):
        self.assertEqual(len(mt.chain("extract", {**FULL, "LLM_ROUTER": "off"})), 1)

    def test_which_tier_a_model_belongs_to(self):
        self.assertEqual(mt.tier_of_model("claude-sonnet-5-5", FULL), "standard")
        self.assertEqual(mt.tier_of_model("claude-haiku-4-5-20251001", FULL), "fast")
        self.assertEqual(mt.tier_of_model("claude-opus-5-5", FULL), "strong")
        self.assertEqual(mt.tier_of_model("something-else", FULL), "standard")
        self.assertEqual(mt.tier_of_model("claude-sonnet-5-5", env()), "standard")       # collapsed slots do not claim it

    def test_describe_for_the_settings_screen(self):
        d = mt.describe(FULL)
        self.assertEqual(d["router"], "auto")
        self.assertEqual(d["slots"]["strong"]["model"], "claude-opus-5-5")
        self.assertFalse(d["slots"]["fast"]["collapsed"])
        self.assertEqual({p["purpose"] for p in d["purposes"]}, set(mt.PURPOSES))
        self.assertEqual(mt.describe({**FULL, "LLM_ROUTER": "off"})["router"], "off")
        json.dumps(d)                                                                   # must be plain data


class FakeClient:
    """A stand-in for one provider's model."""
    def __init__(self, model, behaviours, log):
        self.model, self.behaviours, self.log = model, list(behaviours), log

    async def ainvoke(self, messages, output_format=None, **kw):
        self.log.append((self.model, messages, output_format, kw))
        b = self.behaviours.pop(0) if self.behaviours else "ok"
        if isinstance(b, BaseException):
            raise b
        usage = None if b == "no usage" else NS(prompt_tokens=100, completion_tokens=10)
        return NS(completion=f"answer from {self.model}", usage=usage)


class Harness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ledger = mt.Ledger(Path(self._tmp.name) / "usage.json")
        self.calls: list = []
        self.script: dict[str, list] = {}
        self.built: list = []

    def factory(self, provider, model):
        self.built.append((provider, model))
        return FakeClient(model, self.script.get(model, []), self.calls)

    def tiered(self, purpose="extract", e=FULL):
        return mt.TieredLLM(mt.chain(purpose, e), self.factory, self.ledger)


class Escalation(Harness):
    async def test_the_first_tier_answers_when_it_works(self):
        llm = self.tiered()
        r = await llm.ainvoke(["hi"], "format", temperature=0)
        self.assertEqual(r.completion, "answer from claude-haiku-4-5-20251001")
        self.assertEqual([c[0] for c in self.calls], ["claude-haiku-4-5-20251001"])
        self.assertEqual(self.calls[0][1:], (["hi"], "format", {"temperature": 0}))     # arguments pass straight through
        self.assertEqual(llm.model, "claude-haiku-4-5-20251001")

    async def test_a_failure_moves_up_one_tier_at_a_time(self):
        self.script = {"claude-haiku-4-5-20251001": [RuntimeError("bad json")], "claude-sonnet-5-5": [RuntimeError("rate limit")]}
        llm = self.tiered()
        r = await llm.ainvoke(["hi"])
        self.assertEqual(r.completion, "answer from claude-opus-5-5")
        self.assertEqual([c[0] for c in self.calls], ["claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"])
        self.assertEqual([(e["tier"], e["ok"]) for e in llm.events], [("fast", False), ("standard", False), ("strong", True)])
        self.assertIn("bad json", llm.events[0]["error"])
        self.assertEqual(llm.model, "claude-opus-5-5")

    async def test_when_every_tier_fails_the_last_error_is_raised(self):
        self.script = {m: [ValueError(f"{m} down")] for m in ("claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5")}
        with self.assertRaises(ValueError) as ctx:
            await self.tiered().ainvoke(["hi"])
        self.assertIn("claude-opus-5-5 down", str(ctx.exception))
        self.assertEqual(len(self.calls), 3)                                             # no loop, no extra spend

    async def test_one_model_gets_one_try(self):
        self.script = {"claude-sonnet-5-5": [RuntimeError("down")]}
        with self.assertRaises(RuntimeError):
            await self.tiered("extract", env()).ainvoke(["hi"])
        self.assertEqual(len(self.calls), 1)

    async def test_cancellation_is_not_an_error_to_escalate_from(self):
        self.script = {"claude-haiku-4-5-20251001": [asyncio.CancelledError()]}
        with self.assertRaises(asyncio.CancelledError):
            await self.tiered().ainvoke(["hi"])
        self.assertEqual(len(self.calls), 1)

    async def test_a_tier_that_cannot_even_be_built_is_skipped(self):
        def factory(provider, model):
            if model == "claude-haiku-4-5-20251001":
                raise RuntimeError("no key")
            return FakeClient(model, [], self.calls)
        llm = mt.TieredLLM(mt.chain("extract", FULL), factory, self.ledger)
        r = await llm.ainvoke(["hi"])
        self.assertEqual(r.completion, "answer from claude-sonnet-5-5")

    async def test_a_job_starting_at_strong_falls_back_to_standard(self):
        self.script = {"claude-opus-5-5": [RuntimeError("overloaded")]}
        r = await self.tiered("draft").ainvoke(["hi"])
        self.assertEqual(r.completion, "answer from claude-sonnet-5-5")

    async def test_clients_are_built_once_per_model(self):
        llm = self.tiered()
        for _ in range(3):
            await llm.ainvoke(["hi"])
        self.assertEqual(self.built, [("claude", "claude-haiku-4-5-20251001")])

    async def test_it_looks_like_a_model_to_browser_use(self):
        llm = self.tiered()
        self.assertTrue(llm._verified_api_keys)
        self.assertEqual((llm.model, llm.model_name, llm.name, llm.provider),
                         ("claude-haiku-4-5-20251001",) * 3 + ("claude",))
        with self.assertRaises(ValueError):
            mt.TieredLLM([], self.factory)

    async def test_it_works_without_a_ledger_and_without_usage_numbers(self):
        self.script = {"claude-haiku-4-5-20251001": ["no usage"]}
        llm = mt.TieredLLM(mt.chain("extract", FULL), self.factory, None)
        await llm.ainvoke(["hi"])
        llm2 = self.tiered()
        self.script["claude-haiku-4-5-20251001"] = ["no usage"]
        await llm2.ainvoke(["hi"])
        self.assertEqual(self.ledger.summary()["totals"]["fast"]["calls"], 0)


class Counting(Harness):
    async def test_each_answered_call_is_counted_under_the_tier_that_answered(self):
        self.script = {"claude-haiku-4-5-20251001": [RuntimeError("bad"), "ok"]}
        llm = self.tiered()
        await llm.ainvoke(["a"])                    # fast fails, standard answers
        await llm.ainvoke(["b"])                    # fast answers
        t = self.ledger.summary()["totals"]
        self.assertEqual((t["fast"]["calls"], t["standard"]["calls"], t["strong"]["calls"]), (1, 1, 0))
        self.assertEqual((t["fast"]["prompt"], t["fast"]["completion"]), (100, 10))
        self.assertEqual(self.ledger.summary()["models"]["claude-sonnet-5-5"]["tier"], "standard")

    async def test_a_failed_call_is_not_counted(self):
        self.script = {m: [RuntimeError("x")] for m in ("claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5")}
        with self.assertRaises(RuntimeError):
            await self.tiered().ainvoke(["a"])
        self.assertEqual(sum(t["calls"] for t in self.ledger.summary()["totals"].values()), 0)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "usage.json"
        self.now = [0.0]
        self.day = [date(2026, 10, 1)]
        self.ledger = mt.Ledger(self.path, clock=lambda: self.now[0], today=lambda: self.day[0])

    def test_add_and_summarise_by_tier_model_and_day(self):
        self.ledger.add("fast", "m1", 100, 10)
        self.ledger.add("fast", "m1", 50, 5)
        self.ledger.add("strong", "m2", 1000, 100)
        s = self.ledger.summary()
        self.assertEqual(s["totals"]["fast"], {"calls": 2, "prompt": 150, "completion": 15})
        self.assertEqual(s["totals"]["strong"]["prompt"], 1000)
        self.assertEqual(s["models"]["m1"], {"tier": "fast", "calls": 2, "prompt": 150, "completion": 15})
        self.assertEqual(s["days"][0]["date"], "2026-10-01")

    def test_nothing_is_written_until_the_interval_or_a_flush(self):
        self.ledger.add("fast", "m", 1, 1)
        self.assertFalse(self.path.exists())
        self.now[0] = mt.FLUSH_EVERY + 1
        self.ledger.add("fast", "m", 1, 1)
        self.assertEqual(mt.Ledger(self.path).summary()["totals"]["fast"]["calls"], 2)

    def test_a_flush_makes_it_durable_and_is_cheap_when_nothing_changed(self):
        self.ledger.add("standard", "m", 5, 5)
        self.ledger.flush()
        mtime = self.path.stat().st_mtime_ns
        self.ledger.flush()
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        self.assertEqual(mt.Ledger(self.path).summary()["totals"]["standard"]["calls"], 1)
        self.assertEqual(list(Path(self._tmp.name).glob("*.tmp")), [])

    def test_the_range_and_old_days(self):
        for offset in (0, 3, 10, 40):
            self.day[0] = date(2026, 10, 1) - timedelta(days=offset)
            self.ledger.add("fast", "m", 1, 1)
        self.day[0] = date(2026, 10, 1)
        self.assertEqual(self.ledger.summary(7)["totals"]["fast"]["calls"], 2)
        self.assertEqual(self.ledger.summary(30)["totals"]["fast"]["calls"], 3)
        self.ledger.flush()
        self.ledger.add("fast", "m", 1, 1)
        self.ledger.flush()
        days = json.loads(self.path.read_text())["days"]
        self.assertNotIn("2026-08-22", days)                                    # older than KEEP_DAYS is dropped

    def test_an_unknown_tier_counts_as_standard_and_negatives_are_ignored(self):
        self.ledger.add("weird", "m", -5, -5, calls=-1)
        t = self.ledger.summary()["totals"]["standard"]
        self.assertEqual((t["calls"], t["prompt"], t["completion"]), (0, 0, 0))

    def test_a_damaged_file_starts_fresh(self):
        self.path.write_text("{nope", encoding="utf-8")
        fresh = mt.Ledger(self.path)
        fresh.add("fast", "m", 1, 1)
        fresh.flush()
        self.assertEqual(mt.Ledger(self.path).summary()["totals"]["fast"]["calls"], 1)

    def test_a_disk_error_never_breaks_a_run(self):
        ledger = mt.Ledger(Path(self._tmp.name) / "usage.json" / "x" / "y")      # a path that can't be created
        (Path(self._tmp.name) / "usage.json").write_text("{}", encoding="utf-8")
        ledger.add("fast", "m", 1, 1)
        ledger.flush()                                                           # must not raise

    def test_what_browser_use_measured_for_a_whole_run(self):
        by_model = {"claude-sonnet-5-5": NS(prompt_tokens=5000, completion_tokens=400, invocations=12),
                    "claude-haiku-4-5-20251001": {"prompt_tokens": 800, "completion_tokens": 60, "invocations": 3},
                    "empty": NS(prompt_tokens=0, completion_tokens=0, invocations=0)}
        self.ledger.record_agent_usage(by_model, env=FULL)
        t = self.ledger.summary()["totals"]
        self.assertEqual((t["standard"]["calls"], t["standard"]["prompt"]), (12, 5000))
        self.assertEqual((t["fast"]["calls"], t["fast"]["completion"]), (3, 60))
        self.assertTrue(self.path.exists())                                      # recording a run flushes
        self.ledger.record_agent_usage(None)
        self.ledger.record_agent_usage({"m": NS(prompt_tokens="lots")})

    def test_totals_from_an_agent_are_counted_as_what_is_new(self):
        first = {"claude-sonnet-5-5": NS(prompt_tokens=1000, completion_tokens=100, invocations=4)}
        seen = self.ledger.record_agent_usage(first, env=FULL)
        self.assertEqual(seen, {"claude-sonnet-5-5": (1000, 100, 4)})
        grown = {"claude-sonnet-5-5": NS(prompt_tokens=1800, completion_tokens=150, invocations=7),
                 "claude-haiku-4-5-20251001": NS(prompt_tokens=300, completion_tokens=30, invocations=2)}
        seen = self.ledger.record_agent_usage(grown, env=FULL, already=seen)
        t = self.ledger.summary()["totals"]
        self.assertEqual(t["standard"], {"calls": 7, "prompt": 1800, "completion": 150})        # not 4+7 calls, not 2800 tokens
        self.assertEqual(t["fast"]["calls"], 2)
        again = self.ledger.record_agent_usage(grown, env=FULL, already=seen)                   # nothing new: nothing added
        self.assertEqual(self.ledger.summary()["totals"]["standard"]["calls"], 7)
        self.assertEqual(again, seen)

    def test_a_counter_that_went_backwards_adds_nothing_negative(self):
        seen = self.ledger.record_agent_usage({"m": NS(prompt_tokens=500, completion_tokens=50, invocations=3)})
        self.ledger.record_agent_usage({"m": NS(prompt_tokens=100, completion_tokens=10, invocations=1)}, already=seen)
        t = self.ledger.summary()["totals"]["standard"]
        self.assertEqual((t["prompt"], t["completion"]), (500, 50))

    def test_concurrent_adds_lose_nothing(self):
        threads = [threading.Thread(target=lambda: [self.ledger.add("fast", "m", 1, 1) for _ in range(100)]) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(self.ledger.summary()["totals"]["fast"]["calls"], 500)


class RouterTests(Harness):
    def make(self, e):
        self.e = dict(e)
        return mt.Router(ledger=self.ledger, factory=self.factory, env=self.e)

    def test_settings_changes_apply_to_the_next_call_without_a_restart(self):
        r = self.make(env())
        self.assertEqual(r.pick("extract").model, "claude-sonnet-5-5")
        self.e.update(LLM_FAST_MODEL="claude-haiku-4-5-20251001")
        self.assertEqual(r.pick("extract").model, "claude-haiku-4-5-20251001")
        self.e["LLM_ROUTER"] = "off"
        self.assertEqual(r.pick("extract").model, "claude-sonnet-5-5")

    def test_a_plain_client_is_cached_and_a_changed_key_builds_a_new_one(self):
        r = self.make(FULL)
        a, b = r.llm("extract"), r.llm("extract")
        self.assertIs(a, b)
        self.assertEqual(len(self.built), 1)
        self.e["ANTHROPIC_API_KEY"] = "sk-ant-different-key"
        self.assertIsNot(r.llm("extract"), a)

    def test_a_model_handed_to_something_that_counts_for_itself_does_not_count_twice(self):
        r = self.make(FULL)
        llm = r.escalating("extract", count=False)
        asyncio.run(llm.ainvoke(["x"]))
        self.assertEqual(sum(t["calls"] for t in self.ledger.summary()["totals"].values()), 0)

    def test_escalating_gives_a_tiered_model_that_counts(self):
        r = self.make(FULL)
        llm = r.escalating("extract")
        self.assertIsInstance(llm, mt.TieredLLM)
        asyncio.run(llm.ainvoke(["x"]))
        self.assertEqual(self.ledger.summary()["totals"]["fast"]["calls"], 1)

    def test_distinct_from_standard(self):
        self.assertTrue(self.make(FULL).distinct_from_standard("extract"))
        self.assertFalse(self.make(env()).distinct_from_standard("extract"))
        self.assertFalse(self.make(FULL).distinct_from_standard("agent"))
        self.assertFalse(self.make({**FULL, "LLM_ROUTER": "off"}).distinct_from_standard("extract"))


if __name__ == "__main__":
    unittest.main()
