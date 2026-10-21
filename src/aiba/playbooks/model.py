"""
The playbook format: a recorded task that can be replayed without an AI deciding each step.

This module is pure data and rules (no files, no browser, no network), so it is unit-tested
directly. It answers four questions:

  * What is a playbook?          -> Playbook / Step / Variable / Fingerprint (pydantic models).
  * Is this one safe to store?   -> validate(): every problem is listed, not just the first.
  * What may it touch?           -> compute_domains(): the sites a run is allowed to visit.
  * Has the user approved THIS?  -> approval_hash(): the approval is tied to the exact steps,
    so editing any step silently drops it (see docs/playbooks.md, "Approval policy").

Values typed into a playbook run (variables) are checked against their declared type before
they are substituted, so a "LinkedIn profile URL" variable can never carry another site.
Secrets never appear here as real values: only the <secret>name</secret> placeholder.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal, Optional
from urllib.parse import quote_plus, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1
MAX_STEPS = 300
MAX_TEXT = 2000
MAX_VARIABLES = 50
MAX_ATTRS = 25
MAX_ATTR_VALUE = 300

# Step kinds this version can store. A kind in the roadmap that is not here yet (repeat, if,
# checkpoint, run_playbook) is rejected rather than stored half-understood.
KINDS = ("open", "search", "back", "click", "type", "select", "key", "scroll", "wait", "tab", "handoff",
         "approve", "input", "scrape", "foreach", "extract", "ai")
AI_KINDS = ("extract", "ai")           # these need a model; every other kind replays without one
VAR_TYPES = ("text", "number", "url", "linkedin_url")
_FIELDS_USED = {                        # which optional fields each kind may carry
    "open": {"url"}, "search": {"query", "engine"}, "back": set(), "click": {"target"},
    "type": {"target", "text"}, "select": {"target", "text"}, "key": {"keys"}, "scroll": {"target"},
    "wait": {"seconds"}, "tab": {"tab_index"}, "handoff": {"message"}, "approve": {"message", "timeout_minutes", "on_timeout"},
    "input": {"message", "variable", "timeout_minutes"},
    "scrape": {"fields", "row_selector", "max_rows"},
    "foreach": {"source", "column", "variable", "body", "max_rows", "on_failure"},
    "extract": {"query", "tier"}, "ai": {"query", "tier"},
}
_CHECKED_FIELDS = ("url", "target", "text", "keys", "seconds", "query", "engine", "message", "tab_index",
                   "timeout_minutes", "variable", "on_timeout", "tier", "fields", "row_selector", "max_rows",
                   "source", "column", "body", "on_failure")
DEFAULT_APPROVAL_MINUTES = 24 * 60

# Search engines a `search` step can use. The address is built here from the engine and the
# (URL-encoded) query, so a search value can never change which site is visited.
SEARCH_URLS = {
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "google": "https://www.google.com/search?q={q}&udm=14",
    "bing": "https://www.bing.com/search?q={q}",
}
SEARCH_HOSTS = {"duckduckgo": "duckduckgo.com", "google": "www.google.com", "bing": "www.bing.com"}


def search_url(engine: str, query: str) -> str:
    return SEARCH_URLS[engine].format(q=quote_plus(query))

VAR_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
STEP_ID_RE = re.compile(r"^s\d{1,4}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
SECRET_RE = re.compile(r"<secret>([^<>]{1,100})</secret>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
LINKEDIN_PROFILE_RE = re.compile(
    r"^https://(?:www\.|[a-z]{2}\.)?linkedin\.com/in/[^/\s?#]+/?(?:\?[^\s#]*)?(?:#\S*)?$", re.IGNORECASE)
LINKEDIN_HOSTS = ["linkedin.com"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------ models
class Fingerprint(BaseModel):
    """How to find an element again. Never a numeric index: those change on every page load."""
    model_config = ConfigDict(extra="forbid")
    node_name: str = Field(default="", max_length=40)
    attributes: dict[str, str] = Field(default_factory=dict)
    ax_name: Optional[str] = Field(default=None, max_length=500)    # what a person reads on it
    x_path: Optional[str] = Field(default=None, max_length=2000)
    # Text, not numbers: they are 64-bit, and JavaScript (the review screen sends a playbook through the
    # browser and back) only keeps 53 bits of a number, which would silently corrupt every hash.
    element_hash: Optional[str] = Field(default=None, max_length=40)
    stable_hash: Optional[str] = Field(default=None, max_length=40)

    @field_validator("element_hash", "stable_hash", mode="before")
    @classmethod
    def _hash_as_text(cls, v):
        return None if v is None or isinstance(v, bool) else str(v)


class Check(BaseModel):
    """Something cheap to verify after a step ran."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["url_host"] = "url_host"
    hosts: list[str] = Field(default_factory=list, max_length=20)


