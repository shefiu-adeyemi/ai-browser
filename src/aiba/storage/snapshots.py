"""
What a watched playbook read the last few times, kept on this computer so the next run has something to compare with.

One file per playbook and per set of inputs (a schedule over 30 pages watches 30 things, each with its own history).
Only the last few snapshots are kept, and anything older than the retention period is removed with the run history.
"""

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

from aiba.storage.chat_store import _atomic_write

log = logging.getLogger("snapshots")
KEEP = 10
_PID = re.compile(r"pb_[0-9a-f]{12}")
_SUBJECT = re.compile(r"[0-9a-f]{16}")


def subject_of(values: dict, scope: str = "") -> str:
    """The same inputs (watched by the same schedule) always give the same name, whatever order they were given in.
    `scope` is the schedule, so two schedules watching the same page with different settings never share a history."""
    body = json.dumps({"scope": str(scope), "values": {str(k): str(v) for k, v in (values or {}).items()}}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


class SnapshotStore:
    def __init__(self, directory: Path, keep: int = KEEP) -> None:
        self.dir = Path(directory)
        self.keep = max(1, keep)
        self._lock = threading.RLock()

    def _path(self, pid: str, subject: str) -> Path:
        if not _PID.fullmatch(pid or "") or not _SUBJECT.fullmatch(subject or ""):
            raise ValueError("invalid snapshot name")
        return self.dir / pid / f"{subject}.json"

    def _read(self, path: Path) -> list[dict]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            log.warning("could not read %s (%s); starting its history again", path.name, e)
            return []
        items = data.get("snapshots") if isinstance(data, dict) else None
        return [x for x in items if isinstance(x, dict) and isinstance(x.get("tables"), list)] if isinstance(items, list) else []

    def history(self, pid: str, subject: str) -> list[dict]:
        """Oldest first."""
        with self._lock:
            return self._read(self._path(pid, subject))

    def latest(self, pid: str, subject: str) -> Optional[dict]:
        h = self.history(pid, subject)
        return h[-1] if h else None

    def add(self, pid: str, subject: str, run_id: str, fingerprint: str, tables: list[dict], now: Optional[float] = None) -> None:
        path = self._path(pid, subject)
        with self._lock:
            items = self._read(path)
            items.append({"at": now if now is not None else time.time(), "run_id": run_id, "fp": fingerprint, "tables": tables})
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, json.dumps({"snapshots": items[-self.keep:]}))

    def touch(self, pid: str, subject: str, now: Optional[float] = None) -> None:
        """Say the latest snapshot was confirmed again just now (so retention does not remove the one we compare with)."""
        path = self._path(pid, subject)
        with self._lock:
            items = self._read(path)
            if not items:
                return
            items[-1]["at"] = now if now is not None else time.time()
            _atomic_write(path, json.dumps({"snapshots": items[-self.keep:]}))

    def delete_playbook(self, pid: str) -> int:
        if not _PID.fullmatch(pid or ""):
            return 0
        folder = self.dir / pid
        n = 0
        with self._lock:
            for f in folder.glob("*.json") if folder.is_dir() else []:
                try:
                    f.unlink(); n += 1
                except OSError:
                    pass
            try:
                folder.rmdir()
            except OSError:
                pass
        return n

    def prune_older_than(self, days: float, now: Optional[float] = None) -> int:
        """Drop snapshots older than this; a file with none left is removed. Returns how many snapshots went."""
        cutoff = (now if now is not None else time.time()) - days * 86400
        gone = 0
        with self._lock:
            for f in self.dir.glob("pb_*/*.json") if self.dir.is_dir() else []:
                items = self._read(f)
                keep = [x for x in items if isinstance(x.get("at"), (int, float)) and x["at"] >= cutoff]
                if len(keep) == len(items):
                    continue
                gone += len(items) - len(keep)
                try:
                    if keep:
                        _atomic_write(f, json.dumps({"snapshots": keep}))
                    else:
                        f.unlink()
                except OSError:
                    log.warning("could not prune %s", f.name)
        return gone
