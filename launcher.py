"""
Starts AI Browser Agent: finds a Python that has the app's packages, launches agent_ui.py
without a console window, and opens the UI in the browser.

This is a LAUNCHER, not a standalone program. The built "AI Browser Agent.exe" does not contain
Python or the app; it must sit in the project folder next to agent_ui.py and uses a Python that has
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
import webbrowser
from pathlib import Path
from typing import Callable, Optional

REQUIRED_MODULES = ("browser_use", "fastapi", "uvicorn", "psutil")
PORT = 8000
CREATE_NO_WINDOW = 0x08000000
_CHECK = ("import importlib.util as u; "
          f"print(' '.join(m for m in {REQUIRED_MODULES!r} if u.find_spec(m) is None))")


def app_dir() -> Path:
    """Where the project lives. A frozen exe reports its own folder via sys.executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def log(base: Path, message: str) -> None:
    try:
        with open(base / "launcher.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {message}\n")
    except OSError:
        pass


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
def stop_previous_server(base: Path) -> None:
    """Stop whatever is listening on our port. /T also ends its child processes (the agent's
    Chromium), which would otherwise be left running."""
    try:
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if f":{PORT} " in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                log(base, f"Killing PID {pid} on port {PORT}")
                subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
                time.sleep(1)
                break
    except Exception as e:
        log(base, f"Port kill error: {e}")


# ------------------------------------------------------------------------------ main
def main() -> int:
    base = app_dir()
    log(base, "=== Launcher started ===")
    log(base, f"base={base} frozen={getattr(sys, 'frozen', False)}")

    script = base / "agent_ui.py"
    if not script.exists():
        log(base, f"ERROR: {script} not found")
        message_box("agent_ui.py was not found next to this program.\n\n"
                    f"This launcher must stay in the project folder:\n{base}")
        return 1

    python, report = pick_python(base)
    if python is None:
        log(base, "ERROR: no suitable Python. " + repr(report))
        message_box(explain_no_python(report))
        return 1
    log(base, f"Python: {python}")

    stop_previous_server(base)

    err_log = open(base / "agent_error.log", "w", encoding="utf-8")
    try:
        proc = subprocess.Popen([python, str(script)], cwd=str(base), creationflags=CREATE_NO_WINDOW,
                                stderr=err_log, stdout=subprocess.DEVNULL)
        log(base, f"Server started, PID={proc.pid}")
    except Exception as e:
        log(base, f"Launch error: {e}")
        message_box(f"Could not start the app:\n{e}")
        return 1

    time.sleep(4)
    webbrowser.open(f"http://localhost:{PORT}")
    log(base, "Browser opened.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