class Variable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=40)
    type: Literal["text", "number", "url", "linkedin_url"] = "text"
    label: str = Field(default="", max_length=100)
    default: Optional[str] = Field(default=None, max_length=MAX_TEXT)
    required: bool = True
    allowed_hosts: list[str] = Field(default_factory=list, max_length=20)   # type "url": sites it may point at
    description: str = Field(default="", max_length=300)
    # A runtime variable is not given when the run starts: a person answers an `input` step DURING the run
    # (text or a number only; a web address typed mid-run could send a run anywhere).
    runtime: bool = False
    # A loop variable takes each value of a column in turn, inside a `foreach` step. It is never given up front.
    loop: bool = False


MAX_LOOP_ROWS = 50
DEFAULT_LOOP_ROWS = 20
MAX_LOOP_BODY = 20
LOOP_KINDS = ("open", "search", "back", "click", "type", "select", "key", "scroll", "wait", "tab", "scrape")
MAX_SCRAPE_FIELDS = 30
MAX_SCRAPE_ROWS = 500
DEFAULT_SCRAPE_ROWS = 100
SCRAPE_TYPES = ("text", "number", "link", "attr")


class ScrapeField(BaseModel):
    """One column of a scraped table: where to read it (a CSS selector, relative to the row) and how to read it."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=40)
    selector: str = Field(max_length=300)           # ":scope" means the row element itself
    type: Literal["text", "number", "link", "attr"] = "text"
    attr: Optional[str] = Field(default=None, max_length=60)      # type "attr": which attribute to read


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["open", "search", "back", "click", "type", "select", "key", "scroll", "wait", "tab",
                  "handoff", "approve", "input", "scrape", "foreach", "extract", "ai"]
    tag: Literal["deterministic", "ai"] = "deterministic"
    note: str = Field(default="", max_length=300)           # plain words: what this step does
    url: Optional[str] = None
    new_tab: bool = False
    target: Optional[Fingerprint] = None
    text: Optional[str] = Field(default=None, max_length=MAX_TEXT)
    clear: bool = True
    keys: Optional[str] = Field(default=None, max_length=200)
    seconds: Optional[float] = Field(default=None, ge=0, le=300)
    down: bool = True
    pages: float = Field(default=1.0, gt=0, le=50)
    query: Optional[str] = Field(default=None, max_length=MAX_TEXT)   # what to search for / the goal of an extract or ai step
    engine: Optional[Literal["duckduckgo", "google", "bing"]] = None  # for a search step
    message: Optional[str] = Field(default=None, max_length=500)      # what the user is asked to do (handoff)
    tab_index: Optional[int] = Field(default=None, ge=0, le=30)       # which open tab, counting from 0 (tab step)
    close_tab: bool = False                                          # tab step: close it instead of switching to it
    # approve step: how long to wait for the user's answer when nobody is there (default one day). No answer
    # in time means NO: the run is cancelled, never carried on.
    timeout_minutes: Optional[int] = Field(default=None, ge=5, le=7 * 24 * 60)
    # approve step: what happens when nobody answers in time. "cancel" (the default) ends the run; "skip" leaves
    # out the step the question was about and carries on with the rest. Never "go ahead": silence is never a yes.
    on_timeout: Optional[Literal["cancel", "skip"]] = None
    variable: Optional[str] = Field(default=None, max_length=40)       # input step: the runtime variable it fills
    # extract / ai step: which model TIER reads the page (a tier, never a model name, so the models behind the
    # tiers can be swapped in Settings at any time). None = automatic, the router's own choice for the job.
    tier: Optional[Literal["fast", "standard", "strong"]] = None
    # scrape step: read a table from the page with no AI. `row_selector` picks each repeated item (a row, a card,
    # a result); every field is then read inside it. Without a row selector the page gives ONE row.
    fields: Optional[list[ScrapeField]] = Field(default=None, max_length=MAX_SCRAPE_FIELDS)
    row_selector: Optional[str] = Field(default=None, max_length=300)
    max_rows: Optional[int] = Field(default=None, ge=1, le=MAX_SCRAPE_ROWS)
    # foreach step: run the next `body` steps once for each value of `column` of an earlier scrape step (`source` is
    # that step's id). The value goes into the loop variable named in `variable`. `max_rows` caps the loop (MAX_LOOP_ROWS).
    source: Optional[str] = Field(default=None, max_length=20)
    column: Optional[str] = Field(default=None, max_length=40)
    body: Optional[int] = Field(default=None, ge=1, le=MAX_LOOP_BODY)
    on_failure: Optional[Literal["stop", "skip"]] = None       # a row whose steps fail: stop the run (default) or skip that row
    ask: bool = False        # reaches other people or can't be undone: needs the user's approval
    check: Optional[Check] = None


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hash: str
    approved_at: str


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ai_run", "manual", "teach"] = "ai_run"
    task: str = Field(default="", max_length=MAX_TEXT)


class Playbook(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION
    id: str = ""
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=MAX_TEXT)
    version: int = 1
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    variables: list[Variable] = Field(default_factory=list, max_length=MAX_VARIABLES)
    steps: list[Step] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    source: Source = Field(default_factory=Source)
    # Let a model pick the replacement when a step's element (or a table's selectors) can't be found. Off by default; it
    # changes what a run may do, so it is part of what the user approves. Never applies to steps that send or post.
    repair: bool = False
    approval: Optional[Approval] = None


# ------------------------------------------------------------------------------ hosts
def host_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def host_allowed(host: str, allowed: list[str]) -> bool:
    """A host is allowed if it IS an allowed site or a subdomain of one (www.linkedin.com for
    linkedin.com). 'evil-linkedin.com' and 'linkedin.com.evil.net' are not."""
    host = (host or "").lower().strip(".")
    return any(host == a or host.endswith("." + a) for a in (x.lower().strip(".") for x in allowed) if a)


def _variable_hosts(v: Variable) -> list[str]:
    return list(LINKEDIN_HOSTS) if v.type == "linkedin_url" else list(v.allowed_hosts)


# --------------------------------------------------------------------------- validation
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_PUBLIC_SUFFIX_2LD = {"co", "com", "org", "net", "gov", "edu", "ac"}


def host_problem(host: str) -> Optional[str]:
    """Why this is not a usable site name. 'com', '*.com', 'co.uk' or 'https://a.com' would each let a
    playbook reach far more than its author meant, because a listed site covers its subdomains."""
    h = (host or "").strip().lower().strip(".")
    if h == "localhost":
        return None
    if not _HOST_RE.fullmatch(h):
        return f"'{host}' is not a site name like example.com"
    labels = h.split(".")
    if len(labels) == 2 and labels[0] in _PUBLIC_SUFFIX_2LD and len(labels[1]) == 2:
        return f"'{host}' is a whole country domain, not one site"
    return None


def _url_problem(template: str, variables: dict[str, Variable]) -> Optional[str]:
    """None if this URL (which may be just {{variable}}) is acceptable."""
    template = (template or "").strip()
    whole = PLACEHOLDER_RE.fullmatch(template)
    if whole:
        v = variables.get(whole.group(1))
        if v is None:
            return f"uses the undefined variable '{whole.group(1)}'"
        if v.type not in ("url", "linkedin_url"):
            return f"variable '{v.name}' is a {v.type}, but a URL is needed"
        if not _variable_hosts(v):
            return f"variable '{v.name}' needs at least one allowed site"
        return None
    if PLACEHOLDER_RE.search(template):
        return "a variable may only be the whole URL, not part of it"
    parsed = urlparse(template)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "must be an http(s) address"
    if parsed.username or parsed.password:
        return "must not contain a username or password"
    return None


def _check_placeholders(label: str, value: Optional[str], variables: dict[str, Variable],
                        allow_secret: bool) -> list[str]:
    out: list[str] = []
    if value is None:
        return out
    for name in PLACEHOLDER_RE.findall(value):
        if name not in variables:
            out.append(f"{label}: uses the undefined variable '{name}'")
    if not allow_secret and SECRET_RE.search(value):
        out.append(f"{label}: a stored secret may only be typed into a field")
    if _CONTROL_RE.search(value):
        out.append(f"{label}: contains control characters")
    return out


_FIELD_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,39}$")
_ATTR_RE = re.compile(r"^[A-Za-z_:][-A-Za-z0-9_:.]{0,59}$")


def scrape_problems(s: Step) -> list[str]:
    """Why a table-reading step can't be stored (selectors themselves are checked by the page when it runs)."""
    out: list[str] = []
    fields = s.fields or []
    if not fields:
        return ["a scrape step needs at least one field"]
    if s.row_selector is not None and (not s.row_selector.strip() or _CONTROL_RE.search(s.row_selector)):
        out.append("the row selector is empty or has control characters")
    names: set[str] = set()
    for f in fields:
        if not _FIELD_NAME_RE.fullmatch(f.name):
            out.append(f"field name '{f.name}' must start with a letter and use letters, digits, spaces, - or _")
        elif f.name.lower() in names:
            out.append(f"field '{f.name}' is named twice")
        names.add(f.name.lower())
        if not f.selector.strip() or _CONTROL_RE.search(f.selector):
            out.append(f"field '{f.name}' needs a selector (no control characters)")
        if f.type == "attr" and not (f.attr and _ATTR_RE.fullmatch(f.attr)):
            out.append(f"field '{f.name}' needs the name of the attribute to read")
        if f.type != "attr" and f.attr is not None:
            out.append(f"field '{f.name}': only an attribute field has an attribute name")
        if f.selector.strip() == ":scope" and s.row_selector is None:
            out.append(f"field '{f.name}': ':scope' means the row, and this step has no row selector")
    return out


