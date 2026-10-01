"""The chat feed against a REAL browser-use run (scripted model, no API calls).  Opt-in:
    RUN_BROWSER_TESTS=1 python -m unittest test_ui_feed_browser -v

Why this exists: the feed's unit tests used hand-made log records, and browser-use rewrites record.name
to a short label while it formats a line ("Agent", "tools"...), so a filter written against the
documented names matched nothing in the real app and the chat showed no progress at all.
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())

import asyncio
import http.server
import logging
import os
import pathlib
import tempfile
import threading
import unittest

import agent_ui
from browser_use import Agent
from fake_llm import FakeLLM
from ui_feed import UIFeed


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(b"<html><title>t</title><body>hello</body></html>")
    def log_message(self, *a): pass


class Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.feed = UIFeed(secrets=lambda: [])

    def emit(self, record):
        msg = self.feed.prepare(record)
        if msg:
            self.sent.append(msg)


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class FeedWithARealRun(unittest.IsolatedAsyncioTestCase):
    async def test_a_real_run_produces_step_lines_and_no_internals(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        collect = Collect()
        logging.getLogger("browser_use").addHandler(collect)
        s = agent_ui._make_session()
        s.browser_profile.user_data_dir = pathlib.Path(tempfile.mkdtemp())
        try:
            agent = Agent(task="open it", llm=FakeLLM([{"navigate": f"http://127.0.0.1:{srv.server_port}/"}, {"done": "ok"}]),
                          browser_session=s)
            await agent.run(max_steps=4)
        finally:
            logging.getLogger("browser_use").removeHandler(collect)
            srv.shutdown()
            try:
                await s.kill()
            except Exception:
                pass
        texts = [m["text"] for m in collect.sent]
        steps = [m["step"] for m in collect.sent if "step" in m]
        self.assertTrue(texts, "the chat would have shown nothing at all")
        self.assertEqual(steps[:2], [1, 2], texts)                       # real step markers, in order
        self.assertTrue(any("Navigated" in t for t in texts), texts)     # what the agent did
        for noise in ("viewport", "SessionManager", "telemetry", "CDP"):
            self.assertFalse(any(noise.lower() in t.lower() for t in texts), (noise, texts))


if __name__ == "__main__":
    unittest.main()
