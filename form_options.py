"""
Checkboxes and radio buttons the page listing can't show the model.

Many sites hide the real <input> (opacity 0, pointer-events none) and draw their own box next to a
<label for=...>. browser-use leaves both out of its numbered list on purpose (an invisible input is not
clickable by a person, a `label for` would double-activate), so the model could not tick them, and could
not tell whether a box it did reach was ticked.

list_options() and set_option() work from what a person sees: the visible label text and whether the
box is on. set_option clicks the label like a person would, then reads the box again to check it changed.
"""

import json
from typing import Any

MAX_OPTIONS = 80
MAX_LABEL = 120

# Walks the page, open shadow roots and same-site iframes. Defines `collect()` -> [{el, type, group, label, checked, disabled}].
_COLLECT = r"""
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const roots = [];
  const walk = (root) => {
    roots.push(root);
    for (const el of Array.from(root.querySelectorAll('*'))) {
      try {
        if (el.shadowRoot) walk(el.shadowRoot);
        if (el.tagName === 'IFRAME' && el.contentDocument) walk(el.contentDocument);
      } catch (e) {}
    }
  };
  const nameOf = (el) => {
    let t = '';
    try { if (el.labels && el.labels.length) t = Array.from(el.labels).map((l) => l.innerText || l.textContent).join(' '); } catch (e) {}
    if (!norm(t)) {
      const by = el.getAttribute('aria-labelledby');
      if (by) t = by.split(/\s+/).map((id) => { const n = el.getRootNode().getElementById && el.getRootNode().getElementById(id); return n ? n.textContent : ''; }).join(' ');
    }
    if (!norm(t)) t = el.getAttribute('aria-label') || '';
    if (!norm(t) && el.tagName !== 'INPUT') t = el.innerText || el.textContent || '';
    if (!norm(t)) { const p = el.closest('label'); if (p) t = p.innerText || p.textContent; }
    if (!norm(t)) { const s = el.nextElementSibling; if (s) t = s.innerText || s.textContent; }
    return norm(t).slice(0, %MAXLABEL%);
  };
  const collect = () => {
    walk(document);
    const out = [];
    const seen = new Set();
    for (const root of roots) {
      for (const el of Array.from(root.querySelectorAll('input[type=checkbox], input[type=radio], [role=checkbox], [role=radio], [role=switch]'))) {
        if (seen.has(el)) continue;
        seen.add(el);
        const native = el.tagName === 'INPUT';
        const checked = native ? !!el.checked : el.getAttribute('aria-checked') === 'true';
        const disabled = native ? !!el.disabled : el.getAttribute('aria-disabled') === 'true';
        const type = native ? el.type : (el.getAttribute('role') === 'switch' ? 'checkbox' : el.getAttribute('role'));
        out.push({el: el, type: type, group: native ? (el.name || '') : '', label: nameOf(el), checked: checked, disabled: disabled});
      }
    }
    return out;
  };
  const stateOf = (el) => (el.tagName === 'INPUT' ? !!el.checked : el.getAttribute('aria-checked') === 'true');
""".replace("%MAXLABEL%", str(MAX_LABEL))

_LIST = "() => {" + _COLLECT + r"""
  const items = collect().slice(0, %MAX%).map((o) => ({type: o.type, group: o.group, label: o.label, checked: o.checked, disabled: o.disabled}));
  return JSON.stringify(items);
}""".replace("%MAX%", str(MAX_OPTIONS))

_SET = "(label, want) => {" + _COLLECT + r"""
  const target = norm(label).toLowerCase();
  const all = collect();
  let hits = all.filter((o) => o.label.toLowerCase() === target);
  if (!hits.length) hits = all.filter((o) => target && o.label.toLowerCase().includes(target));
  if (!hits.length) return JSON.stringify({error: 'none', labels: all.slice(0, 40).map((o) => o.label)});
  if (hits.length > 1) return JSON.stringify({error: 'many', labels: hits.slice(0, 10).map((o) => o.type + ': ' + o.label)});
  const o = hits[0];
  if (o.disabled) return JSON.stringify({error: 'disabled', label: o.label});
  if (o.type === 'radio' && !want) return JSON.stringify({error: 'radio', label: o.label});
  if (o.checked === want) return JSON.stringify({ok: true, changed: false, label: o.label, type: o.type, checked: o.checked});
  try { o.el.scrollIntoView({block: 'center'}); } catch (e) {}
  let lab = null;
  try { lab = o.el.labels && o.el.labels[0]; } catch (e) {}
  (lab || o.el).click();
  if (stateOf(o.el) !== want && lab) o.el.click();
  const now = stateOf(o.el);
  return JSON.stringify({ok: now === want, changed: now !== o.checked, label: o.label, type: o.type, checked: now});
}"""


def describe(items: list[dict]) -> str:
    """The options as text for the model: one per line, with whether each is on."""
    if not items:
        return "No checkboxes or radio buttons found on this page."
    lines = []
    for o in items:
        mark = "[x]" if o.get("checked") else "[ ]"
        group = f' (group "{o["group"]}")' if o.get("group") and o.get("type") == "radio" else ""
        off = " (disabled)" if o.get("disabled") else ""
        lines.append(f'{mark} {o.get("type")}: {o.get("label") or "(no label)"}{group}{off}')
    return "\n".join(lines)


async def list_options(page: Any) -> list[dict]:
    raw = await page.evaluate(_LIST)
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data if isinstance(data, list) else []


async def set_option(page: Any, label: str, checked: bool = True) -> tuple[bool, str]:
    """Tick or untick the option with this label. Returns (worked, message for the model)."""
    raw = await page.evaluate(_SET, label, bool(checked))
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        return False, "Could not read the page's options."
    err = data.get("error")
    if err == "none":
        have = "; ".join(data.get("labels") or []) or "none"
        return False, f'No checkbox or radio button is labelled "{label}". Options on the page: {have}.'
    if err == "many":
        return False, f'More than one option matches "{label}": {"; ".join(data.get("labels") or [])}. Use the full label text.'
    if err == "disabled":
        return False, f'"{data.get("label")}" is disabled, so it cannot be changed right now.'
    if err == "radio":
        return False, f'"{data.get("label")}" is a radio button: choose another option in its group instead of unticking it.'
    name = f'{data.get("type")} "{data.get("label")}"'
    state = "ticked" if data.get("checked") else "not ticked"
    if data.get("ok"):
        return True, f"{name} is now {state}." if data.get("changed") else f"{name} was already {state}."
    return False, f"Clicked {name} but it is still {state}. The page may need another step first."
