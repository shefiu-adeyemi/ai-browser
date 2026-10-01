"""Tests for schedule_store.py."""

import json
import tempfile
import threading
import unittest
from pathlib import Path

import schedules as sch
from schedule_store import ScheduleNotFound, ScheduleStore, TooManySchedules
from schedules import Cadence, QuietHours, Schedule

PID = "pb_" + "a" * 12


def make(name="Daily", **kw):
    return Schedule(name=name, playbook_id=PID, items=[{"p": "x"}], cadence=Cadence(kind="daily", at="09:00"), **kw)


class Store(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "schedules.json"
        self.store = ScheduleStore(self.path)

    def test_create_assigns_id_and_dates(self):
        s = self.store.save(make())
        self.assertTrue(sch.is_valid_id(s.id))
        self.assertTrue(s.created_at and s.updated_at)
        self.assertEqual(self.store.get(s.id), s)
        self.assertEqual([x.id for x in self.store.all()], [s.id])

    def test_nothing_yet(self):
        self.assertEqual(self.store.all(), [])
        self.assertIsNone(self.store.get("sch_" + "0" * 12))
        self.assertFalse(self.store.paused_all)

    def test_a_user_edit_keeps_the_scheduler_state(self):
        s = self.store.save(make())
        self.store.change_state(s.id, lambda st: st.model_copy(update={"missed": 3, "queue": [0, 1], "last_status": "done"}))
        edited = self.store.save(self.store.get(s.id).model_copy(update={"name": "Renamed", "enabled": False}))
        again = self.store.get(s.id)
        self.assertEqual((again.name, again.enabled), ("Renamed", False))
        self.assertEqual((again.state.missed, again.state.queue, again.state.last_status), (3, [0, 1], "done"))
        self.assertEqual(edited.created_at, s.created_at)

    def test_a_user_save_with_a_blank_state_does_not_wipe_the_running_state(self):
        s = self.store.save(make())
        self.store.change_state(s.id, lambda st: st.model_copy(update={"missed": 2}))
        self.store.save(make("Renamed").model_copy(update={"id": s.id}))                       # a form knows no state
        self.assertEqual(self.store.get(s.id).state.missed, 2)

    def test_the_scheduler_never_touches_the_users_settings(self):
        s = self.store.save(make())
        self.store.save(s.model_copy(update={"name": "Edited by the user"}))
        self.store.change_state(s.id, lambda st: st.model_copy(update={"runs_total": 9}))
        got = self.store.get(s.id)
        self.assertEqual((got.name, got.state.runs_total), ("Edited by the user", 9))

    def test_state_changes_cannot_leak_a_half_applied_change(self):
        s = self.store.save(make())
        def boom(st): raise RuntimeError("bug")
        with self.assertRaises(RuntimeError):
            self.store.change_state(s.id, boom)
        self.assertEqual(self.store.get(s.id).state, sch.ScheduleState())

    def test_unknown_schedule(self):
        with self.assertRaises(ScheduleNotFound):
            self.store.save(make().model_copy(update={"id": "sch_" + "0" * 12}))
        with self.assertRaises(ScheduleNotFound):
            self.store.change_state("sch_" + "0" * 12, lambda st: st)
        self.assertFalse(self.store.delete("sch_" + "0" * 12))

    def test_a_deleted_schedule_is_not_recreated_by_a_late_state_change(self):
        s = self.store.save(make())
        self.store.delete(s.id)
        with self.assertRaises(ScheduleNotFound):
            self.store.change_state(s.id, lambda st: st)
        self.assertEqual(self.store.all(), [])

    def test_delete_and_delete_for_playbook(self):
        a, b = self.store.save(make("a")), self.store.save(make("b").model_copy(update={"playbook_id": "pb_" + "b" * 12}))
        self.assertEqual(self.store.delete_for_playbook(PID), 1)
        self.assertEqual([s.id for s in self.store.all()], [b.id])
        self.assertTrue(self.store.delete(b.id))
        self.assertEqual(self.store.delete_for_playbook(PID), 0)

    def test_the_number_of_schedules_is_limited(self):
        for i in range(sch.MAX_SCHEDULES):
            self.store.save(make(f"s{i}"))
        with self.assertRaises(TooManySchedules):
            self.store.save(make("one too many"))

    def test_pause_all_is_remembered(self):
        self.store.set_paused_all(True)
        self.assertTrue(ScheduleStore(self.path).paused_all)
        self.store.save(make())                                   # saving a schedule must not clear it
        self.assertTrue(ScheduleStore(self.path).paused_all)
        self.store.set_paused_all(False)
        self.assertFalse(ScheduleStore(self.path).paused_all)

    def test_quiet_hours_are_remembered(self):
        self.assertEqual(self.store.quiet_hours(), QuietHours())
        self.store.set_quiet_hours(QuietHours(enabled=True, start="23:00", end="06:30"))
        self.assertEqual(ScheduleStore(self.path).quiet_hours(), QuietHours(enabled=True, start="23:00", end="06:30"))
        self.store.save(make())                                       # saving a schedule or pausing must not lose them
        self.store.set_paused_all(True)
        self.assertTrue(ScheduleStore(self.path).quiet_hours().enabled)

    def test_unreadable_quiet_hours_fall_back_to_off(self):
        self.store.save(make())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["quiet_hours"] = {"enabled": "maybe", "start": 5}
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(ScheduleStore(self.path).quiet_hours(), QuietHours())
        self.assertEqual(len(ScheduleStore(self.path).all()), 1)

    def test_a_damaged_file_is_set_aside_not_overwritten(self):
        self.path.write_text("{nope", encoding="utf-8")
        self.assertEqual(self.store.all(), [])
        self.assertIn("could not be read", self.store.load_problem)
        aside = list(Path(self._tmp.name).glob("schedules.json.bad-*"))
        self.assertEqual(len(aside), 1)
        self.assertEqual(aside[0].read_text(encoding="utf-8"), "{nope")
        s = self.store.save(make())
        self.assertEqual(ScheduleStore(self.path).get(s.id).name, "Daily")
        self.assertEqual(self.store.load_problem, "")

    def test_one_unreadable_schedule_does_not_hide_the_others(self):
        good = self.store.save(make("good"))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["schedules"].append({"id": "sch_" + "9" * 12, "cadence": {"kind": "hourly"}})
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual([s.id for s in ScheduleStore(self.path).all()], [good.id])

    def test_unicode_and_no_leftover_temp_files(self):
        s = self.store.save(make("Profils d'été ✓ 日本"))
        self.assertEqual(ScheduleStore(self.path).get(s.id).name, "Profils d'été ✓ 日本")
        self.assertEqual(list(Path(self._tmp.name).glob("*.tmp")), [])

    def test_concurrent_saves_lose_nothing(self):
        def work():
            for i in range(5):
                self.store.save(make("x"))
        threads = [threading.Thread(target=work) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.store.all()), 20)
        self.assertEqual(len({s.id for s in self.store.all()}), 20)


if __name__ == "__main__":
    unittest.main()
