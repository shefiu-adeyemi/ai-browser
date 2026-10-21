# Contributing

Thanks for taking a look. Bug reports, fixes and ideas are all welcome.

## Set up

```bash
git clone https://github.com/shefiu-adeyemi/ai-browser.git && cd ai-browser
pip install -e ".[dev]"
cd frontend && npm install && npm run build && cd ..
```

Needs Python 3.12 and Node.js 22+.

## Before you open a pull request

```bash
ruff check .                                   # lint
python -m unittest discover -s tests           # fast tests, no browser, no API keys
cd frontend && npm test && npm run typecheck && npm run build   # if you touched the UI
```

The real-browser tests are opt-in because they launch Chromium (`RUN_BROWSER_TESTS=1 python -m unittest discover -s tests`).
No test needs an API key: the model is replaced by `tests/fake_llm.py`.

## Guidelines

- **A fix comes with a test that fails without it.** Many tests here exist because a unit test passed while the real
  app failed, so prefer a test that exercises the real behaviour (a real Chromium, a real HTTP call) over a mock of it.
- **Keep the safety layer honest.** Anything that sends, posts, buys, deletes or types a credential must go through
  `GuardedTools`. Do not add a way around it.
- **No secrets, personal data or real people's names** in code, tests, fixtures or docs. Use invented data.
- **Keep changes focused.** Match the existing style; ruff is the arbiter. Comments explain *why*, not *what*.
- **Tests must not depend on today's date or on the network.** Pass a clock in, as the existing tests do.

## Layout

See [docs/architecture.md](docs/architecture.md) for the package map and how a task flows through the code.
