"""Tests for playbooks/picker.py: what is accepted from the page (untrusted) and how the endpoints behave."""

import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import json
import unittest
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import picker as pp
class FakePage:
    def __init__(self, answer=None):
        self.answer, self.calls = answer, []

    async def evaluate(self, fn, *args):
        self.calls.append((fn, args))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Clean(unittest.TestCase):
    def test_a_row_pick_keeps_a_few_good_candidates(self):
        raw = {"candidates": [{"selector": "tr.item", "count": 12, "text": "Ann  5"}, {"selector": "td", "count": 1}, "junk",
                              {"selector": "  ", "count": 5}, {"selector": "li", "count": True}]}
        self.assertEqual(pp.clean_pick(raw), {"candidates": [{"selector": "tr.item", "count": 12, "text": "Ann  5"}]})

    def test_no_usable_candidate_is_an_error_in_words(self):
        for raw in ({"candidates": []}, {"candidates": [{"selector": "x", "count": 1}]}):
            self.assertIn("appears several times", pp.clean_pick(raw)["error"])

    def test_at_most_eight_candidates(self):
        raw = {"candidates": [{"selector": f"a{i}", "count": 5} for i in range(20)]}
        self.assertEqual(len(pp.clean_pick(raw)["candidates"]), pp.MAX_CANDIDATES)

    def test_a_column_pick_is_cleaned(self):
        raw = {"selector": ".price", "sample": "$5.00", "href": "https://a.com/x", "suggest": "number", "hits": 10, "rows": 12}
        self.assertEqual(pp.clean_pick(raw), {"selector": ".price", "sample": "$5.00", "href": "https://a.com/x",
                                              "suggest": "number", "hits": 10, "rows": 12})

    def test_what_a_hostile_page_might_send_is_cut_down(self):
        raw = {"selector": "a" * 5000, "sample": "x" * 5000, "href": "javascript:alert(1)", "suggest": "<script>", "hits": -5, "rows": "many"}
        out = pp.clean_pick(raw)
        self.assertEqual(len(out["selector"]), pp.MAX_SELECTOR)
        self.assertEqual(len(out["sample"]), pp.MAX_TEXT)
        self.assertEqual((out["href"], out["suggest"], out["hits"], out["rows"]), ("", "text", 0, 0))

    def test_control_characters_never_get_through_in_a_selector(self):
        self.assertIsNone(pp.clean_pick({"selector": "a\nb"}))
        self.assertEqual(pp.clean_pick({"selector": "a", "sample": "x\x00y\ny"})["sample"], "x y y")

    def test_things_that_are_not_picks_are_dropped(self):
        for raw in (None, "x", 5, [], {}, {"selector": ""}, {"selector": 5}):
            self.assertIsNone(pp.clean_pick(raw))

    def test_an_error_from_the_page_is_passed_on_cut_short(self):
        self.assertEqual(pp.clean_pick({"error": "Click inside one of the rows."}), {"error": "Click inside one of the rows."})
        self.assertEqual(len(pp.clean_pick({"error": "e" * 999})["error"]), 200)


