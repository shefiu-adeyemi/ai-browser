"""Watching for changes end to end: a scheduled run is compared with the last one, recorded, and announced once."""

import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.scheduling import schedules as sch
from aiba.scheduling.monitor import Watch
from aiba.playbooks.model import Playbook, ScrapeField, Step, Variable
from aiba.playbooks.player import RunResult, StepResult
from aiba.scheduling.limits import Decision
from aiba.storage.snapshots import SnapshotStore
from aiba.scheduling.scheduler import Scheduler
from test_schedules_app import Base, FakeClient, profile_pb


def table_out(rows, columns=("Name", "Price"), step="s2"):
    return [{"step_id": step, "query": "Prices", "text": f"{len(rows)} rows read",
             "table": {"columns": list(columns), "rows": [list(r) for r in rows], "truncated": False}}]


def done(outputs, steps=None):
    return RunResult(status="done", started_at=0, finished_at=1, outputs=outputs,
                     steps=steps or [StepResult("s1", "open"), StepResult("s2", "scrape")])


def price_pb():
    return pbk.refresh(Playbook(
        name="Prices", variables=[Variable(name="page", required=False)],
        steps=[Step(id="s1", kind="open", url="https://shop.test/list"),
               Step(id="s2", kind="scrape", row_selector="tr", fields=[ScrapeField(name="Name", selector=".n"), ScrapeField(name="Price", selector=".p")])]))


