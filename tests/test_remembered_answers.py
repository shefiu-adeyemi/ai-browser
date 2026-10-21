"""Tests for storage/remembered_answers.py: typed answers the user chose to keep for later runs."""

import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.playbooks.model import Playbook, Step, Variable
from aiba.playbooks.store import PlaybookStore
from aiba.storage.remembered_answers import RememberedAnswers


class Store(unittest.TestCase):
    def setUp(self):
        self.path = pathlib.Path(tempfile.mkdtemp()) / "r.json"
        self.r = RememberedAnswers(self.path)

    def test_nothing_is_remembered_to_begin_with(self):
        self.assertEqual((self.r.get("pb_1"), self.r.names("pb_1")), ({}, []))
        self.assertFalse(self.path.exists())

    def test_an_answer_is_kept_per_playbook_and_survives_a_restart(self):
        self.assertTrue(self.r.set("pb_1", "code", " 42 "))
        self.r.set("pb_2", "code", "other")
        again = RememberedAnswers(self.path)
        self.assertEqual(again.get("pb_1"), {"code": "42"})
        self.assertEqual(again.get("pb_2"), {"code": "other"})

    def test_empty_or_huge_answers_are_not_kept(self):
        for bad in ("", "   ", "x" * 5000, None, 5):
            self.assertFalse(self.r.set("pb_1", "code", bad), bad)
        self.assertFalse(self.r.set("pb_1", "", "x"))
        self.assertEqual(self.r.get("pb_1"), {})

    def test_forgetting_one_or_all(self):
        self.r.set("pb_1", "a", "1"); self.r.set("pb_1", "b", "2")
        self.assertTrue(self.r.forget("pb_1", "a"))
        self.assertEqual(self.r.get("pb_1"), {"b": "2"})
        self.assertFalse(self.r.forget("pb_1", "a"))
        self.assertTrue(self.r.forget("pb_1"))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {})
        self.assertFalse(self.r.forget("pb_1"))

    def test_a_damaged_file_means_nothing_is_remembered_not_a_crash(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.r.get("pb_1"), {})
        self.path.write_text(json.dumps({"pb_1": {"a": 5, "b": "ok"}, "pb_2": "junk", "pb_3": []}), encoding="utf-8")
        self.assertEqual(self.r.get("pb_1"), {"b": "ok"})
        self.assertEqual(self.r.get("pb_2"), {})
        self.assertTrue(self.r.set("pb_1", "c", "new"))                   # and it can be written again


class App(unittest.TestCase):
    def setUp(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.pbs = PlaybookStore(tmp / "pbs")
        self.r = RememberedAnswers(tmp / "r.json")
        for target, value in (("_playbooks", self.pbs), ("_remembered", self.r)):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)
        self.pb = self.pbs.save(pbk.refresh(Playbook(
            name="Ask", variables=[Variable(name="code", type="number", runtime=True), Variable(name="note", runtime=True)],
            steps=[Step(id="s1", kind="open", url="https://a.com/form"),
                   Step(id="s2", kind="input", message="Code?", variable="code"),
                   Step(id="s3", kind="input", message="Note?", variable="note"),
                   Step(id="s4", kind="wait", seconds=1)])), approve=True)
        from fastapi.testclient import TestClient
        self.client = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.client.get("/")

    def test_the_kept_answers_become_answers_to_the_questions_by_step(self):
        self.r.set(self.pb.id, "code", "42")
        self.assertEqual(agent_ui._remembered_answers(self.pb), {"s2": "42"})
        self.r.set(self.pb.id, "note", "hello")
        self.assertEqual(agent_ui._remembered_answers(self.pb), {"s2": "42", "s3": "hello"})

    def test_a_kept_answer_that_no_longer_fits_is_ignored_so_it_is_asked_again(self):
        self.r.set(self.pb.id, "code", "not a number")
        self.assertEqual(agent_ui._remembered_answers(self.pb), {})

    def test_an_answer_for_a_variable_the_playbook_does_not_ask_is_ignored(self):
        self.r.set(self.pb.id, "gone", "x")
        self.assertEqual(agent_ui._remembered_answers(self.pb), {})

    def test_the_list_shows_names_never_values(self):
        self.r.set(self.pb.id, "code", "42")
        body = self.client.get(f"/playbooks/{self.pb.id}/remembered").json()
        self.assertEqual(body, {"ok": True, "variables": ["code"]})
        self.assertNotIn("42", json.dumps(body))

    def test_forgetting_one(self):
        self.r.set(self.pb.id, "code", "42")
        self.assertEqual(self.client.delete(f"/playbooks/{self.pb.id}/remembered/code").json(), {"ok": True, "forgotten": True})
        self.assertEqual(self.r.get(self.pb.id), {})
        self.assertEqual(self.client.delete(f"/playbooks/{self.pb.id}/remembered/code").json()["forgotten"], False)

    def test_a_bad_playbook_id_is_refused(self):
        self.assertEqual(self.client.get("/playbooks/..%5Cx/remembered").status_code, 400)

    def test_deleting_the_playbook_forgets_its_answers(self):
        self.r.set(self.pb.id, "code", "42")
        self.assertTrue(self.client.delete(f"/playbooks/{self.pb.id}").json()["ok"])
        self.assertEqual(self.r.get(self.pb.id), {})

    def test_the_endpoints_need_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.get(f"/playbooks/{self.pb.id}/remembered").status_code, (401, 403))
        self.assertIn(stranger.delete(f"/playbooks/{self.pb.id}/remembered/code").status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
