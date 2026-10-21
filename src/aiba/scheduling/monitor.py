"""
Change monitoring: compare what a playbook read this time with what it read last time.

A snapshot is the tables one run read. Rows are matched by KEY (the columns the user chose to identify a row, by
default the first column), so a price that moved is a changed cell on the same row and not "one row gone, another
appeared". Noise is controlled by the user's settings: columns to leave out, patterns to cut out of cells
("3 minutes ago"), and a smallest change worth hearing about for numbers.

Nothing here talks to a model or the network: an unchanged page costs nothing and says nothing.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

MAX_PATTERNS = 5
MAX_PATTERN_LEN = 100
MAX_KEYS = 5
MAX_IGNORED = 20
KEEP_SNAPSHOTS = 10
MAX_DIFF_ITEMS = 50          # what is kept of a diff in the run record (the counts are always exact)

_NESTED_QUANTIFIER = re.compile(r"[+*}]\)?[+*{]|\([^)]*[+*][^)]*\)[+*{]")     # (a+)+ , (a*)* , (.+)* : can take forever


class Watch(BaseModel):
    """What a schedule watches for. Off by default."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    key_columns: list[str] = Field(default_factory=list, max_length=MAX_KEYS)       # empty: the first column of each table
    ignore_columns: list[str] = Field(default_factory=list, max_length=MAX_IGNORED)
    ignore_patterns: list[str] = Field(default_factory=list, max_length=MAX_PATTERNS)   # cut out of every cell before comparing
    min_change_percent: float = Field(default=0.0, ge=0, le=100)                    # numbers that moved less than this are not changes
    report_added: bool = True
    report_removed: bool = True
    report_changed: bool = True


def watch_problems(w: Watch) -> list[str]:
    out: list[str] = []
    for name in (*w.key_columns, *w.ignore_columns):
        if not name.strip() or len(name) > 40:
            out.append(f"'{name[:20]}' is not a column name")
    clash = {k.lower() for k in w.key_columns} & {c.lower() for c in w.ignore_columns}
    if clash:
        out.append(f"'{sorted(clash)[0]}' can't be both a key and left out")
    for pat in w.ignore_patterns:
        if not pat.strip() or len(pat) > MAX_PATTERN_LEN:
            out.append(f"a pattern must be 1 to {MAX_PATTERN_LEN} characters")
            continue
        if _NESTED_QUANTIFIER.search(pat):
            out.append(f"the pattern '{pat[:30]}' repeats a repeat, which can hang: write it more simply")
            continue
        try:
            re.compile(pat)
        except re.error as e:
            out.append(f"the pattern '{pat[:30]}' is not valid ({e})")
    if w.enabled and not (w.report_added or w.report_removed or w.report_changed):
        out.append("choose at least one kind of change to report")
    return out


def fingerprint(w: Watch) -> str:
    """Changes when the settings change in a way that makes an old snapshot not comparable."""
    import hashlib, json
    body = {"k": [k.lower() for k in w.key_columns], "i": sorted(c.lower() for c in w.ignore_columns), "p": w.ignore_patterns,
            "m": w.min_change_percent}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------- snapshots
def tables_of(outputs: list) -> list[dict]:
    """The tables in a run's outputs, as a snapshot keeps them: {"id", "columns", "rows", "truncated"}. `id` is the step's id,
    so the same step is matched to itself next time whatever row of a loop it was."""
    out = []
    for o in outputs or []:
        t = o.get("table") if isinstance(o, dict) else None
        if isinstance(t, dict) and isinstance(t.get("columns"), list) and isinstance(t.get("rows"), list):
            out.append({"id": str(o.get("step_id", "")), "columns": [str(c) for c in t["columns"]],
                        "rows": [[str(c) for c in r] for r in t["rows"] if isinstance(r, list)],
                        "truncated": bool(t.get("truncated"))})
    return out


def _clean(cell: str, patterns: list["re.Pattern"]) -> str:
    text = " ".join(str(cell).split())
    for p in patterns:
        text = p.sub("", text)
    return " ".join(text.split())


_NUM = re.compile(r"[-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)")


def _number(text: str) -> Optional[float]:
    m = _NUM.fullmatch(text.replace("$", "").replace("€", "").replace("£", "").replace("%", "").strip())
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _small(before: str, after: str, percent: float) -> bool:
    """Is this number's move smaller than the user cares about?"""
    if percent <= 0:
        return False
    a, b = _number(before), _number(after)
    if a is None or b is None:
        return False
    biggest = max(abs(a), abs(b))
    return biggest == 0 or abs(a - b) / biggest * 100 < percent


@dataclass
class Item:
    table: str
    key: str
    cells: dict[str, str]


