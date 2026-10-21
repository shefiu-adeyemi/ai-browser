"""Tests for site_limits.py."""

import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from aiba.playbooks import model as pbk
from aiba.scheduling import limits as sl
from aiba.playbooks.model import Playbook, Step, Variable

NOW = datetime(2026, 10, 1, 12, 0, 0)
VIEW = {"linkedin_profile_view": 1}


class Rng(random.Random):
    """Always picks the top of the range, so waits are predictable."""
    def uniform(self, a, b): return b


class Activities(unittest.TestCase):
    def pb(self, steps, variables=None):
        return pbk.refresh(Playbook(name="x", steps=steps, variables=variables or []))

    def test_a_profile_open_counts_as_a_profile_view(self):
        pb = self.pb([Step(id="s1", kind="open", url="{{p}}")], [Variable(name="p", type="linkedin_url")])
        self.assertEqual(sl.activities(pb, {"p": "https://www.linkedin.com/in/jane"}), VIEW)

    def test_other_pages_do_not(self):
        pb = self.pb([Step(id="s1", kind="open", url="https://www.linkedin.com/feed/")])
        self.assertEqual(sl.activities(pb, {}), {})
        pb = self.pb([Step(id="s1", kind="open", url="https://a.com/in/jane")])
        self.assertEqual(sl.activities(pb, {}), {})

    def test_two_profile_opens_count_twice(self):
        pb = self.pb([Step(id="s1", kind="open", url="https://www.linkedin.com/in/a"),
                      Step(id="s2", kind="open", url="https://www.linkedin.com/in/b")])
        self.assertEqual(sl.activities(pb, {})["linkedin_profile_view"], 2)

    def test_sensitive_steps_on_linkedin_are_counted(self):
        fp = pbk.Fingerprint(node_name="button", ax_name="Connect")
        pb = self.pb([Step(id="s1", kind="open", url="https://www.linkedin.com/feed/"),
                      Step(id="s2", kind="click", target=fp, ask=True), Step(id="s3", kind="click", target=fp, ask=True)])
        self.assertEqual(sl.activities(pb, {}), {"linkedin_sensitive": 2})

    def test_sensitive_steps_elsewhere_are_not(self):
        fp = pbk.Fingerprint(node_name="button", ax_name="Send")
        pb = self.pb([Step(id="s1", kind="open", url="https://a.com/"), Step(id="s2", kind="click", target=fp, ask=True)])
        self.assertEqual(sl.activities(pb, {}), {})


