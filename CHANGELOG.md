# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.1.0] - First public release

### Added
- Live browser view with manual control, tabs, address bar and an agent that works in a real Chromium.
- A code-level safety guard (allow / ask / block) between the model and the browser, with approval modes.
- Five AI providers (Claude, OpenAI, Gemini, DeepSeek, Qwen), model tiers, a usage ledger and a vision toggle.
- A repair step for DeepSeek's malformed action answers.
- `list_options` and `set_option` actions for checkboxes and radio buttons that a page hides from the element list.
- Honest runs: an end-of-run check, a step-limit pause with Continue, and a report of what was found when a run is stopped.
- Playbooks: record a run, replay it without a model, repair broken steps, draft one from a description.
- Schedules with per-site pacing limits, change watching and notifications.
- Packaged as `src/aiba` with a `tests/` suite, CI, lint configuration and documentation.

### Security
- Findings from an item-by-item audit are fixed or documented in `docs/security-audit.md`.
