"""Tests for scheduler.py with real stores in a temp folder, a fake clock and a fake runner."""

import asyncio
import random
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import playbook as pbk
import schedules as sch
from notifications import NotificationStore
from playbook import Check, Fingerprint, Playbook, Step, Variable
from playbook_store import PlaybookStore
from schedule_store import ScheduleStore
from schedules import Cadence, QuietHours, Schedule
from scheduler import Outcome, Scheduler
from site_limits import SiteLimits

T0 = datetime(2026, 10, 1, 9, 0, 0)              # a Thursday morning
PROFILE = "https://www.linkedin.com/in/{}"


class Rng(random.Random):
    def uniform(self, a, b): return a              # shortest waits: a gap is exactly its minimum


class Harness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Path(self._tmp.name)
        self.pbs, self.scheds = PlaybookStore(d / "pb"), ScheduleStore(d / "sch.json")
        self.limits = SiteLimits(d / "lim.json", rng=Rng())
        self.now = T0
        self.idle = True
        self.calls: list[tuple] = []                  # (playbook name, values, row)
        self.script: list = []                        # Outcomes (or exceptions) to return, in order
        self.notes: list[dict] = []
        self.maintained: list[int] = []

        async def execute(pb, values, s, row):
            self.calls.append((pb.name, dict(values), row))
            nxt = self.script.pop(0) if self.script else Outcome("done", run_id="run_" + "1" * 12)
            if isinstance(nxt, BaseException):
                raise nxt
            self.now += timedelta(seconds=3)          # the run took a few seconds
            return nxt

        async def notify(level, title, text="", **kw):
            self.notes.append({"level": level, "title": title, "text": text, **kw})

        async def maintenance(days):
            self.maintained.append(days)

        self.sched = Scheduler(schedules=self.scheds, playbooks=self.pbs, limits=self.limits, execute=execute,
                               idle=lambda: self.idle, notify=notify, now=lambda: self.now, maintenance=maintenance)
        self.pb = self.save_pb()

    # ------------------------------------------------------------------------ helpers
    def save_pb(self, approve=True, **kw):
        base = dict(name="Open a profile", variables=[Variable(name="p", type="linkedin_url")],
                    steps=[Step(id="s1", kind="open", url="{{p}}", check=Check(hosts=["www.linkedin.com"]))])
        base.update(kw)
        return self.pbs.save(pbk.refresh(Playbook(**base)), approve=approve)

    def add(self, items=None, cadence=None, next_run=None, **kw):
        items = items if items is not None else [{"p": PROFILE.format("jane")}]
        s = Schedule(name=kw.pop("name", "Daily"), playbook_id=kw.pop("playbook_id", self.pb.id), items=items,
                     cadence=cadence or Cadence(kind="daily", at="09:00"), **kw)
        s = self.scheds.save(s)
        nxt = next_run if next_run is not None else sch.iso(sch.next_run(s.cadence, self.now))
        return self.scheds.change_state(s.id, lambda st: st.model_copy(update={"next_run": nxt}))

    def get(self, s): return self.scheds.get(s.id)

    def rows(self, n, prefix="p"): return [{"p": PROFILE.format(f"{prefix}{i}")} for i in range(n)]

    async def ticks(self, n=1, step=timedelta(seconds=20)):
        for _ in range(n):
            await self.sched.tick()
            self.now += step


