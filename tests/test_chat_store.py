"""Unit tests for chat_store.ChatStore (plain files in a temp folder; no browser, no app)."""
import json
import pathlib
import tempfile
import threading
import time
import unittest
from unittest import mock

from aiba.storage import chat_store
from aiba.storage.chat_store import ChatStore, MAX_ENTRIES


def turns(n, first="Find jobs on LinkedIn"):
    out = [{"role": "user", "content": first, "ts": "10:00"}]
    for i in range(1, n):
        out.append({"role": "agent" if i % 2 else "user", "content": f"turn {i}", "ts": "10:01"})
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.dir = self.tmp / "sessions"
        self.store = ChatStore(self.dir)


class Ids(Base):
    def test_new_ids_are_valid_and_unique(self):
        ids = {ChatStore.new_id() for _ in range(5000)}
        self.assertEqual(len(ids), 5000)          # the old timestamp-chopped-to-20-chars scheme collided
        self.assertTrue(all(ChatStore.is_valid_id(i) for i in ids))

    def test_old_style_ids_still_work(self):
        self.assertTrue(ChatStore.is_valid_id("20260925_231702_9448"))

    def test_path_traversal_and_junk_ids_are_rejected(self):
        for bad in ("..", "../x", "..\\x", "..\\..\\evil", "a/b", "a\\b", "", " ", "x" * 300,
                    "20260925_231702_9448/../../x", "20260925_231702_9448.json", "20260925_231702_",
                    "20260925_231702_ZZZZ", "2026-09-25", None, 123, "index"):
            self.assertFalse(ChatStore.is_valid_id(bad), repr(bad))
            with self.assertRaises(ValueError):
                self.store.path(bad)                       # type: ignore[arg-type]

    def test_bad_ids_cannot_delete_or_read_anything(self):
        victim = self.tmp / "victim.json"
        victim.write_text("[1]")
        self.assertFalse(self.store.delete("..\\victim"))
        self.assertFalse(self.store.delete("../victim"))
        self.assertEqual(self.store.load("..\\victim"), [])
        self.assertTrue(victim.exists())


