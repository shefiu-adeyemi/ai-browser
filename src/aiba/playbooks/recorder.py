"""
Turns a finished AI run into a DRAFT playbook (nothing is stored here).

The agent's history says, step by step, what the model chose to do and which element it acted on.
This module keeps what can be replayed without a model (open a page, click a known element, type,
press keys, scroll, wait), tags what still needs one (extract), drops what only helped the model
look around (find_text, search_page, ...), and tells the user everything it left out, so a draft is
never silently incomplete.

Three safety rules apply while converting:
  * a real secret value is replaced by its <secret>name</secret> placeholder, and a literal typed
    into a password field is NOT recorded at all;
  * a click on a control whose label says send/post/buy/delete/... and an Enter/Space key press
    are marked `ask`, so the user sees them listed when they approve the playbook;
  * the sites a click landed on are recorded as checks, so the approved site list covers them.

The input is duck-typed (anything shaped like browser-use's AgentHistoryList) so it can be tested
without a browser.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import quote_plus, urlparse, urlunparse

from pydantic import ValidationError

from aiba.playbooks import model as pbk
from aiba.agent.action_guard import activates_with, is_risky_label
from aiba.playbooks.model import Check, Fingerprint, Playbook, Source, Step

_FP_ATTRS = ("id", "name", "aria-label", "type", "role", "placeholder", "title", "alt", "href",
             "data-testid", "data-test", "for")
_ATTR_MAX = 200
# Actions that only helped the model look around or hold data: not part of a replay.
_OBSERVATION = {"done", "find_text", "search_page", "find_elements", "dropdown_options", "screenshot",
                "read_file"}
_NOT_RECORDED = {"evaluate": "ran JavaScript", "upload_file": "uploaded a file",
                 "write_file": "wrote a file", "replace_file": "edited a file",
                 "save_as_pdf": "saved a PDF"}


@dataclass
class Draft:
    playbook: Playbook
    suggestions: list[dict] = field(default_factory=list)   # variables the user may accept
    notes: list[str] = field(default_factory=list)           # what was left out or needs a look

    def to_dict(self) -> dict:
        return {"playbook": self.playbook.model_dump(mode="json"), "suggestions": self.suggestions,
                "notes": self.notes}


# ------------------------------------------------------------------------------ secrets
def flatten_secrets(sensitive_data: Optional[dict]) -> dict[str, str]:
    """{real value: placeholder name} from browser-use's (possibly domain-scoped) sensitive_data."""
    out: dict[str, str] = {}

    def walk(d: dict) -> None:
        for k, v in d.items():
            if isinstance(v, dict):
                walk(v)
            elif isinstance(v, str) and v:
                out[v] = str(k)

    walk(sensitive_data or {})
    return out


def redact(text: Optional[str], secrets: dict[str, str]) -> Optional[str]:
    """Replace any real secret value inside `text` by its placeholder (longest value first)."""
    if not text or not secrets:
        return text
    for value in sorted(secrets, key=len, reverse=True):
        if value in text:
            text = text.replace(value, f"<secret>{secrets[value]}</secret>")
    return text


# ----------------------------------------------------------------------------- helpers
def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _action_dict(action: Any) -> dict:
    if isinstance(action, dict):
        return action
    return action.model_dump(exclude_none=True, mode="json")


def _name_params(action: Any) -> tuple[str, dict]:
    d = _action_dict(action)
    if not d:
        return "", {}
    name = next(iter(d))
    params = d[name]
    return name, params if isinstance(params, dict) else {}


_NOT_AVAILABLE = re.compile(r"^Element index [0-9]+ not available", re.IGNORECASE)


def _failed(result: Any) -> bool:
    """browser-use reports a click on a missing element as a plain message, not as an error."""
    if _get(result, "error"):
        return True
    return bool(_NOT_AVAILABLE.match(str(_get(result, "extracted_content", "") or "")))


