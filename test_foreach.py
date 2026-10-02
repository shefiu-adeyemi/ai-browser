"""The "for each row" step: the format rules and how the player runs a loop over a scraped column."""

import asyncio
import unittest

import playbook as pbk
import playbook_scrape as ps
from playbook import Playbook, ScrapeField, Step, Variable
from playbook_player import summarize
from test_playbook_player import PlayerCase, S, el, fp, make_pb

LIST = {"total": 3, "rows": [["Ann", "https://a.com/p/1"], ["Bob", "https://a.com/p/2"], ["Cy", "https://a.com/p/3"]]}
DETAIL = {"total": 1, "rows": [["Engineer"]]}


def loop_pb(*, body=2, on_failure=None, max_rows=None, hosts=("a.com",), extra=None, vtype="url", column="Link", variable_loop=True):
    steps = [S(1, "open", url="https://a.com/list"),
             S(2, "scrape", row_selector="tr", note="The list",
               fields=[ScrapeField(name="Name", selector=".n"), ScrapeField(name="Link", selector="a", type="link")]),
             S(3, "foreach", source="s2", column=column, variable="item", body=body, on_failure=on_failure, max_rows=max_rows),
             S(4, "open", url="{{item}}"),
             S(5, "scrape", note="Details", fields=[ScrapeField(name="Role", selector=".role")])]
    if extra:
        steps += extra
    v = Variable(name="item", type=vtype, allowed_hosts=list(hosts) if vtype == "url" else [], loop=variable_loop)
    return make_pb(steps, variables=[v], approve=True)


