"""
Runs a playbook: the same steps the AI chose, replayed without an AI deciding each one.

The player never touches the browser itself. It talks to a Driver (read the page, run one action),
so every rule here is unit-tested with a fake page, and the real Driver (SessionDriver) only wraps
the same GuardedTools the AI agent uses. That is the point: a playbook step gets NO weaker safety
than an AI step. The guard still blocks credentials typed into the wrong field, non-http addresses
and so on; what changes is only who answers its "may I?" questions:

  * a step the user approved when saving ("Send" clicks, Enter on a message box) is pre-approved,
    but only if the element found is a HIGH confidence match for the one they approved;
  * anything else the guard wants approved is asked of the user (attended run), or STOPS the run
    (unattended): silence never means yes;
  * a block is final.

Beyond the guard, the player enforces what the playbook promised:

  * it only visits the playbook's approved sites (and stops, fatally, if a click leaves them);
  * an `open` is checked after it loads (right site? or a sign-in wall -> "needs you");
  * a step that can't find its element stops the run instead of clicking a guess.

A run stops at the first step that does not succeed. Skipping to the next ITEM of a batch is the
batch runner's job (a later phase), not this one's.
"""

import asyncio
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from aiba.playbooks import model as pbk
from aiba.playbooks import repair as playbook_repair
from aiba.playbooks import scrape as playbook_scrape
from aiba.playbooks.model import Playbook, Step
from aiba.playbooks.match import Candidate, Match, candidates_from_selector_map, closest, find

MAX_RUN_SECONDS = 900
MAX_OUTPUT = 20000
ROW_PAUSE = 1.5                 # seconds between the rows of a loop, so a site is not hit page after page
MAX_RUN_CELLS = 60000             # table cells a whole run keeps (a loop over many pages stays bounded)
MAX_SCREENSHOT = 400_000          # bytes; a bigger picture is not kept
_LOGIN_PATH = re.compile(r"^/(login|signin|sign-in|sign_in|uas/login|authwall|checkpoint)(/|$|\?)", re.IGNORECASE)
_NOT_AVAILABLE = re.compile(r"^Element index \d+ not available", re.IGNORECASE)


# ------------------------------------------------------------------------------- results
@dataclass
class StepResult:
    id: str
    kind: str
    note: str = ""
    status: str = "ok"            # ok | failed | needs_you | waiting (an approve step with nobody there)
    detail: str = ""
    seconds: float = 0.0
    matched: Optional[str] = None     # how the element was found (exact, stable, identity, ...)
    approval: Optional[str] = None    # preapproved | asked | None
    output: Optional[str] = None      # what an extract step read
    model: Optional[str] = None       # which model read it, with its tier: "claude-haiku-4-5 (Fast)"
    tokens: Optional[int] = None      # what that cost in tokens (prompt + answer), when the model reported it
    fatal: bool = False               # the run must stop even if later steps could go on
    meta: Optional[dict] = None       # for a waiting step: what is being asked (see PlaybookPlayer._approve)
    table: Optional[dict] = None      # what a scrape step read: {"columns", "rows", "truncated", "total"}
    repair: Optional[dict] = None     # set when this step only worked because it was repaired (see playbook_repair)


@dataclass
class RunResult:
    status: str = "done"           # done | failed | needs_you | waiting
    steps: list[StepResult] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    screenshot: Optional[bytes] = None      # what the page looked like when the run stopped (JPEG), if it did not finish
    waiting: Optional[dict] = None          # set when the run is parked at an approve step: the question and where
    repairs: list[dict] = field(default_factory=list)      # steps that were repaired during the run, so the user can update the playbook


    @property
    def seconds(self) -> float:
        return round(self.finished_at - self.started_at, 1)

    def to_dict(self) -> dict:
        steps = [{k: v for k, v in asdict(s).items() if k != "table"} for s in self.steps]     # tables live in `outputs`
        return {"status": self.status, "error": self.error, "seconds": self.seconds,
                "steps": steps, "outputs": self.outputs,
                "screenshot": self.screenshot is not None, "waiting": self.waiting, "repairs": self.repairs}


# ------------------------------------------------------------------------------- approvals
class StepApprover:
    """The `approver` the guard asks. The player says which step is running and whether the user
    pre-approved it; this decides the rest. Fails closed."""

    def __init__(self, inner: Any, attended: bool, clock: Callable[[], float] = time.monotonic) -> None:
        self.inner = inner
        self.attended = attended
        self._clock = clock
        self.preapproved = False        # set by the player for the step that is about to run
        self.last: Optional[str] = None     # preapproved | asked | denied | unattended
        self.last_title = ""
        self.waited = 0.0               # seconds spent waiting for the user (not counted against a run's time limit)

    def begin(self, preapproved: bool) -> None:
        self.preapproved, self.last, self.last_title = preapproved, None, ""

    async def ask(self, kind: str, title: str, detail: str = "", url: str = "") -> bool:
        self.last_title = title
        if kind == "approve" and self.preapproved:
            self.last = "preapproved"
            return True
        if not self.attended or self.inner is None:
            self.last = "unattended"
            return False
        began = self._clock()
        try:
            ok = await self.inner.ask(kind, title, detail, url)
        finally:
            self.waited += self._clock() - began
        self.last = "asked" if ok else "denied"
        return ok

    async def ask_text(self, title: str, detail: str = "", url: str = "") -> Optional[str]:
        """Ask the user to type something. None unless a person is here and answers."""
        if not self.attended or self.inner is None or not hasattr(self.inner, "ask_text"):
            self.last = "unattended"
            return None
        began = self._clock()
        try:
            text = await self.inner.ask_text(title, detail, url)
        finally:
            self.waited += self._clock() - began
        self.last = "asked" if text else "denied"
        return text


