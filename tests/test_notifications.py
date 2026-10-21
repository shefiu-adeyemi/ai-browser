"""Tests for server/notifications.py."""

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from aiba.server.notifications import DesktopNotifier, NotificationStore

NOW = datetime(2026, 10, 1, 12, 0, 0)


class Inbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "n.json"
        self.store = NotificationStore(self.path, keep=5)

    def test_add_and_list_newest_first(self):
        a = self.store.add("info", "First", "one", now=NOW)
        b = self.store.add("error", "Second", "two", schedule_id="sch_x", playbook_id="pb_y", run_id="run_z", now=NOW)
        rows = self.store.recent()
        self.assertEqual([r["id"] for r in rows], [b["id"], a["id"]])
        self.assertEqual((rows[0]["level"], rows[0]["schedule_id"], rows[0]["read"]), ("error", "sch_x", False))
        self.assertEqual(self.store.unread_count(), 2)

    def test_an_action_is_kept_only_if_it_is_a_known_plain_one(self):
        ok = self.store.add("warning", "Waiting", action={"type": "checkpoint", "schedule_id": "sch_x", "run_id": "run_y", "evil": "1"})
        self.assertEqual(ok["action"], {"type": "checkpoint", "schedule_id": "sch_x", "run_id": "run_y", "kind": "approve"})
        self.assertEqual(NotificationStore(self.path).recent()[0]["action"], ok["action"])         # and it is saved
        for bad in (None, "x", {"type": "run-this", "schedule_id": "a", "run_id": "b"}, {"type": "checkpoint"},
                    {"type": "checkpoint", "schedule_id": "a"}):
            self.assertIsNone(self.store.add("info", "x", action=bad)["action"], bad)

    def test_text_is_tidied_and_clipped(self):
        n = self.store.add("loud", "A\n\ttitle   " + "x" * 500, "line1\nline2 " + "y" * 900)
        self.assertEqual(n["level"], "info")                              # an unknown level becomes info
        self.assertNotIn("\n", n["title"] + n["text"])
        self.assertLessEqual(len(n["title"]), 120)
        self.assertLessEqual(len(n["text"]), 400)

    def test_the_inbox_is_capped(self):
        for i in range(9):
            self.store.add("info", f"n{i}")
        titles = [r["title"] for r in self.store.recent(100)]
        self.assertEqual(titles, ["n8", "n7", "n6", "n5", "n4"])

    def test_mark_read(self):
        a, b, c = (self.store.add("info", t) for t in "abc")
        self.assertEqual(self.store.mark_read([a["id"], "n_missing"]), 1)
        self.assertEqual(self.store.unread_count(), 2)
        self.assertEqual(self.store.mark_read(), 2)
        self.assertEqual(self.store.unread_count(), 0)
        self.assertEqual(self.store.mark_read(), 0)

    def test_clear(self):
        self.store.add("info", "x")
        self.assertEqual(self.store.clear(), 1)
        self.assertEqual(self.store.recent(), [])

    def test_retention(self):
        self.store.add("info", "old", now=NOW - timedelta(days=40))
        self.store.add("info", "new", now=NOW - timedelta(days=2))
        self.assertEqual(self.store.prune_older_than(30, now=NOW), 1)
        self.assertEqual([r["title"] for r in self.store.recent()], ["new"])
        self.assertEqual(self.store.prune_older_than(30, now=NOW), 0)

    def test_it_survives_a_restart_and_a_damaged_file(self):
        self.store.add("info", "kept")
        self.assertEqual(NotificationStore(self.path).recent()[0]["title"], "kept")
        self.path.write_text("{nope", encoding="utf-8")
        fresh = NotificationStore(self.path)
        self.assertEqual(fresh.recent(), [])
        fresh.add("info", "again")
        self.assertEqual(NotificationStore(self.path).unread_count(), 1)

    def test_unicode(self):
        self.store.add("info", "Étape ✓ échouée", "失败 — profil de José")
        self.assertEqual(NotificationStore(self.path).recent()[0]["text"], "失败 — profil de José")

    def test_concurrent_adds_lose_nothing(self):
        big = NotificationStore(self.path, keep=500)
        threads = [threading.Thread(target=lambda: [big.add("info", "x") for _ in range(10)]) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(big.recent(1000)), 50)
        self.assertEqual(list(Path(self._tmp.name).glob("*.tmp")), [])

    def test_desktop_setting(self):
        self.assertTrue(self.store.desktop_enabled())
        self.store.set_desktop(False)
        self.assertFalse(NotificationStore(self.path).desktop_enabled())


