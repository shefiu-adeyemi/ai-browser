"""
Scheduled (unattended) runs through the real scheduler, the real runner and a REAL Chromium. No API cost:
a playbook run needs no model, and the one AI-style task here is scripted.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_scheduler_browser -v
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import agent_ui
import playbook as pbk
import schedules as sch
from notifications import DesktopNotifier, NotificationStore
from playbook_match import candidates_from_selector_map
from playbook_runs import RunStore
from playbook_store import PlaybookStore
from schedule_store import ScheduleStore
from scheduler import Scheduler
from site_limits import SiteLimits
from test_lifecycle_browser import LifecycleBase, until
from test_playbook_player_browser import _FormPage


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class ScheduledRuns(LifecycleBase):
    async def asyncSetUp(self):
        import test_lifecycle_browser as tlb
        orig = tlb._Page
        tlb._Page = _FormPage
        try:
            await super().asyncSetUp()
        finally:
            tlb._Page = orig
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="aiba-sch-"))
        self.pbs, self.runs = PlaybookStore(tmp / "playbooks"), RunStore(tmp / "runs")
        self.scheds, self.notes = ScheduleStore(tmp / "s.json"), NotificationStore(tmp / "n.json")
        self.limits = SiteLimits(tmp / "l.json")
        self.toasts = []
        desktop = DesktopNotifier(show=lambda t, x: self.toasts.append(t))
        for target, value in (("_playbooks", self.pbs), ("_runs", self.runs), ("_schedules", self.scheds),
                              ("_limits", self.limits), ("_notifications", self.notes), ("_desktop", desktop)):
            p = mock.patch.object(agent_ui, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.sched = Scheduler(schedules=self.scheds, playbooks=self.pbs, limits=self.limits,
                               execute=agent_ui._execute_scheduled,
                               idle=lambda: not agent_ui._ctl.running and not agent_ui._ctl.paused,
                               notify=agent_ui._notify_user)

    async def send_button(self):
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.url)
        await asyncio.sleep(1.5)
        state = await session.get_browser_state_summary(include_screenshot=False)
        return {c.attributes.get("id"): c for c in candidates_from_selector_map(state.dom_state.selector_map)}["send"]

    def schedule(self, pb, allow_sensitive=False):
        s = self.scheds.save(sch.Schedule(name="Unattended", playbook_id=pb.id, items=[{}], on_failure="stop",
                                          allow_sensitive=allow_sensitive))
        due = datetime.now() - timedelta(seconds=5)
        return self.scheds.change_state(s.id, lambda st: st.model_copy(update={"next_run": sch.iso(due)}))

    async def finished_run(self, pb):
        await self.sched.tick()
        await until(lambda: not agent_ui._ctl.running and self.runs.recent(pb.id), what="the scheduled run")
        return self.runs.get(pb.id, self.runs.recent(pb.id)[0]["run_id"])

    def send_playbook(self, fp):
        steps = [pbk.Step(id="s1", kind="open", url=self.url), pbk.Step(id="s2", kind="click", target=fp, ask=True)]
        return self.pbs.save(pbk.refresh(pbk.Playbook(name="Send", steps=steps)), approve=True)

    async def test_a_due_schedule_runs_by_itself_and_records_it(self):
        pb = self.pbs.save(pbk.refresh(pbk.Playbook(name="Open the form", steps=[
            pbk.Step(id="s1", kind="open", url=self.url), pbk.Step(id="s2", kind="wait", seconds=1)])), approve=True)
        s = self.schedule(pb)
        rec = await self.finished_run(pb)
        self.assertEqual((rec["status"], rec["attended"], rec["schedule_id"]), ("done", False, s.id))
        page = await self.session.get_current_page()
        self.assertEqual(await page.evaluate("() => document.title"), "form")
        got = self.scheds.get(s.id)
        self.assertEqual((got.state.last_status, got.state.runs_total, got.state.paused_reason), ("done", 1, ""))
        self.assertEqual(self.notes.recent(), [])                              # a quiet success
        self.assertEqual(len(self.llms), 0)

    async def test_an_approved_send_runs_unattended_with_the_second_yes_and_a_certain_match(self):
        c = await self.send_button()
        fp = pbk.Fingerprint(node_name=c.node_name, attributes={"id": "send"}, ax_name=c.ax_name, x_path=c.x_path,
                             element_hash=c.element_hash, stable_hash=c.stable_hash)
        pb = self.send_playbook(fp)
        self.schedule(pb, allow_sensitive=True)
        await self.session.navigate_to("about:blank")
        rec = await self.finished_run(pb)
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(rec["steps"][1]["approval"], "preapproved")
        page = await self.session.get_current_page()
        self.assertEqual(await page.evaluate("() => document.title"), "SENT")
        self.assertEqual([m for m in self.ws.msgs if m["type"] == "approval"], [])

    async def test_an_unsure_match_stops_an_unattended_send_and_tells_the_user(self):
        c = await self.send_button()
        # only the visible name still matches: not certain enough to press Send with nobody there
        fp = pbk.Fingerprint(node_name=c.node_name, ax_name=c.ax_name, x_path="/somewhere/else",
                             element_hash="1", stable_hash="2")
        pb = self.send_playbook(fp)
        s = self.schedule(pb, allow_sensitive=True)
        await self.session.navigate_to("about:blank")
        rec = await self.finished_run(pb)
        self.assertEqual(rec["status"], "needs_you", rec)
        page = await self.session.get_current_page()
        self.assertNotEqual(await page.evaluate("() => document.title"), "SENT", "it pressed Send without being sure")
        self.assertEqual(self.scheds.get(s.id).state.paused_reason, "needs_you")
        self.assertEqual(self.notes.recent()[0]["title"], 'Needs you: "Unattended"')
        self.assertTrue(self.toasts)
        self.assertEqual([m for m in self.ws.msgs if m["type"] == "approval"], [], "an unattended run must never ask")
        self.assertEqual(len([m for m in self.ws.msgs if m["type"] == "notification"]), 1)

    async def test_a_scheduled_run_that_stops_keeps_a_real_picture_of_the_page(self):
        from playbook_player import PlaybookPlayer as _PP
        ghost = pbk.Fingerprint(node_name="button", ax_name="Not on this page", x_path="/nope", element_hash="1", stable_hash="2")
        pb = self.pbs.save(pbk.refresh(pbk.Playbook(name="Click a ghost", steps=[
            pbk.Step(id="s1", kind="open", url=self.url), pbk.Step(id="s2", kind="click", target=ghost)])), approve=True)
        self.schedule(pb)
        quick = lambda *a, **k: _PP(*a, find_timeout=1.0, **k)                # do not wait the full 10 s
        with mock.patch.object(agent_ui, "PlaybookPlayer", quick):
            rec = await self.finished_run(pb)
        self.assertEqual(rec["status"], "failed", rec)
        self.assertTrue(rec["screenshot"])
        data = self.runs.screenshot(pb.id, rec["run_id"])
        self.assertTrue(data and data[:2] == b"\xff\xd8", "a JPEG of the page")
        self.assertLess(len(data), 400_000)

    async def test_a_user_task_in_progress_is_not_interrupted(self):
        pb = self.pbs.save(pbk.refresh(pbk.Playbook(name="Open the form", steps=[
            pbk.Step(id="s1", kind="open", url=self.url)])), approve=True)
        self.schedule(pb)
        self.scripts.append([{"wait": 3}, {"done": "mine"}])
        self.assertTrue((await agent_ui.start_task({"task": "my own task"}))["ok"])
        await until(lambda: len(self.llms) == 1, what="my task to start")
        await self.sched.tick()
        self.assertEqual(self.runs.recent(pb.id), [], "the scheduled run barged in")
        await until(lambda: not agent_ui._ctl.running, what="my task to finish")
        rec = await self.finished_run(pb)
        self.assertEqual(rec["status"], "done")


if __name__ == "__main__":
    unittest.main()
