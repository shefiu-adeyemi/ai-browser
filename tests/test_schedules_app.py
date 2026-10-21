"""App-level tests for schedules: the endpoints, the inbox, and how a scheduled run is handed to the browser.
No browser: the player is replaced."""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.scheduling import schedules as sch
from aiba.storage.chat_store import ChatStore
from aiba.server.notifications import NotificationStore
from aiba.playbooks.model import Check, Fingerprint, Playbook, Step, Variable
from aiba.playbooks.player import RunResult, StepResult
from aiba.playbooks.runs import RunStore
from aiba.playbooks.store import PlaybookStore
from aiba.scheduling.store import ScheduleStore
from aiba.scheduling.scheduler import Outcome, Scheduler
from aiba.scheduling.limits import Decision, SiteLimits

URL = "https://www.linkedin.com/in/jane-doe"
REAL_SCHEDULER = agent_ui._scheduler        # the one the app really uses (the tests below swap in their own)


def profile_pb(name="Open a profile", **kw):
    base = dict(name=name, variables=[Variable(name="p", type="linkedin_url")],
                steps=[Step(id="s1", kind="open", url="{{p}}", check=Check(hosts=["www.linkedin.com"]))])
    base.update(kw)
    return pbk.refresh(Playbook(**base))


class FakeClient:
    def __init__(self): self.msgs = []
    async def send_json(self, m): self.msgs.append(m)
    def of(self, kind): return [m for m in self.msgs if m["type"] == kind]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(_tf.mkdtemp())
        self.pbs, self.runs = PlaybookStore(self.tmp / "pb"), RunStore(self.tmp / "runs")
        self.scheds, self.notes = ScheduleStore(self.tmp / "sch.json"), NotificationStore(self.tmp / "n.json")
        self.limits = SiteLimits(self.tmp / "lim.json")
        self.toasts: list[tuple] = []
        self.desktop = mock.Mock()
        self.desktop.notify = lambda t, x="": self.toasts.append((t, x)) or True
        self.executed: list = []

        async def execute(pb, values, s, row):
            self.executed.append((pb.name, values, row))
            return Outcome("done", "run_" + "1" * 12)
        self.sched = Scheduler(schedules=self.scheds, playbooks=self.pbs, limits=self.limits, execute=execute,
                               idle=lambda: True, notify=agent_ui._notify_user)
        for target, value in (("_playbooks", self.pbs), ("_runs", self.runs), ("_schedules", self.scheds),
                              ("_limits", self.limits), ("_notifications", self.notes), ("_desktop", self.desktop),
                              ("_scheduler", self.sched), ("_last_run", None),
                              ("_current_session_id", ChatStore.new_id())):
            p = mock.patch.object(agent_ui, target, value)
            p.start()
            self.addCleanup(p.stop)
        agent_ui._ctl.running = agent_ui._ctl.paused = False
        self.pb = self.pbs.save(profile_pb(), approve=True)


