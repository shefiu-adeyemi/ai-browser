# Playbooks: design (approved; phases 1, 2 and 3 built, see the status sections at the end)

Full plan = this file (core: recording, approval policy, first playbook, build order) plus
PLAYBOOKS_ROADMAP.md (scrape + batch, change monitoring + notifications, human checkpoints, natural
language to playbook, and the format requirements for phase 1). Overall order: core, checkpoints,
scrape + batch, monitoring + notifications, natural language to playbook.

Session note: a signed-in LinkedIn session lives in the app's persistent browser profile
(`app_paths.UI_PROFILE_DIR`), not in playbooks. Playbooks hold only steps, variables and `<secret>`
placeholders. An expired session makes an unattended run stop with "Needs you". Add a session-health
check (is the account still signed in?) to phase 1.

Goal: run browser steps without an LLM directing each one. Record a run, save it as a playbook with
variables, replay it (attended or scheduled), use AI only where judgement is needed.

## Recording
- Record button. Two sources: an AI run (every step captured) or teach mode (the user acts in the live
  view; no AI cost).
- Each step is tagged `deterministic` or `ai`. Elements are saved by stable fingerprint, never by index.
- On finish: Save to playbook or Cancel. The save dialog lists the steps, AI/no-AI badges, proposed
  variables (typed text, URLs), the domains touched, and any `ask`-class actions.
- Secrets are stored only as `<secret>` placeholders, scoped to their domain.

## Steps that need no AI
Open a URL (incl. a variable URL), back/forward/reload, tab switches, waits, typing a variable into a
known field, clicking a known stable element, scrolling, key presses, extracting with stable selectors,
fixed-list loops, simple if-exists conditions, login with stored placeholders.

## Steps that need AI
Choosing among results, reading/summarising unknown content, unexpected pop-ups or layout changes,
self-heal when a saved element is gone, judging goal completion.

## Cost levers
1. Deterministic steps cost 0 tokens. 2. Self-heal only on a failed check, one narrow call, no screenshot
unless needed. 3. For AI steps: vision off or low-res, `flash_mode`, low `max_history_items`.
4. Cheaper `page_extraction_llm`. 5. No end-of-run judge on playbooks (per-step checks; judge only on
failure). 6. Prompt caching for the fixed prompt. 7. A successful self-heal updates the fingerprint.

## Approval policy (decided)
Approval happens before the run. During an unattended run anything outside what was approved stops the
run; nothing is ever auto-approved.

1. Approve at save: the user ticks one approval for the exact steps. It is tied to a hash of steps,
   allowed domains and parameter types; editing a step clears it.
2. Approve the schedule: playbook, fixed values or allowed list, run cap.
3. `action_guard` stays on for every run:

| Situation | Attended | Unattended |
|---|---|---|
| `allow`, inside playbook | runs | runs |
| `ask`, listed at save | runs | runs |
| `ask`, not in playbook (e.g. from self-heal) | prompts | stops, "Needs you", notify |
| `block` | refused | refused |
| domain not approved | refused | refused |
| login wall / CAPTCHA / 2FA / unexpected page | hand-off prompt | stops, "Needs you" |

Unattended self-heal may only re-find an element for an existing step: no new action kinds, no new domains.

Hard rules: only `<secret>` placeholders, never real secrets; no purchases/sends/posts/downloads
unattended (off by default); Stop and a global "pause all schedules" always work; every unattended run
writes a readable log.

### Failure behaviour (decided)
- When an unattended run hits something unexpected the user chooses PER SCHEDULE: **stop and notify**, or
  **notify and skip to the next item** in a list.
- At most one retry, no retry loop (account-restriction risk on LinkedIn).
- Overlap with a live task: queue, never interleave.
- Missed runs (app was closed): skipped and recorded as missed; configurable per schedule later.
- Schedules only fire while the app is open. A background service is a later, separate job.
- LinkedIn limits (REVISED after market research; replaces the earlier flat "20 runs per day"): caps are
  per action type, with a warm-up ramp for new or low-activity accounts, and a random gap of at least 30 s
  between runs. Starting defaults: profile views about 50/day, ramping by about 10/week toward a ceiling of
  150/day; connection requests about 15/day (ceiling 25); messages about 40/day (ceiling 80). Published
  safe ranges are roughly 150-300 views, 15-25 connection requests, 40-80 messages per day for established
  accounts; lower for accounts under 60 days old. Sends and connection requests stay blocked unattended
  unless guarded by an `approve` checkpoint or a pre-approved exact action. All caps are editable.
  The UI states plainly that automating LinkedIn can breach its terms and risk account restrictions.

