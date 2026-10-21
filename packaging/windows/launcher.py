"""
Starts AI Browser Agent: finds a Python that has the app's packages, launches the app
(python -m aiba) without a console window, and opens the UI in the browser.

This is a LAUNCHER, not a standalone program. The built "AI Browser Agent.exe" does not contain
Python or the app; it must sit in the project folder (next to src/) and uses a Python that has
the packages from requirements.txt installed. (A fully self-contained build would have to bundle
Python, browser-use and a Chromium; that is a different, much larger packaging job.)

Looking for Python, in order: AIBA_PYTHON (env var), <project>\\.venv, <project>\\venv, the Windows
"py -3" launcher, then `python` / `python3` on PATH. A candidate is only used if it can actually
find every package the app needs, so a bare system Python doesn't get picked and then fail later
with an unexplained crash.
"""

import datetime
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Optional

REQUIRED_MODULES = ("browser_use", "fastapi", "uvicorn", "psutil")
PORT = int(os.environ.get("AIBA_PORT", "8000"))     # same setting the app reads (for a second copy side by side)
CREATE_NO_WINDOW = 0x08000000
START_TIMEOUT = 90           # seconds to wait for the server to answer (first start imports a lot)
LOG_LIMIT = 200_000          # bytes; a log bigger than this is moved to <name>.old before writing
_CHECK = ("import importlib.util as u; "
          f"print(' '.join(m for m in {REQUIRED_MODULES!r} if u.find_spec(m) is None))")