class Firing(Harness):
    async def test_a_due_schedule_runs_its_row_with_its_inputs(self):
        s = self.add(next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual(self.calls, [("Open a profile", {"p": PROFILE.format("jane")}, 0)])
        got = self.get(s)
        self.assertEqual((got.state.last_status, got.state.runs_total, got.state.queue), ("done", 1, []))
        self.assertEqual(got.state.next_run, "2026-10-02T09:00:00")        # the next day, not "now + 24h of catching up"

    async def test_nothing_happens_before_it_is_due(self):
        self.add(next_run=sch.iso(T0 + timedelta(hours=1)))
        await self.ticks(3)
        self.assertEqual(self.calls, [])

    async def test_a_run_a_little_late_still_runs(self):
        self.add(next_run=sch.iso(T0 - timedelta(minutes=4)))
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_a_run_missed_by_more_than_the_grace_is_skipped_and_recorded(self):
        s = self.add(next_run=sch.iso(T0 - timedelta(hours=3)))
        await self.sched.tick()
        self.assertEqual(self.calls, [])
        got = self.get(s)
        self.assertEqual((got.state.missed, got.state.last_status), (1, "missed"))
        self.assertEqual(got.state.next_run, "2026-10-02T09:00:00")
        self.assertEqual([(n["level"], n["title"]) for n in self.notes], [("info", 'Missed run: "Daily"')])
        await self.sched.tick()                                              # and it is not skipped again
        self.assertEqual(len(self.notes), 1)

    async def test_pause_all_stops_everything(self):
        self.add(next_run=sch.iso(T0))
        self.scheds.set_paused_all(True)
        await self.ticks(2)
        self.assertEqual(self.calls, [])
        self.scheds.set_paused_all(False)
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_disabled_and_paused_schedules_do_not_run(self):
        a = self.add(next_run=sch.iso(T0), enabled=False)
        b = self.add(next_run=sch.iso(T0), name="paused")
        self.scheds.change_state(b.id, lambda st: st.model_copy(update={"paused_reason": "failed"}))
        await self.ticks(2)
        self.assertEqual(self.calls, [])

    async def test_a_new_schedule_without_a_next_time_gets_one(self):
        s = self.add(next_run=None)
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"next_run": None}))
        await self.sched.tick()
        self.assertEqual(self.get(s).state.next_run, "2026-10-02T09:00:00")
        self.assertEqual(self.calls, [])

    async def test_an_interval_schedule_keeps_going(self):
        s = self.add(cadence=Cadence(kind="interval", every_minutes=30), next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual(self.get(s).state.next_run, "2026-10-01T09:30:00")
        self.now = T0 + timedelta(minutes=31)
        await self.sched.tick()
        self.assertEqual(len(self.calls), 2)

    async def test_a_one_off_runs_once_and_is_done(self):
        s = self.add(cadence=Cadence(kind="once", run_at="2026-10-01T09:00:30"), next_run="2026-10-01T09:00:30")
        self.now = T0 + timedelta(seconds=40)
        await self.ticks(3)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.get(s).state.next_run)

    async def test_the_clock_going_backwards_does_not_double_run(self):
        self.add(next_run=sch.iso(T0))
        await self.sched.tick()
        self.now = T0 - timedelta(hours=1)
        await self.ticks(2)
        self.assertEqual(len(self.calls), 1)