class Allowances(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "limits.json"
        self.lim = sl.SiteLimits(self.path, rng=Rng())

    def test_new_accounts_start_low_and_ramp_up_weekly_to_a_ceiling(self):
        self.lim.record(VIEW, NOW)                                     # first use: today
        today = NOW.date()
        self.assertEqual(self.lim.cap_today("linkedin_profile_view", today), 50)
        self.assertEqual(self.lim.cap_today("linkedin_profile_view", today + timedelta(days=6)), 50)
        self.assertEqual(self.lim.cap_today("linkedin_profile_view", today + timedelta(days=7)), 60)
        self.assertEqual(self.lim.cap_today("linkedin_profile_view", today + timedelta(days=35)), 100)
        self.assertEqual(self.lim.cap_today("linkedin_profile_view", today + timedelta(days=365)), 150)

    def test_before_first_use_the_starting_amount_applies(self):
        self.assertEqual(self.lim.cap_today("linkedin_profile_view", NOW.date()), 50)
        self.assertEqual(self.lim.cap_today("linkedin_sensitive", NOW.date()), 15)

    def test_the_cap_holds_back_runs_until_tomorrow(self):
        for _ in range(50):
            self.lim.record(VIEW, NOW)
        d = self.lim.check(VIEW, NOW + timedelta(hours=1))
        self.assertFalse(d.ok)
        self.assertEqual(d.wait_until.date(), NOW.date() + timedelta(days=1))
        self.assertIn("50 of 50", d.reason)
        self.assertTrue(self.lim.check(VIEW, NOW + timedelta(days=1, hours=2)).ok)

    def test_a_run_that_would_cross_the_cap_is_held_back(self):
        for _ in range(49):
            self.lim.record(VIEW, NOW)
        self.assertTrue(self.lim.check({"linkedin_profile_view": 1}, NOW + timedelta(hours=1)).ok)
        self.assertFalse(self.lim.check({"linkedin_profile_view": 2}, NOW + timedelta(hours=1)).ok)

    def test_a_decision_says_why_in_a_field_not_in_words(self):
        self.lim.record(VIEW, NOW)
        self.assertEqual(self.lim.check(VIEW, NOW + timedelta(seconds=1)).kind, "gap")
        for _ in range(50):
            self.lim.record(VIEW, NOW)
        self.assertEqual(self.lim.check(VIEW, NOW + timedelta(hours=1)).kind, "cap")
        self.assertEqual(self.lim.check({}, NOW).kind, "")

    def test_a_gap_is_kept_between_runs_on_a_site(self):
        self.lim.record(VIEW, NOW)
        soon = self.lim.check(VIEW, NOW + timedelta(seconds=10))
        self.assertFalse(soon.ok)
        self.assertEqual(soon.wait_until, NOW + timedelta(seconds=60))          # 30 + the largest jitter (30)
        self.assertTrue(self.lim.check(VIEW, NOW + timedelta(seconds=61)).ok)

    def test_the_gap_is_random_within_its_range(self):
        lim = sl.SiteLimits(Path(self._tmp.name) / "r.json", rng=random.Random(1))
        waits = set()
        for _ in range(20):
            lim.record(VIEW, NOW)
            waits.add((datetime.fromisoformat(lim._data["next_ok"]["linkedin"]) - NOW).total_seconds())
        self.assertGreater(len(waits), 5)
        self.assertTrue(all(30 <= w <= 61 for w in waits), waits)

    def test_sharing_a_site_shares_the_gap(self):
        self.lim.record({"linkedin_sensitive": 1}, NOW)
        self.assertFalse(self.lim.check(VIEW, NOW + timedelta(seconds=5)).ok)

    def test_nothing_to_count_nothing_to_wait_for(self):
        self.lim.record({}, NOW)
        self.assertTrue(self.lim.check({}, NOW).ok)
        self.assertFalse(self.path.exists())

    def test_unknown_activities_are_ignored(self):
        self.lim.record({"nope": 5}, NOW)
        self.assertTrue(self.lim.check({"nope": 5}, NOW).ok)

    def test_counts_survive_a_restart_and_old_days_are_dropped(self):
        for _ in range(3):
            self.lim.record(VIEW, NOW)
        self.lim.record(VIEW, NOW - timedelta(days=60))
        again = sl.SiteLimits(self.path)
        self.assertEqual(again.used_today("linkedin_profile_view", NOW.date()), 3)
        self.lim.record(VIEW, NOW + timedelta(minutes=1))
        self.assertNotIn((NOW - timedelta(days=60)).date().isoformat(), json.loads(self.path.read_text())["usage"])

    def test_a_damaged_file_starts_fresh(self):
        self.path.write_text("{nope", encoding="utf-8")
        lim = sl.SiteLimits(self.path)
        self.assertEqual(lim.used_today("linkedin_profile_view", NOW.date()), 0)
        lim.record(VIEW, NOW)
        self.assertEqual(sl.SiteLimits(self.path).used_today("linkedin_profile_view", NOW.date()), 1)

    def test_usage_summary(self):
        self.lim.record(VIEW, NOW)
        s = self.lim.usage_summary(NOW.date())
        self.assertEqual(s["linkedin_profile_view"], {"used": 1, "cap": 50})
        self.assertEqual(s["linkedin_sensitive"], {"used": 0, "cap": 15})


class Configuring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lim = sl.SiteLimits(Path(self._tmp.name) / "limits.json", rng=Rng())

    def test_limits_can_be_changed_and_are_remembered(self):
        self.assertEqual(self.lim.configure({"linkedin_profile_view": {"start": 20, "ceiling": 80}}, {"min": 60, "jitter": 10}), [])
        again = sl.SiteLimits(Path(self._tmp.name) / "limits.json")
        self.assertEqual(again.cap_today("linkedin_profile_view", NOW.date()), 20)
        self.assertEqual(again.gap(), (60, 10))
        self.assertEqual(again.settings()["allowances"]["linkedin_profile_view"]["ceiling"], 80)

    def test_nonsense_is_refused_and_nothing_changes(self):
        for allow in ({"nope": {"start": 1}}, {"linkedin_profile_view": {"start": 0}},
                      {"linkedin_profile_view": {"start": 100, "ceiling": 50}},
                      {"linkedin_profile_view": {"ceiling": 5000}}, {"linkedin_profile_view": {"start": "x"}},
                      {"linkedin_profile_view": {"ramp_per_week": -1}}):
            self.assertTrue(self.lim.configure(allow), allow)
        for gap in ({"min": 1}, {"min": 99999}, {"jitter": -5}, {"min": "x"}):
            self.assertTrue(self.lim.configure(None, gap), gap)
        self.assertEqual(self.lim.cap_today("linkedin_profile_view", NOW.date()), 50)
        self.assertEqual(self.lim.gap(), (30, 30))

    def test_all_or_nothing(self):
        problems = self.lim.configure({"linkedin_profile_view": {"start": 20}, "nope": {}}, {"min": 60})
        self.assertTrue(problems)
        self.assertEqual(self.lim.cap_today("linkedin_profile_view", NOW.date()), 50)
        self.assertEqual(self.lim.gap(), (30, 30))

    def test_a_hand_damaged_setting_falls_back_to_the_default(self):
        self.lim._data["allowances"] = {"linkedin_profile_view": {"start": "banana"}}
        self.assertEqual(self.lim.cap_today("linkedin_profile_view", NOW.date()), 50)
        self.lim._data["gap"] = {"min": "x"}
        self.assertEqual(self.lim.gap(), (30, 30))


if __name__ == "__main__":
    unittest.main()
