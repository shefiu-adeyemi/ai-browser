# Playbooks roadmap: features 2 to 5 (plan, not built)

Feature 1 (playbook core: record, variables, deterministic player, scheduler) is in PLAYBOOKS.md.
Features below build on it. Everything keeps the same guard, approval and secret rules.

## What the format must allow from day one
So these features don't force a rewrite, the phase-1 playbook format includes:
- `outputs`: named values a step can produce (extract results, counters).
- `inputs`: typed variables (text, url, linkedin_url, number, list, file).
- step kinds: `open`, `click`, `type`, `key`, `scroll`, `wait`, `check`, `extract`, `checkpoint`,
  `ai`, `repeat` (loop), `if` (condition), `run_playbook` (call another).
- a `version` field and a step `id` on every step (stable references, safe edits).
- a run record: per-run id, inputs, outputs, per-step status, timings, tokens/cost, log.

---

## Feature 2: Scrape to table + batch runs

**What it is.** Extract named fields from a page into rows; run a playbook once per row of an input list.

**Extract step.** A recorded selection: the user clicks "extract" and points at elements in the live view;
each becomes a field (name, selector fingerprint, type text/number/link/attr). Fields use stable selectors
(not indexes), same fingerprint matching as click steps. List extraction: point at one repeated item and the
player finds all siblings (rows), so one step yields many rows.
- AI-free when selectors are stable. If a field is missing, the check fails and self-heal (one narrow AI
  call) may re-find it; a successful heal updates the fingerprint.
- Optional `ai` extract for unstructured pages (summarise/classify), using the cheaper `page_extraction_llm`.

**Batch.** Input = pasted list, CSV upload, or a previous run's output. The runner makes one child run per
row:
- honours the per-site caps (LinkedIn: 20/day, 30 s+ random gap); over-cap rows stay queued for the next day.
- per-row status (done, needs you, failed, skipped); one row failing never loses the others.
- failure policy per schedule/batch (already decided): stop and notify, or notify and skip to next row.
- resumable: if the app closes, the batch continues from the first unfinished row (no duplicates).
- max rows per batch and a dry-run preview of the first 3 rows.

**Results.** A table view in the UI with per-row status; export CSV/JSON. Stored locally per run.
Later: Google Sheets/webhook as export targets (outbound = `ask`-class, enabled explicitly per playbook).

**Data safety.** Extracted personal data (e.g. LinkedIn profiles) stays local; run logs redact secrets;
a retention setting deletes old results. Users are reminded that scraping is subject to the site's terms.

**Tests.** Stable/changed fixture pages; list of 10 rows with 2 failures; resume after a simulated crash;
cap enforcement; CSV round trip with unicode/commas/newlines.

---

## Feature 3: Change monitoring + notifications

**What it is.** A schedule runs an extract playbook, compares with the previous result, and notifies only
when something changed.

**Comparison.** Per-playbook watched fields. Store the last N snapshots (default 10) locally.
- diff types: changed value, new row, removed row, appeared/disappeared element.
- noise control: ignore-patterns (timestamps, counters), numeric thresholds ("price change > 5%"),
  whitespace normalisation.
- optional `ai` summary of the diff ("the role changed from X to Y") only when a change exists; unchanged
  runs cost zero tokens.

**Notifications.** Channels, in build order: in-app inbox (always), desktop notification, then email and
Telegram/webhook (outbound, explicit opt-in, credentials as secrets). Events: change detected, run needs
you, run failed, batch finished, schedule paused by cap. Rules: rate-limit and group (one digest, not 50
pings), quiet hours, and never include secrets in a message.

**Scheduler additions.** Interval or cron-like presets (hourly, daily at a time), per-schedule enable/pause,
next-run display, the missed-run log, and the global "pause all" switch.

**Tests.** Diff engine unit tests (added/removed/changed/noise), snapshot retention, notification grouping
and rate limit, quiet hours, a fake clock for the scheduler.

---

## Feature 4: Human checkpoints

**What it is.** A step that pauses the run and asks the user, which is the safe way to allow sends, form
submits, and risky clicks.

**Kinds.**
- `approve`: yes/no with the exact thing shown (screenshot of the page, the text to be sent, target URL).
- `input`: ask for a value mid-run (e.g. a 2FA code, a choice from a list).
- `handoff`: "do this manually in the live view, then press Continue" (login wall, CAPTCHA).
  Reuses the existing approval/hand-off UI and `request_user_help`.

**Behaviour.**
- Attended: a prompt in the UI (existing ApprovalPrompt) and the run waits, with a timeout.
- Unattended: the run parks in state "Waiting for you" and sends a notification; it resumes when the user
  answers, or expires (per checkpoint: cancel or skip) after a set time. A parked run never times out into
  an approval: silence means no.
- A checkpoint answer applies to that run only; it never becomes a standing approval.
- Sends/posts/submits are only allowed unattended when guarded by an `approve` checkpoint.
- The state must survive an app restart (persisted in the run record).

**Tests.** approve/deny/timeout paths; restart while parked; "silence = no"; an `approve` showing exactly
the text that would be sent; concurrent runs each with their own checkpoint.

---

## Feature 5: Natural language to playbook

**What it is.** The user describes a task; the AI drafts a playbook; the user reviews and saves. Every later
run is free.

**Flow.**
1. The user types the task. The AI runs it once (the existing agent), attended, with recording on, OR drafts
   steps from the description plus a page snapshot.
2. The draft is converted to deterministic steps wherever possible; decisions stay as `ai` steps.
3. A review screen shows each step in plain language with AI/no-AI badges, proposed variables, and domains.
4. A dry run (no `ask` actions) checks the draft; failures are shown before saving.
5. The user approves (layer 1 of the approval policy), then saves.