class Rows(Harness):
    async def test_rows_run_one_per_tick_in_order(self):
        s = self.add(items=self.rows(3), next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual([c[2] for c in self.calls], [0])
        self.assertEqual(self.get(s).state.queue, [1, 2])
        self.now += timedelta(seconds=120)                                   # past the gap between runs
        await self.sched.tick()
        self.now += timedelta(seconds=120)
        await self.sched.tick()
        self.assertEqual([c[2] for c in self.calls], [0, 1, 2])
        self.assertEqual([r.status for r in self.get(s).state.batch_results], ["done"] * 3)

    async def test_a_batch_of_several_rows_ends_with_one_summary(self):
        self.add(items=self.rows(2), next_run=sch.iso(T0))
        await self.ticks(8, step=timedelta(seconds=60))
        self.assertEqual([n["title"] for n in self.notes], ['Finished: "Daily"'])

    async def test_a_single_successful_row_is_silent(self):
        self.add(next_run=sch.iso(T0))
        await self.ticks(3)
        self.assertEqual(self.notes, [])

    async def test_the_user_is_not_interrupted_the_work_waits_its_turn(self):
        s = self.add(next_run=sch.iso(T0))
        self.idle = False
        await self.ticks(3)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.get(s).state.queue, [0])                       # due and queued, waiting
        self.idle = True
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_only_one_row_runs_per_tick_across_schedules_oldest_first(self):
        a = self.add(name="A", next_run=sch.iso(T0))
        b = self.add(name="B", next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)
        await self.ticks(5, step=timedelta(seconds=120))
        self.assertEqual(len(self.calls), 2)

    async def test_bad_row_inputs_are_a_failed_row_not_a_run(self):
        s = self.add(items=[{"p": "https://evil.com/in/x"}], next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual(self.calls, [])
        got = self.get(s)
        self.assertEqual(got.state.last_status, "failed")
        self.assertIn("not valid", got.state.last_detail)

    async def test_a_runner_that_crashes_is_a_failed_row(self):
        self.script = [RuntimeError("browser died")]
        s = self.add(next_run=sch.iso(T0))
        await self.sched.tick()
        got = self.get(s)
        self.assertEqual((got.state.last_status, got.state.paused_reason), ("failed", "failed"))
        self.assertIn("browser died", got.state.last_detail)

    async def test_a_busy_browser_leaves_the_row_waiting_without_penalty(self):
        self.script = [Outcome("busy")]
        s = self.add(next_run=sch.iso(T0))
        await self.sched.tick()
        got = self.get(s)
        self.assertEqual((got.state.queue, got.state.runs_total, got.state.paused_reason), ([0], 0, ""))
        self.assertEqual(self.limits.used_today("linkedin_profile_view", T0.date()), 0)      # nothing happened, nothing counted
        await self.sched.tick()
        self.assertEqual(self.get(s).state.last_status, "done")

    async def test_cancellation_is_not_swallowed(self):
        self.script = [asyncio.CancelledError()]
        self.add(next_run=sch.iso(T0))
        with self.assertRaises(asyncio.CancelledError):
            await self.sched.tick()

    async def test_a_schedule_deleted_while_its_row_runs_is_not_brought_back(self):
        s = self.add(next_run=sch.iso(T0))
        orig = self.sched._execute

        async def deleting(pb, values, sc, row):
            self.scheds.delete(sc.id)
            return await orig(pb, values, sc, row)
        self.sched._execute = deleting
        await self.sched.tick()
        self.assertEqual(self.scheds.all(), [])


class Failures(Harness):
    async def test_stop_policy_pauses_the_schedule_and_tells_the_user(self):
        self.script = [Outcome("done"), Outcome("needs_you", run_id="run_" + "2" * 12, detail="linkedin.com is asking you to sign in.")]
        s = self.add(items=self.rows(4), next_run=sch.iso(T0), on_failure="stop")
        await self.ticks(8, step=timedelta(seconds=60))
        self.assertEqual([c[2] for c in self.calls], [0, 1])                 # rows 2 and 3 never ran
        got = self.get(s)
        self.assertEqual((got.state.paused_reason, got.state.queue), ("needs_you", []))
        self.assertIn("asking you to sign in", got.state.paused_detail)
        self.assertEqual([(n["level"], n["title"]) for n in self.notes], [("error", 'Needs you: "Daily"')])
        self.assertEqual(self.notes[0]["run_id"], "run_" + "2" * 12)
        self.assertIn("paused until you resume", self.notes[0]["text"])

    async def test_skip_policy_carries_on_and_sends_one_summary(self):
        self.script = [Outcome("done"), Outcome("needs_you", detail="sign in"), Outcome("done"), Outcome("failed", detail="no element")]
        s = self.add(items=self.rows(4), next_run=sch.iso(T0), on_failure="skip")
        await self.ticks(10, step=timedelta(seconds=60))
        self.assertEqual([c[2] for c in self.calls], [0, 1, 2, 3])
        got = self.get(s)
        self.assertEqual(got.state.paused_reason, "")                       # nothing is paused
        self.assertEqual([r.status for r in got.state.batch_results], ["done", "needs_you", "done", "failed"])
        self.assertEqual([n["title"] for n in self.notes], ['"Daily": 2 of 4 need you'])
        self.assertEqual(self.notes[0]["level"], "warning")
        self.assertIn("Row 2: sign in", self.notes[0]["text"])

    async def test_skip_policy_where_everything_fails_is_an_error(self):
        self.script = [Outcome("failed", detail="x"), Outcome("failed", detail="y")]
        self.add(items=self.rows(2), next_run=sch.iso(T0), on_failure="skip")
        await self.ticks(6, step=timedelta(seconds=60))
        self.assertEqual(self.notes[0]["level"], "error")

    async def test_the_next_scheduled_time_still_comes_after_a_skip_batch(self):
        self.script = [Outcome("failed", detail="x")]
        s = self.add(items=self.rows(1), next_run=sch.iso(T0), on_failure="skip")
        await self.ticks(3)
        self.assertEqual(self.get(s).state.next_run, "2026-10-02T09:00:00")
        self.now = datetime(2026, 10, 2, 9, 0, 5)
        await self.sched.tick()
        self.assertEqual(len(self.calls), 2)

    async def test_the_user_pressing_stop_cancels_the_batch_without_pausing(self):
        self.script = [Outcome("stopped", detail="Stopped by you.")]
        s = self.add(items=self.rows(3), next_run=sch.iso(T0), on_failure="stop")
        await self.ticks(4, step=timedelta(seconds=60))
        self.assertEqual(len(self.calls), 1)
        got = self.get(s)
        self.assertEqual((got.state.paused_reason, got.state.queue), ("", []))
        self.assertEqual([n["title"] for n in self.notes], ['Stopped: "Daily"'])
        self.assertEqual(self.notes[0]["level"], "info")

    async def test_resume_clears_the_pause_and_finds_the_next_time(self):
        self.script = [Outcome("failed", detail="x")]
        s = self.add(next_run=sch.iso(T0), on_failure="stop")
        await self.sched.tick()
        self.assertEqual(self.get(s).state.paused_reason, "failed")
        got = self.sched.resume(s.id)
        self.assertEqual((got.state.paused_reason, got.state.queue), ("", []))
        self.assertTrue(got.state.next_run)
        await self.ticks(2)
        self.assertEqual(len(self.calls), 1)                                 # not run again until it is due
        with self.assertRaises(Exception):
            self.sched.resume("sch_" + "0" * 12)


class Approval(Harness):
    async def test_editing_the_playbook_stops_scheduled_runs(self):
        s = self.add(next_run=sch.iso(T0))
        pb = self.pbs.get(self.pb.id)
        self.pbs.save(pb.model_copy(update={"steps": [pb.steps[0].model_copy(update={"new_tab": True})]}))   # approval drops
        await self.sched.tick()
        self.assertEqual(self.calls, [])
        got = self.get(s)
        self.assertEqual(got.state.paused_reason, "not_approved")
        self.assertIn("Approve the playbook", got.state.paused_detail)
        self.assertEqual([(n["level"], n["title"]) for n in self.notes], [("error", 'Paused: "Daily"')])

    async def test_a_playbook_that_was_never_approved_does_not_run(self):
        other = self.save_pb(approve=False, name="Unapproved")
        s = self.add(playbook_id=other.id, next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.get(s).state.paused_reason, "not_approved")

    async def test_a_playbook_with_a_send_step_needs_the_schedules_own_yes(self):
        fp = Fingerprint(node_name="button", ax_name="Send")
        risky = self.save_pb(name="Sends", variables=[], steps=[Step(id="s1", kind="open", url="https://a.com/"),
                                                                Step(id="s2", kind="click", target=fp, ask=True)])
        no = self.add(playbook_id=risky.id, items=[{}], next_run=sch.iso(T0), name="no")
        yes = self.add(playbook_id=risky.id, items=[{}], next_run=sch.iso(T0), name="yes", allow_sensitive=True)
        await self.ticks(3, step=timedelta(seconds=60))
        self.assertEqual(self.get(no).state.paused_reason, "not_approved")
        self.assertEqual([c[0] for c in self.calls], ["Sends"])
        self.assertEqual(self.get(yes).state.last_status, "done")

    async def test_a_deleted_or_damaged_playbook_pauses_the_schedule(self):
        s = self.add(next_run=sch.iso(T0))
        self.pbs.delete(self.pb.id)
        await self.sched.tick()
        got = self.get(s)
        self.assertEqual((got.state.paused_reason, got.state.paused_detail), ("invalid", "The playbook was deleted."))
        s2 = self.add(next_run=sch.iso(T0), playbook_id=self.save_pb(name="B").id)
        (Path(self._tmp.name) / "pb" / f"{s2.playbook_id}.json").write_text("{nope", encoding="utf-8")
        await self.sched.tick()
        self.assertEqual(self.get(s2).state.paused_reason, "invalid")


class Pacing(Harness):
    def use_up_the_day(self, n=50):
        for _ in range(n):
            self.limits.record({"linkedin_profile_view": 1}, self.now - timedelta(hours=1))

    async def test_the_daily_allowance_holds_the_rest_until_tomorrow(self):
        self.use_up_the_day()
        s = self.add(items=self.rows(3), next_run=sch.iso(T0))
        await self.ticks(4, step=timedelta(minutes=10))
        self.assertEqual(self.calls, [])
        got = self.get(s)
        self.assertEqual(got.state.queue, [0, 1, 2])
        self.assertEqual(sch.parse_iso(got.state.resume_after).date(), T0.date() + timedelta(days=1))
        self.assertEqual([n["level"] for n in self.notes], ["warning"])      # told once, not every tick
        self.assertIn("continue tomorrow", self.notes[0]["text"])
        self.now = datetime(2026, 10, 2, 10, 0)
        await self.sched.tick()
        self.assertEqual([c[2] for c in self.calls], [0])

    async def test_a_gap_is_kept_between_two_runs_on_the_same_site(self):
        s = self.add(items=self.rows(2), next_run=sch.iso(T0))
        await self.sched.tick()                                              # row 0 runs (3 s long)
        self.now += timedelta(seconds=10)                                    # only ~13 s since it started
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)                                 # row 1 waits for the gap
        self.assertEqual(self.notes, [])                                     # a gap wait is not worth a message
        self.now += timedelta(seconds=60)
        await self.sched.tick()
        self.assertEqual(len(self.calls), 2)

    async def test_a_waiting_schedule_does_not_block_another_that_can_run(self):
        self.use_up_the_day()
        profile = self.add(name="profiles", items=self.rows(1), next_run=sch.iso(T0))
        other = self.save_pb(name="elsewhere", variables=[], steps=[Step(id="s1", kind="open", url="https://a.com/")])
        free = self.add(name="free", playbook_id=other.id, items=[{}], next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual([c[0] for c in self.calls], ["elsewhere"])

    async def test_runs_are_counted_toward_the_allowance(self):
        self.add(items=self.rows(2), next_run=sch.iso(T0))
        await self.ticks(6, step=timedelta(seconds=60))
        self.assertEqual(self.limits.used_today("linkedin_profile_view", T0.date()), 2)

    async def test_a_playbook_that_is_not_limited_is_not_counted(self):
        other = self.save_pb(name="elsewhere", variables=[], steps=[Step(id="s1", kind="open", url="https://a.com/")])
        self.add(playbook_id=other.id, items=[{}], next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual(self.limits.used_today("linkedin_profile_view", T0.date()), 0)
        self.assertEqual(len(self.calls), 1)


class UserActions(Harness):
    async def test_run_now(self):
        s = self.add(items=self.rows(2), next_run=sch.iso(T0 + timedelta(days=1)))
        self.assertEqual(await self.sched.run_now(s.id), "")
        self.assertEqual(self.get(s).state.queue, [0, 1])
        self.assertEqual(self.get(s).state.next_run, sch.iso(T0 + timedelta(days=1)))   # the normal schedule is untouched
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("already running", await self.sched.run_now(s.id))

    async def test_run_now_works_on_a_switched_off_schedule(self):
        s = self.add(next_run=sch.iso(T0 + timedelta(days=1)), enabled=False)
        self.assertEqual(await self.sched.run_now(s.id), "")
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.get(s).state.last_status, "done")
        await self.ticks(3, step=timedelta(days=1))                          # but it does not start running by the clock
        self.assertEqual(len(self.calls), 1)

    async def test_run_now_refusals(self):
        self.assertIn("no longer exists", await self.sched.run_now("sch_" + "0" * 12))
        s = self.add(next_run=sch.iso(T0))
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"paused_reason": "failed"}))
        self.assertIn("paused", await self.sched.run_now(s.id))