def _fingerprint(el: Any, secrets: dict[str, str]) -> Optional[Fingerprint]:
    if el is None:
        return None
    attrs = _get(el, "attributes") or {}
    kept = {k: redact(_strip_query(str(v)) if k == "href" else str(v), secrets) for k, v in attrs.items()
            if k in _FP_ATTRS and v and len(str(v)) <= _ATTR_MAX}
    ax = _get(el, "ax_name")
    x_path = _get(el, "x_path")
    fp = Fingerprint(
        node_name=str(_get(el, "node_name", "") or "").lower()[:40],
        attributes=kept,
        # A big container's accessible name can be a page of text: keep the start, it is only a hint.
        ax_name=(redact(ax, secrets) or "")[:500] if ax else None,
        x_path=str(x_path)[:2000] if x_path else None,
        element_hash=_get(el, "element_hash"),
        stable_hash=_get(el, "stable_hash"),
    )
    if not (fp.attributes or fp.ax_name or fp.x_path or fp.element_hash or fp.stable_hash):
        return None
    return fp


def _strip_query(href: str) -> str:
    """A link's ?query and #fragment often carry tokens, tracking ids or personal data, and change
    between visits anyway; the path is what identifies the link."""
    try:
        p = urlparse(href)
        return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except ValueError:
        return href.split("?", 1)[0].split("#", 1)[0]


def _label(fp: Optional[Fingerprint]) -> str:
    if fp is None:
        return ""
    a = fp.attributes
    return " ".join(x for x in [fp.ax_name or "", a.get("aria-label", ""), a.get("title", ""),
                                a.get("alt", "")] if x)


def _describe(fp: Optional[Fingerprint], fallback: str = "the element") -> str:
    label = (fp.ax_name or fp.attributes.get("aria-label") or fp.attributes.get("placeholder")
             or fp.attributes.get("name") or "") if fp else ""
    label = label.strip()
    return f"'{label[:60]}'" if label else fallback


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:30]
    return s if s and s[0].isalpha() else fallback


def _is_password(fp: Optional[Fingerprint]) -> bool:
    return bool(fp and fp.attributes.get("type", "").lower() == "password")


