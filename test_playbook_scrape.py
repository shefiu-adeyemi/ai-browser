"""Tests for playbook_scrape.py (cleaning, failure rules, how the page is asked) and the scrape step's format rules."""

import json
import unittest
import unittest.mock

import playbook as pbk
from playbook import Playbook, ScrapeField, Step
import playbook_scrape as ps


def f(name, selector="td", type="text", attr=None):
    return {"name": name, "selector": selector, "type": type, "attr": attr}


class Numbers(unittest.TestCase):
    def test_numbers_are_read_out_of_text(self):
        for text, want in (("$1,299.50", "1299.5"), ("about 12 items", "12"), ("-3.10", "-3.1"), ("100", "100"),
                           ("1,000,000", "1000000"), (".5", "0.5"), ("0.00", "0"), ("EUR 7", "7"), ("v2 of 3", "2")):
            self.assertEqual(ps._number(text), want, text)

    def test_no_number_is_an_empty_cell_not_a_guess(self):
        for text in ("", "n/a", "none", "--"):
            self.assertEqual(ps._number(text), "", text)

    def test_no_scientific_notation(self):
        self.assertEqual(ps._number("1e10"), "1")                 # "e10" is not part of the number: read what is plainly there
        self.assertNotIn("E", ps._number("1000000000000000000000"))


class Cells(unittest.TestCase):
    def test_text_is_tidied_and_capped(self):
        self.assertEqual(ps._cell("  a \n\t b  ", "text"), "a b")
        self.assertEqual(len(ps._cell("x" * 5000, "text")), ps.MAX_CELL)
        self.assertEqual(ps._cell(None, "text"), "")

    def test_only_web_links_are_kept(self):
        self.assertEqual(ps._cell("https://a.com/x", "link"), "https://a.com/x")
        for bad in ("javascript:alert(1)", "data:text/html,hi", "/relative", "mailto:a@b.c", "file:///c:/x"):
            self.assertEqual(ps._cell(bad, "link"), "", bad)

    def test_an_attribute_is_text_like_any_other(self):
        self.assertEqual(ps._cell(" id-7 ", "attr"), "id-7")


class Tables(unittest.TestCase):
    fields = [f("Name", ".n"), f("Price", ".p", "number"), f("Link", "a", "link")]

    def test_rows_become_a_clean_table(self):
        raw = {"total": 2, "rows": [[" Ann ", "$5.00", "https://a.com/1"], ["Bob", "n/a", None]]}
        table, why = ps.build_table(self.fields, raw, True)
        self.assertEqual(why, "")
        self.assertEqual(table["columns"], ["Name", "Price", "Link"])
        self.assertEqual(table["rows"], [["Ann", "5", "https://a.com/1"], ["Bob", "", ""]])
        self.assertFalse(table["truncated"])

    def test_a_field_missing_in_some_rows_is_just_empty(self):
        raw = {"total": 2, "rows": [["A", "1", None], ["B", None, "https://a.com/2"]]}
        table, why = ps.build_table(self.fields, raw, True)
        self.assertEqual(why, "")
        self.assertEqual(table["rows"][1][1], "")

    def test_no_rows_is_a_failure_not_an_empty_success(self):
        table, why = ps.build_table(self.fields, {"total": 0, "rows": []}, True)
        self.assertIsNone(table)
        self.assertIn("No rows", why)

    def test_nothing_at_all_in_one_record_mode_says_so(self):
        table, why = ps.build_table(self.fields, {"total": 0, "rows": []}, False)
        self.assertIsNone(table)
        self.assertEqual(why, "Nothing was found on the page.")

    def test_a_field_empty_in_every_row_names_the_selector(self):
        raw = {"total": 2, "rows": [["A", None, "https://a.com"], ["B", None, "https://b.com"]]}
        table, why = ps.build_table(self.fields, raw, True)
        self.assertIsNone(table)
        self.assertIn('".p"', why)
        self.assertIn('"Price"', why)

    def test_one_row_mode_needs_every_field(self):
        table, why = ps.build_table(self.fields, {"total": 1, "rows": [["A", None, "https://a.com"]]}, False)
        self.assertIsNone(table)
        self.assertIn("on the page", why)

    def test_more_rows_on_the_page_than_were_read_is_said(self):
        raw = {"total": 900, "rows": [["A", "1", "https://a.com"]] * 3}
        table, _ = ps.build_table(self.fields, raw, True)
        self.assertTrue(table["truncated"])
        self.assertEqual(table["total"], 900)

    def test_a_huge_table_is_cut_to_the_cell_budget(self):
        wide = [f(f"c{i}", "td") for i in range(10)]
        raw = {"total": 50, "rows": [["x"] * 10 for _ in range(50)]}
        with unittest.mock.patch.object(ps, "MAX_CELLS", 100):
            table, _ = ps.build_table(wide, raw, True)
        self.assertEqual(len(table["rows"]), 10)
        self.assertTrue(table["truncated"])

    def test_odd_rows_do_not_crash_it(self):
        raw = {"total": 3, "rows": [["only one"], "junk", None, ["A", "2", "https://a.com"]]}
        table, why = ps.build_table(self.fields, raw, True)
        self.assertEqual(why, "")
        self.assertEqual(len(table["rows"]), 2)


