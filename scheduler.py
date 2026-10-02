"""
The scheduler: every few seconds it asks "is anything due, and may it run now?" and, if so, runs ONE row.

It decides; it does not run browsers. Running a playbook with nobody there is handed to `execute`
(agent_ui wires it to the same player and guard as a manual run, with attended=False), so this file is
tested with a fake page and a fake clock.

The policy it enforces (PLAYBOOKS.md, "Approval policy" and "Failure behaviour"):

  * Schedules only fire while the app is open. A run that was due more than GRACE ago (the app was closed,
    the computer asleep) is SKIPPED and recorded as missed, never run late.
  * A scheduled run only does what the user approved: the playbook must still be approved (editing it
    removes the approval), and sensitive steps need the schedule's own "allow" tick. If either is gone the
    schedule is paused with the reason and the user is told.
  * One run at a time, and never while the user's own task is running: due work waits its turn.
  * Pacing: daily allowances per kind of activity (ramping up from a low start) and a random gap between
    runs on the same site (site_limits.py). A day's allowance used up means the rest waits for tomorrow.
  * When a row stops because it needs the user (a sign-in wall, an approval nobody can give) or fails, the
    schedule's own choice applies: STOP (pause the schedule, tell the user) or SKIP (tell the user, carry on
    with the next row). The user pressing Stop cancels the batch without pausing anything.
  * Notifications are only for what needs attention, one per event (a skipping batch gets one summary).
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

import playbook as pbk
import schedules as sch
from playbook_store import PlaybookInvalid, PlaybookNotFound, PlaybookStore
from schedule_store import ScheduleNotFound, ScheduleStore
from schedules import Schedule, ScheduleState
from site_limits import SiteLimits, activities

log = logging.getLogger(__name__)

GRACE = timedelta(minutes=5)
MAX_ANSWER = 2000                       # a typed answer is a short value, never a document
STALE_ANSWER = timedelta(hours=24)      # a yes that has waited this long past its question's deadline is void
STALE_BATCH = timedelta(hours=12)       # a waiting batch older than this (and not held for a daily allowance) is dropped
TICK_SECONDS = 15
RETENTION_DAYS = 30


@dataclass
class Outcome:
    status: str                  # done | failed | needs_you | stopped | busy (could not start) | waiting (parked)
    run_id: str = ""
    detail: str = ""
    waiting: Optional[dict] = None     # for "waiting": message, detail, url, step_id, timeout_minutes
    watch: Optional[dict] = None       # for a watched run: {"status": baseline|unchanged|changed|reset|skipped, "summary": ...}


Notify = Callable[..., Awaitable[None]]


class Scheduler:
    def __init__(self, *, schedules: ScheduleStore, playbooks: PlaybookStore, limits: SiteLimits,
                 execute: Callable[..., Awaitable[Outcome]],
                 idle: Callable[[], bool], notify: Notify,
                 now: Callable[[], datetime] = datetime.now, grace: timedelta = GRACE,
                 maintenance: Optional[Callable[[int], Awaitable[None]]] = None,
                 resolved: Optional[Callable[[Schedule, "sch.ParkedRun", str, str], None]] = None) -> None:
        self.schedules, self.playbooks, self.limits = schedules, playbooks, limits
        self._execute, self._idle, self._notify, self._now = execute, idle, notify, now
        self.grace = grace
        self._maintenance = maintenance
        self._resolved = resolved                           # told when a parked run is answered no, expires or is cancelled
        self._maintained_on: Optional[str] = None
        self._typed: dict[str, dict[str, str]] = {}        # run id -> typed answers. In memory ONLY: never written to disk
        self._warned_cap: set[str] = set()                 # batches already told about a daily limit

    # ----------------------------------------------------------------------------- loop
    async def run_forever(self, tick_seconds: float = TICK_SECONDS) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(tick_seconds)

    async def tick(self) -> None:
        """One pass: housekeeping, fire or skip what is due, then run at most one row."""
        now = self._now()
        await self._recover_interrupted(now)           # bookkeeping, even while everything is paused
        await self._expire_parked(now)
        if self.schedules.paused_all:
            return
        await self._housekeeping(now)
        for s in self.schedules.all():
            await self._drop_if_stale(s, now)
            await self._fire_or_miss(s, now)
        await self._run_one_row(now)

    # --------------------------------------------------------- crashes and old batches
    async def _recover_interrupted(self, now: datetime) -> None:
        """A row marked in flight when no run can be going (a tick never overlaps another) means the app
        died or was closed while it ran. It may or may not have finished (a message may have been sent), so
        it is NOT run again: it is reported, and the schedule's own failure choice decides what comes next."""
        for s in self.schedules.all():
            row = s.state.inflight
            if row is None:
                continue
            outcome = Outcome("failed", detail="The app was closed while this row was running, so it may or may "
                                               "not have finished. It was not run again.")
            pb, acts = None, None
            try:                                              # it probably did start: count it toward the daily allowance
                pb = self.playbooks.get(s.playbook_id)
                values, _errors = pbk.bind_values(pb, s.items[row] if row < len(s.items) else {})
                acts = activities(pb, values)
            except Exception:
                pass
            await self._finish_row(s, row, outcome, pb, acts, now)

    async def _drop_if_stale(self, s: Schedule, now: datetime) -> None:
        """A batch left waiting from long ago (the app was closed, or the user was busy all day) is a missed
        run, not something to start now. A batch held back for a daily allowance is waiting on purpose."""
        st = s.state
        started = sch.parse_iso(st.batch_started)
        if not st.queue or st.resume_after or started is None or now - started <= STALE_BATCH:
            return
        self._set_state(s.id, lambda x: x.model_copy(update={
            "queue": [], "missed": x.missed + 1, "last_status": "missed",
            "last_detail": f"Skipped: a batch from {started:%b %d %H:%M} never got its turn."}))
        await self._say("info", f'Missed run: "{s.name}"',
                        f"The run from {started:%b %d at %H:%M} never got a turn, so it was skipped.",
                        schedule_id=s.id, playbook_id=s.playbook_id)

    # ------------------------------------------------------------------ fire or miss
    async def _fire_or_miss(self, s: Schedule, now: datetime) -> None:
        st = s.state
        if not s.enabled or st.paused_reason or st.queue:
            return
        due = sch.parse_iso(st.next_run)
        if due is None:
            if st.next_run is None and st.runs_total == 0 and not st.last_run_at and st.missed == 0:
                self._set_state(s.id, lambda x: x.model_copy(update={"next_run": sch.iso(sch.next_run(s.cadence, now))}))
            return
        if now < due:
            return
        upcoming = sch.iso(sch.next_run(s.cadence, now))
        if now - due > self.grace:
            self._set_state(s.id, lambda x: x.model_copy(update={
                "next_run": upcoming, "missed": x.missed + 1, "last_status": "missed",
                "last_detail": f"Skipped: the app was not running at {due:%b %d %H:%M}."}))
            await self._say("info", f'Missed run: "{s.name}"',
                            f"The app was not running at {due:%H:%M} on {due:%b %d}, so that run was skipped.",
                            schedule_id=s.id, playbook_id=s.playbook_id)
            return
        self._start_batch(s, now, upcoming)

    def _start_batch(self, s: Schedule, now: datetime, upcoming: Optional[str]) -> None:
        batch = "b_" + "%06x" % random.randrange(1 << 24)
        self._set_state(s.id, lambda x: x.model_copy(update={
            "queue": list(range(len(s.items))), "batch_id": batch, "batch_started": sch.iso(now),
            "batch_results": [], "resume_after": None, "next_run": upcoming}))

    async def run_now(self, sid: str) -> str:
        """Queue every row of a schedule now. Returns "" if started, else why not."""
        s = self.schedules.get(sid)
        if s is None:
            return "That schedule no longer exists."
        if s.state.queue:
            return "It is already running."
        if s.state.paused_reason:
            return "It is paused. Resume it first."
        self._start_batch(s, self._now(), s.state.next_run)
        return ""

    # --------------------------------------------------------------------- one row
    async def _run_one_row(self, now: datetime) -> None:
        if not self._idle() or sch.in_quiet_hours(self.schedules.quiet_hours(), now):
            return                                            # the user is busy, or it is quiet time: due work waits
        # A run the user has just approved goes first: they are waiting for it.
        answered = sorted((s for s in self.schedules.all() if s.state.resume and not s.state.paused_reason),
                          key=lambda s: s.state.resume[0].parked_at)
        for s in answered:
            if await self._try_resume(s, now):
                return
        # A queued batch runs whether or not the schedule is switched on: "Run now" works on a schedule that
        # is off (to try it), and switching one off empties its queue (see agent_ui.save_schedule).
        waiting = sorted((s for s in self.schedules.all() if s.state.queue and not s.state.paused_reason),
                         key=lambda s: s.state.batch_started or "")
        for s in waiting:
            resume = sch.parse_iso(s.state.resume_after)
            if resume is not None and resume > now:
                continue
            if await self._try_row(s, now):
                return                                        # one row per tick: the browser is shared

    async def _try_resume(self, s: Schedule, now: datetime) -> bool:
        """Run again, from the top, a run whose approve step the user said yes to."""
        job = s.state.resume[0]
        pb = await self._load_playbook(s)
        if pb is None:
            return True
        problems = sch.unattended_problems(pb, s.allow_sensitive)
        asked_until = sch.parse_iso(job.expires_at)
        too_old = asked_until is not None and now - asked_until > STALE_ANSWER      # the situation it was about has moved on
        if problems or too_old or pbk.approval_hash(pb) != job.pb_hash:
            why = (problems[0] if problems else
                   "Your answer (or the time that ran out) was too long ago to act on now, so it was not run." if too_old else
                   "The playbook was changed while this waited for you, so what you approved was not run.")
            self._set_state(s.id, lambda x: x.model_copy(update={"resume": [j for j in x.resume if j.run_id != job.run_id]}))
            await self._conclude_parked(s, job, "cancelled", why)
            return True
        typed = dict(self._typed.get(job.run_id, {}))
        if any(i not in typed for i in job.typed):            # the app was restarted: what was typed is gone
            self._set_state(s.id, lambda x: x.model_copy(update={"resume": [j for j in x.resume if j.run_id != job.run_id]}))
            await self._conclude_parked(s, job, "cancelled",
                                        "The app was closed before this ran, and typed answers are never saved, so it was not run. Run it again.")
            return True
        acts = activities(pb, job.values)
        decision = self.limits.check(acts, now)
        if not decision.ok:
            return False                                      # its turn will come; the answer is not lost
        # It leaves the list BEFORE it runs and is marked in flight, so a crash can never run it a second time.
        self._set_state(s.id, lambda x: x.model_copy(update={
            "resume": [j for j in x.resume if j.run_id != job.run_id], "inflight": job.row}))
        try:
            outcome = await self._execute(pb, dict(job.values), s, job.row, approved=frozenset(job.approved),
                                          done=frozenset(job.done), run_id=job.run_id,
                                          answers=typed, skipped=frozenset(job.skipped))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("resumed run crashed")
            outcome = Outcome("failed", run_id=job.run_id, detail=f"{type(e).__name__}: {e}"[:300])
        if outcome.status == "busy":                          # never started: put the answer back
            self._set_state(s.id, lambda x: x.model_copy(update={"resume": [job, *x.resume], "inflight": None}))
            return False
        self._typed.pop(job.run_id, None)                     # parks again below if it needs to, carrying the answers
        return await self._finish_row(s, job.row, outcome, pb, acts, self._now(), values=dict(job.values),
                                      approved=frozenset(job.approved), answers=typed,
                                      skipped=frozenset(job.skipped))

    async def _try_row(self, s: Schedule, now: datetime) -> bool:
        """Run the next row of this schedule. False if it could not run yet (try another schedule)."""
        row = s.state.queue[0]
        pb = await self._load_playbook(s)
        if pb is None:
            return True                                       # the schedule was paused; that used the tick
        problems = sch.unattended_problems(pb, s.allow_sensitive)
        if problems:
            await self._pause(s, "not_approved", problems[0])
            return True
        item = s.items[row] if row < len(s.items) else {}
        values, errors = pbk.bind_values(pb, item)
        if errors:
            return await self._finish_row(s, row, Outcome("failed", detail="Row inputs are not valid: " + "; ".join(errors)), pb, None, now)
        acts = activities(pb, values)
        decision = self.limits.check(acts, now)
        if not decision.ok:
            if decision.wait_until is not None:
                self._set_state(s.id, lambda x: x.model_copy(update={"resume_after": sch.iso(decision.wait_until)}))
            if decision.kind == "cap" and s.state.batch_id not in self._warned_cap:
                self._warned_cap.add(s.state.batch_id)
                await self._say("warning", f'Daily limit reached: "{s.name}"',
                                f"{decision.reason}. The remaining {len(s.state.queue)} will continue tomorrow.",
                                schedule_id=s.id, playbook_id=s.playbook_id)
            return False
        # Mark the row as running BEFORE it starts, so a crash in the middle is never mistaken for "not run yet".
        try:
            self._set_state(s.id, lambda x: x.model_copy(update={"inflight": row}))
        except ScheduleNotFound:
            return True
        try:
            outcome = await self._execute(pb, values, s, row)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("scheduled run crashed")
            outcome = Outcome("failed", detail=f"{type(e).__name__}: {e}"[:300])
        if outcome.status == "busy":
            try:                                              # it never started: the row is simply still waiting
                self._set_state(s.id, lambda x: x.model_copy(update={"inflight": None}))
            except ScheduleNotFound:
                pass
            return False                                      # the browser was taken at that instant: not this row's fault
        return await self._finish_row(s, row, outcome, pb, acts, self._now(), values=values)

    async def _load_playbook(self, s: Schedule) -> Optional[pbk.Playbook]:
        try:
            return self.playbooks.get(s.playbook_id)
        except PlaybookNotFound:
            await self._pause(s, "invalid", "The playbook was deleted.")
        except PlaybookInvalid as e:
            await self._pause(s, "invalid", "The playbook can't be read: " + (e.problems[0] if e.problems else ""))
        except OSError as e:
            await self._pause(s, "invalid", f"The playbook can't be read ({type(e).__name__}).")
        return None

    async def _finish_row(self, s: Schedule, row: int, outcome: Outcome, pb: Optional[pbk.Playbook],
                          acts: Optional[dict], now: datetime, values: Optional[dict] = None,
                          approved: frozenset = frozenset(), answers: Optional[dict] = None,
                          skipped: frozenset = frozenset()) -> bool:
        if acts is not None:
            self.limits.record(acts, now)
        w = outcome.watch or {}
        result = sch.BatchResult(index=row, status=outcome.status, run_id=outcome.run_id, detail=outcome.detail[:300],
                                 watch=str(w.get("status", ""))[:12], change=str(w.get("summary", ""))[:300])
        waiting = outcome.status == "waiting" and outcome.waiting and pb is not None
        ok = outcome.status == "done" or bool(waiting)           # parked is not a failure: it is waiting for a yes
        stop = outcome.status == "stopped" or (not ok and s.on_failure == "stop")
        parked = None
        if waiting:
            w = outcome.waiting
            parked = sch.ParkedRun(
                row=row, run_id=outcome.run_id, step_id=str(w.get("step_id", "")), message=str(w.get("message", ""))[:500],
                detail=str(w.get("detail", ""))[:600], url=str(w.get("url", ""))[:300], parked_at=sch.iso(now) or "",
                expires_at=sch.iso(now + timedelta(minutes=int(w.get("timeout_minutes") or pbk.DEFAULT_APPROVAL_MINUTES))) or "",
                approved=sorted(approved), done=[str(i) for i in w.get("done", [])][:200],
                values=dict(values or {}), pb_hash=pbk.approval_hash(pb),
                kind="input" if w.get("kind") == "input" else "approve", variable=str(w.get("variable", ""))[:60],
                on_timeout="skip" if w.get("on_timeout") == "skip" and w.get("guarded") else "cancel",
                guarded=str(w.get("guarded", ""))[:60], typed=sorted(answers or {}), skipped=sorted(skipped))
            if answers:
                self._typed[outcome.run_id] = dict(answers)

        def apply(x: ScheduleState) -> ScheduleState:
            rest = [] if stop else [i for i in x.queue if i != row]
            results = [r for r in x.batch_results if r.index != row] + [result]     # a resumed row replaces its "waiting"
            park = [p for p in x.parked if p.run_id != outcome.run_id] + ([parked] if parked else [])
            return x.model_copy(update={
                "queue": rest, "inflight": None, "parked": park, "batch_results": results, "last_run_at": sch.iso(now),
                "last_status": outcome.status, "last_detail": outcome.detail[:400], "runs_total": x.runs_total + 1,
                "paused_reason": ("" if ok or outcome.status == "stopped" or s.on_failure == "skip" else outcome.status),
                "paused_detail": ("" if ok or outcome.status == "stopped" or s.on_failure == "skip" else outcome.detail[:400]),
            })

        try:
            updated = self._set_state(s.id, apply)
        except ScheduleNotFound:
            return True                                       # deleted while it ran
        if not ok and outcome.status != "stopped" and s.on_failure == "stop":
            title = "Needs you" if outcome.status == "needs_you" else "Failed"
            await self._say("error", f'{title}: "{s.name}"',
                            f"{outcome.detail or 'It stopped.'} The schedule is paused until you resume it.",
                            schedule_id=s.id, playbook_id=s.playbook_id, run_id=outcome.run_id)
        if parked is not None:
            await self._say("warning", f'Waiting for you: "{s.name}"',
                            f"{parked.message} {parked.detail}".strip() + " Open the inbox or Schedules to answer.",
                            schedule_id=s.id, playbook_id=s.playbook_id, run_id=outcome.run_id,
                            action={"type": "checkpoint", "schedule_id": s.id, "run_id": outcome.run_id, "kind": parked.kind})
        if self._batch_finished(updated):
            await self._batch_over(updated, stopped_by_user=outcome.status == "stopped")
        return True

    @staticmethod
    def _batch_finished(s: Schedule) -> bool:
        return not s.state.queue and not s.state.parked and not s.state.resume

    async def _batch_over(self, s: Schedule, stopped_by_user: bool) -> None:
        results = s.state.batch_results
        bad = [r for r in results if r.status not in ("done", "declined")]      # a "no" is the user's choice, not a problem
        self._warned_cap.discard(s.state.batch_id)
        if stopped_by_user:
            await self._say("info", f'Stopped: "{s.name}"', "You stopped this run, so the rest of it was cancelled.",
                            schedule_id=s.id, playbook_id=s.playbook_id)
        elif bad and s.on_failure == "skip":
            first = bad[0]
            await self._say("warning" if len(bad) < len(results) else "error",
                            f'"{s.name}": {len(bad)} of {len(results)} need you',
                            f"Row {first.index + 1}: {first.detail or first.status}. The others ran.",
                            schedule_id=s.id, playbook_id=s.playbook_id, run_id=first.run_id)
        elif not bad and len(results) > 1 and not s.watch.enabled:       # a watch speaks up only for a change, not to say "all ran"
            declined = sum(1 for r in results if r.status == "declined")
            ran = len(results) - declined
            await self._say("info", f'Finished: "{s.name}"',
                            f"All {ran} rows ran." if not declined else f"{ran} ran and you said no to {declined}.",
                            schedule_id=s.id, playbook_id=s.playbook_id)
        if s.watch.enabled and not stopped_by_user:
            await self._tell_changes(s, results)
        if sch.parse_iso(s.state.next_run) is None and s.cadence.kind == "once":
            self._set_state(s.id, lambda x: x.model_copy(update={"next_run": None}))

    async def _tell_changes(self, s: Schedule, results: list) -> None:
        """One message for a whole batch: what changed (or that watching has started). Unchanged says nothing."""
        changed = sorted((r for r in results if r.watch == "changed"), key=lambda r: r.index)
        n = len(results)
        if changed:
            lines = [f"Row {r.index + 1}: {r.change}" if n > 1 else r.change for r in changed[:5]]
            more = f" And {len(changed) - 5} more." if len(changed) > 5 else ""
            text = (" ".join(lines) + more).strip()[:900]
            title = f'Changed: "{s.name}"' if n == 1 else f'{len(changed)} of {n} changed: "{s.name}"'
            now = sch.iso(self._now()) or ""
            self._set_state(s.id, lambda x: x.model_copy(update={"last_change": text[:400], "last_change_at": now}))
            await self._say("warning", title, text, schedule_id=s.id, playbook_id=s.playbook_id, run_id=changed[0].run_id)
        elif results and all(r.watch == "skipped" and r.status == "done" for r in results):
            # it ran, but nothing could be compared: say so, or the user believes it is being watched (at most once a day)
            last = sch.parse_iso(s.state.watch_warned_at)
            now = self._now()
            if last is None or now - last >= timedelta(hours=24):
                self._set_state(s.id, lambda x: x.model_copy(update={"watch_warned_at": sch.iso(now)}))
                await self._say("warning", f'Not watched this time: "{s.name}"', (results[0].change or "Nothing could be compared.")[:400],
                                schedule_id=s.id, playbook_id=s.playbook_id, run_id=results[0].run_id)
        elif results and all(r.watch in ("baseline", "reset") for r in results):
            reset = any(r.watch == "reset" for r in results)
            await self._say("info", f'Now watching: "{s.name}"',
                            ("The settings changed, so this look starts a new comparison. " if reset else "This first look is saved to compare with. ")
                            + "You will hear from it only when something changes.",
                            schedule_id=s.id, playbook_id=s.playbook_id, run_id=results[0].run_id)

    # ---------------------------------------------------------------- the user's answers
    def pending(self, s: Schedule) -> list[sch.ParkedRun]:
        return list(s.state.parked)

    async def answer(self, sid: str, run_id: str, approve: bool, value: Optional[str] = None) -> str:
        """The user's answer to a parked run. Returns "" if taken, else why not. A no ends that run; a yes puts
        it first in line to run again (it only runs if the playbook is exactly as it was when it was asked).
        A question that asks for a value needs one: a yes without it is not an answer."""
        s = self.schedules.get(sid)
        if s is None:
            return "That schedule no longer exists."
        job = next((p for p in s.state.parked if p.run_id == run_id), None)
        if job is None:
            return "That question was already answered or has run out of time."
        if approve and job.kind == "input":
            if not isinstance(value, str) or not value.strip():
                return "Type the answer first."
            if len(value) > MAX_ANSWER:
                return "That answer is too long."
            self._typed[run_id] = {**self._typed.get(run_id, {}), job.step_id: value.strip()}
            yes = job.model_copy(update={"typed": sorted({*job.typed, job.step_id})})
            self._set_state(sid, lambda x: x.model_copy(update={
                "parked": [p for p in x.parked if p.run_id != run_id], "resume": [*x.resume, yes]}))
            return ""
        if approve:
            yes = job.model_copy(update={"approved": sorted({*job.approved, job.step_id})})
            self._set_state(sid, lambda x: x.model_copy(update={
                "parked": [p for p in x.parked if p.run_id != run_id], "resume": [*x.resume, yes]}))
            return ""
        self._set_state(sid, lambda x: x.model_copy(update={"parked": [p for p in x.parked if p.run_id != run_id]}))
        await self._conclude_parked(s, job, "declined", f"You said no: {job.message}")
        return ""

    async def _expire_parked(self, now: datetime) -> None:
        """No answer in time means NO. Silence is never a yes."""
        for s in self.schedules.all():
            for job in s.state.parked:
                due = sch.parse_iso(job.expires_at)
                if due is not None and due <= now:
                    if job.kind == "approve" and job.on_timeout == "skip" and job.guarded:
                        # The user chose in advance: no answer means leave that step out and carry on with the rest.
                        # The question itself counts as answered (not a yes to the step, which is skipped).
                        go = job.model_copy(update={"approved": sorted({*job.approved, job.step_id}),
                                                    "skipped": sorted({*job.skipped, job.guarded})})
                        self._set_state(s.id, lambda x, rid=job.run_id, go=go: x.model_copy(update={
                            "parked": [p for p in x.parked if p.run_id != rid], "resume": [*x.resume, go]}))
                        await self._say("info", f'Skipped one step: "{s.name}"',
                                        f"Nobody answered in time, so this step was left out and the rest carries on (anything typed before it was left as it was): {job.message}",
                                        schedule_id=s.id, playbook_id=s.playbook_id, run_id=job.run_id)
                        continue
                    self._set_state(s.id, lambda x, rid=job.run_id: x.model_copy(update={"parked": [p for p in x.parked if p.run_id != rid]}))
                    await self._conclude_parked(s, job, "expired", f"Nobody answered in time, so it was cancelled: {job.message}")
                    await self._say("info", f'Cancelled: "{s.name}"', f"Nobody answered in time, so this was not sent: {job.message}",
                                    schedule_id=s.id, playbook_id=s.playbook_id, run_id=job.run_id)

    async def _conclude_parked(self, s: Schedule, job: "sch.ParkedRun", status: str, detail: str) -> None:
        """A parked run ended without running on (declined, expired, or cancelled): record it everywhere."""
        self._typed.pop(job.run_id, None)
        result = sch.BatchResult(index=job.row, status=status, run_id=job.run_id, detail=detail[:300])
        try:
            updated = self._set_state(s.id, lambda x: x.model_copy(update={
                "batch_results": [r for r in x.batch_results if r.index != job.row] + [result],
                "last_status": status, "last_detail": detail[:400]}))
        except ScheduleNotFound:
            return
        if self._resolved is not None:
            try:
                self._resolved(s, job, status, detail)
            except Exception:
                log.exception("could not record the end of a parked run")
        if self._batch_finished(updated):
            await self._batch_over(updated, stopped_by_user=False)

    async def _pause(self, s: Schedule, reason: str, detail: str) -> None:
        try:
            self._set_state(s.id, lambda x: x.model_copy(update={"queue": [], "paused_reason": reason, "paused_detail": detail[:400]}))
        except ScheduleNotFound:
            return
        await self._say("error", f'Paused: "{s.name}"', detail, schedule_id=s.id, playbook_id=s.playbook_id)

    async def _say(self, level: str, title: str, text: str = "", **ids) -> None:
        """Tell the user. A failure to do so (a full disk, a closed window) must never undo or stop bookkeeping."""
        try:
            await self._notify(level, title, text, **ids)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("could not send a notification")

    # -------------------------------------------------------------------- user actions
    def resume(self, sid: str) -> Schedule:
        """Clear a pause and look for the next run from now on."""
        s = self.schedules.get(sid)
        if s is None:
            raise ScheduleNotFound(sid)
        nxt = sch.iso(sch.next_run(s.cadence, self._now()))
        return self._set_state(sid, lambda x: x.model_copy(update={
            "paused_reason": "", "paused_detail": "", "queue": [], "inflight": None, "resume_after": None,
            "next_run": nxt}))

    # -------------------------------------------------------------------- housekeeping
    async def _housekeeping(self, now: datetime) -> None:
        today = now.date().isoformat()
        if self._maintenance is None or self._maintained_on == today:
            return
        self._maintained_on = today
        try:
            await self._maintenance(RETENTION_DAYS)
        except Exception:
            log.exception("scheduler housekeeping failed")

    def _set_state(self, sid: str, fn: Callable[[ScheduleState], ScheduleState]) -> Schedule:
        return self.schedules.change_state(sid, fn)