class CrashSafety(Harness):
    """A send must never go out twice because the app died at the wrong moment."""
    async def test_the_row_is_marked_as_running_while_it_runs_and_cleared_after(self):
        s = self.add(next_run=sch.iso(T0))
        seen = {}
        orig = self.sched._execute

        async def peek(pb, values, sc, row):
            seen["inflight"] = self.scheds.get(sc.id).state.inflight
            return await orig(pb, values, sc, row)
        self.sched._execute = peek
        await self.sched.tick()
        self.assertEqual(seen["inflight"], 0)
        self.assertIsNone(self.get(s).state.inflight)

    async def test_a_row_that_was_running_when_the_app_died_is_not_run_again(self):
        s = self.add(items=self.rows(2), next_run=sch.iso(T0 + timedelta(days=1)), on_failure="stop")
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"queue": [0, 1], "inflight": 0, "batch_id": "b_1", "batch_started": sch.iso(T0)}))
        await self.ticks(3, step=timedelta(seconds=60))
        self.assertEqual(self.calls, [])                                     # NOT executed again
        got = self.get(s)
        self.assertEqual((got.state.paused_reason, got.state.queue, got.state.inflight), ("failed", [], None))
        self.assertIn("may or may not have finished", got.state.paused_detail)
        self.assertEqual([n["title"] for n in self.notes], ['Failed: "Daily"'])

    async def test_with_the_skip_choice_the_next_rows_carry_on(self):
        s = self.add(items=self.rows(3), next_run=sch.iso(T0 + timedelta(days=1)), on_failure="skip")
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"queue": [0, 1, 2], "inflight": 0, "batch_id": "b_1", "batch_started": sch.iso(T0)}))
        await self.ticks(6, step=timedelta(seconds=60))
        self.assertEqual([c[2] for c in self.calls], [1, 2])
        got = self.get(s)
        self.assertEqual([r.status for r in got.state.batch_results], ["failed", "done", "done"])
        self.assertEqual(got.state.paused_reason, "")

    async def test_an_interrupted_row_still_counts_toward_the_daily_allowance(self):
        s = self.add(next_run=sch.iso(T0))
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"queue": [0], "inflight": 0, "batch_id": "b_1", "batch_started": sch.iso(T0)}))
        await self.sched.tick()
        self.assertEqual(self.limits.used_today("linkedin_profile_view", T0.date()), 1)

    async def test_recovery_happens_even_while_everything_is_paused(self):
        s = self.add(next_run=sch.iso(T0))
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"queue": [0], "inflight": 0, "batch_id": "b_1", "batch_started": sch.iso(T0)}))
        self.scheds.set_paused_all(True)
        await self.sched.tick()
        self.assertIsNone(self.get(s).state.inflight)

    async def test_a_busy_browser_clears_the_marker(self):
        self.script = [Outcome("busy")]
        s = self.add(next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertIsNone(self.get(s).state.inflight)
        self.assertEqual(self.get(s).state.queue, [0])

    async def test_a_runner_that_crashes_clears_the_marker(self):
        self.script = [RuntimeError("boom")]
        s = self.add(next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertIsNone(self.get(s).state.inflight)

    async def test_a_failing_notification_does_not_undo_the_bookkeeping(self):
        async def broken(*a, **k): raise OSError("disk full")
        self.sched._notify = broken
        self.script = [Outcome("needs_you", detail="sign in")]
        s = self.add(next_run=sch.iso(T0), on_failure="stop")
        await self.sched.tick()
        got = self.get(s)
        self.assertEqual((got.state.paused_reason, got.state.last_status, got.state.inflight), ("needs_you", "needs_you", None))


class OldBatches(Harness):
    def waiting(self, started, resume=None, **kw):
        s = self.add(items=self.rows(3), next_run=sch.iso(T0 + timedelta(days=1)), **kw)
        return self.scheds.change_state(s.id, lambda st: st.model_copy(update={
            "queue": [0, 1, 2], "batch_id": "b_1", "batch_started": sch.iso(started), "resume_after": resume}))

    async def test_a_batch_that_never_got_its_turn_is_a_missed_run_not_a_late_one(self):
        s = self.waiting(T0 - timedelta(hours=13))
        await self.sched.tick()
        self.assertEqual(self.calls, [])
        got = self.get(s)
        self.assertEqual((got.state.queue, got.state.missed, got.state.last_status), ([], 1, "missed"))
        self.assertEqual([(n["level"], n["title"]) for n in self.notes], [("info", 'Missed run: "Daily"')])

    async def test_a_recent_batch_still_runs(self):
        self.waiting(T0 - timedelta(hours=11))
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_a_batch_held_for_the_daily_allowance_is_kept_however_old(self):
        s = self.waiting(T0 - timedelta(days=3), resume=sch.iso(T0 + timedelta(hours=1)))
        await self.sched.tick()
        self.assertEqual(self.get(s).state.queue, [0, 1, 2])
        self.assertEqual(self.notes, [])


class Quiet(Harness):
    async def test_nothing_starts_in_quiet_hours_and_it_runs_when_they_end(self):
        self.scheds.set_quiet_hours(QuietHours(enabled=True, start="08:00", end="10:00"))
        s = self.add(next_run=sch.iso(T0))                                   # due at 09:00, inside the window
        await self.ticks(3)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.get(s).state.queue, [0])                       # due and waiting
        self.now = T0.replace(hour=10, minute=1)
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_quiet_hours_off_change_nothing(self):
        self.scheds.set_quiet_hours(QuietHours(enabled=False, start="08:00", end="10:00"))
        self.add(next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_an_overnight_window(self):
        self.scheds.set_quiet_hours(QuietHours(enabled=True, start="22:00", end="07:00"))
        self.now = datetime(2026, 10, 1, 23, 30)
        self.add(next_run=sch.iso(self.now))
        await self.sched.tick()
        self.assertEqual(self.calls, [])
        self.now = datetime(2026, 10, 2, 7, 5)
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)


class Loop(Harness):
    async def test_housekeeping_runs_once_a_day(self):
        await self.sched.tick()
        await self.sched.tick()
        self.assertEqual(self.maintained, [30])
        self.now += timedelta(days=1)
        await self.sched.tick()
        self.assertEqual(self.maintained, [30, 30])

    async def test_a_failing_housekeeping_does_not_stop_the_tick(self):
        async def bad(days): raise RuntimeError("disk")
        self.sched._maintenance = bad
        self.add(next_run=sch.iso(T0))
        await self.sched.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_the_loop_survives_a_failing_tick_and_stops_when_cancelled(self):
        count = {"n": 0}
        orig = self.sched.tick

        async def flaky():
            count["n"] += 1
            if count["n"] == 1:
                raise RuntimeError("boom")
        self.sched.tick = flaky
        task = asyncio.create_task(self.sched.run_forever(tick_seconds=0.01))
        for _ in range(100):
            if count["n"] >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertGreaterEqual(count["n"], 3)


if __name__ == "__main__":
    unittest.main()
