"""Self-repair in the player: what it may repair, what it never does, and what it records."""

import unittest

from aiba.playbooks import model as pbk
from aiba.playbooks import repair as pr
from aiba.playbooks.model import Playbook, ScrapeField
from test_foreach import LIST, loop_pb
from test_playbook_player import PlayerCase, S, el, fp


class FakeRepairer:
    def __init__(self, pick=None, fix=None, chooser=None):
        self.pick, self.fix, self.chooser = pick, fix, chooser
        self.chosen, self.asked = [], []

    async def choose(self, step, options, host):
        self.chosen.append((step.id, [c.index for c in options], host))
        if self.chooser is not None:
            want = self.chooser(step, options)
            return next(((c, "same purpose") for c in options if c.index == want), None)
        if self.pick is None:
            return None
        return next(((c, "same purpose") for c in options if c.index == self.pick), None)

    async def selectors(self, step, url):
        self.asked.append(step.id)
        return self.fix


def rpb(steps, repair=True, approve=True, variables=None):
    pb = pbk.refresh(Playbook(name="Repair run", steps=steps, variables=variables or [], repair=repair))
    return pbk.with_approval(pb) if approve else pb


MISSING = fp("Send message", h="old1", sh="old2", attrs={"aria-label": "Send message"})


class Elements(PlayerCase):
    def setup_page(self, elements, repairer, **kw):
        page = self.build(elements, **kw)
        self.player._repairer = repairer
        return page

    def pb(self, **kw):
        return rpb([S(1, "open", url="https://a.com/"), S(2, "click", target=MISSING, note="Send it", **kw)])

    async def test_nothing_is_repaired_unless_the_playbook_says_so(self):
        rep = FakeRepairer(pick=11)
        self.setup_page([el(11, "Send msg")], rep)
        r = await self.run_pb(rpb([S(1, "open", url="https://a.com/"), S(2, "click", target=MISSING)], repair=False))
        self.assertEqual(r.status, "failed")
        self.assertIn("Couldn't find", r.steps[1].detail)
        self.assertEqual(rep.chosen, [])
        self.assertEqual(r.repairs, [])

    async def test_nothing_is_repaired_without_a_repairer(self):
        self.build([el(11, "Send msg")])
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "failed")

    async def test_a_missing_element_is_replaced_and_the_step_goes_on_and_says_so(self):
        rep = FakeRepairer(pick=12)
        page = self.setup_page([el(11, "Cancel", h="c"), el(12, "Send message now", h="n", attrs={"aria-label": "Send message now"})], rep)
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual([a for a in page.actions if a[0] == "click"], [("click", {"index": 12})])
        self.assertEqual(r.steps[1].matched, "repaired (medium)")
        (rec,) = r.repairs
        self.assertEqual((rec["step_id"], rec["kind"], rec["why"]), ("s2", "element", "same purpose"))
        self.assertEqual(rec["now"]["ax_name"], "Send message now")
        self.assertIn('called "Send message"', rec["was"])
        self.assertIn("Send message now", rec["summary"])
        self.assertTrue(any("🔧 Repaired step 2" in m for m in self.msgs))
        self.assertEqual(r.to_dict()["repairs"], r.repairs)

    async def test_the_model_only_sees_close_compatible_elements_from_this_page(self):
        rep = FakeRepairer(pick=12)
        self.setup_page([el(11, "Unrelated"), el(12, "Send message now"), el(13, "Send message", node="div")], rep,
                               url="https://a.com/")
        await self.run_pb(self.pb())
        (step_id, offered, host), = rep.chosen
        self.assertEqual((step_id, offered, host), ("s2", [12], "a.com"))

    async def test_when_the_model_picks_nothing_the_step_fails_as_it_always_did(self):
        rep = FakeRepairer(pick=None)
        self.setup_page([el(12, "Send message now")], rep)
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "failed")
        self.assertIn("Couldn't find", r.steps[1].detail)
        self.assertEqual(r.repairs, [])

    async def test_a_step_that_sends_is_never_repaired(self):
        rep = FakeRepairer(pick=12)
        self.setup_page([el(12, "Send message now")], rep)
        r = await self.run_pb(self.pb(ask=True))
        self.assertEqual(r.status, "failed")
        self.assertEqual(rep.chosen, [])

    async def test_typing_a_stored_secret_is_never_repaired(self):
        rep = FakeRepairer(pick=12)
        self.setup_page([el(12, "Pass word", node="input")], rep)
        pb = rpb([S(1, "open", url="https://a.com/"), S(2, "type", target=fp("Password", node="input", attrs={"id": "pw"}), text="<secret>pw</secret>")])
        r = await self.run_pb(pb)
        self.assertEqual(rep.chosen, [])
        self.assertNotEqual(r.status, "done")

    async def test_typing_ordinary_text_into_a_renamed_field_is_repaired(self):
        rep = FakeRepairer(pick=12)
        page = self.setup_page([el(12, "Search the shop", node="input", attrs={"placeholder": "Search"})], rep)
        pb = rpb([S(1, "open", url="https://a.com/"), S(2, "type", target=fp("Search", node="input", attrs={"placeholder": "Search"}), text="red shoes")])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.actions[1], ("input", {"index": 12, "text": "red shoes", "clear": True}))

    async def test_only_so_many_repairs_per_run(self):
        steps = [S(1, "open", url="https://a.com/")] + [S(n, "click", target=fp(f"Gone {n}", h=f"x{n}", sh=f"y{n}")) for n in range(2, 7)]
        rep = FakeRepairer(pick=1)
        self.setup_page([el(1, "Gone 2 gone 3 gone 4 gone 5 gone 6")], rep)
        self.player.find_timeout = 1
        await self.run_pb(rpb(steps))
        self.assertLessEqual(len(rep.chosen), pr.MAX_REPAIRS)

    async def test_exactly_three_repairs_are_made_and_the_fourth_step_stops_the_run(self):
        steps = [S(1, "open", url="https://a.com/")] + [S(n, "click", target=fp(f"Gone {n}", h=f"x{n}", sh=f"y{n}")) for n in range(2, 7)]
        rep = FakeRepairer(chooser=lambda step, options: options[0].index)
        self.setup_page([el(n, f"Gone {n} now") for n in range(2, 7)], rep)
        self.player.find_timeout = 1
        r = await self.run_pb(rpb(steps))
        self.assertEqual(len(rep.chosen), pr.MAX_REPAIRS)
        self.assertEqual([s.status for s in r.steps[1:]], ["ok", "ok", "ok", "failed"])
        self.assertEqual(len(r.repairs), pr.MAX_REPAIRS)

    async def test_a_repair_is_never_a_high_confidence_match(self):
        rep = FakeRepairer(pick=12)
        page = self.setup_page([el(12, "Send message now")], rep)
        page.risky[12] = "Send the message?"                      # the safety guard would ask about this element
        r = await self.run_pb(self.pb(), )
        self.assertEqual(r.steps[1].matched, "repaired (medium)")
        self.assertEqual(r.steps[1].approval, "asked")             # asked, never pre-approved

    async def test_unattended_a_repaired_click_the_guard_would_stop_is_stopped(self):
        rep = FakeRepairer(pick=12)
        page = self.setup_page([el(12, "Send message now")], rep, attended=False)
        page.risky[12] = "Send the message?"
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "needs_you")
        self.assertEqual(r.repairs, [])                             # it did not work, so it is not recorded as a repair

    async def test_a_found_element_is_never_repaired(self):
        rep = FakeRepairer(pick=12)
        self.setup_page([el(5, "Send message", h="old1", sh="old2", attrs={"aria-label": "Send message"}), el(12, "Other")], rep)
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "done")
        self.assertEqual((rep.chosen, r.repairs), ([], []))

    async def test_a_failing_action_after_a_repair_is_not_recorded_as_one(self):
        rep = FakeRepairer(pick=12)
        page = self.setup_page([el(12, "Send message now")], rep)
        page.blocked.add(12)
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.repairs, [])

    async def test_a_second_run_starts_with_a_fresh_allowance(self):
        rep = FakeRepairer(pick=12)
        self.setup_page([el(12, "Send message now")], rep)
        for _ in range(2):
            r = await self.run_pb(self.pb())
            self.assertEqual(len(r.repairs), 1)