class Format(unittest.TestCase):
    def problems(self, pb):
        return pbk.validate(pb)

    def test_a_good_loop_is_valid_and_the_variable_is_not_asked_for_up_front(self):
        pb = loop_pb()
        self.assertEqual(self.problems(pb), [])
        values, errors = pbk.bind_values(pb, {})
        self.assertEqual((values, errors), ({}, []))
        self.assertTrue(pbk.bind_values(pb, {"item": "https://a.com/x"})[1])        # can't be supplied by hand

    def test_the_loop_reaches_only_the_sites_the_variable_allows(self):
        self.assertEqual(pbk.compute_domains(loop_pb()), ["a.com"])

    def test_the_source_must_be_an_earlier_scrape_step(self):
        steps = [S(1, "open", url="https://a.com/"), S(2, "foreach", source="s9", column="Link", variable="item", body=1),
                 S(3, "open", url="{{item}}")]
        v = Variable(name="item", type="url", allowed_hosts=["a.com"], loop=True)
        self.assertTrue(any("earlier step that reads a table" in p for p in self.problems(make_pb(steps, variables=[v]))))

    def test_the_column_must_exist(self):
        self.assertTrue(any("no column 'Nope'" in p for p in self.problems(loop_pb(column="Nope"))))
        self.assertEqual(self.problems(loop_pb(column="link")), [])                    # case does not matter

    def test_the_variable_must_be_a_loop_variable_of_a_sensible_type(self):
        self.assertTrue(any("loop variable" in p for p in self.problems(loop_pb(variable_loop=False))))
        steps = loop_pb().steps
        v = Variable(name="item", type="linkedin_url", loop=True)
        self.assertTrue(any("not linkedin_url" in p for p in self.problems(make_pb(steps, variables=[v]))))

    def test_a_loop_variable_nobody_uses_is_refused(self):
        pb = loop_pb()
        stray = pb.model_copy(update={"variables": [*pb.variables, Variable(name="spare", loop=True)]})
        self.assertTrue(any("no foreach step uses it" in p for p in self.problems(stray)))

    def test_a_loop_variable_cannot_be_shared_by_two_loops(self):
        steps = loop_pb().steps + [S(6, "foreach", source="s2", column="Name", variable="item", body=1), S(7, "wait", seconds=1)]
        pb = make_pb(steps, variables=loop_pb().variables)
        self.assertTrue(any("already used by another loop" in p for p in self.problems(pb)))

    def test_it_cannot_also_be_answered_during_the_run(self):
        pb = loop_pb()
        both = pb.model_copy(update={"variables": [Variable(name="item", type="text", loop=True, runtime=True)]})
        self.assertTrue(any("both a loop variable" in p for p in self.problems(both)))

    def test_the_body_must_exist(self):
        self.assertTrue(any("not 5 steps after it" in p for p in self.problems(loop_pb(body=5))))

    def test_nothing_that_sends_or_asks_can_be_inside_a_loop(self):
        send = S(6, "click", target=fp("Send", h=1, sh=2), ask=True)
        self.assertTrue(any("can't be inside a loop" in p for p in self.problems(loop_pb(body=3, extra=[send]))))
        for kind, kw in (("approve", {"message": "ok?"}), ("handoff", {"message": "do it"}), ("input", {"message": "q?", "variable": "item"})):
            steps = loop_pb().steps[:4] + [S(9, kind, **kw), S(10, "wait", seconds=1)]
            pb = make_pb(steps, variables=loop_pb().variables)
            pb = pb.model_copy(update={"steps": [pb.steps[0], pb.steps[1], pb.steps[2].model_copy(update={"body": 3}), *pb.steps[3:]]})
            self.assertTrue(any("can't be inside a loop" in p for p in self.problems(pb)), kind)

    def test_loops_cannot_nest(self):
        steps = loop_pb().steps[:4] + [S(6, "foreach", source="s2", column="Name", variable="item", body=1), S(7, "wait", seconds=1)]
        pb = make_pb(steps, variables=loop_pb().variables)
        pb = pb.model_copy(update={"steps": [pb.steps[0], pb.steps[1], pb.steps[2].model_copy(update={"body": 3}), *pb.steps[3:]]})
        self.assertTrue(any("foreach step can't be inside a loop" in p for p in self.problems(pb)))

    def test_linkedin_is_never_looped_over(self):
        pb = loop_pb(hosts=("www.linkedin.com",))
        self.assertTrue(any("can't be used on LinkedIn" in p for p in self.problems(pb)))

    def test_the_loop_value_exists_only_inside_the_loop(self):
        after = S(6, "open", url="{{item}}")
        self.assertTrue(any("only exists inside its loop" in p for p in self.problems(loop_pb(body=2, extra=[after]))))
        before = [S(0, "search", engine="duckduckgo", query="{{item}}")]
        pb = loop_pb()
        early = pb.model_copy(update={"steps": [*[S(0, "search", engine="duckduckgo", query="{{item}}")], *pb.steps]})
        self.assertTrue(any("only exists inside its loop" in p for p in self.problems(early)))

    def test_a_row_limit_and_a_missing_setting(self):
        self.assertTrue(any("at most 50" in p for p in self.problems(loop_pb(max_rows=51))))
        self.assertEqual(self.problems(loop_pb(max_rows=50)), [])
        bare = Playbook(name="x", steps=[S(1, "open", url="https://a.com/"), S(2, "scrape", fields=[ScrapeField(name="A", selector="a")]),
                                         Step(id="s3", kind="foreach", tag="deterministic", source="s2")])
        self.assertTrue(any("needs a column" in p for p in self.problems(bare)))

    def test_other_kinds_may_not_carry_loop_settings(self):
        bad = Playbook(name="x", steps=[S(1, "wait", seconds=1, column="x")])
        self.assertTrue(any("no 'column'" in p for p in self.problems(bad)))

    def test_the_loop_is_part_of_what_the_user_approves(self):
        a = loop_pb()
        for change in ({"column": "Name"}, {"body": 1}, {"max_rows": 5}, {"on_failure": "skip"}):
            b = a.model_copy(update={"steps": [a.steps[0], a.steps[1], a.steps[2].model_copy(update=change), *a.steps[3:]]})
            self.assertFalse(pbk.is_approved(b), change)
        wider = a.model_copy(update={"variables": [Variable(name="item", type="url", allowed_hosts=["a.com", "evil.com"], loop=True)]})
        self.assertFalse(pbk.is_approved(wider))
        text = a.model_copy(update={"variables": [Variable(name="item", type="url", allowed_hosts=["a.com"], loop=False)]})
        self.assertFalse(pbk.is_approved(text))

    def test_the_body_helper(self):
        pb = loop_pb()
        self.assertEqual([s.id for s in pbk.loop_body(pb, 2)], ["s4", "s5"])
        self.assertEqual(pbk.loop_body(pb, 0), [])


