"""Tests for monitor.py: the settings, the comparison and how a change is reported."""

import unittest

from aiba.scheduling.monitor import Diff, Watch, compare, counts, fingerprint, summarize, tables_of, to_record, watch_problems


def table(rows, columns=("Name", "Price"), tid="s2", truncated=False):
    return {"id": tid, "columns": list(columns), "rows": [list(r) for r in rows], "truncated": truncated}


def W(**kw):
    return Watch(enabled=True, **kw)


class Settings(unittest.TestCase):
    def test_good_settings_have_no_problems(self):
        w = W(key_columns=["Link"], ignore_columns=["Seen"], ignore_patterns=[r"\d+ (minutes?|hours?) ago"], min_change_percent=5)
        self.assertEqual(watch_problems(w), [])

    def test_off_by_default_and_everything_is_reported(self):
        w = Watch()
        self.assertFalse(w.enabled)
        self.assertTrue(w.report_added and w.report_removed and w.report_changed)

    def test_a_pattern_that_could_hang_is_refused(self):
        for bad in ("(a+)+", r"(\w+\s?)+$", "(.*)*", "(a*)*b"):
            self.assertTrue(watch_problems(W(ignore_patterns=[bad])), bad)

    def test_ordinary_patterns_are_fine(self):
        for ok in (r"\d+ minutes? ago", r"Updated: .*", r"\s+", r"(?:\d+)?", r"[0-9]{4}-[0-9]{2}-[0-9]{2}"):
            self.assertEqual(watch_problems(W(ignore_patterns=[ok])), [], ok)

    def test_bad_patterns_and_limits(self):
        self.assertTrue(watch_problems(W(ignore_patterns=["("])))                   # not valid
        self.assertTrue(watch_problems(W(ignore_patterns=[""])))
        self.assertTrue(watch_problems(W(ignore_patterns=["x" * 101])))
        with self.assertRaises(Exception):
            Watch(ignore_patterns=["a"] * 6)
        with self.assertRaises(Exception):
            Watch(min_change_percent=101)
        with self.assertRaises(Exception):
            Watch(min_change_percent=-1)

    def test_a_column_cannot_be_both_key_and_left_out(self):
        self.assertTrue(any("both a key" in p for p in watch_problems(W(key_columns=["Name"], ignore_columns=["name"]))))

    def test_something_must_be_reported(self):
        self.assertTrue(watch_problems(W(report_added=False, report_removed=False, report_changed=False)))
        self.assertEqual(watch_problems(Watch(report_added=False, report_removed=False, report_changed=False)), [])   # off: no matter

    def test_the_fingerprint_follows_what_matters_to_comparing(self):
        a = fingerprint(W(key_columns=["Name"]))
        self.assertEqual(a, fingerprint(W(key_columns=["name"])))                   # case does not matter
        self.assertEqual(a, fingerprint(W(key_columns=["Name"], report_added=False)))       # what to report is not about comparing
        for other in (W(key_columns=["Price"]), W(key_columns=["Name"], ignore_columns=["x"]), W(key_columns=["Name"], ignore_patterns=["a"]),
                      W(key_columns=["Name"], min_change_percent=1)):
            self.assertNotEqual(a, fingerprint(other))


class Snapshots(unittest.TestCase):
    def test_tables_are_taken_out_of_outputs(self):
        outputs = [{"step_id": "s2", "text": "x", "table": {"columns": ["A"], "rows": [["1"], [2]], "truncated": True}},
                   {"step_id": "s3", "text": "just text"}, "junk", None, {"step_id": "s4", "table": "bad"},
                   {"step_id": "s5", "table": {"columns": ["B"], "rows": ["junk", ["x"]]}}]
        got = tables_of(outputs)
        self.assertEqual([t["id"] for t in got], ["s2", "s5"])
        self.assertEqual(got[0], {"id": "s2", "columns": ["A"], "rows": [["1"], ["2"]], "truncated": True})
        self.assertEqual(got[1]["rows"], [["x"]])

    def test_nothing_in_nothing_out(self):
        self.assertEqual(tables_of([]), [])
        self.assertEqual(tables_of(None), [])