class Tables(PlayerCase):
    FIX = {"row_selector": "tr.new", "fields": [{"name": "Name", "selector": ".t", "type": "text", "attr": None},
                                                {"name": "Price", "selector": ".c", "type": "number", "attr": None}],
           "table": {"columns": ["Name", "Price"], "rows": [["Ann", "5"]], "truncated": False, "total": 1}}

    def pb(self, repair=True):
        return rpb([S(1, "open", url="https://a.com/"),
                    S(2, "scrape", note="Prices", row_selector="tr.old",
                      fields=[ScrapeField(name="Name", selector=".n"), ScrapeField(name="Price", selector=".p", type="number")])], repair=repair)

    def serve(self, repairer):
        page = self.build()
        calls = []

        async def scrape(row_selector, fields, max_rows):
            calls.append((row_selector, [f["selector"] for f in fields]))
            if row_selector == "tr.new":
                return {"total": 2, "rows": [["Ann", "$5"], ["Bob", "7"]]}
            return {"total": 0, "rows": []}
        page.scrape = scrape
        page.scrape_log = calls
        self.player._repairer = repairer
        return page

    async def test_selectors_that_stopped_matching_are_replaced_for_the_same_columns(self):
        rep = FakeRepairer(fix=self.FIX)
        page = self.serve(rep)
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(r.outputs[0]["table"]["rows"], [["Ann", "5"], ["Bob", "7"]])
        (rec,) = r.repairs
        self.assertEqual((rec["step_id"], rec["kind"]), ("s2", "scrape"))
        self.assertEqual(rec["now"]["row_selector"], "tr.new")
        self.assertIn("tr.old", rec["was"])
        self.assertEqual(page.scrape_log[-1], ("tr.new", [".t", ".c"]))
        self.assertTrue(any("🔧 Repaired step 2" in m for m in self.msgs))

    async def test_not_without_the_playbooks_say_so(self):
        rep = FakeRepairer(fix=self.FIX)
        self.serve(rep)
        r = await self.run_pb(self.pb(repair=False))
        self.assertEqual(r.status, "failed")
        self.assertEqual(rep.asked, [])

    async def test_no_fix_found_is_the_same_honest_failure(self):
        rep = FakeRepairer(fix=None)
        self.serve(rep)
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "failed")
        self.assertIn("No rows were found", r.steps[1].detail)
        self.assertEqual((rep.asked, r.repairs), (["s2"], []))

    async def test_it_waits_for_a_slow_page_before_asking_for_a_repair(self):
        rep = FakeRepairer(fix=self.FIX)
        page = self.build()
        answers = [{"total": 0, "rows": []}, {"total": 0, "rows": []}, {"total": 1, "rows": [["Ann", "5"]]}]
        async def scrape(row_selector, fields, max_rows):
            return answers.pop(0) if len(answers) > 1 else answers[0]
        page.scrape = scrape
        self.player._repairer = rep
        r = await self.run_pb(self.pb())
        self.assertEqual((r.status, rep.asked, r.repairs), ("done", [], []))

    async def test_a_repair_inside_a_loop_is_found_once_and_used_for_every_row(self):
        rep = FakeRepairer(fix={"row_selector": None, "fields": [{"name": "Role", "selector": ".role2", "type": "text", "attr": None}],
                                "table": {"columns": ["Role"], "rows": [["Eng"]], "truncated": False, "total": 1}})
        page = self.build()
        calls = []
        async def scrape(row_selector, fields, max_rows):
            calls.append([f["selector"] for f in fields])
            if fields and fields[0]["selector"] == ".role2":
                return {"total": 1, "rows": [["Engineer"]]}
            if fields and fields[0]["selector"] == ".n":
                return LIST
            return {"total": 0, "rows": []}
        page.scrape = scrape
        self.player._repairer = rep
        pb = loop_pb().model_copy(update={"repair": True})
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(rep.asked, ["s5"])                                   # asked once, for the first row
        self.assertEqual(len(r.repairs), 1)
        self.assertEqual(sum(1 for c in calls if c == [".role2"]), 3)         # one read per row with the repaired selectors
        self.assertEqual(len([o for o in r.outputs if o["table"]["columns"][0] == "Link"]), 3)


