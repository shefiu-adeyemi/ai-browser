"""
A playbook's results as a spreadsheet (CSV): one row per run, with the inputs it was given and what it read.

Anything that goes into a cell that a spreadsheet could take for a formula (starting with = + - @) is
prefixed with an apostrophe, so a page that returns "=HYPERLINK(...)" can't make the user's spreadsheet
do anything when they open the file.
"""

import csv
import io
from datetime import datetime
from typing import Iterable

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
MAX_CELL = 32000                   # a spreadsheet cell holds about this much


def _safe(value: object) -> str:
    text = "" if value is None else str(value)
    text = text[:MAX_CELL]
    return "'" + text if text.startswith(_FORMULA_START) else text


def runs_to_csv(records: Iterable[dict]) -> str:
    """Oldest run first. Columns: when, status, how it started, one per input, what it read, any error."""
    def when_key(r: dict) -> float:
        v = r.get("started_at")
        return float(v) if isinstance(v, (int, float)) else 0.0
    records = sorted(records, key=when_key)
    input_names: list[str] = []
    for r in records:
        for k in (r.get("inputs") or {}):
            if k not in input_names:
                input_names.append(k)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["Started", "Status", "How", *[_safe(n) for n in input_names], "Result", "Error"])
    for r in records:
        started = r.get("started_at")
        when = datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S") if isinstance(started, (int, float)) and started else ""
        read = " | ".join(str(o.get("text", "")) for o in (r.get("outputs") or []))
        inputs = r.get("inputs") or {}
        writer.writerow([when, _safe(r.get("status")), "scheduled" if r.get("attended") is False else "by hand",
                         *[_safe(inputs.get(n, "")) for n in input_names], _safe(read), _safe(r.get("error"))])
    return out.getvalue()
