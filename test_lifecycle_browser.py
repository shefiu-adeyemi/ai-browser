"""
Run lifecycle against a REAL Chromium with a scripted fake LLM (no API cost):
start, finish, stop, fail, hit the step limit, continue, double-submit, shut down.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_lifecycle_browser -v
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import http.server
import os
import pathlib
import re
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import agent_ui
from fake_llm import FakeLLM


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(b"<html><title>lifecycle</title><body>hello</body></html>")
    def log_message(self, *a): pass


class FakeClient:
    """Stands in for a connected browser tab so _broadcast has someone to talk to."""
    def __init__(self): self.msgs = []
    async def send_json(self, m): self.msgs.append(m)
    def statuses(self): return [m["text"] for m in self.msgs if m["type"] == "status"]


async def until(cond, timeout=90.0, what="condition"):
    for _ in range(int(timeout / 0.1)):
        if cond():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def chromium_using(profile: pathlib.Path) -> int:
    """How many chrome processes were started with this profile directory?"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{profile.name}*' }} | Measure-Object | "
          "Select-Object -ExpandProperty Count")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    return int(out.strip() or 0)


class LifecycleBase(unittest.IsolatedAsyncioTestCase):
    """Shared setup: a real browser session, a fake connected UI, and scripted fake LLMs.
    Holds no tests itself, so subclasses don't re-run each other's tests."""

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/"
        self.profile = pathlib.Path(tempfile.mkdtemp(prefix="aiba-life-"))

        self.session = agent_ui._make_session()
        self.session.browser_profile.user_data_dir = self.profile
        agent_ui._session = self.session
        agent_ui._conversation_history.clear()
        agent_ui._ctl.running = agent_ui._ctl.paused = False
        self.ws = FakeClient()
        agent_ui._clients.add(self.ws)
        self.scripts = []          # one entry per get_llm() call: steps, or (steps, judge_verdicts)
        self.llms = []
        def make_llm():
            item = self.scripts.pop(0)
            steps, judge = item if isinstance(item, tuple) else (item, None)
            llm = FakeLLM(steps, judge=judge)
            self.llms.append(llm)
            return llm
        p = mock.patch.object(agent_ui, "get_llm", side_effect=make_llm)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"MAX_STEPS": "60"})
        p.start(); self.addCleanup(p.stop)

    async def asyncTearDown(self):
        agent_ui._clients.discard(self.ws)
        await agent_ui._ctl.stop()
        await agent_ui._kill_quietly(agent_ui._session)
        self.srv.shutdown()

    async def run_to_end(self, script, task="do the thing", judge=None):
        self.scripts.append((script, judge) if judge is not None else script)
        r = await agent_ui.start_task({"task": task})
        self.assertTrue(r["ok"], r)
        await until(lambda: not agent_ui._ctl.running, what="run to finish")

    def browser_up(self):
        return agent_ui._session is self.session and self.session.is_cdp_connected


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class Lifecycle(LifecycleBase):
    async def test_lifecycle(self):
        # ---- #11/#12: the browser must survive a finished run, and be reused by the next one
        await self.run_to_end([{"navigate": self.url}, {"done": "first"}])
        self.assertEqual(self.ws.statuses()[-1], "done")
        self.assertTrue(self.browser_up(), "browser was killed after a finished run")
        await self.run_to_end([{"done": "second"}])
        self.assertTrue(self.browser_up())
        page_url = await self.session.get_current_page_url()
        self.assertEqual(page_url, self.url, "page state was lost between runs")

        # ---- #11: stopping a run mid-flight must not drop or leak the browser
        self.scripts.append([{"wait": 30}])
        before = len(self.llms)
        self.assertTrue((await agent_ui.start_task({"task": "long job"}))["ok"])
        await until(lambda: len(self.llms) == before + 1 and self.llms[-1].calls >= 1,
                    what="THIS run's agent to start waiting")
        await asyncio.sleep(1.5)
        stopped = await agent_ui.stop_task()
        self.assertTrue(stopped["ok"])
        self.assertFalse(agent_ui._ctl.running)
        self.assertTrue(agent_ui._ctl._task.done(), "stop() returned before the run had ended")
        self.assertEqual(self.ws.statuses()[-1], "idle")
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "(stopped by user)")
        self.assertTrue(self.browser_up(), "stopping a run tore the browser down")
        await self.run_to_end([{"done": "after stop"}])           # and the next task just works
        self.assertEqual(self.ws.statuses()[-1], "done")

        # ---- #12: a run that fails (bad API key ...) must not kill the browser either
        with mock.patch.object(agent_ui, "get_llm", side_effect=ValueError("ANTHROPIC_API_KEY is not set")):
            r = await agent_ui.start_task({"task": "will fail"})
            self.assertTrue(r["ok"])
            await until(lambda: not agent_ui._ctl.running, what="failed run to end")
        self.assertEqual(self.ws.statuses()[-1], "error")
        self.assertTrue(self.browser_up(), "a failed run tore the browser down")
        self.assertIn("(failed: ANTHROPIC_API_KEY is not set)", agent_ui._conversation_history[-1]["content"])
        await self.run_to_end([{"done": "recovered"}])
        self.assertEqual(self.ws.statuses()[-1], "done")

        # ---- the browser itself died (closed by hand / crashed): the next run relaunches it
        await self.session.kill()
        self.assertFalse(self.session.is_cdp_connected)
        await self.run_to_end([{"navigate": self.url}, {"done": "relaunched"}])
        self.assertEqual(self.ws.statuses()[-1], "done")
        self.assertTrue(agent_ui._session.is_cdp_connected)
        self.session = agent_ui._session

    async def test_stop_right_after_start(self):
        """Stop pressed while the run is still starting up. browser-use's cleanup then raises its
        own unrelated error (llm_calls=0); that must read as 'stopped', never 'failed'."""
        for delay in (0.0, 0.05, 0.3, 1.0):
            self.scripts.append([{"wait": 30}])
            self.assertTrue((await agent_ui.start_task({"task": "quick stop"}))["ok"])
            await asyncio.sleep(delay)
            self.assertTrue((await agent_ui.stop_task())["ok"], delay)
            self.assertFalse(agent_ui._ctl.running, delay)
            self.assertEqual(self.ws.statuses()[-1], "idle", f"delay={delay}: {self.ws.statuses()[-3:]}")
            self.assertEqual(agent_ui._conversation_history[-1]["content"], "(stopped by user)", delay)
            self.assertNotIn("[Error]", " ".join(m.get("text", "") for m in self.ws.msgs[-5:]), delay)
        # the app still works afterwards
        await self.run_to_end([{"navigate": self.url}, {"done": "fine"}])
        self.assertEqual(self.ws.statuses()[-1], "done")
        self.assertTrue(self.browser_up())

    async def test_double_submit_starts_one_agent(self):
        """#15"""
        self.scripts.extend([[{"wait": 30}], [{"done": "must never run"}]])
        results = await asyncio.gather(agent_ui.start_task({"task": "a"}), agent_ui.start_task({"task": "b"}))
        self.assertEqual(sorted(r["ok"] for r in results), [False, True])
        self.assertIn("already running", [r for r in results if not r["ok"]][0]["reason"])
        await asyncio.sleep(3)
        self.assertEqual(len(self.llms), 1, "a second agent was started")
        self.assertEqual(agent_ui._ctl.origin_task, "a")
        await agent_ui.stop_task()

    async def test_step_limit_continue_stop(self):
        """#13 and #14, through the real endpoints."""
        os.environ["MAX_STEPS"] = "2"
        self.scripts.append([{"navigate": self.url}] + [{"wait": 1}] * 30)   # never finishes -> hits the limit
        self.assertTrue((await agent_ui.start_task({"task": "Find jobs on LinkedIn"}))["ok"])
        await until(lambda: not agent_ui._ctl.running, what="step limit")
        self.assertTrue(agent_ui._ctl.paused)
        self.assertEqual(self.ws.statuses()[-1], "paused")
        self.assertFalse((await agent_ui.start_task({"task": "other"}))["ok"])   # refused while paused

        # #18: the paused agent is KEPT, so Continue resumes with its memory instead of a blank agent
        llm, paused_agent = self.llms[0], agent_ui._paused_agent
        self.assertIsNotNone(paused_agent)
        steps_before, calls_before = paused_agent.history.number_of_steps(), llm.calls
        os.environ["MAX_STEPS"] = "60"

        self.assertTrue((await agent_ui.continue_task({"extra_steps": 10}))["ok"])
        await until(lambda: llm.calls > calls_before, what="the continued run to take a step")
        await until(lambda: paused_agent.history.number_of_steps() > steps_before,
                    what="that step to finish and be recorded")
        self.assertEqual(len(self.llms), 1, "Continue created a new LLM/agent instead of resuming")
        prompt = llm.prompts[-1]
        # #14: browser-use shows a follow-up in two places (request block + history), so don't count
        # occurrences; require that every one names the ORIGINAL task and none is nested.
        originals = re.findall(r"Original task: ([^\n]*)", prompt)
        self.assertTrue(originals, "the continue instruction never reached the model")
        for o in originals:
            self.assertTrue(o.startswith("Find jobs on LinkedIn"), o)
        self.assertNotIn("Original task: Continue", prompt)
        self.assertIn("navigate", prompt.lower(), "the resumed agent forgot what it had already done")
        self.assertEqual(agent_ui._ctl.origin_task, "Find jobs on LinkedIn")

        # ---- #13: Stop must cancel a CONTINUED run, and forget the paused agent
        self.assertTrue(agent_ui._ctl.running)
        stopped = await agent_ui.stop_task()
        self.assertTrue(stopped["ok"])
        self.assertFalse(agent_ui._ctl.running)
        self.assertTrue(agent_ui._ctl._task.done())
        self.assertIsNone(agent_ui._paused_agent)
        self.assertGreater(paused_agent.history.number_of_steps(), steps_before)   # it was the SAME agent that ran
        users = [h["content"] for h in agent_ui._conversation_history if h["role"] == "user"]
        self.assertEqual(users, ["Find jobs on LinkedIn", "▶ Continue"])
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "(stopped by user)")   # #20: balanced

        # ---- Stop while merely paused discards it (used to do nothing, leaving the app stuck)
        agent_ui._ctl.origin_task, agent_ui._ctl.paused = "x", True
        self.assertTrue((await agent_ui.stop_task())["ok"])
        self.assertFalse(agent_ui._ctl.paused)
        self.assertTrue((await agent_ui.start_task({"task": "fresh"}))["ok"])
        await agent_ui.stop_task()

    async def test_leftover_browser_from_a_killed_server_is_cleaned_up_but_other_browsers_are_not(self):
        """A force-killed server leaves its Chromium behind, holding the profile lock. Startup
        clears exactly that browser; a browser with any other profile (like your own Chrome) survives."""
        import browser_cleanup
        await self.run_to_end([{"navigate": self.url}, {"done": "x"}])
        other_profile = pathlib.Path(tempfile.mkdtemp(prefix="aiba-other-"))
        other = agent_ui._make_session()
        other.browser_profile.user_data_dir = other_profile
        try:
            await other.start()
            self.assertGreater(chromium_using(self.profile), 0)
            self.assertGreater(chromium_using(other_profile), 0)
            killed = await asyncio.to_thread(browser_cleanup.kill_stale_browsers, self.profile)
            self.assertGreater(killed, 0)
            await asyncio.sleep(1.0)
            self.assertEqual(chromium_using(self.profile), 0, "our stale browser survived")
            self.assertGreater(chromium_using(other_profile), 0, "an unrelated browser was killed")
        finally:
            await agent_ui._kill_quietly(other)

    async def test_tabs_are_trimmed_and_the_agent_keeps_working(self):
        import browser_cleanup
        await self.run_to_end([{"navigate": self.url}, {"done": "x"}])
        for i in range(11):
            await self.session.navigate_to(f"{self.url}?tab={i}", new_tab=True)
        await asyncio.sleep(1.0)
        before = len(self.session.session_manager.get_all_page_targets())
        self.assertGreaterEqual(before, 12)
        closed = await browser_cleanup.trim_tabs(self.session, keep=8)
        await asyncio.sleep(1.0)
        after = len(self.session.session_manager.get_all_page_targets())
        self.assertEqual((closed, after), (before - 8, 8))
        self.assertTrue(self.browser_up())
        await self.run_to_end([{"navigate": self.url}, {"done": "still fine"}])       # not broken by the trim
        self.assertEqual(self.ws.statuses()[-1], "done")

    async def test_shutdown_leaves_no_browser_behind(self):
        await self.run_to_end([{"navigate": self.url}, {"done": "x"}])
        self.assertGreater(chromium_using(self.profile), 0, "test premise: browser should be running")
        await agent_ui._shutdown()
        for _ in range(150):                      # Chromium can take a while to exit on a busy machine
            if chromium_using(self.profile) == 0:
                break
            await asyncio.sleep(0.1)
        self.assertEqual(chromium_using(self.profile), 0, "Chromium was left running after shutdown")