class FakeEvalPage:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def evaluate(self, fn, *args):
        self.calls.append((fn, args))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class ReadPage(unittest.IsolatedAsyncioTestCase):
    async def test_the_selectors_travel_as_arguments_not_inside_the_script(self):
        page = FakeEvalPage(json.dumps({"total": 1, "rows": [["A"]]}))
        evil = "a'); alert(1); ('"
        out = await ps.read_page(page, evil, [f("N", evil)], 10)
        fn, args = page.calls[0]
        self.assertNotIn("alert", fn)
        self.assertEqual(args[0], evil)
        self.assertEqual(args[1], [{"selector": evil, "type": "text", "attr": None}])
        self.assertEqual(out, {"total": 1, "rows": [["A"]]})

    async def test_the_row_limit_is_kept_in_range(self):
        for asked, want in ((None, 100), (0, 100), (5, 5), (10 ** 6, 500), (-3, 1)):
            page = FakeEvalPage(json.dumps({"total": 0, "rows": []}))
            await ps.read_page(page, "tr", [f("N")], asked)
            self.assertEqual(page.calls[0][1][2], want, asked)

    async def test_a_bad_selector_is_reported_in_plain_words(self):
        page = FakeEvalPage(json.dumps({"error": "'<<' is not a valid selector"}))
        with self.assertRaises(ps.ScrapeError) as cm:
            await ps.read_page(page, "<<", [f("N")], 10)
        self.assertIn("not valid", str(cm.exception))

    async def test_a_page_that_errors_or_answers_oddly_is_a_scrape_error(self):
        for answer in (RuntimeError("target closed"), "not json", json.dumps([1, 2]), json.dumps({"rows": "x"})):
            with self.assertRaises(ps.ScrapeError, msg=str(answer)):
                await ps.read_page(FakeEvalPage(answer), "tr", [f("N")], 10)

    async def test_the_script_only_reads(self):
        for word in ("click(", ".submit", ".value =", "innerHTML =", "fetch(", "XMLHttpRequest", "eval(", "location"):
            self.assertNotIn(word, ps._READ_PAGE, word)