def validate(pb: Playbook) -> list[str]:
    """Every reason this playbook cannot be stored or run; empty means fine."""
    problems: list[str] = []
    if pb.schema_version != SCHEMA_VERSION:
        problems.append(f"schema version {pb.schema_version} is not supported (this app reads {SCHEMA_VERSION})")
    if not pb.name.strip():
        problems.append("the playbook needs a name")
    if not pb.steps:
        problems.append("the playbook has no steps")
    if len(pb.steps) > MAX_STEPS:
        problems.append(f"too many steps ({len(pb.steps)}; the limit is {MAX_STEPS})")

    variables: dict[str, Variable] = {}
    for v in pb.variables:
        if not VAR_NAME_RE.fullmatch(v.name):
            problems.append(f"variable name '{v.name}' must be lowercase letters, digits and underscores")
        elif v.name in variables:
            problems.append(f"variable '{v.name}' is defined twice")
        variables[v.name] = v
        if v.default is not None:
            problems += [f"variable '{v.name}' default: {e}" for e in value_problems(v, v.default)]
        if v.type == "url" and not v.allowed_hosts:
            problems.append(f"variable '{v.name}' is a URL and needs at least one allowed site")
        problems += [f"variable '{v.name}': {p}" for p in map(host_problem, v.allowed_hosts) if p]

    seen: set[str] = set()
    for n, s in enumerate(pb.steps, start=1):
        where = f"step {s.id}"
        if not STEP_ID_RE.fullmatch(s.id):
            problems.append(f"{where}: bad step id")
        if s.id in seen:
            problems.append(f"{where}: duplicate step id")
        seen.add(s.id)

        if s.check is not None:
            problems += [f"{where} check: {p}" for p in map(host_problem, s.check.hosts) if p]
        if s.target is not None:
            if len(s.target.attributes) > MAX_ATTRS or any(
                    len(k) > 60 or len(v) > MAX_ATTR_VALUE for k, v in s.target.attributes.items()):
                problems.append(f"{where}: the element description is too large")

        expects_ai = s.kind in AI_KINDS
        if expects_ai and s.tag != "ai":
            problems.append(f"{where}: a {s.kind} step needs a model, so it must be tagged 'ai'")
        if not expects_ai and s.tag == "ai":
            problems.append(f"{where}: a {s.kind} step never needs a model, so it can't be tagged 'ai'")

        need = {"open": ("url", s.url), "search": ("query", s.query), "click": ("target", s.target),
                "type": ("target", s.target), "select": ("target", s.target), "key": ("keys", s.keys),
                "wait": ("seconds", s.seconds), "tab": ("tab_index", s.tab_index),
                "handoff": ("message", s.message), "approve": ("message", s.message),
                "input": ("variable", s.variable), "foreach": ("source", s.source),
                "extract": ("query", s.query), "ai": ("query", s.query)}
        if s.kind in need and need[s.kind][1] is None:
            problems.append(f"{where}: a {s.kind} step needs '{need[s.kind][0]}'")
        if s.kind == "search" and s.engine is None:
            problems.append(f"{where}: a search step needs an 'engine'")
        if s.kind == "search" and not (s.query or "").strip():
            problems.append(f"{where}: a search step needs something to search for")
        if s.kind in ("handoff", "approve", "input") and not (s.message or "").strip():
            problems.append(f"{where}: a {s.kind} step needs a message for the user")
        if s.kind in ("approve", "input") and n == len(pb.steps):
            problems.append(f"{where}: a {s.kind} step must come BEFORE the step it is about, so it can't be last")
        if s.kind in ("type", "select") and s.text is None:
            problems.append(f"{where}: a {s.kind} step needs 'text'")
        if s.kind == "scrape":
            problems += [f"{where}: {p}" for p in scrape_problems(s)]
        if s.kind == "foreach" and not (s.column and s.variable and s.body):
            problems.append(f"{where}: a foreach step needs a column, a variable and the number of steps to repeat")

        if s.kind == "open" and s.url is not None:
            p = _url_problem(s.url, variables)
            if p:
                problems.append(f"{where}: url {p}")
            if SECRET_RE.search(s.url):
                problems.append(f"{where}: a stored secret may only be typed into a field, never put in a URL")
        # A field the step's kind doesn't use is refused, so it can't hold data that a future player
        # might act on without the user having seen it on the review screen.
        for field in _CHECKED_FIELDS:
            if getattr(s, field) is not None and field not in _FIELDS_USED[s.kind]:
                problems.append(f"{where}: a {s.kind} step has no '{field}'")

        problems += _check_placeholders(f"{where} text", s.text, variables, allow_secret=s.kind == "type")
        problems += _check_placeholders(f"{where} keys", s.keys, variables, allow_secret=False)
        problems += _check_placeholders(f"{where} query", s.query, variables, allow_secret=False)
        # A handoff message is shown as written; an approve message may use the run's inputs ("Send to {{name}}?")
        problems += _check_placeholders(f"{where} message", s.message,
                                        {k: v for k, v in variables.items() if not v.runtime} if s.kind in ("approve", "input") else {},
                                        allow_secret=False)
    problems += _runtime_problems(pb, variables)
    problems += _loop_problems(pb, variables)
    for n, s in enumerate(pb.steps):
        if s.kind == "approve" and s.on_timeout == "skip" and not (n + 1 < len(pb.steps) and pb.steps[n + 1].ask):
            problems.append(f"step {s.id}: 'skip and carry on' only works when the very next step is one that needs approval "
                            "(a send, a post); anything else would leave the run half-done")
    return problems