class Comparing(unittest.TestCase):
    def test_identical_tables_are_no_change(self):
        t = [table([["Ann", "5"], ["Bob", "7"]])]
        self.assertTrue(compare(t, t, W()).empty)

    def test_a_new_row_a_gone_row_and_a_moved_value(self):
        before = [table([["Ann", "5"], ["Bob", "7"], ["Cy", "9"]])]
        after = [table([["Ann", "6"], ["Cy", "9"], ["Dee", "1"]])]
        d = compare(before, after, W())
        self.assertEqual([i.key for i in d.added], ["Dee"])
        self.assertEqual([i.key for i in d.removed], ["Bob"])
        self.assertEqual([(c.key, c.columns) for c in d.changed], [("Ann", [("Price", "5", "6")])])
        self.assertEqual(counts(d), {"added": 1, "removed": 1, "changed": 1})

    def test_a_price_that_moved_is_one_changed_row_not_a_removal_and_an_addition(self):
        d = compare([table([["Shoes", "50"]])], [table([["Shoes", "45"]])], W())
        self.assertEqual((len(d.added), len(d.removed), len(d.changed)), (0, 0, 1))

    def test_the_order_of_rows_is_not_a_change(self):
        a = [table([["Ann", "5"], ["Bob", "7"]])]
        b = [table([["Bob", "7"], ["Ann", "5"]])]
        self.assertTrue(compare(a, b, W()).empty)

    def test_rows_are_matched_by_the_key_columns_chosen(self):
        cols = ("Name", "Link")
        before = [table([["Ann", "1"], ["Bob", "2"]], columns=cols)]
        after = [table([["Annie", "1"], ["Bob", "2"]], columns=cols)]
        d = compare(before, after, W())                                              # default key: Name, so Ann left and Annie came
        self.assertEqual(([i.key for i in d.added], [i.key for i in d.removed], len(d.changed)), (["Annie"], ["Ann"], 0))
        d = compare(before, after, W(key_columns=["Link"]))
        self.assertEqual([(c.key, c.columns) for c in d.changed], [("1", [("Name", "Ann", "Annie")])])

    def test_two_key_columns_together(self):
        before = [table([["A", "x", "1"], ["A", "y", "2"]], columns=("Name", "Where", "N"))]
        after = [table([["A", "x", "1"], ["A", "y", "3"]], columns=("Name", "Where", "N"))]
        d = compare(before, after, W(key_columns=["Name", "Where"]))
        self.assertEqual([c.key for c in d.changed], ["A / y"])

    def test_a_key_column_the_table_does_not_have_falls_back_to_its_first_column(self):
        d = compare([table([["Ann", "5"]])], [table([["Ann", "6"]])], W(key_columns=["Nope"]))
        self.assertEqual([c.key for c in d.changed], ["Ann"])

    def test_repeated_keys_are_told_apart_by_position(self):
        before = [table([["Ann", "1"], ["Ann", "2"]])]
        after = [table([["Ann", "1"], ["Ann", "3"]])]
        d = compare(before, after, W())
        self.assertEqual([c.key for c in d.changed], ["Ann (#2)"])
        self.assertTrue(compare(before, before, W()).empty)

    def test_whitespace_alone_is_not_a_change(self):
        d = compare([table([["Ann", "5 items"]])], [table([["Ann ", "5   items\n"]])], W())
        self.assertTrue(d.empty)

    def test_columns_can_be_left_out(self):
        before = [table([["Ann", "5", "3 mins ago"]], columns=("Name", "Price", "Seen"))]
        after = [table([["Ann", "5", "9 mins ago"]], columns=("Name", "Price", "Seen"))]
        self.assertFalse(compare(before, after, W()).empty)
        self.assertTrue(compare(before, after, W(ignore_columns=["seen"])).empty)

    def test_the_default_key_is_the_first_column_that_is_not_left_out(self):
        before = [table([["Ann", "5"], ["Bob", "7"]], columns=("Name", "Price"))]
        after = [table([["Ann", "5"], ["Bob", "8"], ["Cy", "1"]], columns=("Name", "Price"))]
        d = compare(before, after, W(ignore_columns=["Name"]))                        # keyed by Price now
        self.assertEqual(sorted(i.key for i in d.added), ["1", "8"])
        self.assertEqual([i.key for i in d.removed], ["7"])

    def test_a_table_with_everything_left_out_has_nothing_to_compare(self):
        t = [table([["Ann", "5"]])]
        self.assertTrue(compare(t, [table([["Zed", "9"]])], W(ignore_columns=["Name", "Price"])).empty)

    def test_one_record_both_times_is_the_same_record(self):
        before = [table([["Ann Lee", "Engineer"]], columns=("Name", "Headline"))]
        after = [table([["Ann Lee", "Manager"]], columns=("Name", "Headline"))]
        d = compare(before, after, W())
        self.assertEqual((len(d.added), len(d.removed)), (0, 0))
        self.assertEqual([(c.key, c.columns) for c in d.changed], [("Ann Lee", [("Headline", "Engineer", "Manager")])])
        renamed = compare(before, [table([["Ann Smith", "Engineer"]], columns=("Name", "Headline"))], W())
        self.assertEqual([c.columns for c in renamed.changed], [[("Name", "Ann Lee", "Ann Smith")]])

    def test_one_record_with_a_noisy_column_left_out(self):
        before = [table([["Updated 1 min ago"]], columns=("Seen",))]
        after = [table([["Updated 9 min ago"]], columns=("Seen",))]
        self.assertTrue(compare(before, after, W(ignore_columns=["seen"])).empty)
        self.assertEqual(len(compare(before, after, W()).changed), 1)

    def test_one_row_that_becomes_two_is_one_new_row(self):
        d = compare([table([["Ann", "5"]])], [table([["Ann", "5"], ["Bob", "7"]])], W())
        self.assertEqual(([i.key for i in d.added], len(d.removed), len(d.changed)), (["Bob"], 0, 0))

    def test_patterns_cut_noise_out_of_cells(self):
        pat = [r"\d+ (minutes?|hours?) ago"]
        before = [table([["Ann", "Engineer, active 5 minutes ago"]])]
        after = [table([["Ann", "Engineer, active 2 hours ago"]])]
        self.assertFalse(compare(before, after, W()).empty)
        self.assertTrue(compare(before, after, W(ignore_patterns=pat)).empty)
        real = [table([["Ann", "Manager, active 2 hours ago"]])]
        self.assertEqual(len(compare(before, real, W(ignore_patterns=pat)).changed), 1)

    def test_small_number_moves_can_be_ignored(self):
        before = [table([["Shoes", "$100.00"], ["Socks", "10"]])]
        after = [table([["Shoes", "$101.00"], ["Socks", "12"]])]
        d = compare(before, after, W(min_change_percent=5))
        self.assertEqual([c.key for c in d.changed], ["Socks"])                         # 1% is below 5%, 20% is not
        self.assertEqual(len(compare(before, after, W()).changed), 2)

    def test_the_percent_rule_does_not_hide_a_change_to_or_from_text(self):
        d = compare([table([["A", "10"]])], [table([["A", "sold out"]])], W(min_change_percent=50))
        self.assertEqual(len(d.changed), 1)

    def test_zero_to_zero_and_zero_to_something(self):
        self.assertTrue(compare([table([["A", "0"]])], [table([["A", "0.0"]])], W(min_change_percent=5)).empty)         # the same number
        self.assertEqual(len(compare([table([["A", "0"]])], [table([["A", "3"]])], W(min_change_percent=5)).changed), 1)

    def test_only_the_kinds_asked_for_are_reported(self):
        before = [table([["Ann", "5"], ["Bob", "7"]])]
        after = [table([["Ann", "6"], ["Cy", "1"]])]
        d = compare(before, after, W(report_added=False))
        self.assertEqual((len(d.added), len(d.removed), len(d.changed)), (0, 1, 1))
        d = compare(before, after, W(report_removed=False, report_changed=False))
        self.assertEqual((len(d.added), len(d.removed), len(d.changed)), (1, 0, 0))

    def test_tables_are_matched_by_their_step(self):
        before = [table([["A", "1"]], tid="s2"), table([["X", "1"]], tid="s5")]
        after = [table([["A", "1"]], tid="s2"), table([["X", "2"]], tid="s5")]
        d = compare(before, after, W())
        self.assertEqual([(c.table, c.key) for c in d.changed], [("s5", "X")])

    def test_a_table_that_appears_or_disappears_is_all_added_or_all_gone(self):
        d = compare([], [table([["A", "1"], ["B", "2"]])], W())
        self.assertEqual(len(d.added), 2)
        d = compare([table([["A", "1"]])], [], W())
        self.assertEqual(len(d.removed), 1)

    def test_loop_pages_are_matched_by_their_first_column(self):
        cols = ("Link", "Role")
        before = [table([["https://a/1", "Eng"]], columns=cols, tid="s5"), table([["https://a/2", "Des"]], columns=cols, tid="s5")]
        after = [table([["https://a/2", "Des"]], columns=cols, tid="s5"), table([["https://a/1", "Mgr"]], columns=cols, tid="s5")]
        d = compare(before, after, W())
        self.assertEqual([(c.key, c.columns) for c in d.changed], [("https://a/1", [("Role", "Eng", "Mgr")])])

    def test_a_cut_list_is_flagged(self):
        d = compare([table([["A", "1"]], truncated=True)], [table([["A", "2"]], truncated=True)], W())
        self.assertTrue(d.cut)
        self.assertIn("cut at its limit", summarize(d))
        self.assertFalse(compare([table([["A", "1"]])], [table([["A", "1"]])], W()).cut)

    def test_odd_rows_do_not_crash_it(self):
        t = {"id": "s", "columns": ["A", "B"], "rows": [["only one"], []], "truncated": False}
        self.assertTrue(compare([t], [t], W()).empty)


