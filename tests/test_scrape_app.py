"""The scrape preview endpoint and a scrape playbook through the real HTTP API."""

import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks.store import PlaybookStore

FIELDS = [{"name": "Name", "selector": ".n", "type": "text"}, {"name": "Price", "selector": ".p", "type": "number"}]


class FakePage:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    async def evaluate(self, fn, *args):
        self.calls.append(args)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FakeSession:
    def __init__(self, page, url="https://shop.test/list"):
        self.page, self.url, self.navigated = page, url, []

    async def get_current_page(self): return self.page
    async def get_current_page_url(self): return self.url
    async def navigate_to(self, url): self.navigated.append(url)


class Preview(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.client = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.client.get("/")
        self.page = FakePage(json.dumps({"total": 3, "rows": [["Ann", "$5.00"], ["Bob", "7"], ["Cy", "x"]]}))
        self.session = FakeSession(self.page)
        async def ensure(): return self.session
        for target, value in (("_ensure_session", ensure),):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)

    def post(self, **kw):
        body = {"row_selector": "tr.item", "fields": FIELDS}
        body.update(kw)
        return self.client.post("/playbooks/scrape-preview", json=body)

    def test_it_shows_what_the_selectors_find_on_the_current_page(self):
        r = self.post().json()
        self.assertEqual((r["ok"], r["found"], r["url"]), (True, True, "https://shop.test/list"))
        self.assertEqual(r["columns"], ["Name", "Price"])
        self.assertEqual(r["rows"], [["Ann", "5"], ["Bob", "7"], ["Cy", ""]])
        self.assertEqual(self.session.navigated, [])                               # it never moves the browser

    def test_it_reads_only_a_few_rows(self):
        self.post(max_rows=400)
        self.assertEqual(self.page.calls[0][2], 10)

    def test_nothing_found_says_why_instead_of_an_empty_table(self):
        self.page.answer = json.dumps({"total": 0, "rows": []})
        r = self.post().json()
        self.assertEqual((r["ok"], r["found"]), (True, False))
        self.assertIn("No rows", r["message"])

    def test_a_bad_selector_is_told_in_words(self):
        self.page.answer = json.dumps({"error": "'<<' is not a valid selector"})
        r = self.post(row_selector="<<")
        self.assertEqual(r.status_code, 422)
        self.assertIn("not valid", r.json()["reason"])

    def test_settings_that_cannot_be_a_step_are_refused_before_the_page_is_touched(self):
        for body in ({"fields": []}, {"fields": [{"name": "1bad", "selector": "td"}]}, {"fields": "x"},
                     {"fields": [{"name": "A", "selector": "td", "type": "attr"}]}, {"max_rows": 99999},
                     {"fields": [{"name": "A", "selector": ":scope"}], "row_selector": ""}):
            r = self.post(**body)
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual(self.page.calls, [])

    def test_it_waits_while_a_task_is_using_the_browser(self):
        for flag in ("running", "paused"):
            with mock.patch.object(agent_ui._ctl, flag, True):
                self.assertEqual(self.post().status_code, 409, flag)
        self.assertEqual(self.page.calls, [])

    def test_a_dead_browser_is_a_clear_error_not_a_crash(self):
        self.page.answer = RuntimeError("target closed")
        self.assertEqual(self.post().status_code, 422)

    def test_it_needs_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.post("/playbooks/scrape-preview", json={"fields": FIELDS}).status_code, (401, 403))


class SavedThroughTheApi(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.store = PlaybookStore(pathlib.Path(tempfile.mkdtemp()))
        p = mock.patch.object(agent_ui, "_playbooks", self.store)
        p.start(); self.addCleanup(p.stop)
        self.client = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.client.get("/")

    def pb(self, **step):
        s = {"id": "s2", "kind": "scrape", "tag": "deterministic", "row_selector": "tr", "fields": FIELDS, "ask": False}
        s.update(step)
        return {"name": "Prices", "variables": [], "steps": [
            {"id": "s1", "kind": "open", "tag": "deterministic", "url": "https://shop.test/list", "ask": False, "note": ""}, s]}

    def test_a_scrape_playbook_saves_and_comes_back_whole(self):
        r = self.client.post("/playbooks", json={"playbook": self.pb(), "approve": True}).json()
        self.assertTrue(r["ok"], r)
        got = self.client.get(f"/playbooks/{r['playbook']['id']}").json()["playbook"]["steps"][1]
        self.assertEqual([f["selector"] for f in got["fields"]], [".n", ".p"])
        self.assertEqual(got["row_selector"], "tr")

    def test_a_scrape_step_without_fields_is_refused_with_a_reason(self):
        r = self.client.post("/playbooks", json={"playbook": self.pb(fields=[])})
        self.assertEqual(r.status_code, 400)
        self.assertIn("at least one field", json.dumps(r.json()))

    def test_the_results_can_be_downloaded_as_csv_and_json(self):
        saved = self.client.post("/playbooks", json={"playbook": self.pb()}).json()["playbook"]
        runs = agent_ui._runs
        rec = {"run_id": "run_" + "a" * 12, "started_at": 1.0, "status": "done", "attended": True, "inputs": {},
               "outputs": [{"step_id": "s2", "text": "1 row read",
                            "table": {"columns": ["Name"], "rows": [["Ann"]], "truncated": False}}], "error": ""}
        with mock.patch.object(agent_ui, "_runs", type(runs)(pathlib.Path(tempfile.mkdtemp()))) as store:
            agent_ui._runs.save(saved["id"], rec)
            csv_body = self.client.get(f"/playbooks/{saved['id']}/results.csv").content.decode("utf-8-sig")
            self.assertIn("Name", csv_body.splitlines()[0])
            self.assertIn("Ann", csv_body)
            data = self.client.get(f"/playbooks/{saved['id']}/results.json").json()
            self.assertEqual(data["runs"][0]["tables"][0]["rows"], [["Ann"]])
        self.assertEqual(self.client.get("/playbooks/..%5Cx/results.json").status_code, 400)


if __name__ == "__main__":
    unittest.main()
