"""
Self-repair in a REAL Chromium, through the real runner: a page that changes after the playbook was recorded.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_repair_browser -v
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import os
import re
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import agent_ui
import playbook as pbk
import playbook_repair as pr
import playbook_suggest as ps
from playbook import ScrapeField
from playbook_match import candidates_from_selector_map
from test_lifecycle_browser import until
from test_scrape_browser import PAGES, ScrapeBase

BEFORE = (b"<html><title>shop</title><body><button id='show' aria-label='Show results' onclick=\"document.title='SHOWN'\">Show results</button>"
          b"<button id='cancel' onclick=\"document.title='CANCELLED'\">Cancel</button>"
          b"<table><tr class='item'><td class='n'>Red shoes</td><td class='p'>$50</td></tr>"
          b"<tr class='item'><td class='n'>Blue socks</td><td class='p'>$7</td></tr></table></body></html>")
AFTER = (b"<html><title>shop</title><body><p>Notice: we moved things around</p><div class='bar'><button id='display' aria-label='Display results' "
         b"onclick=\"document.title='SHOWN'\">Display results</button></div>"
         b"<button id='cancel2' onclick=\"document.title='CANCELLED'\">Cancel</button>"
         b"<div class='cards'><div class='card'><b class='title'>Red shoes</b><i class='cost'>$50</i></div>"
         b"<div class='card'><b class='title'>Blue socks</b><i class='cost'>$7</i></div></div></body></html>")


class ModelThatReads:
    """A stand-in for the model: it reads the numbered list it is shown and picks what a sensible person would."""
    def __init__(self):
        self.calls = []

    async def ainvoke(self, messages, output_format=None, **kw):
        self.calls.append(output_format)
        text = messages[1].content
        if output_format is pr.Choice:
            m = re.search(r'^(\d+)\. <\w+> "Display results"', text, re.M)
            return NS(completion=pr.Choice(number=int(m.group(1)) if m else None, reason="renamed"))
        return NS(completion=ps.Suggestion(row_selector="div.card", fields=[
            ps.SuggestedField(name="Name", selector=".title"), ps.SuggestedField(name="Price", selector=".cost", type="number")]))


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class RepairingInABrowser(ScrapeBase):
    async def asyncSetUp(self):
        self._old = dict(PAGES)
        PAGES["/shop"] = BEFORE
        await super().asyncSetUp()
        self.model = ModelThatReads()
        p = mock.patch.object(agent_ui._router, "escalating", lambda purpose, **kw: self.model)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(agent_ui.model_tiers, "read_slots", lambda *a, **k: {"standard": NS(usable=True)})
        p.start(); self.addCleanup(p.stop)
        self.addCleanup(lambda: (PAGES.clear(), PAGES.update(self._old)))

    async def fingerprints(self):
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.base() + "/shop")
        await asyncio.sleep(1.5)
        state = await session.get_browser_state_summary(include_screenshot=False)
        out = {}
        for c in candidates_from_selector_map(state.dom_state.selector_map):
            out[c.attributes.get("id")] = pr.fingerprint_of(c)
        return out

    async def saved(self, repair):
        fps = await self.fingerprints()
        steps = [pbk.Step(id="s1", kind="open", url=self.base() + "/shop"),
                 pbk.Step(id="s2", kind="click", target=fps["show"], note="Show them"),
                 pbk.Step(id="s3", kind="scrape", row_selector="tr.item", note="Prices",
                          fields=[ScrapeField(name="Name", selector=".n"), ScrapeField(name="Price", selector=".p", type="number")])]
        return self.pbs.save(pbk.refresh(pbk.Playbook(name="Shop", steps=steps, repair=repair)), approve=True)

    async def replay(self, pb):
        r = await agent_ui.run_playbook(pb.id, {"inputs": {}})
        self.assertTrue(r["ok"], r)
        await until(lambda: not agent_ui._ctl.running, what="the run")
        return self.runs.get(pb.id, r["run_id"])

    async def title(self):
        page = await self.session.get_current_page()
        return await page.evaluate("() => document.title")

    async def test_an_unchanged_page_needs_no_repair_and_no_model(self):
        pb = await self.saved(repair=True)
        rec = await self.replay(pb)
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual((rec["repairs"], self.model.calls), ([], []))
        self.assertEqual(await self.title(), "SHOWN")

    async def test_a_page_that_changed_stops_the_run_when_repair_is_off(self):
        pb = await self.saved(repair=False)
        PAGES["/shop"] = AFTER
        rec = await self.replay(pb)
        self.assertEqual(rec["status"], "failed")
        self.assertIn("Couldn't find", " ".join(s["detail"] for s in rec["steps"]))
        self.assertEqual(self.model.calls, [])
        self.assertNotEqual(await self.title(), "SHOWN")

    async def test_a_renamed_button_and_a_restructured_table_are_repaired(self):
        pb = await self.saved(repair=True)
        PAGES["/shop"] = AFTER
        rec = await self.replay(pb)
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(await self.title(), "SHOWN")                                   # the renamed button was clicked
        kinds = sorted(r["kind"] for r in rec["repairs"])
        self.assertEqual(kinds, ["element", "scrape"])
        self.assertEqual(rec["outputs"][0]["table"]["rows"], [["Red shoes", "50"], ["Blue socks", "7"]])
        click = next(s for s in rec["steps"] if s["id"] == "s2")
        self.assertEqual(click["matched"], "repaired (medium)")
        element = next(r for r in rec["repairs"] if r["kind"] == "element")
        self.assertEqual(element["now"]["ax_name"], "Display results")

    async def test_the_playbook_is_not_changed_by_a_repair_until_the_user_says_so(self):
        pb = await self.saved(repair=True)
        PAGES["/shop"] = AFTER
        rec = await self.replay(pb)
        same = self.pbs.get(pb.id)
        self.assertEqual((same.version, same.steps[1].target.ax_name), (1, "Show results"))
        self.assertTrue(pbk.is_approved(same))

    async def test_applying_the_repairs_makes_a_version_that_works_without_the_model(self):
        pb = await self.saved(repair=True)
        PAGES["/shop"] = AFTER
        rec = await self.replay(pb)
        out = await agent_ui.apply_run_repairs(pb.id, rec["run_id"], {})
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["approved"])
        new = self.pbs.get(pb.id)
        self.assertEqual(new.version, 2)
        # approve it the way the user would, turn repair off, and replay on the changed page: no model, no repair
        again = self.pbs.save(new.model_copy(update={"repair": False}), approve=True)
        self.model.calls.clear()
        await self.session.navigate_to("about:blank")
        rec2 = await self.replay(again)
        self.assertEqual(rec2["status"], "done", rec2)
        self.assertEqual((rec2["repairs"], self.model.calls), ([], []))
        self.assertEqual(await self.title(), "SHOWN")

    async def test_the_cancel_button_is_never_picked_for_the_missing_send_button(self):
        class Careless(ModelThatReads):
            async def ainvoke(self, messages, output_format=None, **kw):
                if output_format is pr.Choice:
                    m = re.search(r'^(\d+)\. <\w+> "Cancel"', messages[1].content, re.M)
                    self.offered_cancel = bool(m)
                    return NS(completion=pr.Choice(number=int(m.group(1)) if m else None))
                return await super().ainvoke(messages, output_format, **kw)
        self.model = Careless()
        self.model.offered_cancel = False
        pb = await self.saved(repair=True)
        PAGES["/shop"] = AFTER
        rec = await self.replay(pb)
        self.assertFalse(self.model.offered_cancel, "an unrelated button was even offered")
        self.assertNotEqual(await self.title(), "CANCELLED")
        self.assertEqual(rec["status"], "failed")
