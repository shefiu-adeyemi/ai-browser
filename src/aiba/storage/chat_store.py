"""
On-disk storage for chat sessions.

Replaces loose helper functions in server/main.py that had these problems:
  * the session id came straight from the URL and was pasted into a file path, so a crafted id
    (``..\\something``) could delete or read files outside the sessions folder;
  * ids were a timestamp chopped to 20 characters, so two chats created in the same fraction of
    a second could collide;
  * the index file was read, changed and rewritten with no lock, and written in place, so two
    saves at once, or a crash mid-write, could drop entries or leave a half-written file;
  * save errors were swallowed, so a full disk silently lost history;
  * the file kept only the LAST 200 turns while the title came from the FIRST turn, so after a
    long chat the title and turn count were wrong.

All of it is plain file I/O with no dependency on the web app, so it is unit-tested directly.
"""

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Old ids look like 20260925_231702_9448, new ones 20260925_231702_a3f9c1. Both match; nothing
# containing a slash, backslash, dot or anything else that could climb out of the folder does.
_SID_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{4,12}$")

MAX_ENTRIES = 2000        # per chat; when exceeded the FIRST entry (the original request) is kept
TITLE_LEN = 60


def _atomic_write(path: Path, text: str) -> None:
    """Write to a temp file then rename over the target, so a crash or a full disk can never
    leave a half-written (corrupt) file behind."""
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class ChatStore:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self._lock = threading.RLock()     # guards every read-modify-write of the index

    # ----------------------------------------------------------------------- ids
    @staticmethod
    def new_id() -> str:
        # The timestamp only has one-second resolution, so uniqueness rests on the random part:
        # 48 bits (not 24, which collided about once per 5,000 ids created in the same second).
        return datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(6)

    @staticmethod
    def is_valid_id(sid: object) -> bool:
        return isinstance(sid, str) and bool(_SID_RE.fullmatch(sid))

    def path(self, sid: str) -> Path:
        if not self.is_valid_id(sid):
            raise ValueError(f"invalid session id: {sid!r}")
        return self.dir / f"{sid}.json"

    # --------------------------------------------------------------------- index
    def index(self) -> list[dict]:
        with self._lock:
            return self._read_index()

    def _read_index(self) -> list[dict]:
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [e for e in data if isinstance(e, dict) and self.is_valid_id(e.get("id"))]
            except (OSError, ValueError):
                pass
            # Unreadable: keep the bad file for inspection instead of silently overwriting it.
            try:
                self.index_path.replace(self.index_path.with_name(f"index.corrupt-{int(time.time())}.json"))
            except OSError:
                pass
            log.warning("chat index was unreadable; rebuilding it from the saved chats")
        return self._rebuild_index()

    def _rebuild_index(self) -> list[dict]:
        """The chat files are the source of truth; the index is a cache that can be recreated."""
        entries = []
        for p in sorted(self.dir.glob("*.json")):
            sid = p.stem
            if not self.is_valid_id(sid):
                continue
            history = self.load(sid)
            first_user = next((t.get("content", "") for t in history if t.get("role") == "user"), "")
            stamp = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            entries.append({"id": sid, "title": (first_user or "New chat")[:TITLE_LEN],
                            "created_at": stamp, "updated_at": stamp, "turns": len(history) // 2})
        if entries:
            self._write_index(entries)
        return entries

    def _write_index(self, entries: list[dict]) -> None:
        _atomic_write(self.index_path, json.dumps(entries, indent=2))

    # ------------------------------------------------------------------ chats
    def has(self, sid: str) -> bool:
        return self.is_valid_id(sid) and any(e["id"] == sid for e in self.index())

    def load(self, sid: str) -> list[dict]:
        if not self.is_valid_id(sid):
            return []
        p = self.dir / f"{sid}.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        # A damaged or hand-edited file must not be able to crash the code that reads chats later.
        return [t for t in data if isinstance(t, dict) and isinstance(t.get("content", ""), str)]

    def save(self, sid: str, history: list[dict], verdict: Optional[str] = None,
             duration: Optional[float] = None) -> None:
        """Persist a chat and its index entry. RAISES on failure: the caller decides how to tell
        the user, rather than history quietly vanishing."""
        path = self.path(sid)
        kept = history if len(history) <= MAX_ENTRIES else history[:1] + history[-(MAX_ENTRIES - 1):]
        with self._lock:
            _atomic_write(path, json.dumps(kept, indent=2))
            entries = self._read_index()
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = next((e for e in entries if e["id"] == sid), None)
            if entry is None:
                first_user = next((t.get("content", "") for t in history if t.get("role") == "user"), "")
                entry = {"id": sid, "created_at": now, "title": (first_user or "New chat")[:TITLE_LEN]}
                entries.append(entry)
            # The title is fixed when the chat is created: it must not change because old turns
            # were trimmed away.
            entry["updated_at"] = now
            entry["turns"] = len(history) // 2
            if verdict is not None:
                entry["last_verdict"] = verdict
            if duration is not None:
                entry["last_duration"] = duration
            self._write_index(entries)

    def rename(self, sid: str, title: str) -> Optional[str]:
        """Give a chat a name of the user's choosing. Returns the stored title, or None if the chat
        doesn't exist or the name is empty."""
        title = " ".join(str(title).split())[:TITLE_LEN]
        if not title or not self.is_valid_id(sid):
            return None
        with self._lock:
            entries = self._read_index()
            entry = next((e for e in entries if e["id"] == sid), None)
            if entry is None:
                return None
            entry["title"] = title
            self._write_index(entries)
            return title

    def delete(self, sid: str) -> bool:
        if not self.is_valid_id(sid):
            return False
        with self._lock:
            try:
                (self.dir / f"{sid}.json").unlink()
            except FileNotFoundError:
                pass
            entries = self._read_index()
            remaining = [e for e in entries if e["id"] != sid]
            self._write_index(remaining)
            return len(remaining) != len(entries)
