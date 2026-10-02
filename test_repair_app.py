"""Self-repair through the app: building the repairer, telling the user, and applying repairs to the playbook."""

import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest import mock

import agent_ui
import playbook as pbk
import playbook_repair as pr
import schedules as sch
from playbook import Fingerprint, Playbook, ScrapeField, Step
from playbook_player import RunResult, StepResult
from scheduler import Scheduler
from site_limits import Decision
from test_schedules_app import Base, FakeClient

RUN = "run_" + "a" * 12
NOW = {"node_name": "button", "ax_name": "Dispatch", "attributes": {"id": "go"}}


def repairable(repair=True):
    return pbk.refresh(Playbook(name="Shop", repair=repair, steps=[
        Step(id="s1", kind="open", url="https://a.com/"),
        Step(id="s2", kind="click", target=Fingerprint(node_name="button", ax_name="Send", attributes={"id": "send"})),
        Step(id="s3", kind="scrape", row_selector="tr.old", fields=[ScrapeField(name="Name", selector=".n")])]))


class Building(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.page = object()
        class Session:
            async def get_current_page(s): return outer.page
        outer = self
        async def ensure(): return Session()
        async def no_trim(*a, **k): return None
        for target, value in (("_ensure_session", ensure), ("trim_tabs", no_trim), ("linkedin_sensitive_data", lambda: None)):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)
        self.slots = {"standard": NS(usable=True)}
        p = mock.patch.object(agent_ui.model_tiers, "read_slots", lambda *a, **k: self.slots)
        p.start(); self.addCleanup(p.stop)
        self.asked = []
        p = mock.patch.object(agent_ui._router, "escalating", lambda purpose, **kw: (self.asked.append(purpose), "LLM")[1])
        p.start(); self.addCleanup(p.stop)

    async def test_a_playbook_that_allows_it_gets_a_repairer_on_the_repair_model(self):
        player = await agent_ui._build_player(repairable(), attended=False)
        self.assertIsInstance(player._repairer, pr.Repairer)
        self.assertEqual(self.asked, ["repair"])
        self.assertEqual(player._repairer.llm, "LLM")

    async def test_not_unless_the_playbook_allows_it(self):
        player = await agent_ui._build_player(repairable(repair=False), attended=False)
        self.assertIsNone(player._repairer)
        self.assertEqual(self.asked, [])

    async def test_not_without_a_model(self):
        self.slots = {"standard": NS(usable=False)}
        self.assertIsNone((await agent_ui._build_player(repairable(), attended=False))._repairer)

    async def test_not_for_a_playbook_with_nothing_to_find(self):
        pb = pbk.refresh(Playbook(name="Open only", repair=True, steps=[Step(id="s1", kind="open", url="https://a.com/")]))
        self.assertIsNone((await agent_ui._build_player(pb, attended=False))._repairer)

    async def test_a_model_setup_that_breaks_means_no_repairs_not_a_failed_run(self):
        with mock.patch.object(agent_ui.model_tiers, "read_slots", side_effect=RuntimeError("bad settings")):
            self.assertIsNone((await agent_ui._build_player(repairable(), attended=False))._repairer)


class Telling(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws = FakeClient()
        agent_ui._clients.add(self.ws)
        self.addCleanup(agent_ui._clients.discard, self.ws)
        self.pb2 = self.pbs.save(repairable(), approve=True)
        self.result = None
        outer = self
        p = mock.patch.object(self.limits, "check", return_value=Decision(True))
        p.start(); self.addCleanup(p.stop)

        class FakePlayer:
            async def run(self, pb, values, **kw):
                return outer.result
        async def build(pb, attended):
            return FakePlayer()
        p = mock.patch.object(agent_ui, "_build_player", build)
        p.start(); self.addCleanup(p.stop)
        self.real = Scheduler(schedules=self.scheds, playbooks=self.pbs, limits=self.limits, execute=agent_ui._execute_scheduled,
                              idle=lambda: not agent_ui._ctl.running, notify=agent_ui._notify_user, resolved=agent_ui._record_parked_end)
        p = mock.patch.object(agent_ui, "_scheduler", self.real)
        p.start(); self.addCleanup(p.stop)

    def run_with(self, repairs):
        self.result = RunResult(status="done", started_at=0, finished_at=1, steps=[StepResult("s1", "open"), StepResult("s2", "click")],
                                repairs=repairs)
        s = self.scheds.save(sch.Schedule(name="Shop watch", playbook_id=self.pb2.id, items=[{}]))
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"next_run": sch.iso(datetime.now() - timedelta(seconds=5))}))
        return s

    async def test_a_scheduled_run_that_repaired_something_says_so_and_keeps_the_details(self):
        self.run_with([{"step_id": "s2", "kind": "element", "now": NOW, "why": "x", "summary": '"Send" was not on the page, so "Dispatch" was used instead'}])
        await self.real.tick()
        await asyncio.sleep(0)
        note = self.notes.recent()[0]
        self.assertEqual((note["title"], note["level"]), ('Repaired 1 step: "Shop"', "info"))
        self.assertIn("Dispatch", note["text"])
        self.assertIn("run history", note["text"])
        rid = self.runs.recent(self.pb2.id)[0]["run_id"]
        self.assertEqual(self.runs.get(self.pb2.id, rid)["repairs"][0]["now"], NOW)

    async def test_a_run_that_repaired_nothing_says_nothing_about_it(self):
        self.run_with([])
        await self.real.tick()
        self.assertFalse([n for n in self.notes.recent() if n["title"].startswith("Repaired")])
        rid = self.runs.recent(self.pb2.id)[0]["run_id"]
        self.assertEqual(self.runs.get(self.pb2.id, rid)["repairs"], [])

    async def test_several_repairs_are_counted(self):
        self.run_with([{"step_id": "s2", "kind": "element", "now": NOW, "summary": "a"}, {"step_id": "s3", "kind": "scrape", "now": {}, "summary": "b"}])
        await self.real.tick()
        self.assertEqual(self.notes.recent()[0]["title"], 'Repaired 2 steps: "Shop"')


