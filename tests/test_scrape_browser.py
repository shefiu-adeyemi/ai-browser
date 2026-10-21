"""
Reading a table off real pages in a REAL Chromium, through the real runner. No model is involved.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_scrape_browser -v
"""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import http.server
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.playbooks.model import ScrapeField
from aiba.playbooks.runs import RunStore
from aiba.playbooks.store import PlaybookStore
from test_lifecycle_browser import LifecycleBase, until

PAGES = {
    "/": (b"<html><title>shop</title><body><h1>Shop</h1><table id='t'>"
          b"<tr class='item' data-id='a1'><td class='n'>  Red   shoes </td><td class='p'>$1,299.50</td><td><a class='l' href='/p/1'>view</a></td></tr>"
          b"<tr class='item' data-id='b2'><td class='n'>Blue socks</td><td class='p'>about 7 dollars</td><td><a class='l' href='javascript:alert(1)'>view</a></td></tr>"
          b"<tr class='item' data-id='c3'><td class='n'>=HYPERLINK(\"http://evil\")</td><td class='p'>n/a</td></tr>"
          b"</table><div id='one'><span class='who'>Jane Doe</span><span class='role'>Engineer</span></div></body></html>"),
    "/slow": (b"<html><title>slow</title><body><ul id='l'></ul><script>setTimeout(()=>{for(let i=0;i<3;i++){"
              b"const li=document.createElement('li');li.className='r';li.textContent='row'+i;document.getElementById('l').appendChild(li)}},1500);"
              b"</script></body></html>"),
    "/changed": b"<html><title>changed</title><body><div class='redesign'>new layout</div></body></html>",
    "/cards": (b"<html><title>cards</title><body><div class='css-1a2b3c list' id='app'>"
               b"<div class='card k9' data-i='1'><h3 class='title'>Alpha</h3><span class='price'>$5.00</span><a class='go' href='/a'>go</a></div>"
               b"<div class='card k9' data-i='2'><h3 class='title'>Beta</h3><span class='price'>$7.50</span><a class='go' href='/b'>go</a></div>"
               b"<div class='card k9' data-i='3'><h3 class='title'>Gamma</h3><span class='price'>$9.00</span><a class='go' href='/c'>go</a></div>"
               b"<div class='card k9' data-i='4'><h3 class='title'>Delta</h3><span class='price'>$1.25</span></div></div>"
               b"<div class='sidebar'><div class='card k9 promo'><h3 class='title'>Ad</h3></div></div>"
               b"<button id='b' onclick=\"document.title='CLICKED'\">Buy</button><p id='out'>outside</p></body></html>"),
    "/secrets": (b"<html><title>s</title><head><style>.x{color:red}</style></head><body><form><input type='password' name='pw' value='hunter2'>"
                 b"<input name='q' value='my private search'><script>var secret='TOPSECRET'</script>"
                 b"<a href='/profile?token=SECRET123#frag'>me</a><img src='data:image/png;base64,AAAA' alt='logo'><svg><text>SVGTEXT</text></svg><p>Visible text</p></form></body></html>"),
    "/deep": (b"<html><title>deep</title><body><div id='host'></div><iframe id='fr' src='/frame2' width=500 height=160></iframe>"
              b"<div id='outer'></div><script>"
              b"const r=document.getElementById('host').attachShadow({mode:'open'});"
              b"r.innerHTML='<ul><li class=it><b class=n>One</b><i class=p>$1.50</i></li><li class=it><b class=n>Two</b><i class=p>$2</i></li></ul>';"
              b"const o=document.getElementById('outer').attachShadow({mode:'open'});"
              b"o.innerHTML='<div class=card><span class=n>Nested</span><div id=in></div></div>';"
              b"const inner=o.getElementById('in').attachShadow({mode:'open'});inner.innerHTML='<em class=deep>Deep value</em>';"
              b"</script></body></html>"),
    "/frame2": (b"<html><body><div class='row'><span class='n'>FrameA</span><span class='p'>3</span></div>"
                b"<div class='row'><span class='n'>FrameB</span><span class='p'>4</span></div></body></html>"),
    "/people": (b"<html><title>people</title><body><table>"
                b"<tr class='p'><td class='n'>Ann</td><td><a href='/person/1'>profile</a></td></tr>"
                b"<tr class='p'><td class='n'>Bob</td><td><a href='/person/2'>profile</a></td></tr>"
                b"<tr class='p'><td class='n'>Cy</td><td><a href='/person/3'>profile</a></td></tr>"
                b"<tr class='p'><td class='n'>Eve</td><td><a href='http://evil.test/steal'>profile</a></td></tr>"
                b"</table></body></html>"),
    "/person/1": b"<html><title>p1</title><body><h1 class='who'>Ann Lee</h1><p class='role'>Engineer</p></body></html>",
    "/person/2": b"<html><title>p2</title><body><h1 class='who'>Bob Ray</h1><p class='role'>Designer</p></body></html>",
    "/person/3": b"<html><title>p3</title><body><h1 class='who'>Cy Dunn</h1></body></html>",
    "/many": (b"<html><title>many</title><body><ul>" + b"".join(b"<li class='r'>item%d</li>" % i for i in range(30)) + b"</ul></body></html>"),
}


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(PAGES.get(self.path.split("?")[0], b"<html><title>404</title></html>"))
    def log_message(self, *a): pass


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class ScrapeBase(LifecycleBase):
    """The browser, the fixture pages and a helper to run a scrape playbook (no tests of its own)."""
    async def asyncSetUp(self):
        import test_lifecycle_browser as tlb
        orig = tlb._Page
        tlb._Page = _Pages
        try:
            await super().asyncSetUp()
        finally:
            tlb._Page = orig
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="aiba-scrape-"))
        self.pbs, self.runs = PlaybookStore(tmp / "playbooks"), RunStore(tmp / "runs")
        for target, value in (("_playbooks", self.pbs), ("_runs", self.runs)):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)

    def base(self):
        return self.url.rstrip("/")

    async def run_pb(self, path, **step):
        fields = step.pop("fields", [ScrapeField(name="Name", selector=".n"), ScrapeField(name="Price", selector=".p", type="number")])
        steps = [pbk.Step(id="s1", kind="open", url=self.base() + path),
                 pbk.Step(id="s2", kind="scrape", fields=fields, note="Read it", **step)]
        saved = self.pbs.save(pbk.refresh(pbk.Playbook(name="Scrape", steps=steps)), approve=True)
        n_llm = len(self.llms)
        r = await agent_ui.run_playbook(saved.id, {"inputs": {}})
        self.assertTrue(r["ok"], r)
        await until(lambda: not agent_ui._ctl.running, what="the run to finish")
        self.assertEqual(len(self.llms), n_llm, "reading a table must not call a model")
        return saved, self.runs.get(saved.id, r["run_id"])