class Running(PlayerCase):
    def serve(self, answers):
        page = self.build()
        page.scrape_answers = answers
        return page

    def opened(self, page):
        return [a[1]["url"] for a in page.actions if a[0] == "navigate"]

    async def test_the_steps_after_it_run_once_for_each_value_with_that_value(self):
        page = self.serve([LIST, DETAIL])
        r = await self.run_pb(loop_pb())
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.opened(page), ["https://a.com/list", "https://a.com/p/1", "https://a.com/p/2", "https://a.com/p/3"])
        self.assertEqual(r.steps[2].detail, "3 rows to go through")

    async def test_every_page_read_in_the_loop_says_which_row_it_came_from(self):
        self.serve([LIST, DETAIL])
        r = await self.run_pb(loop_pb())
        tables = [o["table"] for o in r.outputs if o["table"]["columns"] != ["Name", "Link"]]
        self.assertEqual(len(tables), 3)
        self.assertEqual(tables[1]["columns"], ["Link", "Role"])
        self.assertEqual(tables[1]["rows"], [["https://a.com/p/2", "Engineer"]])
        self.assertEqual([o["query"] for o in r.outputs][1], "Details (row 1 of 3)")

    async def test_each_step_is_labelled_with_its_row(self):
        self.serve([LIST, DETAIL])
        r = await self.run_pb(loop_pb())
        notes = [s.note for s in r.steps]
        self.assertIn("Open a page (row 2 of 3)", notes)
        self.assertTrue(any("↻ Row 2 of 3" in m for m in self.msgs))

    async def test_values_are_used_once_and_empty_ones_are_not_used(self):
        page = self.serve([{"total": 5, "rows": [["A", "https://a.com/p/1"], ["B", "https://a.com/p/1"], ["C", ""], ["D", None], ["E", "https://a.com/p/2"]]}, DETAIL])
        r = await self.run_pb(loop_pb())
        self.assertEqual(self.opened(page)[1:], ["https://a.com/p/1", "https://a.com/p/2"])
        self.assertEqual(r.status, "done", r.error)

    async def test_the_row_limit_is_kept_and_said(self):
        many = {"total": 30, "rows": [[f"N{i}", f"https://a.com/p/{i}"] for i in range(30)]}
        page = self.serve([many, DETAIL])
        r = await self.run_pb(loop_pb(max_rows=4))
        self.assertEqual(len(self.opened(page)) - 1, 4)
        self.assertIn("26 more than the limit of 4 left out", r.steps[2].detail)

    async def test_the_default_limit_is_twenty(self):
        many = {"total": 40, "rows": [[f"N{i}", f"https://a.com/p/{i}"] for i in range(40)]}
        page = self.serve([many, DETAIL])
        await self.run_pb(loop_pb())
        self.assertEqual(len(self.opened(page)) - 1, pbk.DEFAULT_LOOP_ROWS)

    async def test_a_value_from_a_page_is_only_used_if_it_is_on_an_approved_site(self):
        page = self.serve([{"total": 3, "rows": [["A", "https://a.com/p/1"], ["B", "https://evil.com/steal"], ["C", "https://sub.a.com/p/3"]]}, DETAIL])
        r = await self.run_pb(loop_pb())
        self.assertEqual(self.opened(page)[1:], ["https://a.com/p/1", "https://sub.a.com/p/3"])
        self.assertNotIn("evil.com", " ".join(self.opened(page)))
        self.assertIn("1 left out", r.steps[2].detail)
        self.assertIn("evil.com", r.steps[2].detail)

    async def test_nothing_usable_is_a_failure_and_nothing_is_visited(self):
        page = self.serve([{"total": 2, "rows": [["A", "https://evil.com/1"], ["B", "https://evil.com/2"]]}, DETAIL])
        r = await self.run_pb(loop_pb())
        self.assertEqual(r.status, "failed")
        self.assertIn("None of the 2 values can be used", r.steps[2].detail)
        self.assertEqual(self.opened(page), ["https://a.com/list"])

    async def test_a_column_with_no_values_is_a_failure(self):
        self.serve([{"total": 2, "rows": [["A", None], ["B", ""]]}, DETAIL])
        r = await self.run_pb(loop_pb())                                           # the Link column has nothing in any row
        self.assertEqual(r.status, "failed")
        self.assertTrue(r.steps[1].status == "failed" or "has no values" in r.steps[2].detail)

    async def test_a_failing_row_stops_the_run_by_default_and_says_which(self):
        page = self.serve([LIST, DETAIL, ps.ScrapeError("A selector is not valid: x")])
        r = await self.run_pb(loop_pb())
        self.assertEqual(r.status, "failed")
        self.assertIn("row 2 of 3", r.steps[-1].note)
        self.assertEqual(len(self.opened(page)), 3)                                # list, row 1, row 2: it did not carry on

    async def test_a_failing_row_can_be_skipped_and_the_rest_carry_on(self):
        page = self.serve([LIST, DETAIL, ps.ScrapeError("boom"), DETAIL])
        r = await self.run_pb(loop_pb(on_failure="skip"))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(len(self.opened(page)), 4)                                # all three rows were visited
        self.assertEqual([s.status for s in r.steps if s.id == "s5"], ["ok", "failed", "ok"])
        self.assertEqual(r.error, "")
        self.assertTrue(any("Left out 1 row" in m for m in self.msgs))

    async def test_skipping_never_steps_over_leaving_the_approved_sites(self):
        page = self.serve([LIST, DETAIL])
        page.redirect["https://a.com/p/2"] = "https://evil.com/trap"
        r = await self.run_pb(loop_pb(on_failure="skip"))
        self.assertEqual(r.status, "failed")
        self.assertIn("is evil.com", " ".join(s.detail for s in r.steps))
        self.assertNotIn("https://a.com/p/3", self.opened(page))                    # it stopped at the wrong site

    async def test_skipping_never_steps_over_a_sign_in_wall(self):
        page = self.serve([LIST, DETAIL])
        page.redirect["https://a.com/p/2"] = "https://a.com/login"
        r = await self.run_pb(loop_pb(on_failure="skip"))
        self.assertEqual(r.status, "needs_you")
        self.assertNotIn("https://a.com/p/3", self.opened(page))

    async def test_skipping_never_steps_over_the_time_limit(self):
        page = self.serve([LIST, DETAIL])
        self.player.max_seconds = 5
        orig = page.scrape
        async def slow(*a):
            self.clock.now += 3
            return await orig(*a)
        page.scrape = slow
        r = await self.run_pb(loop_pb(on_failure="skip"))
        self.assertEqual(r.status, "failed")
        self.assertIn("took longer", r.error)

    async def test_there_is_a_pause_between_rows_but_not_before_the_first_or_after_the_last(self):
        self.serve([LIST, DETAIL])
        slept = []
        orig = self.player._sleep
        async def spy(t):
            slept.append(t)
            await orig(t)
        self.player._sleep = spy
        await self.run_pb(loop_pb())
        import playbook_player as pp
        self.assertEqual(slept.count(pp.ROW_PAUSE), 2)                             # between three rows

    async def test_what_the_loop_set_is_gone_after_it(self):
        self.serve([LIST, DETAIL])
        pb = loop_pb(extra=[S(6, "wait", seconds=1)])
        await self.run_pb(pb)
        self.assertNotIn("item", self.player._values)
        self.assertIsNone(self.player._loop_ctx)

    async def test_a_second_run_does_not_inherit_the_first_runs_loop(self):
        self.serve([LIST, DETAIL])
        await self.run_pb(loop_pb())
        page = self.serve([LIST, DETAIL])
        r = await self.run_pb(loop_pb())
        self.assertEqual(len(self.opened(page)), 4)
        self.assertEqual(r.status, "done")

    async def test_the_steps_after_the_loop_still_run(self):
        page = self.serve([LIST, DETAIL])
        r = await self.run_pb(loop_pb(extra=[S(6, "open", url="https://a.com/done")]))
        self.assertEqual(self.opened(page)[-1], "https://a.com/done")
        self.assertEqual(r.status, "done")

    async def test_a_loop_of_one_step_works(self):
        page = self.serve([LIST, DETAIL])
        steps = loop_pb().steps
        pb = make_pb([steps[0], steps[1], steps[2].model_copy(update={"body": 1}), steps[3]], variables=loop_pb().variables, approve=True)
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.opened(page)[1:], ["https://a.com/p/1", "https://a.com/p/2", "https://a.com/p/3"])

    async def test_a_loop_over_text_values_feeds_a_search(self):
        page = self.serve([{"total": 2, "rows": [["red shoes", "x"], ["blue socks", "y"]]}])
        steps = [S(1, "open", url="https://a.com/list"),
                 S(2, "scrape", row_selector="tr", fields=[ScrapeField(name="Name", selector=".n"), ScrapeField(name="Link", selector="a", type="link")]),
                 S(3, "foreach", source="s2", column="Name", variable="term", body=1),
                 S(4, "search", engine="duckduckgo", query="{{term}}")]
        r = await self.run_pb(make_pb(steps, variables=[Variable(name="term", type="text", loop=True)], approve=True))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.opened(page)[1:], ["https://duckduckgo.com/?q=red+shoes", "https://duckduckgo.com/?q=blue+socks"])

    async def test_a_number_loop_variable_checks_each_value(self):
        page = self.serve([{"total": 3, "rows": [["12", "x"], ["many", "y"], ["7", "z"]]}])
        steps = [S(1, "open", url="https://a.com/list"),
                 S(2, "scrape", row_selector="tr", fields=[ScrapeField(name="Count", selector=".n", type="text"), ScrapeField(name="L", selector="a", type="link")]),
                 S(3, "foreach", source="s2", column="Count", variable="n", body=1),
                 S(4, "wait", seconds=1)]
        r = await self.run_pb(make_pb(steps, variables=[Variable(name="n", type="number", loop=True)], approve=True))
        self.assertEqual(r.status, "done", r.error)
        self.assertIn("1 left out", r.steps[2].detail)
        self.assertEqual(len([s for s in r.steps if s.id == "s4"]), 2)

    async def test_the_chat_summary_shows_the_tables(self):
        self.serve([LIST, DETAIL])
        pb = loop_pb()
        r = await self.run_pb(pb)
        self.assertIn("Details (row 3 of 3)", summarize(pb, r))


class Budget(PlayerCase):
    async def test_a_run_keeps_only_so_many_cells_in_all(self):
        import playbook_player as pp
        page = self.build()
        big = {"total": 100, "rows": [[f"v{i}", f"https://a.com/p/{i}"] for i in range(100)]}
        page.scrape_answers = [{"total": 2, "rows": [["A", "https://a.com/p/1"], ["B", "https://a.com/p/2"]]}, big]
        old = pp.MAX_RUN_CELLS
        pp.MAX_RUN_CELLS = 150
        try:
            r = await self.run_pb(loop_pb())
        finally:
            pp.MAX_RUN_CELLS = old
        kept = sum(len(o["table"]["rows"]) * len(o["table"]["columns"]) for o in r.outputs)
        self.assertLessEqual(kept, 150)
        self.assertTrue(any(o["table"]["truncated"] for o in r.outputs))

    async def test_a_table_inside_the_budget_is_kept_whole(self):
        page = self.build()
        page.scrape_answers = [LIST, DETAIL]
        r = await self.run_pb(loop_pb())
        self.assertFalse(any(o["table"]["truncated"] for o in r.outputs))


if __name__ == "__main__":
    unittest.main()