def runtime_variables(pb: Playbook) -> list[Variable]:
    return [v for v in pb.variables if v.runtime]


def loop_body(pb: Playbook, index: int) -> list[Step]:
    """The steps a foreach step at this position (0-based) repeats."""
    s = pb.steps[index]
    return pb.steps[index + 1: index + 1 + (s.body or 0)] if s.kind == "foreach" else []


def _loop_problems(pb: Playbook, variables: dict[str, Variable]) -> list[str]:
    """Rules for `foreach`: a loop reads values from a table an earlier step scraped, runs only plain read-and-move steps
    for each, never touches LinkedIn (which needs paced, capped visits) and may not carry a value it did not check."""
    out: list[str] = []
    loops = [(n, s) for n, s in enumerate(pb.steps) if s.kind == "foreach"]
    used_by: dict[str, int] = {}
    inside: set[int] = set()
    for n, s in loops:
        where = f"step {s.id}"
        scrape = next((x for x in pb.steps[:n] if x.id == s.source and x.kind == "scrape"), None)
        if scrape is None:
            out.append(f"{where}: 'source' must be an earlier step that reads a table")
        elif s.column and s.column.lower() not in {f.name.lower() for f in scrape.fields or []}:
            out.append(f"{where}: the table has no column '{s.column}'")
        v = variables.get(s.variable or "")
        if v is None:
            out.append(f"{where}: the loop variable '{s.variable}' is not defined")
        else:
            if not v.loop:
                out.append(f"{where}: variable '{v.name}' must be marked as a loop variable")
            if v.type not in ("text", "number", "url"):
                out.append(f"{where}: a loop variable can be text, a number or a web address, not {v.type}")
            if v.name in used_by:
                out.append(f"{where}: loop variable '{v.name}' is already used by another loop")
            used_by[v.name] = n
        if s.max_rows is not None and s.max_rows > MAX_LOOP_ROWS:
            out.append(f"{where}: a loop runs at most {MAX_LOOP_ROWS} rows")
        body = pb.steps[n + 1: n + 1 + (s.body or 0)]
        if s.body and len(body) < s.body:
            out.append(f"{where}: there are not {s.body} steps after it to repeat")
        for k, b in enumerate(body, start=n + 1):
            inside.add(k)
            if b.kind not in LOOP_KINDS:
                out.append(f"step {b.id}: a {b.kind} step can't be inside a loop (only {', '.join(LOOP_KINDS)})")
            if b.ask:
                out.append(f"step {b.id}: a step that sends or can't be undone can't be inside a loop; use a schedule with one row each")
    if loops and any(host_allowed(h, ["linkedin.com"]) for h in compute_domains(pb)):
        out.append("a loop can't be used on LinkedIn, because LinkedIn visits are paced and capped per day: "
                   "use a schedule with one row per profile instead")
    for v in variables.values():
        if v.loop and v.name not in used_by:
            out.append(f"variable '{v.name}' is a loop variable, but no foreach step uses it")
        if v.loop and v.runtime:
            out.append(f"variable '{v.name}' can't be both a loop variable and answered during the run")
    for k, s in enumerate(pb.steps):
        for field in ("url", "text", "keys", "query", "message"):
            for name in PLACEHOLDER_RE.findall(getattr(s, field, None) or ""):
                at = used_by.get(name)
                if at is not None and not (at < k < at + 1 + (pb.steps[at].body or 0)):
                    out.append(f"step {s.id}: uses '{name}', which only exists inside its loop")
    return out


