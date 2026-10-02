# Playbooks: design (approved; phases 1 to 4 built, see the status sections at the end)

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


## Phase 3 review and MVP pass (built and tested)
Review fixes:
- **No double sends after a crash.** A row is marked "in flight" before it runs. If the app dies mid-row, the
  row is NOT run again after a restart: it is reported ("may or may not have finished"), counted toward the
  daily allowance, and the schedule's own failure choice decides what happens next.
- **No stale batches.** A batch that waited more than 12 hours (and is not held for a daily allowance) is
  dropped as a missed run instead of starting late.
- The scheduler's wait reasons are a field (`Decision.kind`), not a word searched for in a sentence; a failing
  notification can no longer undo or stop the scheduler's bookkeeping.

MVP limits fixed:
- **Quiet hours** (default off, e.g. 22:00-07:00): nothing starts, no desktop pop-ups; due runs wait and the
  inbox still records everything.
- **Failure report with a picture:** when a run stops, a small JPEG of the page is kept with the run record
  (deleted with it), shown in Run history with the step-by-step account, a "Scheduled" badge and an
  "Edit the playbook" button.
- **"Did you mean...?"**: a step that can't find its element now says what page it is on and which elements
  look closest (or that nothing does, so check it is the right page).
- **Templates:** one click creates an UNAPPROVED playbook (open a LinkedIn profile; search the web) and opens it
  for review. A template is never pre-approved.
- **Results download:** "Download results (CSV)" in Run history: one row per run with its inputs and what it
  read; formula-looking cells are neutralised.
- **LinkedIn sign-in check** button in Schedules: opens the feed and says whether the browser is signed in.

Still limits for after the MVP: general AI steps; self-repair of a broken step (the report now helps a
person fix it); checkpoints/approvals answered from the inbox; a background service so schedules run with the
app closed; a daily time inside a clock-change hour counts as missed.


## Phase 4 status: human checkpoints (built and tested)
A playbook can contain an **approve step** ("Ask you first"): the user is asked, with exactly what is about
to happen (and the text just typed), before the step that follows. In the editor, every risky step has an
"Ask me each time this runs" tick that puts one in front of it.

- **Attended** (you are running it): the question appears as the usual approval prompt. A yes lets the NEXT
  step through without a second question.