## First playbook: open a LinkedIn profile
1. Variable `profile_url`, validated as a LinkedIn profile URL (`https://www.linkedin.com/in/...`).
2. Open the URL directly (no AI).
3. Check: final URL is still a linkedin.com profile and the page has a profile heading.
4. Authwall/login page: stop with "Needs you". No unattended sign-in at this stage.
5. Later: extract name and headline with fixed selectors.

## Build order
1. Playbook format + save a finished run as a playbook (incl. variables, tags, fingerprints).
2. Deterministic player through `GuardedTools`/approvals, variable form, live-view display.
3. Scheduler (per-schedule failure policy, caps, queueing, missed-run log).
4. Self-heal, then teach mode, then hybrid AI steps/loops/conditions, then library UI.

Open risk: browser-use replay reliability on real sites is untested; prototype before committing.


## Phase 1 status (built and tested)
Built: the playbook format (`playbook.py`), storage with version history/undo (`playbook_store.py`), the
recorder that turns a finished AI run into a draft (`playbook_recorder.py`), the API (`/playbooks`,
`/playbooks/draft`, `/playbooks/variable`, `/playbooks/{id}`, `/playbooks/{id}/restore`) and the
Playbooks dialog in the UI (save the last run: review steps with AI/no-AI badges, make inputs, approve,
save; library with delete). Tests: `test_playbook*.py`, plus the opt-in real-browser
`test_playbook_recorder_browser.py`.

Facts learned from a real browser-use run (they shaped the recorder):
- Element indexes are DOM node ids (e.g. 24, 25, 36), not 1, 2, 3, so a saved index means nothing later;
  only the fingerprint (hashes, accessible name, attributes, XPath) is stored.
- A click on a missing element returns NO error, only the message "Element index N not available", and
  has no recorded element; the recorder treats both as a failed step and leaves it out (with a note).
- browser-use opens a URL found in the task by itself ("Initial navigation"); a repeated open of the same
  page is merged into one step.
- Repeated clicks on the same element are kept (they may be paging through results) and flagged.

Not built yet (phase 2 onward): the player (replay through `GuardedTools`), running with variable values,
scheduler, checkpoints, self-heal, scrape + batch, monitoring, natural-language drafting, model tiers.
Known limits of drafts (SUPERSEDED, see "Limits pass" below): searches replayed exactly; tab switches and
hand-offs were not recorded.


## Phase 2 status (built and tested)
Built: the element matcher (`playbook_match.py`), the player (`playbook_player.py`), run records
(`playbook_runs.py`), the run endpoints (`POST /playbooks/{id}/run`, `GET /playbooks/{id}/runs[/{run}]`),
`run_playbook_task` in `agent_ui.py` and the Run form in the Playbooks dialog. A run happens in the
current chat through the same TaskController, GuardedTools, approval prompts and live view as an AI run,
and is recorded step by step. Tests: `test_playbook_match.py`, `test_playbook_player.py` (fake page),
`test_playbook_runs.py`, `test_playbook_runs_app.py`, and the opt-in real-browser
`test_playbook_player_browser.py` (record with a scripted fake LLM, save, replay with a new input: no
model call, a pre-approved Send runs without a prompt, an unapproved one asks and a "no" stops the run,
Stop and a vanished element are recorded).

How a step runs: the fingerprint is matched against the live page (exact hash, stable hash, identity
attribute, XPath, attributes, visible name; ambiguity is never guessed), then the action goes through
GuardedTools. The guard runs in "ask" mode always; the player's approver answers for it: a step the user
approved is pre-approved only if the element found is a HIGH confidence match (and an Enter key only if
the step before it was), otherwise the user is asked (attended) or the run stops "needs you" (unattended,
supported by the player but not yet reachable from the UI). A block is final. Sites a run may visit are
recomputed from the steps (never read from the file). A sign-in wall after an open stops the run with
"needs you". The run stops at the first failing step.

Facts learned: element hashes are 64-bit so they are stored as TEXT (JavaScript would corrupt them);
browser-use element numbers change between pages and runs, so tests find elements by id (the fake LLM
now accepts {"click": "send"}); a run's time limit does not count time spent waiting for the user.

Not built yet: scheduler and unattended entry point, checkpoints, self-heal, AI steps (`ai` kind stops
the run; `extract` works and needs a configured model), run history screen, scrape + batch, monitoring.


