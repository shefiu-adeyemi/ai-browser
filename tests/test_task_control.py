"""Unit tests for task_control.TaskController (no browser)."""
import asyncio
import unittest

from aiba.server.task_control import Busy, TaskController


class Gate:
    """A run that blocks until released, so tests control when it ends."""
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.runs = 0

    async def run(self):
        self.runs += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class Start(unittest.IsolatedAsyncioTestCase):
    async def test_double_submit_starts_only_one_run(self):
        """#15: the slot is claimed synchronously, before the task even exists."""
        c, g = TaskController(), Gate()
        c.start(g.run, origin="a", fmt="auto")
        with self.assertRaises(Busy):                      # no await in between: the second call
            c.start(g.run, origin="b", fmt="auto")         # must already see the slot taken
        await g.started.wait()
        await asyncio.sleep(0)
        self.assertEqual(g.runs, 1)
        self.assertEqual(c.origin_task, "a")               # the rejected call did not overwrite it
        g.release.set()

    async def test_slot_is_freed_when_the_run_finishes(self):
        c, g = TaskController(), Gate()
        c.start(g.run, origin="a", fmt="auto")
        g.release.set()
        await c._task
        self.assertFalse(c.running)
        c.start(g.run, origin="b", fmt="auto")             # can start again
        g.release.set()
        await c._task

    async def test_slot_is_freed_when_the_run_crashes(self):
        c = TaskController()

        async def boom():
            raise RuntimeError("llm exploded")
        c.start(boom, origin="a", fmt="auto")
        await c._task                                       # the crash is contained, not raised
        self.assertFalse(c.running)

    async def test_start_is_refused_while_paused(self):
        c = TaskController()
        c.paused = True
        with self.assertRaises(Busy):
            c.start(Gate().run, origin="a", fmt="auto")


class Stop(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_and_waits_for_the_run_to_really_end(self):
        c, g = TaskController(), Gate()
        c.start(g.run, origin="a", fmt="auto")
        await g.started.wait()
        self.assertEqual(await c.stop(), "stopped")
        self.assertTrue(g.cancelled)                        # it was cancelled ...
        self.assertTrue(c._task.done())                     # ... and had ended when stop() returned
        self.assertFalse(c.running)

    async def test_a_cleanup_error_during_a_requested_stop_is_visible_to_the_run(self):
        """browser-use can raise its own error while unwinding an early cancel. The run must be
        able to tell that apart from a genuine failure."""
        c = TaskController()
        seen = {}

        async def unwinds_badly():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                seen["stop_requested"] = c.stop_requested
                raise ValueError("Agent must have _task_start_time attribute")  # noqa: B904 (replaces the cancellation, as browser-use's cleanup does)
        c.start(unwinds_badly, origin="a", fmt="auto")
        await asyncio.sleep(0)
        self.assertFalse(c.stop_requested)                   # not stopping yet
        self.assertEqual(await c.stop(), "stopped")
        self.assertTrue(seen["stop_requested"])
        self.assertFalse(c.running)

    async def test_stop_flag_resets_for_the_next_run(self):
        c, g = TaskController(), Gate()
        c.start(g.run, origin="a", fmt="auto")
        await g.started.wait()
        await c.stop()
        self.assertTrue(c.stop_requested)
        g2 = Gate()
        c.start(g2.run, origin="b", fmt="auto")
        self.assertFalse(c.stop_requested)                   # a genuine failure now must not be masked
        g2.release.set()
        await c._task

    async def test_stop_when_idle(self):
        self.assertEqual(await TaskController().stop(), "idle")

    async def test_stop_while_paused_discards_it(self):
        """The Continue bar's Stop button: used to do nothing and leave the task stuck paused."""
        c = TaskController()
        c.origin_task, c.paused = "a", True
        self.assertEqual(await c.stop(), "discarded")
        self.assertFalse(c.paused)
        c.start(Gate().run, origin="b", fmt="auto")         # and a new task can now start
        await c.stop()

    async def test_a_run_that_ignores_cancellation_cannot_wedge_the_app(self):
        c = TaskController(stop_timeout=0.05)
        quit_now = asyncio.Event()
        self.addCleanup(quit_now.set)                        # even if an assertion below fails

        async def stubborn():
            while not quit_now.is_set():                      # swallows every cancel until told to quit
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    pass
        c.start(stubborn, origin="a", fmt="auto")
        await asyncio.sleep(0)
        self.assertEqual(await c.stop(), "stopped")
        self.assertFalse(c.running)                          # slot released; user isn't locked out
        quit_now.set()                                       # let the test's own task end cleanly
        await c._task


class Continue(unittest.IsolatedAsyncioTestCase):
    async def _paused(self, c):
        async def hit_limit():
            c.pause()
        c.start(hit_limit, origin="Find jobs on LinkedIn", fmt="table")
        await c._task
        self.assertTrue(c.paused and not c.running)

    async def test_continue_keeps_the_original_task_and_never_nests(self):
        """#14: 'Continue ... Original task: Continue ... Original task: ...' growing each time."""
        c = TaskController()
        await self._paused(c)
        seen = []

        async def again():
            seen.append((c.origin_task, c.fmt))
            c.pause()                                       # hits the step limit again
        for _ in range(3):
            c.resume(again)
            await c._task
        self.assertEqual(seen, [("Find jobs on LinkedIn", "table")] * 3)
        self.assertEqual(c.origin_task, "Find jobs on LinkedIn")

    async def test_stop_can_cancel_a_continued_run(self):
        """#13: the continued run's task handle was never stored, so Stop could not cancel it."""
        c, g = TaskController(), Gate()
        await self._paused(c)
        c.resume(g.run)
        await g.started.wait()
        self.assertEqual(await c.stop(), "stopped")
        self.assertTrue(g.cancelled)
        self.assertFalse(c.running)

    async def test_continue_needs_a_paused_task(self):
        c = TaskController()
        with self.assertRaises(Busy):
            c.resume(Gate().run)
        await self._paused(c)
        g = Gate()
        c.resume(g.run)
        with self.assertRaises(Busy):                       # already running again
            c.resume(g.run)
        await g.started.wait()
        g.release.set()
        await c._task

    async def test_clear_forgets_a_paused_task(self):
        c = TaskController()
        await self._paused(c)
        c.clear()
        with self.assertRaises(Busy):
            c.resume(Gate().run)


if __name__ == "__main__":
    unittest.main()
