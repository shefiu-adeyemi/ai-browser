"""
Drafting a playbook from words in a REAL Chromium, with the real player doing the walk. A stand-in model writes the plans (a real
model is not needed to test everything around it), and the drafts it produces are then saved and REPLAYED.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_draft_browser -v
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import os
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.playbooks import draft as pd
from aiba.playbooks import suggest as ps
from test_lifecycle_browser import until
from test_scrape_browser import PAGES, ScrapeBase

EXTRA = {
    "/clicky": (b"<html><title>start</title><body><button id='more' onclick=\"document.title='MORE'\">Show more</button>"
                b"<a id='ppl' href='/people'>People list</a><button id='buy' onclick=\"document.title='BOUGHT'\">Buy now</button></body></html>"),
    "/form": (b"<html><title>form</title><body><input id='q' placeholder='Search the shop'>"
              b"<button id='go' onclick=\"document.title='GO:'+document.getElementById('q').value\">Go</button></body></html>"),
}


class Planner:
    def __init__(self, plan):
        self.plan, self.calls = plan, 0
        self.tokens, self.model = 0, "stand-in"

    async def ainvoke(self, messages, output_format=None, **kw):
        self.calls += 1
        self.tokens += 250
        if output_format is pd.DraftPlan:
            return NS(completion=self.plan)
        return NS(completion=self.selectors(messages))

    def selectors(self, messages):
        text = messages[1].content
        if "Columns: Name, Price" in text or "price" in text.lower():
            return ps.Suggestion(row_selector="#app > div.card", fields=[ps.SuggestedField(name="Name", selector=".title"),
                                                                   ps.SuggestedField(name="Price", selector=".price", type="number")])
        return ps.Suggestion(row_selector="tr.p", fields=[ps.SuggestedField(name="Name", selector=".n"),
                                                           ps.SuggestedField(name="Page", selector="a", type="link")])


def S(kind, **kw):
    return pd.PlanStep(kind=kind, **kw)


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class DraftingInABrowser(ScrapeBase):
    async def asyncSetUp(self):
        self._old = dict(PAGES)
        PAGES.update(EXTRA)
        await super().asyncSetUp()
        self.addCleanup(lambda: (PAGES.clear(), PAGES.update(self._old)))

    async def draft(self, plan, description=None, **kw):
        session = await agent_ui._ensure_session()
        player = await agent_ui._build_player(pbk.Playbook(name="Drafting", steps=[]), attended=False, trim=False)
        planner = Planner(plan)
        description = description or f"go to {self.base()} and do the thing"
        out = await pd.draft(description, llm=planner, player=player, page=session.get_current_page, select_llm=planner,
                             url=await session.get_current_page_url(), **kw)
        return out, planner

    async def title(self):
        page = await self.session.get_current_page()
        return await page.evaluate("() => document.title")

    async def replay(self, pb, inputs=None):
        saved = self.pbs.save(pb)
        r = await agent_ui.run_playbook(saved.id, {"inputs": inputs or {}})
        self.assertTrue(r["ok"], r)
        await until(lambda: not agent_ui._ctl.running, what="the replay")
        return self.runs.get(saved.id, r["run_id"])

    async def test_a_page_and_a_table_become_a_playbook_that_replays_to_the_same_rows(self):
        plan = pd.DraftPlan(name="Shop prices", description="Reads the shop", steps=[
            S("open", url=self.base() + "/cards", note="Open the shop"), S("read_table", table="each product with its name and price", columns=["Name", "Price"])])
        out, planner = await self.draft(plan)
        self.assertEqual([r["status"] for r in out.report], ["verified", "verified"])
        sample = out.report[1]["sample"]
        self.assertEqual((sample["columns"], sample["rows"][0]), (["Name", "Price"], ["Alpha", "5"]))
        self.assertEqual(sample["total"], 4)
        self.assertEqual(pbk.validate(out.playbook), [])
        self.assertIsNone(out.playbook.approval)
        self.assertEqual(out.tokens, 500)                                         # 250 to plan, 250 to find the table
        rec = await self.replay(out.playbook)
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(rec["outputs"][0]["table"]["rows"], [["Alpha", "5"], ["Beta", "7.5"], ["Gamma", "9"], ["Delta", "1.25"]])

    async def test_a_link_is_found_by_its_words_followed_and_the_next_page_is_read(self):
        plan = pd.DraftPlan(name="People", steps=[
            S("open", url=self.base() + "/clicky"), S("click", label="People list", element="link", note="Go to the list"),
            S("read_table", table="each person and their page link", columns=["Name", "Page"])])
        out, _ = await self.draft(plan)
        self.assertEqual([r["status"] for r in out.report], ["verified"] * 3)
        self.assertEqual(out.playbook.steps[1].target.attributes.get("id"), "ppl")
        self.assertEqual(out.report[2]["sample"]["rows"][0][0], "Ann")
        rec = await self.replay(out.playbook)
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(len(rec["outputs"][0]["table"]["rows"]), 4)

    async def test_something_that_buys_is_found_marked_and_never_clicked(self):
        plan = pd.DraftPlan(name="Buy", steps=[S("open", url=self.base() + "/clicky"), S("click", label="Buy now", element="button"), S("wait", seconds=1)])
        out, _ = await self.draft(plan)
        self.assertEqual([r["status"] for r in out.report], ["verified", "not_run", "left_out"])
        self.assertTrue(out.playbook.steps[1].ask)
        self.assertEqual(await self.title(), "start", "the draft pressed Buy")

    async def test_a_harmless_button_is_really_pressed_while_drafting(self):
        plan = pd.DraftPlan(name="More", steps=[S("open", url=self.base() + "/clicky"), S("click", label="Show more", element="button")])
        out, _ = await self.draft(plan)
        self.assertEqual(out.report[1]["status"], "verified")
        self.assertEqual(await self.title(), "MORE")
        self.assertFalse(out.playbook.steps[1].ask)

    async def test_typing_uses_the_example_and_the_saved_draft_takes_a_new_value(self):
        v = pd.PlanVariable(name="term", label="Search for", example="red shoes")
        plan = pd.DraftPlan(name="Search", variables=[v], steps=[
            S("open", url=self.base() + "/form"), S("type", label="Search the shop", element="field", text="{{term}}"),
            S("click", label="Go", element="button")])
        out, _ = await self.draft(plan)
        self.assertEqual([r["status"] for r in out.report], ["verified"] * 3)
        self.assertEqual(await self.title(), "GO:red shoes")                           # the walk really did it
        self.assertEqual(out.playbook.variables[0].default, "red shoes")
        rec = await self.replay(out.playbook, {"term": "blue socks"})
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(await self.title(), "GO:blue socks")

    async def test_a_site_the_user_did_not_name_is_never_visited(self):
        plan = pd.DraftPlan(name="X", steps=[S("open", url=self.base() + "/cards"), S("open", url="http://evil.test/steal")])
        out, _ = await self.draft(plan)
        self.assertEqual(out.report[1]["status"], "unresolved")
        page = await self.session.get_current_page()
        self.assertNotIn("evil.test", await page.evaluate("() => location.href"))

    async def test_a_button_that_is_not_on_the_page_says_what_is(self):
        plan = pd.DraftPlan(name="X", steps=[S("open", url=self.base() + "/clicky"), S("click", label="Checkout", element="button")])
        out, _ = await self.draft(plan)
        self.assertEqual(out.report[1]["status"], "unresolved")
        self.assertIn("Couldn't find", out.report[1]["detail"])
        self.assertEqual(len(out.playbook.steps), 1)

    async def test_the_draft_does_not_disturb_the_users_other_tabs(self):
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.base() + "/cards")
        before = len((await session.get_browser_state_summary(include_screenshot=False)).tabs)
        plan = pd.DraftPlan(name="X", steps=[S("open", url=self.base() + "/clicky")])
        await self.draft(plan)
        after = len((await session.get_browser_state_summary(include_screenshot=False)).tabs)
        self.assertEqual(before, after)
