"""Unit tests (no browser) for the lifecycle review fixes."""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import pathlib
import unittest
from types import SimpleNamespace
from unittest import mock

from aiba.server import main as agent_ui
from aiba.agent import browser_cleanup
from aiba.server.task_control import TaskController


# ------------------------------------------------------------------ stale browser cleanup
class FakeProc:
    def __init__(self, pid, name, cmdline):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "cmdline": cmdline}
        self.killed = False

    def kill(self):
        self.killed = True


PROFILE = pathlib.Path(r"C:\Users\someone\AppData\Local\AIBrowserAgent\profiles\ui")


def kill_with(procs):
    with mock.patch.object(browser_cleanup.psutil, "process_iter", return_value=procs), \
            mock.patch.object(browser_cleanup.psutil, "wait_procs"):
        return browser_cleanup.kill_stale_browsers(PROFILE)


class StaleBrowsers(unittest.TestCase):
    def test_kills_only_browsers_started_with_our_profile(self):
        ours   = FakeProc(1, "chrome.exe", ["chrome.exe", f"--user-data-dir={PROFILE}", "--headless=new"])
        ours2  = FakeProc(2, "chrome.exe", ["chrome.exe", "--type=renderer", f"--user-data-dir={PROFILE}"])
        yours  = FakeProc(3, "chrome.exe", ["chrome.exe", "--profile-directory=Default"])   # your normal Chrome
        other  = FakeProc(4, "chrome.exe", ["chrome.exe", r"--user-data-dir=C:\Users\someone\OtherProfile"])
        prefix = FakeProc(5, "chrome.exe", ["chrome.exe", f"--user-data-dir={PROFILE}-backup"])   # look-alike path
        notchr = FakeProc(6, "python.exe", ["python.exe", f"--user-data-dir={PROFILE}"])
        nocmd  = FakeProc(7, "chrome.exe", None)
        n = kill_with([ours, ours2, yours, other, prefix, notchr, nocmd])
        self.assertEqual(n, 2)
        self.assertEqual([p.killed for p in (ours, ours2)], [True, True])
        self.assertFalse(any(p.killed for p in (yours, other, prefix, notchr, nocmd)))

    def test_path_spelling_does_not_matter(self):
        p = FakeProc(1, "Chrome.EXE", ["x", '--user-data-dir="c:/users/someone/appdata/local/aibrowseragent/profiles/ui/"'])
        self.assertEqual(kill_with([p]), 1)

    def test_processes_that_vanish_or_deny_access_are_skipped(self):
        class Gone(FakeProc):
            def kill(self):
                raise browser_cleanup.psutil.NoSuchProcess(9)
        self.assertEqual(kill_with([Gone(9, "chrome.exe", [f"--user-data-dir={PROFILE}"])]), 1)   # no crash


# ---------------------------------------------------------------------------- tab trimming
class Target:
    def __init__(self, tid): self.target_id = tid


class FakeSession:
    def __init__(self, n, focused):
        self.session_manager = SimpleNamespace(get_all_page_targets=lambda: [Target(f"t{i}") for i in range(n)])
        self.agent_focus_target_id = focused
        self.closed = []

        async def close(params):
            self.closed.append(params["targetId"])
        self.cdp_client = SimpleNamespace(send=SimpleNamespace(Target=SimpleNamespace(closeTarget=close)))


class TrimTabs(unittest.IsolatedAsyncioTestCase):
    async def test_closes_oldest_extras_but_never_the_focused_tab(self):
        s = FakeSession(12, focused="t0")                     # the OLDEST tab is the one in use
        n = await browser_cleanup.trim_tabs(s, keep=8)
        self.assertEqual(n, 4)
        self.assertNotIn("t0", s.closed)
        self.assertEqual(s.closed, ["t1", "t2", "t3", "t4"])

    async def test_under_the_limit_closes_nothing(self):
        s = FakeSession(5, focused="t2")
        self.assertEqual(await browser_cleanup.trim_tabs(s, keep=8), 0)
        self.assertEqual(s.closed, [])

    async def test_housekeeping_never_breaks_a_run(self):
        self.assertEqual(await browser_cleanup.trim_tabs(SimpleNamespace(), keep=8), 0)


