"""
Scheduled (unattended) runs through the real scheduler, the real runner and a REAL Chromium. No API cost:
a playbook run needs no model, and the one AI-style task here is scripted.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_scheduler_browser -v
"""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.scheduling import schedules as sch
from aiba.server.notifications import DesktopNotifier, NotificationStore
from aiba.playbooks.match import candidates_from_selector_map
from aiba.playbooks.runs import RunStore
from aiba.playbooks.store import PlaybookStore
from aiba.scheduling.store import ScheduleStore
from aiba.scheduling.scheduler import Scheduler
from aiba.scheduling.limits import SiteLimits
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
                               notify=agent_ui._notify_user, resolved=agent_ui._record_parked_end)

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
        from aiba.playbooks.player import PlaybookPlayer as _PP
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

    async def candidates(self):
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.url)
        await asyncio.sleep(1.5)
        state = await session.get_browser_state_summary(include_screenshot=False)
        return {c.attributes.get("id"): c for c in candidates_from_selector_map(state.dom_state.selector_map)}

    @staticmethod
    def fingerprint(c, certain=True):
        return pbk.Fingerprint(node_name=c.node_name, attributes={"id": c.attributes.get("id")} if certain else {},
                               ax_name=c.ax_name, x_path=c.x_path if certain else "/somewhere/else",
                               element_hash=c.element_hash if certain else "1", stable_hash=c.stable_hash if certain else "2")

    async def parked_send(self, certain=True):
        cands = await self.candidates()
        steps = [pbk.Step(id="s1", kind="open", url=self.url),
                 pbk.Step(id="s2", kind="type", target=self.fingerprint(cands["q"]), text="hello there"),
                 pbk.Step(id="s3", kind="approve", message="Send this message?", timeout_minutes=60),
                 pbk.Step(id="s4", kind="click", target=self.fingerprint(cands["send"], certain), ask=True)]
        pb = self.pbs.save(pbk.refresh(pbk.Playbook(name="Guarded send", steps=steps)), approve=True)
        s = self.schedule(pb)                                      # no blanket "allow" tick: the checkpoint is the guard
        await self.session.navigate_to("about:blank")
        rec = await self.finished_run(pb)
        return pb, s, rec

    async def title(self):
        page = await self.session.get_current_page()
        return await page.evaluate("() => document.title")

    async def test_a_scheduled_send_waits_for_a_yes_then_sends_exactly_once(self):
        pb, s, rec = await self.parked_send()
        self.assertEqual(rec["status"], "waiting", rec)
        self.assertEqual(rec["waiting"]["detail"], 'Typed just before: "hello there"')
        self.assertTrue(rec["screenshot"])
        self.assertEqual(self.runs.screenshot(pb.id, rec["run_id"])[:2], b"\xff\xd8")
        self.assertNotEqual(await self.title(), "SENT", "it sent before anyone said yes")
        self.assertFalse(agent_ui._ctl.running, "a waiting run must not hold the browser")
        (job,) = self.scheds.get(s.id).state.parked
        note = self.notes.recent()[0]
        self.assertEqual(note["action"]["run_id"], rec["run_id"])
        self.assertEqual([m for m in self.ws.msgs if m["type"] == "approval"], [], "nobody is asked while away: it parks")

        self.assertEqual(await self.sched.answer(s.id, job.run_id, True), "")
        self.limits.configure(gap={"min": 10, "jitter": 0})
        self.limits._data["next_ok"] = {}
        await self.sched.tick()
        await until(lambda: not agent_ui._ctl.running, what="the answered run")
        again = self.runs.get(pb.id, rec["run_id"])
        self.assertEqual((again["status"], again["answered"]), ("done", ["s3"]))
        self.assertEqual(again["steps"][2]["approval"], "asked")
        self.assertEqual(again["steps"][3]["approval"], "preapproved")
        self.assertEqual(await self.title(), "SENT")
        self.assertEqual(len(self.runs.recent(pb.id)), 1)                          # one run in the history

    async def test_a_no_never_sends(self):
        pb, s, rec = await self.parked_send()
        (job,) = self.scheds.get(s.id).state.parked
        self.assertEqual(await self.sched.answer(s.id, job.run_id, False), "")
        await self.sched.tick()
        await asyncio.sleep(0.5)
        self.assertNotEqual(await self.title(), "SENT")
        self.assertEqual(self.runs.get(pb.id, rec["run_id"])["status"], "declined")

    async def test_silence_never_sends(self):
        pb, s, rec = await self.parked_send()
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"parked": [
            j.model_copy(update={"expires_at": sch.iso(datetime.now() - timedelta(minutes=1))}) for j in st.parked]}))
        await self.sched.tick()
        self.assertNotEqual(await self.title(), "SENT")
        self.assertEqual(self.runs.get(pb.id, rec["run_id"])["status"], "expired")

    async def typed_value(self):
        page = await self.session.get_current_page()
        return await page.evaluate("() => document.getElementById('q').value")

    async def asking_for_text(self):
        cands = await self.candidates()
        steps = [pbk.Step(id="s1", kind="open", url=self.url),
                 pbk.Step(id="s2", kind="input", message="What should be sent?", variable="msg", timeout_minutes=60),
                 pbk.Step(id="s3", kind="type", target=self.fingerprint(cands["q"]), text="{{msg}}"),
                 pbk.Step(id="s4", kind="approve", message="Send this message?", timeout_minutes=60),
                 pbk.Step(id="s5", kind="click", target=self.fingerprint(cands["send"]), ask=True)]
        pb = self.pbs.save(pbk.refresh(pbk.Playbook(name="Ask then send", variables=[pbk.Variable(name="msg", runtime=True)],
                                                    steps=steps)), approve=True)
        s = self.schedule(pb)
        await self.session.navigate_to("about:blank")
        return pb, s, await self.finished_run(pb)

    async def next_turn(self):
        self.limits._data["next_ok"] = {}
        await self.sched.tick()
        await until(lambda: not agent_ui._ctl.running, what="the next run")

    async def test_a_value_typed_in_by_the_user_is_used_then_the_send_still_waits_for_its_own_yes(self):
        pb, s, rec = await self.asking_for_text()
        self.assertEqual((rec["status"], rec["waiting"]["kind"], rec["waiting"]["variable"]), ("waiting", "input", "msg"))
        (job,) = self.scheds.get(s.id).state.parked
        self.assertEqual(await self.sched.answer(s.id, job.run_id, True), "Type the answer first.")
        self.assertEqual(await self.sched.answer(s.id, job.run_id, True, "hello from me"), "")
        await self.next_turn()
        again = self.runs.get(pb.id, rec["run_id"])
        self.assertEqual((again["status"], again["waiting"]["kind"]), ("waiting", "approve"))     # now the send's own question
        self.assertEqual(await self.typed_value(), "hello from me")
        self.assertNotEqual(await self.title(), "SENT", "typing a value is not a yes to the send")
        self.assertNotIn("hello from me", str(again), "the typed value must not be kept in the run record")
        (job,) = self.scheds.get(s.id).state.parked
        self.assertEqual(job.typed, ["s2"])                                    # carried to the next question
        await self.sched.answer(s.id, job.run_id, True)
        await self.next_turn()
        self.assertEqual(await self.title(), "SENT")
        self.assertEqual(self.runs.get(pb.id, rec["run_id"])["status"], "done")
        self.assertEqual(self.scheds.get(s.id).state.parked, [])

    async def test_no_value_means_nothing_is_typed_or_sent(self):
        pb, s, rec = await self.asking_for_text()
        (job,) = self.scheds.get(s.id).state.parked
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"parked": [
            j.model_copy(update={"expires_at": sch.iso(datetime.now() - timedelta(minutes=1))}) for j in st.parked]}))
        await self.sched.tick()
        self.assertEqual(self.runs.get(pb.id, rec["run_id"])["status"], "expired")
        self.assertNotEqual(await self.title(), "SENT")
        self.assertEqual(await self.typed_value(), "")

    async def test_silence_on_a_step_set_to_skip_leaves_the_send_out_and_finishes_the_rest(self):
        cands = await self.candidates()
        steps = [pbk.Step(id="s1", kind="open", url=self.url),
                 pbk.Step(id="s2", kind="type", target=self.fingerprint(cands["q"]), text="hello there"),
                 pbk.Step(id="s3", kind="approve", message="Send this message?", timeout_minutes=60, on_timeout="skip"),
                 pbk.Step(id="s4", kind="click", target=self.fingerprint(cands["send"]), ask=True),
                 pbk.Step(id="s5", kind="wait", seconds=1)]
        pb = self.pbs.save(pbk.refresh(pbk.Playbook(name="Skippable send", steps=steps)), approve=True)
        s = self.schedule(pb)
        await self.session.navigate_to("about:blank")
        rec = await self.finished_run(pb)
        self.assertEqual((rec["status"], rec["waiting"]["on_timeout"], rec["waiting"]["guarded"]), ("waiting", "skip", "s4"))
        self.scheds.change_state(s.id, lambda st: st.model_copy(update={"parked": [
            j.model_copy(update={"expires_at": sch.iso(datetime.now() - timedelta(minutes=1))}) for j in st.parked]}))
        await self.sched.tick()                                                  # silence: moved on, not cancelled
        await self.next_turn()
        end = self.runs.get(pb.id, rec["run_id"])
        self.assertEqual(end["status"], "done", end)
        self.assertNotEqual(await self.title(), "SENT", "a skipped send was sent")
        self.assertEqual(await self.typed_value(), "hello there")                # the steps before it did run
        self.assertEqual(end["steps"][4]["status"], "ok")                        # and the one after it
        self.assertTrue(any(n["title"].startswith("Skipped one step") for n in self.notes.recent()))

    async def test_a_yes_does_not_excuse_an_unsure_match(self):
        pb, s, rec = await self.parked_send(certain=False)
        (job,) = self.scheds.get(s.id).state.parked
        await self.sched.answer(s.id, job.run_id, True)
        self.limits._data["next_ok"] = {}
        await self.sched.tick()
        await until(lambda: not agent_ui._ctl.running, what="the answered run")
        self.assertNotEqual(await self.title(), "SENT", "pressed Send on an element it was not sure about")
        self.assertEqual(self.runs.get(pb.id, rec["run_id"])["status"], "needs_you")

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