class Reporting(unittest.TestCase):
    def diff(self):
        return compare([table([["Ann", "5"], ["Bob", "7"], ["Cy", "9"]])], [table([["Ann", "6"], ["Dee", "1"]])], W())

    def test_the_summary_is_plain_and_short(self):
        text = summarize(self.diff())
        self.assertIn('"Ann": Price 5 → 6', text)
        self.assertIn('New: "Dee"', text)
        self.assertIn('Gone: "Bob"', text)
        self.assertEqual(summarize(Diff()), "")

    def test_many_changes_are_counted_not_listed(self):
        before = [table([[f"n{i}", "1"] for i in range(20)])]
        after = [table([[f"n{i}", "2"] for i in range(20)])]
        text = summarize(compare(before, after, W()), limit=3)
        self.assertEqual(text.count(";"), 3)
        self.assertIn("and 17 more", text)

    def test_long_values_are_shortened(self):
        d = compare([table([["x" * 200, "a" * 200]])], [table([["x" * 200, "b" * 200]])], W())
        self.assertLess(len(summarize(d)), 200)

    def test_the_record_keeps_exact_counts_and_a_capped_list(self):
        before = [table([[f"n{i}", "1"] for i in range(80)])]
        d = compare(before, [], W())
        rec = to_record(d)
        self.assertEqual((rec["removed"], len(rec["removed_rows"])), (80, 50))
        self.assertEqual(rec["added"], 0)

    def test_each_kind_is_capped_in_the_record_but_counted_exactly(self):
        added = to_record(compare([], [table([[f"n{i}", "1"] for i in range(80)])], W()))
        self.assertEqual((added["added"], len(added["added_rows"])), (80, 50))
        changed = to_record(compare([table([[f"n{i}", "1"] for i in range(80)])], [table([[f"n{i}", "2"] for i in range(80)])], W()))
        self.assertEqual((changed["changed"], len(changed["changed_rows"])), (80, 50))

    def test_the_record_has_the_changed_cells(self):
        rec = to_record(self.diff())
        self.assertEqual(rec["changed_rows"], [{"table": "s2", "key": "Ann", "columns": [{"column": "Price", "before": "5", "after": "6"}]}])


if __name__ == "__main__":
    unittest.main()