class Endpoints(Base):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")

    def body(self, **over):
        s = {"name": "Daily profile", "playbook_id": self.pb.id, "enabled": True, "on_failure": "stop",
             "cadence": {"kind": "daily", "at": "09:00"}, "items": [{"p": URL}]}
        s.update(over)
        return {"schedule": s}

    def create(self, **over):
        r = self.c.post("/schedules", json=self.body(**over))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["schedule"]

    # ------------------------------------------------------------------------ create and list
    def test_create_and_list(self):
        s = self.create()
        self.assertTrue(sch.is_valid_id(s["id"]))
        self.assertEqual((s["status"], s["playbook_name"], s["cadence_text"]), ("scheduled", "Open a profile", "Every day at 09:00"))
        self.assertTrue(s["next_run"])
        data = self.c.get("/schedules").json()
        self.assertEqual([x["id"] for x in data["schedules"]], [s["id"]])
        self.assertFalse(data["paused_all"])
        self.assertEqual(data["limits"]["usage"]["linkedin_profile_view"], {"used": 0, "cap": 50})
        self.assertTrue(data["desktop"])

    def test_problems_are_listed_and_nothing_is_saved(self):
        cases = [
            self.body(playbook_id="pb_" + "0" * 12),
            self.body(items=[{"p": "https://evil.com/in/x"}]),
            self.body(items=[]),
            self.body(name=""),
            self.body(cadence={"kind": "once", "run_at": "2020-01-01T00:00:00"}),
            self.body(cadence={"kind": "weekly", "at": "09:00", "weekdays": []}),
        ]
        for body in cases:
            r = self.c.post("/schedules", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertTrue(r.json()["problems"], body)
        self.assertEqual(self.scheds.all(), [])

    def test_an_unapproved_playbook_cannot_be_scheduled(self):
        other = self.pbs.save(profile_pb("Unapproved"))
        r = self.c.post("/schedules", json=self.body(playbook_id=other.id))
        self.assertEqual(r.status_code, 400)
        self.assertTrue(any("Approve the playbook" in p for p in r.json()["problems"]))

    def test_a_playbook_with_a_send_step_needs_the_second_yes(self):
        fp = Fingerprint(node_name="button", ax_name="Send")
        risky = self.pbs.save(Playbook(name="Sends", steps=[Step(id="s1", kind="open", url="https://a.com/"),
                                                            Step(id="s2", kind="click", target=fp, ask=True)]), approve=True)
        r = self.c.post("/schedules", json=self.body(playbook_id=risky.id, items=[{}]))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.post("/schedules", json=self.body(playbook_id=risky.id, items=[{}], allow_sensitive=True)).status_code, 200)

    def test_the_client_cannot_set_the_internal_state_or_dates(self):
        s = self.create()["id"]
        forged = self.body(id=s)
        forged["schedule"].update(state={"queue": [0], "paused_reason": "x", "missed": 99}, created_at="1999-01-01T00:00:00",
                                  status="running", runs_total=7)
        r = self.c.post("/schedules", json=forged)
        self.assertEqual(r.status_code, 200, r.text)
        got = self.scheds.get(s)
        self.assertEqual((got.state.queue, got.state.paused_reason, got.state.missed, got.state.runs_total), ([], "", 0, 0))
        self.assertNotEqual(got.created_at, "1999-01-01T00:00:00")

    def test_rows_may_arrive_as_non_text(self):
        pb = self.pbs.save(Playbook(name="n", variables=[Variable(name="n", type="number")],
                                    steps=[Step(id="s1", kind="key", keys="Tab")]), approve=True)
        r = self.c.post("/schedules", json=self.body(playbook_id=pb.id, items=[{"n": 5}, {"n": 2.5}]))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["schedule"]["items"], [{"n": "5"}, {"n": "2.5"}])

    def test_garbage_is_a_400_not_a_crash(self):
        for body in ({}, {"schedule": []}, {"schedule": {"name": 5}}, {"schedule": {"cadence": {"kind": "hourly"}}},
                     {"schedule": {"on_failure": "explode", "name": "x", "playbook_id": self.pb.id}}):
            r = self.c.post("/schedules", json=body)
            self.assertIn(r.status_code, (400, 422), body)

    # ----------------------------------------------------------------------------- update
    def test_a_rename_keeps_the_state_and_the_next_time(self):
        s = self.create()
        self.scheds.change_state(s["id"], lambda st: st.model_copy(update={"runs_total": 4, "last_status": "done"}))
        r = self.c.post("/schedules", json=self.body(id=s["id"], name="Renamed"))
        got = r.json()["schedule"]
        self.assertEqual((got["name"], got["runs_total"], got["next_run"]), ("Renamed", 4, s["next_run"]))

    def test_a_new_time_works_out_the_next_run_again(self):
        s = self.create(cadence={"kind": "daily", "at": "09:00"})
        r = self.c.post("/schedules", json=self.body(id=s["id"], cadence={"kind": "interval", "every_minutes": 30}))
        got = r.json()["schedule"]
        self.assertNotEqual(got["next_run"], s["next_run"])
        self.assertLess(sch.parse_iso(got["next_run"]) - datetime.now(), timedelta(minutes=31))

    def test_changed_rows_cancel_a_half_done_batch(self):
        s = self.create(items=[{"p": URL}, {"p": URL}])
        self.scheds.change_state(s["id"], lambda st: st.model_copy(update={"queue": [1], "batch_id": "b_1"}))
        r = self.c.post("/schedules", json=self.body(id=s["id"], items=[{"p": URL}]))
        self.assertEqual(r.json()["schedule"]["remaining"], 0)

    def test_switching_a_schedule_off_stops_its_batch(self):
        s = self.create(items=[{"p": URL}, {"p": URL}])
        self.scheds.change_state(s["id"], lambda st: st.model_copy(update={"queue": [1], "batch_id": "b_1"}))
        got = self.c.post("/schedules", json=self.body(id=s["id"], enabled=False, items=[{"p": URL}, {"p": URL}])).json()["schedule"]
        self.assertEqual((got["remaining"], got["status"]), (0, "off"))

    def test_unchanged_rows_leave_a_running_batch_alone(self):
        s = self.create(items=[{"p": URL}, {"p": URL}])
        self.scheds.change_state(s["id"], lambda st: st.model_copy(update={"queue": [1], "batch_id": "b_1"}))
        r = self.c.post("/schedules", json=self.body(id=s["id"], name="Renamed", items=[{"p": URL}, {"p": URL}]))
        self.assertEqual(r.json()["schedule"]["remaining"], 1)

    def test_switching_a_schedule_back_on_looks_for_the_next_time_from_now(self):
        s = self.create(enabled=False)
        self.scheds.change_state(s["id"], lambda st: st.model_copy(update={"next_run": "2020-01-01T09:00:00"}))
        got = self.c.post("/schedules", json=self.body(id=s["id"], enabled=True)).json()["schedule"]
        self.assertGreater(sch.parse_iso(got["next_run"]), datetime.now())

    def test_unknown_and_malformed_ids(self):
        self.assertEqual(self.c.post("/schedules", json=self.body(id="sch_" + "0" * 12)).status_code, 404)
        for method, url in (("delete", "/schedules/..%5Cx"), ("post", "/schedules/nope/resume"), ("post", "/schedules/nope/run-now")):
            self.assertEqual(getattr(self.c, method)(url).status_code, 400, url)
        self.assertEqual(self.c.post("/schedules/sch_" + "0" * 12 + "/resume").status_code, 404)

    # ----------------------------------------------------------------------------- status
    def test_the_status_words(self):
        s = self.create()
        sid = s["id"]
        now = datetime.now()
        def status(**state):
            self.scheds.change_state(sid, lambda st: sch.ScheduleState(**{**st.model_dump(), **state}))
            return self.c.get("/schedules").json()["schedules"][0]["status"]
        self.assertEqual(status(), "scheduled")
        self.assertEqual(status(queue=[0]), "running")
        self.assertEqual(status(queue=[0], resume_after=sch.iso(now + timedelta(hours=2))), "waiting")
        self.assertEqual(status(queue=[], resume_after=None, next_run=None), "finished")
        self.assertEqual(status(paused_reason="failed", paused_detail="x"), "paused")
        self.c.post("/schedules", json=self.body(id=sid, enabled=False))
        self.assertEqual(status(paused_reason=""), "off")

    # ------------------------------------------------------------------ pause, resume, run now
    def test_pause_all(self):
        self.assertTrue(self.c.post("/schedules/pause-all", json={"paused": True}).json()["paused_all"])
        self.assertTrue(self.c.get("/schedules").json()["paused_all"])
        self.assertFalse(self.c.post("/schedules/pause-all", json={"paused": "yes"}).json()["paused_all"])   # only a real true

    def test_resume_a_paused_schedule(self):
        s = self.create()
        self.scheds.change_state(s["id"], lambda st: st.model_copy(update={"paused_reason": "needs_you", "paused_detail": "sign in"}))
        got = self.c.post(f"/schedules/{s['id']}/resume").json()["schedule"]
        self.assertEqual((got["status"], got["paused_reason"]), ("scheduled", ""))

    def test_run_now(self):
        s = self.create(items=[{"p": URL}, {"p": URL}])
        self.assertTrue(self.c.post(f"/schedules/{s['id']}/run-now").json()["ok"])
        self.assertEqual(self.c.get("/schedules").json()["schedules"][0]["remaining"], 2)
        r = self.c.post(f"/schedules/{s['id']}/run-now")
        self.assertEqual(r.status_code, 409)
        self.assertIn("already running", r.json()["reason"])

    def test_delete_and_a_deleted_playbook_takes_its_schedules(self):
        self.create()
        other = self.pbs.save(profile_pb("Other"), approve=True)
        s2 = self.create(playbook_id=other.id, name="other")
        self.assertEqual(self.c.delete(f"/schedules/{s2['id']}").json(), {"ok": True, "deleted": True})
        self.assertEqual(self.c.delete(f"/playbooks/{self.pb.id}").json()["schedules_deleted"], 1)
        self.assertEqual(self.scheds.all(), [])

    def test_editing_an_approved_playbook_says_which_schedules_it_affects(self):
        self.create()
        pb = self.pbs.get(self.pb.id)
        edited = pb.model_copy(update={"steps": [pb.steps[0].model_copy(update={"new_tab": True})]})
        r = self.c.post("/playbooks", json={"playbook": edited.model_dump(mode="json")}).json()
        self.assertEqual((r["approved"], r["schedules_affected"]), (False, 1))
        r = self.c.post("/playbooks", json={"playbook": edited.model_dump(mode="json"), "approve": True}).json()
        self.assertEqual((r["approved"], r["schedules_affected"]), (True, 0))

    # ------------------------------------------------------------------------------ limits
    def test_limits_can_be_changed(self):
        r = self.c.post("/scheduler/limits", json={"allowances": {"linkedin_profile_view": {"start": 20, "ceiling": 60}}, "gap": {"min": 45}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["limits"]["usage"]["linkedin_profile_view"]["cap"], 20)
        self.assertEqual(r.json()["limits"]["gap"]["min"], 45)

    def test_bad_limits_are_refused_and_nothing_changes(self):
        r = self.c.post("/scheduler/limits", json={"allowances": {"linkedin_profile_view": {"start": 500}}})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.json()["problems"])
        self.assertEqual(self.limits.cap_today("linkedin_profile_view", datetime.now().date()), 50)
        self.assertEqual(self.c.post("/scheduler/limits", json={"allowances": "x", "gap": 5}).status_code, 200)   # ignored, not a crash

    # ------------------------------------------------------------------------------ inbox
    # ------------------------------------------------------------------------ quiet hours
    def test_quiet_hours_can_be_set_and_are_listed(self):
        self.assertFalse(self.c.get("/schedules").json()["quiet_hours"]["enabled"])
        r = self.c.post("/scheduler/quiet-hours", json={"enabled": True, "start": "23:00", "end": "06:30"})
        self.assertEqual(r.json()["quiet_hours"], {"enabled": True, "start": "23:00", "end": "06:30"})
        self.assertEqual(self.c.get("/schedules").json()["quiet_hours"]["start"], "23:00")
        self.assertEqual(self.scheds.quiet_hours().end, "06:30")

    def test_bad_quiet_hours_are_refused(self):
        for body in ({"enabled": True, "start": "late", "end": "06:30"}, {"enabled": True, "start": "08:00", "end": "08:00"},
                     {"enabled": "yes", "start": "22:00", "end": "07:00"}, {"enabled": True, "start": 5, "end": "07:00"}):
            self.assertEqual(self.c.post("/scheduler/quiet-hours", json=body).status_code, 400, body)
        self.assertFalse(self.scheds.quiet_hours().enabled)

    # ---------------------------------------------------------------------- failure pictures
    def test_a_failure_picture_is_served_and_unknown_ones_are_not(self):
        rid = RunStore.new_id()
        self.runs.save(self.pb.id, {"run_id": rid, "status": "failed", "screenshot": True})
        self.runs.save_screenshot(self.pb.id, rid, b"\xff\xd8\xff-fake-jpeg")
        r = self.c.get(f"/playbooks/{self.pb.id}/runs/{rid}/screenshot")
        self.assertEqual((r.status_code, r.headers["content-type"], r.content), (200, "image/jpeg", b"\xff\xd8\xff-fake-jpeg"))
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(self.c.get(f"/playbooks/{self.pb.id}/runs/run_{'0' * 12}/screenshot").status_code, 404)
        self.assertEqual(self.c.get(f"/playbooks/{self.pb.id}/runs/..%5Cx/screenshot").status_code, 400)
        self.assertEqual(self.c.get("/playbooks/nope/runs/x/screenshot").status_code, 400)

    def test_the_picture_needs_the_app_cookie(self):
        from fastapi.testclient import TestClient
        rid = RunStore.new_id()
        self.runs.save(self.pb.id, {"run_id": rid, "status": "failed"})
        self.runs.save_screenshot(self.pb.id, rid, b"x")
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.get(f"/playbooks/{self.pb.id}/runs/{rid}/screenshot").status_code, (401, 403))

    # ------------------------------------------------------------------ LinkedIn sign-in check
    def fake_browser(self, ends_at=None, fail=None):
        class Session:
            async def navigate_to(inner, url):
                if fail:
                    raise fail
                self.opened = url
            async def get_current_page_url(inner):
                return ends_at
        return mock.patch.object(agent_ui, "_ensure_session", new=mock.AsyncMock(return_value=Session()))

    def check(self, ends_at=None, fail=None):
        with self.fake_browser(ends_at, fail), mock.patch.object(agent_ui.asyncio, "sleep", new=mock.AsyncMock()):
            return self.c.post("/session/check-linkedin")

    def test_signed_in(self):
        r = self.check("https://www.linkedin.com/feed/")
        self.assertEqual((r.status_code, r.json()["signed_in"]), (200, True))
        self.assertEqual(self.opened, "https://www.linkedin.com/feed/")

    def test_not_signed_in_wherever_linkedin_sends_you(self):
        for url in ("https://www.linkedin.com/", "https://www.linkedin.com/authwall?trk=x", "https://www.linkedin.com/login",
                    "https://www.linkedin.com/checkpoint/challenge/abc", "https://www.linkedin.com/uas/login"):
            r = self.check(url)
            self.assertFalse(r.json()["signed_in"], url)
            self.assertIn("not signed in", r.json()["message"])

    def test_somewhere_else_is_unknown_and_lookalikes_do_not_count(self):
        for url in ("https://example.com/feed/", "https://evil-linkedin.com/feed/", "https://linkedin.com.evil.net/feed/", ""):
            self.assertIsNone(self.check(url).json()["signed_in"], url)

    def test_a_browser_error_is_explained(self):
        r = self.check(fail=RuntimeError("browser died"))
        self.assertEqual(r.status_code, 502)
        self.assertIn("browser died", r.json()["reason"])

    def test_not_while_a_task_is_running_and_counts_as_using_the_browser(self):
        agent_ui._ctl.running = True
        self.assertEqual(self.check("https://www.linkedin.com/feed/").status_code, 409)
        agent_ui._ctl.running = False
        with mock.patch.object(agent_ui, "_last_user_activity", float("-inf")):
            self.check("https://www.linkedin.com/feed/")
            self.assertTrue(agent_ui._user_is_busy())

    def test_the_check_needs_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.post("/session/check-linkedin").status_code, (401, 403))

    def test_the_inbox(self):
        self.notes.add("error", "Needs you", "sign in")
        self.notes.add("info", "Finished", "ok")
        data = self.c.get("/notifications").json()
        self.assertEqual((data["unread"], [i["title"] for i in data["items"]]), (2, ["Finished", "Needs you"]))
        first = data["items"][1]["id"]
        self.assertEqual(self.c.post("/notifications/read", json={"ids": [first]}).json(), {"ok": True, "changed": 1, "unread": 1})
        self.assertEqual(self.c.post("/notifications/read", json={}).json()["unread"], 0)
        self.assertEqual(self.c.get("/notifications?limit=1").json()["items"][0]["title"], "Finished")
        self.assertEqual(self.c.delete("/notifications").json()["cleared"], 2)
        self.assertEqual(self.c.get("/notifications").json()["items"], [])

    def test_the_desktop_switch(self):
        self.assertFalse(self.c.post("/notifications/desktop", json={"enabled": False}).json()["desktop"])
        self.assertFalse(self.c.get("/schedules").json()["desktop"])
        self.assertTrue(self.c.post("/notifications/desktop", json={"enabled": True}).json()["desktop"])

    def test_everything_needs_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        for method, url in (("get", "/schedules"), ("post", "/schedules"), ("get", "/notifications"),
                            ("delete", "/notifications"), ("post", "/schedules/pause-all"),
                            ("post", "/scheduler/limits"), ("post", "/notifications/desktop")):
            self.assertIn(getattr(stranger, method)(url, **({"json": {}} if method == "post" else {})).status_code, (401, 403), url)