def _runtime_problems(pb: Playbook, variables: dict[str, Variable]) -> list[str]:
    """Rules for values a person types DURING a run: each has exactly one input step that fills it, and no
    step uses it before that step has asked."""
    out: list[str] = []
    asked_at: dict[str, int] = {}
    for n, s in enumerate(pb.steps):
        if s.kind == "input" and s.variable is not None:
            v = variables.get(s.variable)
            if v is None:
                out.append(f"step {s.id}: asks for '{s.variable}', which is not a variable")
            elif not v.runtime:
                out.append(f"step {s.id}: '{s.variable}' is given when the run starts, so it can't also be asked for during it")
            elif s.variable in asked_at:
                out.append(f"step {s.id}: '{s.variable}' is asked for twice")
            else:
                asked_at[s.variable] = n
    for v in variables.values():
        if v.runtime:
            if v.type not in ("text", "number"):
                out.append(f"variable '{v.name}': a value typed during a run can only be text or a number")
            if v.name not in asked_at and not any(s.kind == "input" and s.variable == v.name for s in pb.steps):
                out.append(f"variable '{v.name}' is asked during the run, but no step asks for it")
    for n, s in enumerate(pb.steps):
        for field in ("url", "text", "keys", "query", "message"):
            value = getattr(s, field, None)
            for name in PLACEHOLDER_RE.findall(value or ""):
                if name in asked_at and n <= asked_at[name]:
                    out.append(f"step {s.id}: uses '{name}' before the step that asks for it")
    return out


