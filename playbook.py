"""
The playbook format: a recorded task that can be replayed without an AI deciding each step.

This module is pure data and rules (no files, no browser, no network), so it is unit-tested
directly. It answers four questions:

  * What is a playbook?          -> Playbook / Step / Variable / Fingerprint (pydantic models).
  * Is this one safe to store?   -> validate(): every problem is listed, not just the first.
  * What may it touch?           -> compute_domains(): the sites a run is allowed to visit.
  * Has the user approved THIS?  -> approval_hash(): the approval is tied to the exact steps,
    so editing any step silently drops it (see PLAYBOOKS.md, "Approval policy").

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
         "approve", "extract", "ai")
AI_KINDS = ("extract", "ai")           # these need a model; every other kind replays without one
VAR_TYPES = ("text", "number", "url", "linkedin_url")
_FIELDS_USED = {                        # which optional fields each kind may carry
    "open": {"url"}, "search": {"query", "engine"}, "back": set(), "click": {"target"},
    "type": {"target", "text"}, "select": {"target", "text"}, "key": {"keys"}, "scroll": {"target"},
    "wait": {"seconds"}, "tab": {"tab_index"}, "handoff": {"message"}, "approve": {"message", "timeout_minutes"},
    "extract": {"query"}, "ai": {"query"},
}
_CHECKED_FIELDS = ("url", "target", "text", "keys", "seconds", "query", "engine", "message", "tab_index",
                   "timeout_minutes")
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


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["open", "search", "back", "click", "type", "select", "key", "scroll", "wait", "tab",
                  "handoff", "approve", "extract", "ai"]
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
                "extract": ("query", s.query), "ai": ("query", s.query)}
        if s.kind in need and need[s.kind][1] is None:
            problems.append(f"{where}: a {s.kind} step needs '{need[s.kind][0]}'")
        if s.kind == "search" and s.engine is None:
            problems.append(f"{where}: a search step needs an 'engine'")
        if s.kind == "search" and not (s.query or "").strip():
            problems.append(f"{where}: a search step needs something to search for")
        if s.kind in ("handoff", "approve") and not (s.message or "").strip():
            problems.append(f"{where}: a {s.kind} step needs a message for the user")
        if s.kind == "approve" and n == len(pb.steps):
            problems.append(f"{where}: an approve step must come BEFORE the step it guards, so it can't be last")
        if s.kind in ("type", "select") and s.text is None:
            problems.append(f"{where}: a {s.kind} step needs 'text'")

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
        problems += _check_placeholders(f"{where} message", s.message, variables if s.kind == "approve" else {},
                                        allow_secret=False)
    return problems


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
def approval_hash(pb: Playbook) -> str:
    """Fingerprint of everything a user approves: the steps, what the variables may be, the sites.
    Notes and names are cosmetic and excluded, so renaming does not cost the approval."""
    body = {
        "steps": [s.model_dump(mode="json", exclude={"note"}) for s in pb.steps],
        "variables": [{"name": v.name, "type": v.type, "allowed_hosts": sorted(v.allowed_hosts)}
                      for v in pb.variables],
        "domains": compute_domains(pb),
    }
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
    for v in pb.variables:
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