class LoopsKeepTheirRepair(PlayerCase):
    async def test_a_repaired_table_that_later_fails_is_not_repaired_again(self):
        rep = FakeRepairer(fix={"row_selector": None, "fields": [{"name": "Role", "selector": ".role2", "type": "text", "attr": None}],
                                "table": {"columns": ["Role"], "rows": [["Eng"]], "truncated": False, "total": 1}})
        page = self.build()
        reads = {"role2": 0}
        async def scrape(row_selector, fields, max_rows):
            if fields and fields[0]["selector"] == ".n":
                return LIST
            if fields and fields[0]["selector"] == ".role2":
                reads["role2"] += 1
                return {"total": 1, "rows": [["Engineer"]]} if reads["role2"] == 1 else {"total": 0, "rows": []}   # fine once, then broken
            return {"total": 0, "rows": []}
        page.scrape = scrape
        self.player._repairer = rep
        pb = loop_pb(on_failure="skip").model_copy(update={"repair": True})
        r = await self.run_pb(pb)
        self.assertEqual(rep.asked, ["s5"])                                   # one model call, not one per failing row
        self.assertEqual(len(r.repairs), 1)


class Format(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(Playbook(name="x", steps=[]).repair)

    def test_it_is_part_of_what_the_user_approves(self):
        steps = [S(1, "open", url="https://a.com/")]
        off = rpb(steps, repair=False)
        self.assertTrue(pbk.is_approved(off))
        on = off.model_copy(update={"repair": True})
        self.assertFalse(pbk.is_approved(on))                                # switching it on needs a new approval
        self.assertNotEqual(pbk.approval_hash(off), pbk.approval_hash(on))


if __name__ == "__main__":
    unittest.main()
