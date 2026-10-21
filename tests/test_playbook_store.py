"""Tests for playbooks/store.py."""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from aiba.playbooks import model as pbk
from aiba.playbooks.model import Playbook, Step, Variable
from aiba.playbooks.store import (HISTORY_KEEP, PlaybookConflict, PlaybookInvalid, PlaybookNotFound,
                            PlaybookStore)


def make(name="Open a profile") -> Playbook:
    return Playbook(name=name, variables=[Variable(name="profile_url", type="linkedin_url")],
                    steps=[Step(id="s1", kind="open", url="{{profile_url}}")])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = PlaybookStore(Path(self._tmp.name))

    def test_create_assigns_id_version_domains(self):
        saved = self.store.save(make())
        self.assertTrue(PlaybookStore.is_valid_id(saved.id))
        self.assertEqual(saved.version, 1)
        self.assertEqual(saved.domains, ["linkedin.com"])
        self.assertEqual(self.store.get(saved.id), saved)

    def test_ids_cannot_escape_the_folder(self):
        for bad in ("../x", "pb_..\\x", "pb_12", "", "pb_" + "g" * 12, "pb_" + "a" * 12 + "/x", None, 5):
            self.assertFalse(PlaybookStore.is_valid_id(bad), bad)
            with self.assertRaises(ValueError):
                self.store._path(bad)
        with self.assertRaises(ValueError):
            self.store.delete("../../etc")

    def test_invalid_playbook_is_not_stored(self):
        with self.assertRaises(PlaybookInvalid) as ctx:
            self.store.save(Playbook(name="", steps=[]))
        self.assertTrue(len(ctx.exception.problems) >= 2)          # every problem, not just the first
        self.assertEqual(list(Path(self._tmp.name).iterdir()), [])

    def test_update_bumps_version_and_keeps_history(self):
        a = self.store.save(make())
        b = self.store.save(a.model_copy(update={"name": "Renamed"}), expected_version=1)
        self.assertEqual(b.version, 2)
        self.assertEqual(b.created_at, a.created_at)
        self.assertEqual(self.store.versions(a.id), [1])

    def test_conflict_when_version_is_stale(self):
        a = self.store.save(make())
        self.store.save(a.model_copy(update={"name": "B"}), expected_version=1)
        with self.assertRaises(PlaybookConflict):
            self.store.save(a.model_copy(update={"name": "C"}), expected_version=1)
        self.assertEqual(self.store.get(a.id).name, "B")

    def test_update_of_unknown_id_fails(self):
        with self.assertRaises(PlaybookNotFound):
            self.store.save(make().model_copy(update={"id": "pb_" + "0" * 12}))

    def test_client_cannot_choose_version_or_dates(self):
        forged = make().model_copy(update={"version": 99, "created_at": "1999-01-01T00:00:00+00:00"})
        saved = self.store.save(forged)
        self.assertEqual(saved.version, 1)
        self.assertNotEqual(saved.created_at, "1999-01-01T00:00:00+00:00")

    def test_approval_only_when_asked(self):
        a = self.store.save(make())
        self.assertFalse(pbk.is_approved(a))
        b = self.store.save(make(), approve=True)
        self.assertTrue(pbk.is_approved(self.store.get(b.id)))

    def test_forged_approval_is_dropped(self):
        forged = make().model_copy(update={"approval": pbk.Approval(hash="0" * 64, approved_at="now")})
        saved = self.store.save(forged)
        self.assertIsNone(saved.approval)
        self.assertIsNone(self.store.get(saved.id).approval)

    def test_editing_a_step_clears_the_approval(self):
        a = self.store.save(make(), approve=True)
        edited = a.model_copy(update={"steps": [a.steps[0].model_copy(update={"new_tab": True})]})
        b = self.store.save(edited)
        self.assertFalse(pbk.is_approved(b))
        self.assertFalse(pbk.is_approved(self.store.get(a.id)))

    def test_the_user_can_take_an_approval_back(self):
        a = self.store.save(make(), approve=True)
        b = self.store.save(a, revoke=True)
        self.assertFalse(pbk.is_approved(b))
        self.assertFalse(pbk.is_approved(self.store.get(a.id)))
        c = self.store.save(b, approve=True, revoke=True)                       # approve wins if both are sent
        self.assertTrue(pbk.is_approved(c))

    def test_rename_keeps_the_approval(self):
        a = self.store.save(make(), approve=True)
        b = self.store.save(a.model_copy(update={"name": "New name"}))
        self.assertTrue(pbk.is_approved(b))

    def test_list_summaries_newest_first(self):
        a = self.store.save(make("A"))
        b = self.store.save(make("B"), approve=True)
        rows = self.store.summaries()
        self.assertEqual({r["id"] for r in rows}, {a.id, b.id})
        row = next(r for r in rows if r["id"] == b.id)
        self.assertTrue(row["approved"])
        self.assertEqual((row["steps"], row["ai_steps"], row["variables"]), (1, 0, ["profile_url"]))

    def test_damaged_file_is_skipped_in_list_and_reported_on_get(self):
        good = self.store.save(make())
        (Path(self._tmp.name) / f"{PlaybookStore.new_id()}.json").write_text("{not json", encoding="utf-8")
        self.assertEqual([r["id"] for r in self.store.summaries()], [good.id])
        bad_id = PlaybookStore.new_id()
        (Path(self._tmp.name) / f"{bad_id}.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(PlaybookInvalid):
            self.store.get(bad_id)

    def test_a_hand_edited_site_list_is_recomputed_on_load_and_the_approval_survives_only_if_steps_match(self):
        a = self.store.save(make(), approve=True)
        path = Path(self._tmp.name) / f"{a.id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["domains"] = ["linkedin.com", "evil.org"]
        path.write_text(json.dumps(data), encoding="utf-8")
        got = self.store.get(a.id)
        self.assertEqual(got.domains, ["linkedin.com"])               # the extra site is gone
        self.assertTrue(pbk.is_approved(got))                         # the steps did not change
        data["steps"][0]["new_tab"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(pbk.is_approved(self.store.get(a.id)))       # but an edited step is not approved

    def test_damaged_files_are_listed_so_they_can_be_seen_and_deleted(self):
        good = self.store.save(make())
        bad1, bad2 = PlaybookStore.new_id(), PlaybookStore.new_id()
        (Path(self._tmp.name) / f"{bad1}.json").write_text("{not json", encoding="utf-8")
        (Path(self._tmp.name) / f"{bad2}.json").write_text("{}", encoding="utf-8")        # parses, breaks the rules
        (Path(self._tmp.name) / "notes.json").write_text("{}", encoding="utf-8")          # not a playbook file at all
        found = {d["id"]: d["problem"] for d in self.store.damaged()}
        self.assertEqual(set(found), {bad1, bad2})
        self.assertTrue(all(found.values()))
        self.assertNotIn(good.id, found)
        self.assertTrue(self.store.delete(bad1))
        self.assertEqual([d["id"] for d in self.store.damaged()], [bad2])

    def test_unknown_get_and_delete(self):
        with self.assertRaises(PlaybookNotFound):
            self.store.get("pb_" + "0" * 12)
        self.assertFalse(self.store.delete("pb_" + "0" * 12))

    def test_delete_removes_history_too(self):
        a = self.store.save(make())
        self.store.save(a.model_copy(update={"name": "B"}))
        self.assertTrue(self.store.delete(a.id))
        self.assertEqual(list(Path(self._tmp.name).iterdir()), [])

    def test_history_is_capped(self):
        pb = self.store.save(make())
        for i in range(HISTORY_KEEP + 5):
            pb = self.store.save(pb.model_copy(update={"name": f"v{i}"}))
        versions = self.store.versions(pb.id)
        self.assertEqual(len(versions), HISTORY_KEEP)
        self.assertEqual(versions[0], pb.version - 1)

    def test_restore_creates_a_new_version_without_approval(self):
        a = self.store.save(make("First"), approve=True)
        self.store.save(a.model_copy(update={"name": "Second"}))
        restored = self.store.restore(a.id, 1)
        self.assertEqual(restored.name, "First")
        self.assertEqual(restored.version, 3)
        self.assertIsNone(restored.approval)
        with self.assertRaises(PlaybookNotFound):
            self.store.restore(a.id, 42)

    def test_concurrent_saves_never_corrupt(self):
        a = self.store.save(make())
        errors: list[Exception] = []

        def worker(n: int):
            try:
                for i in range(10):
                    cur = self.store.get(a.id)
                    try:
                        self.store.save(cur.model_copy(update={"name": f"w{n}-{i}"}), expected_version=cur.version)
                    except PlaybookConflict:
                        pass
            except Exception as e:                       # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        final = self.store.get(a.id)
        json.loads((Path(self._tmp.name) / f"{a.id}.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(final.version, 2)
        self.assertEqual(list(Path(self._tmp.name).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