@dataclass
class Change:
    table: str
    key: str
    columns: list[tuple[str, str, str]]          # (column, before, after)


@dataclass
class Diff:
    added: list[Item] = field(default_factory=list)
    removed: list[Item] = field(default_factory=list)
    changed: list[Change] = field(default_factory=list)
    cut: bool = False                             # a list was cut at its limit, so rows past it could not be compared

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def _rows(table: dict, watch: Watch, patterns: list["re.Pattern"]) -> list[tuple[str, dict[str, str]]]:
    """[(key, {column: cleaned cell})], with a repeated key told apart by its position among the repeats."""
    columns = table["columns"]
    lower = [c.lower() for c in columns]
    ignored = {c.lower() for c in watch.ignore_columns}
    keep = [i for i in range(len(columns)) if lower[i] not in ignored]
    # the key is what the user chose, else the first column they did not leave out (a table with nothing left has one key)
    keys = [lower.index(k.lower()) for k in watch.key_columns if k.lower() in lower and lower.index(k.lower()) in keep] or keep[:1]
    out: list[tuple[str, dict[str, str]]] = []
    seen: dict[str, int] = {}
    for r in table["rows"]:
        cells = {columns[i]: _clean(r[i] if i < len(r) else "", patterns) for i in keep}
        key = " / ".join(cells.get(columns[i], "") for i in keys)
        n = seen.get(key, 0)
        seen[key] = n + 1
        out.append((key if n == 0 else f"{key} (#{n + 1})", cells))
    return out


def compare(before: list[dict], after: list[dict], watch: Watch) -> Diff:
    patterns = [re.compile(p) for p in watch.ignore_patterns]
    diff = Diff()
    ids = list(dict.fromkeys([t["id"] for t in before] + [t["id"] for t in after]))
    for tid in ids:
        old = [t for t in before if t["id"] == tid]
        new = [t for t in after if t["id"] == tid]
        old_rows = [x for t in old for x in _rows(t, watch, patterns)]
        new_rows = [x for t in new for x in _rows(t, watch, patterns)]
        diff.cut = diff.cut or any(t.get("truncated") for t in old + new)
        if len(old_rows) == 1 and len(new_rows) == 1:
            # one record both times (a profile, a product page) is the SAME record: what in it moved is the change
            new_rows = [(old_rows[0][0], new_rows[0][1])]
        old_map, new_map = dict(old_rows), dict(new_rows)
        for key, cells in new_rows:
            if key not in old_map:
                diff.added.append(Item(tid, key, cells))
        for key, cells in old_rows:
            if key not in new_map:
                diff.removed.append(Item(tid, key, cells))
        for key, cells in new_rows:
            if key not in old_map:
                continue
            was = old_map[key]
            moved = [(c, was[c], v) for c, v in cells.items() if c in was and was[c] != v and not _small(was[c], v, watch.min_change_percent)]
            if moved:
                diff.changed.append(Change(tid, key, moved))
    if not watch.report_added:
        diff.added = []
    if not watch.report_removed:
        diff.removed = []
    if not watch.report_changed:
        diff.changed = []
    return diff


# ----------------------------------------------------------------------------------------------- reporting
def _short(text: str, n: int = 40) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def summarize(diff: Diff, limit: int = 4) -> str:
    """The change in a few plain lines, for a notification."""
    lines: list[str] = []
    for c in diff.changed:
        lines.append(f'"{_short(c.key)}": ' + ", ".join(f"{col} {_short(b, 24)} → {_short(a, 24)}" for col, b, a in c.columns[:3]))
    lines += [f'New: "{_short(i.key)}"' for i in diff.added]
    lines += [f'Gone: "{_short(i.key)}"' for i in diff.removed]
    total = len(lines)
    text = "; ".join(lines[:limit]) + (f"; and {total - limit} more" if total > limit else "")
    return text + (" (the list was cut at its limit, so rows past it were not compared)" if diff.cut and lines else "")


def counts(diff: Diff) -> dict:
    return {"added": len(diff.added), "removed": len(diff.removed), "changed": len(diff.changed)}


def to_record(diff: Diff) -> dict:
    """What a run record keeps: exact counts, and the first MAX_DIFF_ITEMS of each kind."""
    cap = MAX_DIFF_ITEMS
    return {
        **counts(diff), "cut": diff.cut,
        "added_rows": [{"table": i.table, "key": i.key, "cells": i.cells} for i in diff.added[:cap]],
        "removed_rows": [{"table": i.table, "key": i.key, "cells": i.cells} for i in diff.removed[:cap]],
        "changed_rows": [{"table": c.table, "key": c.key, "columns": [{"column": a, "before": b, "after": d} for a, b, d in c.columns]}
                         for c in diff.changed[:cap]],
    }