# ------------------------------------------------------------------------------ domains
def compute_domains(pb: Playbook) -> list[str]:
    """The sites a run may visit: those the steps open or are expected to land on, plus what URL
    variables may point at."""
    variables = {v.name: v for v in pb.variables}
    hosts: set[str] = set()
    for s in pb.steps:
        if s.check is not None:                      # a site a step is expected to land on
            hosts.update(h.lower().strip(".") for h in s.check.hosts if h)
        if s.kind == "search" and s.engine in SEARCH_HOSTS:
            hosts.add(SEARCH_HOSTS[s.engine])
        if s.kind != "open" or not s.url:
            continue
        whole = PLACEHOLDER_RE.fullmatch(s.url.strip())
        if whole and whole.group(1) in variables:
            hosts.update(h.lower().strip(".") for h in _variable_hosts(variables[whole.group(1)]))
        else:
            h = host_of(s.url)
            if h:
                hosts.add(h)
    # www.linkedin.com adds nothing once linkedin.com is listed: a site covers its subdomains.
    return sorted(h for h in hosts if not any(o != h and h.endswith("." + o) for o in hosts))


# ------------------------------------------------------------------------------ approval
# Step fields added after the first playbooks were approved. They are part of the hash only when a step USES them, so a
# playbook approved before they existed keeps its approval (adding a field to the format must never cost everyone theirs).
_LATE_STEP_FIELDS = ("timeout_minutes", "on_timeout", "variable", "fields", "row_selector", "max_rows",
                     "source", "column", "body", "on_failure")