def _steps_from_action(name: str, p: dict, fp: Optional[Fingerprint], goal: str,
                       secrets: dict[str, str]) -> tuple[list[Step], list[str]]:
    """The steps (usually one) for one recorded action, plus notes for the user."""
    note_goal = (goal or "")[:300]
    notes: list[str] = []

    if name == "navigate":
        url = p.get("url") or ""
        return [Step(id="", kind="open", url=redact(url, secrets), new_tab=bool(p.get("new_tab")),
                     note=note_goal or f"Open {url[:80]}")], notes
    if name == "search":
        engine = (p.get("engine") or "duckduckgo").lower()
        query = p.get("query") or ""
        if engine not in pbk.SEARCH_URLS:
            return [], [f"A search on an unknown engine ('{engine}') was not recorded."]
        return [Step(id="", kind="search", engine=engine, query=redact(query, secrets),
                     note=note_goal or f"Search for {query[:60]}")], notes
    if name == "request_user_help":
        reason = redact(str(p.get("reason") or ""), secrets).strip()
        return [Step(id="", kind="handoff", message=reason[:500] or "Do the manual step in the browser, then continue.",
                     note=note_goal or "A step only you can do")], notes
    if name == "go_back":
        return [Step(id="", kind="back", note=note_goal or "Go back")], notes
    if name == "wait":
        secs = min(max(float(p.get("seconds") or 0), 0.0), 300.0)
        return [Step(id="", kind="wait", seconds=secs, note=note_goal or f"Wait {secs:g} s")], notes
    if name == "scroll":
        pages = min(max(float(p.get("pages") or 1.0), 0.1), 50.0)
        return [Step(id="", kind="scroll", down=bool(p.get("down", True)), pages=pages,
                     target=fp, note=note_goal or "Scroll")], notes
    if name == "send_keys":
        keys = redact(str(p.get("keys") or ""), secrets)
        return [Step(id="", kind="key", keys=keys, ask=activates_with(keys or ""),
                     note=note_goal or f"Press {keys}")], notes
    if name == "extract":
        query = p.get("query") or ""
        return [Step(id="", kind="extract", tag="ai", query=redact(query, secrets),
                     note=note_goal or "Read information from the page")], notes

    if name in ("click", "input", "select_dropdown"):
        what = {"click": "Click", "input": "Type into", "select_dropdown": "Choose an option in"}[name]
        if fp is None:
            # browser-use only has no element for an index that was NOT on the page, i.e. the action
            # did not do anything. (Its result carries no error for this, only a message.)
            return [], [f"A {name} on an element that was not on the page was left out."]
        if name == "click":
            return [Step(id="", kind="click", target=fp, ask=is_risky_label(_label(fp)),
                         note=note_goal or f"Click {_describe(fp)}")], notes
        text = redact(str(p.get("text") if p.get("text") is not None else ""), secrets) or ""
        if name == "input":
            if _is_password(fp) and not pbk.SECRET_RE.search(text):
                return [Step(id="", kind="handoff", message="Type the password into the box yourself, then continue.",
                             note="A password was typed here. It is never recorded; this step asks you to do it.")], \
                    ["A value typed into a password field was NOT recorded. The playbook will ask you to type it, "
                     "or use a stored secret instead."]
            return [Step(id="", kind="type", target=fp, text=text, clear=bool(p.get("clear", True)),
                         note=note_goal or f"Type into {_describe(fp)}")], notes
        return [Step(id="", kind="select", target=fp, text=text,
                     note=note_goal or f"Choose '{text[:40]}' in {_describe(fp)}")], notes

    return [], notes