- **Unattended** (scheduled): the run is PARKED, not stopped. It is saved with the question, a picture of the
  page, the inputs and the answers so far; the browser is freed at once. The question goes to the inbox (with
  Go ahead / Don't buttons) and shows on the schedule. A schedule can then run a send WITHOUT the blanket
  "let these steps run while I'm away" tick, because every send is asked about.
- **Yes**: the run starts again from the top under the same run id. Steps that reach other people and already
  ran are SKIPPED, never repeated (so no message goes out twice); harmless steps (open, type, click around)
  simply run again. The send only happens if its button is found with certainty, exactly as before; a yes
  does not excuse a doubtful match. Only the one step after the question is covered.
- **No** or **silence** (default 24 h, set per step from 1 hour to 7 days): cancelled, never sent. The history
  says "You said no" / "No answer". An answer that waits more than 24 h past the deadline is void.
- **Safety**: if the playbook was edited or un-approved while waiting, the yes is void. An answered run goes
  before other waiting work, is marked in flight and removed from the list BEFORE it runs, so a crash can't
  run it twice. Only a real `true` is a yes. Deleting a schedule cancels what it was waiting on.
- Tests: `test_playbook.py` (format), `test_playbook_player.py` (Checkpoints), `test_scheduler.py`
  (Checkpoints), `test_schedules_app.py` (CheckpointFlow), and four opt-in real-browser tests
  (`test_scheduler_browser.py`): a scheduled send waits for a yes then sends exactly once; a no never sends;
  silence never sends; a yes does not excuse an unsure match.

### Phase 4 additions: typed answers and "skip on silence" (built and tested)
- **Typed value mid-run** (an `input` step): a text/number variable marked `runtime` is not asked for when
  the run starts; the run asks for it when it gets there (a code, a name). In the editor, a "type" step has
  "Ask me for this text each time this runs", which adds the question and a `{{answer_N}}` variable.
  Attended: a text box in the prompt. Unattended: the run parks like any checkpoint; the inbox / Schedules
  card shows a text box (Send / Don't). The answer is checked like any input (number, length, no secret
  placeholders) both when typed and again when the run starts, is kept only for that run, and is never written
  to the run record, the log, or shown back in a later question ("Typed just before" says "what you typed in
  when asked"). Typing a value approves nothing: a send after it still asks its own question.
  **You decide whether it is kept**: every answer box has "Remember this for next runs" (off by default).
  Unticked, the answer lives in the app's memory for that one run and is NEVER written to disk (if the app is
  closed before the run starts again, the run is cancelled and must be started again, rather than saved
  anywhere). Ticked, it is saved on this computer (`remembered_answers.json`, per playbook and question) and
  that question is no longer asked; the editor lists kept answers (names only) with a Forget button, and
  deleting the playbook forgets them. A kept answer that no longer fits its variable is ignored and asked
  again. Don't tick it for codes or passwords: the file is plain text.
- **Skip and carry on** (`on_timeout: skip` on an approve step): if nobody answers in time, the guarded step
  is left out (never run) and the rest of the playbook carries on, instead of cancelling the whole run. Only
  allowed when the very next step is one that needs approval. A no is still a no. Whatever ran before the
  skipped step (e.g. text typed into a box) stays as it was (the notification says so); if a later step then
  fails, its message says a step was left out because nobody answered, which may be why.
- **Review fixes**: the approvals endpoint no longer treats any truthy value as a yes (only a real `true`);
  answer length capped; a typed answer for a question the playbook no longer has is refused; the editor keeps
  a guard directly in front of the step it guards, and deleting a question puts the typed text back.
- Tests: new classes in `test_playbook.py` (InputStep, SkipOnTimeout), `test_playbook_player.py`
  (InputsDuringTheRun, SkipWhenUnanswered), `test_scheduler.py` (InputQuestions), `test_schedules_app.py`,
  `test_approvals_paths.py`, frontend `shared.test.ts`, `ValueAnswer.test.tsx`, `ApprovalPrompt.test.tsx`,
  and three more opt-in real-browser tests. A 19-item mutation check (break one piece, a test must fail)
  caught every change.

Not built: answering from outside the app (email/phone), by design for now.

## Phase 5 status: scrape to table (built and tested)
A new step kind, **Read a table** (`scrape`), reads data off the page with **no AI and no clicking**:
- **Rows and columns.** A *row selector* (CSS) picks each repeated item (a table row, a card, a search result);
  each *column* has a name, a selector inside the row (`:scope` = the row itself) and a kind: text, number
  ("$1,299.50" becomes 1299.5), link (absolute web address; `javascript:` and other schemes are dropped) or an
  attribute (e.g. `data-id`). With no row selector the page gives one record. Up to 30 columns and 500 rows per
  step (default 100); at most 20,000 cells and 500 characters per cell are kept, and the table says when the page
  had more than was read.
- **Safe by construction.** The page script only reads (`querySelectorAll`, `querySelector`, text, `href`,
  `getAttribute`). Selectors are passed to it as data, never pasted into it. The selectors and columns are part of
  what the user approves (change one and the approval is lost). Reading is not an "ask" action and needs no model.
- **Honest failures.** A page that is still loading gets a few seconds. No rows at all, or a column that matched
  nothing in any row, FAILS the run and names the selector ("the page may have changed"); it never reports an
  empty table as success. A bad selector is reported in words.
- **Results.** The table is stored with the run, shown in the run history, and the first rows appear in the chat.
  `results.csv` becomes one combined table across runs (one line per scraped row, with that run's inputs beside it, so
  a batch of 30 profiles is one spreadsheet; spreadsheet formulas in cells are neutralised) and `results.json`
  gives the same for another program. A playbook keeps its last 500 runs (it was 50) so a big batch isn't lost.
- **Editor.** "Add a step that reads a table", a column editor, and **Test on the page in the browser**: it reads
  the page the browser is showing and previews up to 10 rows (never navigates, clicks or types, and only when no
  task is running). A ready-made template, "Read a list into a table" (Hacker News front page), starts it off.
- **Batch.** Scheduled runs over a list of inputs already run one row at a time with the daily limits, so scraping
  a list of pages or profiles works through the existing schedules.
- Tests: `test_playbook_scrape.py` (cleaning, failure rules, format), `test_playbook_player.py` (ScrapeStep),
  `test_templates_export.py` (tables in CSV/JSON), `test_scrape_app.py` (preview and API), frontend
  `ScrapeEditor.test.tsx`, and nine opt-in real-Chromium tests (`test_scrape_browser.py`: cleaning, links and
  attributes, one record, a page that fills in late, a changed page, a bad selector, the row limit, read-only,
  the preview). A 24-item mutation check (break one piece, a test must fail) caught every change.

### Phase 5 additions: picking, AI suggestions and batch inputs (built and tested)
- **Click to pick.** In the step editor, "Pick on the page" puts a small outline overlay on the page in the browser (the
  dialog steps aside, a bar with Cancel stays): hover to see what you would pick, click one item of the list and the
  rows are found for you, with the other sizes offered ("not the right rows?"); then a pick button per column reads
  the value you click inside one row, picks a selector relative to the row, guesses text / number / link, names the
  column from its class and tells you if it is found in only some rows. Clicks are swallowed while picking, so the
  page does not react; the overlay is removed on finish or cancel and removes itself after five minutes. Anything the
  page reports back is treated as untrusted and cut to size. It cannot see into iframes or shadow DOM (nor can the
  reading step).
- **Suggest with AI.** Describe what you want ("the product names and prices") and your model proposes the row and column
  selectors from a short outline of the page (no scripts, styles, images or typed values; link query strings
  removed; repeated items collapsed; about 12,000 characters at most). The page text is marked as data the model must
  not obey, and the model can only answer with a row selector and columns. The proposal is TRIED on the page; columns
  that find nothing are dropped and said; you see the rows and press "Use this" (nothing changes before). It uses the
  new "selectors" job of the model router (Standard tier by default) and is counted in the usage ledger.
- **Batch inputs.** A schedule's rows can come from a CSV file or pasted spreadsheet rows (a header row picks the
  columns by input name or label; without one, columns go in order; at most 200 rows and 1 MB), or from a column of
  another playbook's scraped results (each value once). "Check the first 3 rows" is a dry run that opens no browser:
  it validates every row, shows exactly what the first three would do (a stored secret stays a placeholder) and says
  how many days the daily limits will spread the batch over.
