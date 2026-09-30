"""
Housekeeping for the agent's long-lived browser.

The browser now stays up for the whole life of the server (keep_alive), so two things that
used to be hidden by "kill it after every task" have to be handled explicitly:
  * a previous server that was force-killed leaves its Chromium behind, still holding the
    profile lock  ->  kill_stale_browsers()
  * tabs the agent opens pile up across tasks  ->  trim_tabs()

Both only ever touch the agent's own browser. kill_stale_browsers matches on the exact
--user-data-dir of OUR profile, so your own Chrome is never affected.
"""

import logging
import os
from pathlib import Path
from typing import Any

import psutil

log = logging.getLogger(__name__)

_BROWSER_NAMES = {"chrome.exe", "chromium.exe", "msedge.exe", "chrome", "chromium", "google-chrome"}
MAX_TABS = 8


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.strip().strip('"')))


def kill_stale_browsers(profile_dir: Path) -> int:
    """Kill browser processes that were started with exactly this profile directory."""
    target = _norm(str(profile_dir))
    victims = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (proc.info["name"] or "").lower() not in _BROWSER_NAMES:
                continue
            for arg in proc.info["cmdline"] or []:
                if arg.startswith("--user-data-dir=") and _norm(arg.split("=", 1)[1]) == target:
                    victims.append(proc)
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for proc in victims:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(victims, timeout=5)
    if victims:
        log.warning("Closed %d leftover browser process(es) from a previous run", len(victims))
    return len(victims)


async def trim_tabs(session: Any, keep: int = MAX_TABS) -> int:
    """Close the oldest tabs beyond `keep`, never the one the agent is focused on."""
    try:
        targets = list(session.session_manager.get_all_page_targets())
        focused = session.agent_focus_target_id
        extras = [t for t in targets if t.target_id != focused][: max(0, len(targets) - keep)]
        for t in extras:
            await session.cdp_client.send.Target.closeTarget(params={"targetId": t.target_id})
        return len(extras)
    except Exception as e:                       # housekeeping must never break a run
        log.debug("could not trim tabs: %s", e)
        return 0
