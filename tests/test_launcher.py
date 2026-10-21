"""Tests for launcher.py. Nothing is launched: subprocesses, dialogs and the browser are faked."""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER_DIR = ROOT / "packaging" / "windows"
sys.path.insert(0, str(LAUNCHER_DIR))        # the launcher is a Windows script, not part of the aiba package
import launcher  # noqa: E402


def result(out="", code=0):
    return SimpleNamespace(returncode=code, stdout=out, stderr="")


def put_app_in(base):
    entry = base.joinpath(*launcher.ENTRY)
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("")


class Candidates(unittest.TestCase):
    def setUp(self):
        self.base = pathlib.Path(tempfile.mkdtemp())

    def find(self, env=None, py_launcher="C:\\Py\\launcher\\python.exe", which=None):
        which = which or {"python": "C:\\path\\python.exe", "python3": None}
        run = mock.Mock(return_value=result(py_launcher + "\n") if py_launcher else result("", 1))
        return launcher.python_candidates(self.base, env=env or {}, run=run, which=lambda n: which.get(n))

    def test_order_is_env_then_project_venv_then_py_launcher_then_path(self):
        (self.base / ".venv" / "Scripts").mkdir(parents=True)
        (self.base / ".venv" / "Scripts" / "python.exe").write_text("")
        got = self.find(env={"AIBA_PYTHON": "D:\\mine\\python.exe"})
        self.assertEqual(got, ["D:\\mine\\python.exe", str(self.base / ".venv" / "Scripts" / "python.exe"),
                               "C:\\Py\\launcher\\python.exe", "C:\\path\\python.exe"])

    def test_the_microsoft_store_stub_is_never_offered(self):
        stub = "C:\\Users\\x\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe"
        got = self.find(py_launcher="", which={"python": stub, "python3": stub})
        self.assertEqual(got, [])

    def test_duplicates_are_removed(self):
        got = self.find(py_launcher="C:\\path\\python.exe", which={"python": "C:\\path\\python.exe"})
        self.assertEqual(got, ["C:\\path\\python.exe"])

    def test_a_missing_py_launcher_is_not_an_error(self):
        run = mock.Mock(side_effect=FileNotFoundError("py"))
        got = launcher.python_candidates(self.base, env={}, run=run, which=lambda n: "C:\\path\\python.exe" if n == "python" else None)
        self.assertEqual(got, ["C:\\path\\python.exe"])

    def test_no_hard_coded_user_paths_remain(self):
        source = (LAUNCHER_DIR / "launcher.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"C:\\Users\\[A-Za-z0-9_.-]+")      # no per-user path of any kind
        self.assertNotIn("Python312", source)


class MissingModules(unittest.TestCase):
    def test_reports_exactly_what_is_missing(self):
        self.assertEqual(launcher.missing_modules("p", run=lambda *a, **k: result("")), [])
        self.assertEqual(launcher.missing_modules("p", run=lambda *a, **k: result("fastapi psutil\n")), ["fastapi", "psutil"])

    def test_an_unusable_python_is_none(self):
        self.assertIsNone(launcher.missing_modules("p", run=lambda *a, **k: result("", 1)))
        self.assertIsNone(launcher.missing_modules("p", run=mock.Mock(side_effect=OSError("nope"))))
        self.assertIsNone(launcher.missing_modules("p", run=mock.Mock(side_effect=subprocess.TimeoutExpired("p", 30))))


class Picking(unittest.TestCase):
    def pick(self, answers):
        """answers: {python path: list of missing modules or None}"""
        with mock.patch.object(launcher, "python_candidates", return_value=list(answers)), \
                mock.patch.object(launcher, "missing_modules", side_effect=lambda p, run=None: answers[p]):
            return launcher.pick_python(pathlib.Path("."))

    def test_takes_the_first_python_that_has_everything(self):
        chosen, report = self.pick({"bare": ["browser_use", "fastapi"], "good": [], "also_good": []})
        self.assertEqual(chosen, "good")
        self.assertEqual(list(report), ["bare", "good"])              # stops at the first good one

    def test_none_good_returns_the_full_report(self):
        chosen, report = self.pick({"a": ["uvicorn"], "b": None})
        self.assertIsNone(chosen)
        self.assertEqual(report, {"a": ["uvicorn"], "b": None})

    def test_the_explanation_names_each_python_and_the_fix(self):
        text = launcher.explain_no_python({"C:\\a\\python.exe": ["uvicorn", "psutil"], "C:\\b\\python.exe": None})
        self.assertIn("C:\\a\\python.exe", text)
        self.assertIn("missing: uvicorn, psutil", text)
        self.assertIn("could not be run", text)
        self.assertIn("pip install -r requirements.txt", text)
        self.assertIn("AIBA_PYTHON", text)

    def test_the_explanation_when_there_is_no_python_at_all(self):
        text = launcher.explain_no_python({})
        self.assertIn("No Python was found", text)
        self.assertIn("requirements.txt", text)


class Main(unittest.TestCase):
    def setUp(self):
        self.base = pathlib.Path(tempfile.mkdtemp())
        self.box = mock.Mock()
        for p in (mock.patch.object(launcher, "app_dir", return_value=self.base),
                  mock.patch.object(launcher, "message_box", self.box),
                  mock.patch.object(launcher, "stop_previous_server", return_value=None),
                  mock.patch.object(launcher, "wait_until_up", return_value="up"),
                  mock.patch.object(launcher.webbrowser, "open")):
            p.start()
            self.addCleanup(p.stop)

    def test_refuses_to_start_when_the_app_is_not_next_to_it(self):
        self.assertEqual(launcher.main(), 1)
        self.assertIn("source (src/aiba) was not found", self.box.call_args[0][0])
        self.assertIn(str(self.base), self.box.call_args[0][0])

    def test_explains_when_no_python_has_the_packages(self):
        put_app_in(self.base)
        with mock.patch.object(launcher, "pick_python", return_value=(None, {"C:\\p\\python.exe": ["fastapi"]})):
            self.assertEqual(launcher.main(), 1)
        self.assertIn("missing: fastapi", self.box.call_args[0][0])
        self.assertIn("ERROR: no suitable Python", (self.base / "launcher.log").read_text(encoding="utf-8"))

    def test_starts_the_app_with_the_chosen_python_in_the_project_folder(self):
        put_app_in(self.base)
        with mock.patch.object(launcher, "pick_python", return_value=("C:\\good\\python.exe", {})), \
                mock.patch.object(launcher.subprocess, "Popen", return_value=SimpleNamespace(pid=4242, poll=lambda: None)) as popen:
            self.assertEqual(launcher.main(), 0)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["C:\\good\\python.exe", "-m", "aiba"])
        self.assertTrue(kwargs["env"]["PYTHONPATH"].startswith(str(self.base / "src")))
        self.assertEqual(kwargs["cwd"], str(self.base))
        self.assertEqual(kwargs["creationflags"], launcher.CREATE_NO_WINDOW)
        launcher.webbrowser.open.assert_called_once_with("http://localhost:8000")
        self.assertIn("PID=4242", (self.base / "launcher.log").read_text(encoding="utf-8"))

    def test_a_failed_start_is_reported_not_swallowed(self):
        put_app_in(self.base)
        with mock.patch.object(launcher, "pick_python", return_value=("C:\\good\\python.exe", {})), \
                mock.patch.object(launcher.subprocess, "Popen", side_effect=OSError("access denied")):
            self.assertEqual(launcher.main(), 1)
        self.assertIn("access denied", self.box.call_args[0][0])
        launcher.webbrowser.open.assert_not_called()


    def test_a_foreign_process_on_the_port_stops_the_launch(self):
        put_app_in(self.base)
        with mock.patch.object(launcher, "pick_python", return_value=("p", {})), \
                mock.patch.object(launcher, "stop_previous_server", return_value="Port 8000 is in use"), \
                mock.patch.object(launcher.subprocess, "Popen") as popen:
            self.assertEqual(launcher.main(), 1)
        popen.assert_not_called()
        self.assertIn("in use", self.box.call_args[0][0])

    def test_a_server_that_dies_at_once_shows_its_error_output(self):
        put_app_in(self.base)

        def start(*a, **k):
            k["stderr"].write("ImportError: no module named x" + chr(10))
            return SimpleNamespace(pid=1, poll=lambda: 1)
        with mock.patch.object(launcher, "pick_python", return_value=("p", {})), \
                mock.patch.object(launcher.subprocess, "Popen", side_effect=start), \
                mock.patch.object(launcher, "wait_until_up", return_value="exited"):
            self.assertEqual(launcher.main(), 1)
        self.assertIn("ImportError: no module named x", self.box.call_args[0][0])
        launcher.webbrowser.open.assert_not_called()

    def test_the_error_log_is_appended_to_not_wiped(self):
        put_app_in(self.base)
        (self.base / "agent_error.log").write_text("earlier crash" + chr(10), encoding="utf-8")
        with mock.patch.object(launcher, "pick_python", return_value=("p", {})), \
                mock.patch.object(launcher.subprocess, "Popen", return_value=SimpleNamespace(pid=1, poll=lambda: None)):
            launcher.main()
        self.assertIn("earlier crash", (self.base / "agent_error.log").read_text(encoding="utf-8"))


