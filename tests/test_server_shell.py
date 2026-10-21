"""Tests for agent_ui.py's shell: broadcast fan-out, log feed, lifespan, and the 'UI not built' page."""
import asyncio
import logging
import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("AIBA_DATA_DIR", tempfile.mkdtemp())

from aiba.server import main as agent_ui  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_SOURCE = ROOT / "src" / "aiba" / "server" / "main.py"


class FakeWS:
    def __init__(self, delay=0.0, fail=False):
        self.delay, self.fail, self.got, self.closed = delay, fail, [], False

    async def close(self):
        self.closed = True

    async def send_json(self, msg):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("closed")
        self.got.append(msg)


class Broadcast(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = set(agent_ui._clients)
        agent_ui._clients.clear()

    def tearDown(self):
        agent_ui._clients.clear()
        agent_ui._clients.update(self._saved)

    async def test_a_stuck_client_does_not_hold_up_the_others(self):
        slow, fast = FakeWS(delay=30), FakeWS()
        agent_ui._clients.update({slow, fast})
        with mock.patch.object(agent_ui, "_SEND_TIMEOUT", 0.2):
            t = asyncio.get_running_loop().time()
            await agent_ui._broadcast({"type": "x"})
            took = asyncio.get_running_loop().time() - t
        self.assertEqual(fast.got, [{"type": "x"}])
        self.assertLess(took, 2)
        self.assertNotIn(slow, agent_ui._clients)        # dropped; it reconnects on its own
        await asyncio.sleep(0.05)
        self.assertTrue(slow.closed)                     # and hung up on, so its page notices
        self.assertIn(fast, agent_ui._clients)

    async def test_frames_to_a_slow_client_are_dropped_not_queued_and_do_not_block(self):
        slow, fast = FakeWS(delay=0.3), FakeWS()
        agent_ui._clients.update({slow, fast})
        t = asyncio.get_running_loop().time()
        for i in range(10):
            await agent_ui._broadcast({"type": "screenshot", "data": i})
            await asyncio.sleep(0.01)
        self.assertLess(asyncio.get_running_loop().time() - t, 0.5)   # the sender never waited for the slow one
        await asyncio.sleep(0.8)
        self.assertEqual([m["data"] for m in fast.got], list(range(10)))
        got = [m["data"] for m in slow.got]
        self.assertLess(len(got), 10)                 # skipped frames
        self.assertEqual(got[-1], 9)                  # but it ends on the newest one
        self.assertEqual(got, sorted(got))

    async def test_failed_clients_are_dropped_and_healthy_ones_kept(self):
        bad, good = FakeWS(fail=True), FakeWS()
        agent_ui._clients.update({bad, good})
        await agent_ui._broadcast({"type": "y"})
        self.assertEqual(agent_ui._clients, {good})

    async def test_no_clients_is_fine(self):
        await agent_ui._broadcast({"type": "z"})


class LogHandler(unittest.IsolatedAsyncioTestCase):
    async def test_only_cleaned_agent_lines_are_broadcast(self):
        sent = []

        async def fake(msg):
            sent.append(msg)
        handler = agent_ui._WSLogHandler()
        with mock.patch.object(agent_ui, "_broadcast", fake):
            for name, text in (("browser_use.Agent", "📍 Step 2: go"), ("browser_use.BrowserSession", "cdp internals"),
                               ("browser_use.tools", "typed sk-abcdefghijklmnopqrstuvwxyz123456")):
                handler.emit(logging.LogRecord(name, logging.INFO, __file__, 1, text, None, None))
            await asyncio.sleep(0.05)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]["step"], 2)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", sent[1]["text"])


class Shell(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_runs_startup_then_shutdown(self):
        order = []

        async def up():
            order.append("up")

        async def down():
            order.append("down")
        with mock.patch.object(agent_ui, "_startup", up), mock.patch.object(agent_ui, "_shutdown", down):
            async with agent_ui._lifespan(agent_ui.app):
                order.append("running")
        self.assertEqual(order, ["up", "running", "down"])

    def test_the_page_is_drawn_larger_by_giving_the_browser_a_smaller_window(self):
        """Page zoom = a proportionally smaller headless window, so the live view shows everything bigger
        with nothing cropped."""
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIBA_PAGE_ZOOM", None)
            self.assertEqual(agent_ui._page_viewport(), {"width": 1920, "height": 1080})        # normal size
            with mock.patch.dict(os.environ, {"AIBA_PAGE_ZOOM": "1.1"}):
                self.assertEqual(agent_ui._page_viewport(), {"width": 1745, "height": 982})   # 1920x1080 / 1.1
        for value, expected in (("1.25", 1536), ("1", 1920), ("5", 960), ("0.2", 1920), ("junk", 1920)):
            with mock.patch.dict(os.environ, {"AIBA_PAGE_ZOOM": value}):
                self.assertEqual(agent_ui._page_viewport()["width"], expected, value)   # clamped to 1.0 .. 2.0

    def test_the_browser_session_uses_that_window(self):
        s = agent_ui._make_session()
        self.assertEqual(dict(s.browser_profile.viewport), agent_ui._page_viewport())

    def test_the_session_cookie_is_per_port(self):
        """Browsers share cookies between ports of one host, so two copies of the app (e.g. port 8000 and a
        test copy) overwrote each other's token and logged each other out."""
        self.assertIn(str(agent_ui.PORT), agent_ui._COOKIE_NAME)

    def test_no_deprecated_event_hooks(self):
        self.assertNotIn("on_event(", SERVER_SOURCE.read_text(encoding="utf-8"))

    def test_the_inline_legacy_ui_is_gone(self):
        source = SERVER_SOURCE.read_text(encoding="utf-8")
        self.assertLess(source.count("<div") + source.count("<script"), 3)         # no page markup inline
        self.assertEqual(source.count("<!DOCTYPE html>"), 1)        # only the tiny 'not built' page

    def test_without_a_build_the_page_says_how_to_build_it(self):
        from fastapi.testclient import TestClient
        with mock.patch.object(agent_ui, "FRONTEND_DIST", pathlib.Path(tempfile.mkdtemp()) / "missing"), \
                mock.patch.object(agent_ui, "_startup", mock.AsyncMock()), \
                mock.patch.object(agent_ui, "_shutdown", mock.AsyncMock()):
            r = TestClient(agent_ui.app, base_url="http://localhost:8000").get("/")
        self.assertEqual(r.status_code, 503)
        self.assertIn("npm run build", r.text)


class BackgroundTasks(unittest.IsolatedAsyncioTestCase):
    """Fire-and-forget tasks are held until they finish: the event loop only keeps weak references to tasks."""

    async def test_a_spawned_task_is_held_until_it_is_done(self):
        gate = asyncio.Event()

        async def work():
            await gate.wait()
            return "done"

        task = agent_ui._spawn(work())
        self.assertIn(task, agent_ui._background_tasks)
        gate.set()
        self.assertEqual(await task, "done")
        await asyncio.sleep(0)
        self.assertNotIn(task, agent_ui._background_tasks)


if __name__ == "__main__":
    unittest.main()