- **Tests.** `test_playbook_picker.py`, `test_playbook_suggest.py`, `test_batch_check.py`, frontend `csv.test.ts`,
  `BatchInputs.test.tsx`, `ScrapePick.test.tsx`, `ScrapeSuggest.test.tsx`, and 12 more opt-in real-Chromium tests
  (`test_scrape_browser.py`: picking cards and table rows, columns, links, clicks outside rows, the page not
  reacting while picking, the digest leaking nothing, a suggestion read end to end). A 35-item mutation check caught
  every change (it found two pieces of redundant code, which were simplified, and two untested messages).

### Phase 5 limits closed: shadow DOM, iframes and "for each row" (built and tested)
- **Shadow DOM and iframes.** A selector may step INTO an open shadow root or a same-site iframe with `>>>`
  (`#host >>> li.item`, `#frame >>> div.row`, as deep as the page goes). The reader, the click-to-pick tool and the
  outline sent to the AI all understand it: picking inside a shadow root or a frame produces the right `>>>` selector, and
  the AI is told when and how to use it. An iframe from another site cannot be entered (the page cannot see into it), so
  nothing is found there, and a closed shadow root is not reachable. A selector without `>>>` behaves as before.
- **For each row** (`foreach` step). Takes a column of an earlier "read a table" step and runs the next 1 to 20 steps once
  per value, with the value in a loop variable (`{{item}}`). The typical use: read a list, open each link, read the detail
  page; every table read inside the loop gets the loop value as its first column, so the combined CSV shows which row
  each line came from. Rules, all checked when the playbook is saved and again as it runs:
  - values come from a page, so each is checked against the loop variable before it is used (a web address must be on the
    sites the variable allows; a number must be a number); ones that fail are left out and said, and if none pass the run
    fails without visiting anything; repeated and empty values are used once or not at all;
  - at most 50 rows (default 20), a 1.5 s pause between rows, and the whole run keeps at most 60,000 table cells;
  - only plain steps may be inside a loop (open, search, back, click, type, select, key, scroll, wait, tab, read a table):
    nothing that sends or asks first, no AI steps, no nested loops, and not on LinkedIn (its visits are paced and capped,
    so use a schedule with one row per profile there);
  - "if a row fails": stop the run (default) or skip that row. A sign-in wall, a page off the approved sites, a refused
    action or the time limit always stop the run, never skipped;
  - the loop variable's type and sites, the column, the source and the number of steps are all part of the approval.
- **Editor.** "Add a loop: open each link and read it" adds the whole pattern in one click (a web-address variable limited to
  the site of the first page); the loop step has controls for the table and column, how many steps repeat (and lists them),
  what each value is and which sites it may open, the row limit and what a failing row does.
