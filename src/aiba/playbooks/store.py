"""
On-disk storage for playbooks (one JSON file each, plus a short version history for undo).

Same rules as chat_store.py: ids are checked before they touch a path, every write goes through a
temp file and a rename, and a lock covers each read-modify-write. In addition:

  * a playbook is validated before it is stored, so a bad one never reaches disk;
  * saving always recomputes the sites it may visit, and drops an approval that no longer matches
    the steps, so the stored file can't claim an approval it doesn't have;
  * a save names the version it was based on, so two editors can't silently overwrite each other;
  * the last HISTORY_KEEP versions are kept, so an edit that broke a playbook can be undone.
"""

import json
import logging
import re
import secrets
import threading
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from aiba.playbooks import model as pbk
from aiba.storage.chat_store import _atomic_write
from aiba.playbooks.model import Playbook

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"^pb_[0-9a-f]{12}$")
HISTORY_KEEP = 20


class PlaybookInvalid(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class PlaybookConflict(Exception):
    """Someone saved a newer version since this one was loaded."""


class PlaybookNotFound(KeyError):
    pass


class PlaybookStore:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ----------------------------------------------------------------------- ids
    @staticmethod
    def new_id() -> str:
        return "pb_" + secrets.token_hex(6)

    @staticmethod
    def is_valid_id(pid: object) -> bool:
        return isinstance(pid, str) and bool(_ID_RE.fullmatch(pid))

    def _path(self, pid: str) -> Path:
        if not self.is_valid_id(pid):
            raise ValueError(f"invalid playbook id: {pid!r}")
        return self.dir / f"{pid}.json"

    def _history_dir(self, pid: str) -> Path:
        self._path(pid)                                    # validates the id
        return self.dir / f"{pid}.history"

    @staticmethod
    def _load(path: Path) -> Playbook:
        """Read AND re-check a stored file: a hand-edited one must not slip past the rules that
        a save enforces. Raises PlaybookInvalid if it is damaged or breaks them."""
        text = path.read_text(encoding="utf-8")            # FileNotFoundError propagates
        try:
            pb = Playbook.model_validate_json(text)
        except (ValidationError, ValueError) as e:
            log.warning("damaged playbook file %s: %s", path.name, str(e)[:300])
            raise PlaybookInvalid(["the file is damaged (it is not a valid playbook)"]) from e
        problems = pbk.validate(pb)
        if problems:
            raise PlaybookInvalid([f"the stored playbook is not valid: {p}" for p in problems])
        return pbk.refresh(pb)             # the derived site list is recomputed, never trusted from the file

    # ---------------------------------------------------------------------- reads
    def get(self, pid: str) -> Playbook:
        with self._lock:
            path = self._path(pid)
            try:
                return self._load(path)
            except FileNotFoundError:
                raise PlaybookNotFound(pid) from None

    def summaries(self) -> "list[dict]":
        """One summary per playbook, newest first. A damaged file is skipped (and logged), never fatal."""
        out: list[dict] = []
        with self._lock:
            for path in self.dir.glob("pb_*.json"):
                if not self.is_valid_id(path.stem):
                    continue
                try:
                    pb = self._load(path)
                except (OSError, PlaybookInvalid) as e:
                    log.warning("skipping unreadable playbook %s: %s", path.name, e)
                    continue
                out.append({
                    "id": pb.id, "name": pb.name, "description": pb.description, "version": pb.version,
                    "updated_at": pb.updated_at, "steps": len(pb.steps),
                    "ai_steps": sum(1 for s in pb.steps if s.tag == "ai"),
                    "variables": [v.name for v in pb.variables], "domains": pb.domains,
                    "approved": pbk.is_approved(pb),
                })
        return sorted(out, key=lambda r: r["updated_at"], reverse=True)

    def damaged(self) -> "list[dict]":
        """Files that look like playbooks but can't be read or break the rules, so the user can SEE them
        (and delete them) instead of having them silently vanish from the list."""
        out: list[dict] = []
        with self._lock:
            for path in sorted(self.dir.glob("pb_*.json")):
                if not self.is_valid_id(path.stem):
                    continue
                try:
                    self._load(path)
                except PlaybookInvalid as e:
                    out.append({"id": path.stem, "problem": e.problems[0][:200] if e.problems else "unreadable"})
                except OSError as e:
                    out.append({"id": path.stem, "problem": f"{type(e).__name__}: {e}"[:200]})
        return out

    def versions(self, pid: str) -> list[int]:
        with self._lock:
            hist = self._history_dir(pid)
            nums = sorted((int(p.stem) for p in hist.glob("*.json") if p.stem.isdigit()), reverse=True) \
                if hist.is_dir() else []
            return nums

    # --------------------------------------------------------------------- writes
    def save(self, pb: Playbook, expected_version: Optional[int] = None, approve: bool = False,
             revoke: bool = False) -> Playbook:
        """Create (no id yet) or update (id given) a playbook. Raises PlaybookInvalid with every
        problem, or PlaybookConflict if `expected_version` is not the stored version."""
        with self._lock:
            pb = pbk.refresh(pb)
            problems = pbk.validate(pb)
            if problems:
                raise PlaybookInvalid(problems)

            previous: Optional[Playbook] = None
            if pb.id:
                try:
                    previous = self.get(pb.id)
                except PlaybookNotFound:
                    raise PlaybookNotFound(pb.id) from None          # an update must target a real one
                if expected_version is not None and previous.version != expected_version:
                    raise PlaybookConflict(
                        f"this playbook was changed (now version {previous.version}); reload it and try again")
                pb = pb.model_copy(update={"version": previous.version + 1, "created_at": previous.created_at,
                                           "updated_at": pbk._now()})
            else:
                pb = pb.model_copy(update={"id": self.new_id(), "version": 1,
                                           "created_at": pbk._now(), "updated_at": pbk._now()})
            if approve:                     # a stale or forged approval was already dropped by refresh()
                pb = pbk.with_approval(pb)
            elif revoke:                    # the user took the approval back without changing a step
                pb = pb.model_copy(update={"approval": None})

            if previous is not None:
                self._keep_history(previous)
            _atomic_write(self._path(pb.id), pb.model_dump_json(indent=2))
            return pb

    def restore(self, pid: str, version: int) -> Playbook:
        """Bring back an older version as a NEW version (history is never rewritten). The approval is
        dropped: a restored playbook has to be approved again."""
        with self._lock:
            old_path = self._history_dir(pid) / f"{int(version)}.json"
            try:
                old = self._load(old_path)
            except FileNotFoundError:
                raise PlaybookNotFound(f"{pid} version {version}") from None
            return self.save(old.model_copy(update={"approval": None}))

    def delete(self, pid: str) -> bool:
        with self._lock:
            path = self._path(pid)
            hist = self._history_dir(pid)
            existed = path.exists()
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            if hist.is_dir():
                for f in hist.glob("*.json"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
                try:
                    hist.rmdir()
                except OSError:
                    pass
            return existed

    def _keep_history(self, previous: Playbook) -> None:
        hist = self._history_dir(previous.id)
        hist.mkdir(parents=True, exist_ok=True)
        _atomic_write(hist / f"{previous.version}.json", previous.model_dump_json(indent=2))
        for old in self.versions(previous.id)[HISTORY_KEEP:]:
            try:
                (hist / f"{old}.json").unlink()
            except OSError:
                pass
