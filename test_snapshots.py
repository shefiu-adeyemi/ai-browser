"""Tests for snapshots.py."""

import json
import pathlib
import tempfile
import unittest

from snapshots import SnapshotStore, subject_of

PID = "pb_" + "a" * 12
T = [{"id": "s2", "columns": ["A"], "rows": [["1"]], "truncated": False}]


class Subjects(unittest.TestCase):
    def test_the_same_inputs_give_the_same_name_in_any_order(self):
        self.assertEqual(subject_of({"a": "1", "b": "2"}), subject_of({"b": "2", "a": "1"}))
        self.assertEqual(subject_of({}), subject_of(None))

    def test_different_inputs_give_different_names(self):
        self.assertNotEqual(subject_of({"a": "1"}), subject_of({"a": "2"}))
        self.assertNotEqual(subject_of({"a": "1"}), subject_of({"b": "1"}))

    def test_the_schedule_is_part_of_the_name(self):
        self.assertNotEqual(subject_of({"a": "1"}, "sch_1"), subject_of({"a": "1"}, "sch_2"))
        self.assertEqual(subject_of({"a": "1"}, "sch_1"), subject_of({"a": "1"}, "sch_1"))
        self.assertEqual(subject_of({"a": "1"}), subject_of({"a": "1"}, ""))

    def test_a_name_is_safe_to_use_in_a_file_name(self):
        self.assertRegex(subject_of({"p": "../../etc/passwd"}), r"^[0-9a-f]{16}$")


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.s = SnapshotStore(self.dir)
        self.sub = subject_of({"p": "x"})

    def test_nothing_before_the_first_run(self):
        self.assertEqual((self.s.history(PID, self.sub), self.s.latest(PID, self.sub)), ([], None))

    def test_snapshots_are_kept_oldest_first_and_survive_a_restart(self):
        self.s.add(PID, self.sub, "run_1", "fp", T, now=1.0)
        self.s.add(PID, self.sub, "run_2", "fp", T, now=2.0)
        again = SnapshotStore(self.dir)
        self.assertEqual([x["run_id"] for x in again.history(PID, self.sub)], ["run_1", "run_2"])
        self.assertEqual(again.latest(PID, self.sub)["tables"], T)

    def test_only_the_last_few_are_kept(self):
        s = SnapshotStore(self.dir, keep=3)
        for i in range(6):
            s.add(PID, self.sub, f"run_{i}", "fp", T, now=float(i))
        self.assertEqual([x["run_id"] for x in s.history(PID, self.sub)], ["run_3", "run_4", "run_5"])

    def test_each_set_of_inputs_has_its_own_history(self):
        other = subject_of({"p": "y"})
        self.s.add(PID, self.sub, "run_a", "fp", T)
        self.s.add(PID, other, "run_b", "fp", T)
        self.assertEqual(self.s.latest(PID, self.sub)["run_id"], "run_a")
        self.assertEqual(self.s.latest(PID, other)["run_id"], "run_b")

    def test_names_that_could_escape_the_folder_are_refused(self):
        for pid, sub in (("../x", self.sub), (PID, "../../x"), ("", self.sub), (PID, ""), ("pb_" + "g" * 12, self.sub)):
            with self.assertRaises(ValueError):
                self.s.add(pid, sub, "r", "fp", T)
            with self.assertRaises(ValueError):
                self.s.history(pid, sub)

    def test_a_damaged_file_starts_the_history_again_without_a_crash(self):
        self.s.add(PID, self.sub, "run_1", "fp", T)
        (self.dir / PID / f"{self.sub}.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.s.history(PID, self.sub), [])
        self.s.add(PID, self.sub, "run_2", "fp", T)
        self.assertEqual([x["run_id"] for x in self.s.history(PID, self.sub)], ["run_2"])

    def test_odd_content_is_filtered_not_trusted(self):
        f = self.dir / PID / f"{self.sub}.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"snapshots": ["junk", {"tables": "x"}, {"tables": [], "run_id": "ok"}, 5]}), encoding="utf-8")
        self.assertEqual([x["run_id"] for x in self.s.history(PID, self.sub)], ["ok"])
        f.write_text(json.dumps([1, 2]), encoding="utf-8")
        self.assertEqual(self.s.history(PID, self.sub), [])

    def test_deleting_a_playbook_removes_its_history_and_only_its(self):
        other = "pb_" + "b" * 12
        self.s.add(PID, self.sub, "r", "fp", T)
        self.s.add(PID, subject_of({"p": "y"}), "r", "fp", T)
        self.s.add(other, self.sub, "r", "fp", T)
        self.assertEqual(self.s.delete_playbook(PID), 2)
        self.assertEqual(self.s.history(PID, self.sub), [])
        self.assertEqual(len(self.s.history(other, self.sub)), 1)
        self.assertEqual(self.s.delete_playbook("../x"), 0)
        self.assertEqual(self.s.delete_playbook(PID), 0)

    def test_old_snapshots_are_pruned_and_empty_files_removed(self):
        day = 86400.0
        self.s.add(PID, self.sub, "old", "fp", T, now=0.0)
        self.s.add(PID, self.sub, "new", "fp", T, now=40 * day)
        lone = subject_of({"p": "lonely"})
        self.s.add(PID, lone, "ancient", "fp", T, now=1.0)
        self.assertEqual(self.s.prune_older_than(30, now=45 * day), 2)
        self.assertEqual([x["run_id"] for x in self.s.history(PID, self.sub)], ["new"])
        self.assertFalse((self.dir / PID / f"{lone}.json").exists())
        self.assertEqual(self.s.prune_older_than(30, now=45 * day), 0)

    def test_touching_refreshes_the_latest_only_and_survives_pruning(self):
        day = 86400.0
        self.s.add(PID, self.sub, "r1", "fp", T, now=0.0)
        self.s.touch(PID, self.sub, now=44 * day)
        self.assertEqual(self.s.prune_older_than(30, now=45 * day), 0)
        self.assertEqual(self.s.latest(PID, self.sub)["run_id"], "r1")
        self.s.touch(PID, subject_of({"nothing": "here"}))                         # touching what is not there is fine

    def test_pruning_with_no_folder_yet_is_fine(self):
        self.assertEqual(SnapshotStore(self.dir / "nope").prune_older_than(30), 0)


if __name__ == "__main__":
    unittest.main()
