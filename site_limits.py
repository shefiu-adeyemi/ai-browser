"""
Pacing for sites that restrict accounts which act like a robot (LinkedIn, so far).

Published safe ranges for an established account are roughly 150-300 profile views, 15-25 connection
requests and 40-80 messages a day, lower for a new account, with human-looking gaps between actions
(PLAYBOOKS.md, "LinkedIn limits"). So an unattended run is counted against a daily allowance per kind
of activity, the allowance starts low and ramps up week by week from the first day it is used, and two
runs on the same site are always a random while apart.

This module only decides and remembers (a small JSON file); it never runs anything. It uses the time
it is given, so tests use made-up times. Manual runs are counted too (they are the same account), but
only scheduled runs are held back: if you start one yourself, that is your call.
"""

import json
import logging
import random
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import playbook as pbk
from chat_store import _atomic_write
from playbook import Playbook

log = logging.getLogger(__name__)

KEEP_DAYS = 35
SITE_OF = {"linkedin_profile_view": "linkedin", "linkedin_sensitive": "linkedin"}


@dataclass
class Allowance:
    start: int             # runs a day at the beginning
    ceiling: int           # never more than this
    ramp_per_week: int     # added each full week since the first use


DEFAULTS = {
    "linkedin_profile_view": Allowance(start=50, ceiling=150, ramp_per_week=10),
    "linkedin_sensitive": Allowance(start=15, ceiling=25, ramp_per_week=5),   # connects, messages, other sends
}
MIN_GAP = 10
MAX_GAP = 3600
HARD_CEILING = 300


@dataclass
class Decision:
    ok: bool
    wait_until: Optional[datetime] = None
    reason: str = ""
    kind: str = ""                 # "cap" (a daily allowance is used up) | "gap" (the pause between runs) | ""


def activities(pb: Playbook, values: dict[str, str]) -> dict[str, int]:
    """What a run of this playbook counts as, per kind of activity: {"linkedin_profile_view": 1, ...}."""
    out: dict[str, int] = {}
    for s in pb.steps:
        if s.kind == "open" and s.url:
            url = (pbk.render(s.url, values) or "").strip()
            if pbk.LINKEDIN_PROFILE_RE.fullmatch(url):
                out["linkedin_profile_view"] = out.get("linkedin_profile_view", 0) + 1
    on_linkedin = any(pbk.host_allowed(h, ["linkedin.com"]) for h in pbk.compute_domains(pb))
    sensitive = sum(1 for s in pb.steps if s.ask)
    if on_linkedin and sensitive:
        out["linkedin_sensitive"] = sensitive
    return out