class WatchFlow(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws = FakeClient()
        agent_ui._clients.add(self.ws)
        self.addCleanup(agent_ui._clients.discard, self.ws)
        self.snaps = SnapshotStore(self.tmp / "snaps")
        self.pb2 = self.pbs.save(price_pb(), approve=True)
        self.reads: dict = {}                                    # what the page "shows" per value of `page`; "*" for any
        outer = self
        for target, value in (("_snapshots", self.snaps),):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(self.limits, "check", return_value=Decision(True))
        p.start(); self.addCleanup(p.stop)

        class FakePlayer:
            async def run(self, pb, values, approved=None, done_=None, answers=None, skipped=None, **kw):
                key = values.get("page", "*")
                return outer.reads.get(key, outer.reads.get("*"))
        async def build(pb, attended):
            return FakePlayer()
        p = mock.patch.object(agent_ui, "_build_player", build)
        p.start(); self.addCleanup(p.stop)
        self.real = Scheduler(schedules=self.scheds, playbooks=self.pbs, limits=self.limits, execute=agent_ui._execute_scheduled,
                              idle=lambda: not agent_ui._ctl.running, notify=agent_ui._notify_user,
                              resolved=agent_ui._record_parked_end)
        p = mock.patch.object(agent_ui, "_scheduler", self.real)
        p.start(); self.addCleanup(p.stop)

    def make(self, items=None, watch=None, **kw):
        s = self.scheds.save(sch.Schedule(name="Price watch", playbook_id=self.pb2.id, items=items or [{}],
                                          watch=watch if watch is not None else Watch(enabled=True), **kw))
        return self.due(s)

    def due(self, s):
        return self.scheds.change_state(s.id, lambda st: st.model_copy(update={"next_run": sch.iso(datetime.now() - timedelta(seconds=5))}))

    async def run_once(self, s, outputs=None, result=None):
        self.reads["*"] = result or done(outputs)
        self.due(s)
        await self.real.tick()
        await asyncio.sleep(0)
        return self.runs.recent(self.pb2.id)[0]["run_id"]

    def titles(self):
        return [n["title"] for n in self.notes.recent()]

    async def test_the_first_run_is_a_baseline_and_says_watching_has_started(self):
        s = self.make()
        rid = await self.run_once(s, table_out([["Ann", "5"]]))
        rec = self.runs.get(self.pb2.id, rid)
        self.assertEqual(rec["watch"], {"status": "baseline"})
        self.assertEqual(self.titles(), ['Now watching: "Price watch"'])
        self.assertIn("only when something changes", self.notes.recent()[0]["text"])

    async def test_a_change_is_recorded_and_announced_once_in_words(self):
        s = self.make()
        await self.run_once(s, table_out([["Ann", "5"], ["Bob", "7"]]))
        rid = await self.run_once(s, table_out([["Ann", "6"], ["Cy", "1"]]))
        rec = self.runs.get(self.pb2.id, rid)
        self.assertEqual(rec["watch"]["status"], "changed")
        self.assertEqual((rec["watch"]["added"], rec["watch"]["removed"], rec["watch"]["changed"]), (1, 1, 1))
        self.assertEqual(rec["watch"]["diff"]["changed_rows"][0]["columns"][0]["after"], "6")
        note = self.notes.recent()[0]
        self.assertEqual((note["title"], note["level"]), ('Changed: "Price watch"', "warning"))
        self.assertIn('"Ann": Price 5 → 6', note["text"])
        self.assertEqual(note["run_id"] if "run_id" in note else rid, rid)
        st = self.scheds.get(s.id).state
        self.assertIn("Ann", st.last_change)
        self.assertTrue(st.last_change_at)

    async def test_an_unchanged_run_says_nothing_at_all(self):
        s = self.make()
        await self.run_once(s, table_out([["Ann", "5"]]))
        before = len(self.notes.recent())
        rid = await self.run_once(s, table_out([["Ann", "5"]]))
        self.assertEqual(self.runs.get(self.pb2.id, rid)["watch"], {"status": "unchanged"})
        self.assertEqual(len(self.notes.recent()), before)
        self.assertEqual(self.toasts, [])

    async def test_the_run_list_says_which_runs_changed(self):
        s = self.make()
        await self.run_once(s, table_out([["Ann", "5"]]))
        await self.run_once(s, table_out([["Ann", "6"]]))
        self.assertEqual(sorted(r["watch"] for r in self.runs.recent(self.pb2.id)), ["baseline", "changed"])

    async def test_without_a_watch_nothing_is_compared_or_kept(self):
        s = self.make(watch=Watch(enabled=False))
        rid = await self.run_once(s, table_out([["Ann", "5"]]))
        self.assertNotIn("watch", self.runs.get(self.pb2.id, rid))
        self.assertFalse((self.tmp / "snaps").exists())
        self.assertNotIn('Now watching: "Price watch"', self.titles())

    async def test_a_run_that_read_no_table_is_skipped_with_a_reason(self):
        s = self.make()
        rid = await self.run_once(s, [{"step_id": "s2", "text": "x"}])
        w = self.runs.get(self.pb2.id, rid)["watch"]
        self.assertEqual(w["status"], "skipped")
        self.assertIn("no table", w["reason"])
        (note,) = self.notes.recent()                               # it ran but could not be watched: the user is told
        self.assertEqual(note["title"], 'Not watched this time: "Price watch"')
        self.assertIn("no table", note["text"])

    async def test_that_warning_comes_at_most_once_a_day(self):
        s = self.make()
        await self.run_once(s, [{"step_id": "s2", "text": "x"}])
        await self.run_once(s, [{"step_id": "s2", "text": "x"}])
        self.assertEqual(len([t for t in self.titles() if t.startswith("Not watched")]), 1)
        later = datetime.now() + timedelta(hours=25)
        self.real._now = lambda: later
        self.reads["*"] = done([{"step_id": "s2", "text": "x"}])
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"next_run": sch.iso(later - timedelta(seconds=5))}))
        await self.real.tick()
        self.assertEqual(len([t for t in self.titles() if t.startswith("Not watched")]), 2)

    async def test_a_run_that_failed_is_not_reported_as_not_watched(self):
        s = self.make(on_failure="skip")
        failed = RunResult(status="failed", error="boom", started_at=0, finished_at=1, steps=[StepResult("s1", "open", status="failed")])
        await self.run_once(s, result=failed)
        self.assertFalse([t for t in self.titles() if t.startswith("Not watched")])

    async def test_two_schedules_watching_the_same_page_do_not_disturb_each_other(self):
        a = self.make(watch=Watch(enabled=True))
        other = self.scheds.save(sch.Schedule(name="Other", playbook_id=self.pb2.id, items=[{}], watch=Watch(enabled=True, key_columns=["Price"])))
        b = self.due(other)
        self.reads["*"] = done(table_out([["Ann", "5"]]))
        self.due(a); await self.real.tick(); self.due(b); await self.real.tick()
        self.reads["*"] = done(table_out([["Ann", "6"]]))
        self.due(a); await self.real.tick(); self.due(b); await self.real.tick()
        states = [self.runs.get(self.pb2.id, r["run_id"]).get("watch", {}).get("status") for r in self.runs.recent(self.pb2.id)]
        self.assertEqual(sorted(states), ["baseline", "baseline", "changed", "changed"])

    async def test_a_failed_run_is_not_compared(self):
        s = self.make(on_failure="skip")
        await self.run_once(s, table_out([["Ann", "5"]]))
        failed = RunResult(status="failed", error="boom", started_at=0, finished_at=1, steps=[StepResult("s1", "open", status="failed")])
        rid = await self.run_once(s, result=failed)
        self.assertNotIn("watch", self.runs.get(self.pb2.id, rid))

    async def test_a_comparison_that_breaks_never_fails_the_run(self):
        s = self.make()
        with mock.patch.object(agent_ui.watcher, "observe", side_effect=RuntimeError("disk full")):
            rid = await self.run_once(s, table_out([["Ann", "5"]]))
        rec = self.runs.get(self.pb2.id, rid)
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["watch"]["status"], "skipped")

    async def test_a_batch_gives_one_message_for_all_the_rows_that_changed(self):
        items = [{"page": "a"}, {"page": "b"}, {"page": "c"}]
        s = self.make(items=items)
        for key in "abc":
            self.reads[key] = done(table_out([[f"item-{key}", "1"]]))
        for _ in range(3):
            self.due(self.scheds.get(s.id)) if not self.scheds.get(s.id).state.queue else None
            await self.real.tick()
        self.assertEqual(self.titles().count('Now watching: "Price watch"'), 1)                    # one, not three
        self.notes.clear()
        for key, price in (("a", "2"), ("b", "1"), ("c", "9")):
            self.reads[key] = done(table_out([[f"item-{key}", price]]))
        self.due(self.scheds.get(s.id))
        for _ in range(4):
            await self.real.tick()
        digest = [n for n in self.notes.recent() if n["level"] == "warning"]
        self.assertEqual(len(digest), 1)
        self.assertEqual(digest[0]["title"], '2 of 3 changed: "Price watch"')
        self.assertIn("Row 1:", digest[0]["text"])
        self.assertIn("Row 3:", digest[0]["text"])
        self.assertNotIn("Row 2:", digest[0]["text"])
        self.assertNotIn('Finished: "Price watch"', self.titles())                                # a watch does not say "all ran"

    async def test_a_batch_the_user_stopped_still_reports_what_the_rows_that_ran_found(self):
        s = self.make(items=[{"page": "a"}, {"page": "b"}])
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"batch_results": [
            sch.BatchResult(index=0, status="done", run_id="run_" + "a" * 12, watch="changed", change='"Ann": Price 5 → 6'),
            sch.BatchResult(index=1, status="stopped", detail="Stopped by you.")]}))
        await self.real._batch_over(self.scheds.get(s.id), stopped_by_user=True)
        titles = self.titles()
        self.assertIn('1 of 2 changed: "Price watch"', titles)
        self.assertIn('Stopped: "Price watch"', titles)

    async def test_an_ordinary_batch_still_says_it_finished(self):
        self.make(items=[{"page": "a"}, {"page": "b"}], watch=Watch(enabled=False))
        self.reads["*"] = done(table_out([["Ann", "5"]]))
        for _ in range(3):
            await self.real.tick()
        self.assertIn('Finished: "Price watch"', self.titles())

    async def test_changed_settings_say_a_new_comparison_has_started(self):
        s = self.make()
        await self.run_once(s, table_out([["Ann", "5"]]))
        self.notes.clear()
        saved = self.scheds.get(s.id)
        self.scheds.save(saved.model_copy(update={"watch": Watch(enabled=True, key_columns=["Price"])}))
        await self.run_once(s, table_out([["Ann", "6"]]))
        self.assertEqual(self.titles(), ['Now watching: "Price watch"'])
        self.assertIn("new comparison", self.notes.recent()[0]["text"])

    async def test_housekeeping_removes_old_snapshots_and_deleting_a_playbook_removes_its_own(self):
        s = self.make()
        await self.run_once(s, table_out([["Ann", "5"]]))
        subject = next((self.tmp / "snaps" / self.pb2.id).glob("*.json")).stem
        self.snaps.add(self.pb2.id, subject, "old", "fp", [], now=1.0)
        removed = await asyncio.to_thread(self.snaps.prune_older_than, 30)
        self.assertGreaterEqual(removed, 1)
        from fastapi.testclient import TestClient
        c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        c.get("/")
        self.assertTrue(c.delete(f"/playbooks/{self.pb2.id}").json()["ok"])
        self.assertFalse((self.tmp / "snaps" / self.pb2.id).exists())


