"""App-level tests for running playbooks: the endpoints, and the chat/record handling of a run.
No browser: the player and the browser session are replaced."""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import pathlib
import unittest
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.storage.chat_store import ChatStore
from aiba.playbooks.model import Check, Playbook, Step, Variable
from aiba.playbooks.player import RunResult, StepResult
from aiba.playbooks.runs import RunStore
from aiba.playbooks.store import PlaybookStore
from aiba.scheduling.store import ScheduleStore


def linkedin_pb() -> Playbook:
    return Playbook(name="Open a profile",
                    variables=[Variable(name="profile_url", type="linkedin_url")],
                    steps=[Step(id="s1", kind="open", url="{{profile_url}}", check=Check(hosts=["www.linkedin.com"]))])


class FakeClient:
    def __init__(self): self.msgs = []
    async def send_json(self, m): self.msgs.append(m)
    def of(self, kind): return [m for m in self.msgs if m["type"] == kind]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(_tf.mkdtemp())
        self.store, self.runs = PlaybookStore(self.tmp / "pb"), RunStore(self.tmp / "runs")
        for p in (mock.patch.object(agent_ui, "_playbooks", self.store), mock.patch.object(agent_ui, "_runs", self.runs),
                  mock.patch.object(agent_ui, "_current_session_id", ChatStore.new_id()),
                  mock.patch.object(agent_ui, "_last_run", None),
                  mock.patch.object(agent_ui, "_schedules", ScheduleStore(self.tmp / "sch.json"))):
            p.start()
            self.addCleanup(p.stop)
        agent_ui._ctl.running = agent_ui._ctl.paused = False


