"""
Pick a table's rows and columns by CLICKING on the page, instead of typing CSS selectors.

A small overlay is put on the page the browser is showing, only when the user asks: it outlines what is under
the mouse and swallows clicks (so the page does not react) while it works out a stable selector for the click.
  * row mode: the user clicks one item of a repeated list; the answer is every repeated ancestor of the click,
    innermost first, each with how many items it matches, so the user can say "bigger" or "smaller".
  * field mode: the user clicks inside a row; the answer is a selector relative to that row, a sample of the text,
    a guess at the kind (text / number / link), and in how many rows that selector finds something.
The overlay is removed when the user finishes, cancels, or after five minutes, and a page reload removes it too.

Whatever comes back from the page is UNTRUSTED (a page can fake it): everything is cut to size and cleaned here, and
the selectors only ever go into the editor for the user to read and approve like any other.
"""

import json
import re
from typing import Any, Optional

from piercing_js import HELPERS

MAX_SELECTOR = 300
MAX_TEXT = 120
MAX_CANDIDATES = 8
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

_START = r"""(config) => {
  if (window.__aibaPick && window.__aibaPick.stop) window.__aibaPick.stop();
  /*HELPERS*/
  const state = {picks: [], active: true};
  const BAD = /^(active|selected|hover|focus|open|opened|closed|show|hide|hidden|visible|disabled|current|is-.*|has-.*|js-.*)$/i;
  const stable = (c) => c.length <= 30 && /^[A-Za-z_-][A-Za-z0-9_-]*$/.test(c) && !/\d{3,}/.test(c)
    && !/^(css|sc|jsx|emotion|_)-?[A-Za-z0-9]{5,}$/i.test(c) && !BAD.test(c);
  const part = (el) => {
    const cls = Array.from(el.classList || []).filter(stable).slice(0, 2);
    return el.tagName.toLowerCase() + cls.map((c) => '.' + CSS.escape(c)).join('');
  };
  const nth = (el) => {
    const p = el.parentElement;
    if (!p) return part(el);
    const same = Array.from(p.children).filter((c) => c.tagName === el.tagName);
    return same.length > 1 ? part(el) + ':nth-of-type(' + (same.indexOf(el) + 1) + ')' : part(el);
  };
  const rootOf = (el) => el.getRootNode();
  const uniq = (sel, el) => { try { const f = rootOf(el).querySelectorAll(sel); return f.length === 1 && f[0] === el; } catch (e) { return false; } };
  const topOf = (el) => { const d = el.ownerDocument; return d && d.body ? d.body : null; };
  const pathTo = (el) => {                       // a selector for exactly this element, inside ITS shadow root or page
    const parts = [];
    for (let a = el; a && a.nodeType === 1 && a !== a.ownerDocument.documentElement; a = a.parentElement) {
      if (a.id && /^[A-Za-z][\w-]{0,40}$/.test(a.id) && !/\d{4,}/.test(a.id) && uniq('#' + CSS.escape(a.id), a)) { parts.unshift('#' + CSS.escape(a.id)); break; }
      parts.unshift(a === topOf(a) ? 'body' : nth(a));
      if (uniq(parts.join(' > '), el)) break;
    }
    return parts.join(' > ');
  };
  const fullPath = (el) => {                     // a selector for this element from the top page, with >>> at each boundary
    const host = hostOf(rootOf(el));
    const local = pathTo(el);
    return host ? fullPath(host) + ' >>> ' + local : local;
  };
  const prefixOf = (el) => { const host = hostOf(rootOf(el)); return host ? fullPath(host) + ' >>> ' : ''; };
  const count = (sel) => { try { return qsaAll(document, sel).length; } catch (e) { return 0; } };
  const clip = (s, n) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim().slice(0, n);

  const rowCandidates = (el) => {
    const out = [];
    const base = prefixOf(el);
    for (let a = el; a && a.nodeType === 1 && a !== a.ownerDocument.body && a !== a.ownerDocument.documentElement; a = a.parentElement) {
      const p = a.parentElement;
      if (!p) break;
      const sig = part(a);
      const sibs = Array.from(p.children).filter((c) => part(c) === sig);
      if (sibs.length < 2) continue;
      let sel = base + sig;
      if (count(sel) > sibs.length) {            // the same shape appears elsewhere too: anchor it to its parent
        const own = pathTo(p);
        if (own) sel = base + own + ' > ' + sig;
      }
      const n = count(sel);
      if (n >= 2 && !out.some((o) => o.selector === sel)) out.push({selector: sel, count: n, text: clip(a.innerText || a.textContent, 100)});
    }
    return out;
  };

  const fieldPick = (el) => {
    let rows;
    try { rows = qsaAll(document, config.rowSel); } catch (e) { return {error: 'The row selector is not valid.'}; }
    const set = new Set(rows);
    let row = null;
    for (let a = el; a; a = up(a)) { if (set.has(a)) { row = a; break; } }
    if (!row) return {error: 'Click inside one of the rows.'};
    let sel;
    if (el === row) sel = ':scope';
    else {
      const chain = [];
      for (let a = el; a && a !== row; a = up(a)) chain.unshift(a);
      const crosses = chain.some((n) => n.parentElement === null || rootOf(n) !== rootOf(row));
      const join = (nodes, make) => {            // pieces within one root joined by >, roots joined by >>>
        const groups = [];
        for (const n of nodes) {
          const r = rootOf(n);
          if (!groups.length || groups[groups.length - 1].root !== r) groups.push({root: r, parts: []});
          groups[groups.length - 1].parts.push(make(n));
        }
        return groups.map((g) => g.parts.join(' > ')).join(' >>> ');
      };
      const tries = [];
      if (!crosses) for (let k = 1; k <= chain.length; k++) tries.push(chain.slice(chain.length - k));
      tries.push(chain);
      sel = null;
      for (const make of [part, nth]) {
        for (const nodes of tries) {
          const s = join(nodes, make);
          try { if (qs1(row, s) === el) { sel = s; break; } } catch (e) {}
        }
        if (sel) break;
      }
      if (!sel) sel = join(chain, nth);
    }
    const hits = rows.filter((r) => { try { return sel === ':scope' || !!qs1(r, sel); } catch (e) { return false; } }).length;
    const link = el.tagName === 'A' ? el : (el.querySelector && el.querySelector('a')) || (el.closest && el.closest('a'));
    const text = clip(el.innerText !== undefined ? el.innerText : el.textContent, 200);
    const numeric = /^[^\d-+.]{0,3}[-+]?[\d,]*\.?\d+\s?[%A-Za-z$€£]{0,3}$/.test(text);
    const suggest = el.tagName === 'A' && (!text || /^https?:/.test(text)) ? 'link' : numeric ? 'number' : 'text';
    return {selector: sel, sample: text, href: link && typeof link.href === 'string' ? link.href : '', suggest, hits, rows: rows.length};
  };

  const box = document.createElement('div');
  box.setAttribute('data-aiba-picker', '1');
  box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #2563eb;background:rgba(37,99,235,.18);display:none;box-sizing:border-box;border-radius:3px';
  document.documentElement.appendChild(box);
  const target = (e) => { const p = e.composedPath ? e.composedPath() : []; const t = p.length && p[0].nodeType === 1 ? p[0] : e.target; return t && t !== box && t.nodeType === 1 ? t : null; };
  const rectOf = (el) => {                       // where it is on the top page, even from inside a frame
    const r = el.getBoundingClientRect();
    let left = r.left, top = r.top;
    for (let w = el.ownerDocument.defaultView; w && w !== window; ) {
      let f = null;
      try { f = w.frameElement; } catch (e) {}
      if (!f) break;
      const fr = f.getBoundingClientRect();
      left += fr.left; top += fr.top;
      w = f.ownerDocument.defaultView;
    }
    return {left, top, width: r.width, height: r.height};
  };
  const show = (el) => {
    let t = el;
    if (config.mode === 'row') {
      const c = rowCandidates(el);
      if (c.length) { try { const hit = qsaAll(document, c[c.length - 1].selector).find((x) => x === el || x.contains(el)); if (hit) t = hit; } catch (e) {} }
    }
    const r = rectOf(t);
    box.style.display = 'block';
    box.style.left = r.left + 'px'; box.style.top = r.top + 'px'; box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
  };
  const onMove = (e) => { const el = target(e); if (el) show(el); };
  const swallow = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); };
  const onClick = (e) => {
    swallow(e);
    const el = target(e);
    if (!el) return;
    if (config.mode === 'row') {
      const candidates = rowCandidates(el);
      state.picks.push(candidates.length ? {candidates} : {error: 'Click an item that appears several times, like one row of a list.'});
    } else state.picks.push(fieldPick(el));
  };
  const events = ['mousedown', 'mouseup', 'pointerdown', 'pointerup', 'dblclick', 'contextmenu', 'submit', 'auxclick'];
  const docs = [];
  const collect = (d, depth) => {                // this page and the pages of the iframes from the same site, as deep as 4
    docs.push(d);
    if (depth >= 4) return;
    let frames = [];
    try { frames = Array.from(d.querySelectorAll('iframe,frame')); } catch (e) {}
    for (const f of frames) { let cd = null; try { cd = f.contentDocument; } catch (e) {} if (cd && !docs.includes(cd)) collect(cd, depth + 1); }
  };
  collect(document, 0);
  for (const d of docs) {
    d.addEventListener('mousemove', onMove, true);
    d.addEventListener('click', onClick, true);
    events.forEach((n) => d.addEventListener(n, swallow, true));
  }
  state.stop = () => {
    for (const d of docs) {
      try {
        d.removeEventListener('mousemove', onMove, true);
        d.removeEventListener('click', onClick, true);
        events.forEach((n) => d.removeEventListener(n, swallow, true));
      } catch (e) {}
    }
    box.remove();
    state.active = false;
  };
  window.__aibaPick = state;
  setTimeout(() => { if (state.active) state.stop(); }, 300000);
  return 'started';
}"""