class SiteLimits:
    def __init__(self, path: Path, rng: Optional[random.Random] = None) -> None:
        self.path = Path(path)
        self._rng = rng or random.Random()
        self._lock = threading.RLock()
        self._data: dict[str, Any] = self._read()

    # ------------------------------------------------------------------------- storage
    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("could not read %s (%s); starting with fresh counts", self.path.name, e)
        return {}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.path, json.dumps(self._data, indent=2))

    # ------------------------------------------------------------------------- settings
    def allowance(self, key: str) -> Allowance:
        base = DEFAULTS[key]
        mine = (self._data.get("allowances") or {}).get(key) or {}
        try:
            return Allowance(**{**asdict(base), **{k: int(v) for k, v in mine.items() if k in asdict(base)}})
        except (TypeError, ValueError):
            return base

    def gap(self) -> tuple[int, int]:
        """(minimum seconds between two runs on a site, extra random seconds on top)."""
        g = self._data.get("gap") or {}
        try:
            return int(g.get("min", 30)), int(g.get("jitter", 30))
        except (TypeError, ValueError):
            return 30, 30

    def settings(self) -> dict:
        return {"allowances": {k: asdict(self.allowance(k)) for k in DEFAULTS},
                "gap": dict(zip(("min", "jitter"), self.gap()))}

    def configure(self, allowances: Optional[dict] = None, gap: Optional[dict] = None) -> list[str]:
        """Change the limits. Returns the problems found (nothing is changed unless there are none)."""
        problems: list[str] = []
        new_allow: dict[str, dict] = {}
        for key, vals in (allowances or {}).items():
            if key not in DEFAULTS:
                problems.append(f"'{key}' is not something that has a limit")
                continue
            try:
                a = Allowance(**{**asdict(self.allowance(key)), **{k: int(v) for k, v in vals.items()}})
            except (TypeError, ValueError, AttributeError):
                problems.append(f"{key}: the numbers must be whole numbers")
                continue
            if not (1 <= a.start <= a.ceiling <= HARD_CEILING and 0 <= a.ramp_per_week <= 100):
                problems.append(f"{key}: need 1 <= start <= ceiling <= {HARD_CEILING} and a ramp of 0 to 100")
                continue
            new_allow[key] = asdict(a)
        new_gap = None
        if gap is not None:
            try:
                new_gap = {"min": int(gap.get("min", self.gap()[0])), "jitter": int(gap.get("jitter", self.gap()[1]))}
            except (TypeError, ValueError, AttributeError):
                problems.append("gap: the numbers must be whole numbers")
            else:
                if not (MIN_GAP <= new_gap["min"] <= MAX_GAP and 0 <= new_gap["jitter"] <= MAX_GAP):
                    problems.append(f"gap: at least {MIN_GAP} seconds, at most {MAX_GAP}")
        if problems:
            return problems
        with self._lock:
            if new_allow:
                self._data.setdefault("allowances", {}).update(new_allow)
            if new_gap is not None:
                self._data["gap"] = new_gap
            self._write()
        return []

    # ---------------------------------------------------------------------------- counts
    def cap_today(self, key: str, today: date) -> int:
        """Today's allowance: the starting amount plus a ramp for each full week since the first use."""
        a = self.allowance(key)
        first = self._data.get("first_use", {}).get(key)
        try:
            weeks = max(0, (today - date.fromisoformat(first)).days // 7) if first else 0
        except ValueError:
            weeks = 0
        return min(a.ceiling, a.start + a.ramp_per_week * weeks)

    def used_today(self, key: str, today: date) -> int:
        return int((self._data.get("usage", {}).get(today.isoformat(), {})).get(key, 0))

    def usage_summary(self, today: date) -> dict[str, dict]:
        return {k: {"used": self.used_today(k, today), "cap": self.cap_today(k, today)} for k in DEFAULTS}

    def record(self, acts: dict[str, int], now: datetime) -> None:
        """Count a run that happened (manual or scheduled) and start the gap before the next one."""
        if not acts:
            return
        with self._lock:
            day = now.date().isoformat()
            usage = self._data.setdefault("usage", {}).setdefault(day, {})
            first = self._data.setdefault("first_use", {})
            sites = set()
            for key, n in acts.items():
                if key not in DEFAULTS:
                    continue
                usage[key] = int(usage.get(key, 0)) + int(n)
                first.setdefault(key, day)
                sites.add(SITE_OF[key])
            low, jitter = self.gap()
            for site in sites:
                self._data.setdefault("next_ok", {})[site] = (
                    now + timedelta(seconds=low + self._rng.uniform(0, jitter))).isoformat(timespec="seconds")
            cutoff = (now.date() - timedelta(days=KEEP_DAYS)).isoformat()
            self._data["usage"] = {d: v for d, v in self._data["usage"].items() if d >= cutoff}
            self._write()

    # -------------------------------------------------------------------------- deciding
    def check(self, acts: dict[str, int], now: datetime) -> Decision:
        """May a SCHEDULED run that counts as `acts` start now? If not: when to look again, and why."""
        if not acts:
            return Decision(True)
        today = now.date()
        for key, n in acts.items():
            if key not in DEFAULTS:
                continue
            cap = self.cap_today(key, today)
            used = self.used_today(key, today)
            if used + n > cap:
                tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time())
                wait = tomorrow + timedelta(minutes=self._rng.uniform(1, 30))
                return Decision(False, wait.replace(microsecond=0),
                                f"today's limit for {_words(key)} is reached ({used} of {cap})", kind="cap")
        for site in {SITE_OF[k] for k in acts if k in SITE_OF}:
            from_text = self._data.get("next_ok", {}).get(site)
            try:
                until = datetime.fromisoformat(from_text) if from_text else None
            except ValueError:
                until = None
            if until is not None and until > now:
                return Decision(False, until, f"waiting a few seconds between runs on {site}", kind="gap")
        return Decision(True)


def _words(key: str) -> str:
    return {"linkedin_profile_view": "LinkedIn profile views",
            "linkedin_sensitive": "LinkedIn messages and requests"}.get(key, key)
