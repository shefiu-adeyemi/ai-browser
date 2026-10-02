"""Tests for watcher.py: what a run's comparison says."""

import pathlib
import tempfile
import unittest

from monitor import Watch
from snapshots import SnapshotStore, subject_of
from watcher import observe

PID = "pb_" + "a" * 12


def out(rows, step="s2", columns=("Name", "Price"), truncated=False):
    return [{"step_id": step, "text": f"{len(rows)} rows read",
             "table": {"columns": list(columns), "rows": [list(r) for r in rows], "truncated": truncated}}]


OK = [{"id": "s1", "status": "ok"}, {"id": "s2", "status": "ok"}]


class Observing(unittest.TestCase):
    def setUp(self):
        self.store = SnapshotStore(pathlib.Path(tempfile.mkdtemp()))
        self.watch = Watch(enabled=True)
        self.values = {"p": "x"}

    def see(self, outputs, run="run_1", steps=OK, values=None, watch=None):
        return observe(self.store, PID, self.values if values is None else values, run, outputs, steps, watch or self.watch)

    def test_the_first_run_is_a_baseline(self):
        r = self.see(out([["Ann", "5"]]))
        self.assertEqual(r, {"status": "baseline"})
        self.assertEqual(len(self.store.history(PID, subject_of(self.values))), 1)

    def test_the_same_again_says_nothing(self):
        self.see(out([["Ann", "5"]]))
        self.assertEqual(self.see(out([["Ann", "5"]]), run="run_2"), {"status": "unchanged"})

    def test_a_change_is_described_and_counted(self):
        self.see(out([["Ann", "5"], ["Bob", "7"]]))
        r = self.see(out([["Ann", "6"], ["Cy", "1"]]), run="run_2")
        self.assertEqual(r["status"], "changed")
        self.assertEqual((r["added"], r["removed"], r["changed"]), (1, 1, 1))
        self.assertIn("Ann", r["summary"])
        self.assertEqual(r["diff"]["changed_rows"][0]["columns"][0], {"column": "Price", "before": "5", "after": "6"})

    def test_it_compares_with_the_run_before_not_the_first_one(self):
        self.see(out([["Ann", "5"]]))
        self.see(out([["Ann", "6"]]), run="run_2")
        self.assertEqual(self.see(out([["Ann", "6"]]), run="run_3")["status"], "unchanged")

    def test_each_set_of_inputs_is_watched_on_its_own(self):
        self.see(out([["Ann", "5"]]), values={"p": "a"})
        self.assertEqual(self.see(out([["Zed", "9"]]), values={"p": "b"})["status"], "baseline")
        self.assertEqual(self.see(out([["Ann", "5"]]), values={"p": "a"}, run="run_2")["status"], "unchanged")

    def test_a_run_with_a_failed_step_is_not_compared_or_kept(self):
        self.see(out([["Ann", "5"]]))
        bad = [{"id": "s1", "status": "ok"}, {"id": "s2", "status": "failed"}]
        r = self.see(out([]), run="run_2", steps=bad)
        self.assertEqual(r["status"], "skipped")
        self.assertIn("failed", r["reason"])
        self.assertEqual(len(self.store.history(PID, subject_of(self.values))), 1)
        self.assertEqual(self.see(out([["Ann", "5"]]), run="run_3")["status"], "unchanged")      # the baseline was not spoiled

    def test_a_run_that_read_no_table_is_skipped(self):
        for outputs in ([], [{"step_id": "s2", "text": "just text"}], None):
            r = self.see(outputs)
            self.assertEqual(r["status"], "skipped")
            self.assertIn("no table", r["reason"])
        self.assertEqual(self.store.history(PID, subject_of(self.values)), [])

    def test_changed_settings_start_a_new_comparison_instead_of_a_wrong_diff(self):
        self.see(out([["Ann", "5"]]))
        r = self.see(out([["Ann", "6"]]), run="run_2", watch=Watch(enabled=True, key_columns=["Price"]))
        self.assertEqual(r["status"], "reset")
        self.assertEqual(self.see(out([["Ann", "7"]]), run="run_3", watch=Watch(enabled=True, key_columns=["Price"]))["status"], "changed")

    def test_the_noise_settings_apply(self):
        w = Watch(enabled=True, min_change_percent=10)
        self.see(out([["Shoes", "100"]]), watch=w)
        self.assertEqual(self.see(out([["Shoes", "103"]]), run="run_2", watch=w)["status"], "unchanged")
        self.assertEqual(self.see(out([["Shoes", "150"]]), run="run_3", watch=w)["status"], "changed")

    def test_a_small_drift_cannot_hide_a_slow_change_forever(self):
        w = Watch(enabled=True, min_change_percent=10)                              # each step is small, together they are not
        self.see(out([["Shoes", "100"]]), watch=w)
        got = [self.see(out([["Shoes", price]]), run=f"run_{i}", watch=w)["status"] for i, price in enumerate(("105", "108", "115", "116"), start=2)]
        self.assertEqual(got, ["unchanged", "unchanged", "changed", "unchanged"])   # reported once it passed 10%, then the new level is the reference

    def test_an_unchanged_run_confirms_the_reference_so_retention_does_not_lose_it(self):
        self.see(out([["Ann", "5"]]))
        subject = subject_of(self.values)
        self.store.touch(PID, subject, now=1.0)
        self.see(out([["Ann", "5"]]), run="run_2")
        self.assertEqual(len(self.store.history(PID, subject)), 1)                  # still the one reference, not a pile of copies
        self.assertGreater(self.store.latest(PID, subject)["at"], 1.0)

    def test_the_same_page_watched_by_two_schedules_has_two_histories(self):
        one = observe(self.store, PID, self.values, "r1", out([["Ann", "5"]]), OK, self.watch, scope="sch_1")
        two = observe(self.store, PID, self.values, "r1", out([["Ann", "5"]]), OK, self.watch, scope="sch_2")
        self.assertEqual((one["status"], two["status"]), ("baseline", "baseline"))
        again = observe(self.store, PID, self.values, "r2", out([["Ann", "6"]]), OK, self.watch, scope="sch_1")
        self.assertEqual(again["status"], "changed")
        self.assertEqual(observe(self.store, PID, self.values, "r2", out([["Ann", "5"]]), OK, self.watch, scope="sch_2")["status"], "unchanged")

    def test_odd_steps_do_not_crash_it(self):
        self.assertEqual(self.see(out([["A", "1"]]), steps=[None, "x", {"status": "ok"}])["status"], "baseline")


if __name__ == "__main__":
    unittest.main()
