"""
Where the app keeps its private data.

API keys, the LinkedIn password and logged-in browser profiles (cookies, saved
logins) used to live inside the project folder, where a zip, a backup or a
`git add .` would carry them away. They now live in a per-user data directory
outside the project (on Windows: %LOCALAPPDATA%\\AIBrowserAgent, which only your
account and administrators can read). Override with the AIBA_DATA_DIR env var.

On first start, anything still in the old project-folder locations is MOVED here
(not copied), so no second copy of a secret is left behind.
"""

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent


def _default_data_dir() -> Path:
    override = os.getenv("AIBA_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.getenv("LOCALAPPDATA") or os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "AIBrowserAgent"


DATA_DIR             = _default_data_dir()
ENV_PATH             = DATA_DIR / ".env"
SESSIONS_DIR         = DATA_DIR / "sessions"
PLAYBOOKS_DIR        = DATA_DIR / "playbooks"
PLAYBOOK_RUNS_DIR    = DATA_DIR / "playbook_runs"
SCHEDULES_FILE       = DATA_DIR / "schedules.json"
SITE_LIMITS_FILE     = DATA_DIR / "site_limits.json"
NOTIFICATIONS_FILE   = DATA_DIR / "notifications.json"
UI_PROFILE_DIR       = DATA_DIR / "profiles" / "ui"          # browser used by the web UI
LINKEDIN_PROFILE_DIR = DATA_DIR / "profiles" / "linkedin"    # browser used by linkedin_agent.py
LAST_URL_FILE        = DATA_DIR / "last_url"

# (old location inside the project, new location)
_LEGACY = [
    (APP_DIR / ".env",             ENV_PATH),
    (APP_DIR / "sessions",         SESSIONS_DIR),
    (APP_DIR / ".browser_profile", UI_PROFILE_DIR),
    (APP_DIR / "browser_profile",  LINKEDIN_PROFILE_DIR),
    (APP_DIR / ".last_url",        LAST_URL_FILE),
    (APP_DIR / "task_history.json", DATA_DIR / "task_history.json"),   # unused leftover, holds task text
]


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _is_empty_dir(p: Path) -> bool:
    return p.is_dir() and not any(p.iterdir())


def _move(src: Path, dst: Path) -> None:
    """Move src to dst so that dst only ever appears COMPLETE. Same drive: an instant rename.
    Different drive: copy to <dst>.partial, rename that into place, then delete the original, so an
    interruption leaves the original intact plus a .partial folder that the next run discards."""
    partial = dst.with_name(dst.name + ".partial")
    if partial.is_dir():
        shutil.rmtree(partial, ignore_errors=True)
    elif partial.exists():
        partial.unlink()
    try:
        os.rename(src, dst)
        return
    except OSError:
        pass                                   # different drive (or locked): fall back to copying
    if src.is_dir():
        shutil.copytree(src, partial)
    else:
        shutil.copy2(src, partial)
    os.replace(partial, dst)
    try:
        shutil.rmtree(src) if src.is_dir() else src.unlink()
    except OSError as e:
        log.warning("Copied %s to %s but could not remove the original (%s); delete it by hand.", src, dst, e)


def migrate_legacy() -> list[str]:
    """Move private data out of the project folder. Never overwrites: if the
    destination already holds data the old copy is left alone and reported. An empty
    destination folder (created at import time by other modules) counts as absent."""
    ensure_data_dir()
    moved: list[str] = []
    for src, dst in _LEGACY:
        if not src.exists():
            continue
        if _is_empty_dir(dst):
            dst.rmdir()
        if dst.exists():
            log.warning("Not moving %s: %s already exists. Remove the old copy by hand.", src, dst)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        _move(src, dst)
        moved.append(f"{src.name} -> {dst}")
        log.info("Moved %s to %s", src, dst)
    return moved