**Quality guards.** The AI may only emit known step kinds (validated against the schema); unknown fields are
rejected; it cannot add secrets, domains or `ask` actions without showing them in the review; steps that
depend on brittle selectors are flagged. Prompt-injection from page content can't add steps: the draft is
reviewed by the user, and untrusted page text stays labelled as data.

**Cost.** One AI run to draft (same cost as today's run), then $0 on replays. Show "estimated cost per run"
on each playbook and accumulate "saved by replaying" vs the original AI run.

**Tests.** Schema validation of drafts; a fake-LLM draft round trip; malicious page text trying to inject a
step or a domain; review screen lists every `ask` action.

---

## Order and dependencies
1. Core (PLAYBOOKS.md) with the format above.
2. Feature 4 (checkpoints) early, because the scheduler needs "Waiting for you" and notifications need it.
3. Feature 2 (extract + batch) on the core player.
4. Feature 3 (change monitoring + notifications) on the scheduler and extract.
5. Feature 5 (NL to playbook) last, once the format is stable.

Each step still gets the fix, optimization review, fix cycle; the slow real-browser suite runs at the end.

## Competitive features (from market research, to place in the build order)

Positioning: "automation you can trust and afford": local and private, free replays, shows its own cost and
success rate, never does anything not approved, and says plainly when and why it broke.
Known competitor weaknesses this targets: silent failures, credit burn, brittle selectors, weak control,
poor onboarding. (Research from review sites and vendor blogs; treat as direction, not proof.)

6. **Failure report + one-click repair** (highest value). Per failed run: which step, a screenshot of the
   page, the plain-English reason, and a fix button (re-record the step, or let AI repair it).
7. **Cost and success-rate badge per playbook** (tokens/cost per run, success over the last N runs, estimated
   saving vs an AI run) and a monthly budget cap that pauses AI steps but never the whole playbook.
8. **Pre-approved exact actions.** At save/schedule time the user approves one exact action ("send this text to
   this person"); an unattended run may do exactly that and nothing else. This closes the known gap where
   scheduled jobs stall because a final confirmation can't be given.
9. **Per-site safety profiles.** Per-action caps, random delays and a warm-up ramp (LinkedIn values in
   PLAYBOOKS.md), editable, with a plain-language terms-of-service warning.
10. **Templates + guided first playbook.** Ready-made playbooks and plain-language error messages.
11. **Session-health inbox.** Before a scheduled run, check the signed-in state and surface "needs sign-in".
12. **Versioning and undo** for playbooks (restore an earlier version after an edit breaks it); all steps and
    variables stay editable after saving.
13. **SPA/iframe/shadow-DOM robustness.** Test fingerprinting against these early; they are a known weak spot
    of competitors.
Later opt-in: CSV/Sheets/webhook exports.

Build-order placement: 6, 7, 11, 12 join the core/player/scheduler phases (they depend on the run record);
9 ships with the scheduler; 8 ships with checkpoints; 10 and 13 are polish after scrape + batch.

## Model tiers and router (approved; lessons from Cursor research)

Settings gain three model slots, each a (provider, model) pair picked from the existing provider list
(`providers.py` stays the single source of truth):
- **Fast** (low latency, cheap): self-heal element lookups, `extract` steps (`page_extraction_llm`), simple
  classification, diff summaries, checkpoint text.
- **Standard** (default): normal chat tasks and AI steps inside playbooks.
- **Strong** (high capability, slower, dearer): hard reasoning, drafting a playbook from natural language,
  repairing a step that the fast model failed on, planning.

Selection:
- **Auto (default):** the router picks the tier per task/step type using a fixed table (above), no extra LLM
  call to decide. Escalation: a step that fails on Fast is retried once on Standard, then Strong; each
  escalation is logged and counted in the run cost. Never escalate silently past a per-run cost cap.
- **Manual:** the existing model selector stays; choosing a model for a chat or playbook pins it and turns
  the router off for that run. Each playbook AI step may be pinned to a tier.
- Every AI call records tier, model, tokens and cost in the run record (feeds the cost badge, feature 7).
- Safety: the guard, approvals and secrets rules are identical for every tier; a cheaper model never gets
  looser rules. Fall back to one model if only one provider key is set (tiers collapse to that model).
- Tests: tier table lookup, escalation order and cap, pinned model overrides router, single-key collapse,
  cost accounting per tier.

## More lessons from the market (planned, to place after the core)
- **Own the fast path.** Replays are our "tab completion": the frequent, cheap, instant thing. Keep their
  latency and cost visible.
- **Predictable pricing and budgets** (feature 7) so there is no surprise bill.
- **Rules/context file per playbook or site**: plain-text hints the AI steps always receive ("this site's
  login is on a popup", "ignore cookie banners"), like Cursor's rules files.
- **Index/knowledge of the user's own playbooks** so natural-language requests reuse an existing playbook
  instead of drafting a new AI run.
- **Background runs** that don't block the live view (queue, notify), like Cursor's background agents.
- **Honest measurement:** record real success rate and time saved per playbook; don't assume savings.
- **Trust incidents to avoid:** never let the AI state invented facts as results (extractions must come from
  the page, with the source element); verify before reporting success; no unreviewed outbound actions.
- **Reduce switching cost:** import/export playbooks as JSON; keep chat and live view unchanged.

## Decisions (approved by the user)
- Notifications: in-app + desktop first; email/Telegram later, opt-in.
- Results stay local only for now; Sheets/webhook exports come later, explicit opt-in per playbook.
- Retention: 30 days default for run logs and snapshots (configurable).

Status: all five features are planned and approved. Nothing is built yet. Phase 1 (playbook core, with the
format above) starts next, after the user's go-ahead.
