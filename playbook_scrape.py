"""
Reading a table off a page with no AI: a row selector picks each repeated item, and every field is read inside it.

The page only ever READS here: the script below calls querySelectorAll / querySelector and reads text, a link
or an attribute. Selectors and field settings travel as JSON arguments, never pasted into the script.
What comes back is plain text: cells are cleaned, capped, and a number or link that is not one is left empty.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import urlparse

from piercing_js import HELPERS
from playbook import DEFAULT_SCRAPE_ROWS, MAX_SCRAPE_ROWS

MAX_CELL = 500                  # characters kept per cell
MAX_CELLS = 20000               # rows x columns kept per table; more is cut and the table says so

_READ_PAGE = r"""(rowSel, fields, maxRows) => {
  /*HELPERS*/
  const clean = (s) => (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim();
  const fail = (e) => JSON.stringify({error: String((e && e.message) || e)});
  let roots;
  try { roots = rowSel ? qsaAll(document, rowSel) : [document.documentElement]; }
  catch (e) { return fail(e); }
  const rows = [];
  for (const root of roots.slice(0, maxRows)) {
    const row = [];
    for (const f of fields) {
      let el = null;
      try { el = f.selector.trim() === ':scope' ? root : qs1(root, f.selector); }
      catch (e) { return fail(e); }
      if (!el) { row.push(null); continue; }
      if (f.type === 'link') {
        const a = el.tagName === 'A' ? el : (el.querySelector('a') || el.closest('a'));
        row.push(a && typeof a.href === 'string' ? a.href : null);
      } else if (f.type === 'attr') {
        row.push(el.getAttribute(f.attr));
      } else {
        row.push(clean(el.innerText !== undefined ? el.innerText : el.textContent));
      }
    }
    rows.push(row);
  }
  return JSON.stringify({total: roots.length, rows: rows});
}"""


_READ_PAGE = _READ_PAGE.replace("/*HELPERS*/", HELPERS)


class ScrapeError(Exception):
    """The page could not be read (a bad selector, a page that is gone)."""


async def read_page(page: Any, row_selector: Optional[str], fields: list[dict], max_rows: Optional[int]) -> dict:
    """Ask the page for the raw cells. Returns {"total": rows on the page, "rows": [[cell or None, ...], ...]}."""
    limit = max(1, min(int(max_rows or DEFAULT_SCRAPE_ROWS), MAX_SCRAPE_ROWS))
    args = [{"selector": f["selector"], "type": f["type"], "attr": f.get("attr")} for f in fields]
    try:
        raw = await page.evaluate(_READ_PAGE, row_selector or None, args, limit)
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        raise ScrapeError(f"Couldn't read the page ({type(e).__name__}: {str(e)[:200]}).") from e
    if not isinstance(data, dict):
        raise ScrapeError("The page gave back something unexpected.")
    if data.get("error"):
        raise ScrapeError(f"A selector is not valid: {str(data['error'])[:200]}")
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise ScrapeError("The page gave back something unexpected.")
    total = data.get("total")
    return {"total": total if isinstance(total, int) else len(rows), "rows": rows}


_NUMBER = re.compile(r"[-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)")


def _number(text: str) -> str:
    """The first number in the text ("$1,299.50" -> "1299.5"), or '' if there isn't one."""
    m = _NUMBER.search(text or "")
    if not m:
        return ""
    try:
        d = Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return ""
    return format(d.normalize(), "f")


def _cell(value: Any, ftype: str) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if ftype == "number":
        return _number(text)
    if ftype == "link":
        return text[:MAX_CELL] if urlparse(text).scheme in ("http", "https") else ""
    return text[:MAX_CELL]


def build_table(fields: list[dict], raw: dict, row_mode: bool) -> tuple[Optional[dict], str]:
    """(table, "") or (None, why it can't be used). A selector that matched nothing at all (no rows, or a field
    empty in every row) means the page changed or has not loaded: that is a failure, never an empty success."""
    rows = [r for r in raw.get("rows", []) if isinstance(r, list)]
    names = [f["name"] for f in fields]
    if row_mode and not rows:
        return None, "No rows were found on the page. It may not have finished loading, or the page has changed."
    if not row_mode and not rows:
        return None, "Nothing was found on the page."
    for i, f in enumerate(fields):
        if all(i >= len(r) or r[i] is None for r in rows):
            where = "in any row" if row_mode else "on the page"
            return None, f'Nothing matched "{f["selector"]}" {where}, so "{f["name"]}" could not be read. The page may have changed.'
    table_rows = [[_cell(r[i] if i < len(r) else None, f["type"]) for i, f in enumerate(fields)] for r in rows]
    truncated = int(raw.get("total", len(rows))) > len(table_rows)
    keep = max(1, MAX_CELLS // max(1, len(names)))
    if len(table_rows) > keep:
        table_rows, truncated = table_rows[:keep], True
    return {"columns": names, "rows": table_rows, "truncated": truncated, "total": int(raw.get("total", len(rows)))}, ""