class UserPresence(Base, unittest.TestCase):
    def test_the_scheduler_waits_while_the_user_is_working_by_hand(self):
        with mock.patch.object(agent_ui, "_last_user_activity", float("-inf")):
            self.assertFalse(agent_ui._user_is_busy())
            agent_ui._touch_user()
            self.assertTrue(agent_ui._user_is_busy())
            with mock.patch.object(agent_ui.time, "monotonic", return_value=agent_ui._last_user_activity + 30):
                self.assertTrue(agent_ui._user_is_busy())
            with mock.patch.object(agent_ui.time, "monotonic", return_value=agent_ui._last_user_activity + 61):
                self.assertFalse(agent_ui._user_is_busy())

    def test_a_running_or_paused_task_counts_as_busy(self):
        with mock.patch.object(agent_ui, "_last_user_activity", float("-inf")):
            agent_ui._ctl.running = True
            self.assertTrue(agent_ui._user_is_busy())
            agent_ui._ctl.running, agent_ui._ctl.paused = False, True
            self.assertTrue(agent_ui._user_is_busy())

    def test_toolbar_and_live_view_use_count_as_activity(self):
        from fastapi.testclient import TestClient
        c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        c.get("/")
        with mock.patch.object(agent_ui, "_last_user_activity", float("-inf")):
            c.post("/browser/reload")                      # fails without a browser, but the user DID try
            self.assertTrue(agent_ui._user_is_busy())

    def test_the_real_scheduler_only_runs_when_the_user_is_not_busy(self):
        self.assertIs(REAL_SCHEDULER._idle, agent_ui._scheduler_idle)
        with mock.patch.object(agent_ui, "_user_is_busy", return_value=True):
            self.assertFalse(agent_ui._scheduler_idle())
        with mock.patch.object(agent_ui, "_user_is_busy", return_value=False):
            self.assertTrue(agent_ui._scheduler_idle())


