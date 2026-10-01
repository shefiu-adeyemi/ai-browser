"""
The record of each playbook run: what was asked, what happened step by step, what it read.

One JSON file per run under <runs dir>/<playbook id>/<run id>.json. This is what a failure report,
a cost/success badge and a change monitor are built from later, so it is kept complete from the
start. Rules shared with the other stores: ids are checked before they touch a path, writes go
through a temp file and a rename, and a lock covers each read-modify-write.

Input values are stored (they are not secrets: a value containing a secret placeholder is refused
before a run starts, see playbook.bind_values). Old runs are pruned: at most KEEP_PER_PLAYBOOK per
playbook, and (when asked) nothing older than a retention period.
"""

import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from chat_store import _atomic_write
from playbook_store import PlaybookStore

log = logging.getLogger(__name__)

_RUN_ID_RE = re.compile(r"^run_[0-9a-f]{12}$")
KEEP_PER_PLAYBOOK = 50


class RunStore:
    def __init__(self, directory: Path, keep: int = KEEP_PER_PLAYBOOK) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self._lock = threading.RLock()

    @staticmethod
    def new_id() -> str:
        return "run_" + secrets.token_hex(6)

    @staticmethod
    def is_valid_id(rid: object) -> bool:
        return isinstance(rid, str) and bool(_RUN_ID_RE.fullmatch(rid))

    def _folder(self, pid: str) -> Path:
        if not PlaybookStore.is_valid_id(pid):
            raise ValueError(f"invalid playbook id: {pid!r}")
        return self.dir / pid

    def _path(self, pid: str, rid: str) -> Path:
        if not self.is_valid_id(rid):
            raise ValueError(f"invalid run id: {rid!r}")
        return self._folder(pid) / f"{rid}.json"

    def save(self, pid: str, record: dict) -> dict:
        """Store a run. The record needs a `run_id` (use new_id())."""
        with self._lock:
            rid = record.get("run_id")
            path = self._path(pid, rid)                 # validates both ids
            self._folder(pid).mkdir(parents=True, exist_ok=True)
            record = {**record, "playbook_id": pid}
            _atomic_write(path, json.dumps(record, indent=2, ensure_ascii=False, default=str))
            self._prune(pid)
            return record

    def get(self, pid: str, rid: str) -> Optional[dict]:
        with self._lock:
            path = self._path(pid, rid)                  # a bad id is an error, not "unreadable"
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, ValueError) as e:
                log.warning("unreadable run %s/%s: %s", pid, rid, e)
                return None

    def recent(self, pid: str, limit: int = 20) -> list[dict]:
        """Newest first; each row is a short summary, not the whole record."""
        rows = []
        with self._lock:
            folder = self._folder(pid)
            if not folder.is_dir():
                return []
            for path in folder.glob("run_*.json"):
                rec = self.get(pid, path.stem) if self.is_valid_id(path.stem) else None
                if rec is None:
                    continue
                rows.append({k: rec.get(k) for k in ("run_id", "status", "started_at", "finished_at", "seconds",
                                                    "attended", "error", "playbook_version")}
                            | {"steps": len(rec.get("steps", [])), "ok_steps": sum(
                                1 for s in rec.get("steps", []) if s.get("status") == "ok")})
        rows.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
        return rows[:limit]

    def delete_playbook_runs(self, pid: str) -> int:
        with self._lock:
            folder = self._folder(pid)
            n = 0
            if folder.is_dir():
                for f in folder.glob("run_*.json"):
                    try:
                        f.unlink()
                        n += 1
                    except OSError:
                        pass
                try:
                    folder.rmdir()
                except OSError:
                    pass
            return n

    def prune_older_than(self, days: float, now: Optional[float] = None) -> int:
        """Delete runs older than `days` (the retention setting) across every playbook."""
        cutoff = (now if now is not None else time.time()) - days * 86400
        n = 0
        with self._lock:
            for folder in self.dir.iterdir():
                if not folder.is_dir() or not PlaybookStore.is_valid_id(folder.name):
                    continue
                for f in folder.glob("run_*.json"):
                    try:
                        if f.stat().st_mtime < cutoff:
                            f.unlink()
                            n += 1
                    except OSError:
                        pass
        return n

    def _prune(self, pid: str) -> None:
        files = sorted(self._folder(pid).glob("run_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[self.keep:]:
            try:
                old.unlink()
            except OSError:
                pass