# ---------------------------------------------------------------------------- the draft
def draft_from_history(history: Any, task: str = "", sensitive_data: Optional[dict] = None,
                       name: str = "") -> Draft:
    secrets = flatten_secrets(sensitive_data)
    items = list(_get(history, "history", None) or history or [])
    steps: list[Step] = []
    notes: list[str] = []
    seen_notes: set[str] = set()

    def add_note(n: str) -> None:
        if n not in seen_notes:
            seen_notes.add(n)
            notes.append(n)

    first_tabs = 0
    for item in items:                      # how many tabs were open when the run began
        st = _get(item, "state")
        if st is not None and _get(st, "tabs", None):
            first_tabs = len(list(_get(st, "tabs")))
            break

    for i, item in enumerate(items):
        mo = _get(item, "model_output")
        if mo is None:
            continue
        actions = list(_get(mo, "action", None) or [])
        results = list(_get(item, "result", None) or [])
        state = _get(item, "state")
        elements = list(_get(state, "interacted_element", None) or []) if state is not None else []
        brain = _get(mo, "current_state", None)
        goal = (_get(brain, "next_goal", "") if brain is not None else _get(mo, "next_goal", "")) or ""
        current = (_get(state, "url", "") or "") if state is not None else ""
        landed = ""
        if i + 1 < len(items):
            nxt = _get(items[i + 1], "state")
            landed = (_get(nxt, "url", "") or "") if nxt is not None else ""

        for j, action in enumerate(actions):
            aname, params = _name_params(action)
            if not aname or aname in _OBSERVATION:
                continue
            result = results[j] if j < len(results) else None
            if result is not None and _failed(result):
                add_note(f"A failed {aname} step was left out.")
                continue
            if aname in ("switch", "close"):
                idx = _tab_index(_get(state, "tabs", None), str(params.get("tab_id", "")))
                if idx is None:
                    add_note(f"A tab {aname} could not be recorded (that tab was not in the list).")
                    continue
                steps.append(Step(id="", kind="tab", tab_index=idx, close_tab=aname == "close",
                                  note=goal[:300] or f"{'Close' if aname == 'close' else 'Switch to'} tab {idx + 1}"))
                if first_tabs > 1:
                    add_note(f"This run began with {first_tabs} tabs open, but a replay starts from one tab, so "
                             "tab steps may not line up. The run stops with a clear message if a tab is missing.")
                continue
            if aname in _NOT_RECORDED:
                add_note(f"Not recorded: the agent {_NOT_RECORDED[aname]}. Check the playbook still makes sense.")
                continue
            fp = _fingerprint(elements[j] if j < len(elements) else None, secrets)
            try:
                new, new_notes = _steps_from_action(aname, params, fp, goal, secrets)
                new = [_redact_step(st, secrets) for st in new]        # the model's own words too
            except (ValidationError, ValueError, TypeError):
                # One odd step (a huge value, a malformed number) must not lose the whole draft.
                add_note(f"A {aname} step was left out because its values were too large or malformed.")
                continue
            for n in new_notes:
                add_note(n)
            if not new and not new_notes:
                add_note(f"Not recorded: an unsupported '{aname}' step.")
            # Where did the last action of this step land? Record it as a check, so a replay verifies
            # it and the approved site list covers it.
            if new and j == len(actions) - 1 and landed:
                new[-1] = _with_landing_check(new[-1], current, landed)
            steps.extend(new)

    steps = _merge_duplicate_opens(steps)
    _note_repeats(steps, add_note)
    steps = [s.model_copy(update={"id": f"s{n}"}) for n, s in enumerate(steps, start=1)]
    task = redact((task or "").strip(), secrets) or ""
    pb = Playbook(
        name=_short_name(name or task),
        description=task[:pbk.MAX_TEXT],
        steps=steps,
        source=Source(kind="ai_run", task=task[:pbk.MAX_TEXT]),
    )
    pb = pbk.refresh(pb)
    if not steps:
        add_note("The run had no steps that can be replayed.")
    return Draft(playbook=pb, suggestions=suggest_variables(pb, task), notes=notes)


def _tab_index(tabs: Any, tab_id: str) -> Optional[int]:
    """Position (from 0) of the tab with this id among the tabs that were open. browser-use names a tab
    by the last 4 characters of its id, which means nothing in a later run; its position does."""
    for i, t in enumerate(list(tabs or [])):
        if tab_id and str(_get(t, "target_id", "") or "")[-4:] == tab_id:
            return i
    return None


def _redact_step(step: Step, secrets: dict[str, str]) -> Step:
    if not secrets:
        return step
    return step.model_copy(update={f: redact(getattr(step, f), secrets)
                                   for f in ("note", "url", "text", "keys", "query")
                                   if getattr(step, f) is not None})


def _short_name(text: str) -> str:
    """A task can be a paragraph; a playbook name should fit on one line."""
    text = " ".join((text or "").split())
    if len(text) > 60:
        text = text[:59].rstrip() + "…"
    return text or "Recorded run"


def _with_landing_check(step: Step, current_url: str, landed_url: str) -> Step:
    """Record where a step ended up. An `open` always gets a check (its own site plus wherever it
    landed, e.g. after a redirect). A click or key press only gets one if it LEFT the site: staying
    on the same site is the normal case and needs no check."""
    landed = pbk.host_of(landed_url)
    if not landed:
        return step
    if step.kind == "open":
        hosts = sorted({h for h in (pbk.host_of(step.url or ""), landed) if h})
        return step.model_copy(update={"check": Check(kind="url_host", hosts=hosts)})
    if step.kind in ("click", "key", "select"):
        here = pbk.host_of(current_url)
        if here and not pbk.host_allowed(landed, [here]) and not pbk.host_allowed(here, [landed]):
            return step.model_copy(update={"check": Check(kind="url_host", hosts=[landed])})
    return step