class Notifying(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws = FakeClient()
        agent_ui._clients.add(self.ws)
        self.addCleanup(agent_ui._clients.discard, self.ws)

    async def test_a_notification_is_stored_pushed_and_toasted_when_it_matters(self):
        await agent_ui._notify_user("error", "Needs you", "sign in", schedule_id="sch_x", run_id="run_y")
        await agent_ui._notify_user("info", "Finished", "ok")
        stored = self.notes.recent()
        self.assertEqual([n["title"] for n in stored], ["Finished", "Needs you"])
        pushed = self.ws.of("notification")
        self.assertEqual([(m["title"], m["unread"]) for m in pushed], [("Needs you", 1), ("Finished", 2)])
        self.assertEqual(pushed[0]["schedule_id"], "sch_x")
        self.assertEqual(self.toasts, [("Needs you", "sign in")])               # only the one that needs attention

    async def test_no_pop_up_in_quiet_hours_but_the_inbox_still_records(self):
        self.scheds.set_quiet_hours(sch.QuietHours(enabled=True, start="00:00", end="23:59"))
        await agent_ui._notify_user("error", "Needs you", "sign in")
        self.assertEqual(self.toasts, [])
        self.assertEqual(self.notes.recent()[0]["title"], "Needs you")
        self.assertEqual(len(self.ws.of("notification")), 1)                   # the open window is still told
        self.scheds.set_quiet_hours(sch.QuietHours(enabled=False))
        await agent_ui._notify_user("error", "Needs you again", "")
        self.assertEqual([t[0] for t in self.toasts], ["Needs you again"])

    async def test_a_warning_also_toasts_and_unknown_fields_are_dropped(self):
        await agent_ui._notify_user("warning", "Limit", "x", bogus="field")
        self.assertEqual(self.toasts, [("Limit", "x")])
        self.assertNotIn("bogus", self.notes.recent()[0])

    async def test_a_full_disk_never_breaks_the_scheduler(self):
        with mock.patch.object(self.notes, "add", side_effect=OSError("disk full")):
            await agent_ui._notify_user("error", "x")                           # must not raise
        self.assertEqual(self.ws.of("notification"), [])


class Handoff(Base, unittest.IsolatedAsyncioTestCase):
    """_execute_scheduled and run_playbook_unattended."""
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.ws = FakeClient()
        agent_ui._clients.add(self.ws)
        self.addCleanup(agent_ui._clients.discard, self.ws)
        self.result = RunResult(status="done", steps=[StepResult("s1", "open", "Open a page")], started_at=0, finished_at=2)
        self.player_args = None
        outer = self

        class FakePlayer:
            async def run(self, pb, values, approved=None, done=None, answers=None, skipped=None):
                outer.player_args = (pb, values)
                outer.approved_seen = approved
                if isinstance(outer.result, BaseException):
                    raise outer.result
                if outer.result == "hang":
                    await asyncio.sleep(60)
                return outer.result
        self.attended_seen = []

        async def build(pb, attended):
            self.attended_seen.append(attended)
            return FakePlayer()
        p = mock.patch.object(agent_ui, "_build_player", build)
        p.start(); self.addCleanup(p.stop)
        self.schedule = self.scheds.save(sch.Schedule(name="Daily", playbook_id=self.pb.id, items=[{"p": URL}]))
        agent_ui._conversation_history.clear()

    async def run_it(self):
        return await agent_ui._execute_scheduled(self.pb, {"p": URL}, self.schedule, 0)

    async def test_a_scheduled_run_is_unattended_recorded_and_leaves_the_chat_alone(self):
        out = await self.run_it()
        self.assertEqual((out.status, out.detail), ("done", ""))
        self.assertEqual(self.attended_seen, [False])
        rec = self.runs.get(self.pb.id, out.run_id)
        self.assertEqual((rec["status"], rec["attended"], rec["schedule_id"], rec["row"]), ("done", False, self.schedule.id, 0))
        self.assertEqual(rec["inputs"], {"p": URL})
        self.assertEqual(agent_ui._conversation_history, [])
        self.assertEqual([m["text"] for m in self.ws.of("status")], ["running", "done"])
        self.assertIn("⏰ Scheduled: Daily (row 1 of 1)", [m["text"] for m in self.ws.of("action")])
        self.assertEqual(self.ws.of("result"), [])                              # no answer bubble in the chat
        self.assertFalse(agent_ui._ctl.running)

    async def test_a_picture_of_where_it_stopped_is_kept_with_the_record(self):
        self.result = RunResult(status="failed", error="Couldn't find Go on the page.", started_at=0, finished_at=1,
                                steps=[StepResult("s1", "click", status="failed", detail="Couldn't find Go")],
                                screenshot=b"\xff\xd8\xff-fake-jpeg")
        out = await self.run_it()
        rec = self.runs.get(self.pb.id, out.run_id)
        self.assertTrue(rec["screenshot"])
        self.assertEqual(self.runs.screenshot(self.pb.id, out.run_id), b"\xff\xd8\xff-fake-jpeg")

    async def test_a_run_that_finished_has_no_picture(self):
        out = await self.run_it()
        self.assertNotIn("screenshot", {k for k, v in self.runs.get(self.pb.id, out.run_id).items() if v is True})
        self.assertIsNone(self.runs.screenshot(self.pb.id, out.run_id))

    async def test_a_picture_that_cannot_be_saved_does_not_lose_the_record(self):
        self.result = RunResult(status="failed", error="x", started_at=0, finished_at=1, screenshot=b"jpg")
        with mock.patch.object(self.runs, "save_screenshot", side_effect=OSError("disk full")):
            out = await self.run_it()
        rec = self.runs.get(self.pb.id, out.run_id)
        self.assertEqual(rec["status"], "failed")
        self.assertNotIn("screenshot", rec)

    async def test_what_stopped_the_run_is_passed_on(self):
        self.result = RunResult(status="needs_you", error="Step 1: asking you to sign in", started_at=0, finished_at=1)
        out = await self.run_it()
        self.assertEqual((out.status, out.detail), ("needs_you", "Step 1: asking you to sign in"))
        self.assertEqual(self.runs.get(self.pb.id, out.run_id)["status"], "needs_you")
        self.assertEqual(self.ws.of("status")[-1]["text"], "error")

    async def test_a_busy_slot_means_try_again_later(self):
        agent_ui._ctl.running = True
        out = await self.run_it()
        self.assertEqual(out.status, "busy")
        self.assertEqual(self.attended_seen, [])

    async def test_a_crash_is_a_failed_row(self):
        self.result = RuntimeError("browser went away")
        out = await self.run_it()
        self.assertEqual((out.status, out.detail), ("failed", "browser went away"))
        self.assertEqual(self.runs.get(self.pb.id, out.run_id)["status"], "failed")

    async def test_the_user_pressing_stop_ends_it_as_stopped(self):
        self.result = "hang"
        task = asyncio.create_task(self.run_it())
        for _ in range(100):
            if self.attended_seen:
                break
            await asyncio.sleep(0.01)
        await agent_ui._ctl.stop()
        out = await asyncio.wait_for(task, 5)
        self.assertEqual(out.status, "stopped")
        recs = self.runs.recent(self.pb.id)
        self.assertEqual(recs[0]["status"], "stopped")
        self.assertFalse(agent_ui._ctl.running)

    async def test_a_stop_before_the_run_even_began_does_not_hang_the_scheduler(self):
        task = asyncio.create_task(self.run_it())
        await asyncio.sleep(0)                                                  # the run was claimed, not yet started
        await agent_ui._ctl.stop()
        out = await asyncio.wait_for(task, 5)
        self.assertEqual(out.status, "stopped")

    async def test_the_whole_loop_from_a_due_schedule_to_a_record_and_a_notification(self):
        pbk_saved = self.pbs.get(self.pb.id)
        s = self.scheds.change_state(self.schedule.id, lambda st: st.model_copy(update={"next_run": sch.iso(datetime.now() - timedelta(seconds=5))}))
        real = Scheduler(schedules=self.scheds, playbooks=self.pbs, limits=self.limits, execute=agent_ui._execute_scheduled,
                         idle=lambda: not agent_ui._ctl.running, notify=agent_ui._notify_user)
        self.result = RunResult(status="needs_you", error="linkedin.com is asking you to sign in.", started_at=0, finished_at=1)
        await real.tick()
        got = self.scheds.get(s.id)
        self.assertEqual((got.state.paused_reason, got.state.last_status), ("needs_you", "needs_you"))
        self.assertEqual(self.notes.recent()[0]["title"], 'Needs you: "Daily"')
        self.assertEqual(self.toasts[0][0], 'Needs you: "Daily"')
        self.assertEqual(self.limits.used_today("linkedin_profile_view", datetime.now().date()), 1)
        self.assertEqual(len(self.runs.recent(pbk_saved.id)), 1)


class CheckpointFlow(Base, unittest.IsolatedAsyncioTestCase):
    """A scheduled run that reaches an approve step is parked, answered from the inbox, and run again."""
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.ws = FakeClient()
        agent_ui._clients.add(self.ws)
        self.addCleanup(agent_ui._clients.discard, self.ws)
        fp = Fingerprint(node_name="button", ax_name="Send")
        self.send = self.pbs.save(pbk.refresh(Playbook(
            name="Message", variables=[Variable(name="p", type="linkedin_url")],
            steps=[Step(id="s1", kind="open", url="{{p}}"), Step(id="s2", kind="approve", message="Send to {{p}}?"),
                   Step(id="s3", kind="click", target=fp, ask=True)])), approve=True)
        self.schedule = self.scheds.save(sch.Schedule(name="Outreach", playbook_id=self.send.id, items=[{"p": URL}]))
        self.waiting = {"message": "Send to Jane?", "detail": 'Typed just before: "Hi"', "url": URL, "step_id": "s2",
                        "index": 1, "timeout_minutes": 60}
        self.result = RunResult(status="waiting", error="Step 2: waiting for you.", started_at=0, finished_at=1,
                                steps=[StepResult("s1", "open"), StepResult("s2", "approve", status="waiting")],
                                waiting=self.waiting, screenshot=b"\xff\xd8\xff-picture")
        self.approved_seen = None
        self.starts = 0
        outer = self
        p = mock.patch.object(self.limits, "check", return_value=Decision(True))        # pacing is tested elsewhere
        p.start(); self.addCleanup(p.stop)

        class FakePlayer:
            async def run(self, pb, values, approved=None, done=None, answers=None, skipped=None):
                outer.approved_seen = approved
                outer.done_seen = done
                outer.answers_seen = answers
                outer.starts += 1
                return outer.result
        async def build(pb, attended):
            return FakePlayer()
        p = mock.patch.object(agent_ui, "_build_player", build)
        p.start(); self.addCleanup(p.stop)
        self.real = Scheduler(schedules=self.scheds, playbooks=self.pbs, limits=self.limits, execute=agent_ui._execute_scheduled,
                              idle=lambda: not agent_ui._ctl.running, notify=agent_ui._notify_user,
                              resolved=agent_ui._record_parked_end)
        p = mock.patch.object(agent_ui, "_scheduler", self.real)
        p.start(); self.addCleanup(p.stop)
        self.scheds.change_state(self.schedule.id, lambda st: st.model_copy(update={
            "next_run": sch.iso(datetime.now() - timedelta(seconds=5))}))
        from fastapi.testclient import TestClient
        self.client = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.client.get("/")

    async def park(self):
        await self.real.tick()
        return self.scheds.get(self.schedule.id).state.parked[0]

    async def test_the_scheduled_run_parks_and_the_record_says_what_it_is_waiting_for(self):
        job = await self.park()
        rec = self.runs.get(self.send.id, job.run_id)
        self.assertEqual(rec["status"], "waiting")
        self.assertEqual(rec["waiting"]["message"], "Send to Jane?")
        self.assertTrue(rec["screenshot"])                                         # what the page looked like at the question
        self.assertEqual(self.runs.screenshot(self.send.id, job.run_id), b"\xff\xd8\xff-picture")
        self.assertEqual(rec["answered"], [])
        self.assertFalse(agent_ui._ctl.running)                                    # the browser is free again
        self.assertEqual(self.ws.of("status")[-1]["text"], "done")                 # not shown as an error

    async def test_the_question_is_in_the_inbox_with_buttons_and_in_the_schedule(self):
        job = await self.park()
        n = self.notes.recent()[0]
        self.assertEqual(n["action"], {"type": "checkpoint", "schedule_id": self.schedule.id, "run_id": job.run_id, "kind": "approve"})
        self.assertEqual(self.ws.of("notification")[0]["action"]["run_id"], job.run_id)
        row = self.client.get("/schedules").json()["schedules"][0]
        self.assertEqual(row["status"], "asking")
        self.assertEqual(row["pending"][0]["message"], "Send to Jane?")
        self.assertEqual(row["pending"][0]["detail"], 'Typed just before: "Hi"')

    async def test_yes_over_http_runs_it_again_under_the_same_run_id_with_the_answer(self):
        job = await self.park()
        r = self.client.post(f"/schedules/{self.schedule.id}/checkpoints/{job.run_id}/answer", json={"approve": True})
        self.assertEqual(r.json(), {"ok": True})
        self.result = RunResult(status="done", steps=[StepResult("s1", "open")], started_at=0, finished_at=2)
        await self.real.tick()
        self.assertEqual(self.approved_seen, {"s2"})
        rec = self.runs.get(self.send.id, job.run_id)
        self.assertEqual((rec["status"], rec["answered"]), ("done", ["s2"]))
        self.assertEqual(len(self.runs.recent(self.send.id)), 1)                    # one entry, not two
        self.assertEqual(self.scheds.get(self.schedule.id).state.last_status, "done")

    async def input_question(self):
        pb = self.pbs.save(pbk.refresh(Playbook(
            name="Code", variables=[Variable(name="who"), Variable(name="code", type="number", runtime=True)],
            steps=[Step(id="s1", kind="open", url="https://www.linkedin.com/feed/"),
                   Step(id="s2", kind="input", message="Code for {{who}}?", variable="code"),
                   Step(id="s3", kind="wait", seconds=1)])), approve=True)
        self.scheds.change_state(self.schedule.id, lambda st: st.model_copy(update={"next_run": None}))     # only this one is due
        sc = self.scheds.save(sch.Schedule(name="Codes", playbook_id=pb.id, items=[{"who": "Jane"}]))
        self.waiting = {"kind": "input", "message": "Code for Jane?", "detail": "", "url": "", "variable": "code",
                        "step_id": "s2", "index": 1, "timeout_minutes": 60, "on_timeout": "cancel", "guarded": ""}
        self.result = RunResult(status="waiting", error="waiting", started_at=0, finished_at=1,
                                steps=[StepResult("s1", "open"), StepResult("s2", "input", status="waiting")], waiting=self.waiting)
        self.scheds.change_state(sc.id, lambda st: st.model_copy(update={"next_run": sch.iso(datetime.now() - timedelta(seconds=5))}))
        await self.real.tick()
        return sc, self.scheds.get(sc.id).state.parked[0]

    async def test_a_question_for_a_value_is_shown_as_one_and_needs_a_value_that_fits(self):
        sc, job = await self.input_question()
        row = next(r for r in self.client.get("/schedules").json()["schedules"] if r["id"] == sc.id)
        self.assertEqual((row["pending"][0]["kind"], row["pending"][0]["variable"]), ("input", "code"))
        url = f"/schedules/{sc.id}/checkpoints/{job.run_id}/answer"
        self.assertEqual(self.client.post(url, json={"approve": True}).status_code, 409)                     # no value
        self.assertEqual(self.client.post(url, json={"approve": True, "value": 5}).status_code, 400)         # not text
        r = self.client.post(url, json={"approve": True, "value": "twelve"})                                # not a number
        self.assertEqual(r.status_code, 400)
        self.assertIn("number", r.json()["reason"])
        self.assertEqual(len(self.scheds.get(sc.id).state.parked), 1)                                       # still waiting
        self.assertEqual(self.client.post(url, json={"approve": True, "value": "42"}).json(), {"ok": True})
        self.assertEqual(self.scheds.get(sc.id).state.resume[0].typed, ["s2"])

    async def test_ticking_remember_keeps_the_answer_for_later_runs_and_otherwise_it_is_not(self):
        from aiba.storage.remembered_answers import RememberedAnswers
        keep = RememberedAnswers(pathlib.Path(tempfile.mkdtemp()) / "r.json")
        p = mock.patch.object(agent_ui, "_remembered", keep)
        p.start(); self.addCleanup(p.stop)
        sc, job = await self.input_question()
        url = f"/schedules/{sc.id}/checkpoints/{job.run_id}/answer"
        self.assertTrue(self.client.post(url, json={"approve": True, "value": "42", "remember": "true"}).json()["ok"])
        self.assertEqual(keep.get(sc.playbook_id), {})                                    # only a real true counts
        self.scheds.change_state(sc.id, lambda st: st.model_copy(update={"resume": [], "parked": [job]}))
        self.assertTrue(self.client.post(url, json={"approve": True, "value": "43", "remember": True}).json()["ok"])
        self.assertEqual(keep.get(sc.playbook_id), {"code": "43"})

    async def test_an_answer_the_scheduler_turned_away_is_not_remembered(self):
        from aiba.storage.remembered_answers import RememberedAnswers
        keep = RememberedAnswers(pathlib.Path(tempfile.mkdtemp()) / "r.json")
        p = mock.patch.object(agent_ui, "_remembered", keep)
        p.start(); self.addCleanup(p.stop)
        sc, job = await self.input_question()
        url = f"/schedules/{sc.id}/checkpoints/{job.run_id}/answer"
        self.assertTrue(self.client.post(url, json={"approve": True, "value": "42"}).json()["ok"])
        again = self.client.post(url, json={"approve": True, "value": "99", "remember": True})      # already answered
        self.assertEqual(again.status_code, 409)
        self.assertEqual(keep.get(sc.playbook_id), {})

    async def test_a_scheduled_run_uses_a_kept_answer_and_so_does_not_ask(self):
        from aiba.storage.remembered_answers import RememberedAnswers
        keep = RememberedAnswers(pathlib.Path(tempfile.mkdtemp()) / "r.json")
        p = mock.patch.object(agent_ui, "_remembered", keep)
        p.start(); self.addCleanup(p.stop)
        pb = self.pbs.save(pbk.refresh(Playbook(
            name="Code", variables=[Variable(name="code", type="number", runtime=True)],
            steps=[Step(id="s1", kind="open", url="https://www.linkedin.com/feed/"),
                   Step(id="s2", kind="input", message="Code?", variable="code"),
                   Step(id="s3", kind="wait", seconds=1)])), approve=True)
        keep.set(pb.id, "code", "42")
        self.scheds.change_state(self.schedule.id, lambda st: st.model_copy(update={"next_run": None}))
        sc = self.scheds.save(sch.Schedule(name="Codes", playbook_id=pb.id, items=[{}]))
        self.scheds.change_state(sc.id, lambda st: st.model_copy(update={"next_run": sch.iso(datetime.now() - timedelta(seconds=5))}))
        self.result = RunResult(status="done", steps=[StepResult("s1", "open")], started_at=0, finished_at=2)
        await self.real.tick()
        self.assertEqual(self.answers_seen, {"s2": "42"})

    async def test_a_refused_answer_is_not_remembered(self):
        from aiba.storage.remembered_answers import RememberedAnswers
        keep = RememberedAnswers(pathlib.Path(tempfile.mkdtemp()) / "r.json")
        p = mock.patch.object(agent_ui, "_remembered", keep)
        p.start(); self.addCleanup(p.stop)
        sc, job = await self.input_question()
        r = self.client.post(f"/schedules/{sc.id}/checkpoints/{job.run_id}/answer", json={"approve": True, "value": "twelve", "remember": True})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(keep.get(sc.playbook_id), {})

    async def test_an_answer_for_a_question_the_playbook_no_longer_has_is_refused(self):
        sc, job = await self.input_question()
        self.scheds.change_state(sc.id, lambda st: st.model_copy(update={"parked": [
            p.model_copy(update={"variable": "gone"}) for p in st.parked]}))
        r = self.client.post(f"/schedules/{sc.id}/checkpoints/{job.run_id}/answer", json={"approve": True, "value": "42"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.scheds.get(sc.id).state.resume, [])

    async def test_the_value_reaches_the_player_when_the_run_starts_again(self):
        sc, job = await self.input_question()
        self.client.post(f"/schedules/{sc.id}/checkpoints/{job.run_id}/answer", json={"approve": True, "value": "42"})
        self.result = RunResult(status="done", steps=[StepResult("s1", "open")], started_at=0, finished_at=2)
        await self.real.tick()
        self.assertEqual(self.answers_seen, {"s2": "42"})
        self.assertEqual(self.approved_seen, set())                                                         # typing approves nothing

    async def test_no_over_http_ends_it_and_the_history_says_so(self):
        job = await self.park()
        r = self.client.post(f"/schedules/{self.schedule.id}/checkpoints/{job.run_id}/answer", json={"approve": False})
        self.assertTrue(r.json()["ok"])
        rec = self.runs.get(self.send.id, job.run_id)
        self.assertEqual(rec["status"], "declined")
        self.assertNotIn("waiting", rec)
        self.assertIn("You said no", rec["error"])
        await self.real.tick()
        self.assertEqual(self.starts, 1)                                            # never run again

    async def test_only_a_real_true_is_a_yes(self):
        job = await self.park()
        for sneaky in ("true", 1, "yes", [True], None):
            self.client.post(f"/schedules/{self.schedule.id}/checkpoints/{job.run_id}/answer", json={"approve": sneaky})
            self.assertEqual(self.scheds.get(self.schedule.id).state.resume, [], sneaky)    # all of those were a no
            break                                                                  # the first no already ended it

    async def test_a_yes_to_something_that_is_not_waiting_is_refused(self):
        job = await self.park()
        base = f"/schedules/{self.schedule.id}/checkpoints"
        self.assertEqual(self.client.post(f"{base}/run_{'0' * 12}/answer", json={"approve": True}).status_code, 409)
        self.assertEqual(self.client.post(f"/schedules/sch_{'0' * 12}/checkpoints/{job.run_id}/answer", json={"approve": True}).status_code, 404)
        self.assertEqual(self.client.post(f"{base}/..%5Cx/answer", json={"approve": True}).status_code, 400)
        self.assertEqual(self.client.post("/schedules/nope/checkpoints/x/answer", json={"approve": True}).status_code, 400)
        self.assertEqual(self.client.post(f"{base}/{job.run_id}/answer", json={"approve": True}).status_code, 200)
        self.assertEqual(self.client.post(f"{base}/{job.run_id}/answer", json={"approve": True}).status_code, 409)   # twice

    async def test_the_answer_needs_the_app_cookie(self):
        from fastapi.testclient import TestClient
        job = await self.park()
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        r = stranger.post(f"/schedules/{self.schedule.id}/checkpoints/{job.run_id}/answer", json={"approve": True})
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(self.scheds.get(self.schedule.id).state.resume, [])

    async def test_deleting_the_schedule_cancels_what_was_waiting(self):
        job = await self.park()
        self.assertTrue(self.client.delete(f"/schedules/{self.schedule.id}").json()["deleted"])
        rec = self.runs.get(self.send.id, job.run_id)
        self.assertEqual(rec["status"], "cancelled")
        self.assertIn("schedule was deleted", rec["error"])

    async def test_a_send_guarded_by_an_approve_step_can_be_scheduled_without_the_blanket_tick(self):
        body = {"schedule": {"name": "Guarded", "playbook_id": self.send.id, "items": [{"p": URL}],
                             "cadence": {"kind": "daily", "at": "09:00"}, "allow_sensitive": False}}
        self.assertEqual(self.client.post("/schedules", json=body).status_code, 200)

    async def test_silence_cancels_and_the_history_says_so(self):
        job = await self.park()
        self.scheds.change_state(self.schedule.id, lambda st: st.model_copy(update={"parked": [
            job.model_copy(update={"expires_at": sch.iso(datetime.now() - timedelta(minutes=1))})]}))
        await self.real.tick()
        self.assertEqual(self.runs.get(self.send.id, job.run_id)["status"], "expired")
        self.assertEqual(self.scheds.get(self.schedule.id).state.parked, [])


class ManualRunsCount(Base, unittest.IsolatedAsyncioTestCase):
    async def test_a_manual_run_counts_toward_the_allowance(self):
        agent_ui._count_usage(self.pb, {"p": URL})
        self.assertEqual(self.limits.used_today("linkedin_profile_view", datetime.now().date()), 1)

    async def test_a_counting_failure_never_breaks_a_run(self):
        with mock.patch.object(self.limits, "record", side_effect=OSError("disk")):
            agent_ui._count_usage(self.pb, {"p": URL})


if __name__ == "__main__":
    unittest.main()