class Arrows(unittest.TestCase):
    def test_all_three_scripts_share_the_same_helpers(self):
        import playbook_picker, playbook_suggest
        from piercing_js import HELPERS
        for script in (ps._READ_PAGE, playbook_picker._START):
            self.assertIn(HELPERS.strip(), script)
            self.assertNotIn("/*HELPERS*/", script)
        self.assertIn("shadowRoot", playbook_suggest._DIGEST)

    def test_the_separator_is_the_one_the_scripts_split_on(self):
        from piercing_js import HELPERS, SEPARATOR
        self.assertEqual(SEPARATOR, ">>>")
        self.assertIn("split('>>>')", HELPERS)

    def test_a_step_may_use_arrow_selectors_and_they_are_part_of_the_approval(self):
        step = Step(id="s1", kind="scrape", row_selector="#host >>> li.it", fields=[ScrapeField(name="N", selector="#in >>> em")])
        pb = pbk.refresh(Playbook(name="x", steps=[step]))
        self.assertEqual(pbk.validate(pb), [])
        a = pbk.with_approval(pb)
        changed = a.model_copy(update={"steps": [a.steps[0].model_copy(update={"row_selector": "#other >>> li.it"})]})
        self.assertFalse(pbk.is_approved(changed))


class Format(unittest.TestCase):
    def pb(self, **kw):
        step = dict(id="s1", kind="scrape", row_selector="tr", fields=[ScrapeField(name="Name", selector="td")])
        step.update(kw)
        return Playbook(name="x", steps=[Step(**step)])

    def test_a_good_scrape_step_is_valid_and_needs_no_model(self):
        self.assertEqual(pbk.validate(self.pb()), [])
        self.assertNotIn("scrape", pbk.AI_KINDS)

    def test_it_needs_fields(self):
        self.assertTrue(any("at least one field" in p for p in pbk.validate(self.pb(fields=[]))))
        self.assertTrue(any("'fields'" in p or "at least one field" in p for p in pbk.validate(self.pb(fields=None))))

    def test_field_rules(self):
        cases = {
            "named twice": [ScrapeField(name="A", selector="td"), ScrapeField(name="a", selector="td")],
            "must start with a letter": [ScrapeField(name="1x", selector="td")],
            "needs a selector": [ScrapeField(name="A", selector="  ")],
            "attribute to read": [ScrapeField(name="A", selector="td", type="attr")],
            "only an attribute field": [ScrapeField(name="A", selector="td", type="text", attr="id")],
        }
        for text, fields in cases.items():
            self.assertTrue(any(text in p for p in pbk.validate(self.pb(fields=fields))), text)

    def test_a_bad_attribute_name_is_refused(self):
        bad = [ScrapeField(name="A", selector="td", type="attr", attr='x" onload="y')]
        self.assertTrue(pbk.validate(self.pb(fields=bad)))
        good = [ScrapeField(name="A", selector="td", type="attr", attr="data-id")]
        self.assertEqual(pbk.validate(self.pb(fields=good)), [])

    def test_scope_means_the_row_and_needs_one(self):
        scope = [ScrapeField(name="Link", selector=":scope", type="link")]
        self.assertEqual(pbk.validate(self.pb(fields=scope)), [])
        self.assertTrue(any("':scope'" in p for p in pbk.validate(self.pb(fields=scope, row_selector=None))))

    def test_other_kinds_may_not_carry_scrape_settings(self):
        bad = Playbook(name="x", steps=[Step(id="s1", kind="wait", seconds=1, row_selector="tr")])
        self.assertTrue(any("no 'row_selector'" in p for p in pbk.validate(bad)))

    def test_limits(self):
        with self.assertRaises(Exception):
            Step(id="s1", kind="scrape", max_rows=0)
        with self.assertRaises(Exception):
            Step(id="s1", kind="scrape", max_rows=501)
        with self.assertRaises(Exception):
            Step(id="s1", kind="scrape", fields=[ScrapeField(name=f"f{i}", selector="td") for i in range(31)])

    def test_the_selectors_are_part_of_what_the_user_approves(self):
        a = pbk.with_approval(pbk.refresh(self.pb()))
        self.assertTrue(pbk.is_approved(a))
        for change in ({"row_selector": "tr.other"}, {"max_rows": 5},
                       {"fields": [ScrapeField(name="Name", selector="td.other")]}):
            b = a.model_copy(update={"steps": [a.steps[0].model_copy(update=change)]})
            self.assertFalse(pbk.is_approved(b), change)


if __name__ == "__main__":
    unittest.main()