def _merge_duplicate_opens(steps: list[Step]) -> list[Step]:
    """browser-use opens a URL found in the task by itself ("Initial navigation"), and the model often
    opens the same address again. Opening the same page twice in a row changes nothing, so keep one."""
    out: list[Step] = []
    for s in steps:
        prev = out[-1] if out else None
        if prev is not None and s.kind == "open" and prev.kind == "open" and s.url == prev.url                 and s.new_tab == prev.new_tab:
            if prev.check is None and s.check is not None:
                out[-1] = prev.model_copy(update={"check": s.check})
            continue
        out.append(s)
    return out


def _note_repeats(steps: list[Step], add_note) -> None:
    """The same element clicked several times in a row is kept as recorded: it may be paging
    through results ("Next" five times) or a slow page the model retried, and only the user knows
    which. Say so, so they can delete the extras in the review."""
    for prev, cur in zip(steps, steps[1:]):
        if (cur.kind == "click" and prev.kind == "click" and cur.target and prev.target
                and cur.target.stable_hash is not None and cur.target.stable_hash == prev.target.stable_hash):
            add_note("The same element was clicked more than once in a row. Keep every click if you were "
                     "paging through results; delete the extras if the page was just slow.")
            return


# ------------------------------------------------------------------------ suggestions
def _url_in_task(url: str, task_l: str) -> bool:
    """Did the user write this address in their request? Ignores a trailing slash on either side
    (the browser adds one: the task says https://a.com, the page is https://a.com/)."""
    base = url.lower().rstrip("/")
    return bool(base) and re.search(re.escape(base) + r"(?=/?(?:[\s,;)\]\"']|[.!?]*$|[.!?]\s))", task_l) is not None


def suggest_variables(pb: Playbook, task: str) -> list[dict]:
    """Values the user probably wants to change from run to run. Nothing is applied here."""
    task_l = (task or "").lower()
    used: set[str] = {v.name for v in pb.variables}
    out: list[dict] = []

    def unique(base: str) -> str:
        name, n = base, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        return name

    for s in pb.steps:
        if s.kind == "open" and s.url and not pbk.PLACEHOLDER_RE.search(s.url):
            if pbk.LINKEDIN_PROFILE_RE.fullmatch(s.url.strip()):
                out.append({"step_id": s.id, "field": "url", "value": s.url, "name": unique("profile_url"),
                            "type": "linkedin_url", "allowed_hosts": [],
                            "reason": "A profile address usually changes from run to run."})
            elif _url_in_task(s.url, task_l):
                host = urlparse(s.url).hostname or ""
                out.append({"step_id": s.id, "field": "url", "value": s.url, "name": unique("page_url"),
                            "type": "url", "allowed_hosts": [host] if host else [],
                            "reason": "You gave this address in your request."})
        elif s.kind == "search" and s.query and not pbk.PLACEHOLDER_RE.search(s.query):
            if len(s.query.strip()) >= 2 and s.query.lower() in task_l:
                out.append({"step_id": s.id, "field": "query", "value": s.query, "name": unique("search_text"),
                            "type": "text", "allowed_hosts": [],
                            "reason": "You gave this search in your request."})
        elif s.kind == "type" and s.text and s.target:
            if pbk.SECRET_RE.search(s.text) or pbk.PLACEHOLDER_RE.search(s.text):
                continue
            if len(s.text.strip()) >= 2 and s.text.lower() in task_l:
                fallback = "text"
                base = _slug(s.target.attributes.get("name") or s.target.attributes.get("placeholder")
                             or s.target.attributes.get("aria-label") or s.target.ax_name or "", fallback)
                out.append({"step_id": s.id, "field": "text", "value": s.text, "name": unique(base),
                            "type": "number" if re.fullmatch(r"-?\d+(\.\d+)?", s.text.strip()) else "text",
                            "allowed_hosts": [], "reason": "You gave this text in your request."})
    return out
