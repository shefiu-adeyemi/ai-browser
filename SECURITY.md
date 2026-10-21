# Security policy

This software drives a real browser, may hold logged-in sessions and API keys, and acts on web pages
that can contain hostile instructions. Security reports are welcome and taken seriously.

## Reporting a vulnerability

Please **do not open a public issue** for a vulnerability. Use GitHub's private
[security advisory form](https://github.com/shefiu-adeyemi/ai-browser/security/advisories/new) for this repository.
Include what you found, how to reproduce it and the impact you expect. You will get an answer as soon as it can be
looked at, and a fix before any public write-up.

## What is in scope

- Ways for a web page to make the agent act outside what the user asked (prompt injection that gets past the
  action guard in `src/aiba/agent/action_guard.py`).
- Ways for another website or local program to use the control server (`/ws`, `/task`, `/settings`, approvals).
- Leaks of API keys, the stored login password or browser profiles.

## How the project defends itself

The design is described in [docs/architecture.md](docs/architecture.md#safety-model) and the findings of an
item-by-item audit are in [docs/security-audit.md](docs/security-audit.md). Three things worth knowing:

- Secrets and browser profiles live outside the project folder, in the user data directory.
- Risky actions need a human click; the guard is code, not a prompt.
- The guard gates *actions*, not *what the model reads*: an injected page can still steer what the agent
  summarises. That limit is documented in the audit (#62).

## Supported versions

Only the latest release on the default branch is supported.