# ------------------------------------------------------------------------------- driver
class SessionDriver:
    """The real page: browser-use's BrowserSession, acting through GuardedTools."""

    def __init__(self, session: Any, tools: Any, *, sensitive_data: Optional[dict] = None,
                 llm: Any = None, action_timeout: float = 120.0,
                 llm_for: Optional[Callable[[str], Any]] = None) -> None:
        self.session, self.tools = session, tools
        self.sensitive_data, self.llm, self.action_timeout = sensitive_data, llm, action_timeout
        self.llm_for = llm_for                      # builds the model for a tier a step asks for (None: one model for all)
        self._by_tier: dict[str, Any] = {}
        self.last_model: Optional[str] = None       # who answered the last model call, for the run record
        self.last_tokens: Optional[int] = None      # and what it used, when the model reports that

    @property
    def has_llm(self) -> bool:
        return self.llm is not None

    async def candidates(self) -> list[Candidate]:
        state = await self.session.get_browser_state_summary(include_screenshot=False)
        return candidates_from_selector_map(state.dom_state.selector_map)

    async def url(self) -> str:
        return await self.session.get_current_page_url()

    async def screenshot(self) -> Optional[bytes]:
        """A small JPEG of the page, or None if it can't be taken (the page is gone, it takes too long)."""
        try:
            return await asyncio.wait_for(self.session.take_screenshot(format="jpeg", quality=55), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def scrape(self, row_selector: Optional[str], fields: list[dict], max_rows: Optional[int]) -> dict:
        """The raw cells of the table the page shows now (see playbook_scrape). Reads only; never acts on the page."""
        page = await self.session.get_current_page()
        return await playbook_scrape.read_page(page, row_selector, fields, max_rows)

    async def tabs(self) -> list[str]:
        """The ids (browser-use names a tab by the last 4 characters of its id) of the open tabs, in order."""
        state = await self.session.get_browser_state_summary(include_screenshot=False)
        return [str(t.target_id)[-4:] for t in state.tabs]

    async def settle(self, max_wait: float = 5.0) -> None:
        """Wait until the page has finished loading and stopped changing (same document state and
        element count on two looks in a row), or max_wait. A fixed pause is too short for a slow page
        and wasted time on a fast one."""
        page = await self.session.get_current_page()
        deadline = time.monotonic() + max_wait
        last = None
        steady = 0
        while time.monotonic() < deadline:
            try:
                state = await page.evaluate(
                    "() => document.readyState + ':' + document.getElementsByTagName('*').length")
            except Exception:
                return                          # the page is navigating or gone: nothing more to wait for here
            steady = steady + 1 if state == last and str(state).startswith("complete") else 0
            if steady >= 2:
                return
            last = state
            await asyncio.sleep(0.25)

    def _llm(self, tier: Optional[str]) -> Any:
        if not tier or self.llm_for is None:
            return self.llm
        if tier not in self._by_tier:
            self._by_tier[tier] = self.llm_for(tier)
        return self._by_tier[tier]

    @staticmethod
    def _who(llm: Any) -> Optional[str]:
        model = getattr(llm, "model", None)
        if not isinstance(model, str) or not model:
            return None
        label = {"fast": "Fast", "standard": "Standard", "strong": "Strong"}.get(getattr(llm, "tier", ""), "")
        return f"{model} ({label})" if label else model

    async def act(self, name: str, params: dict, tier: Optional[str] = None) -> Any:
        from typing import Union
        from pydantic import create_model
        from browser_use.agent.views import ActionModel
        reg = self.tools.registry.registry.actions[name]
        Model = create_model("PlaybookAction", __base__=ActionModel, **{name: (Union[reg.param_model, None], None)})
        action = Model(**{name: reg.param_model(**params)})
        llm = self._llm(tier)
        if name == "extract" and hasattr(llm, "reset_tokens"):
            llm.reset_tokens()
        try:
            return await asyncio.wait_for(
                self.tools.act(action, self.session, page_extraction_llm=llm,
                               sensitive_data=self.sensitive_data, action_timeout=self.action_timeout),
                timeout=self.action_timeout + 15)
        finally:
            self.last_model = self._who(llm) if name == "extract" else None
            used = getattr(llm, "tokens", None) if name == "extract" else None
            self.last_tokens = used if isinstance(used, int) and used > 0 else None


# ------------------------------------------------------------------------------- helpers
def host_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def describe(step: Step) -> str:
    return step.note or {"open": "Open a page", "click": "Click", "type": "Type", "select": "Choose an option",
                         "key": "Press keys", "scroll": "Scroll", "wait": "Wait", "back": "Go back",
                         "extract": "Read the page", "scrape": "Read a table", "foreach": "For each row", "ai": "AI step"}.get(step.kind, step.kind)


def _result_error(result: Any) -> str:
    """Why an action did not work, or '' if it did. browser-use reports a click on a missing element
    as a plain message rather than an error, so that is recognised here too."""
    err = getattr(result, "error", None)
    if err:
        return str(err)
    msg = str(getattr(result, "extracted_content", "") or "")
    return msg if _NOT_AVAILABLE.match(msg) else ""


def _step_target_text(step: Step) -> str:
    t = step.target
    if t is None:
        return "the element"
    return t.ax_name or t.attributes.get("aria-label") or t.attributes.get("placeholder") \
        or t.attributes.get("name") or f"a <{t.node_name}>"


# ------------------------------------------------------------------------------- the player
class PlaybookPlayer:
    def __init__(self, driver: Any, approver: StepApprover, *, notify: Optional[Callable[[str], Awaitable[None]]] = None,
                 settle: float = 0.7, find_timeout: float = 10.0, poll: float = 0.5,
                 max_seconds: float = MAX_RUN_SECONDS, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 remember: Optional[Callable[[str, str], None]] = None, repairer: Any = None) -> None:
        self._repairer = repairer                              # picks a replacement for a missing element (None: never repairs)
        self._remember = remember                              # called with (variable, value) for an answer to keep
        self.driver, self.approver, self._notify = driver, approver, notify
        self.settle, self.find_timeout, self.poll = settle, find_timeout, poll
        self.max_seconds, self._clock, self._sleep = max_seconds, clock, sleep

    async def _say(self, text: str) -> None:
        if self._notify:
            try:
                await self._notify(text)
            except Exception:
                pass

    # ----------------------------------------------------------------------------- run
    async def run(self, pb: Playbook, supplied: Optional[dict] = None,
                  approved: Optional[set[str]] = None, done: Optional[set[str]] = None,
                  answers: Optional[dict[str, str]] = None, skipped: Optional[set[str]] = None) -> RunResult:
        """Run the playbook from the top. `approved` holds the ids of approve steps the user has ALREADY said
        yes to in an earlier attempt of this same run: they pass at once (and grant the step after them), which
        is how a run that was parked while nobody was there carries on once the user answers.

        `done` holds the ids of steps that reach other people or can't be undone and ALREADY ran in that earlier
        attempt. They are skipped, never repeated: starting again from the top must not send a message twice.
        Everything else (opening pages, typing, clicking around) is simply done again.

        `answers` maps the id of an `input` step to the text the user already typed for it. `skipped` holds ids
        of steps to leave out because the user did not answer in time and the question said to carry on."""
        result = RunResult(started_at=self._clock())

        def finish(status: str, error: str = "") -> RunResult:
            result.status, result.error, result.finished_at = status, error, self._clock()
            return result

        problems = pbk.validate(pb)
        if problems:
            return finish("failed", "This playbook can't run: " + "; ".join(problems[:3]))
        values, errors = pbk.bind_values(pb, supplied or {})
        if errors:
            return finish("failed", "Check the inputs: " + "; ".join(errors))

        await self._begin(pb, values, approved, done, answers, skipped)
        total = len(pb.steps)
        i = 0
        while i < total:
            step = pb.steps[i]
            ended = await self._exec(result, step, i + 1, total, finish)
            if ended is not None:
                return ended
            if step.kind == "foreach":
                ended = await self._run_loop(result, pb, i, total, finish)
                if ended is not None:
                    return ended
                i += step.body or 0
            i += 1
        return finish("done")

    async def _begin(self, pb: Playbook, values: dict, approved: Optional[set[str]] = None, done: Optional[set[str]] = None,
                     answers: Optional[dict[str, str]] = None, skipped: Optional[set[str]] = None) -> None:
        """Set up the state of one run. Split from run() so a drafting walk can use the real step logic one step at a time."""
        self._preapproved_playbook = pbk.is_approved(pb)
        # A playbook that signs in by itself (a stored secret typed into a field) or asks the user to
        # (a handoff step) has its own answer to a sign-in page, so the generic wall check stays out of it.
        self._logs_in = any((s.kind == "type" and s.text and pbk.SECRET_RE.search(s.text)) or s.kind == "handoff"
                            for s in pb.steps)
        self._values = values
        self._approved_cps = set(approved or ())
        self._skip = set(done or ())
        self._answers = dict(answers or {})
        self._unanswered = set(skipped or ())
        self._did: set[str] = set(done or ())       # risky steps that have run, in this attempt or an earlier one
        self._granted: set[str] = set()         # steps the user said yes to at an approve step, for THIS run only
        self._typed = ""                        # the last text typed (never a secret), shown at the next approve step
        self._steps = pb.steps
        self._prev_high = False                 # was the previous step an element step matched with high confidence?
        self._variables = {v.name: v for v in pb.variables}
        allowed = pbk.compute_domains(pb)     # recomputed: the stored list is not what the approval covers
        if not allowed:                    # no open step: the page the run starts on is the only site
            start = host_of(await self._safe_url())
            allowed = [start] if start else []
        self._allowed = allowed

        self._left_out = False                                        # a step was skipped because nobody answered
        self._tables: dict[str, dict] = {}                            # what each scrape step read, for a foreach step
        self._pending_loop: Optional[dict] = None
        self._repair_on = bool(pb.repair) and self._repairer is not None      # the playbook (and so the user's approval) allows it
        self._repairs_used = 0
        self._scrape_override: dict[str, tuple[Optional[str], list[dict]]] = {}      # selectors a repair found, for the rest of the run
        self._loop_ctx: Optional[tuple[str, str]] = None
        self._cells_left = MAX_RUN_CELLS

    async def _exec(self, result: RunResult, step: Step, n: int, total: int, finish: Callable[..., RunResult],
                    row: Optional[tuple[int, int]] = None) -> Optional[RunResult]:
        """Run one step and judge it. Returns the finished result if the run must end here, else None."""
        if self._clock() - result.started_at - self.approver.waited > self.max_seconds:
            return finish("failed", f"Stopped: the run took longer than {int(self.max_seconds)} seconds.")
        where = f" (row {row[0]} of {row[1]})" if row else ""
        await self._say(f"▶ Step {n}/{total}{where}: {describe(step)}")
        t0 = self._clock()
        try:
            if step.id in self._unanswered:
                self._left_out = True
                res = StepResult(step.id, step.kind, detail="Skipped: nobody answered the question in time.")
            elif step.id in self._skip:
                res = StepResult(step.id, step.kind, detail="Already done before the question, so not repeated.")
            else:
                res = await self._step(step)
        except asyncio.CancelledError:
            raise
        except Exception as e:                                     # a bug must not become a silent skip
            res = StepResult(step.id, step.kind, describe(step), "failed",
                             f"Unexpected error: {type(e).__name__}: {e}")
        res.id, res.kind, res.note = step.id, step.kind, describe(step) + where
        self._track_typed(step, res)
        if step.kind != "approve":              # asking the user changes nothing on the page: focus is where it was
            self._prev_high = res.status == "ok" and (res.matched or "").endswith("(high)")
        res.seconds = round(self._clock() - t0, 2)
        result.steps.append(res)
        if res.repair:                                          # only set on a step that worked
            result.repairs.append({**res.repair, "step_id": step.id, "note": describe(step)})
            await self._say(f"🔧 Repaired step {n}: {res.repair.get('summary', 'found a replacement')}")

        if res.status == "ok" and step.ask and step.id not in self._skip:
            self._did.add(step.id)
        if res.status == "ok":
            if res.table is not None:
                self._tables[step.id] = res.table
                result.outputs.append({"step_id": step.id, "query": (step.note or "Table") + where, "text": res.output or "",
                                       "table": self._within_budget(res.table)})
            elif res.output is not None:
                result.outputs.append({"step_id": step.id, "query": step.query or "", "text": res.output})
            left = await self._left_approved_sites()
            if left:
                res.status, res.fatal = "failed", True
                res.detail = f"Stopped: the page went to {left}, which this playbook is not approved to visit."
            else:
                await self._check_sign_in(res)
        if self._left_out and res.status in ("failed", "needs_you") and "was skipped" not in res.detail:
            res.detail += " (An earlier step was left out because nobody answered, which may be why.)"
        if res.status == "waiting":
            await self._say(f"⏸ Step {n} is waiting for you: {res.detail}")
            result.screenshot = await self._picture()
            result.waiting = {**(res.meta or {}), "step_id": step.id, "index": n - 1, "done": sorted(self._did)}
            return finish("waiting", f"Step {n} ({describe(step)}): waiting for you.")
        if res.status != "ok":
            await self._say(f"✖ Step {n} stopped the run: {res.detail}")
            result.screenshot = await self._picture()
            return finish("needs_you" if res.status == "needs_you" else "failed",
                          f"Step {n}{where} ({describe(step)}): {res.detail}")
        return None

    async def _run_loop(self, result: RunResult, pb: Playbook, at: int, total: int,
                        finish: Callable[..., RunResult]) -> Optional[RunResult]:
        """Run the steps after a foreach step once for each value it found. A row whose steps fail stops the run, or
        (when the step says so) is skipped; a sign-in wall, a refused action or running out of time always stops it."""
        loop, self._pending_loop = self._pending_loop, None
        step = pb.steps[at]
        body = pb.steps[at + 1: at + 1 + (step.body or 0)]
        values = loop["values"] if loop else []
        skipped_rows = 0
        for r, value in enumerate(values, start=1):
            self._values[step.variable] = value
            self._loop_ctx = (step.column or step.variable, value)
            if r > 1:
                await self._sleep(ROW_PAUSE)
            await self._say(f"↻ Row {r} of {len(values)}: {value[:80]}")
            for k, inner in enumerate(body, start=at + 2):
                before = len(result.steps)
                ended = await self._exec(result, inner, k, total, finish, row=(r, len(values)))
                if ended is None:
                    continue
                last = result.steps[-1] if len(result.steps) > before else None
                if (step.on_failure == "skip" and ended.status == "failed" and last is not None
                        and not last.fatal and not await self._left_approved_sites()):       # a page off the approved sites ends the run
                    result.status, result.error, result.finished_at, result.screenshot = "done", "", 0.0, None
                    skipped_rows += 1
                    break                                              # leave this row, carry on with the next
                self._values.pop(step.variable, None)
                self._loop_ctx = None
                return ended
        self._values.pop(step.variable, None)
        self._loop_ctx = None
        self._prev_high = False
        if skipped_rows:
            await self._say(f"Left out {skipped_rows} row{'' if skipped_rows == 1 else 's'} whose steps failed.")
        return None

    def _within_budget(self, table: dict) -> dict:
        """A run keeps only so many cells in all, so a loop over many pages can't grow a record without limit."""
        cols = max(1, len(table["columns"]))
        keep = max(0, self._cells_left // cols)
        if len(table["rows"]) <= keep:
            self._cells_left -= len(table["rows"]) * cols
            return table
        self._cells_left = 0
        return {**table, "rows": table["rows"][:keep], "truncated": True}

    async def _foreach(self, step: Step) -> StepResult:
        """Read the values of the loop and check every one BEFORE any is used: a value taken from a page is only
        ever used if it is acceptable for the loop variable (for a web address, on a site the playbook may visit)."""
        table = self._tables.get(step.source or "")
        var = self._variables.get(step.variable or "")
        if table is None or var is None:
            return StepResult(step.id, step.kind, status="failed", detail="The step that reads the table did not run, so there is nothing to go through.")
        names = [c.lower() for c in table["columns"]]
        if (step.column or "").lower() not in names:
            return StepResult(step.id, step.kind, status="failed", detail=f"The table has no column '{step.column}'.")
        at = names.index((step.column or "").lower())
        found = list(dict.fromkeys(str(r[at]).strip() for r in table["rows"] if at < len(r) and str(r[at]).strip()))
        if not found:
            return StepResult(step.id, step.kind, status="failed", detail=f"Column '{step.column}' has no values.")
        cap = step.max_rows or pbk.DEFAULT_LOOP_ROWS
        good: list[str] = []
        why = ""
        left = 0
        for v in found:
            problems = pbk.value_problems(var, v)
            if problems:
                left += 1
                why = why or f"{v[:60]} {problems[0]}"
            elif len(good) < cap:
                good.append(v)
        if not good:
            return StepResult(step.id, step.kind, status="failed", detail=f"None of the {len(found)} values can be used ({why}).")
        extra = len(found) - left - len(good)
        notes = [f"{left} left out ({why})" if left else "", f"{extra} more than the limit of {cap} left out" if extra > 0 else ""]
        self._pending_loop = {"values": good}
        detail = f"{len(good)} row{'' if len(good) == 1 else 's'} to go through" + ("; " + "; ".join(n for n in notes if n) if any(notes) else "")
        return StepResult(step.id, step.kind, detail=detail)

    # ---------------------------------------------------------------------------- steps
    async def _step(self, step: Step) -> StepResult:
        kind = step.kind
        self.approver.begin(False)             # nothing pre-approved unless a branch below says so
        if kind == "open":
            return await self._open(step)
        if kind == "search":
            return await self._search(step)
        if kind == "tab":
            return await self._tab(step)
        if kind == "handoff":
            return await self._handoff(step)
        if kind == "approve":
            return await self._approve(step)
        if kind == "input":
            return await self._input(step)
        if kind == "back":
            return await self._act_step(step, "go_back", {})
        if kind == "wait":
            await self._sleep(min(float(step.seconds or 0), 300.0))
            return StepResult(step.id, kind)
        if kind == "key":
            # Enter/Space act on whatever has focus. That is only the control the user approved if the
            # step before it found ITS element with certainty; otherwise the user is asked.
            self.approver.begin(step.ask and (self._preapproved_playbook or step.id in self._granted) and self._prev_high)
            keys = pbk.render(step.keys, self._values) or ""
            return await self._act_step(step, "send_keys", {"keys": keys})
        if kind == "scroll" and step.target is None:
            return await self._act_step(step, "scroll", {"down": step.down, "pages": step.pages})
        if kind in ("click", "type", "select", "scroll"):
            return await self._on_element(step)
        if kind == "scrape":
            return await self._scrape(step)
        if kind == "foreach":
            return await self._foreach(step)
        if kind == "extract":
            if not self.driver.has_llm:
                return StepResult(step.id, kind, status="failed",
                                  detail="This step reads the page with an AI model, and none is set up (see Settings).")
            query = pbk.render(step.query, self._values) or ""
            res = await self._act_step(step, "extract", {"query": query}, tier=step.tier)
            if res.status == "ok":
                res.output = (res.output or "")[:MAX_OUTPUT]
            return res
        return StepResult(step.id, kind, status="failed",
                          detail="This step needs an AI to decide what to do, which playbook runs can't do yet.")

    async def _open(self, step: Step) -> StepResult:
        return await self._navigate(step, (pbk.render(step.url, self._values) or "").strip(), self._expected_hosts(step))

    async def _search(self, step: Step) -> StepResult:
        # The address is built from the engine and the URL-encoded query: a search value can't change the site.
        url = pbk.search_url(step.engine or "duckduckgo", pbk.render(step.query, self._values) or "")
        return await self._navigate(step, url, [])

    async def _navigate(self, step: Step, url: str, expected: list[str]) -> StepResult:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return StepResult(step.id, step.kind, status="failed", fatal=True, detail=f"'{url[:80]}' is not a web address.")
        if not pbk.host_allowed(parsed.hostname, self._allowed):
            return StepResult(step.id, step.kind, status="failed", fatal=True,
                              detail=f"{parsed.hostname} is not one of this playbook's approved sites.")
        self.approver.begin(False)
        res = await self._act_step(step, "navigate", {"url": url, "new_tab": step.new_tab})
        if res.status != "ok":
            return res
        final = await self._safe_url()
        if expected and not pbk.host_allowed(host_of(final), expected):
            return StepResult(step.id, step.kind, status="failed",
                              detail=f"Expected {', '.join(expected)} but the page is {host_of(final) or final or 'blank'}.")
        return res

    async def _scrape(self, step: Step) -> StepResult:
        """Read a table off the page: no AI, no clicking. A page that is still loading is given a few seconds;
        a selector that finds nothing at all is a failure (the page changed), never an empty success."""
        read = getattr(self.driver, "scrape", None)
        if read is None:
            return StepResult(step.id, step.kind, status="failed", detail="This browser can't read a table off a page.")
        row_selector, fields = self._scrape_override.get(step.id) or (step.row_selector, [f.model_dump() for f in (step.fields or [])])
        row_mode = bool(row_selector)
        deadline = self._clock() + self.find_timeout
        repaired: Optional[dict] = None
        while True:
            try:
                raw = await asyncio.wait_for(read(row_selector, fields, step.max_rows), timeout=60)
            except asyncio.CancelledError:
                raise
            except playbook_scrape.ScrapeError as e:
                return StepResult(step.id, step.kind, status="failed", detail=str(e))
            except asyncio.TimeoutError:
                return StepResult(step.id, step.kind, status="failed", detail="Reading the page took too long.")
            table, problem = playbook_scrape.build_table(fields, raw, row_mode)
            if table is not None:
                break
            if self._clock() >= deadline:
                if repaired is None and self._can_repair(step) and step.id not in self._scrape_override:
                    self._repairs_used += 1
                    try:
                        fix = await self._repairer.selectors(step, await self._safe_url())
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        fix = None
                    if fix:
                        row_selector, fields, row_mode = fix["row_selector"], fix["fields"], bool(fix["row_selector"])
                        self._scrape_override[step.id] = (row_selector, fields)
                        repaired = {"kind": "scrape", "was": f'{step.row_selector or "the page"}: ' + ", ".join(f.selector for f in step.fields or []),
                                    "now": {"row_selector": row_selector, "fields": fields},
                                    "why": "The old selectors found nothing, so new ones were found for the same columns.",
                                    "summary": "the table's selectors stopped matching, so new ones were found for the same columns"}
                        deadline = self._clock() + self.find_timeout
                        continue
                return StepResult(step.id, step.kind, status="failed", detail=problem)
            await self._sleep(self.poll)
        ctx = self._loop_ctx
        if ctx and ctx[0].lower() not in [c.lower() for c in table["columns"]]:      # say which row of the loop each line came from
            table = {**table, "columns": [ctx[0], *table["columns"]], "rows": [[ctx[1], *r] for r in table["rows"]]}
        n = len(table["rows"])
        more = f" (the page has {table['total']}; the rest were left out)" if table["truncated"] else ""
        return StepResult(step.id, step.kind, output=f"{n} row{'' if n == 1 else 's'} read{more}", table=table, repair=repaired)

    async def _tab(self, step: Step) -> StepResult:
        ids = await self.driver.tabs()
        idx = step.tab_index or 0
        if idx >= len(ids):
            return StepResult(step.id, "tab", status="failed",
                              detail=f"There is no tab number {idx + 1} (only {len(ids)} open).")
        return await self._act_step(step, "close" if step.close_tab else "switch", {"tab_id": ids[idx]})

    async def _approve(self, step: Step) -> StepResult:
        """A checkpoint: the user is asked before the step that follows. They see what is about to happen and,
        if they typed something just before, exactly what. A yes lets the NEXT step (and only it) through, and
        even then a risky click still needs its element found with certainty. With nobody there the run is
        parked ("waiting"); silence is never a yes."""
        message = pbk.render(step.message, self._values) or ""
        ids = [x.id for x in self._steps]
        at = ids.index(step.id)
        later = self._steps[at + 1] if at + 1 < len(ids) else None
        detail = f'Typed just before: "{self._typed[:300]}"' if self._typed else ""
        url = await self._safe_url()
        if step.id in self._approved_cps:                          # answered earlier in this run: yes
            if later is not None:
                self._granted.add(later.id)
            return StepResult(step.id, "approve", approval="asked")
        if self.approver.attended and self.approver.inner is not None:
            if await self.approver.ask("approve", message, detail, url):
                if later is not None:
                    self._granted.add(later.id)
                return StepResult(step.id, "approve", approval="asked")
            return StepResult(step.id, "approve", status="needs_you",
                              detail=f"Not approved (you said no, or nobody answered in time): {message}")
        return StepResult(step.id, "approve", status="waiting", detail=message,
                          meta={"kind": "approve", "message": message, "detail": detail, "url": url,
                                "timeout_minutes": step.timeout_minutes or pbk.DEFAULT_APPROVAL_MINUTES,
                                "on_timeout": step.on_timeout or "cancel",
                                "guarded": later.id if later is not None else ""})

    async def _input(self, step: Step) -> StepResult:
        """Ask the user to TYPE a value the rest of the run needs (a code, a name, a number). It is checked like
        any input, kept only for this run unless the user chose to remember it, and never written to the log. With nobody there the run is parked."""
        message = pbk.render(step.message, self._values) or ""
        var = self._variables.get(step.variable or "")
        if var is None:                                           # validation prevents this; never guess
            return StepResult(step.id, "input", status="failed", detail=f"'{step.variable}' is not a variable.")
        url = await self._safe_url()
        value = self._answers.get(step.id)
        asked = False
        keep = False                                              # the user ticked "remember this for next runs"
        if value is None and self.approver.attended and self.approver.inner is not None:
            value, asked = await self.approver.ask_text(message, "", url), True
            if value is None:
                return StepResult(step.id, "input", status="needs_you", detail=f"No answer was given: {message}")
            keep = bool(getattr(value, "remember", False))
        if value is None:
            return StepResult(step.id, "input", status="waiting", detail=message,
                              meta={"kind": "input", "message": message, "detail": "", "url": url, "variable": var.name,
                                    "timeout_minutes": step.timeout_minutes or pbk.DEFAULT_APPROVAL_MINUTES,
                                    "on_timeout": "cancel", "guarded": ""})
        value = value.strip() if var.type == "number" else value
        problems = pbk.value_problems(var, value)
        if problems:
            return StepResult(step.id, "input", status="failed", detail=f"That answer {problems[0]}.")
        self._values[var.name] = value
        if asked and keep and self._remember is not None:
            try:
                self._remember(var.name, str(value))
            except Exception:                                  # failing to remember must never fail the run
                pass
        return StepResult(step.id, "input", approval="asked" if asked or step.id in self._answers else None)

    async def _handoff(self, step: Step) -> StepResult:
        """A step only a person can do (sign in, a CAPTCHA, typing a password). Attended: ask, and carry
        on when they say they are done. Unattended: the run stops, "needs you"."""
        message = step.message or "Do the manual step in the browser, then continue."
        if await self.approver.ask("handoff", "Your turn", message, await self._safe_url()):
            return StepResult(step.id, "handoff", approval="asked")
        if self.approver.last == "unattended":
            return StepResult(step.id, "handoff", status="needs_you", detail=f"Needs you: {message}")
        return StepResult(step.id, "handoff", status="needs_you", detail=f"You didn't continue: {message}")

    async def _check_sign_in(self, res: StepResult) -> None:
        """After any step: if the site now shows a sign-in wall (and this playbook doesn't sign in itself),
        a person has to deal with it. Attended: ask them and check again. Unattended: stop."""
        if self._logs_in:
            return
        url = await self._safe_url()
        if not _LOGIN_PATH.match(urlparse(url).path or ""):
            return
        host = host_of(url)
        self.approver.begin(False)
        if await self.approver.ask("handoff", "Sign in needed",
                                   f"{host} is asking you to sign in. Sign in in the browser, then continue.", url):
            if not _LOGIN_PATH.match(urlparse(await self._safe_url()).path or ""):
                res.approval = "asked"
                return                                          # they signed in: carry on
            res.status, res.detail = "needs_you", f"{host} is still asking you to sign in."
            return
        res.status = "needs_you"
        res.detail = f"{host} is asking you to sign in. Sign in in the browser, then run again."

    def _expected_hosts(self, step: Step) -> list[str]:
        whole = pbk.PLACEHOLDER_RE.fullmatch((step.url or "").strip())
        if whole and whole.group(1) in self._variables:
            return pbk._variable_hosts(self._variables[whole.group(1)])      # the variable decides where it may land
        return list(step.check.hosts) if step.check else []

    async def _on_element(self, step: Step) -> StepResult:
        text = pbk.render(step.text, self._values) if step.kind in ("type", "select") else None
        repaired: Optional[dict] = None
        for attempt in (1, 2):
            match = await self._resolve(step)
            if match is None and repaired is None and self._can_repair(step):
                match, repaired = await self._repair_element(step)
            if match is None:
                return StepResult(step.id, step.kind, status="failed",
                                  detail=f"Couldn't find {_step_target_text(step)} on the page." + await self._hint(step))
            self.approver.begin(step.ask and (self._preapproved_playbook or step.id in self._granted)
                                and match.confidence == "high")
            if step.kind == "click":
                name, params = "click", {"index": match.index}
            elif step.kind == "type":
                name, params = "input", {"index": match.index, "text": text or "", "clear": step.clear}
            elif step.kind == "select":
                name, params = "select_dropdown", {"index": match.index, "text": text or ""}
            else:
                name, params = "scroll", {"down": step.down, "pages": step.pages, "index": match.index}
            res = await self._act_step(step, name, params, matched=f"{match.level} ({match.confidence})")
            # The page can change between reading it and acting on it: read it again once.
            if res.status == "failed" and "not available" in res.detail and attempt == 1:
                continue
            if repaired is not None and res.status == "ok":
                res.repair = repaired
            return res
        return res                                                       # pragma: no cover

    def _can_repair(self, step: Step) -> bool:
        """Only when the playbook allows it, within the per-run cap, and never for something that sends or posts
        or types a stored secret: a wrong guess there is not a small mistake."""
        if not self._repair_on or self._repairs_used >= playbook_repair.MAX_REPAIRS or step.ask:
            return False
        if step.kind == "type" and pbk.SECRET_RE.search(step.text or ""):
            return False
        return True

    async def _repair_element(self, step: Step) -> tuple[Optional[Match], Optional[dict]]:
        """Ask for a replacement among the few elements that look most like the missing one. It is only ever a
        MEDIUM match, so nothing the user pre-approved is waved through on the strength of a guess."""
        self._repairs_used += 1
        try:
            options = playbook_repair.shortlist(step, await self.driver.candidates())
            host = host_of(await self._safe_url()) or "a blank page"
            got = await self._repairer.choose(step, options, host)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None, None
        if got is None:
            return None, None
        cand, why = got
        fp = playbook_repair.fingerprint_of(cand)
        label = (cand.ax_name or cand.attributes.get("aria-label") or cand.node_name)[:60]
        return (Match(index=cand.index, level="repaired", confidence="medium"),
                {"kind": "element", "was": playbook_repair.describe(step), "now": fp.model_dump(),
                 "why": why[:200], "summary": f'{_step_target_text(step)} was not on the page, so "{label}" was used instead'})

    async def _hint(self, step: Step) -> str:
        """After a miss: where the run is, and what on the page looks most like the missing element."""
        try:
            host = host_of(await self._safe_url()) or "a blank page"
            near = closest(step.target, await self.driver.candidates())
        except asyncio.CancelledError:
            raise
        except Exception:
            return ""
        names = [f'"{(c.ax_name or c.attributes.get("aria-label") or c.attributes.get("placeholder") or c.node_name)[:50]}"' for c in near]
        if names:
            return f" The page is {host}. The closest elements on it: {', '.join(names)}."
        return f" The page is {host} and nothing on it looks like that element, so check it is the right page."

    async def _resolve(self, step: Step) -> Optional[Match]:
        deadline = self._clock() + self.find_timeout
        while True:
            match = find(step.target, await self.driver.candidates())
            if match is not None:
                return match
            if self._clock() >= deadline:
                return None
            await self._sleep(self.poll)

    async def _act_step(self, step: Step, name: str, params: dict, matched: Optional[str] = None,
                        tier: Optional[str] = None) -> StepResult:
        # `tier` is passed only for a step that chose one, so a driver written before tiers existed keeps working
        result = await (self.driver.act(name, params, tier=tier) if tier else self.driver.act(name, params))
        err = _result_error(result)
        if err:
            return self._failure(step, err, matched)
        await self._settle()
        out = StepResult(step.id, step.kind, matched=matched, approval=self.approver.last
                         if self.approver.last in ("preapproved", "asked") else None)
        if name == "extract":
            out.output = str(getattr(result, "extracted_content", "") or "")
            out.model = getattr(self.driver, "last_model", None)
            out.tokens = getattr(self.driver, "last_tokens", None)
        return out

    def _failure(self, step: Step, err: str, matched: Optional[str]) -> StepResult:
        """Turn a refused or failed action into the right status, in plain words."""
        last = self.approver.last
        if err.startswith("Blocked by safety policy"):
            return StepResult(step.id, step.kind, status="failed", fatal=True, matched=matched,
                              detail=err.replace(" Do not retry this. Continue without it or report it.", ""))
        if last == "unattended":
            return StepResult(step.id, step.kind, status="needs_you", matched=matched,
                              detail=f"Needs your approval: {self.approver.last_title}. "
                                     "Approve the playbook (with this step) or run it while you are here.")
        if last == "denied":
            return StepResult(step.id, step.kind, status="needs_you", matched=matched,
                              detail=f"You didn't approve: {self.approver.last_title}")
        return StepResult(step.id, step.kind, status="failed", matched=matched, detail=err[:300])

    # ----------------------------------------------------------------------------- pages
    async def _settle(self) -> None:
        """A short pause (the page reacts to the action), then wait for it to stop changing."""
        await self._sleep(self.settle)
        wait = getattr(self.driver, "settle", None)
        if wait is not None:
            try:
                await wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def _safe_url(self) -> str:
        try:
            return await self.driver.url() or ""
        except Exception:
            return ""

    def _track_typed(self, step: Step, res: StepResult) -> None:
        if res.status != "ok":
            return
        if step.kind == "type":
            text = pbk.render(step.text, self._values) or ""
            if pbk.SECRET_RE.search(step.text or ""):
                self._typed = ""
            elif any(self._variables.get(n) is not None and self._variables[n].runtime for n in pbk.PLACEHOLDER_RE.findall(step.text or "")):
                self._typed = "(what you typed in when asked)"        # an answer may be a code or a password: not shown again
            else:
                self._typed = text
        elif step.kind in ("open", "search", "click", "key", "tab", "back", "select"):
            self._typed = ""

    async def _picture(self) -> Optional[bytes]:
        """What the page looked like when the run stopped, for the failure report. Never raises."""
        take = getattr(self.driver, "screenshot", None)
        if take is None:
            return None
        try:
            data = await take()
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        return data if isinstance(data, (bytes, bytearray)) and 0 < len(data) <= MAX_SCREENSHOT else None

    async def _left_approved_sites(self) -> str:
        """The host the page is on if it is outside the approved sites, else ''."""
        host = host_of(await self._safe_url())
        if not host or not self._allowed:
            return ""
        return "" if pbk.host_allowed(host, self._allowed) else host


# ------------------------------------------------------------------------------- summary
def _table_text(table: dict, show: int = 5) -> str:
    """The first rows of a table as plain lines, for the chat."""
    lines = [" | ".join(table["columns"])]
    lines += [" | ".join(r) for r in table["rows"][:show]]
    if len(table["rows"]) > show:
        lines.append(f"... and {len(table['rows']) - show} more (see the run history).")
    return "\n".join(lines)


def summarize(pb: Playbook, run: RunResult) -> str:
    """What the user reads in the chat when a run ends."""
    done = sum(1 for s in run.steps if s.status == "ok")
    total = len(pb.steps)
    if run.status == "done":
        head = f'Ran "{pb.name}": all {total} step{"" if total == 1 else "s"} done in {run.seconds:g} s.'
    elif run.status == "needs_you":
        head = f'"{pb.name}" stopped: it needs you. {run.error}'
    elif run.status == "waiting":
        head = f'"{pb.name}" is waiting for you: {(run.waiting or {}).get("message", "an approval")}'
    else:
        head = f'"{pb.name}" stopped after {done} of {total} steps. {run.error}'
    parts = [head]
    for out in run.outputs:
        table = out.get("table")
        if table:
            parts.append(f"\n{out['query'] or 'Table'}: {out['text']}\n{_table_text(table)}")
        else:
            parts.append(f"\n{out['query'] or 'Result'}:\n{out['text']}")
    return "\n".join(parts)
