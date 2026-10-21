# Architecture

This document explains how the pieces fit together and why they are shaped the way they are. For setup
and day-to-day commands see the [README](../README.md).

## The idea in one picture

```
                    ┌────────────────────────── browser (React UI) ───────────────────────────┐
                    │  chat · live view · approvals · settings · playbooks · schedules        │
                    └───────────────▲──────────────────────────────┬──────────────────────────┘
                       WebSocket /ws │  frames, steps, results       │ HTTP  (task, stop, settings…)
                    ┌───────────────┴──────────────────────────────▼──────────────────────────┐
                    │  server/main.py  (FastAPI)   auth token · Host/Origin checks             │
                    │   TaskController ─ one run at a time, stop / continue                    │
                    └───────┬──────────────────────────────────────────────────────┬──────────┘
                            │ run_task                                              │ playbook run
                  ┌─────────▼──────────┐                                  ┌─────────▼──────────┐
                  │ browser-use Agent  │   model decides the next action  │ playbooks/player   │
                  │  + llm/ (provider, │◄────────────────────────────────►│  deterministic     │
                  │    tiers, repair)  │                                  │  replay, AI only   │
                  └─────────┬──────────┘                                  │  where needed      │
                            │ every action                                └─────────┬──────────┘
                  ┌─────────▼──────────┐                                            │
                  │ agent/action_guard │  allow · ask the human · block             │
                  │  (GuardedTools)    │◄───────────────────────────────────────────┘
                  └─────────┬──────────┘
                            │ approved actions only
                  ┌─────────▼──────────┐
                  │  Chromium (CDP)    │  persistent profile, live screencast
                  └────────────────────┘
```

Two ways to get work done share one safety layer:

1. **AI runs.** The model sees the page and picks the next action each step. Flexible, slower, costs tokens.
2. **Playbooks.** A finished AI run is recorded as steps with variables; replaying it uses no model except for
   steps explicitly marked as needing judgement. Fast, cheap, repeatable, schedulable.

Both go through `GuardedTools`, so the rules below apply to a replay exactly as they do to a live run.

## Package map

| Package | Responsibility | Notable modules |
|---|---|---|
| `aiba.server` | HTTP and WebSocket API, the run lifecycle, the live view, human approvals, the chat feed | `main.py` (entry point and wiring), `live_view.py`, `approvals.py`, `task_control.py`, `ui_feed.py` |
| `aiba.agent` | What stands between the model and the browser | `action_guard.py`, `run_outcome.py`, `form_options.py`, `browser_nav.py` |
| `aiba.llm` | Providers, model choice, tiers, usage ledger | `providers.py` (single source of truth), `config.py`, `tiers.py`, `deepseek.py` |
| `aiba.playbooks` | Record, replay, repair and draft playbooks | `model.py` (the format), `recorder.py`, `player.py`, `repair.py`, `draft.py` |
| `aiba.scheduling` | Unattended runs and watching for change | `scheduler.py`, `limits.py`, `monitor.py`, `watcher.py` |
| `aiba.storage` | Everything kept on disk | `paths.py`, `env.py`, `secret_box.py`, `chat_store.py` |
| `aiba.cli` | The original command-line agents | `browser_agent.py`, `linkedin_agent.py` |

Dependencies point one way: `server` uses everything, `playbooks` and `scheduling` use `agent`, `llm` and
`storage`, and `storage` uses nothing else in the package.

## Life of a task

1. The UI posts the task to `POST /task`. `TaskController` (`server/task_control.py`) owns the
   running/paused flags, so only one run exists at a time and stop/continue are race-free.
2. `run_task` builds a prompt (the task, plus a short, capped summary of earlier chat turns treated as *data*, never
   as instructions), picks a model through the tier router and starts a browser-use `Agent` with `GuardedTools`.
3. Each step the model proposes actions. `GuardedTools.act` classifies each one **before** it runs. If the answer is
   *ask*, the run waits on `ApprovalBroker` until you click in the UI (or declines at once in unattended mode).
4. Progress streams to the UI over the WebSocket: log lines (filtered and with secrets redacted by `ui_feed.py`),
   browser frames from `live_view.py`, and approval requests.
5. When the agent finishes, `run_outcome.assess` turns *what the agent did* into *what happened*: **done**, **limit**
   (the step budget ran out, so the run pauses and offers Continue) or **failed**. An independent judge can mark a
   finished answer *Needs review*, which may trigger one more attempt, on a stronger model if one is configured.
6. A stopped run still reports what it found, and that report is passed to the next run as progress so far.

## Safety model

