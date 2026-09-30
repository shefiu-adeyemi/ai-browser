"""Tests for the approval broker, the approval endpoint and the private-data migration."""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_paths
from approvals import ApprovalBroker, ConsoleApprover


def make_broker(clients=True, timeout=5.0):
    sent = []

    async def bc(m):
        sent.append(m)

    return ApprovalBroker(bc, lambda: clients, timeout=timeout), sent


class Broker(unittest.IsolatedAsyncioTestCase):
    async def test_user_approves(self):
        b, sent = make_broker()
        task = asyncio.create_task(b.ask("approve", "Send?", "detail", "https://x.com"))
        await asyncio.sleep(0.01)
        req = sent[0]
        self.assertEqual((req["type"], req["title"], req["kind"]), ("approval", "Send?", "approve"))
        self.assertTrue(b.resolve(req["id"], True))
        self.assertTrue(await task)
        self.assertEqual(sent[-1], {"type": "approval_done", "id": req["id"]})   # UI prompt is cleared
        self.assertEqual(b.snapshot(), [])

    async def test_user_denies(self):
        b, sent = make_broker()
        task = asyncio.create_task(b.ask("approve", "Send?"))
        await asyncio.sleep(0.01)
        b.resolve(sent[0]["id"], False)
        self.assertFalse(await task)

    async def test_no_answer_in_time_means_no(self):
        b, _ = make_broker(timeout=0.05)
        self.assertFalse(await b.ask("approve", "Send?"))

    async def test_nobody_watching_means_no_and_no_prompt(self):
        b, sent = make_broker(clients=False)
        self.assertFalse(await b.ask("approve", "Send?"))
        self.assertEqual(sent, [])

    async def test_stop_denies_everything_outstanding(self):
        b, _ = make_broker()
        t1 = asyncio.create_task(b.ask("approve", "a"))
        t2 = asyncio.create_task(b.ask("handoff", "b"))
        await asyncio.sleep(0.01)
        b.cancel_all()
        self.assertEqual([await t1, await t2], [False, False])

    async def test_pending_prompt_is_replayed_to_a_refreshed_page(self):
        b, sent = make_broker()
        task = asyncio.create_task(b.ask("approve", "Send?"))
        await asyncio.sleep(0.01)
        self.assertEqual([m["id"] for m in b.snapshot()], [sent[0]["id"]])
        b.cancel_all()
        await task

    async def test_unknown_or_repeated_answers_are_ignored(self):
        b, sent = make_broker()
        self.assertFalse(b.resolve("nope", True))
        task = asyncio.create_task(b.ask("approve", "x"))
        await asyncio.sleep(0.01)
        aid = sent[0]["id"]
        self.assertTrue(b.resolve(aid, True))
        self.assertFalse(b.resolve(aid, False))      # can't flip a decision already made
        self.assertTrue(await task)

    async def test_stopping_the_task_while_a_prompt_is_open_really_stops_it(self):
        b, sent = make_broker()
        task = asyncio.create_task(b.ask("approve", "Send?"))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):    # must NOT come back as a "no" answer
            await task
        self.assertEqual(b.snapshot(), [])                   # and the prompt is cleaned up
        self.assertEqual(sent[-1]["type"], "approval_done")

    async def test_console_approver(self):
        c = ConsoleApprover()
        for typed, kind, expected in (("y", "approve", True), ("", "approve", False), ("no", "approve", False),
                                      ("", "handoff", True), ("n", "handoff", False)):
            with mock.patch("builtins.input", return_value=typed), mock.patch("builtins.print"):
                self.assertEqual(await c.ask(kind, "t"), expected, (typed, kind))
        with mock.patch("builtins.input", side_effect=EOFError), mock.patch("builtins.print"):
            self.assertFalse(await c.ask("approve", "t"))          # closed stdin fails closed


class Endpoint(unittest.TestCase):
    def test_needs_auth_and_answers_pending_request(self):
        import agent_ui
        from fastapi.testclient import TestClient
        c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertEqual(c.post("/approvals/abc", json={"allow": True}).status_code, 401)   # no cookie
        c.get("/")
        self.assertEqual(c.post("/approvals/abc", json={"allow": True}).json(), {"ok": False})   # unknown id

        async def flow():
            task = asyncio.create_task(agent_ui._broker.ask("approve", "t"))
            with mock.patch.object(agent_ui, "_clients", {object()}):
                pass
            return task
        # resolve a real pending future through the HTTP endpoint
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            agent_ui._broker._pending["known"] = fut
            r = c.post("/approvals/known", json={"allow": True})
            self.assertEqual(r.json(), {"ok": True})
            self.assertTrue(fut.result())
        finally:
            agent_ui._broker._pending.pop("known", None)
            loop.close()