_START = _START.replace("/*HELPERS*/", HELPERS)


_POLL = r"""() => {
  const s = window.__aibaPick;
  if (!s) return JSON.stringify({active: false, picks: []});
  return JSON.stringify({active: !!s.active, picks: s.picks.splice(0)});
}"""

_STOP = r"""() => { const s = window.__aibaPick; if (s && s.stop) s.stop(); window.__aibaPick = undefined; return 'stopped'; }"""


class PickError(Exception):
    """The picker could not be started or read."""


def _text(value: Any, n: int) -> str:
    return _CONTROL.sub(" ", str(value if value is not None else ""))[:n].strip()


def _selector(value: Any) -> str:
    s = value if isinstance(value, str) else ""
    return "" if not s.strip() or _CONTROL.search(s) else s.strip()[:MAX_SELECTOR]


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**6 else 0


def clean_pick(raw: Any) -> Optional[dict]:
    """One pick from the page, made safe. None if it is not a pick at all."""
    if not isinstance(raw, dict):
        return None
    if raw.get("error"):
        return {"error": _text(raw["error"], 200)}
    if isinstance(raw.get("candidates"), list):
        out = []
        for c in raw["candidates"][:MAX_CANDIDATES]:
            if isinstance(c, dict) and _selector(c.get("selector")) and _int(c.get("count")) >= 2:
                out.append({"selector": _selector(c["selector"]), "count": _int(c["count"]), "text": _text(c.get("text"), MAX_TEXT)})
        return {"candidates": out} if out else {"error": "Click an item that appears several times, like one row of a list."}
    sel = _selector(raw.get("selector"))
    if not sel:
        return None
    href = raw.get("href") if isinstance(raw.get("href"), str) and raw["href"].startswith(("http://", "https://")) else ""
    suggest = raw.get("suggest") if raw.get("suggest") in ("text", "number", "link") else "text"
    return {"selector": sel, "sample": _text(raw.get("sample"), MAX_TEXT), "href": _text(href, 200), "suggest": suggest,
            "hits": _int(raw.get("hits")), "rows": _int(raw.get("rows"))}


async def start(page: Any, mode: str, row_selector: Optional[str] = None) -> None:
    if mode not in ("row", "field"):
        raise PickError("Pick either a row or a column.")
    if mode == "field" and not _selector(row_selector):
        raise PickError("Pick the rows first.")
    try:
        await page.evaluate(_START, {"mode": mode, "rowSel": _selector(row_selector)})
    except Exception as e:
        raise PickError(f"Couldn't start picking on this page ({type(e).__name__}: {str(e)[:150]}).") from e


async def poll(page: Any) -> dict:
    """{"active": bool, "picks": [clean picks]}. A page that was reloaded or closed simply is not active."""
    try:
        raw = await page.evaluate(_POLL)
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {"active": False, "picks": []}
    if not isinstance(data, dict):
        return {"active": False, "picks": []}
    picks = [p for p in (clean_pick(x) for x in (data.get("picks") if isinstance(data.get("picks"), list) else [])[:5]) if p]
    return {"active": data.get("active") is True, "picks": picks}


async def stop(page: Any) -> None:
    try:
        await page.evaluate(_STOP)
    except Exception:
        pass                                   # the page is gone: there is nothing left to remove
