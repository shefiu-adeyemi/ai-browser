"""
Typed answers the user chose to KEEP for next time ("Remember this for next runs").

By default an answer typed during a run is used for that run only and is never written to disk. When the
user ticks "remember", it is saved here, on this computer, per playbook and variable, and used for later runs
without asking again until they forget it. Values are plain text in a local file (like the rest of the app's
data), so a code or password should not be remembered; the variable names are all the UI ever shows.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from aiba.storage.chat_store import _atomic_write

log = logging.getLogger("remembered_answers")
MAX_VALUE = 2000


class RememberedAnswers:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("could not read %s (%s); nothing is remembered", self.path.name, e)
            return {}
        if not isinstance(data, dict):
            return {}
        return {pid: {k: v for k, v in vals.items() if isinstance(k, str) and isinstance(v, str)}
                for pid, vals in data.items() if isinstance(pid, str) and isinstance(vals, dict)}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.path, json.dumps(data, indent=2))

    def get(self, playbook_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._read().get(playbook_id, {}))

    def names(self, playbook_id: str) -> list[str]:
        return sorted(self.get(playbook_id))

    def set(self, playbook_id: str, variable: str, value: str) -> bool:
        if not variable or not isinstance(value, str) or not value.strip() or len(value) > MAX_VALUE:
            return False
        with self._lock:
            data = self._read()
            data.setdefault(playbook_id, {})[variable] = value.strip()
            self._write(data)
        return True

    def forget(self, playbook_id: str, variable: Optional[str] = None) -> bool:
        """Forget one variable's answer (or all of this playbook's). True if something was removed."""
        with self._lock:
            data = self._read()
            mine = data.get(playbook_id)
            if not mine or (variable is not None and variable not in mine):
                return False
            if variable is None:
                del data[playbook_id]
            else:
                del mine[variable]
                if not mine:
                    del data[playbook_id]
            self._write(data)
        return True