def approval_hash(pb: Playbook) -> str:
    """Fingerprint of everything a user approves: the steps, what the variables may be, the sites.
    Notes and names are cosmetic and excluded, so renaming does not cost the approval. So is the model tier of a
    step: it changes what a run costs, not what it does or may reach (the guard is the same on every tier).
    Settings that were added later (see _LATE_STEP_FIELDS, a runtime or loop variable, repair) count only when they
    are set, so playbooks approved before those existed are still approved."""
    steps = []
    for s in pb.steps:
        d = s.model_dump(mode="json", exclude={"note", "tier"})
        for k in _LATE_STEP_FIELDS:
            if d.get(k) is None:
                d.pop(k, None)
        steps.append(d)
    variables = []
    for v in pb.variables:
        item = {"name": v.name, "type": v.type, "allowed_hosts": sorted(v.allowed_hosts)}
        if v.runtime:
            item["runtime"] = True
        if v.loop:
            item["loop"] = True
        variables.append(item)
    body = {"steps": steps, "variables": variables, "domains": compute_domains(pb)}
    if pb.repair:
        body["repair"] = True
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def is_approved(pb: Playbook) -> bool:
    return pb.approval is not None and pb.approval.hash == approval_hash(pb)


def with_approval(pb: Playbook) -> Playbook:
    return pb.model_copy(update={"approval": Approval(hash=approval_hash(pb), approved_at=_now())})


def guarded_by_checkpoint(pb: Playbook, index: int) -> bool:
    """Is the step at this position (0-based) directly preceded by an approve step? Then the user is asked
    before it every time it runs, which is what makes it safe to run while they are away."""
    return index > 0 and pb.steps[index - 1].kind == "approve"


def unguarded_ask_steps(pb: Playbook) -> list[int]:
    """Positions (1-based) of steps that reach other people or can't be undone and have NO approve step
    right before them: these only run unattended on the strength of the playbook's own approval."""
    return [i + 1 for i, s in enumerate(pb.steps) if s.ask and not guarded_by_checkpoint(pb, i)]


def ask_steps(pb: Playbook) -> list[Step]:
    """The steps the user is explicitly approving as outward-facing or irreversible."""
    return [s for s in pb.steps if s.ask]


# ------------------------------------------------------------------------------ variables
def value_problems(v: Variable, value: str) -> list[str]:
    """Why this value is not acceptable for this variable (empty list = fine)."""
    if not isinstance(value, str):
        return ["must be text"]
    if len(value) > MAX_TEXT:
        return [f"is longer than {MAX_TEXT} characters"]
    if _CONTROL_RE.search(value):
        return ["contains control characters"]
    if SECRET_RE.search(value):
        return ["must not contain a secret placeholder"]
    if v.type == "number":
        try:
            float(value)
        except ValueError:
            return ["must be a number"]
    elif v.type == "linkedin_url":
        if not LINKEDIN_PROFILE_RE.fullmatch(value.strip()):
            return ["must be a LinkedIn profile address like https://www.linkedin.com/in/name"]
    elif v.type == "url":
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ["must be an http(s) address"]
        if parsed.username or parsed.password:
            return ["must not contain a username or password"]
        if not host_allowed(parsed.hostname, v.allowed_hosts):
            return [f"must be on one of: {', '.join(v.allowed_hosts) or '(no sites allowed)'}"]
    return []


