"""Tests for playbooks/runs.py."""

import os
import tempfile
import time
import unittest
from pathlib import Path

from aiba.playbooks.runs import RunStore

PID = "pb_" + "a" * 12


def rec(rid=None, status="done", started=None, steps=None):
    return {"run_id": rid or RunStore.new_id(), "status": status, "started_at": started if started is not None else time.time(),
            "finished_at": 0, "seconds": 1.5, "attended": True, "error": "", "playbook_version": 1,
            "steps": steps if steps is not None else [{"id": "s1", "status": "ok"}, {"id": "s2", "status": "failed"}]}


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = RunStore(Path(self._tmp.name), keep=5)

    def test_save_and_get_round_trip(self):
        r = self.store.save(PID, rec("run_" + "1" * 12))
        self.assertEqual(self.store.get(PID, r["run_id"]), r)
        self.assertEqual(r["playbook_id"], PID)

    def test_unicode_survives(self):
        r = self.store.save(PID, {**rec(), "error": "Étape ✓ échouée — 失败"})
        self.assertEqual(self.store.get(PID, r["run_id"])["error"], "Étape ✓ échouée — 失败")

    def test_ids_are_checked_before_touching_a_path(self):
        for bad_pid in ("../x", "pb_..", "", None, "pb_" + "g" * 12):
            with self.assertRaises(ValueError):
                self.store.save(bad_pid, rec())
            with self.assertRaises(ValueError):
                self.store.recent(bad_pid)
        for bad_rid in ("../../etc", "run_zz", "", None, "run_" + "a" * 11):
            with self.assertRaises(ValueError):
                self.store.get(PID, bad_rid)
        with self.assertRaises(ValueError):
            self.store.save(PID, {"status": "done"})                  # no run id
        self.assertEqual([p for p in Path(self._tmp.name).rglob("*") if p.is_file()], [])

    def test_unknown_run_and_unknown_playbook(self):
        self.assertIsNone(self.store.get(PID, "run_" + "0" * 12))
        self.assertEqual(self.store.recent(PID), [])

    def test_recent_is_newest_first_with_summaries(self):
        a = self.store.save(PID, rec(started=100, status="failed"))
        b = self.store.save(PID, rec(started=200))
        rows = self.store.recent(PID)
        self.assertEqual([r["run_id"] for r in rows], [b["run_id"], a["run_id"]])
        self.assertEqual((rows[0]["steps"], rows[0]["ok_steps"], rows[0]["status"]), (2, 1, "done"))
        self.assertNotIn("inputs", rows[0])
        self.assertEqual(len(self.store.recent(PID, limit=1)), 1)

    def test_a_damaged_file_is_skipped_not_fatal(self):
        good = self.store.save(PID, rec())
        (Path(self._tmp.name) / PID / f"{RunStore.new_id()}.json").write_text("{nope", encoding="utf-8")
        self.assertEqual([r["run_id"] for r in self.store.recent(PID)], [good["run_id"]])

    def test_only_the_newest_are_kept(self):
        ids = []
        for i in range(8):
            r = self.store.save(PID, rec(started=i))
            os.utime(Path(self._tmp.name) / PID / f"{r['run_id']}.json", (1000 + i, 1000 + i))
            ids.append(r["run_id"])
        kept = {p.stem for p in (Path(self._tmp.name) / PID).glob("run_*.json")}
        self.assertEqual(len(kept), 5)
        self.assertTrue(set(ids[-4:]) <= kept)

    def test_retention_deletes_old_runs_everywhere(self):
        other = "pb_" + "b" * 12
        old = self.store.save(PID, rec())
        new = self.store.save(other, rec())
        for pid, r in ((PID, old), ):
            f = Path(self._tmp.name) / pid / f"{r['run_id']}.json"
            os.utime(f, (time.time() - 40 * 86400,) * 2)
        self.assertEqual(self.store.prune_older_than(30), 1)
        self.assertIsNone(self.store.get(PID, old["run_id"]))
        self.assertIsNotNone(self.store.get(other, new["run_id"]))

    def test_delete_a_playbooks_runs(self):
        self.store.save(PID, rec())
        self.store.save(PID, rec())
        self.assertEqual(self.store.delete_playbook_runs(PID), 2)
        self.assertEqual(self.store.recent(PID), [])
        self.assertFalse((Path(self._tmp.name) / PID).exists())
        self.assertEqual(self.store.delete_playbook_runs(PID), 0)

    def test_a_picture_lives_and_dies_with_its_record(self):
        a, b = self.store.save(PID, rec(started=1)), self.store.save(PID, rec(started=2))
        for r in (a, b):
            self.store.save_screenshot(PID, r["run_id"], b"jpeg-" + r["run_id"].encode())
        self.assertEqual(self.store.screenshot(PID, a["run_id"]), b"jpeg-" + a["run_id"].encode())
        self.assertIsNone(self.store.screenshot(PID, "run_" + "0" * 12))
        files = lambda: sorted(p.name for p in (Path(self._tmp.name) / PID).iterdir())
        self.assertEqual(len(files()), 4)
        self.assertEqual(self.store.delete_playbook_runs(PID), 2)
        self.assertEqual(list((Path(self._tmp.name)).rglob("*.jpg")), [])

    def test_pruning_removes_the_pictures_too(self):
        ids = []
        for i in range(8):
            r = self.store.save(PID, rec(started=i))
            self.store.save_screenshot(PID, r["run_id"], b"x")
            os.utime(Path(self._tmp.name) / PID / f"{r['run_id']}.json", (1000 + i, 1000 + i))
            ids.append(r["run_id"])
        self.store.save(PID, rec(started=99))                          # triggers pruning down to 5
        jpgs = {p.stem for p in (Path(self._tmp.name) / PID).glob("*.jpg")}
        jsons = {p.stem for p in (Path(self._tmp.name) / PID).glob("*.json")}
        self.assertEqual(jpgs - jsons, set())                          # no picture without its record
        self.assertLessEqual(len(jsons), 5)

    def test_retention_removes_old_pictures(self):
        r = self.store.save(PID, rec())
        self.store.save_screenshot(PID, r["run_id"], b"x")
        os.utime(Path(self._tmp.name) / PID / f"{r['run_id']}.json", (time.time() - 40 * 86400,) * 2)
        self.assertEqual(self.store.prune_older_than(30), 1)
        self.assertEqual(list(Path(self._tmp.name).rglob("*.jpg")), [])

    def test_the_picture_ids_are_checked_too(self):
        for bad in ("../x", "run_zz", None):
            with self.assertRaises(ValueError):
                self.store.save_screenshot(PID, bad, b"x")
            with self.assertRaises(ValueError):
                self.store.screenshot(PID, bad)
        with self.assertRaises(ValueError):
            self.store.save_screenshot("../x", "run_" + "1" * 12, b"x")

    def test_no_temp_files_are_left(self):
        self.store.save(PID, rec())
        self.assertEqual(list(Path(self._tmp.name).rglob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
