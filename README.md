# AI Browser Agent

Give an AI a real browser and describe what you want in plain English. You watch it work in a live
view, can click and type in that view yourself, and it asks before it does anything irreversible
(sending, posting, buying, deleting).

Built on [browser-use](https://github.com/browser-use/browser-use) with a FastAPI server and a React UI.

## Setup

1. **Python 3.12** and **Node.js 18+**.
2. Install the Python packages: `pip install -r requirements.txt`
   (the first run also downloads a Chromium for the agent if it can't find one).
3. Build the web UI once: `cd frontend && npm install && npm run build`
4. Start it: `python agent_ui.py` and open <http://localhost:8000>
   (or use `launch.bat` / `AI Browser Agent.exe`, which start it without a console window).
5. Open **Settings** in the app, choose a provider and paste its API key.

Your keys and browser profiles are stored **outside this folder**, in
`%LOCALAPPDATA%\AIBrowserAgent` (set `AIBA_DATA_DIR` to move it). Nothing secret lives in the
project, so the folder can be zipped or committed safely. API keys and the LinkedIn password are
encrypted in that file with Windows DPAPI (readable only by your Windows account on this computer; a
settings file copied elsewhere shows the keys as missing and asks you to enter them again).
`.env.example` documents every option.

## Browsing by hand

The browser view has a tab strip, back / forward / reload and an address bar. Type an address to
open it, or any other text to search. It is look-only while the agent works (stop it first), and
usable during a CAPTCHA hand-off. Only `http` and `https` addresses open; this app's own address and
addresses with a username or password in them are refused. Text that isn't an address is searched on
Google; set `AIBA_SEARCH_URL` (for example `https://duckduckgo.com/?q=`) to use another engine. Google can
show a CAPTCHA to automated browsers, which is the usual reason to switch.

Two buttons in the toolbar shape the view: **Fill** zooms the page in 10 to 25% so the grey bands shrink
(cropping a little at the edges) and **Fit** shows the whole page. The chat panel can be resized (drag the
divider), widened, or hidden to a slim rail; your choices are remembered.

To run a second copy beside the first (for testing) set `AIBA_PORT` and `AIBA_DATA_DIR`.

## Safety

- The agent only ever types your stored credentials into that site's own login form.
- Sending, posting, connecting, buying, deleting, downloading and running JavaScript need your
  approval. **Auto** mode (Settings) approves those only on the site your task names, declines the
  rest, and logs every decision.
- CAPTCHAs are never solved by the agent; it asks you to do them in the live view.
- The agent's browser can't open this app's own page, so it can't approve its own requests.

## Developing

| | |
|---|---|
| Tests (fast, no browser) | `python -m unittest` |
| Tests with a real Chromium (slow) | `RUN_BROWSER_TESTS=1 python -m unittest` |
| Frontend tests / build | `cd frontend && npm test` / `npm run build` |
| Continuous checks | `.github/workflows/ci.yml` runs the fast tests, the frontend tests, the build and `npm audit` on every push |
| Dev tools (PyInstaller, test client) | `pip install -r requirements-dev.txt` |
| Build the launcher exe | `build_exe.bat` (not standalone: keep it beside `agent_ui.py`) |
| Known issues / what was fixed | `ISSUES.md` |

Main files: `agent_ui.py` (server), `action_guard.py` + `approvals.py` (safety),
`browser_nav.py` (address bar, tabs, history), `ui_feed.py` (what reaches the chat), `secret_box.py`
(encrypted settings), `run_outcome.py` (did it work?), `task_control.py` (start/stop/continue), `live_view.py`
(browser screen), `chat_store.py` (saved chats), `env_store.py` + `providers.py` + `llm_config.py`
(settings and AI models), `fake_llm.py` (a scripted stand-in for the AI, used by the tests).

## Troubleshooting

- **The exe says no Python has the packages:** run `pip install -r requirements.txt`, or set
  `AIBA_PYTHON` to the Python that already has them.
- **Live view stays blank:** reload the page (a restarted server issues a new session cookie).
- **Port 8000 busy:** the launcher stops the previous instance; if something else uses the port, close it.
- **Two copies on one computer:** give the second its own `AIBA_PORT` (the session cookie is per port, so they don't log each other out).
