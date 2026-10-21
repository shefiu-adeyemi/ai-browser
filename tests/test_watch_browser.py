"""
Change monitoring against a REAL Chromium and the real scheduler: a price page that changes between scheduled runs.
No model is involved.

Opt-in (launches a browser):  RUN_BROWSER_TESTS=1 python -m unittest test_watch_browser -v
"""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import http.server
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from aiba.server import main as agent_ui
from aiba.playbooks import model as pbk
from aiba.scheduling import schedules as sch
from aiba.scheduling.monitor import Watch
from aiba.server.notifications import DesktopNotifier, NotificationStore
from aiba.playbooks.model import ScrapeField
from aiba.playbooks.runs import RunStore
from aiba.playbooks.store import PlaybookStore
from aiba.scheduling.store import ScheduleStore
from aiba.scheduling.scheduler import Scheduler
from aiba.scheduling.limits import SiteLimits
from aiba.storage.snapshots import SnapshotStore
from test_lifecycle_browser import LifecycleBase, until

PRICES = {"Red shoes": "$50.00", "Blue socks": "$7.00", "Green hat": "$20.00"}
CLOCK = {"text": "just now"}                       # something on the page that changes by itself every time


def page_html() -> bytes:
    rows = "".join(f"<tr class='item'><td class='n'>{n}</td><td class='p'>{p}</td></tr>" for n, p in PRICES.items())
    return (f"<html><title>shop</title><body><p id='seen'>Updated {CLOCK['text']}</p><table>{rows}</table></body></html>").encode()