class Endpoints(Base):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")
        self.pb = self.store.save(linkedin_pb(), approve=True)
        self.started: list = []

        async def fake_task(pb, values, run_id):
            self.started.append((pb.id, values, run_id))
        p = mock.patch.object(agent_ui, "run_playbook_task", fake_task)
        p.start(); self.addCleanup(p.stop)

    def run_it(self, inputs, pid=None):
        return self.c.post(f"/playbooks/{pid or self.pb.id}/run", json={"inputs": inputs})

    def test_start_a_run(self):
        r = self.run_it({"profile_url": "https://www.linkedin.com/in/jane-doe"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(RunStore.is_valid_id(r.json()["run_id"]))
        self.assertEqual(self.started[0][1], {"profile_url": "https://www.linkedin.com/in/jane-doe"})
        self.assertEqual(self.started[0][2], r.json()["run_id"])

    def test_bad_inputs_are_explained_and_nothing_starts(self):
        for inputs in ({}, {"profile_url": "https://evil.com/in/x"}, {"profile_url": "https://www.linkedin.com/in/a", "x": "1"},
                       {"profile_url": "https://www.linkedin.com/in/a\nhttps://evil.com"}):
            r = self.run_it(inputs)
            self.assertEqual(r.status_code, 400, inputs)
            self.assertTrue(r.json()["problems"])
        self.assertEqual(self.started, [])

    def test_inputs_that_are_not_an_object_are_treated_as_none(self):
        r = self.c.post(f"/playbooks/{self.pb.id}/run", json={"inputs": ["https://www.linkedin.com/in/a"]})
        self.assertEqual(r.status_code, 400)

    def test_unknown_and_malformed_ids(self):
        self.assertEqual(self.run_it({}, pid="pb_" + "0" * 12).status_code, 404)
        for bad in ("..%5Cx", "nope", "pb_zzzzzzzzzzzz"):
            self.assertEqual(self.run_it({}, pid=bad).status_code, 400, bad)

    def test_a_damaged_playbook_is_not_run(self):
        pid = PlaybookStore.new_id()
        (self.tmp / "pb" / f"{pid}.json").write_text("{nope", encoding="utf-8")
        self.assertEqual(self.run_it({}, pid=pid).status_code, 422)

    def test_a_second_run_while_busy_is_refused(self):
        agent_ui._ctl.running = True
        r = self.run_it({"profile_url": "https://www.linkedin.com/in/jane-doe"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.started, [])

    def test_defaults_fill_in_missing_inputs(self):
        pb = self.store.save(Playbook(name="d", variables=[Variable(name="q", default="cats")],
                                      steps=[Step(id="s1", kind="key", keys="Tab")]))
        self.assertEqual(self.run_it({}, pid=pb.id).status_code, 200)
        self.assertEqual(self.started[-1][1], {"q": "cats"})

    def test_run_records_are_listed_and_fetched(self):
        rec = self.runs.save(self.pb.id, {"run_id": RunStore.new_id(), "status": "done", "started_at": 5.0,
                                          "steps": [{"id": "s1", "status": "ok"}], "inputs": {"a": "b"}})
        rows = self.c.get(f"/playbooks/{self.pb.id}/runs").json()["runs"]
        self.assertEqual([r["run_id"] for r in rows], [rec["run_id"]])
        got = self.c.get(f"/playbooks/{self.pb.id}/runs/{rec['run_id']}").json()
        self.assertEqual(got["run"]["inputs"], {"a": "b"})
        self.assertEqual(self.c.get(f"/playbooks/{self.pb.id}/runs/run_{'0' * 12}").status_code, 404)
        self.assertEqual(self.c.get(f"/playbooks/{self.pb.id}/runs/..%5Cx").status_code, 400)
        self.assertEqual(self.c.get("/playbooks/nope/runs").status_code, 400)

    def test_deleting_a_playbook_deletes_its_runs(self):
        self.runs.save(self.pb.id, {"run_id": RunStore.new_id(), "status": "done"})
        self.assertEqual(self.c.delete(f"/playbooks/{self.pb.id}").json(), {"ok": True, "deleted": True, "schedules_deleted": 0})
        self.assertEqual(self.runs.recent(self.pb.id), [])

    def test_the_endpoints_need_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.post(f"/playbooks/{self.pb.id}/run", json={"inputs": {}}).status_code, (401, 403))
        self.assertIn(stranger.get(f"/playbooks/{self.pb.id}/runs").status_code, (401, 403))


class RunHandling(unittest.IsolatedAsyncioTestCase):
    """What run_playbook_task does around the player: chat turns, record, messages, stop, crash."""
    async def asyncSetUp(self):
        self.tmp = pathlib.Path(_tf.mkdtemp())
        self.runs = RunStore(self.tmp / "runs")
        self.chat = ChatStore(self.tmp / "sessions")
        self.ws = FakeClient()
        agent_ui._clients.add(self.ws)
        self.addCleanup(agent_ui._clients.discard, self.ws)
        for p in (mock.patch.object(agent_ui, "_runs", self.runs), mock.patch.object(agent_ui, "_store", self.chat),
                  mock.patch.object(agent_ui, "_ensure_session", new=mock.AsyncMock(return_value=object())),
                  mock.patch.object(agent_ui, "trim_tabs", new=mock.AsyncMock()),
                  mock.patch.object(agent_ui, "get_llm", return_value=object()),
                  mock.patch.object(agent_ui, "SessionDriver", lambda *a, **k: object())):
            p.start()
            self.addCleanup(p.stop)
        agent_ui._conversation_history = []
        agent_ui._current_session_id = ChatStore.new_id()
        agent_ui._ctl.running = agent_ui._ctl.paused = False
        self.pb = Playbook(**{**linkedin_pb().model_dump(), "id": "pb_" + "a" * 12, "version": 3})
        self.player_result = RunResult(status="done", steps=[StepResult("s1", "open", "Open a page")], started_at=0, finished_at=2)
        self.player_args = None

        outer = self
        class FakePlayer:
            def __init__(self, driver, approver, notify=None, **k):
                outer.approver, outer.notify = approver, notify
            async def run(self, pb, values, answers=None):
                outer.player_args = (pb, values)
                if isinstance(outer.player_result, BaseException):
                    raise outer.player_result
                await outer.notify("▶ Step 1/1: Open a page")
                return outer.player_result
        p = mock.patch.object(agent_ui, "PlaybookPlayer", FakePlayer)
        p.start(); self.addCleanup(p.stop)

    async def test_a_finished_run_is_in_the_chat_the_record_and_the_ui(self):
        await agent_ui.run_playbook_task(self.pb, {"profile_url": "https://www.linkedin.com/in/jane-doe"}, "run_" + "1" * 12)
        h = agent_ui._conversation_history
        self.assertEqual([t["role"] for t in h], ["user", "agent"])
        self.assertIn('Run playbook "Open a profile"', h[0]["content"])
        self.assertIn("profile_url: https://www.linkedin.com/in/jane-doe", h[0]["content"])
        self.assertIn("all 1 step done", h[1]["content"])
        rec = self.runs.get(self.pb.id, "run_" + "1" * 12)
        self.assertEqual((rec["status"], rec["playbook_version"], rec["attended"]), ("done", 3, True))
        self.assertEqual(rec["inputs"], {"profile_url": "https://www.linkedin.com/in/jane-doe"})
        self.assertEqual(rec["steps"][0]["id"], "s1")
        self.assertEqual([m["text"] for m in self.ws.of("status")], ["running", "done"])
        res = self.ws.of("result")[0]
        self.assertEqual((res["verdict"], res["duration"]), ("pass", 2.0))
        self.assertIn("▶ Step 1/1: Open a page", [m["text"] for m in self.ws.of("action")])
        self.assertTrue(self.chat.has(agent_ui._current_session_id))                 # the chat was saved

    async def test_a_run_that_needs_you_is_shown_as_such(self):
        self.player_result = RunResult(status="needs_you", error="Step 1 (Open a page): sign in",
                                       steps=[StepResult("s1", "open", status="needs_you", detail="sign in")], finished_at=1)
        await agent_ui.run_playbook_task(self.pb, {}, "run_" + "2" * 12)
        self.assertEqual(self.ws.of("result")[0]["verdict"], "fail")
        self.assertEqual(self.ws.of("status")[-1]["text"], "error")
        self.assertEqual(self.runs.get(self.pb.id, "run_" + "2" * 12)["status"], "needs_you")
        self.assertIn("needs you", agent_ui._conversation_history[-1]["content"])

    async def test_the_guard_runs_in_ask_mode_whatever_the_environment_says(self):
        seen = {}
        class Spy:
            def __init__(self, approver, **kw): seen.update(kw)
        with mock.patch.dict(_os.environ, {"APPROVAL_MODE": "off"}), mock.patch.object(agent_ui, "GuardedTools", Spy):
            await agent_ui.run_playbook_task(self.pb, {}, "run_" + "3" * 12)
        self.assertEqual(seen["mode"], "ask")
        self.assertEqual(seen["task"], "Open a profile")

    async def test_the_approver_is_attended_and_wraps_the_ui_broker(self):
        await agent_ui.run_playbook_task(self.pb, {}, "run_" + "4" * 12)
        self.assertTrue(self.approver.attended)
        self.assertIs(self.approver.inner, agent_ui._broker)

    async def test_a_model_is_only_fetched_for_steps_that_need_one(self):
        with mock.patch.object(agent_ui, "get_llm", side_effect=AssertionError("no model needed")):
            await agent_ui.run_playbook_task(self.pb, {}, "run_" + "5" * 12)             # no extract step
        pb = Playbook(**{**self.pb.model_dump(), "steps": [Step(id="s1", kind="extract", tag="ai", query="q")]})
        with mock.patch.object(agent_ui, "get_llm", side_effect=RuntimeError("no key")):
            await agent_ui.run_playbook_task(pb, {}, "run_" + "6" * 12)                  # fails inside the step, not here
        self.assertEqual(self.runs.get(self.pb.id, "run_" + "6" * 12)["status"], "done")

    async def test_stop_is_recorded_and_propagated(self):
        self.player_result = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await agent_ui.run_playbook_task(self.pb, {}, "run_" + "7" * 12)
        self.assertEqual(self.runs.get(self.pb.id, "run_" + "7" * 12)["status"], "stopped")
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "(stopped by user)")

    async def test_a_crash_is_reported_and_recorded(self):
        self.player_result = RuntimeError("browser went away")
        await agent_ui.run_playbook_task(self.pb, {}, "run_" + "8" * 12)
        rec = self.runs.get(self.pb.id, "run_" + "8" * 12)
        self.assertEqual((rec["status"], rec["error"]), ("failed", "browser went away"))
        self.assertIn("(failed: browser went away)", agent_ui._conversation_history[-1]["content"])
        self.assertEqual(self.ws.of("status")[-1]["text"], "error")

    async def test_a_record_that_cannot_be_saved_does_not_hide_the_result(self):
        with mock.patch.object(self.runs, "save", side_effect=OSError("disk full")):
            await agent_ui.run_playbook_task(self.pb, {}, "run_" + "9" * 12)
        self.assertEqual(self.ws.of("status")[-1]["text"], "done")
        self.assertIn("all 1 step done", agent_ui._conversation_history[-1]["content"])

    async def test_a_playbook_with_tab_steps_starts_from_one_tab(self):
        calls = []
        async def fake_trim(session, keep=5):
            calls.append(keep)
        with mock.patch.object(agent_ui, "trim_tabs", fake_trim):
            await agent_ui.run_playbook_task(self.pb, {}, "run_" + "c" * 12)
            tabbed = Playbook(**{**self.pb.model_dump(), "steps": [Step(id="s1", kind="tab", tab_index=0)]})
            await agent_ui.run_playbook_task(tabbed, {}, "run_" + "d" * 12)
        self.assertEqual(calls[0], 5)
        self.assertEqual(calls[1], 1)

    async def test_it_does_not_offer_a_playbook_run_as_the_last_run_to_save(self):
        agent_ui._last_run = {"task": "x", "history": None, "session_id": agent_ui._current_session_id}
        await agent_ui.run_playbook_task(self.pb, {}, "run_" + "a" * 12)
        self.assertIsNone(agent_ui._last_run)

    async def test_long_input_values_are_shortened_in_the_chat_only(self):
        await agent_ui.run_playbook_task(self.pb, {"profile_url": "https://www.linkedin.com/in/" + "x" * 300}, "run_" + "b" * 12)
        self.assertLess(len(agent_ui._conversation_history[0]["content"]), 200)
        self.assertEqual(len(self.runs.get(self.pb.id, "run_" + "b" * 12)["inputs"]["profile_url"]), 300 + 28)


if __name__ == "__main__":
    unittest.main()
