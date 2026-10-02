"""
Three model slots (Fast, Standard, Strong) and a router that picks one per job.

Why: most AI calls in this app are small and routine (reading a page for a few fields, summarising old
steps) and a cheap, quick model does them as well as an expensive one; a few are hard (drafting a
playbook, a retry after the first attempt was judged incomplete) and deserve the best model. Paying the
top price for every call, or the bottom price for the hard ones, are both wrong.

  Fast      low latency, cheap       reading a page, summaries, small repairs
  Standard  the model in Settings    the agent's own steps, the reviewer (judge)
  Strong    slowest, most capable    drafting a playbook, a retry that follows an incomplete attempt

How a pick is made (no extra AI call, ever: it is a fixed table):
  * the table below gives the FIRST tier for each job;
  * a slot the user has not set, or whose API key is missing, uses the Standard model instead (so with
    one provider key everything still works, it just all runs on one model);
  * "router off" (LLM_ROUTER=off) sends every job to Standard, the way the app worked before tiers;
  * a call that FAILS on a tier (provider error, bad structured answer, rate limit) is retried once on the
    next tier up (Fast -> Standard -> Strong), see TieredLLM. Escalation is bounded by the three slots and is
    logged, so it can't loop or spend without limit.

Safety is unaffected: the guard, approvals and secret rules do not depend on which model answers.

Every call is counted (tier, model, prompt and completion tokens) in a small ledger so the user can see
where their usage went. Tokens, not prices: prices change and differ by account.
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import providers
from chat_store import _atomic_write

log = logging.getLogger(__name__)

TIERS = ("fast", "standard", "strong")
TIER_LABEL = {"fast": "Fast", "standard": "Standard", "strong": "Strong"}
ROUTER_ENV = "LLM_ROUTER"                          # "auto" (default) | "off"
SLOT_ENV = {"fast": ("LLM_FAST_PROVIDER", "LLM_FAST_MODEL"),
            "strong": ("LLM_STRONG_PROVIDER", "LLM_STRONG_MODEL")}

# Job -> the first tier to try. Anything not listed is Standard.
PURPOSES: dict[str, tuple[str, str]] = {
    "agent":            ("standard", "The agent's own steps in a chat task"),
    "judge":            ("standard", "The reviewer that checks a finished task"),
    "extract":          ("fast",     "Reading a page for the data you asked about"),
    "compaction":       ("fast",     "Shortening the history of a long task"),
    "playbook_extract": ("fast",     "A playbook step that reads the page"),
    "repair":           ("fast",     "Finding a button again after a page changed"),
    "retry":            ("strong",   "A second attempt after the reviewer said it was incomplete"),
    "draft":            ("strong",   "Drafting a playbook from a description"),
    "selectors":        ("standard", "Suggesting the selectors for a table you describe in words"),
}

# What "Use suggested" fills in: a cheap model for Fast, the best for Strong, per provider.
SUGGESTED: dict[str, dict[str, str]] = {
    "claude":   {"fast": "claude-haiku-4-5-20251001", "strong": "claude-opus-5-5"},
    "openai":   {"fast": "gpt-5-mini", "strong": "gpt-5"},
    "gemini":   {"fast": "gemini-2.5-flash", "strong": "gemini-2.5-pro"},
    "deepseek": {"fast": "deepseek-v4-flash", "strong": "deepseek-v4-pro"},
}

CLASS_ESCALATION_PAUSE = 0.0                        # seconds before trying the next tier (tests keep it zero)
FLUSH_EVERY = 20.0                                  # seconds between writes of the usage file while it is busy
KEEP_DAYS = 35


# ----------------------------------------------------------------------------------- slots
@dataclass(frozen=True)
class Slot:
    tier: str
    provider_id: str
    model: str
    explicit: bool = False          # the user chose this slot (Standard always counts as chosen)
    usable: bool = True             # its provider has an API key
    collapsed: bool = False         # not used: this slot runs on the Standard model instead
    reason: str = ""                # why it collapsed, short ("not set"); the screen adds "uses the Standard model"

    @property
    def effective_tier(self) -> str:
        """The tier whose model really answers: a slot that collapsed answers with the Standard model."""
        return "standard" if self.collapsed else self.tier


@dataclass(frozen=True)
class Pick:
    purpose: str
    tier: str                       # the tier the table asked for
    slot: Slot                      # the slot that will really answer (may be Standard if it collapsed)

    @property
    def provider_id(self) -> str:
        return self.slot.provider_id

    @property
    def model(self) -> str:
        return self.slot.model


def _has_key(provider_id: str, env: Mapping[str, str]) -> bool:
    provider = providers.BY_ID.get(provider_id)
    return bool(provider and (env.get(provider.key_env) or "").strip())


def _env() -> Mapping[str, str]:
    import os
    return os.environ


def router_on(env: Optional[Mapping[str, str]] = None) -> bool:
    return ((env or _env()).get(ROUTER_ENV) or "auto").strip().lower() != "off"


def read_slots(env: Optional[Mapping[str, str]] = None) -> dict[str, Slot]:
    """The three slots as the settings describe them right now."""
    env = env if env is not None else _env()
    std_provider = providers.resolve_provider_id(env.get("LLM_PROVIDER")) or providers.DEFAULT_PROVIDER
    std_model = providers.strip_model_prefix(env.get("LLM_MODEL") or "") or providers.BY_ID[std_provider].default_model
    standard = Slot("standard", std_provider, std_model, explicit=True, usable=_has_key(std_provider, env))
    slots = {"standard": standard}
    for tier, (provider_key, model_key) in SLOT_ENV.items():
        raw_provider = (env.get(provider_key) or "").strip()
        raw_model = providers.strip_model_prefix(env.get(model_key) or "")
        if not raw_provider and not raw_model:
            slots[tier] = Slot(tier, std_provider, std_model, collapsed=True, usable=standard.usable,
                               reason="not set")
            continue
        provider_id = providers.resolve_provider_id(raw_provider) if raw_provider else std_provider
        if provider_id is None:
            slots[tier] = Slot(tier, std_provider, std_model, explicit=True, usable=standard.usable, collapsed=True,
                               reason=f"'{raw_provider}' is not a known provider")
            continue
        model = raw_model or providers.BY_ID[provider_id].default_model
        if not _has_key(provider_id, env):
            slots[tier] = Slot(tier, std_provider, std_model, explicit=True, usable=False, collapsed=True,
                               reason=f"there is no {providers.BY_ID[provider_id].label} API key")
            continue
        slots[tier] = Slot(tier, provider_id, model, explicit=True)
    return slots


def pick(purpose: str, env: Optional[Mapping[str, str]] = None, tier: Optional[str] = None) -> Pick:
    """Which model should do this job? (A fixed table, no AI involved, unless the user chose a `tier`.)"""
    slots = read_slots(env)
    want = tier if tier in TIERS else PURPOSES.get(purpose, ("standard", ""))[0]
    if not router_on(env):
        return Pick(purpose, want, slots["standard"])
    return Pick(purpose, want, slots[want])


def chain(purpose: str, env: Optional[Mapping[str, str]] = None, tier: Optional[str] = None) -> list[Pick]:
    """The models to try for this job, in order: its own tier, then each tier above it. Slots that
    resolve to the same model are listed once, so a setup with one model has a chain of one and never
    "escalates" to the model that just failed. With the router off the chain is just Standard.
    `tier` is a choice made by the user (for one playbook step, or one run): exactly that tier, with no climbing,
    so what they picked is what answers and what it costs."""
    slots = read_slots(env)
    if not router_on(env):
        return [Pick(purpose, "standard", slots["standard"])]
    if tier in TIERS:
        return [Pick(purpose, tier, slots[tier])]
    start = TIERS.index(PURPOSES.get(purpose, ("standard", ""))[0])
    order = list(TIERS[start:])
    if TIERS[start] == "strong":
        order.append("standard")             # a job that starts at the top may still fall back to Standard if Strong is down
    out: list[Pick] = []
    seen: set[tuple[str, str]] = set()
    for tier in order:
        slot = slots[tier]
        key = (slot.provider_id, slot.model)
        if key not in seen:
            seen.add(key)
            out.append(Pick(purpose, tier, slot))
    return out


def tier_of_model(model: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Which tier a model name belongs to right now (Standard wins when a model fills several slots)."""
    slots = read_slots(env)
    for tier in ("standard", "fast", "strong"):
        s = slots[tier]
        if s.model == model:                    # Standard is checked first, so a slot that collapsed onto it never claims it
            return tier
    return "standard"


