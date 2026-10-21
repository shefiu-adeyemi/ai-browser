"""
The whole playbook loop against a REAL Chromium: an AI run (scripted fake LLM, no API cost) is recorded,
turned into a playbook, saved, then REPLAYED with a different input and no model deciding anything.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_playbook_player_browser -v
"""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import http.server
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.playbooks.recorder import draft_from_history
from aiba.playbooks.player import PlaybookPlayer as _PP
from aiba.playbooks.runs import RunStore
from aiba.playbooks.store import PlaybookStore
from test_lifecycle_browser import LifecycleBase, until

PAGE = (b"<html><title>form</title><body><h1>Form</h1>"
        b"<input id='q' name='q' placeholder='Search text'>"
        b"<button id='go' onclick=\"document.title='GO:'+document.getElementById('q').value\">Go</button>"
        b"<a id='next' href='/next'>Next page</a>"
        b"<button id='send' aria-label='Send message' onclick=\"document.title='SENT'\">Send</button>"
        b"</body></html>")


class _FormPage(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(PAGE if self.path == "/" else b"<html><title>next</title><body>second page</body></html>")
    def log_message(self, *a): pass


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class RecordThenReplay(LifecycleBase):
    async def asyncSetUp(self):
        import test_lifecycle_browser as tlb
        orig = tlb._Page
        tlb._Page = _FormPage
        p = mock.patch.dict(os.environ, {"APPROVAL_MODE": "off"})       # recording: no human to approve "Send"
        p.start(); self.addCleanup(p.stop)
        try:
            await super().asyncSetUp()
        finally:
            tlb._Page = orig
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="aiba-pb-"))
        self.pbs, self.runs = PlaybookStore(tmp / "playbooks"), RunStore(tmp / "runs")
        for target, value in (("_playbooks", self.pbs), ("_runs", self.runs)):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)

    async def record_and_save(self, approve: bool) -> pbk.Playbook:
        # element numbers on this page in a fresh browser (the same as test_playbook_recorder_browser)
        await self.run_to_end([{"navigate": self.url}, {"input": ("q", "red shoes")}, {"click": "go"},
                               {"click": "send"}, {"done": "finished"}],
                              task=f"Open {self.url} and search for red shoes")
        draft = draft_from_history(agent_ui._last_run["history"], task=agent_ui._last_run["task"])
        pb = draft.playbook
        for x in draft.suggestions:
            if x["field"] == "text":
                pb = pbk.make_variable(pb, x["step_id"], x["field"], "term", "text")
        self.assertEqual([s.kind for s in pb.steps], ["open", "type", "click", "click"], draft.notes)
        self.assertTrue(pb.steps[3].ask)
        return self.pbs.save(pb, approve=approve)

    async def replay(self, pb: pbk.Playbook, term: str):
        self.ws.msgs.clear()
        r = await agent_ui.run_playbook(pb.id, {"inputs": {"term": term}})
        self.assertTrue(r["ok"], r)
        return r["run_id"]

    async def page_state(self):
        page = await self.session.get_current_page()
        return (await page.evaluate("() => document.title"), await page.evaluate("() => document.getElementById('q').value"))

    async def test_an_approved_playbook_replays_with_new_input_and_no_prompts(self):
        pb = await self.record_and_save(approve=True)
        # leave the page in a different state, so the replay has to do the work
        await self.session.navigate_to(self.url + "?fresh")
        n_llm = len(self.llms)
        run_id = await self.replay(pb, "blue socks")
        await until(lambda: not agent_ui._ctl.running, what="the playbook run to finish")
        title, value = await self.page_state()
        self.assertEqual((title, value), ("SENT", "blue socks"))
        self.assertEqual(len(self.llms), n_llm, "a playbook replay must not call a model")
        self.assertEqual([m for m in self.ws.msgs if m["type"] == "approval"], [], "pre-approved step asked anyway")
        rec = self.runs.get(pb.id, run_id)
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual([s["status"] for s in rec["steps"]], ["ok"] * 4)
        self.assertEqual(rec["steps"][3]["approval"], "preapproved")
        self.assertIn(rec["steps"][1]["matched"].split()[0], ("exact", "stable", "identity"))
        self.assertEqual(rec["inputs"], {"term": "blue socks"})
        self.assertEqual(self.ws.statuses()[-1], "done")
        result = [m for m in self.ws.msgs if m["type"] == "result"][-1]
        self.assertEqual(result["verdict"], "pass")
        self.assertIn("all 4 steps done", result["text"])
        self.assertIn("Run playbook", agent_ui._conversation_history[-2]["content"])

    async def test_an_unapproved_playbook_asks_before_send_and_a_no_stops_it(self):
        pb = await self.record_and_save(approve=False)
        await self.session.navigate_to(self.url + "?fresh")
        run_id = await self.replay(pb, "green hats")
        await until(lambda: any(m["type"] == "approval" for m in self.ws.msgs), what="the approval prompt")
        prompt = next(m for m in self.ws.msgs if m["type"] == "approval")
        self.assertIn("Send", prompt["title"])
        title, value = await self.page_state()
        self.assertEqual(value, "green hats")
        self.assertNotEqual(title, "SENT", "the send happened before the user answered")
        agent_ui._broker.resolve(prompt["id"], False)
        await until(lambda: not agent_ui._ctl.running, what="the run to stop")
        self.assertNotEqual((await self.page_state())[0], "SENT")
        rec = self.runs.get(pb.id, run_id)
        self.assertEqual(rec["status"], "needs_you", rec)
        self.assertEqual(self.ws.statuses()[-1], "error")

    async def test_stop_ends_a_run_and_records_it(self):
        pb = await self.record_and_save(approve=True)
        slow = pb.model_copy(update={"steps": pb.steps[:1] + [pbk.Step(id="s5", kind="wait", seconds=60)]})
        slow = self.pbs.save(slow.model_copy(update={"id": ""}))
        r = await agent_ui.run_playbook(slow.id, {"inputs": {"term": "x"}})
        self.assertTrue(r["ok"])
        await asyncio.sleep(2)
        self.assertTrue((await agent_ui.stop_task())["ok"])
        self.assertFalse(agent_ui._ctl.running)
        self.assertEqual(self.runs.get(slow.id, r["run_id"])["status"], "stopped")
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "(stopped by user)")

    async def test_a_missing_element_stops_the_run_with_a_reason(self):
        pb = await self.record_and_save(approve=True)
        steps = list(pb.steps)
        steps[2] = steps[2].model_copy(update={"target": steps[2].target.model_copy(update={
            "element_hash": 1, "stable_hash": 2, "x_path": "/nope", "ax_name": "Vanished",
            "attributes": {"id": "vanished"}})})
        broken = self.pbs.save(pb.model_copy(update={"steps": steps, "id": ""}))
        quick = lambda *a, **k: _PP(*a, find_timeout=1.0, **k)         # do not wait the full 10 s
        with mock.patch.object(agent_ui, "PlaybookPlayer", quick):
            r = await agent_ui.run_playbook(broken.id, {"inputs": {"term": "x"}})
            self.assertTrue(r["ok"], r)
            await until(lambda: not agent_ui._ctl.running, what="the run to stop")
        rec = self.runs.get(broken.id, r["run_id"])
        self.assertEqual(rec["status"], "failed", rec)
        self.assertIn("Couldn't find", rec["error"])
        self.assertEqual([s["status"] for s in rec["steps"]], ["ok", "ok", "failed"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------------
# Pages that are hard for a replay: an iframe, a shadow root, a link that opens a new tab, a page
# that keeps changing for a while after it loads.
# ---------------------------------------------------------------------------------------------
HARD = {
    "/": (b"<html><title>main</title><body><a id='nt' target='_blank' href='/b'>Open B in new tab</a>"
          b"<iframe id='fr' src='/frame' width=400 height=120></iframe><div id='host'></div>"
          b"<script>const r=document.getElementById('host').attachShadow({mode:'open'});"
          b"r.innerHTML='<button id=shadowbtn onclick=\"document.title=&quot;SHADOW&quot;\">In shadow</button>';</script>"
          b"</body></html>"),
    "/b": b"<html><title>page B</title><body><h1>B</h1></body></html>",
    "/frame": (b"<html><body><button id='inframe' onclick=\"parent.document.title='FRAME-CLICKED'\">In frame</button>"
               b"</body></html>"),
    "/slow": (b"<html><title>slow</title><body><ul id='l'></ul><script>let n=0;const t=setInterval(()=>{"
              b"const li=document.createElement('li');li.textContent='row'+n;document.getElementById('l').appendChild(li);"
              b"if(++n>=8){clearInterval(t);document.title='LOADED'}},250);</script></body></html>"),
}


class _HardPage(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(HARD.get(self.path.split("?")[0], b"<html><title>404</title></html>"))
    def log_message(self, *a): pass


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class HardPages(LifecycleBase):
    async def asyncSetUp(self):
        import test_lifecycle_browser as tlb
        orig = tlb._Page
        tlb._Page = _HardPage
        try:
            await super().asyncSetUp()
        finally:
            tlb._Page = orig
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="aiba-pb-"))
        self.pbs, self.runs = PlaybookStore(tmp / "playbooks"), RunStore(tmp / "runs")
        for target, value in (("_playbooks", self.pbs), ("_runs", self.runs)):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)

    async def fingerprints(self, path="/"):
        """Fingerprints of the elements on the page, keyed by id, made the way the recorder makes them."""
        from aiba.playbooks.match import candidates_from_selector_map
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.url.rstrip("/") + path)
        await asyncio.sleep(1.5)
        state = await session.get_browser_state_summary(include_screenshot=False)
        out = {}
        for c in candidates_from_selector_map(state.dom_state.selector_map):
            out[c.attributes.get("id")] = pbk.Fingerprint(
                node_name=c.node_name, attributes={k: v for k, v in c.attributes.items() if k in ("id", "name")},
                ax_name=c.ax_name, x_path=c.x_path, element_hash=c.element_hash, stable_hash=c.stable_hash)
        return out

    async def run_saved(self, pb, inputs=None):
        saved = self.pbs.save(pb, approve=True)
        r = await agent_ui.run_playbook(saved.id, {"inputs": inputs or {}})
        self.assertTrue(r["ok"], r)
        await until(lambda: not agent_ui._ctl.running, what="the run to finish")
        return self.runs.get(saved.id, r["run_id"])

    async def test_iframe_shadow_dom_and_new_tab_are_replayed(self):
        fps = await self.fingerprints()
        S = pbk.Step
        pb = pbk.refresh(pbk.Playbook(name="hard page", steps=[
            S(id="s1", kind="open", url=self.url),
            S(id="s2", kind="click", target=fps["inframe"]),
            S(id="s3", kind="click", target=fps["shadowbtn"]),
            S(id="s4", kind="click", target=fps["nt"]),            # opens page B in a new tab and switches to it
            S(id="s5", kind="tab", tab_index=0),                   # back to the first tab
            S(id="s6", kind="tab", tab_index=1, close_tab=True),   # close the second one
        ]))
        await agent_ui._ensure_session()
        await self.session.navigate_to("about:blank")
        rec = await self.run_saved(pb)
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual([s["status"] for s in rec["steps"]], ["ok"] * 6)
        page = await self.session.get_current_page()
        self.assertEqual(await page.evaluate("() => document.title"), "SHADOW")        # the later click won
        state = await self.session.get_browser_state_summary(include_screenshot=False)
        self.assertEqual(len(state.tabs), 1, [t.url for t in state.tabs])
        self.assertTrue(rec["steps"][1]["matched"].endswith("(high)"), rec["steps"][1])

    async def test_a_handoff_waits_for_the_user_in_the_real_ui_flow(self):
        pb = pbk.refresh(pbk.Playbook(name="needs a human", steps=[
            pbk.Step(id="s1", kind="open", url=self.url),
            pbk.Step(id="s2", kind="handoff", message="Please tick the box"),
            pbk.Step(id="s3", kind="wait", seconds=1)]))
        saved = self.pbs.save(pb, approve=True)
        r = await agent_ui.run_playbook(saved.id, {"inputs": {}})
        await until(lambda: any(m["type"] == "approval" for m in self.ws.msgs), what="the hand-off banner")
        prompt = next(m for m in self.ws.msgs if m["type"] == "approval")
        self.assertEqual((prompt["kind"], prompt["detail"]), ("handoff", "Please tick the box"))
        self.assertTrue(agent_ui._ctl.running)
        agent_ui._broker.resolve(prompt["id"], True)
        await until(lambda: not agent_ui._ctl.running, what="the run to finish")
        self.assertEqual(self.runs.get(saved.id, r["run_id"])["status"], "done")

    async def test_settle_waits_for_a_page_that_keeps_changing(self):
        from aiba.playbooks.player import SessionDriver
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.url.rstrip("/") + "/slow")
        driver = SessionDriver(session, tools=None)
        t0 = asyncio.get_running_loop().time()
        await driver.settle(max_wait=10)
        waited = asyncio.get_running_loop().time() - t0
        page = await session.get_current_page()
        rows = await page.evaluate("() => document.getElementsByTagName('li').length")
        self.assertEqual(int(rows), 8, "settle returned while the page was still changing")
        self.assertLess(waited, 6)

    async def test_settle_gives_up_at_its_limit_and_survives_a_closed_page(self):
        from aiba.playbooks.player import SessionDriver
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.url.rstrip("/") + "/slow")
        driver = SessionDriver(session, tools=None)
        t0 = asyncio.get_running_loop().time()
        await driver.settle(max_wait=0.5)
        self.assertLess(asyncio.get_running_loop().time() - t0, 3)
