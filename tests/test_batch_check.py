"""Tests for server/batch_check.py (the dry run), results_export.combined_table and their endpoints."""

import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import pathlib
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.server.batch_check import check_rows
from aiba.playbooks.model import Fingerprint, Playbook, Step, Variable
from aiba.playbooks.runs import RunStore
from aiba.playbooks.store import PlaybookStore
from aiba.server.results_export import combined_table
from aiba.scheduling.limits import SiteLimits

PROFILE = "https://www.linkedin.com/in/{}"


def linkedin_pb(sends=0):
    steps = [Step(id="s1", kind="open", url="{{p}}"),
             Step(id="s2", kind="type", target=Fingerprint(node_name="input", ax_name="Note"), text="Hi <secret>x</secret> {{p}}")]
    for n in range(sends):
        steps.append(Step(id=f"s{3 + n}", kind="click", target=Fingerprint(node_name="button", ax_name="Send"), ask=True))
    return pbk.refresh(Playbook(name="Visit", variables=[Variable(name="p", type="linkedin_url")], steps=steps))


class Check(unittest.TestCase):
    def setUp(self):
        self.limits = SiteLimits(pathlib.Path(tempfile.mkdtemp()) / "l.json")
        self.today = date(2026, 10, 1)

    def rows(self, n, bad_at=()):
        return [{"p": "nope"} if i in bad_at else {"p": PROFILE.format(f"p{i}")} for i in range(n)]

    def test_only_the_first_three_are_shown_but_all_are_counted(self):
        out = check_rows(linkedin_pb(), self.rows(10, bad_at=(7,)), self.limits, self.today)
        self.assertEqual((len(out["rows"]), out["total"], out["bad"]), (3, 10, 1))
        self.assertTrue(all(r["ok"] for r in out["rows"]))

    def test_each_shown_row_says_what_it_would_do(self):
        out = check_rows(linkedin_pb(), self.rows(2), self.limits, self.today)
        steps = out["rows"][1]["steps"]
        self.assertEqual(steps[0], f"Open {PROFILE.format('p1')}")
        self.assertIn(PROFILE.format("p1"), steps[1])

    def test_a_stored_secret_is_never_shown(self):
        out = check_rows(linkedin_pb(), self.rows(1), self.limits, self.today)
        text = " ".join(out["rows"][0]["steps"])
        self.assertIn("<secret>x</secret>", text)                         # still a placeholder, never its value

    def test_a_bad_row_says_why_and_shows_no_steps(self):
        out = check_rows(linkedin_pb(), self.rows(3, bad_at=(1,)), self.limits, self.today)
        bad = out["rows"][1]
        self.assertFalse(bad["ok"])
        self.assertIn("LinkedIn", " ".join(bad["problems"]))
        self.assertEqual(bad["steps"], [])

    def test_a_row_that_is_not_an_object_is_bad_not_a_crash(self):
        out = check_rows(linkedin_pb(), ["x", None, 5, {"p": PROFILE.format("a")}], self.limits, self.today)
        self.assertEqual(out["bad"], 3)
        self.assertEqual([r["problems"] for r in out["rows"]], [["this row is not a set of inputs"]] * 3)

    def test_it_says_how_many_days_the_batch_needs(self):
        out = check_rows(linkedin_pb(), self.rows(120), self.limits, self.today)          # 50 a day to start
        self.assertEqual(out["needs"], {"linkedin_profile_view": 120})
        self.assertEqual(out["days"], 3)
        self.assertEqual(check_rows(linkedin_pb(), self.rows(40), self.limits, self.today)["days"], 1)

    def test_what_was_already_used_today_counts(self):
        self.limits.record({"linkedin_profile_view": 45}, datetime(2026, 10, 1, 9, 0))
        out = check_rows(linkedin_pb(), self.rows(20), self.limits, self.today)          # 5 left today, 15 tomorrow
        self.assertEqual(out["days"], 2)

    def test_rows_that_are_not_valid_do_not_count_toward_the_allowance(self):
        out = check_rows(linkedin_pb(), self.rows(60, bad_at=range(20)), self.limits, self.today)
        self.assertEqual(out["needs"], {"linkedin_profile_view": 40})
        self.assertEqual(out["days"], 1)

    def test_no_limit_means_no_estimate(self):
        pb = pbk.refresh(Playbook(name="x", variables=[Variable(name="t")], steps=[Step(id="s1", kind="search", engine="duckduckgo", query="{{t}}")]))
        out = check_rows(pb, [{"t": "a"}, {"t": "b"}], self.limits, self.today)
        self.assertEqual((out["needs"], out["days"]), ({}, None))
        self.assertEqual(out["rows"][0]["steps"], ['Search duckduckgo for "a"'])

    def test_no_rows_at_all(self):
        out = check_rows(linkedin_pb(), [], self.limits, self.today)
        self.assertEqual((out["rows"], out["total"], out["bad"], out["days"]), ([], 0, 0, None))

    def test_sends_count_separately_and_set_the_pace(self):
        out = check_rows(linkedin_pb(sends=1), self.rows(40), self.limits, self.today)     # 15 sends a day to start
        self.assertEqual(out["needs"]["linkedin_sensitive"], 40)
        self.assertEqual(out["days"], 3)