def describe(env: Optional[Mapping[str, str]] = None) -> dict:
    """Everything the settings screen needs to show the tiers."""
    slots = read_slots(env)
    return {
        "router": "auto" if router_on(env) else "off",
        "slots": {t: {"provider": s.provider_id, "model": s.model, "label": TIER_LABEL[t], "explicit": s.explicit,
                      "usable": s.usable, "collapsed": s.collapsed, "reason": s.reason} for t, s in slots.items()},
        "suggestions": SUGGESTED,
        "purposes": [{"purpose": p, "tier": t, "label": text} for p, (t, text) in PURPOSES.items()],
    }


# ------------------------------------------------------------------------------- ledger
class Ledger:
    """Counts AI calls by day, tier and model. Memory first, written to disk every FLUSH_EVERY seconds
    and when asked, so a busy run does not rewrite a file on every step."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], date] = date.today) -> None:
        self.path = Path(path)
        self._clock, self._today = clock, today
        self._lock = threading.RLock()
        self._data: dict[str, Any] = self._read()
        self._last_flush = clock()
        self._dirty = False

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("days"), dict):
                return data
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("could not read %s (%s); starting an empty usage count", self.path.name, e)
        return {"days": {}}

    def add(self, tier: str, model: str, prompt: int, completion: int, calls: int = 1, purpose: str = "") -> None:
        if tier not in TIERS:
            tier = "standard"
        with self._lock:
            cell = (self._data["days"].setdefault(self._today().isoformat(), {})
                    .setdefault(tier, {}).setdefault(model, {"calls": 0, "prompt": 0, "completion": 0}))
            cell["calls"] += max(0, int(calls))
            cell["prompt"] += max(0, int(prompt or 0))
            cell["completion"] += max(0, int(completion or 0))
            self._dirty = True
            if self._clock() - self._last_flush >= FLUSH_EVERY:
                self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            cutoff = (self._today() - timedelta(days=KEEP_DAYS)).isoformat()
            self._data["days"] = {d: v for d, v in self._data["days"].items() if d >= cutoff}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(self.path, json.dumps(self._data))
                self._dirty = False
            except OSError as e:
                log.warning("could not save the usage count: %s", e)        # counting must never break a run
            self._last_flush = self._clock()

    def record_agent_usage(self, by_model: Mapping[str, Any], purpose: str = "agent",
                           env: Optional[Mapping[str, str]] = None,
                           already: Optional[dict[str, tuple[int, int, int]]] = None) -> dict[str, tuple[int, int, int]]:
        """Count what browser-use measured for an agent ({model: stats with token counts}). Its figures are
        TOTALS since the agent started, so `already` (what an earlier call counted for this same agent) is
        subtracted: continuing a paused task must not count its first half again. Returns the new totals,
        to pass back next time."""
        seen: dict[str, tuple[int, int, int]] = dict(already or {})
        for model, stats in (by_model or {}).items():
            now = (_num(stats, "prompt_tokens"), _num(stats, "completion_tokens"), _num(stats, "invocations"))
            before = seen.get(str(model), (0, 0, 0))
            delta = tuple(max(0, a - b) for a, b in zip(now, before))
            seen[str(model)] = now
            if delta[0] or delta[1]:
                self.add(tier_of_model(str(model), env), str(model), delta[0], delta[1], delta[2] or 1, purpose)
        self.flush()
        return seen

    def summary(self, days: int = 7) -> dict:
        """Totals per tier over the last `days` days, and each day, newest first."""
        with self._lock:
            start = (self._today() - timedelta(days=max(1, days) - 1)).isoformat()
            per_day, totals = [], {t: {"calls": 0, "prompt": 0, "completion": 0} for t in TIERS}
            models: dict[str, dict] = {}
            for day in sorted(self._data["days"], reverse=True):
                if day < start:
                    continue
                row = {"date": day, "tiers": {}}
                for tier, by_model in self._data["days"][day].items():
                    t = {"calls": 0, "prompt": 0, "completion": 0}
                    for model, c in by_model.items():
                        for k in t:
                            t[k] += c.get(k, 0)
                            if tier in totals:
                                totals[tier][k] += c.get(k, 0)
                        m = models.setdefault(model, {"tier": tier, "calls": 0, "prompt": 0, "completion": 0})
                        for k in ("calls", "prompt", "completion"):
                            m[k] += c.get(k, 0)
                    row["tiers"][tier] = t
                per_day.append(row)
            cells: dict[tuple[str, str], list[int]] = {}
            for day in self._data["days"]:
                if day < start:
                    continue
                for tier, by_model in self._data["days"][day].items():
                    for model, c in by_model.items():
                        cell = cells.setdefault((tier if tier in TIERS else "standard", model), [0, 0])
                        cell[0] += c.get("prompt", 0)
                        cell[1] += c.get("completion", 0)
            return {"days": per_day, "totals": totals, "models": models, "range_days": days,
                    "cells": [[t, m, a, b] for (t, m), (a, b) in sorted(cells.items())]}


# ------------------------------------------------------------------------------- prices
MAX_PRICE = 100000.0                                # per million tokens: anything above is a typing mistake


def clean_price(value: Any) -> Optional[float]:
    """A price per million tokens, or None when it is not a usable number (a bool, text, negative, NaN, huge)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return round(v, 6) if 0 <= v <= MAX_PRICE else None        # NaN fails both comparisons