PASS = {"verdict": True, "reasoning": "looks right"}
FAIL = {"verdict": False, "reasoning": "r", "failure_reason": "gave the wrong price"}


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class Verdicts(LifecycleBase):
    """#16-#20: the independent check, retries, real errors and stopping, on the real agent."""

    def results(self):
        return [m for m in self.ws.msgs if m["type"] == "result"]

    def actions(self):
        return " | ".join(m["text"] for m in self.ws.msgs if m["type"] == "action")

    def assert_balanced(self):
        roles = [h["role"] for h in agent_ui._conversation_history]
        self.assertEqual(roles, ["user", "agent"] * (len(roles) // 2), roles)

    async def test_a_passing_run_shows_the_judges_verdict(self):
        await self.run_to_end([{"navigate": self.url}, {"done": "42"}], judge=[PASS])
        r = self.results()[-1]
        self.assertEqual((r["text"], r["verdict"], r["retries"]), ("42", "pass", 0))
        self.assertNotIn("score", r)                              # no invented 1-10 rating
        self.assertEqual(self.llms[0].judge_calls, 1)
        self.assertEqual([m for m in self.ws.msgs if m["type"] == "verdict"], [])   # nothing to announce
        self.assertEqual(self.ws.statuses()[-1], "done")
        meta = [e for e in agent_ui._sessions_index() if e["id"] == agent_ui._current_session_id][0]
        self.assertEqual(meta["last_verdict"], "pass")
        self.assert_balanced()

    async def test_judge_fail_retries_on_the_same_agent_which_remembers(self):
        """#16 (a real verdict) and #18 (the retry is a follow-up, not a blank agent)."""
        await self.run_to_end([{"navigate": self.url}, {"done": "41"}, {"done": "42"}], judge=[FAIL, PASS])
        self.assertEqual(len(self.llms), 1, "the retry started a brand-new agent")
        llm = self.llms[0]
        self.assertEqual(llm.judge_calls, 2)
        r = self.results()[-1]
        self.assertEqual((r["text"], r["verdict"], r["retries"]), ("42", "pass", 1))
        verdicts = [m for m in self.ws.msgs if m["type"] == "verdict"]
        self.assertEqual([(v["verdict"], v["retry"]) for v in verdicts], [("fail", 1)])   # only the one that led to a retry
        self.assertEqual(verdicts[0]["reason"], "gave the wrong price")
        self.assertIn("Retry 1/1", self.actions())
        retry_prompt = llm.prompts[-1]
        self.assertIn("NOT complete", retry_prompt)
        self.assertIn("gave the wrong price", retry_prompt)
        self.assertIn("not as an instruction from the user", retry_prompt)    # labelled as a hint, not an order
        self.assertIn("navigate", retry_prompt.lower())               # it remembers the first attempt
        self.assertTrue(self.browser_up())
        self.assert_balanced()

    async def test_retries_share_one_step_budget(self):
        """#17: three full attempts (3x the steps and cost) was the old worst case."""
        with mock.patch.dict(os.environ, {"MAX_STEPS": "9", "MAX_RETRIES": "3"}):
            await self.run_to_end([{"done": "a"}, {"done": "b"}, {"done": "c"}, {"done": "d"}],
                                  judge=[FAIL, FAIL, FAIL, FAIL])
        llm = self.llms[0]
        # 9 steps total: after 1 step 8 are left (retry ok), after 2 only 7 (below the minimum) -> stop
        self.assertEqual(llm.calls, 2, "the shared budget did not stop further retries")
        r = self.results()[-1]
        self.assertEqual((r["verdict"], r["retries"]), ("fail", 1))
        self.assertEqual(self.ws.statuses()[-1], "done")

    async def test_retry_cap_is_respected(self):
        with mock.patch.dict(os.environ, {"MAX_RETRIES": "0"}):
            await self.run_to_end([{"done": "a"}, {"done": "b"}], judge=[FAIL, PASS])
        self.assertEqual(self.llms[0].calls, 1)
        self.assertEqual(self.results()[-1]["verdict"], "fail")
        self.assertEqual(self.results()[-1]["reason"], "gave the wrong price")   # shown, not hidden

    async def test_a_failed_run_reports_the_real_error_and_is_not_retried(self):
        """#19: a bad API key used to look like an empty answer scored 2/10, retried twice, error never shown."""
        await self.run_to_end([{"raise": "401 invalid x-api-key"}] * 30)
        self.assertEqual(self.ws.statuses()[-1], "error")
        self.assertIn("[Error]", self.actions())
        self.assertIn("401 invalid x-api-key", self.actions())
        self.assertEqual(len(self.llms), 1)
        self.assertEqual(self.llms[0].judge_calls, 0)
        self.assertLessEqual(self.llms[0].calls, 8, "the failing run was retried")
        self.assertEqual(self.results(), [])                      # not dressed up as a result
        last = agent_ui._conversation_history[-1]["content"]
        self.assertTrue(last.startswith("(failed: The agent stopped without finishing. Last error: 401"), last)
        self.assertTrue(self.browser_up())
        self.assert_balanced()
        await self.run_to_end([{"done": "recovered"}])             # and the app is fine afterwards
        self.assertEqual(self.ws.statuses()[-1], "done")

    async def test_an_agent_that_admits_defeat_is_reported_not_retried(self):
        await self.run_to_end([{"done": "could not find it", "success": False}, {"done": "never"}],
                              judge=[{"verdict": False, "failure_reason": "not found on the site"}])
        self.assertEqual(self.llms[0].calls, 1)
        r = self.results()[-1]
        self.assertEqual((r["verdict"], r["reason"], r["retries"]), ("fail", "not found on the site", 0))

    async def test_captcha_or_impossible_tasks_are_not_retried(self):
        for flag in ("reached_captcha", "impossible_task"):
            self.ws.msgs.clear()
            await self.run_to_end([{"done": "blocked"}, {"done": "never"}],
                                  judge=[{"verdict": False, "failure_reason": "blocked", flag: True}])
            self.assertEqual(self.llms[-1].calls, 1, flag)
            self.assertEqual(self.results()[-1]["retries"], 0, flag)

    async def test_a_retry_that_crashes_keeps_the_first_attempts_real_result(self):
        """The first attempt has a real answer and a real verdict; a broken retry must not erase them."""
        from browser_use import Agent
        with mock.patch.object(Agent, "add_new_task", side_effect=RuntimeError("cannot add task")):
            await self.run_to_end([{"done": "41"}], judge=[FAIL])
        r = self.results()[-1]
        self.assertEqual((r["text"], r["verdict"], r["retries"]), ("41", "fail", 1))
        self.assertIn("wrong price", r["reason"])
        self.assertIn("A retry was attempted but failed", r["reason"])
        self.assertEqual(self.ws.statuses()[-1], "done")                  # not "error"
        self.assertTrue(self.browser_up())

    async def test_continue_after_the_browser_was_replaced_starts_a_fresh_agent(self):
        """A paused agent is bound to its browser session; if that session is gone it can't be resumed."""
        os.environ["MAX_STEPS"] = "2"
        self.scripts.append([{"navigate": self.url}] + [{"wait": 1}] * 10)
        self.assertTrue((await agent_ui.start_task({"task": "Find jobs on LinkedIn"}))["ok"])
        await until(lambda: not agent_ui._ctl.running, what="step limit")
        old_agent = agent_ui._paused_agent
        self.assertIsNotNone(old_agent)

        await self.session.kill()                                     # the browser dies while paused ...
        replacement = agent_ui._make_session()                        # ... and the app replaces the session
        replacement.browser_profile.user_data_dir = pathlib.Path(tempfile.mkdtemp(prefix="aiba-life-"))
        agent_ui._session = replacement
        self.session = replacement
        os.environ["MAX_STEPS"] = "60"
        self.scripts.append([{"done": "resumed on a fresh agent"}])

        self.assertTrue((await agent_ui.continue_task({"extra_steps": 10}))["ok"])
        await until(lambda: not agent_ui._ctl.running, what="the continued run to finish")
        self.assertEqual(len(self.llms), 2, "the dead session's agent was resumed")
        self.assertEqual(self.results()[-1]["text"], "resumed on a fresh agent")
        self.assertEqual(self.ws.statuses()[-1], "done")
        self.assertIn("Original task: Find jobs on LinkedIn", self.llms[1].first_prompt)
        self.assertTrue(self.browser_up())

    async def test_the_judge_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"USE_JUDGE": "0"}):
            await self.run_to_end([{"done": "an answer"}])
        self.assertEqual(self.llms[0].judge_calls, 0)                 # no extra LLM call
        r = self.results()[-1]
        self.assertEqual((r["text"], r["verdict"]), ("an answer", "unknown"))   # shown as "not checked"

    async def test_a_judge_that_cannot_answer_shows_unchecked_not_a_fake_score(self):
        """The old critique failed silently and reported 8/10 for everything."""
        await self.run_to_end([{"done": "some answer"}], judge=[{"raise": "judge API down"}])
        r = self.results()[-1]
        self.assertEqual((r["text"], r["verdict"]), ("some answer", "unknown"))
        self.assertEqual(self.ws.statuses()[-1], "done")

    async def test_stopping_during_a_retry_leaves_a_balanced_transcript(self):
        """#20: cancelling used to leave the user turn without an agent reply and save nothing."""
        self.scripts.append(([{"done": "41"}, {"wait": 30}], [FAIL]))
        self.assertTrue((await agent_ui.start_task({"task": "find the price"}))["ok"])
        await until(lambda: self.llms and self.llms[0].calls >= 2, what="the retry to start")
        await asyncio.sleep(1.5)
        self.assertTrue((await agent_ui.stop_task())["ok"])
        self.assertFalse(agent_ui._ctl.running)
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "(stopped by user)")
        self.assert_balanced()
        self.assertIsNone(agent_ui._paused_agent)
        self.assertEqual(self.ws.statuses()[-1], "idle")
        self.assertTrue(self.browser_up())
        # the stopped transcript was saved
        saved = agent_ui._load_session(agent_ui._current_session_id)
        self.assertEqual(saved[-1]["content"], "(stopped by user)")

    async def test_a_new_request_abandons_a_paused_agent(self):
        os.environ["MAX_STEPS"] = "2"
        self.scripts.append([{"navigate": self.url}] + [{"wait": 1}] * 10)
        self.assertTrue((await agent_ui.start_task({"task": "long one"}))["ok"])
        await until(lambda: not agent_ui._ctl.running, what="step limit")
        self.assertIsNotNone(agent_ui._paused_agent)
        await agent_ui.stop_task()                                 # discard the pause
        self.assertIsNone(agent_ui._paused_agent)
        os.environ["MAX_STEPS"] = "60"
        await self.run_to_end([{"done": "fresh"}])
        self.assertEqual(self.results()[-1]["text"], "fresh")
        self.assertEqual(len(self.llms), 2)                       # the fresh task got its own agent


