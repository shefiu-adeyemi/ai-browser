"""
Schedules: when a playbook runs by itself, with which inputs, and what happens when something goes wrong.

Pure data and rules (no files, no browser, no clock of its own): every function takes the time it should
use, so everything here is unit-tested with made-up times. The rules come from docs/playbooks.md ("Approval
policy"): an unattended run may only do what the user approved in advance, so a schedule can only be
created for an APPROVED playbook, whose inputs all validate, and which has no step that needs a person or
an AI to decide. Whenever the playbook is edited later its approval goes, and the scheduler refuses to
run it (see scheduling/scheduler.py), so a schedule can never outlive what the user agreed to.

Times are the computer's local wall-clock times (naive datetimes): "every day at 09:00" means 09:00 where
the user is. ISO strings are used on disk.
"""

import re
import secrets
from datetime import datetime, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from aiba.playbooks import model as pbk
from aiba.scheduling.monitor import Watch, watch_problems
from aiba.playbooks.model import Playbook

MAX_ITEMS = 200               # input rows in one schedule
MAX_SCHEDULES = 50
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 7 * 24 * 60
_ID_RE = re.compile(r"^sch_[0-9a-f]{12}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def new_id() -> str:
    return "sch_" + secrets.token_hex(6)


def is_valid_id(sid: object) -> bool:
    return isinstance(sid, str) and bool(_ID_RE.fullmatch(sid))


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def parse_iso(text: Optional[str]) -> Optional[datetime]:
    """A naive local datetime from its ISO text, or None if there is none / it can't be read."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# ------------------------------------------------------------------------------- models
class Cadence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["once", "interval", "daily", "weekly"]
    every_minutes: Optional[int] = Field(default=None, ge=MIN_INTERVAL_MINUTES, le=MAX_INTERVAL_MINUTES)
    at: Optional[str] = None                                  # "HH:MM", daily and weekly
    weekdays: list[int] = Field(default_factory=list, max_length=7)    # 0 = Monday, weekly only
    run_at: Optional[str] = None                              # a local date and time, once only


class BatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    status: str                                                # done | failed | needs_you | stopped | skipped
    run_id: str = ""
    detail: str = Field(default="", max_length=300)
    watch: str = ""                                            # a watched run: baseline | unchanged | changed | reset | skipped
    change: str = Field(default="", max_length=300)            # what changed, in words


class ParkedRun(BaseModel):
    """A run that stopped at an approve step because nobody was there, waiting for the user's answer. It holds
    everything needed to run again from the top once they say yes (the browser is free in the meantime)."""
    model_config = ConfigDict(extra="forbid")
    row: int
    run_id: str
    step_id: str                                              # the approve step it is waiting at
    message: str = Field(default="", max_length=500)
    detail: str = Field(default="", max_length=600)           # e.g. exactly what was typed and would be sent
    url: str = Field(default="", max_length=300)
    parked_at: str = ""
    expires_at: str = ""                                      # no answer by then means NO: the run is cancelled
    approved: list[str] = Field(default_factory=list)         # approve steps already answered yes in this run
    done: list[str] = Field(default_factory=list)             # risky steps that already ran: never repeated on resume
    values: dict[str, str] = Field(default_factory=dict)      # the inputs it was started with (the answer is for THESE)
    pb_hash: str = ""                                         # the approved playbook it belongs to (changed = cancelled)
    kind: Literal["approve", "input"] = "approve"             # a yes/no question, or a value to type in
    variable: str = ""                                        # input: the playbook variable the answer fills
    on_timeout: Literal["cancel", "skip"] = "cancel"          # approve: what no answer in time means
    guarded: str = ""                                         # approve: the risky step it guards (left out on "skip")
    typed: list[str] = Field(default_factory=list)            # input steps already answered (the VALUES are kept in memory only)
    skipped: list[str] = Field(default_factory=list)          # risky steps left out because nobody answered


class ScheduleState(BaseModel):
    """What the scheduler remembers between ticks (and across a restart of the app)."""
    model_config = ConfigDict(extra="forbid")
    next_run: Optional[str] = None
    queue: list[int] = Field(default_factory=list)            # rows of this batch still to run
    inflight: Optional[int] = None                            # the row being run RIGHT NOW (cleared when it ends)
    parked: list[ParkedRun] = Field(default_factory=list)     # runs waiting for the user's answer
    resume: list[ParkedRun] = Field(default_factory=list)     # answered yes: run again when it is their turn
    batch_id: str = ""
    batch_started: Optional[str] = None
    batch_results: list[BatchResult] = Field(default_factory=list)
    resume_after: Optional[str] = None                        # waiting (a daily limit was reached)
    paused_reason: str = ""                                   # "" | needs_you | failed | not_approved | invalid
    paused_detail: str = Field(default="", max_length=400)
    last_run_at: Optional[str] = None
    last_status: str = ""
    last_detail: str = Field(default="", max_length=400)
    last_change: str = Field(default="", max_length=400)       # the last change a watch found, in words
    last_change_at: Optional[str] = None
    watch_warned_at: Optional[str] = None                      # when we last said a watch could not compare (once a day at most)
    missed: int = 0
    runs_total: int = 0


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = ""
    name: str = Field(default="", max_length=120)
    playbook_id: str = ""
    enabled: bool = True
    cadence: Cadence = Field(default_factory=lambda: Cadence(kind="daily", at="09:00"))
    items: list[dict[str, str]] = Field(default_factory=lambda: [{}], max_length=MAX_ITEMS)   # one run per row
    on_failure: Literal["stop", "skip"] = "stop"             # when a row needs the user: stop, or skip to the next
    # Sends, posts and other steps that reach other people are OFF for unattended runs unless the user says
    # so here, on top of approving the playbook (docs/playbooks.md: "off by default").
    allow_sensitive: bool = False
    watch: Watch = Field(default_factory=Watch)               # tell me only when what it reads changes
    created_at: str = ""
    updated_at: str = ""
    state: ScheduleState = Field(default_factory=ScheduleState)


class QuietHours(BaseModel):
    """A daily window in which nothing is started and no desktop pop-up appears (the inbox still records)."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    start: str = "22:00"
    end: str = "07:00"


def quiet_problems(q: QuietHours) -> list[str]:
    out = [f"'{v}' is not a time like 22:00" for v in (q.start, q.end) if not _TIME_RE.fullmatch(v or "")]
    if not out and q.start == q.end:
        out.append("the start and the end can't be the same time")
    return out


def in_quiet_hours(q: QuietHours, now: datetime) -> bool:
    """Is `now` inside the window? A window may run past midnight (22:00 to 07:00)."""
    if not q.enabled or quiet_problems(q):
        return False
    here = now.strftime("%H:%M")
    if q.start < q.end:
        return q.start <= here < q.end
    return here >= q.start or here < q.end


# --------------------------------------------------------------------------------- timing
def next_run(c: Cadence, after: datetime) -> Optional[datetime]:
    """The first run time strictly after `after`, or None if there will be no more (a one-off in the past)."""
    after = after.replace(microsecond=0)
    if c.kind == "once":
        at = parse_iso(c.run_at)
        return at if at is not None and at > after else None
    if c.kind == "interval":
        return after + timedelta(minutes=c.every_minutes or MAX_INTERVAL_MINUTES)
    m = _TIME_RE.fullmatch(c.at or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    days = set(c.weekdays) if c.kind == "weekly" else set(range(7))
    if not days:
        return None
    for ahead in range(0, 8):
        day = (after + timedelta(days=ahead)).replace(hour=hour, minute=minute, second=0)
        if day > after and day.weekday() in days:
            return day
    return None


def describe(c: Cadence) -> str:
    if c.kind == "once":
        at = parse_iso(c.run_at)
        return f"Once, {at:%b %d at %H:%M}" if at else "Once"
    if c.kind == "interval":
        n = c.every_minutes or 0
        return f"Every {n // 60} h" if n % 60 == 0 and n >= 60 else f"Every {n} min"
    if c.kind == "daily":
        return f"Every day at {c.at}"
    return f"{', '.join(WEEKDAYS[d] for d in sorted(set(c.weekdays)))} at {c.at}"


# ----------------------------------------------------------------------------- validation
def cadence_problems(c: Cadence, now: datetime) -> list[str]:
    out: list[str] = []
    if c.kind == "interval" and c.every_minutes is None:
        out.append("an interval needs 'every_minutes'")
    if c.kind in ("daily", "weekly") and not _TIME_RE.fullmatch(c.at or ""):
        out.append("a time like 09:30 is needed")
    if c.kind == "weekly":
        if not c.weekdays:
            out.append("choose at least one day of the week")
        if any(d < 0 or d > 6 for d in c.weekdays):
            out.append("days of the week must be 0 (Monday) to 6 (Sunday)")
    if c.kind == "once":
        at = parse_iso(c.run_at)
        if at is None:
            out.append("a date and time is needed")
        elif at <= now:
            out.append("that time has already passed")
    stray = {"every_minutes": c.kind != "interval" and c.every_minutes is not None,
             "at": c.kind not in ("daily", "weekly") and c.at is not None,
             "weekdays": c.kind != "weekly" and bool(c.weekdays),
             "run_at": c.kind != "once" and c.run_at is not None}
    out += [f"'{k}' does not apply to a {c.kind} schedule" for k, bad in stray.items() if bad]
    return out


def unattended_problems(pb: Playbook, allow_sensitive: bool = False) -> list[str]:
    """Why this playbook can't run with nobody there. Empty = it can."""
    out: list[str] = []
    if not pbk.is_approved(pb):
        out.append("Approve the playbook first (open it with Edit and tick the approval). "
                   "A scheduled run only does what you approved.")
    # A send guarded by an approve step (the user is asked every time) needs no blanket permission.
    risky = pbk.unguarded_ask_steps(pb)
    if risky and not allow_sensitive:
        which = ", ".join(str(n) for n in risky[:5])
        out.append(f"Step{'s' if len(risky) > 1 else ''} {which} send{'' if len(risky) > 1 else 's'} something, "
                   "post, or can't be undone. Add an \"ask me first\" step before "
                   f"{'each' if len(risky) > 1 else 'it'}, or tick \"let these steps run while I'm away\" to schedule it.")
    for n, s in enumerate(pb.steps, start=1):
        if s.kind == "handoff":
            out.append(f"Step {n} asks you to do something by hand, so it can't run while you are away.")
        elif s.kind == "ai":
            out.append(f"Step {n} needs an AI to decide what to do, which a scheduled run can't do.")
    return out


def validate_schedule(sch: Schedule, pb: Optional[Playbook], now: datetime) -> list[str]:
    """Every reason this schedule cannot be saved; empty means fine."""
    out: list[str] = []
    if not sch.name.strip():
        out.append("the schedule needs a name")
    if not pbk_id_ok(sch.playbook_id):
        out.append("choose a playbook")
    if not sch.items:
        out.append("add at least one set of inputs (or leave one empty row if the playbook has none)")
    out += [f"when: {p}" for p in cadence_problems(sch.cadence, now)]
    if pb is None:
        out.append("that playbook no longer exists")
        return out
    out += unattended_problems(pb, sch.allow_sensitive)
    out += [f"watch: {p}" for p in watch_problems(sch.watch)]
    if sch.watch.enabled and not any(s.kind == "scrape" for s in pb.steps):
        out.append("watch: watching for changes needs a playbook with a step that reads a table")
    for n, item in enumerate(sch.items, start=1):
        _values, errors = pbk.bind_values(pb, item)
        out += [f"row {n}: {e}" for e in errors]
    return out


def pbk_id_ok(pid: str) -> bool:
    return bool(re.fullmatch(r"pb_[0-9a-f]{12}", pid or ""))


def with_next_run(sch: Schedule, now: datetime) -> Schedule:
    """A copy whose next_run is the first time after `now` (cleared if there is none)."""
    state = sch.state.model_copy(update={"next_run": iso(next_run(sch.cadence, now))})
    return sch.model_copy(update={"state": state})
