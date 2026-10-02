"""A Stop must not throw the work away: the chat gets what the run had found so far. No browser, no model."""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())

import unittest
from types import SimpleNamespace
from unittest import mock

import agent_ui

LONG = "Nnebuogo Egboh, Founder of Africa Market Systems & Policy Advisory, trade policy and market systems advisory. " * 2


def agent(steps=7, notes=None, extracted=()):
    fs = SimpleNamespace(display_file=lambda name: notes if name == "results.md" else None)
    hist = SimpleNamespace(number_of_steps=lambda: steps, extracted_content=lambda: list(extracted))
    return SimpleNamespace(history=hist, file_system=fs)


class Report(unittest.TestCase):
    def test_the_agents_own_notes_are_preferred(self):
        text = agent(notes="# Leads\n1. Abdoulie", extracted=[LONG])
        out = agent_ui._progress_report(text)
        self.assertTrue(out.startswith("Stopped after 7 steps. Here is what it had found so far:"))
        self.assertIn("1. Abdoulie", out)
        self.assertNotIn("Nnebuogo", out)

    def test_without_notes_it_uses_the_longest_things_it_read(self):
        out = agent_ui._progress_report(agent(extracted=["Navigated to x", LONG, "ok", LONG + " second"]))
        self.assertIn("Nnebuogo", out)
        self.assertNotIn("Navigated to x", out)                           # a one-line action result is not a finding

    def test_nothing_found_means_no_report(self):
        self.assertEqual(agent_ui._progress_report(agent(extracted=["Navigated to x"])), "")
        self.assertEqual(agent_ui._progress_report(agent(steps=0, notes="x")), "")
        self.assertEqual(agent_ui._progress_report(None), "")

    def test_it_is_capped(self):
        out = agent_ui._progress_report(agent(notes="x" * 20000))
        self.assertLess(len(out), 4200)

    def test_a_broken_agent_never_raises(self):
        broken = SimpleNamespace(history=SimpleNamespace(number_of_steps=lambda: 1 / 0))
        self.assertEqual(agent_ui._progress_report(broken), "")


class OnStop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []

        async def fake_broadcast(m):
            self.sent.append(m)
        for p in (mock.patch.object(agent_ui, "_broadcast", fake_broadcast),
                  mock.patch.object(agent_ui, "_persist", mock.AsyncMock()),
                  mock.patch.object(agent_ui, "_conversation_history", [{"role": "user", "content": "find 25", "ts": "1"}])):
            p.start()
            self.addCleanup(p.stop)

    async def test_the_report_comes_before_the_stop_marker_which_stays_last(self):
        agent_ui._active_agent = agent(notes="5 leads so far")
        await agent_ui._record_stopped()
        roles = [(t["role"], t["content"][:22]) for t in agent_ui._conversation_history]
        self.assertEqual(roles[1][1], "Stopped after 7 steps.")
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "(stopped by user)")
        result = [m for m in self.sent if m["type"] == "result"]
        self.assertEqual(len(result), 1)
        self.assertIn("5 leads so far", result[0]["text"])
        self.assertIsNone(agent_ui._active_agent)                         # used once

    async def test_no_findings_keeps_the_old_behaviour(self):
        agent_ui._active_agent = agent(extracted=[])
        await agent_ui._record_stopped()
        self.assertEqual([t["content"] for t in agent_ui._conversation_history[1:]], ["(stopped by user)"])
        self.assertEqual([m for m in self.sent if m["type"] == "result"], [])

    async def test_a_stop_after_the_answer_was_recorded_adds_nothing(self):
        agent_ui._conversation_history.append({"role": "agent", "content": "the answer", "ts": "2"})
        agent_ui._active_agent = agent(notes="late notes")
        await agent_ui._record_stopped()
        self.assertEqual(agent_ui._conversation_history[-1]["content"], "the answer")
        self.assertEqual(len(agent_ui._conversation_history), 2)


if __name__ == "__main__":
    unittest.main()