class _Shop(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(page_html())
    def log_message(self, *a): pass


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 to launch Chromium")
class WatchedPage(LifecycleBase):
    async def asyncSetUp(self):
        import test_lifecycle_browser as tlb
        PRICES.clear(); PRICES.update({"Red shoes": "$50.00", "Blue socks": "$7.00", "Green hat": "$20.00"})
        CLOCK["text"] = "just now"
        orig = tlb._Page
        tlb._Page = _Shop
        try:
            await super().asyncSetUp()
        finally:
            tlb._Page = orig
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="aiba-watch-"))
        self.pbs, self.runs = PlaybookStore(tmp / "playbooks"), RunStore(tmp / "runs")
        self.scheds, self.notes = ScheduleStore(tmp / "s.json"), NotificationStore(tmp / "n.json")
        self.limits, self.snaps = SiteLimits(tmp / "l.json"), SnapshotStore(tmp / "snaps")
        self.toasts = []
        desktop = DesktopNotifier(show=lambda t, x: self.toasts.append(t))
        for target, value in (("_playbooks", self.pbs), ("_runs", self.runs), ("_schedules", self.scheds), ("_limits", self.limits),
                              ("_notifications", self.notes), ("_desktop", desktop), ("_snapshots", self.snaps)):
            p = mock.patch.object(agent_ui, target, value)
            p.start(); self.addCleanup(p.stop)
        self.sched = Scheduler(schedules=self.scheds, playbooks=self.pbs, limits=self.limits, execute=agent_ui._execute_scheduled,
                               idle=lambda: not agent_ui._ctl.running and not agent_ui._ctl.paused,
                               notify=agent_ui._notify_user, resolved=agent_ui._record_parked_end)
        steps = [pbk.Step(id="s1", kind="open", url=self.url),
                 pbk.Step(id="s2", kind="scrape", row_selector="tr.item", note="Prices",
                          fields=[ScrapeField(name="Name", selector=".n"), ScrapeField(name="Price", selector=".p", type="number")]),
                 pbk.Step(id="s3", kind="scrape", note="Stamp", fields=[ScrapeField(name="Seen", selector="#seen")])]
        self.pb = self.pbs.save(pbk.refresh(pbk.Playbook(name="Prices", steps=steps)), approve=True)

    def schedule(self, **watch):
        s = self.scheds.save(sch.Schedule(name="Price watch", playbook_id=self.pb.id, items=[{}], on_failure="stop",
                                          watch=Watch(enabled=True, **watch)))
        self.sid = s.id
        return s

    async def look(self):
        self.scheds.change_state(self.sid, lambda st: st.model_copy(update={"next_run": sch.iso(datetime.now() - timedelta(seconds=5))}))
        known = {r["run_id"] for r in self.runs.recent(self.pb.id)}
        await self.sched.tick()
        await until(lambda: not agent_ui._ctl.running and any(r["run_id"] not in known for r in self.runs.recent(self.pb.id)), what="the run")
        rid = next(r["run_id"] for r in self.runs.recent(self.pb.id) if r["run_id"] not in known)
        return self.runs.get(self.pb.id, rid)

    def warnings(self):
        return [n for n in self.notes.recent() if n["level"] == "warning"]

    async def test_a_changed_price_a_new_item_and_a_gone_item_are_found_and_announced_once(self):
        self.schedule(ignore_columns=["Seen"])
        first = await self.look()
        self.assertEqual(first["watch"]["status"], "baseline")
        PRICES["Red shoes"] = "$45.00"
        del PRICES["Blue socks"]
        PRICES["Yellow scarf"] = "$12.00"
        CLOCK["text"] = "5 minutes later"                      # the stamp changed too, but the user left it out
        second = await self.look()
        w = second["watch"]
        self.assertEqual((w["status"], w["added"], w["removed"], w["changed"]), ("changed", 1, 1, 1), w)
        row = w["diff"]["changed_rows"][0]
        self.assertEqual((row["key"], row["columns"]), ("Red shoes", [{"column": "Price", "before": "50", "after": "45"}]))
        (note,) = self.warnings()
        self.assertEqual(note["title"], 'Changed: "Price watch"')
        self.assertIn("Red shoes", note["text"])
        self.assertIn("Yellow scarf", note["text"])
        self.assertIn("Blue socks", note["text"])
        self.assertEqual(len(self.toasts), 1)                      # one pop-up, not one per change

    async def test_a_page_that_has_not_changed_says_nothing_even_though_a_stamp_on_it_did(self):
        self.schedule(ignore_columns=["Seen"])
        await self.look()
        before = len(self.notes.recent())
        CLOCK["text"] = "10 minutes later"
        again = await self.look()
        self.assertEqual(again["watch"], {"status": "unchanged"})
        self.assertEqual(len(self.notes.recent()), before)
        self.assertEqual(self.toasts, [])

    async def test_without_leaving_the_stamp_out_every_look_is_a_change(self):
        self.schedule()                                           # the user did nothing about the noise
        await self.look()
        CLOCK["text"] = "later"
        self.assertEqual((await self.look())["watch"]["status"], "changed")

    async def test_a_pattern_cuts_the_noise_out_of_the_stamp(self):
        self.schedule(ignore_patterns=[r"Updated .*"])
        await self.look()
        CLOCK["text"] = "much later"
        self.assertEqual((await self.look())["watch"]["status"], "unchanged")
        PRICES["Green hat"] = "$25.00"
        self.assertEqual((await self.look())["watch"]["status"], "changed")

    async def test_small_price_moves_are_ignored_until_they_add_up(self):
        self.schedule(ignore_columns=["Seen"], min_change_percent=10)
        await self.look()
        statuses = []
        for price in ("$51.00", "$52.00", "$58.00"):
            PRICES["Red shoes"] = price
            statuses.append((await self.look())["watch"]["status"])
        self.assertEqual(statuses, ["unchanged", "unchanged", "changed"])      # 4% and 8% are quiet; 16% is not

    async def test_a_page_that_breaks_is_a_failure_not_a_change(self):
        self.schedule(ignore_columns=["Seen"])
        await self.look()
        PRICES.clear()                                            # the list vanished
        broken = await self.look()
        self.assertEqual(broken["status"], "failed")
        self.assertNotIn("watch", broken)
        self.assertEqual(self.warnings(), [])                      # no "everything is gone" alarm: the failure says what happened
        PRICES.update({"Red shoes": "$50.00", "Blue socks": "$7.00", "Green hat": "$20.00"})
        self.scheds.change_state(self.sid, lambda st: st.model_copy(update={"paused_reason": "", "paused_detail": ""}))
        back = await self.look()
        self.assertEqual(back["watch"]["status"], "unchanged")     # the reference was not spoiled by the failed run