class Prices:
    """What the USER says each model costs per million tokens (input, output). The app ships no prices: they differ
    by account, change often, and a stale number shown as fact would be worse than none. Keyed by model name, so
    swapping the model in a tier never leaves the old model's price on the new one."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, float]] = self._read()

    def _read(self) -> dict[str, dict[str, float]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("could not read %s (%s); no prices set", self.path.name, e)
            return {}
        out: dict[str, dict[str, float]] = {}
        for model, p in (raw.items() if isinstance(raw, dict) else []):
            a, b = clean_price((p or {}).get("in") if isinstance(p, dict) else None), clean_price((p or {}).get("out") if isinstance(p, dict) else None)
            if isinstance(model, str) and a is not None and b is not None:
                out[model] = {"in": a, "out": b}
        return out

    def all(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {m: dict(p) for m, p in self._data.items()}

    def set(self, model: str, price_in: Optional[float], price_out: Optional[float]) -> None:
        """Save a model's price; None for both removes it. Raises ValueError for anything unusable."""
        model = (model or "").strip()
        if not model or len(model) > 100:
            raise ValueError("a model name is needed")
        with self._lock:
            new = dict(self._data)
            if price_in is None and price_out is None:
                new.pop(model, None)
            else:
                a, b = clean_price(price_in), clean_price(price_out)
                if a is None or b is None:
                    raise ValueError(f"a price is a number from 0 to {int(MAX_PRICE)} per million tokens, for both input and output")
                if len(new) >= 200 and model not in new:
                    raise ValueError("too many prices saved")
                new[model] = {"in": a, "out": b}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(self.path, json.dumps(new))
            self._data = new                       # only once it is on disk, so memory never disagrees with the file