NETSTAT = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:80             0.0.0.0:0              LISTENING       4
  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       1234
  TCP    127.0.0.1:18000        0.0.0.0:0              LISTENING       77
  TCP    127.0.0.1:8000         127.0.0.1:5000         ESTABLISHED     999
"""


class OldInstance(unittest.TestCase):
    def setUp(self):
        self.base = pathlib.Path(tempfile.mkdtemp())

    def test_finds_only_the_exact_listening_port(self):
        self.assertEqual(launcher.listening_pid(8000, run=lambda *a, **k: result(NETSTAT)), 1234)
        self.assertIsNone(launcher.listening_pid(800, run=lambda *a, **k: result(NETSTAT)))
        self.assertIsNone(launcher.listening_pid(8000, run=mock.Mock(side_effect=OSError)))

    def fake(self, cmdline, freed=True):
        calls = []
        state = {"killed": False}

        def run(args, **k):
            calls.append(args[0])
            if args[0] == "netstat":
                return result("" if state["killed"] and freed else NETSTAT)
            if args[0] == "powershell":
                return result(cmdline or "", 0 if cmdline else 1)
            state["killed"] = True
            return result()
        return run, calls

    def test_our_own_old_server_is_stopped(self):
        run, calls = self.fake('"C:\\Py\\python.exe" -m aiba')
        self.assertIsNone(launcher.stop_previous_server(self.base, run=run, sleep=lambda s: None))
        self.assertIn("taskkill", calls)

    def test_a_copy_from_before_the_package_layout_is_still_recognised(self):
        run, calls = self.fake('"C:\\Py\\python.exe" "C:\\app\\agent_ui.py"')
        self.assertIsNone(launcher.stop_previous_server(self.base, run=run, sleep=lambda s: None))
        self.assertIn("taskkill", calls)

    def test_someone_elses_program_is_never_killed(self):
        for cmd in ('"C:\\node.exe" server.js', None):            # other program / cannot tell
            run, calls = self.fake(cmd)
            msg = launcher.stop_previous_server(self.base, run=run, sleep=lambda s: None)
            self.assertIn("already in use", msg)
            self.assertNotIn("taskkill", calls)

    def test_reports_when_the_old_one_will_not_die(self):
        run, calls = self.fake("python -m aiba", freed=False)
        self.assertIn("did not stop", launcher.stop_previous_server(self.base, run=run, sleep=lambda s: None))

    def test_a_free_port_needs_nothing(self):
        self.assertIsNone(launcher.stop_previous_server(self.base, run=lambda *a, **k: result(""), sleep=lambda s: None))


class PortSetting(unittest.TestCase):
    def test_the_launcher_and_the_app_read_the_same_port_setting(self):
        import subprocess
        import sys
        code = "import launcher; print(launcher.PORT)"
        for value, expected in (("", "8000"), ("8123", "8123")):
            env = {**__import__("os").environ, "AIBA_PORT": value} if value else {k: v for k, v in __import__("os").environ.items() if k != "AIBA_PORT"}
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(LAUNCHER_DIR), env=env).stdout.strip()
            self.assertEqual(out, expected)


class Waiting(unittest.TestCase):
    def wait(self, answers, alive=True, timeout=5):
        t = {"now": 0.0}
        it = iter(answers)
        proc = SimpleNamespace(poll=lambda: None if alive else 1)
        return launcher.wait_until_up(proc, timeout=timeout, probe=lambda url: next(it, False),
                                      sleep=lambda s: t.__setitem__("now", t["now"] + s), clock=lambda: t["now"])

    def test_up_as_soon_as_it_answers(self):
        self.assertEqual(self.wait([False, False, True]), "up")

    def test_gives_up_after_the_timeout(self):
        self.assertEqual(self.wait([]), "timeout")

    def test_a_dead_server_is_reported_immediately(self):
        self.assertEqual(self.wait([], alive=False), "exited")

    def test_any_http_answer_counts_as_up(self):
        import urllib.error
        with mock.patch.object(launcher.urllib.request, "urlopen",
                               side_effect=urllib.error.HTTPError("u", 503, "x", {}, None)):
            self.assertTrue(launcher._answers("http://127.0.0.1:1/"))
        with mock.patch.object(launcher.urllib.request, "urlopen", side_effect=ConnectionRefusedError):
            self.assertFalse(launcher._answers("http://127.0.0.1:1/"))


class Logs(unittest.TestCase):
    def test_a_big_log_is_rotated_once(self):
        base = pathlib.Path(tempfile.mkdtemp())
        (base / "launcher.log").write_text("x" * (launcher.LOG_LIMIT + 1))
        launcher.log(base, "fresh")
        self.assertTrue((base / "launcher.log.old").exists())
        self.assertIn("fresh", (base / "launcher.log").read_text(encoding="utf-8"))
        self.assertLess((base / "launcher.log").stat().st_size, 1000)

    def test_the_other_launchers_just_call_launcher_py(self):
        for name in ("launch.bat", "launch_silent.vbs"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("launcher.py", text)
            self.assertNotIn("agent_ui.py", text.replace("All the logic", ""))
            self.assertNotIn("taskkill", text)
            self.assertNotIn("||", text)        # "a || b" would run the launcher twice when it fails


class ThisMachine(unittest.TestCase):
    def test_it_finds_a_python_with_everything_the_app_needs(self):
        chosen, report = launcher.pick_python(ROOT)
        self.assertIsNotNone(chosen, report)
        self.assertEqual(launcher.missing_modules(chosen), [])


if __name__ == "__main__":
    unittest.main()