The core rule: **text from a web page is data, never an instruction**, and the model's output is not trusted to
enforce it. Enforcement is code (`agent/action_guard.py`), a pure function `classify(action, element, url)` that
returns one of:

| Verdict | Examples |
|---|---|
| **allow** | Ordinary navigation and clicks, typing into fields, scrolling |
| **ask** | Clicking controls labelled send/post/connect/buy/delete/download…, Enter in a message box, running JavaScript, a very long URL query (data smuggling) |
| **block** | Stored credentials typed into anything but that site's login field, `file:` and `javascript:` URLs |

Around that:

- **Credentials** are passed to browser-use as domain-scoped placeholders; the model only ever sees `<secret>name</secret>`.
- **Approval modes:** `ask` (default), `auto` (approves send/post-style clicks only on the site the task names and
  declines JavaScript or odd links outright), `off` (hand-edited only). Blocks apply in every mode.
- **The control plane:** the server binds to localhost, requires a per-launch token delivered in an HttpOnly cookie,
  and rejects requests whose `Host` or `Origin` is not its own. The agent's browser cannot open the app's address,
  so it cannot approve its own requests.
- **Pacing:** `scheduling/limits.py` keeps unattended runs within per-site daily allowances that start low and
  ramp up, with human-looking gaps between runs.

The full list of what was found and fixed is in [security-audit.md](security-audit.md).

## Models

`llm/providers.py` is the single source of truth for which providers exist and their defaults; the settings
screen, validation and client construction all read it. `llm/config.py` builds the client for the chosen provider.

- **Tiers.** `llm/tiers.py` has three slots, *Fast*, *Standard* and *Strong*, and a router that picks one per purpose
  (reading a page and summarising old steps go to Fast, a second attempt goes to Strong). A slot that is unset or has
  no key collapses to Standard, and a call that fails on one tier is tried once on the next. A `Ledger` counts tokens per
  tier and model.
- **DeepSeek repair.** DeepSeek often returns just the arguments of an action instead of the required
  `{memory, action: [{navigate: {…}}]}` wrapper, which wastes a step (five in a row stop the run).
  `llm/deepseek.py` puts the wrapper back, but only when the arguments belong to exactly one action.
- **Vision.** Whether screenshots are sent is decided from the model's name, with a user override.

## Playbooks

A playbook (`playbooks/model.py`) is a list of steps (open, click, type, scrape a table, loop over rows, ask a human…)
plus typed variables. `recorder.py` builds a draft from a finished run, `draft.py` can produce one from a description,
and `player.py` replays it:

- an element is found again by a *fingerprint* (tag, attributes, accessible name), not a brittle index;
- when it can't be found, `repair.py` proposes a fix for you to review instead of silently guessing;
- tables are read with selectors and no model; a model is asked only for steps tagged as needing judgement;
- editing a step invalidates its approval, so a changed playbook is never run on an old "yes".

`scheduling/` runs playbooks on a cadence, parks a run that needs a human (an approval or a typed answer) instead of
blocking, and `monitor.py` / `watcher.py` compare what a run read with the last time and notify on change.

## Data on disk

Everything lives in `%LOCALAPPDATA%\AIBrowserAgent` (override with `AIBA_DATA_DIR`), never in the project folder:
settings (secrets encrypted with DPAPI), chats, playbooks and their run history (kept 30 days), browser profiles,
schedules, change snapshots and the usage ledger. Writes are atomic (temp file then rename) so a crash cannot leave a
half-written file.

## Testing strategy

- **Pure logic first.** The guard, outcome assessment, tier routing, pacing and the playbook format are pure functions
  or small classes, tested directly with made-up inputs and clocks.
- **A scripted model.** `tests/fake_llm.py` replaces the LLM with a script (`navigate`, `click`, `input`, `done`,
  or a failure), so the *real* browser-use agent and a *real* Chromium run end to end with no API cost and no
  nondeterminism. This is how the guard is shown to stop a real click and how step counting is checked against a real run.
- **Regression tests carry their reason.** Tests for fixed bugs say which finding they close; several exist because
  the original unit tests passed while the real app failed (for example log lines whose logger name browser-use rewrites).
- **Layers:** about 1,700 fast tests, about 110 opt-in real-browser tests (`RUN_BROWSER_TESTS=1`), 184 frontend tests.

## Known limits and next steps

- `server/main.py` is the composition root and is too large; splitting it into routers is the next refactor.
- Windows-first: DPAPI secrets, the launcher and the packaging. The core is platform-neutral but untested elsewhere.
- Live checks against paid providers (DeepSeek, OpenAI, Gemini, Qwen) are listed as open in the audit, because the
  automated tests use the scripted model.