class Api(Base):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")
        self.price = self.pbs.save(price_pb(), approve=True)

    def post(self, watch, playbook=None):
        body = {"schedule": {"name": "W", "playbook_id": (playbook or self.price).id, "items": [{}],
                             "cadence": {"kind": "daily", "at": "09:00"}, "watch": watch}}
        return self.c.post("/schedules", json=body)

    def test_a_watch_is_saved_and_shown(self):
        r = self.post({"enabled": True, "key_columns": ["Name"], "ignore_columns": ["Seen"], "ignore_patterns": [r"\d+ ago"],
                       "min_change_percent": 5, "report_removed": False}).json()
        self.assertTrue(r["ok"], r)
        row = self.c.get("/schedules").json()["schedules"][0]
        self.assertEqual(row["watch"]["key_columns"], ["Name"])
        self.assertFalse(row["watch"]["report_removed"])
        self.assertEqual(row["last_change"], "")

    def test_a_schedule_without_a_watch_has_it_off(self):
        body = {"schedule": {"name": "W", "playbook_id": self.price.id, "items": [{}], "cadence": {"kind": "daily", "at": "09:00"}}}
        self.assertTrue(self.c.post("/schedules", json=body).json()["ok"])
        self.assertFalse(self.c.get("/schedules").json()["schedules"][0]["watch"]["enabled"])

    def test_bad_settings_are_refused_with_reasons(self):
        for watch, text in (({"enabled": True, "ignore_patterns": ["(a+)+"]}, "repeats a repeat"),
                            ({"enabled": True, "ignore_patterns": ["("]}, "not valid"),
                            ({"enabled": True, "key_columns": ["A"], "ignore_columns": ["a"]}, "both a key"),
                            ({"enabled": True, "report_added": False, "report_removed": False, "report_changed": False}, "at least one kind")):
            r = self.post(watch)
            self.assertEqual(r.status_code, 400, watch)
            self.assertIn(text, " ".join(r.json().get("problems", [])), watch)

    def test_out_of_range_numbers_are_refused(self):
        for bad in (-1, 101, "lots"):
            self.assertNotEqual(self.post({"enabled": True, "min_change_percent": bad}).status_code, 200, bad)

    def test_watching_needs_a_playbook_that_reads_a_table(self):
        plain = self.pbs.save(profile_pb(), approve=True)
        r = self.post({"enabled": True}, playbook=plain)
        self.assertEqual(r.status_code, 400)
        self.assertIn("reads a table", " ".join(r.json()["problems"]))
        off = self.post({"enabled": False}, playbook=plain)
        self.assertNotIn("reads a table", " ".join(off.json().get("problems", [])))             # off: no need


if __name__ == "__main__":
    unittest.main()