@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class ScrapeInBrowser(ScrapeBase):
    async def test_a_table_is_read_cleaned_and_kept_with_the_run(self):
        saved, rec = await self.run_pb("/", row_selector="tr.item")
        self.assertEqual(rec["status"], "done", rec)
        (out,) = rec["outputs"]
        self.assertEqual(out["table"]["columns"], ["Name", "Price"])
        self.assertEqual(out["table"]["rows"], [["Red shoes", "1299.5"], ["Blue socks", "7"], ['=HYPERLINK("http://evil")', ""]])
        self.assertEqual(out["text"], "3 rows read")
        self.assertNotIn("table", rec["steps"][1])

    async def test_links_attributes_and_the_row_itself_are_read(self):
        fields = [ScrapeField(name="Id", selector=":scope", type="attr", attr="data-id"),
                  ScrapeField(name="Link", selector=".l", type="link"),
                  ScrapeField(name="Name", selector=".n")]
        saved, rec = await self.run_pb("/", row_selector="tr.item", fields=fields)
        rows = rec["outputs"][0]["table"]["rows"]
        self.assertEqual([r[0] for r in rows], ["a1", "b2", "c3"])
        self.assertEqual(rows[0][1], self.base() + "/p/1")                       # made absolute by the page
        self.assertEqual(rows[1][1], "", "a javascript: link must never be kept")
        self.assertEqual(rows[2][1], "")                                          # no link in that row at all

    async def test_one_record_without_a_row_selector(self):
        fields = [ScrapeField(name="Who", selector=".who"), ScrapeField(name="Role", selector=".role")]
        saved, rec = await self.run_pb("/", fields=fields)
        self.assertEqual(rec["outputs"][0]["table"]["rows"], [["Jane Doe", "Engineer"]])

    async def test_a_page_that_fills_in_later_is_waited_for(self):
        saved, rec = await self.run_pb("/slow", row_selector="li.r", fields=[ScrapeField(name="Text", selector=":scope")])
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual([r[0] for r in rec["outputs"][0]["table"]["rows"]], ["row0", "row1", "row2"])

    async def test_a_changed_page_fails_loudly_and_reads_nothing(self):
        saved, rec = await self.run_pb("/changed", row_selector="tr.item")
        self.assertEqual(rec["status"], "failed")
        self.assertIn("No rows were found", rec["error"] + " ".join(s["detail"] for s in rec["steps"]))
        self.assertEqual(rec["outputs"], [])

    async def test_a_bad_selector_fails_with_a_plain_message(self):
        saved, rec = await self.run_pb("/", row_selector="tr[")
        self.assertEqual(rec["status"], "failed")
        self.assertIn("not valid", " ".join(s["detail"] for s in rec["steps"]))

    async def test_a_long_page_is_cut_at_the_limit_and_says_so(self):
        saved, rec = await self.run_pb("/many", row_selector="li.r", max_rows=10, fields=[ScrapeField(name="Text", selector=":scope")])
        table = rec["outputs"][0]["table"]
        self.assertEqual((len(table["rows"]), table["total"], table["truncated"]), (10, 30, True))
        self.assertIn("the page has 30", rec["outputs"][0]["text"])

    async def test_the_page_is_not_changed_by_reading_it(self):
        await self.run_pb("/", row_selector="tr.item")
        page = await self.session.get_current_page()
        self.assertEqual(await page.evaluate("() => document.title"), "shop")
        self.assertEqual(await page.evaluate("() => document.querySelectorAll('tr.item').length"), "3")

    async def test_rows_inside_a_shadow_root_are_read_with_the_arrow_selector(self):
        saved, rec = await self.run_pb("/deep", row_selector="#host >>> li.it", fields=[
            ScrapeField(name="Name", selector=".n"), ScrapeField(name="Price", selector=".p", type="number")])
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(rec["outputs"][0]["table"]["rows"], [["One", "1.5"], ["Two", "2"]])

    async def test_rows_inside_a_same_site_iframe_are_read(self):
        saved, rec = await self.run_pb("/deep", row_selector="#fr >>> div.row", fields=[
            ScrapeField(name="Name", selector=".n"), ScrapeField(name="Price", selector=".p", type="number")])
        self.assertEqual(rec["outputs"][0]["table"]["rows"], [["FrameA", "3"], ["FrameB", "4"]])

    async def test_a_column_can_step_into_a_shadow_root_inside_the_row(self):
        saved, rec = await self.run_pb("/deep", row_selector="#outer >>> div.card", fields=[
            ScrapeField(name="Name", selector=".n"), ScrapeField(name="Deep", selector="#in >>> em.deep")])
        self.assertEqual(rec["outputs"][0]["table"]["rows"], [["Nested", "Deep value"]])

    async def test_without_the_arrow_a_selector_does_not_reach_inside(self):
        saved, rec = await self.run_pb("/deep", row_selector="li.it")
        self.assertEqual(rec["status"], "failed")                                  # honest: nothing is found, not a silent empty table
        self.assertIn("No rows were found", " ".join(s["detail"] for s in rec["steps"]))

    async def test_an_arrow_into_something_with_nothing_to_enter_finds_nothing(self):
        saved, rec = await self.run_pb("/deep", row_selector="#nothing >>> li")
        self.assertEqual(rec["status"], "failed")

    async def test_the_preview_shows_what_the_selectors_find_on_the_page_in_view(self):
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.base() + "/")
        import asyncio
        await asyncio.sleep(1.0)
        body = {"row_selector": "tr.item", "fields": [{"name": "Name", "selector": ".n", "type": "text"}]}
        r = await agent_ui.scrape_preview(body)                        # the same loop as the browser session
        self.assertTrue(r["found"], r)
        self.assertEqual(r["rows"][0], ["Red shoes"])
        bad = await agent_ui.scrape_preview({**body, "row_selector": "div.none"})
        self.assertFalse(bad["found"])


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class PickerInBrowser(ScrapeBase):
    """Click-to-pick on real pages. The clicks are real DOM events dispatched on the real page."""

    async def open(self, path):
        import asyncio
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.base() + path)
        await asyncio.sleep(1.2)
        return await session.get_current_page()

    async def click(self, page, selector):
        await page.evaluate("(sel) => { document.querySelector(sel).dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); return 'ok' }", selector)

    async def picks(self, page):
        from aiba.playbooks import picker as pp
        return await pp.poll(page)

    async def count(self, page, selector):
        return int(await page.evaluate("(s) => document.querySelectorAll(s).length", selector))

    async def test_clicking_one_card_finds_all_the_cards_and_not_the_ad_in_the_sidebar(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/cards")
        await pp.start(page, "row")
        await self.click(page, ".card[data-i='2'] .title")
        out = await self.picks(page)
        self.assertTrue(out["active"], out)
        (pick,) = out["picks"]
        sels = {c["selector"]: c["count"] for c in pick["candidates"]}
        card = [s for s, n in sels.items() if n == 4]
        self.assertTrue(card, sels)                                          # a selector that matches exactly the four cards
        self.assertEqual(await self.count(page, card[0]), 4)
        self.assertNotIn("Ad", " ".join(c["text"] for c in pick["candidates"] if c["selector"] == card[0]))
        await pp.stop(page)

    async def test_clicking_inside_a_table_row_offers_the_row_and_its_cells(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/")
        await pp.start(page, "row")
        await self.click(page, "tr.item[data-id='b2'] .n")
        (pick,) = (await self.picks(page))["picks"]
        by_count = {c["selector"]: c["count"] for c in pick["candidates"]}
        self.assertIn(3, by_count.values())                                  # the three rows
        row = [s for s, n in by_count.items() if await self.count(page, s) == 3 and "tr" in s]
        self.assertTrue(row, by_count)
        await pp.stop(page)

    async def test_a_column_is_picked_relative_to_the_row_with_a_guess_at_its_kind(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/cards")
        await pp.start(page, "field", "#app > div.card")
        await self.click(page, ".card[data-i='2'] .price")
        (pick,) = (await self.picks(page))["picks"]
        self.assertEqual((pick["suggest"], pick["sample"], pick["hits"], pick["rows"]), ("number", "$7.50", 4, 4))
        got = await page.evaluate("(a) => document.querySelector(a[0]).querySelector(a[1]).textContent", ["#app > div.card", pick["selector"]])
        self.assertEqual(got, "$5.00")                                       # the same selector reads the first card too
        await self.click(page, ".card[data-i='2'] .go")
        (link,) = (await self.picks(page))["picks"]
        self.assertTrue(link["href"].endswith("/b"), link)
        self.assertEqual(link["hits"], 3)                                    # the fourth card has no link: the user is told
        await pp.stop(page)

    async def test_the_row_itself_can_be_a_column(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/cards")
        await pp.start(page, "field", "#app > div.card")
        await self.click(page, ".card[data-i='3']")
        (pick,) = (await self.picks(page))["picks"]
        self.assertEqual(pick["selector"], ":scope")

    async def test_a_click_outside_every_row_says_so(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/cards")
        await pp.start(page, "field", "#app > div.card")
        await self.click(page, "#out")
        (pick,) = (await self.picks(page))["picks"]
        self.assertIn("inside one of the rows", pick["error"])
        await pp.stop(page)

    async def test_the_page_does_not_react_while_picking_and_is_put_back_after(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/cards")
        await pp.start(page, "row")
        await self.click(page, "#b")
        self.assertEqual(await page.evaluate("() => document.title"), "cards", "the page's own button fired while picking")
        self.assertEqual(await self.count(page, "[data-aiba-picker]"), 1)      # the outline is there
        await pp.stop(page)
        self.assertEqual(await self.count(page, "[data-aiba-picker]"), 0)
        self.assertEqual((await self.picks(page))["active"], False)
        await self.click(page, "#b")
        self.assertEqual(await page.evaluate("() => document.title"), "CLICKED")   # and the page works again

    async def test_clicking_inside_a_shadow_root_finds_the_rows_there(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/deep")
        await pp.start(page, "row")
        await page.evaluate("() => document.getElementById('host').shadowRoot.querySelector('.n').dispatchEvent(new MouseEvent('click', {bubbles: true, composed: true, cancelable: true}))")
        (pick,) = (await self.picks(page))["picks"]
        shadow = [c for c in pick["candidates"] if ">>>" in c["selector"] and c["count"] == 2]
        self.assertTrue(shadow, pick)
        self.assertTrue(shadow[0]["selector"].startswith("#host >>> "), shadow[0])
        await pp.stop(page)

    async def test_clicking_inside_an_iframe_finds_the_rows_there(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/deep")
        await pp.start(page, "row")
        await page.evaluate("() => document.getElementById('fr').contentDocument.querySelector('.n').dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))")
        (pick,) = (await self.picks(page))["picks"]
        frame = [c for c in pick["candidates"] if c["selector"].startswith("#fr >>> ") and c["count"] == 2]
        self.assertTrue(frame, pick)
        await pp.stop(page)

    async def test_a_column_picked_inside_a_shadow_row_and_the_whole_table_read_end_to_end(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/deep")
        await pp.start(page, "field", "#host >>> li.it")
        await page.evaluate("() => document.getElementById('host').shadowRoot.querySelector('.p').dispatchEvent(new MouseEvent('click', {bubbles: true, composed: true, cancelable: true}))")
        (pick,) = (await self.picks(page))["picks"]
        self.assertEqual((pick["hits"], pick["rows"], pick["suggest"]), (2, 2, "number"))
        await pp.stop(page)
        saved, rec = await self.run_pb("/deep", row_selector="#host >>> li.it", fields=[ScrapeField(name="Price", selector=pick["selector"], type="number")])
        self.assertEqual(rec["outputs"][0]["table"]["rows"], [["1.5"], ["2"]])

    async def test_a_column_picked_through_two_shadow_roots_reads_back(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/deep")
        await pp.start(page, "field", "#outer >>> div.card")
        await page.evaluate("() => document.getElementById('outer').shadowRoot.getElementById('in').shadowRoot.querySelector('em').dispatchEvent(new MouseEvent('click', {bubbles: true, composed: true, cancelable: true}))")
        (pick,) = (await self.picks(page))["picks"]
        self.assertIn(">>>", pick["selector"], pick)
        await pp.stop(page)
        saved, rec = await self.run_pb("/deep", row_selector="#outer >>> div.card", fields=[ScrapeField(name="V", selector=pick["selector"])])
        self.assertEqual(rec["outputs"][0]["table"]["rows"], [["Deep value"]])

    async def test_the_page_does_not_react_to_clicks_inside_a_frame_while_picking(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/deep")
        await pp.start(page, "row")
        await page.evaluate("() => { const d = document.getElementById('fr').contentDocument; d.body.addEventListener('click', () => { window.parent.document.title = 'FRAMECLICK' }); d.querySelector('.n').dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})) }")
        self.assertEqual(await page.evaluate("() => document.title"), "deep")
        await pp.stop(page)

    async def test_the_outline_follows_the_mouse(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/cards")
        await pp.start(page, "row")
        await page.evaluate("() => document.querySelector('.card .title').dispatchEvent(new MouseEvent('mousemove', {bubbles: true}))")
        shown = await page.evaluate("() => document.querySelector('[data-aiba-picker]').style.display")
        self.assertEqual(shown, "block")
        await pp.stop(page)

    async def test_starting_again_replaces_the_first_picker_instead_of_stacking(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/cards")
        await pp.start(page, "row")
        await pp.start(page, "row")
        self.assertEqual(await self.count(page, "[data-aiba-picker]"), 1)
        await pp.stop(page)

    async def test_what_was_picked_reads_the_table_end_to_end(self):
        from aiba.playbooks import picker as pp
        page = await self.open("/cards")
        await pp.start(page, "row")
        await self.click(page, ".card[data-i='1'] .title")
        (rows,) = (await self.picks(page))["picks"]
        row_sel = [c["selector"] for c in rows["candidates"] if c["count"] == 4][0]
        await pp.stop(page)
        await pp.start(page, "field", row_sel)
        await self.click(page, ".card[data-i='1'] .title")
        (name,) = (await self.picks(page))["picks"]
        await self.click(page, ".card[data-i='1'] .price")
        (price,) = (await self.picks(page))["picks"]
        await pp.stop(page)
        saved, rec = await self.run_pb("/cards", row_selector=row_sel, fields=[
            ScrapeField(name="Name", selector=name["selector"]), ScrapeField(name="Price", selector=price["selector"], type="number")])
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(rec["outputs"][0]["table"]["rows"], [["Alpha", "5"], ["Beta", "7.5"], ["Gamma", "9"], ["Delta", "1.25"]])


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class SuggestInBrowser(ScrapeBase):
    async def open(self, path):
        import asyncio
        session = await agent_ui._ensure_session()
        await session.navigate_to(self.base() + path)
        await asyncio.sleep(1.2)
        return await session.get_current_page()

    async def test_the_digest_is_small_and_collapses_repeated_items(self):
        from aiba.playbooks import suggest as ps
        page = await self.open("/cards")
        text = await ps.digest_page(page)
        self.assertIn("class=\"card k9\"", text)
        self.assertEqual(text.count("<h3 class=\"title\">"), 4)                 # three examples in the list + the one in the sidebar
        self.assertIn("1 more like the one above", text)
        self.assertLessEqual(len(text), ps.MAX_DIGEST)

    async def test_the_digest_goes_into_shadow_roots_and_same_site_frames_and_says_how_to_select_there(self):
        from aiba.playbooks import suggest as ps
        page = await self.open("/deep")
        text = await ps.digest_page(page)
        for seen in ("#shadow-root", "#iframe-page", "One", "FrameA", "Deep value"):
            self.assertIn(seen, text, seen)

    async def test_the_digest_never_contains_scripts_styles_or_what_was_typed_into_fields(self):
        from aiba.playbooks import suggest as ps
        page = await self.open("/secrets")
        text = await ps.digest_page(page)
        for leaked in ("hunter2", "TOPSECRET", "my private search", "color:red", "SVGTEXT", "base64", "SECRET123", "frag"):
            self.assertNotIn(leaked, text, leaked)
        self.assertIn("Visible text", text)
        self.assertIn('href="/profile"', text)                                      # the path is enough to tell links apart

    async def test_a_suggestion_is_tried_on_the_real_page_and_comes_back_with_its_rows(self):
        from aiba.playbooks import suggest as ps
        page = await self.open("/cards")
        llm = type("L", (), {})()
        async def ainvoke(messages, output_format=None, **kw):
            from types import SimpleNamespace
            return SimpleNamespace(completion=ps.Suggestion(row_selector="#app > div.card", fields=[
                ps.SuggestedField(name="Name", selector=".title"), ps.SuggestedField(name="Price", selector=".price", type="number"),
                ps.SuggestedField(name="Ghost", selector=".nothing-here")]))
        llm.ainvoke = ainvoke
        out = await ps.suggest(page, llm, "names and prices", self.base() + "/cards")
        self.assertEqual(out["dropped"], ["Ghost"])
        self.assertEqual(out["table"]["rows"], [["Alpha", "5"], ["Beta", "7.5"], ["Gamma", "9"], ["Delta", "1.25"]])
        saved, rec = await self.run_pb("/cards", row_selector=out["row_selector"], fields=[ScrapeField(**f) for f in out["fields"]])
        self.assertEqual(rec["outputs"][0]["table"]["rows"], out["table"]["rows"])      # what was previewed is what a run reads


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class ForEachInBrowser(ScrapeBase):
    async def run_loop(self, **kw):
        host = self.base().split("//")[1].split(":")[0]
        steps = [pbk.Step(id="s1", kind="open", url=self.base() + "/people"),
                 pbk.Step(id="s2", kind="scrape", row_selector="tr.p", note="People",
                          fields=[ScrapeField(name="Name", selector=".n"), ScrapeField(name="Page", selector="a", type="link")]),
                 pbk.Step(id="s3", kind="foreach", source="s2", column="Page", variable="page", body=2, **kw),
                 pbk.Step(id="s4", kind="open", url="{{page}}"),
                 pbk.Step(id="s5", kind="scrape", note="Detail",
                          fields=[ScrapeField(name="Who", selector=".who"), ScrapeField(name="Role", selector=".role")])]
        pb = pbk.refresh(pbk.Playbook(name="Loop", steps=steps,
                                      variables=[pbk.Variable(name="page", type="url", allowed_hosts=[host], loop=True)]))
        self.assertEqual(pbk.validate(pb), [])
        saved = self.pbs.save(pb, approve=True)
        n_llm = len(self.llms)
        r = await agent_ui.run_playbook(saved.id, {"inputs": {}})
        self.assertTrue(r["ok"], r)
        await until(lambda: not agent_ui._ctl.running, what="the loop to finish")
        self.assertEqual(len(self.llms), n_llm, "a loop must not call a model")
        return saved, self.runs.get(saved.id, r["run_id"])

    async def test_it_visits_each_person_and_reads_the_detail_page(self):
        saved, rec = await self.run_loop(on_failure="skip")
        # the Role of the third person is missing on its page (a real failure), so that row is skipped
        self.assertEqual(rec["status"], "done", rec)
        details = [o["table"] for o in rec["outputs"] if o["table"]["columns"][0] == "Page"]
        self.assertEqual([t["rows"][0][1:] for t in details], [["Ann Lee", "Engineer"], ["Bob Ray", "Designer"]])
        self.assertEqual(details[0]["rows"][0][0], self.base() + "/person/1")

    async def test_a_link_to_another_site_is_never_visited(self):
        saved, rec = await self.run_loop(on_failure="skip")
        step = [s for s in rec["steps"] if s["id"] == "s3"][0]
        self.assertIn("1 left out", step["detail"])
        self.assertIn("evil.test", step["detail"])
        page = await self.session.get_current_page()
        self.assertNotIn("evil.test", await page.evaluate("() => location.href"))

    async def test_without_skip_the_first_bad_row_stops_the_run_and_says_which(self):
        saved, rec = await self.run_loop()
        self.assertEqual(rec["status"], "failed")
        self.assertIn("row 3 of 3", rec["error"])

    async def test_the_combined_results_have_one_line_per_person(self):
        import csv
        import io
        from aiba.server.results_export import runs_to_csv
        saved, rec = await self.run_loop(on_failure="skip")
        rows = list(csv.reader(io.StringIO(runs_to_csv([rec]))))
        self.assertEqual(rows[0][3:], ["Name", "Page", "Who", "Role", "Error"])
        mine = [r for r in rows[1:] if r[5]]
        self.assertEqual(sorted(r[5] for r in mine), ["Ann Lee", "Bob Ray"])


if __name__ == "__main__":
    unittest.main()