class Migration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old, self.data = self.tmp / "project", self.tmp / "data"
        self.old.mkdir()
        (self.old / ".env").write_text("ANTHROPIC_API_KEY=sk-secret\n")
        (self.old / ".browser_profile").mkdir()
        (self.old / ".browser_profile" / "Cookies").write_text("cookie")
        (self.old / "sessions").mkdir()
        (self.old / "sessions" / "index.json").write_text("[]")
        legacy = [(self.old / ".env", self.data / ".env"),
                  (self.old / "sessions", self.data / "sessions"),
                  (self.old / ".browser_profile", self.data / "profiles" / "ui"),
                  (self.old / "browser_profile", self.data / "profiles" / "linkedin"),   # doesn't exist: skipped
                  (self.old / ".last_url", self.data / "last_url")]
        for p in (mock.patch.object(app_paths, "_LEGACY", legacy), mock.patch.object(app_paths, "DATA_DIR", self.data)):
            p.start()
            self.addCleanup(p.stop)

    def test_other_drive_move_never_shows_a_partial_destination(self):
        seen = []
        real_replace = app_paths.os.replace

        def spy(a, b):
            seen.append(Path(b).exists())             # destination must not exist before the final rename
            return real_replace(a, b)
        with mock.patch.object(app_paths.os, "rename", side_effect=OSError("cross-device")), \
                mock.patch.object(app_paths.os, "replace", spy):
            app_paths.migrate_legacy()
        self.assertEqual((self.data / ".env").read_text(), "ANTHROPIC_API_KEY=sk-secret\n")
        self.assertEqual((self.data / "profiles" / "ui" / "Cookies").read_text(), "cookie")
        self.assertFalse((self.old / ".env").exists())
        self.assertFalse((self.old / ".browser_profile").exists())
        self.assertFalse(any(seen))
        self.assertEqual([p.name for p in self.data.rglob("*.partial")], [])

    def test_an_interrupted_copy_leaves_the_original_and_is_retried(self):
        (self.data / "profiles").mkdir(parents=True)
        (self.data / "profiles" / "ui.partial").mkdir()              # debris from an earlier crash
        (self.data / "profiles" / "ui.partial" / "junk").write_text("x")
        with mock.patch.object(app_paths.os, "rename", side_effect=OSError("cross-device")), \
                mock.patch.object(app_paths.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                app_paths.migrate_legacy()
        self.assertTrue((self.old / ".browser_profile" / "Cookies").exists())   # original untouched
        self.assertFalse((self.data / "profiles" / "ui").exists())
        with mock.patch.object(app_paths.os, "rename", side_effect=OSError("cross-device")):
            app_paths.migrate_legacy()                                # retry succeeds, debris discarded
        self.assertEqual((self.data / "profiles" / "ui" / "Cookies").read_text(), "cookie")
        self.assertFalse((self.data / "profiles" / "ui" / "junk").exists())

    def test_moves_not_copies(self):
        moved = app_paths.migrate_legacy()
        self.assertEqual(len(moved), 3)
        self.assertEqual((self.data / ".env").read_text(), "ANTHROPIC_API_KEY=sk-secret\n")
        self.assertEqual((self.data / "profiles" / "ui" / "Cookies").read_text(), "cookie")
        self.assertTrue((self.data / "sessions" / "index.json").exists())
        for name in (".env", ".browser_profile", "sessions"):
            self.assertFalse((self.old / name).exists(), f"{name} left behind in the project folder")

    def test_empty_destination_folder_created_by_import_does_not_block_the_move(self):
        (self.data / "sessions").mkdir(parents=True)          # e.g. SESSIONS_DIR.mkdir() at import
        app_paths.migrate_legacy()
        self.assertTrue((self.data / "sessions" / "index.json").exists())
        self.assertFalse((self.old / "sessions").exists())

    def test_task_history_leftover_moves_too(self):
        (self.old / "task_history.json").write_text("[]")
        legacy = [(self.old / "task_history.json", self.data / "task_history.json")]
        with mock.patch.object(app_paths, "_LEGACY", legacy):
            app_paths.migrate_legacy()
        self.assertTrue((self.data / "task_history.json").exists())
        self.assertFalse((self.old / "task_history.json").exists())

    def test_is_idempotent(self):
        app_paths.migrate_legacy()
        self.assertEqual(app_paths.migrate_legacy(), [])

    def test_never_overwrites_existing_data(self):
        self.data.mkdir()
        (self.data / ".env").write_text("NEW=1\n")
        app_paths.migrate_legacy()
        self.assertEqual((self.data / ".env").read_text(), "NEW=1\n")                 # kept
        self.assertTrue((self.old / ".env").exists())                                   # old copy untouched, reported


class Locations(unittest.TestCase):
    def test_data_lives_outside_the_project(self):
        project = app_paths.APP_DIR.resolve()
        for p in (app_paths.ENV_PATH, app_paths.SESSIONS_DIR, app_paths.UI_PROFILE_DIR,
                  app_paths.LINKEDIN_PROFILE_DIR, app_paths.LAST_URL_FILE):
            self.assertNotIn(project, p.resolve().parents, p)


if __name__ == "__main__":
    unittest.main()
