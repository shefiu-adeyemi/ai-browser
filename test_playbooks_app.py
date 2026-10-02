"""App-level tests for the playbook endpoints. No browser: the finished run is a look-alike."""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import pathlib
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import agent_ui
import playbook as pbk
from chat_store import ChatStore
from playbook_runs import RunStore
from playbook_store import PlaybookStore
from schedule_store import ScheduleStore


def fake_history():
    def item(action, el=None, url="https://www.linkedin.com/feed/"):
        return NS(model_output=NS(action=[action], current_state=NS(next_goal="")),
                  result=[NS(error=None)], state=NS(url=url, interacted_element=[el]))
    return NS(history=[
        item({"navigate": {"url": "https://www.linkedin.com/in/jane-doe"}}),
        item({"click": {"index": 3}}, NS(node_name="BUTTON", attributes={}, ax_name="Send message", x_path="/b",
                                         element_hash=1, stable_hash=2),
             url="https://www.linkedin.com/in/jane-doe"),
        item({"done": {"text": "ok"}}),
    ])


class PlaybookApi(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(_tf.mkdtemp())
        self.store = PlaybookStore(self.tmp / "playbooks")
        for p in (mock.patch.object(agent_ui, "_playbooks", self.store),
                  mock.patch.object(agent_ui, "_runs", RunStore(self.tmp / "runs")),
                  mock.patch.object(agent_ui, "_last_run", None),
                  mock.patch.object(agent_ui, "_schedules", ScheduleStore(self.tmp / "sch.json")),
                  mock.patch.object(agent_ui, "_current_session_id", ChatStore.new_id())):
            p.start()
            self.addCleanup(p.stop)
        from fastapi.testclient import TestClient
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")                                    # picks up the auth cookie

    def finish_a_run(self):
        agent_ui._last_run = {"task": "Open https://www.linkedin.com/in/jane-doe and message her",
                              "history": fake_history(), "session_id": agent_ui._current_session_id}

    def draft(self):
        self.finish_a_run()
        r = self.c.post("/playbooks/draft")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # --------------------------------------------------------------------- drafting
    def test_no_run_yet(self):
        r = self.c.post("/playbooks/draft")
        self.assertEqual(r.status_code, 404)
        self.assertIn("no finished run", r.json()["reason"])

    def test_run_from_another_chat_is_not_offered(self):
        self.finish_a_run()
        agent_ui._last_run["session_id"] = ChatStore.new_id()
        self.assertEqual(self.c.post("/playbooks/draft").status_code, 404)

    def test_draft_shape(self):
        d = self.draft()
        self.assertTrue(d["ok"])
        self.assertEqual([s["kind"] for s in d["playbook"]["steps"]], ["open", "click"])
        self.assertEqual(d["suggestions"][0]["type"], "linkedin_url")
        self.assertTrue(d["playbook"]["steps"][1]["ask"])            # "Send message" reaches another person
        self.assertEqual(self.store.summaries(), [])                  # a draft stores nothing

    def test_draft_failure_is_reported_not_crashed(self):
        self.finish_a_run()
        with mock.patch.object(agent_ui, "draft_from_history", side_effect=RuntimeError("boom")):
            r = self.c.post("/playbooks/draft")
        self.assertEqual(r.status_code, 500)
        self.assertIn("boom", r.json()["reason"])

    # --------------------------------------------------------------------- variables
    def apply(self, pb, sug):
        r = self.c.post("/playbooks/variable", json={
            "playbook": pb, "step_id": sug["step_id"], "field": sug["field"], "name": sug["name"],
            "type": sug["type"], "allowed_hosts": sug["allowed_hosts"]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["playbook"]

    def test_variable_endpoint(self):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        self.assertEqual(pb["steps"][0]["url"], "{{profile_url}}")
        self.assertEqual(pb["variables"][0]["default"], "https://www.linkedin.com/in/jane-doe")

    def test_variable_endpoint_refusals(self):
        d = self.draft()
        base = {"playbook": d["playbook"], "step_id": "s1", "field": "url", "name": "p", "type": "linkedin_url"}
        for change in ({"step_id": "s9"}, {"field": "keys"}, {"name": "Bad Name"}, {"type": "weird"},
                       {"playbook": {"steps": "nonsense"}}):
            r = self.c.post("/playbooks/variable", json={**base, **change})
            self.assertEqual(r.status_code, 400, change)
            self.assertFalse(r.json()["ok"])

    # ----------------------------------------------------------------------- saving
    def test_save_without_approval_then_with(self):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        r = self.c.post("/playbooks", json={"playbook": pb})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["approved"])
        saved = r.json()["playbook"]
        self.assertTrue(saved["id"].startswith("pb_"))
        self.assertEqual(saved["domains"], ["linkedin.com"])
        r = self.c.post("/playbooks", json={"playbook": saved, "approve": True,
                                            "expected_version": saved["version"]})
        self.assertTrue(r.json()["approved"])
        self.assertTrue(self.c.get(f"/playbooks/{saved['id']}").json()["approved"])

    def test_approve_must_be_a_real_true(self):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        for sneaky in ("true", 1, "yes", [True]):
            r = self.c.post("/playbooks", json={"playbook": pb, "approve": sneaky})
            self.assertFalse(r.json()["approved"], sneaky)

    def test_invalid_playbooks_are_refused_with_every_problem(self):
        r = self.c.post("/playbooks", json={"playbook": {"name": "", "steps": []}})
        self.assertEqual(r.status_code, 400)
        self.assertGreaterEqual(len(r.json()["problems"]), 2)
        r = self.c.post("/playbooks", json={"playbook": {"name": "x", "steps": [{"id": "s1", "kind": "teleport"}]}})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/playbooks", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.store.summaries(), [])

    def test_a_secret_in_a_url_is_refused(self):
        pb = {"name": "x", "steps": [{"id": "s1", "kind": "open", "url": "https://a.com/?k=<secret>pw</secret>"}]}
        self.assertEqual(self.c.post("/playbooks", json={"playbook": pb}).status_code, 400)

    def test_stale_version_conflicts(self):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        saved = self.c.post("/playbooks", json={"playbook": pb}).json()["playbook"]
        self.c.post("/playbooks", json={"playbook": {**saved, "name": "B"}, "expected_version": 1})
        r = self.c.post("/playbooks", json={"playbook": {**saved, "name": "C"}, "expected_version": 1})
        self.assertEqual(r.status_code, 409)

    def test_update_of_a_missing_playbook(self):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        r = self.c.post("/playbooks", json={"playbook": {**pb, "id": "pb_" + "0" * 12}})
        self.assertEqual(r.status_code, 404)

    def test_expected_version_must_be_a_real_integer(self):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        saved = self.c.post("/playbooks", json={"playbook": pb}).json()["playbook"]
        # True is an int in Python; it must not be read as "version 1"
        r = self.c.post("/playbooks", json={"playbook": {**saved, "name": "B"}, "expected_version": True})
        self.assertEqual(r.status_code, 200)               # ignored, not treated as a version check
        self.assertEqual(r.json()["playbook"]["version"], 2)

    def test_an_approval_can_be_revoked_but_only_by_a_real_true(self):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        saved = self.c.post("/playbooks", json={"playbook": pb, "approve": True}).json()["playbook"]
        for sneaky in ("true", 1, "yes"):
            r = self.c.post("/playbooks", json={"playbook": saved, "revoke": sneaky})
            self.assertTrue(r.json()["approved"], sneaky)
        r = self.c.post("/playbooks", json={"playbook": saved, "revoke": True})
        self.assertFalse(r.json()["approved"])
        self.assertFalse(self.c.get(f"/playbooks/{saved['id']}").json()["approved"])

    def test_overbroad_site_in_a_saved_playbook_is_refused(self):
        pb = {"name": "x", "variables": [{"name": "u", "type": "url", "allowed_hosts": ["com"]}],
              "steps": [{"id": "s1", "kind": "open", "url": "{{u}}"}]}
        r = self.c.post("/playbooks", json={"playbook": pb})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(any("country domain" in p or "not a site" in p for p in r.json()["problems"]))

    def test_disk_errors_are_explained_not_a_bare_500(self):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        saved = self.c.post("/playbooks", json={"playbook": pb}).json()["playbook"]
        boom = OSError("disk full")
        with mock.patch.object(self.store, "save", side_effect=boom):
            r = self.c.post("/playbooks", json={"playbook": pb})
        self.assertEqual(r.status_code, 500)
        self.assertIn("disk full", r.json()["reason"])
        for method, url, patched in (("get", "/playbooks", "summaries"),
                                     ("delete", f"/playbooks/{saved['id']}", "delete"),
                                     ("get", f"/playbooks/{saved['id']}", "get")):
            with mock.patch.object(self.store, patched, side_effect=boom):
                r = getattr(self.c, method)(url)
            self.assertEqual(r.status_code, 500, url)
            self.assertIn("disk full", r.json()["reason"])
        with mock.patch.object(self.store, "restore", side_effect=boom):
            r = self.c.post(f"/playbooks/{saved['id']}/restore", json={"version": 1})
        self.assertEqual(r.status_code, 500)

    # -------------------------------------------------------- list / get / delete / restore
    def saved(self, name="A"):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        return self.c.post("/playbooks", json={"playbook": {**pb, "name": name}}).json()["playbook"]

    # ------------------------------------------------------------------------ templates
    def test_templates_are_listed_and_create_an_unapproved_playbook(self):
        listed = self.c.get("/playbooks/templates").json()["templates"]
        self.assertEqual({t["id"] for t in listed}, {"linkedin_profile", "web_search", "headlines"})
        r = self.c.post("/playbooks/from-template", json={"id": "linkedin_profile"})
        self.assertEqual(r.status_code, 200, r.text)
        pb = r.json()["playbook"]
        self.assertTrue(pb["id"].startswith("pb_"))
        self.assertIsNone(pb["approval"])
        got = self.c.get(f"/playbooks/{pb['id']}").json()
        self.assertFalse(got["approved"])
        self.assertEqual(got["playbook"]["variables"][0]["type"], "linkedin_url")

    def test_each_use_of_a_template_is_its_own_playbook(self):
        a = self.c.post("/playbooks/from-template", json={"id": "web_search"}).json()["playbook"]["id"]
        b = self.c.post("/playbooks/from-template", json={"id": "web_search"}).json()["playbook"]["id"]
        self.assertNotEqual(a, b)

    def test_an_unknown_template_and_garbage(self):
        self.assertEqual(self.c.post("/playbooks/from-template", json={"id": "nope"}).status_code, 404)
        self.assertEqual(self.c.post("/playbooks/from-template", json={}).status_code, 404)
        self.assertEqual(self.c.post("/playbooks/from-template", json={"id": ["x"]}).status_code, 404)
        self.assertEqual(self.store.summaries(), [])

    def test_a_template_cannot_be_run_before_it_is_approved_for_a_schedule(self):
        pid = self.c.post("/playbooks/from-template", json={"id": "web_search"}).json()["playbook"]["id"]
        from schedule_store import ScheduleStore
        body = {"schedule": {"name": "x", "playbook_id": pid, "items": [{"term": "cats"}], "cadence": {"kind": "daily", "at": "09:00"}}}
        self.assertEqual(self.c.post("/schedules", json=body).status_code, 400)

    def test_templates_need_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.get("/playbooks/templates").status_code, (401, 403))
        self.assertIn(stranger.post("/playbooks/from-template", json={"id": "web_search"}).status_code, (401, 403))

    # --------------------------------------------------------------------------- results
    def test_results_download(self):
        d = self.draft()
        pb = self.apply(d["playbook"], d["suggestions"][0])
        saved = self.c.post("/playbooks", json={"playbook": pb}).json()["playbook"]
        agent_ui._runs.save(saved["id"], {"run_id": RunStore.new_id(), "status": "done", "started_at": 1790000000.0,
                                          "attended": False, "inputs": {"profile_url": "https://www.linkedin.com/in/jane-doe"},
                                          "outputs": [{"text": "=HYPERLINK(\"x\")"}], "error": ""})
        r = self.c.get(f"/playbooks/{saved['id']}/results.csv")
        self.assertEqual((r.status_code, r.headers["content-type"].split(";")[0]), (200, "text/csv"))
        self.assertIn('filename="results.csv"', r.headers["content-disposition"])
        body = r.content.decode("utf-8-sig")
        self.assertIn("profile_url", body)
        self.assertIn("scheduled", body)
        self.assertIn("'=HYPERLINK", body)                               # no live formula
        self.assertTrue(r.content.startswith(b"\xef\xbb\xbf"))             # Excel reads the accents correctly

    def test_results_for_a_playbook_without_runs_is_just_a_header(self):
        d = self.draft()
        saved = self.c.post("/playbooks", json={"playbook": self.apply(d["playbook"], d["suggestions"][0])}).json()["playbook"]
        body = self.c.get(f"/playbooks/{saved['id']}/results.csv").content.decode("utf-8-sig")
        self.assertEqual(body.strip().split("\r\n")[0], "Started,Status,How,Result,Error")
        self.assertEqual(self.c.get("/playbooks/nope/results.csv").status_code, 400)

    def test_the_list_includes_damaged_files(self):
        pid = PlaybookStore.new_id()
        (self.tmp / "playbooks" / f"{pid}.json").write_text("{nope", encoding="utf-8")
        data = self.c.get("/playbooks").json()
        self.assertEqual([d["id"] for d in data["damaged"]], [pid])
        self.assertEqual(self.c.delete(f"/playbooks/{pid}").json(), {"ok": True, "deleted": True, "schedules_deleted": 0})
        self.assertEqual(self.c.get("/playbooks").json()["damaged"], [])

    def test_list_get_delete(self):
        a = self.saved("A")
        rows = self.c.get("/playbooks").json()["playbooks"]
        self.assertEqual([r["id"] for r in rows], [a["id"]])
        got = self.c.get(f"/playbooks/{a['id']}").json()
        self.assertEqual(got["playbook"]["name"], "A")
        self.assertEqual(self.c.delete(f"/playbooks/{a['id']}").json(), {"ok": True, "deleted": True, "schedules_deleted": 0})
        self.assertEqual(self.c.get(f"/playbooks/{a['id']}").status_code, 404)
        self.assertEqual(self.c.delete(f"/playbooks/{a['id']}").json(), {"ok": True, "deleted": False, "schedules_deleted": 0})

    def test_bad_ids_are_refused_without_touching_files(self):
        victim = self.tmp / "victim.json"
        victim.write_text("{}")
        for bad in ("..%5Cvictim", "%2E%2E%2Fvictim", "pb_zzzzzzzzzzzz", "nope"):
            for r in (self.c.get(f"/playbooks/{bad}"), self.c.delete(f"/playbooks/{bad}"),
                      self.c.post(f"/playbooks/{bad}/restore", json={"version": 1})):
                self.assertIn(r.status_code, (400, 404), bad)
        self.assertTrue(victim.exists())

    def test_damaged_stored_file_is_reported(self):
        pid = PlaybookStore.new_id()
        (self.tmp / "playbooks" / f"{pid}.json").write_text("{not json", encoding="utf-8")
        r = self.c.get(f"/playbooks/{pid}")
        self.assertEqual(r.status_code, 422)
        self.assertTrue(r.json()["problems"])

    def test_restore(self):
        a = self.saved("First")
        self.c.post("/playbooks", json={"playbook": {**a, "name": "Second"}, "expected_version": 1})
        self.assertEqual(self.c.get(f"/playbooks/{a['id']}").json()["versions"], [1])
        r = self.c.post(f"/playbooks/{a['id']}/restore", json={"version": 1})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["playbook"]["name"], "First")
        self.assertFalse(r.json()["approved"])
        self.assertEqual(self.c.post(f"/playbooks/{a['id']}/restore", json={"version": 99}).status_code, 404)
        self.assertEqual(self.c.post(f"/playbooks/{a['id']}/restore", json={"version": "x"}).status_code, 400)

    # --------------------------------------------------------------- access control
    def test_requests_without_the_app_cookie_are_refused(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        for method, url in (("get", "/playbooks"), ("post", "/playbooks/draft"), ("delete", "/playbooks/pb_" + "0" * 12)):
            r = getattr(stranger, method)(url)
            self.assertIn(r.status_code, (401, 403), url)


class RunClearsLastRun(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_run_clears_the_previous_one_even_if_it_fails(self):
        agent_ui._last_run = {"task": "old", "history": None, "session_id": agent_ui._current_session_id}
        with mock.patch.object(agent_ui, "get_llm", side_effect=RuntimeError("no key")),                 mock.patch.object(agent_ui, "_persist", new=mock.AsyncMock()),                 mock.patch.object(agent_ui, "_broadcast", new=mock.AsyncMock()):
            await agent_ui.run_task("new task")
        self.assertIsNone(agent_ui._last_run)


class RememberRun(unittest.TestCase):
    def test_remember_run_keeps_task_history_and_chat(self):
        agent = NS(history="HISTORY")
        with mock.patch.object(agent_ui, "_current_session_id", "abc"):
            agent_ui._remember_run("do it", agent)
        self.assertEqual(agent_ui._last_run, {"task": "do it", "history": "HISTORY", "session_id": "abc"})
        agent_ui._last_run = None


if __name__ == "__main__":
    unittest.main()
