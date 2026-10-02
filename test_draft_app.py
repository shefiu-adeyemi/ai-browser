"""The drafting job through the app: starting it, polling it, stopping it, and what it is wired to."""

import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import time
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import agent_ui
import playbook as pbk
import playbook_draft as pd


class Result:
    def __init__(self, tokens=120):
        self.tokens = tokens

    def to_dict(self):
        return {"playbook": {"name": "Drafted"}, "report": [{"n": 1, "kind": "open", "text": "Open x", "status": "verified", "detail": ""}],
                "notes": [], "tokens": self.tokens, "model": "m", "counts": {"verified": 1}}


class Drafting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        agent_ui._drafts.clear()
        agent_ui._ctl.running = agent_ui._ctl.paused = False
        self.calls = []
        self.behaviour = None                                     # an exception to raise, or an awaitable factory
        self.usable = True
        outer = self

        class Session:
            async def get_current_page(s): return "PAGE"
            async def get_current_page_url(s): return "https://shop.test/list"
        async def ensure(): return Session()

        class Player:
            _notify = "notify"
        async def build(pb, attended, trim=True):
            outer.calls.append(("build", attended, trim))
            return Player()

        async def fake_draft(description, **kw):
            outer.calls.append(("draft", description, kw))
            await kw["say"]("Planning the steps…")
            if isinstance(outer.behaviour, BaseException):
                raise outer.behaviour
            if callable(outer.behaviour):
                await outer.behaviour()
            return Result()

        async def digest(page): return "DIGEST"
        for obj, name, value in ((agent_ui, "_ensure_session", ensure), (agent_ui, "_build_player", build),
                                 (agent_ui.playbook_draft, "draft", fake_draft), (agent_ui.playbook_suggest, "digest_page", digest),
                                 (agent_ui._router, "escalating", lambda purpose, **kw: f"LLM:{purpose}"),
                                 (agent_ui.model_tiers, "read_slots", lambda *a, **k: {"standard": NS(usable=outer.usable)})):
            p = mock.patch.object(obj, name, value)
            p.start(); self.addCleanup(p.stop)
        self.addCleanup(lambda: setattr(agent_ui._ctl, "running", False))

    async def wait(self, token, until="running", limit=3.0):
        end = time.time() + limit
        while time.time() < end:
            st = agent_ui._drafts[token]["status"]
            if st != until:
                return st
            await asyncio.sleep(0.01)
        self.fail("the draft did not finish")

    async def start(self, **body):
        body.setdefault("description", "open the list and read it")
        r = await agent_ui.describe_playbook(body)
        return r

    def body(self, resp):
        import json
        return json.loads(resp.body) if hasattr(resp, "body") else resp

    async def test_a_draft_runs_and_its_result_can_be_collected(self):
        r = await self.start()
        self.assertTrue(r["ok"])
        token = r["token"]
        self.assertEqual(await self.wait(token), "done")
        got = await agent_ui.describe_status(token)
        self.assertEqual((got["ok"], got["status"], got["result"]["tokens"]), (True, "done", 120))
        self.assertEqual(got["progress"], ["Planning the steps…"])

    async def test_it_is_wired_to_the_right_models_and_never_trims_the_users_tabs(self):
        r = await self.start(use_page=True, allow_unnamed=True)
        await self.wait(r["token"])
        build = next(c for c in self.calls if c[0] == "build")
        self.assertEqual(build[1:], (False, False))                                  # unattended, and the tabs are left alone
        kw = next(c for c in self.calls if c[0] == "draft")[2]
        self.assertEqual((kw["llm"], kw["select_llm"]), ("LLM:draft", "LLM:selectors"))
        self.assertEqual(kw["choose"].llm, "LLM:repair")
        self.assertEqual((kw["digest"], kw["url"], kw["allow_unnamed"]), ("DIGEST", "https://shop.test/list", True))

    async def test_without_asking_for_the_page_it_is_not_read_and_unnamed_sites_are_not_allowed(self):
        r = await self.start()
        await self.wait(r["token"])
        kw = next(c for c in self.calls if c[0] == "draft")[2]
        self.assertEqual((kw["digest"], kw["url"], kw["allow_unnamed"]), ("", "", False))

    async def test_only_a_real_true_switches_those_on(self):
        r = await self.start(use_page="yes", allow_unnamed=1)
        await self.wait(r["token"])
        kw = next(c for c in self.calls if c[0] == "draft")[2]
        self.assertEqual((kw["digest"], kw["allow_unnamed"]), ("", False))

    async def test_a_failure_is_reported_in_words(self):
        self.behaviour = pd.DraftError("The model's plan had no steps that could be used.")
        r = await self.start()
        self.assertEqual(await self.wait(r["token"]), "failed")
        got = await agent_ui.describe_status(r["token"])
        self.assertIn("no steps that could be used", got["error"])
        self.assertIsNone(got["result"])
        self.assertFalse(agent_ui._ctl.running)                                      # the browser is free again

    async def test_an_unexpected_crash_is_a_plain_failure_not_a_stuck_job(self):
        self.behaviour = RuntimeError("the browser went away")
        r = await self.start()
        self.assertEqual(await self.wait(r["token"]), "failed")
        self.assertIn("RuntimeError", agent_ui._drafts[r["token"]]["error"])
        self.assertFalse(agent_ui._ctl.running)

    async def test_stop_ends_it_and_says_it_was_stopped(self):
        async def forever():
            await asyncio.sleep(30)
        self.behaviour = forever
        r = await self.start()
        await asyncio.sleep(0.05)
        await agent_ui._ctl.stop()
        self.assertEqual(agent_ui._drafts[r["token"]]["status"], "stopped")
        self.assertFalse(agent_ui._ctl.running)

    async def test_it_waits_when_the_browser_is_busy(self):
        agent_ui._ctl.running = True
        r = await self.start()
        body = self.body(r)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Wait for it to finish", body["reason"])
        self.assertEqual(agent_ui._drafts, {})

    async def test_two_at_once_are_not_started(self):
        async def slow():
            await asyncio.sleep(0.3)
        self.behaviour = slow
        first = await self.start()
        second = await self.start()
        self.assertTrue(first["ok"])
        self.assertEqual(second.status_code, 409)
        await agent_ui._ctl.stop()

    async def test_no_model_means_no_draft(self):
        self.usable = False
        r = await self.start()
        self.assertEqual(r.status_code, 409)
        self.assertIn("No AI model", self.body(r)["reason"])
        self.assertFalse(self.calls)

    async def test_a_model_setup_that_breaks_is_the_same_clear_message(self):
        with mock.patch.object(agent_ui.model_tiers, "read_slots", side_effect=RuntimeError("bad")):
            r = await self.start()
        self.assertEqual(r.status_code, 409)

    async def test_bad_descriptions_are_refused_before_anything_starts(self):
        for body in ({"description": ""}, {"description": "   "}, {"description": 5}, {}, {"description": "x" * 1501}):
            r = await agent_ui.describe_playbook(body)
            self.assertEqual(r.status_code, 400, body)
        self.assertFalse(self.calls)
        self.assertEqual(agent_ui._drafts, {})

    async def test_unknown_or_malformed_tokens_are_not_found(self):
        for token in ("nope", "../x", "0" * 16, "G" * 16, ""):
            r = await agent_ui.describe_status(token)
            self.assertEqual(r.status_code, 404, token)

    async def test_only_the_last_few_drafts_are_kept(self):
        tokens = []
        for _ in range(agent_ui.MAX_DRAFT_JOBS + 3):
            r = await self.start()
            tokens.append(r["token"])
            await self.wait(r["token"])
            await asyncio.sleep(0.02)
        self.assertEqual(len(agent_ui._drafts), agent_ui.MAX_DRAFT_JOBS)
        self.assertIn(tokens[-1], agent_ui._drafts)
        self.assertNotIn(tokens[0], agent_ui._drafts)

    async def test_progress_is_kept_short_for_the_screen(self):
        async def chatty():
            pass
        async def many(description, **kw):
            for i in range(60):
                await kw["say"](f"line {i}")
            return Result()
        with mock.patch.object(agent_ui.playbook_draft, "draft", many):
            r = await self.start()
            await self.wait(r["token"])
        got = await agent_ui.describe_status(r["token"])
        self.assertEqual((len(got["progress"]), got["progress"][-1]), (30, "line 59"))

    async def test_the_endpoints_need_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.post("/playbooks/describe", json={"description": "x"}).status_code, (401, 403))
        self.assertIn(stranger.get("/playbooks/describe/" + "a" * 16).status_code, (401, 403))
        self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()
