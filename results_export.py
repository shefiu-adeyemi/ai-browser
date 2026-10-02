"""
A playbook's results as a spreadsheet (CSV): the inputs each run was given and what it read.

Two shapes. A playbook that only reads text gives one row per run. A playbook that scrapes tables gives one
row per SCRAPED row, with the run's inputs beside it, so a batch of 20 profiles or 10 pages ends up as one
table. When a playbook scrapes several tables, they are stacked in the order the runs happened.

Anything that goes into a cell that a spreadsheet could take for a formula (starting with = + - @) is
prefixed with an apostrophe, so a page that returns "=HYPERLINK(...)" can't make the user's spreadsheet
do anything when they open the file.
"""

import csv
import io
import json
from datetime import datetime
from typing import Iterable

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
MAX_CELL = 32000                   # a spreadsheet cell holds about this much


def _safe(value: object) -> str:
    text = "" if value is None else str(value)
    text = text[:MAX_CELL]
    return "'" + text if text.startswith(_FORMULA_START) else text


def _when(record: dict) -> str:
    started = record.get("started_at")
    return datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S") if isinstance(started, (int, float)) and started else ""


def _tables(record: dict) -> list[dict]:
    """The well-formed tables in a run record (a damaged or hand-edited one is skipped, never a crash)."""
    out = []
    for o in (record.get("outputs") or []):
        t = o.get("table") if isinstance(o, dict) else None
        if isinstance(t, dict) and isinstance(t.get("columns"), list) and isinstance(t.get("rows"), list):
            out.append(t)
    return out


def _sorted(records: Iterable[dict]) -> list[dict]:
    def when_key(r: dict) -> float:
        v = r.get("started_at")
        return float(v) if isinstance(v, (int, float)) else 0.0
    return sorted(records, key=when_key)


def runs_to_csv(records: Iterable[dict]) -> str:
    """Oldest run first. Columns: when, status, how it started, one per input, then either one per scraped
    column (a row per scraped row) or what it read as text, then any error."""
    records = _sorted(records)
    input_names: list[str] = []
    table_names: list[str] = []
    for r in records:
        for k in (r.get("inputs") or {}):
            if k not in input_names:
                input_names.append(k)
        for t in _tables(r):
            for c in t["columns"]:
                if str(c) not in table_names:
                    table_names.append(str(c))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    scraping = bool(table_names)
    middle = table_names if scraping else ["Result"]
    writer.writerow(["Started", "Status", "How", *[_safe(n) for n in input_names], *[_safe(n) for n in middle], "Error"])
    for r in records:
        inputs = r.get("inputs") or {}
        head = [_when(r), _safe(r.get("status")), "scheduled" if r.get("attended") is False else "by hand",
                *[_safe(inputs.get(n, "")) for n in input_names]]
        error = _safe(r.get("error"))
        if not scraping:
            read = " | ".join(str(o.get("text", "")) for o in (r.get("outputs") or []))
            writer.writerow([*head, _safe(read), error])
            continue
        wrote = False
        for t in _tables(r):
            cols = [str(c) for c in t["columns"]]
            for row in t["rows"]:
                if not isinstance(row, list):
                    continue
                cells = dict(zip(cols, row))
                writer.writerow([*head, *[_safe(cells.get(n, "")) for n in table_names], error])
                wrote = True
        if not wrote:                                    # a run that read nothing still shows up, with why
            writer.writerow([*head, *[""] * len(table_names), error])
    return out.getvalue()


def runs_to_json(records: Iterable[dict]) -> str:
    """The same results as JSON, for another program: one object per run with its inputs and tables."""
    runs = []
    for r in _sorted(records):
        runs.append({"started": _when(r), "status": r.get("status") or "", "scheduled": r.get("attended") is False,
                     "inputs": r.get("inputs") or {}, "error": r.get("error") or "",
                     "tables": [{"columns": t["columns"], "rows": t["rows"], "truncated": bool(t.get("truncated"))}
                                for t in _tables(r)],
                     "text": [str(o.get("text", "")) for o in (r.get("outputs") or []) if isinstance(o, dict) and not o.get("table")]})
    return json.dumps({"runs": runs}, ensure_ascii=False, indent=2)


def combined_table(records: Iterable[dict], limit: int = 5000) -> dict:
    """Every scraped row of every run, stacked (oldest first), with the union of the columns. Cells are plain text.
    This is what one playbook hands to another as a list of inputs."""
    records = _sorted(records)
    columns: list[str] = []
    for r in records:
        for t in _tables(r):
            for c in t["columns"]:
                if str(c) not in columns:
                    columns.append(str(c))
    rows: list[list[str]] = []
    for r in records:
        for t in _tables(r):
            cols = [str(c) for c in t["columns"]]
            for row in t["rows"]:
                if isinstance(row, list):
                    cells = dict(zip(cols, row))
                    rows.append([str(cells.get(c, "")) for c in columns])
    return {"columns": columns, "rows": rows[:limit], "total": len(rows)}
