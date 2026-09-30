# Playbooks: design (approved, not yet built)

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