- **Tests.** `test_foreach.py` (39: format rules, the loop, limits, failures, budgets), `test_playbook_scrape.py` (arrows),
  frontend `ForEach.test.tsx`, and real-Chromium tests for shadow roots, nested shadow roots, iframes, picking inside
  them, the page outline, and a loop over a real list of people (off-site link refused, a bad row skipped, the combined CSV).

Not built: change monitoring on a scraped table (phase 6), and Google Sheets or webhook export (explicit opt-in, later).

## Phase 6 status: change monitoring (built and tested)
A schedule can **watch** what its playbook reads and tell you only when it changed. Unchanged runs cost nothing and say
nothing (no model is involved at any point).
- **Setting it up.** In the schedule form, "Watch for changes" (shown for playbooks with a "read a table" step; off by default):
  the columns that identify a row (default: the first column not left out), columns to leave out, patterns to cut out of every
  cell before comparing (for "3 minutes ago" or a counter; patterns that could hang are refused), the smallest change worth
  hearing about for numbers (in percent), and which kinds of change to report (new rows, gone rows, changed values).
- **What is compared.** Each run's tables are compared with the last snapshot of the same schedule and the same inputs (a schedule over
  30 pages watches 30 things, each with its own history). Rows are matched by key, so a price that moved is one changed row, not
  one gone and one new. One record both times (a profile, a product page) is the same record: what in it moved is the change. Row
  order and extra spaces are not changes. Tables are matched by their step, so a loop's pages are matched by their loop value.
- **Honest about noise and drift.** Small number moves under your minimum are ignored, but the reference snapshot stays put, so
  small moves add up and are reported once they pass the minimum. Changing the watch settings starts a new comparison (said once)
  instead of a wrong diff. A run with a failed step, or that read no table, is not compared and does not spoil the reference.
  If a run worked but nothing could be compared, you are told (at most once a day) so you don't believe it is being watched.
- **What you hear.** One message per batch: "Changed: <schedule>" with what changed ("Ann: Price 5 → 6; New: Dee; Gone: Bob"), or
  "3 of 30 changed" with the first few rows; "Now watching" the first time. A watching schedule does not announce "all rows ran".
  The run history marks each run (First look, Changed, No change) and shows every changed, new and gone row; the schedule list shows the
  last change.
- **Where it is kept.** The last 10 snapshots per schedule and inputs, in `watch_snapshots` in the data folder, pruned with the 30-day
  retention and removed with the playbook. They hold what was scraped, so treat them as you do the run history. Only scheduled runs
  are compared (a manual run neither compares nor changes the reference).
- **Tests.** `test_monitor.py` (settings, comparing, reporting), `test_snapshots.py`, `test_watcher.py`, `test_watch_app.py` (the
  scheduler, notifications, API), frontend `Watch.test.tsx`, and real-Chromium tests (`test_watch_browser.py`: a price page that changes
  between scheduled runs, a stamp that changes by itself, a page that breaks). The real page found one bug the fakes missed (a
  one-record table keyed by its own value), fixed above.

Not built: a model-written summary of a change (only when something changed), email/Telegram/webhook delivery (inbox and desktop only
for now), and a graph of a value over time.

## Phase 7 status: self-repair (built and tested)
When a recorded step can't find its button or field, or a table's selectors stopped matching, the playbook can ask an AI model to
pick the likely replacement from what is really on the page, instead of stopping. It is **off unless the playbook says so**
("Let AI repair a step when the page has changed" in the editor), and that choice is part of what the user approves (switching it on
costs the approval, like any change to what a run may do).
- **What it may repair.** A click, type, select or scroll whose element is not found (after the usual wait), and a "read a table"
  step whose rows or columns match nothing. **Never** a step that sends or posts (`ask`), never typing a stored password or other
  secret, never key presses, and never a step that WAS found (a repair only starts after the normal search fails).
- **How it is kept safe.** The model sees a short numbered list of elements already on the page (at most 10, only ones that look like
  the recorded one and are a compatible kind: a link and a button can stand in for each other, an input cannot become a link),
  with page text marked as data it must not obey, and may answer only with a number or "none" (unsure means none). It cannot invent a
  selector or an index. For a table it must find ALL the recorded columns by their recorded names and kinds, and the result is tried
  on the page before it is used. A repaired match is only ever **medium confidence**, so a step the user pre-approved is never
  auto-approved on the strength of a guess, and the safety guard still stops risky clicks by their label (an unattended repaired click
  it would stop becomes "needs you"). At most 3 repairs a run; a table repair found in a loop is reused for the remaining rows.
