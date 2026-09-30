"""Tests for launcher.py. Nothing is launched: subprocesses, dialogs and the browser are faked."""
import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import launcher

ROOT = pathlib.Path(__file__).parent


def result(out="", code=0):
    return SimpleNamespace(returncode=code, stdout=out, stderr="")


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
        source = (ROOT / "launcher.py").read_text(encoding="utf-8")
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
                  mock.patch.object(launcher, "stop_previous_server"),
                  mock.patch.object(launcher.time, "sleep"),
                  mock.patch.object(launcher.webbrowser, "open")):
            p.start()
            self.addCleanup(p.stop)

    def test_refuses_to_start_when_the_app_is_not_next_to_it(self):
        self.assertEqual(launcher.main(), 1)
        self.assertIn("agent_ui.py was not found", self.box.call_args[0][0])
        self.assertIn(str(self.base), self.box.call_args[0][0])

    def test_explains_when_no_python_has_the_packages(self):
        (self.base / "agent_ui.py").write_text("")
        with mock.patch.object(launcher, "pick_python", return_value=(None, {"C:\\p\\python.exe": ["fastapi"]})):
            self.assertEqual(launcher.main(), 1)
        self.assertIn("missing: fastapi", self.box.call_args[0][0])
        self.assertIn("ERROR: no suitable Python", (self.base / "launcher.log").read_text(encoding="utf-8"))

    def test_starts_the_app_with_the_chosen_python_in_the_project_folder(self):
        (self.base / "agent_ui.py").write_text("")
        with mock.patch.object(launcher, "pick_python", return_value=("C:\\good\\python.exe", {})), \
                mock.patch.object(launcher.subprocess, "Popen", return_value=SimpleNamespace(pid=4242)) as popen:
            self.assertEqual(launcher.main(), 0)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["C:\\good\\python.exe", str(self.base / "agent_ui.py")])
        self.assertEqual(kwargs["cwd"], str(self.base))
        self.assertEqual(kwargs["creationflags"], launcher.CREATE_NO_WINDOW)
        launcher.webbrowser.open.assert_called_once_with("http://localhost:8000")
        self.assertIn("PID=4242", (self.base / "launcher.log").read_text(encoding="utf-8"))

    def test_a_failed_start_is_reported_not_swallowed(self):
        (self.base / "agent_ui.py").write_text("")
        with mock.patch.object(launcher, "pick_python", return_value=("C:\\good\\python.exe", {})), \
                mock.patch.object(launcher.subprocess, "Popen", side_effect=OSError("access denied")):
            self.assertEqual(launcher.main(), 1)
        self.assertIn("access denied", self.box.call_args[0][0])
        launcher.webbrowser.open.assert_not_called()


class ThisMachine(unittest.TestCase):
    def test_it_finds_a_python_with_everything_the_app_needs(self):
        chosen, report = launcher.pick_python(ROOT)
        self.assertIsNotNone(chosen, report)
        self.assertEqual(launcher.missing_modules(chosen), [])


if __name__ == "__main__":
    unittest.main()
