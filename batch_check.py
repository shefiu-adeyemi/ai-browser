"""
A dry run of a batch: check the first few input rows and say exactly what each would do, WITHOUT opening a browser.

For each row: are the values acceptable, which sites it would open, what it would type (a stored secret is never
shown), and how many of today's daily allowance the whole batch needs, so the user finds out before it starts that
200 profile visits will take four days.
"""

import math
from datetime import date
from typing import Optional

import playbook as pbk
from site_limits import SiteLimits, activities

SHOW = 3


def _plain(step: pbk.Step, values: dict[str, str]) -> str:
    """One step in words, with this row's values filled in. A secret placeholder stays a placeholder."""
    r = lambda t: pbk.render(t, values) or ""
    k = step.kind
    if k == "open":
        return f"Open {r(step.url)}"
    if k == "search":
        return f"Search {step.engine or ''} for \"{r(step.query)}\""
    if k in ("type", "select"):
        return f"{'Type' if k == 'type' else 'Choose'} \"{r(step.text)[:80]}\""
    if k == "scrape":
        return f"Read a table ({', '.join(f.name for f in step.fields or [])})"
    if k in ("approve", "input"):
        return f"Ask you: {r(step.message)}"
    return step.note or {"click": "Click", "key": "Press keys", "scroll": "Scroll", "wait": "Wait", "back": "Go back",
                         "tab": "Switch tab", "handoff": "Wait for you", "extract": "Read the page", "ai": "AI step"}.get(k, k)


def check_rows(pb: pbk.Playbook, rows: list, limits: Optional[SiteLimits] = None, today: Optional[date] = None,
               show: int = SHOW) -> dict:
    """{"rows": [first `show` rows checked], "total", "bad": how many of ALL rows are not acceptable,
    "needs": {activity: n}, "days": days the batch needs for the busiest daily allowance (None: no limit applies)}."""
    today = today or date.today()
    checked = []
    bad = 0
    needs: dict[str, int] = {}
    for i, raw in enumerate(rows):
        supplied = raw if isinstance(raw, dict) else {}
        values, errors = pbk.bind_values(pb, {k: v for k, v in supplied.items() if isinstance(k, str)})
        if not isinstance(raw, dict):
            errors = ["this row is not a set of inputs"]
        if errors:
            bad += 1
        else:
            for kind, n in activities(pb, values).items():
                needs[kind] = needs.get(kind, 0) + n
        if i < show:
            checked.append({"row": i, "ok": not errors, "problems": errors[:6],
                            "steps": [] if errors else [_plain(s, values) for s in pb.steps][:40]})
    days = None
    if limits is not None and needs:
        spans = []
        for kind, n in needs.items():
            left = max(0, limits.cap_today(kind, today) - limits.used_today(kind, today))
            first = min(n, left)
            rest = n - first
            cap = max(1, limits.allowance(kind).start)         # later days: at least the starting allowance
            spans.append(1 + (math.ceil(rest / cap) if rest > 0 else 0))
        days = max(spans)
    return {"rows": checked, "total": len(rows), "bad": bad, "needs": needs, "days": days}