class SaveLoad(Base):
    def test_round_trip_and_index(self):
        sid = ChatStore.new_id()
        self.store.save(sid, turns(4), verdict="pass", duration=12.5)
        self.assertEqual(self.store.load(sid), turns(4))
        (e,) = self.store.index()
        self.assertEqual((e["id"], e["title"], e["turns"], e["last_verdict"], e["last_duration"]),
                         (sid, "Find jobs on LinkedIn", 2, "pass", 12.5))
        self.assertTrue(self.store.has(sid))
        self.assertFalse(self.store.has(ChatStore.new_id()))

    def test_missing_or_corrupt_chat_loads_as_empty(self):
        sid = ChatStore.new_id()
        self.assertEqual(self.store.load(sid), [])
        (self.dir / f"{sid}.json").write_text("{not json")
        self.assertEqual(self.store.load(sid), [])

    def test_malformed_entries_on_disk_are_dropped_not_passed_on(self):
        """A damaged or hand-edited chat file must not be able to crash whatever reads it later."""
        sid = ChatStore.new_id()
        (self.dir / f"{sid}.json").write_text(json.dumps(
            [1, "text", None, ["x"], {"role": "user", "content": "ok"}, {"role": "agent", "content": 5},
             {"role": "agent", "content": "fine"}, {"role": "user"}]))
        self.assertEqual(self.store.load(sid), [{"role": "user", "content": "ok"},
                                                 {"role": "agent", "content": "fine"}, {"role": "user"}])
        (self.dir / f"{sid}.json").write_text(json.dumps({"not": "a list"}))
        self.assertEqual(self.store.load(sid), [])

    def test_no_temp_files_are_left_behind(self):
        for _ in range(5):
            self.store.save(ChatStore.new_id(), turns(3))
        self.assertEqual([p.name for p in self.dir.glob("*.tmp")], [])

    def test_a_failed_write_raises_and_leaves_the_old_file_intact(self):
        """Atomic: a crash / full disk mid-write must never corrupt the chat that was already saved."""
        sid = ChatStore.new_id()
        self.store.save(sid, turns(4))
        with mock.patch.object(chat_store.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.save(sid, turns(10))
        self.assertEqual(self.store.load(sid), turns(4))                 # old content still there
        self.assertEqual([p.name for p in self.dir.glob("*.tmp")], [])   # and no debris

    def test_title_and_turn_count_survive_long_chats(self):
        """The file used to keep only the last 200 turns but take the title from the first turn."""
        sid = ChatStore.new_id()
        self.store.save(sid, turns(10))
        long = turns(MAX_ENTRIES + 500)
        self.store.save(sid, long)
        saved = self.store.load(sid)
        self.assertEqual(len(saved), MAX_ENTRIES)
        self.assertEqual(saved[0]["content"], "Find jobs on LinkedIn")   # the original request is kept
        self.assertEqual(saved[-1], long[-1])                            # and the newest turns
        (e,) = self.store.index()
        self.assertEqual(e["title"], "Find jobs on LinkedIn")
        self.assertEqual(e["turns"], len(long) // 2)                     # counted from the real chat

    def test_title_is_stable_once_set(self):
        sid = ChatStore.new_id()
        self.store.save(sid, turns(2, first="Original request"))
        self.store.save(sid, [{"role": "user", "content": "something else", "ts": "x"}] * 2)
        self.assertEqual(self.store.index()[0]["title"], "Original request")

    def test_delete(self):
        a, b = ChatStore.new_id(), ChatStore.new_id()
        self.store.save(a, turns(2))
        self.store.save(b, turns(2))
        self.assertTrue(self.store.delete(a))
        self.assertEqual([e["id"] for e in self.store.index()], [b])
        self.assertFalse((self.dir / f"{a}.json").exists())
        self.assertFalse(self.store.delete(a))                            # already gone


class Robustness(Base):
    def test_concurrent_saves_never_drop_index_entries(self):
        """The index used to be read, changed and rewritten with no lock: parallel saves lost entries."""
        ids = [ChatStore.new_id() for _ in range(120)]
        errors = []
        real_write = self.store._write_index

        def slow_write(entries):                   # widen the read-modify-write window so a missing
            time.sleep(0.002)                      # lock loses entries every time, not just sometimes
            real_write(entries)

        def worker(chunk):
            try:
                for sid in chunk:
                    self.store.save(sid, turns(4))
            except Exception as e:                 # pragma: no cover - would fail the assertion below
                errors.append(e)
        with mock.patch.object(self.store, "_write_index", side_effect=slow_write):
            threads = [threading.Thread(target=worker, args=(ids[i::8],)) for i in range(8)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual({e["id"] for e in self.store.index()}, set(ids))
        json.loads((self.dir / "index.json").read_text(encoding="utf-8"))      # and the file is valid JSON

    def test_a_corrupt_index_is_rebuilt_from_the_chat_files_and_kept_for_inspection(self):
        ids = [ChatStore.new_id() for _ in range(3)]
        for sid in ids:
            self.store.save(sid, turns(4, first=f"chat {sid[-4:]}"))
        (self.dir / "index.json").write_text("{{ half written")
        entries = self.store.index()
        self.assertEqual({e["id"] for e in entries}, set(ids))               # nothing lost
        self.assertEqual({e["title"] for e in entries}, {f"chat {i[-4:]}" for i in ids})
        self.assertTrue(list(self.dir.glob("index.corrupt-*.json")))         # the bad file was kept, not overwritten
        self.assertEqual(len(json.loads((self.dir / "index.json").read_text(encoding="utf-8"))), 3)

    def test_a_missing_index_is_rebuilt(self):
        sid = ChatStore.new_id()
        self.store.save(sid, turns(4))
        (self.dir / "index.json").unlink()
        self.assertEqual([e["id"] for e in self.store.index()], [sid])

    def test_strange_files_in_the_folder_are_ignored(self):
        (self.dir / "notes.json").write_text("[]")
        (self.dir / "..evil.json").write_text("[]")
        self.assertEqual(self.store.index(), [])

    def test_index_entries_with_bad_ids_are_dropped(self):
        (self.dir / "index.json").write_text(json.dumps([{"id": "..\\..\\x", "title": "bad"}]))
        self.assertEqual([e for e in self.store.index() if e["id"] == "..\\..\\x"], [])


if __name__ == "__main__":
    unittest.main()