- **It never rewrites the playbook by itself.** Every repair is written into the run record and the chat ("Repaired step 2: ..."), a
  scheduled run posts one inbox message ("Repaired 2 steps"), and the run history shows what was repaired and why. "Update the
  playbook with these" (steps can be unticked) saves a NEW version, only if the playbook is still the version that run used, and
  asks for approval again. Until then each run repairs again (and costs a call).
- **Cost and privacy.** One call to the Fast model tier ("repair" job) per repair attempt; the model is sent the element names and
  attributes from the page in view (for a table, the same short outline as "Suggest with AI": no scripts, typed values or link
  query strings). Calls are counted in the usage ledger.
- **Tests.** `test_playbook_repair.py` (shortlist, choosing, table selectors, applying), `test_repair_player.py` (21: what is and is not
  repaired, caps, confidence, the guard, loops), `test_repair_app.py` (building the repairer, the inbox message, applying through the
  API), frontend `RepairsPanel.test.tsx`, and real-Chromium tests (`test_repair_browser.py`: a renamed, moved button and a
  restructured table are repaired and read correctly, the playbook is unchanged until updated, an updated version works with repair
  off and no model, an unrelated button is never offered).

Not built: repairing a step by looking at a screenshot, automatically updating the playbook after repeated identical repairs (it
only ever offers), and repairing a step in a loop body differently per row.

## Phase 8 status: natural language to playbook (built and tested)
In the Playbooks window, "Describe a task and get a playbook": type what you want ("open https://news.ycombinator.com and read the
title and link of every story"), and your AI model plans it, the plan is TRIED in the browser on the left, and you get an unsaved,
unapproved draft to review, with a report of how each step did. Replays cost nothing, as for any playbook.
- **Stage 1, the plan.** The Strong model (the router's "draft" job) answers with a short plan made only of known step kinds (open,
  search, click, type, select, scroll, wait, back, read a table, ask-you, hand-off), a name, and variables with examples. It cannot
  write an element index or a fingerprint: it says "the button called Next". The plan is cut down hard: unknown kinds and fields are
  refused by the schema, a stored secret in any step is refused, a step using a variable that doesn't exist is dropped, and every drop
  is explained.
- **Stage 2, the walk.** The plan is tried on the live browser through the SAME player logic a replay uses, one step at a time:
  - a page is opened only if you wrote that site in your description (or the address is a variable, or the page was already open);
    a site the model chose by itself is NOT visited unless you tick "Let it open sites I did not mention";
  - an element named in words is found on the page by its text (exact, then a whole-word match, with the kind respected: a button is
    a button, link or submit input, a field is not), and only when ONE element is clearly best; otherwise the model chooses among a
    short list of close ones; otherwise it is reported with the nearest names and nothing after it is tried;
  - a table is read with selectors the model proposes and that are tried on the page, and the first rows it would read are SHOWN;
  - a step that sends or posts (by its words, or because the safety guard would ask) is found and marked "needs approval" but
    never run, and nothing after it is tried; a hand-off stops the walk too; questions are added but not asked.
- **What you get.** A draft in the usual editor (unapproved, nothing stored until Save), plus "how the draft did": per step
  "worked on the page / added, not run / couldn't do this / failed / not tried", sample rows, and what drafting cost (tokens from the
  planning call and every model call the walk made). Problems that would stop it being saved are listed.
- **Optional.** "Let it look at the page in the browser first" sends a short outline of the page in view (same outline as "Suggest
  with AI": no scripts, typed values or link query strings) with the description. It never touches your other tabs.
- **Safety summary.** The description and page outline are marked as data the model must not obey; its answer is a schema; every
  step is checked on the real page; nothing risky runs; the draft is unapproved; and you read it before it can run by itself.
- **Tests.** `test_playbook_draft.py` (cleaning, host rule, finding elements, 20 walk cases), `test_draft_app.py` (the job through
  the app: start, poll, stop, busy, no model, failure), frontend `Describe.test.tsx`, and eight real-Chromium tests
  (`test_draft_browser.py`: a page and a table become a playbook that replays to the same rows, a link is followed to the next page,
  a Buy button is never pressed, a button really is pressed, typing uses the example and the saved draft takes a new value, an
  unnamed site is never visited, and other tabs are untouched).

Not built: drafting several playbooks at once, a plan that loops over a table (use the loop button in the editor afterwards), and
asking the user a clarifying question before planning.