class Calls(unittest.IsolatedAsyncioTestCase):
    async def test_start_passes_the_settings_as_data(self):
        page = FakePage("started")
        evil = "tr'); alert(1); ('"
        await pp.start(page, "field", evil)
        fn, args = page.calls[0]
        self.assertNotIn("alert", fn)
        self.assertEqual(args, ({"mode": "field", "rowSel": evil},))

    async def test_row_mode_needs_no_row_selector_and_field_mode_does(self):
        await pp.start(FakePage("ok"), "row")
        for bad in (None, "", "  "):
            with self.assertRaises(pp.PickError):
                await pp.start(FakePage("ok"), "field", bad)
        with self.assertRaises(pp.PickError):
            await pp.start(FakePage("ok"), "other")

    async def test_a_page_that_cannot_be_scripted_is_a_pick_error(self):
        with self.assertRaises(pp.PickError):
            await pp.start(FakePage(RuntimeError("closed")), "row")

    async def test_poll_returns_clean_picks_and_whether_it_is_still_on(self):
        page = FakePage(json.dumps({"active": True, "picks": [{"selector": ".n", "sample": "A"}, "junk", {"error": "x"}]}))
        out = await pp.poll(page)
        self.assertTrue(out["active"])
        self.assertEqual([("selector" in p, "error" in p) for p in out["picks"]], [(True, False), (False, True)])

    async def test_poll_on_a_page_that_is_gone_or_answers_oddly_is_just_not_active(self):
        for answer in (RuntimeError("closed"), "not json", json.dumps([1]), json.dumps({"active": "yes", "picks": "x"})):
            self.assertEqual(await pp.poll(FakePage(answer)), {"active": False, "picks": []})

    async def test_a_page_cannot_flood_the_answer(self):
        page = FakePage(json.dumps({"active": True, "picks": [{"selector": f"s{i}"} for i in range(500)]}))
        self.assertLessEqual(len((await pp.poll(page))["picks"]), 5)

    async def test_stop_never_raises(self):
        await pp.stop(FakePage(RuntimeError("closed")))
        await pp.stop(FakePage("stopped"))

    async def test_the_script_does_not_send_anything_anywhere(self):
        for word in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "eval(", "localStorage", "document.cookie"):
            self.assertNotIn(word, pp._START)


class Endpoints(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.client = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.client.get("/")
        self.page = FakePage(json.dumps({"active": True, "picks": []}))

        class Session:
            async def get_current_page(s): return self.page
            async def get_current_page_url(s): return "https://shop.test/"
        async def ensure(): return Session()
        p = mock.patch.object(agent_ui, "_ensure_session", ensure)
        p.start(); self.addCleanup(p.stop)

    def test_start_poll_stop(self):
        self.page.answer = "started"
        r = self.client.post("/playbooks/scrape-pick", json={"mode": "row"}).json()
        self.assertEqual((r["ok"], r["url"]), (True, "https://shop.test/"))
        self.page.answer = json.dumps({"active": True, "picks": [{"candidates": [{"selector": "tr.item", "count": 3}]}]})
        got = self.client.get("/playbooks/scrape-pick").json()
        self.assertEqual(got["picks"][0]["candidates"][0]["selector"], "tr.item")
        self.assertEqual(self.client.delete("/playbooks/scrape-pick").json(), {"ok": True})

    def test_bad_requests_are_refused(self):
        for body in ({}, {"mode": "other"}, {"mode": "row", "row_selector": 5}):
            self.assertEqual(self.client.post("/playbooks/scrape-pick", json=body).status_code, 400, body)
        self.assertEqual(self.client.post("/playbooks/scrape-pick", json={"mode": "field"}).status_code, 422)   # no rows yet

    def test_nothing_is_started_while_a_task_is_running(self):
        for flag in ("running", "paused"):
            with mock.patch.object(agent_ui._ctl, flag, True):
                self.assertEqual(self.client.post("/playbooks/scrape-pick", json={"mode": "row"}).status_code, 409, flag)
                self.assertEqual(self.client.get("/playbooks/scrape-pick").status_code, 409, flag)
        self.assertEqual(self.page.calls, [])

    def test_a_page_that_cannot_be_scripted_is_a_clear_error(self):
        self.page.answer = RuntimeError("closed")
        self.assertEqual(self.client.post("/playbooks/scrape-pick", json={"mode": "row"}).status_code, 422)

    def test_stopping_always_succeeds(self):
        self.page.answer = RuntimeError("closed")
        self.assertEqual(self.client.delete("/playbooks/scrape-pick").json(), {"ok": True})

    def test_it_needs_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        for call in (lambda: stranger.post("/playbooks/scrape-pick", json={"mode": "row"}), lambda: stranger.get("/playbooks/scrape-pick"),
                     lambda: stranger.delete("/playbooks/scrape-pick")):
            self.assertIn(call().status_code, (401, 403))
        self.assertEqual(self.page.calls, [])


if __name__ == "__main__":
    unittest.main()