def estimate_cost(summary: Mapping[str, Any], prices: Mapping[str, Mapping[str, float]]) -> dict:
    """An estimate (in whatever currency the user entered the prices in) of the summary's usage, per tier and in
    total. Tokens of a model with no price are counted separately, never guessed."""
    per_tier = {t: 0.0 for t in TIERS}
    unpriced = 0
    for tier, model, prompt, completion in summary.get("cells", []):
        p = prices.get(model)
        if p is None:
            unpriced += prompt + completion
        else:
            per_tier[tier] = per_tier.get(tier, 0.0) + (prompt * p["in"] + completion * p["out"]) / 1_000_000
    return {"per_tier": {t: round(v, 6) for t, v in per_tier.items()}, "total": round(sum(per_tier.values()), 6),
            "unpriced_tokens": unpriced}


def _num(obj: Any, name: str) -> int:
    v = obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, 0)
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


# --------------------------------------------------------------------------- the wrapper
class TieredLLM:
    """A model that can be handed to anything expecting one (browser-use's extraction step, a playbook's
    read-the-page step) and that quietly moves up a tier if a call fails.

    Failure means the call raised: a provider error, an invalid structured answer, a rate limit, a missing
    key. A bad ANSWER from a model that technically responded is not detected (that would need a second
    model to judge it); the agent-level retry handles that case (see run_outcome and agent_ui).
    """
    _verified_api_keys = True

    def __init__(self, picks: list[Pick], factory: Callable[[str, str], Any], ledger: Optional[Ledger] = None) -> None:
        if not picks:
            raise ValueError("a TieredLLM needs at least one model")
        self.picks, self._factory, self._ledger = list(picks), factory, ledger
        self._clients: dict[tuple[str, str], Any] = {}
        self.events: list[dict] = []                # what happened, for tests and the run record
        self._current = picks[0]
        self.tokens = 0                             # prompt + completion tokens of the answered calls since reset_tokens()

    def reset_tokens(self) -> None:
        self.tokens = 0

    # BaseChatModel look-alike: report the model that answered last (or will answer first)
    @property
    def model(self) -> str:
        return self._current.model

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def name(self) -> str:
        return self.model

    @property
    def provider(self) -> str:
        return self._current.provider_id

    @property
    def tier(self) -> str:
        """The tier that answered last (Standard if a tier that was not set up fell back to it)."""
        return self._current.slot.effective_tier

    def _client(self, p: Pick) -> Any:
        key = (p.provider_id, p.model)
        if key not in self._clients:
            self._clients[key] = self._factory(p.provider_id, p.model)
        return self._clients[key]

    async def ainvoke(self, messages: list, output_format: Any = None, **kwargs: Any):
        last: Optional[BaseException] = None
        for i, p in enumerate(self.picks):
            try:
                client = self._client(p)
                result = await client.ainvoke(messages, output_format, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last = e
                self.events.append({"tier": p.slot.effective_tier, "model": p.model, "ok": False,
                                    "error": f"{type(e).__name__}: {str(e)[:160]}"})
                log.warning("%s model %s failed (%s)%s", TIER_LABEL[p.slot.effective_tier], p.model, type(e).__name__,
                            "; trying the next tier" if i + 1 < len(self.picks) else "")
                if CLASS_ESCALATION_PAUSE:
                    await asyncio.sleep(CLASS_ESCALATION_PAUSE)
                continue
            self._current = p
            self.events.append({"tier": p.slot.effective_tier, "model": p.model, "ok": True})
            usage = getattr(result, "usage", None)
            if usage is not None:
                prompt, completion = getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
                self.tokens += prompt + completion
                if self._ledger is not None:
                    self._ledger.add(p.slot.effective_tier, p.model, prompt, completion, purpose=p.purpose)
            return result
        assert last is not None
        raise last


# ----------------------------------------------------------------------------- the router
class Router:
    """Builds the models for each job from the current settings. Settings are read on every call, so a
    change in the Settings screen applies to the next run without a restart."""

    def __init__(self, ledger: Optional[Ledger] = None, factory: Optional[Callable[[str, str], Any]] = None,
                 env: Optional[Mapping[str, str]] = None) -> None:
        self.ledger = ledger
        self._env = env                              # None = the live environment
        self._factory = factory or self._default_factory
        self._cache: dict[tuple[str, str, str], Any] = {}

    @staticmethod
    def _default_factory(provider_id: str, model: str) -> Any:
        from llm_config import build_llm
        return build_llm(provider_id, model)

    def _key(self, provider_id: str, model: str) -> tuple[str, str, str]:
        env = self._env if self._env is not None else _env()
        provider = providers.BY_ID[provider_id]
        return provider_id, model, (env.get(provider.key_env) or "")[-6:]       # a changed key builds a new client

    def client(self, provider_id: str, model: str) -> Any:
        k = self._key(provider_id, model)
        if k not in self._cache:
            self._cache[k] = self._factory(provider_id, model)
        return self._cache[k]

    def pick(self, purpose: str, tier: Optional[str] = None) -> Pick:
        return pick(purpose, self._env, tier)

    def llm(self, purpose: str, tier: Optional[str] = None) -> Any:
        """The model for this job: one plain client (no escalation), for callers that need the real class."""
        p = self.pick(purpose, tier)
        return self.client(p.provider_id, p.model)

    def escalating(self, purpose: str, count: bool = True, tier: Optional[str] = None) -> TieredLLM:
        """The model for this job, which moves up a tier if a call fails (not when the user chose a `tier`: then
        that tier answers or the call fails). `count=False` when something else is already counting what it
        uses (browser-use's own run summary counts the models given to an agent; counting here too would show
        every call twice)."""
        return TieredLLM(chain(purpose, self._env, tier), self.client, self.ledger if count else None)

    def distinct_from_standard(self, purpose: str) -> bool:
        """Does this job use a different model from Standard? (If not there is nothing to configure.)"""
        p = self.pick(purpose)
        std = read_slots(self._env)["standard"]
        return (p.provider_id, p.model) != (std.provider_id, std.model)