## Limits pass on phases 1 and 2 (built and tested)
Addressed:
- **Searches can vary.** A `search` step (engine + query) replaces the old recorded search URL; the query can
  be an input. The address is built from the engine and the URL-encoded query, so a value can never change
  the site. The engine's site is part of the approved sites.
- **Manual steps are recorded.** A `handoff` step (from the agent's "ask the user" action, or a password the
  user typed) shows a banner and waits for "I'm done"; unattended it stops "needs you". An approval never
  answers for the user. A password literal is still never recorded.
- **Tabs.** A click that opens a new tab switches to it by itself (verified in a real browser), so only going
  back or closing needs a `tab` step (by position). A playbook with tab steps starts from a single tab; a
  recording that began with several tabs gets a warning note. A missing tab stops the run with a message.
- **iframes and shadow DOM** are matched (verified in a real browser); new-tab links too.
- **Settling.** After each action the player waits until the page has finished loading and stopped changing
  (capped at 5 s) instead of a fixed pause.
- **Sign-in walls** are noticed after ANY step (not only an open). Attended: a hand-off banner, then it checks
  again; unattended: stops. A playbook that signs in itself or has a handoff step is left alone.
- **Editing saved playbooks** in the UI: name, remove steps, input labels/defaults/required, approval (can be
  taken back), older versions with Restore. Damaged files are listed (with a plain reason) and can be deleted.
- **Run history** screen: past runs per playbook and a step-by-step account of each.
- **Layout** was checked on screen (the browser pane needs a fixed viewport size to draw screenshots).
- Request failures show readable text (for example "reload the page" for a lost session cookie).

Still limits (honest list): general `ai` steps stop the run (only `extract` reads with a model); no drag, file
upload, or JavaScript steps (recorded runs list them as notes); no self-heal (a vanished element stops the run);
unattended runs are supported by the player but have no scheduler/entry point yet; nothing has been run
against real LinkedIn (needs a signed-in account and respects its terms); tab positions assume one starting tab.


## Phase 3 status: scheduler (built and tested)
Built: schedules (`schedules.py`, `schedule_store.py`), the scheduler (`scheduler.py`), daily allowances and
gaps (`site_limits.py`), the inbox and desktop toasts (`notifications.py`), the unattended runner and API in
`agent_ui.py` (`/schedules*`, `/scheduler/limits`, `/notifications*`), and the UI (Schedules tab with a
create/edit form and the limits editor, a bell with an unread count, the inbox). Tests: `test_schedules.py`,
`test_site_limits.py`, `test_notifications.py`, `test_schedule_store.py`, `test_scheduler.py`,
`test_schedules_app.py`, and the opt-in real-browser `test_scheduler_browser.py`.

Behaviour (all from the approved policy):
- A schedule is a playbook plus rows of inputs (one run per row), a time (once, every N minutes/hours, daily,
  certain weekdays; local time), and a choice of what to do when a row can't finish: stop and tell me
  (the schedule pauses until resumed) or tell me and carry on with the next row.
- Only APPROVED playbooks can be scheduled. Editing a playbook removes its approval, and the scheduler then
  refuses to run it and pauses the schedule with the reason. A playbook with send/post steps also needs the
  schedule's own "let these run while I'm away" tick. A playbook with a hand-off or AI step can't be scheduled.
- Unattended runs never ask: a pre-approved step runs only if the element is found with certainty, otherwise
  the run stops "needs you" (verified in a real browser: it does not press Send when unsure).
- Fires only while the app is open. A run missed by more than 5 minutes is skipped and recorded, never run late.
- One run at a time, never during the user's own task, and never within 60 s of the user using the live view
  or toolbar by hand: due work waits.
- Pacing: per-activity daily allowances that ramp up weekly (LinkedIn profile views 50/day rising 10/week to
  150; sends 15 rising to 25), editable, and a random 30-60 s gap between runs on a site. A day's allowance used
  up holds the rest of a batch until tomorrow (one notice). Manual runs count toward the same allowances.
- Notifications: inbox (300 kept, 30 days) plus a Windows toast for warnings/errors (sent as PowerShell,
  because Windows only shows toasts from registered apps). Quiet successes are not announced; a batch of several
  rows gets one summary. A "pause all schedules" switch stops everything.
- Retention: run records and inbox entries older than 30 days are pruned once a day.

Not built / known limits: no quiet hours; a daily time that falls in the hour a clock change skips counts as
missed; the batch continues after an app restart (it is not "missed"); the app must be running (no background
service); the Windows toast could not be seen from here, only sent without error (a test toast was sent).