class Combined(unittest.TestCase):
    def rec(self, started, columns, rows):
        return {"started_at": started, "outputs": [{"text": "", "table": {"columns": columns, "rows": rows, "truncated": False}}]}

    def test_rows_are_stacked_oldest_first_with_all_columns(self):
        out = combined_table([self.rec(2, ["A", "C"], [["3", "4"]]), self.rec(1, ["A", "B"], [["1", "2"]])])
        self.assertEqual(out["columns"], ["A", "B", "C"])
        self.assertEqual(out["rows"], [["1", "2", ""], ["3", "", "4"]])
        self.assertEqual(out["total"], 2)

    def test_nothing_scraped_is_an_empty_table(self):
        self.assertEqual(combined_table([{"outputs": [{"text": "x"}]}, {}]), {"columns": [], "rows": [], "total": 0})

    def test_damaged_records_are_skipped_and_the_size_is_capped(self):
        out = combined_table([{"outputs": [{"table": "junk"}]}, self.rec(1, ["A"], [["x"]] * 7 + ["junk"])], limit=5)
        self.assertEqual((len(out["rows"]), out["total"]), (5, 7))


class Endpoints(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.pbs, self.runs = PlaybookStore(tmp / "pbs"), RunStore(tmp / "runs")
        self.limits = SiteLimits(tmp / "l.json")
        for target, value in (("_playbooks", self.pbs), ("_runs", self.runs), ("_limits", self.limits)):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)
        self.pb = self.pbs.save(linkedin_pb(), approve=True)
        self.client = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.client.get("/")

    def test_check_rows_over_http(self):
        rows = [{"p": PROFILE.format("a")}, {"p": "bad"}, {"p": PROFILE.format("c")}]
        r = self.client.post(f"/playbooks/{self.pb.id}/check-rows", json={"rows": rows}).json()
        self.assertTrue(r["ok"])
        self.assertEqual((r["total"], r["bad"], [x["ok"] for x in r["rows"]]), (3, 1, [True, False, True]))

    def test_check_rows_refuses_nonsense(self):
        url = f"/playbooks/{self.pb.id}/check-rows"
        for body in ({}, {"rows": "x"}, {"rows": [{}] * 201}):
            self.assertEqual(self.client.post(url, json=body).status_code, 400, body)
        self.assertEqual(self.client.post(f"/playbooks/pb_{'0' * 12}/check-rows", json={"rows": []}).status_code, 404)
        self.assertEqual(self.client.post("/playbooks/..%5Cx/check-rows", json={"rows": []}).status_code, 400)

    def test_the_results_table_of_a_playbook(self):
        self.runs.save(self.pb.id, {"run_id": "run_" + "a" * 12, "started_at": 1.0, "status": "done",
                                    "outputs": [{"text": "", "table": {"columns": ["Link"], "rows": [["https://x.com/1"]], "truncated": False}}]})
        r = self.client.get(f"/playbooks/{self.pb.id}/results/table").json()
        self.assertEqual((r["columns"], r["rows"]), (["Link"], [["https://x.com/1"]]))
        self.assertEqual(self.client.get("/playbooks/..%5Cx/results/table").status_code, 400)

    def test_both_need_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.get(f"/playbooks/{self.pb.id}/results/table").status_code, (401, 403))
        self.assertIn(stranger.post(f"/playbooks/{self.pb.id}/check-rows", json={"rows": []}).status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