def app_dir() -> Path:
    """Where the project lives. A frozen exe reports its own folder via sys.executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]       # packaging/windows/launcher.py -> the project folder


ENTRY = ("src", "aiba", "__main__.py")            # what is started, relative to the project folder


def app_command(python: str) -> list[str]:
    return [python, "-m", "aiba"]


def app_env(base: Path, env: Optional[dict] = None) -> dict:
    """The environment for the app: the source folder on the path, so no install step is needed."""
    out = dict(os.environ if env is None else env)
    src = str(base / "src")
    out["PYTHONPATH"] = src + (os.pathsep + out["PYTHONPATH"] if out.get("PYTHONPATH") else "")
    return out


def rotate(path: Path, limit: int = LOG_LIMIT) -> None:
    """Keep one previous generation instead of growing forever (or wiping the evidence each start)."""
    try:
        if path.exists() and path.stat().st_size > limit:
            os.replace(path, path.with_name(path.name + ".old"))
    except OSError:
        pass


def log(base: Path, message: str) -> None:
    try:
        path = base / "launcher.log"
        rotate(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {message}" + chr(10))
    except OSError:
        pass


def tail(path: Path, lines: int = 12) -> str:
    try:
        return chr(10).join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def message_box(text: str, title: str = "AI Browser Agent") -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        print(f"{title}: {text}")


# ------------------------------------------------------------------- finding Python
def _is_store_stub(path: str) -> bool:
    """The Microsoft Store 'python.exe' alias just opens the Store; it is not a Python."""
    return "windowsapps" in path.lower()


def python_candidates(base: Path, env=os.environ, run: Callable = subprocess.run,
                      which: Callable = shutil.which) -> list[str]:
    found: list[str] = []

    def add(path: Optional[str]) -> None:
        if path and path not in found and not _is_store_stub(path):
            found.append(path)

    add(env.get("AIBA_PYTHON", "").strip() or None)
    for venv in (".venv", "venv"):
        exe = base / venv / "Scripts" / "python.exe"
        if exe.exists():
            add(str(exe))
    try:
        r = run(["py", "-3", "-c", "import sys; print(sys.executable)"], capture_output=True,
                text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        if r.returncode == 0:
            add(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else None)
    except (OSError, subprocess.SubprocessError):
        pass
    add(which("python"))
    add(which("python3"))
    return found


def missing_modules(python: str, run: Callable = subprocess.run) -> Optional[list[str]]:
    """Which required packages this Python cannot find; None if it could not be run at all."""
    try:
        r = run([python, "-c", _CHECK], capture_output=True, text=True, timeout=30,
                creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.split()


def pick_python(base: Path, **kw) -> tuple[Optional[str], dict[str, Optional[list[str]]]]:
    """(the first Python that has everything, what each candidate was missing)."""
    report: dict[str, Optional[list[str]]] = {}
    run = kw.get("run", subprocess.run)
    for candidate in python_candidates(base, **kw):
        missing = missing_modules(candidate, run=run)
        report[candidate] = missing
        if missing == []:
            return candidate, report
    return None, report


def explain_no_python(report: dict[str, Optional[list[str]]]) -> str:
    if not report:
        return ("No Python was found on this computer.\n\nInstall Python 3.12 from python.org, then run:\n"
                "  pip install -r requirements.txt\nin the project folder.")
    lines = ["Found Python, but none has everything the app needs:\n"]
    for path, missing in report.items():
        lines.append(f"  {path}\n    " + ("could not be run" if missing is None
                                          else "missing: " + ", ".join(missing)))
    lines.append("\nFix: open a terminal in the project folder and run\n  pip install -r requirements.txt")
    lines.append("(or set AIBA_PYTHON to the Python that already has them installed).")
    return "\n".join(lines)


# ------------------------------------------------------------------ the old instance
def listening_pid(port: int = PORT, run: Callable = subprocess.run) -> Optional[int]:
    """The process listening on this port (from netstat), or None."""
    try:
        out = run(["netstat", "-ano"], capture_output=True, text=True, timeout=5,
                  creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].rsplit(":", 1)[-1] == str(port):
            try:
                return int(parts[-1])
            except ValueError:
                return None
    return None


def command_line(pid: int, run: Callable = subprocess.run) -> Optional[str]:
    """What the process was started with, so we only ever stop our own server."""
    try:
        r = run(["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def stop_previous_server(base: Path, run: Callable = subprocess.run, sleep: Callable = time.sleep) -> Optional[str]:
    """If an earlier copy of THIS app holds the port, stop it (with /T, so the agent's Chromium goes
    too). Anything else holding the port is left alone: the return value is then a message for the
    user, because killing an unrelated program (a dev server, a database) is not ours to do."""
    pid = listening_pid(PORT, run)
    if pid is None:
        return None
    cmd = command_line(pid, run)
    if cmd is None or not any(mark in cmd.lower() for mark in ("-m aiba", "agent_ui")):      # agent_ui: copies from before the package layout
        log(base, f"Port {PORT} is held by PID {pid} ({cmd or 'unknown program'}); not ours, leaving it alone")
        who = "an unidentified program" if cmd is None else "another program"
        return (f"Port {PORT} is already in use by {who} (PID {pid}).\n\n"
                "Close it (if it is an old copy of AI Browser Agent, end it in Task Manager) "
                "and start AI Browser Agent again.")
    log(base, f"Stopping the previous AI Browser Agent (PID {pid})")
    run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=CREATE_NO_WINDOW)
    for _ in range(10):                      # wait until the port is actually free
        sleep(0.5)
        if listening_pid(PORT, run) is None:
            return None
    return f"The previous AI Browser Agent (PID {pid}) did not stop. End it in Task Manager and try again."


def _answers(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2).close()
        return True
    except urllib.error.HTTPError:
        return True                          # any HTTP answer means the server is up
    except (OSError, ValueError):
        return False


def wait_until_up(proc, timeout: float = START_TIMEOUT, probe: Callable = _answers,
                  sleep: Callable = time.sleep, clock: Callable = time.monotonic) -> str:
    """'up', 'exited' (the server process died) or 'timeout'."""
    deadline = clock() + timeout
    while True:
        if proc.poll() is not None:
            return "exited"
        if probe(f"http://127.0.0.1:{PORT}/"):
            return "up"
        if clock() >= deadline:
            return "timeout"
        sleep(0.5)


# ------------------------------------------------------------------------------ main
def main() -> int:
    base = app_dir()
    log(base, "=== Launcher started ===")
    log(base, f"base={base} frozen={getattr(sys, 'frozen', False)}")

    entry = base.joinpath(*ENTRY)
    if not entry.exists():
        log(base, f"ERROR: {entry} not found")
        message_box("The app's source (src/aiba) was not found next to this program." + chr(10) + chr(10)
                    + f"This launcher must stay in the project folder:{chr(10)}{base}")
        return 1

    python, report = pick_python(base)
    if python is None:
        log(base, "ERROR: no suitable Python. " + repr(report))
        message_box(explain_no_python(report))
        return 1
    log(base, f"Python: {python}")

    blocked = stop_previous_server(base)
    if blocked:
        message_box(blocked)
        return 1

    err_path = base / "agent_error.log"
    rotate(err_path)
    try:
        with open(err_path, "a", encoding="utf-8") as err_log:     # closed here; the child has its own handle
            err_log.write(f"{chr(10)}=== start {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ==={chr(10)}")
            err_log.flush()
            proc = subprocess.Popen(app_command(python), cwd=str(base), env=app_env(base), creationflags=CREATE_NO_WINDOW,
                                    stderr=err_log, stdout=subprocess.DEVNULL)
        log(base, f"Server started, PID={proc.pid}")
    except Exception as e:
        log(base, f"Launch error: {e}")
        message_box(f"Could not start the app:{chr(10)}{e}")
        return 1

    state = wait_until_up(proc)
    log(base, f"Server state: {state}")
    if state == "exited":
        message_box("The app stopped right after starting." + chr(10) + chr(10) + (tail(err_path) or "(no error output)")
                    + chr(10) + chr(10) + f"Full log: {err_path}")
        return 1
    webbrowser.open(f"http://localhost:{PORT}")     # on 'timeout' still open it: the page retries on its own
    log(base, "Browser opened.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