# ------------------------------------------------------------------ controller: pause race
class PauseFreesTheSlot(unittest.IsolatedAsyncioTestCase):
    async def test_continue_is_accepted_the_instant_paused_is_shown(self):
        c = TaskController()
        proceed = asyncio.Event()

        async def hit_limit():
            c.pause()                       # the run announces "paused" ...
            await proceed.wait()            # ... and is still winding down
        c.start(hit_limit, origin="a", fmt="auto")
        await asyncio.sleep(0)
        self.assertTrue(c.paused)
        self.assertFalse(c.running)         # slot already free
        g = asyncio.Event()

        async def again():
            g.set()
        c.resume(again)                     # would have raised Busy("already running") before
        await g.wait()
        proceed.set()
        await c._task


# --------------------------------------------------------------- crash safety net & startup
class FakeClient:
    def __init__(self): self.msgs = []
    async def send_json(self, m): self.msgs.append(m)


class CrashSafetyNet(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws = FakeClient()
        agent_ui._clients.add(self.ws)
        agent_ui._ctl.running = agent_ui._ctl.paused = False
        agent_ui._conversation_history.clear()

    async def asyncTearDown(self):
        agent_ui._clients.discard(self.ws)
        agent_ui._conversation_history.clear()

    async def test_a_bad_setting_cannot_leave_the_ui_stuck_on_running(self):
        """CONTEXT_TURNS=abc used to raise before run_task's own try block: status stayed 'running'."""
        agent_ui._conversation_history.append({"role": "user", "content": "earlier", "ts": "10:00"})
        with mock.patch.dict(_os.environ, {"CONTEXT_TURNS": "abc"}):
            self.assertTrue((await agent_ui.start_task({"task": "hello"}))["ok"])
            for _ in range(100):
                if not agent_ui._ctl.running:
                    break
                await asyncio.sleep(0.05)
        self.assertFalse(agent_ui._ctl.running)
        statuses = [m["text"] for m in self.ws.msgs if m["type"] == "status"]
        self.assertEqual(statuses[-1], "error", statuses)
        self.assertTrue(any("[Error]" in m.get("text", "") for m in self.ws.msgs if m["type"] == "action"))

    async def test_the_next_task_can_start_after_a_crash(self):
        with mock.patch.object(agent_ui, "run_task", side_effect=RuntimeError("boom")):
            self.assertTrue((await agent_ui.start_task({"task": "a"}))["ok"])
            for _ in range(100):
                if not agent_ui._ctl.running:
                    break
                await asyncio.sleep(0.05)
        self.assertFalse(agent_ui._ctl.running)
        self.assertTrue((await agent_ui.start_task({"task": "b"}))["ok"])
        await agent_ui.stop_task()


class JudgeSwitch(unittest.TestCase):
    def test_on_by_default_and_switchable(self):
        with mock.patch.dict(_os.environ, {}, clear=False):
            _os.environ.pop("USE_JUDGE", None)
            self.assertTrue(agent_ui._judge_enabled("claude"))
        for off in ("0", "off", "false", "No", " OFF "):
            with mock.patch.dict(_os.environ, {"USE_JUDGE": off}):
                self.assertFalse(agent_ui._judge_enabled("claude"), off)
        with mock.patch.dict(_os.environ, {"USE_JUDGE": "1"}):
            self.assertTrue(agent_ui._judge_enabled("openai"))

    def test_never_on_for_deepseek(self):
        """DeepSeek can't do the structured output the judge needs."""
        with mock.patch.dict(_os.environ, {"USE_JUDGE": "1"}):
            self.assertFalse(agent_ui._judge_enabled("deepseek"))


class SerialisedStartup(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_callers_never_start_the_browser_twice_at_once(self):
        active, peak, starts = 0, 0, 0

        class Slow:
            async def start(self):
                nonlocal active, peak, starts
                active += 1
                starts += 1
                peak = max(peak, active)
                await asyncio.sleep(0.05)
                active -= 1

        with mock.patch.object(agent_ui, "_make_session", return_value=Slow()), \
                mock.patch.object(agent_ui, "_session", None):
            results = await asyncio.gather(*[agent_ui._ensure_session() for _ in range(6)])
        self.assertEqual(peak, 1)                                   # start-up never overlapped
        self.assertEqual(len({id(r) for r in results}), 1)          # everyone got the same session

    async def test_a_dead_browser_is_replaced_with_a_fresh_session(self):
        class Dead:
            async def start(self): raise RuntimeError("connection lost")
            async def kill(self): pass

        class Fresh:
            async def start(self): pass

        fresh = Fresh()
        with mock.patch.object(agent_ui, "_session", Dead()), \
                mock.patch.object(agent_ui, "_make_session", return_value=fresh):
            got = await agent_ui._ensure_session()
            self.assertIs(got, fresh)
            self.assertIs(agent_ui._session, fresh)


if __name__ == "__main__":
    unittest.main()
