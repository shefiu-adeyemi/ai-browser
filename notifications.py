"""
Telling the user what happened while they were away.

  * NotificationStore keeps an inbox (a small JSON file): what stopped, what needs you, what finished,
    newest first, with an unread count. Old entries are dropped by age and by a size cap.
  * DesktopNotifier shows a Windows toast for the important ones. It is best effort (never raises, never
    blocks the scheduler, at most one every few seconds) and the text is handed to PowerShell through the
    environment, never pasted into the script, so a profile name can't inject a command.

Messages must never contain a secret: callers pass plain sentences built from step results and inputs.
"""

import json
import logging
import os
import secrets
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from chat_store import _atomic_write

log = logging.getLogger(__name__)

KEEP = 300
LEVELS = ("info", "warning", "error")
TITLE_MAX, TEXT_MAX = 120, 400
CREATE_NO_WINDOW = 0x08000000
# Windows shows a toast only for an app it knows. PowerShell is always registered, so toasts are sent as
# PowerShell (which needs no change to the user's registry); the title and text say what they are about.
TOAST_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TOAST_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$text = $xml.GetElementsByTagName('text')
$text.Item(0).AppendChild($xml.CreateTextNode($env:AIBA_TOAST_TITLE)) | Out-Null
$text.Item(1).AppendChild($xml.CreateTextNode($env:AIBA_TOAST_TEXT)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:AIBA_TOAST_APP).Show($toast)
"""


class NotificationStore:
    def __init__(self, path: Path, keep: int = KEEP) -> None:
        self.path = Path(path)
        self.keep = keep
        self._lock = threading.RLock()

    # ----------------------------------------------------------------------------- file
    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("could not read %s (%s); starting an empty inbox", self.path.name, e)
        return {"items": [], "settings": {}}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.path, json.dumps(data, indent=1, ensure_ascii=False))

    # ----------------------------------------------------------------------------- inbox
    def add(self, level: str, title: str, text: str = "", *, schedule_id: str = "", playbook_id: str = "",
            run_id: str = "", now: Optional[datetime] = None) -> dict:
        item = {
            "id": "n_" + secrets.token_hex(6), "ts": (now or datetime.now()).isoformat(timespec="seconds"),
            "level": level if level in LEVELS else "info",
            "title": " ".join(str(title).split())[:TITLE_MAX], "text": " ".join(str(text).split())[:TEXT_MAX],
            "schedule_id": schedule_id, "playbook_id": playbook_id, "run_id": run_id, "read": False,
        }
        with self._lock:
            data = self._read()
            data["items"] = ([item] + data["items"])[: self.keep]
            self._write(data)
        return item

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return self._read()["items"][: max(1, limit)]

    def unread_count(self) -> int:
        with self._lock:
            return sum(1 for i in self._read()["items"] if not i.get("read"))

    def mark_read(self, ids: Optional[list[str]] = None) -> int:
        """Mark some (or, with no list, all) as read. Returns how many changed."""
        with self._lock:
            data = self._read()
            changed = 0
            for i in data["items"]:
                if not i.get("read") and (ids is None or i.get("id") in ids):
                    i["read"] = True
                    changed += 1
            if changed:
                self._write(data)
            return changed

    def clear(self) -> int:
        with self._lock:
            data = self._read()
            n = len(data["items"])
            data["items"] = []
            self._write(data)
            return n

    def prune_older_than(self, days: float, now: Optional[datetime] = None) -> int:
        cutoff = (now or datetime.now()) - timedelta(days=days)
        with self._lock:
            data = self._read()
            keep = []
            for i in data["items"]:
                try:
                    if datetime.fromisoformat(i.get("ts", "")) >= cutoff:
                        keep.append(i)
                except ValueError:
                    keep.append(i)                         # no readable date: leave it for the user to clear
            dropped = len(data["items"]) - len(keep)
            if dropped:
                data["items"] = keep
                self._write(data)
            return dropped

    # --------------------------------------------------------------------------- settings
    def desktop_enabled(self) -> bool:
        with self._lock:
            return bool(self._read().get("settings", {}).get("desktop", True))

    def set_desktop(self, enabled: bool) -> None:
        with self._lock:
            data = self._read()
            data.setdefault("settings", {})["desktop"] = bool(enabled)
            self._write(data)


class DesktopNotifier:
    """Windows toasts. `show` is injectable so tests never pop anything up."""

    def __init__(self, enabled: Callable[[], bool] = lambda: True, min_gap: float = 5.0,
                 show: Optional[Callable[[str, str], None]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._enabled, self._min_gap, self._clock = enabled, min_gap, clock
        self._show = show or self._powershell_toast
        self._last = -1e9
        self._lock = threading.Lock()

    def notify(self, title: str, text: str = "") -> bool:
        """True if a toast was started. Never raises and never waits for the toast."""
        try:
            if not self._enabled():
                return False
            with self._lock:
                now = self._clock()
                if now - self._last < self._min_gap:
                    return False
                self._last = now
            threading.Thread(target=self._safe_show, args=(title[:TITLE_MAX], text[:TEXT_MAX]), daemon=True).start()
            return True
        except Exception:
            return False

    def _safe_show(self, title: str, text: str) -> None:
        try:
            self._show(title, text)
        except Exception as e:
            log.debug("desktop notification failed: %s", e)

    @staticmethod
    def _powershell_toast(title: str, text: str) -> None:
        if os.name != "nt":
            return
        env = {**os.environ, "AIBA_TOAST_TITLE": title, "AIBA_TOAST_TEXT": text, "AIBA_TOAST_APP": TOAST_APP_ID}
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_SCRIPT],
                       env=env, capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