def bind_values(pb: Playbook, supplied: dict) -> tuple[dict[str, str], list[str]]:
    """Check the values a user typed for a run; returns (values to substitute, errors)."""
    values: dict[str, str] = {}
    errors: list[str] = []
    known = {v.name for v in pb.variables}
    for extra in sorted(set(supplied) - known):
        errors.append(f"'{extra}' is not a variable of this playbook")
    for name in sorted(set(supplied) & {v.name for v in pb.variables if v.runtime}):
        errors.append(f"'{name}' is answered while the run is going, not before it")
    for name in sorted(set(supplied) & {v.name for v in pb.variables if v.loop}):
        errors.append(f"'{name}' is filled from a table while the run is going, not before it")
    for v in pb.variables:
        if v.runtime or v.loop:
            continue
        raw = supplied.get(v.name)
        if raw is None or (isinstance(raw, str) and raw == ""):
            raw = v.default
        if raw is None or raw == "":
            if v.required:
                errors.append(f"'{v.name}' is required")
            continue
        raw = raw if isinstance(raw, str) else str(raw)
        errors += [f"'{v.name}' {e}" for e in value_problems(v, raw)]
        values[v.name] = raw.strip() if v.type in ("url", "linkedin_url") else raw
    return values, errors


def render(template: Optional[str], values: dict[str, str]) -> Optional[str]:
    """Substitute {{name}} placeholders. <secret>name</secret> is left for the agent layer to fill."""
    if template is None:
        return None
    return PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def make_variable(pb: Playbook, step_id: str, field: str, name: str, vtype: str,
                  allowed_hosts: Optional[list[str]] = None, label: str = "") -> Playbook:
    """Turn the literal value in one step into a variable (the literal becomes its default).
    Returns a new playbook; the approval is dropped because the steps changed."""
    if field not in ("url", "text", "query"):
        raise ValueError("only a step's url, text or query can become a variable")
    if vtype not in VAR_TYPES:
        raise ValueError(f"unknown variable type '{vtype}'")
    if not VAR_NAME_RE.fullmatch(name):
        raise ValueError("a variable name is lowercase letters, digits and underscores, starting with a letter")
    if any(v.name == name for v in pb.variables):
        raise ValueError(f"a variable named '{name}' already exists")
    step = next((s for s in pb.steps if s.id == step_id), None)
    if step is None:
        raise ValueError(f"no step {step_id}")
    literal = getattr(step, field)
    if not literal:
        raise ValueError(f"step {step_id} has no {field} to turn into a variable")
    if PLACEHOLDER_RE.search(literal) or SECRET_RE.search(literal):
        raise ValueError("that value is already a variable or a secret")
    hosts = list(allowed_hosts or [])
    if vtype == "url" and not hosts:
        h = host_of(literal)
        hosts = [h] if h else []
    var = Variable(name=name, type=vtype, label=label or name.replace("_", " "), default=literal,
                   allowed_hosts=[] if vtype != "url" else hosts)
    problems = value_problems(var, literal)
    if problems:
        raise ValueError(f"'{literal[:60]}' can't be a {vtype}: {problems[0]}")
    steps = [s.model_copy(update={field: "{{" + name + "}}"}) if s.id == step_id else s for s in pb.steps]
    out = pb.model_copy(update={"variables": [*pb.variables, var], "steps": steps, "approval": None})
    return out.model_copy(update={"domains": compute_domains(out)})


def refresh(pb: Playbook) -> Playbook:
    """Recompute derived fields after any change; drops a stale approval."""
    out = pb.model_copy(update={"domains": compute_domains(pb)})
    if out.approval is not None and out.approval.hash != approval_hash(out):
        out = out.model_copy(update={"approval": None})
    return out