class Desktop(unittest.TestCase):
    def wait_for(self, cond, t=2.0):
        end = time.time() + t
        while time.time() < end and not cond():
            time.sleep(0.01)

    def test_a_toast_is_started_in_the_background(self):
        shown = []
        n = DesktopNotifier(show=lambda t, x: shown.append((t, x)))
        self.assertTrue(n.notify("Needs you", "Sign in"))
        self.wait_for(lambda: shown)
        self.assertEqual(shown, [("Needs you", "Sign in")])

    def test_disabled_shows_nothing(self):
        shown = []
        n = DesktopNotifier(enabled=lambda: False, show=lambda t, x: shown.append(t))
        self.assertFalse(n.notify("x"))
        time.sleep(0.05)
        self.assertEqual(shown, [])

    def test_a_burst_is_limited(self):
        now = [100.0]
        shown = []
        n = DesktopNotifier(min_gap=5, show=lambda t, x: shown.append(t), clock=lambda: now[0])
        self.assertTrue(n.notify("a"))
        self.assertFalse(n.notify("b"))
        now[0] += 6
        self.assertTrue(n.notify("c"))
        self.wait_for(lambda: len(shown) == 2)
        self.assertEqual(sorted(shown), ["a", "c"])

    def test_a_failing_toast_never_raises_or_blocks(self):
        def boom(t, x): raise RuntimeError("no toast service")
        n = DesktopNotifier(show=boom)
        t0 = time.time()
        self.assertTrue(n.notify("x"))
        self.assertLess(time.time() - t0, 0.5)
        self.assertFalse(DesktopNotifier(enabled=lambda: 1 / 0).notify("x"))        # even a broken setting is safe

    def test_text_is_clipped(self):
        shown = []
        DesktopNotifier(show=lambda t, x: shown.append((t, x))).notify("T" * 500, "X" * 900)
        self.wait_for(lambda: shown)
        self.assertEqual((len(shown[0][0]), len(shown[0][1])), (120, 400))

    def test_the_text_goes_through_the_environment_not_the_script(self):
        captured = {}
        def fake_run(cmd, **kw):
            captured.update(cmd=cmd, env=kw["env"])
        with mock.patch("aiba.server.notifications.subprocess.run", fake_run), mock.patch("aiba.server.notifications.os.name", "nt"):
            DesktopNotifier._powershell_toast("Evil'; Remove-Item *; '", "$(calc)")
        script = captured["cmd"][-1]
        self.assertNotIn("Remove-Item", script)
        self.assertNotIn("calc", script)
        self.assertEqual(captured["env"]["AIBA_TOAST_TITLE"], "Evil'; Remove-Item *; '")
        self.assertEqual(captured["env"]["AIBA_TOAST_TEXT"], "$(calc)")
        self.assertIn("powershell.exe", captured["env"]["AIBA_TOAST_APP"])         # a registered app, or Windows hides it
        self.assertIn("$env:AIBA_TOAST_APP", script)

    def test_other_systems_do_nothing(self):
        with mock.patch("aiba.server.notifications.subprocess.run") as run, mock.patch("aiba.server.notifications.os.name", "posix"):
            DesktopNotifier._powershell_toast("a", "b")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
