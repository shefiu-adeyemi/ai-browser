"""
Where schedules live: one small JSON file with every schedule and the "pause everything" switch.

Same habits as the other stores: ids are checked, writes go through a temp file and a rename, one lock
covers each read-modify-write, and a file that can't be read is set aside (never overwritten silently)
so the user's schedules aren't lost to a bad edit.

The scheduler changes a schedule's STATE every few seconds while the user may be editing the schedule
itself, so the two are kept apart: `save` (the user) keeps the running state, `change_state` (the
scheduler) never touches the user's settings.
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from aiba.scheduling import schedules as sch
from aiba.storage.chat_store import _atomic_write
from aiba.scheduling.schedules import QuietHours, Schedule, ScheduleState

log = logging.getLogger(__name__)


class TooManySchedules(Exception):
    pass


class ScheduleNotFound(KeyError):
    pass


class ScheduleStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.load_problem = ""                      # why the last read failed ("" = fine)

    # ------------------------------------------------------------------------------ file
    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
            items = []
            for entry in raw.get("schedules", []):
                try:
                    items.append(Schedule.model_validate(entry))
                except ValidationError as e:
                    log.warning("skipping an unreadable schedule: %s", str(e)[:200])
            self.load_problem = ""
            try:
                quiet = QuietHours.model_validate(raw.get("quiet_hours") or {})
            except ValidationError:
                quiet = QuietHours()
            return {"paused_all": bool(raw.get("paused_all", False)), "schedules": items, "quiet": quiet}
        except FileNotFoundError:
            self.load_problem = ""
        except (OSError, ValueError) as e:
            self.load_problem = f"{self.path.name} could not be read ({e})"
            log.warning(self.load_problem)
            self._set_aside()
        return {"paused_all": False, "schedules": [], "quiet": QuietHours()}

    def _set_aside(self) -> None:
        try:
            self.path.replace(self.path.with_name(f"{self.path.name}.bad-{int(time.time())}"))
        except OSError:
            pass

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"paused_all": data["paused_all"], "quiet_hours": data["quiet"].model_dump(mode="json"),
                "schedules": [s.model_dump(mode="json") for s in data["schedules"]]}
        _atomic_write(self.path, json.dumps(body, indent=1, ensure_ascii=False))

    # ----------------------------------------------------------------------------- reads
    def all(self) -> list[Schedule]:
        with self._lock:
            return self._read()["schedules"]

    def get(self, sid: str) -> Optional[Schedule]:
        return next((s for s in self.all() if s.id == sid), None)

    @property
    def paused_all(self) -> bool:
        with self._lock:
            return self._read()["paused_all"]

    def quiet_hours(self) -> QuietHours:
        with self._lock:
            return self._read()["quiet"]

    def set_quiet_hours(self, quiet: QuietHours) -> None:
        with self._lock:
            data = self._read()
            data["quiet"] = quiet
            self._write(data)

    def set_paused_all(self, paused: bool) -> None:
        with self._lock:
            data = self._read()
            data["paused_all"] = bool(paused)
            self._write(data)

    # ---------------------------------------------------------------------------- writes
    def save(self, schedule: Schedule) -> Schedule:
        """Create (no id yet) or update (id given) as the USER. An update keeps the scheduler's state."""
        with self._lock:
            data = self._read()
            now = sch.iso(datetime.now())
            existing = {s.id: s for s in data["schedules"]}
            if schedule.id:
                old = existing.get(schedule.id)
                if old is None:
                    raise ScheduleNotFound(schedule.id)
                keep = schedule.model_copy(update={"created_at": old.created_at, "updated_at": now,
                                                   "state": schedule.state if schedule.state != ScheduleState() else old.state})
                data["schedules"] = [keep if s.id == keep.id else s for s in data["schedules"]]
            else:
                if len(data["schedules"]) >= sch.MAX_SCHEDULES:
                    raise TooManySchedules(f"at most {sch.MAX_SCHEDULES} schedules")
                keep = schedule.model_copy(update={"id": sch.new_id(), "created_at": now, "updated_at": now})
                data["schedules"].append(keep)
            self._write(data)
            return keep

    def delete(self, sid: str) -> bool:
        with self._lock:
            data = self._read()
            kept = [s for s in data["schedules"] if s.id != sid]
            if len(kept) == len(data["schedules"]):
                return False
            data["schedules"] = kept
            self._write(data)
            return True

    def delete_for_playbook(self, playbook_id: str) -> int:
        """A deleted playbook takes its schedules with it (they could never run again)."""
        with self._lock:
            data = self._read()
            kept = [s for s in data["schedules"] if s.playbook_id != playbook_id]
            n = len(data["schedules"]) - len(kept)
            if n:
                data["schedules"] = kept
                self._write(data)
            return n

    def change_state(self, sid: str, fn: Callable[[ScheduleState], ScheduleState]) -> Schedule:
        """Apply `fn` to one schedule's state, as the SCHEDULER. The user's settings are not touched,
        and a schedule that was deleted meanwhile is reported, not recreated."""
        with self._lock:
            data = self._read()
            for i, s in enumerate(data["schedules"]):
                if s.id == sid:
                    data["schedules"][i] = s.model_copy(update={"state": fn(s.state.model_copy(deep=True))})
                    self._write(data)
                    return data["schedules"][i]
            raise ScheduleNotFound(sid)