class Applying(Base):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")
        self.shop = self.pbs.save(repairable(), approve=True)
        self.repairs = [{"step_id": "s2", "kind": "element", "now": NOW, "why": "x", "was": "a button", "summary": "s"},
                        {"step_id": "s3", "kind": "scrape", "why": "y", "now": {"row_selector": "tr.new", "fields": [{"name": "Name", "selector": ".t", "type": "text"}]}}]
        self.runs.save(self.shop.id, {"run_id": RUN, "started_at": 1.0, "status": "done", "playbook_version": self.shop.version,
                                      "steps": [], "outputs": [], "repairs": self.repairs})

    def apply(self, body=None, pid=None, rid=RUN):
        return self.c.post(f"/playbooks/{pid or self.shop.id}/runs/{rid}/apply-repairs", json=body if body is not None else {})

    def test_the_repairs_become_a_new_version_that_needs_approving_again(self):
        r = self.apply().json()
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(r["applied"]), ["s2", "s3"])
        self.assertFalse(r["approved"])
        now = self.pbs.get(self.shop.id)
        self.assertEqual((now.version, now.steps[1].target.ax_name, now.steps[2].row_selector), (2, "Dispatch", "tr.new"))
        self.assertFalse(pbk.is_approved(now))

    def test_only_the_steps_asked_for_are_changed(self):
        r = self.apply({"step_ids": ["s3"]}).json()
        self.assertEqual(r["applied"], ["s3"])
        now = self.pbs.get(self.shop.id)
        self.assertEqual((now.steps[1].target.ax_name, now.steps[2].row_selector), ("Send", "tr.new"))

    def test_schedules_that_depend_on_it_are_counted(self):
        self.scheds.save(sch.Schedule(name="A", playbook_id=self.shop.id, items=[{}]))
        self.scheds.save(sch.Schedule(name="B", playbook_id=self.shop.id, items=[{}], enabled=False))
        self.assertEqual(self.apply().json()["schedules_affected"], 1)

    def test_a_playbook_changed_since_the_run_is_refused(self):
        self.pbs.save(self.pbs.get(self.shop.id))                                    # version 2 now
        r = self.apply()
        self.assertEqual(r.status_code, 409)
        self.assertIn("changed since that run", r.json()["reason"])
        self.assertEqual(self.pbs.get(self.shop.id).version, 2)

    def test_a_run_that_repaired_nothing_or_is_gone_is_refused(self):
        self.runs.save(self.shop.id, {"run_id": "run_" + "b" * 12, "started_at": 2.0, "status": "done", "playbook_version": 1, "repairs": []})
        nothing = self.apply(rid="run_" + "b" * 12)
        self.assertEqual(nothing.status_code, 409)
        self.assertIn("did not repair anything", nothing.json()["reason"])
        self.assertEqual(self.apply(rid="run_" + "c" * 12).status_code, 404)

    def test_bad_requests(self):
        self.assertEqual(self.apply({"step_ids": "s2"}).status_code, 400)
        self.assertEqual(self.apply({"step_ids": [1]}).status_code, 400)
        self.assertEqual(self.apply(rid="..%5Cx").status_code, 400)
        self.assertEqual(self.apply(pid="..%5Cx").status_code, 400)
        self.assertEqual(self.apply(pid="pb_" + "0" * 12).status_code, 404)

    def test_nothing_that_fits_changes_nothing(self):
        self.runs.save(self.shop.id, {"run_id": "run_" + "d" * 12, "started_at": 3.0, "playbook_version": 1,
                                      "repairs": [{"step_id": "s1", "kind": "element", "now": NOW}]})           # s1 is an open step
        r = self.apply(rid="run_" + "d" * 12)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.pbs.get(self.shop.id).version, 1)

    def test_a_record_with_a_bad_repair_cannot_make_a_broken_playbook(self):
        bad = {"step_id": "s3", "kind": "scrape", "now": {"row_selector": "tr", "fields": []}}
        self.runs.save(self.shop.id, {"run_id": "run_" + "e" * 12, "started_at": 4.0, "playbook_version": 1, "repairs": [bad]})
        r = self.apply(rid="run_" + "e" * 12)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.pbs.get(self.shop.id).steps[2].row_selector, "tr.old")

    def test_it_needs_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.post(f"/playbooks/{self.shop.id}/runs/{RUN}/apply-repairs", json={}).status_code, (401, 403))
        self.assertEqual(self.pbs.get(self.shop.id).version, 1)


if __name__ == "__main__":
    unittest.main()