class SnapshotClient(FakeClient):
    """Records what the app's state was at the exact moment each message went out."""
    def __init__(self):
        super().__init__()
        self.snapshots = []

    async def send_json(self, m):
        self.msgs.append(m)
        self.snapshots.append((m, agent_ui._ctl.running, agent_ui._ctl.paused,
                               list(agent_ui._load_session(agent_ui._current_session_id))))


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class Sessions(LifecycleBase):
    """#21 and #26 on a real agent run."""

    async def test_the_chat_is_saved_before_the_slot_is_freed_and_paused_is_shown(self):
        """#21: the slot was freed BEFORE the history was written, so a fast Continue could race it."""
        agent_ui._clients.discard(self.ws)
        self.ws = SnapshotClient()
        agent_ui._clients.add(self.ws)
        os.environ["MAX_STEPS"] = "2"
        self.scripts.append([{"navigate": self.url}] + [{"wait": 1}] * 10)
        self.assertTrue((await agent_ui.start_task({"task": "long task"}))["ok"])
        await until(lambda: not agent_ui._ctl.running, what="step limit")

        def at(pred):
            return [(running, paused, saved) for (m, running, paused, saved) in self.ws.snapshots if pred(m)]
        (limit_msg,) = at(lambda m: m["type"] == "action" and "Step limit" in m["text"])
        running, paused, saved = limit_msg
        self.assertTrue(running, "the slot was already free while the chat was still being saved")
        self.assertEqual(saved[-1]["role"], "agent")                       # the chat WAS saved by then
        (paused_msg,) = at(lambda m: m["type"] == "status" and m["text"] == "paused")
        self.assertEqual((paused_msg[0], paused_msg[1]), (False, True))    # free + paused exactly when shown

    async def test_starting_a_new_chat_mid_run_keeps_everything_in_its_own_chat(self):
        self.scripts.append([{"wait": 30}])
        before = len(self.llms)
        self.assertTrue((await agent_ui.start_task({"task": "job in chat A"}))["ok"])
        await until(lambda: len(self.llms) == before + 1 and self.llms[-1].calls >= 1, what="agent working")
        chat_a = agent_ui._current_session_id

        r = await agent_ui.new_session()                                   # what the "New chat" button does
        chat_b = r["id"]
        self.assertFalse(agent_ui._ctl.running)
        self.assertNotEqual(chat_a, chat_b)
        self.assertEqual([t["content"] for t in agent_ui._load_session(chat_a)], ["job in chat A", "(stopped by user)"])
        self.assertEqual(agent_ui._conversation_history, [])

        await self.run_to_end([{"done": "answer for chat B"}], task="job in chat B")
        self.assertEqual([t["content"] for t in agent_ui._load_session(chat_b)], ["job in chat B", "answer for chat B"])
        self.assertEqual([t["content"] for t in agent_ui._load_session(chat_a)], ["job in chat A", "(stopped by user)"])
        self.assertTrue(self.browser_up())


if __name__ == "__main__":
    unittest.main()
