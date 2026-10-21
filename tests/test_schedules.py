"""Tests for schedules.py: timing rules and what may be scheduled."""

import unittest
from datetime import datetime

from aiba.playbooks import model as pbk
from aiba.scheduling import schedules as sch
from aiba.playbooks.model import Check, Playbook, Step, Variable
from aiba.scheduling.schedules import Cadence, Schedule
from pydantic import ValidationError

NOW = datetime(2026, 10, 1, 12, 0, 0)          # a Thursday


def approved_pb(steps=None, variables=None, approve=True):
    pb = pbk.refresh(Playbook(
        name="Open a profile",
        variables=variables if variables is not None else [Variable(name="p", type="linkedin_url")],
        steps=steps or [Step(id="s1", kind="open", url="{{p}}", check=Check(hosts=["www.linkedin.com"]))]))
    pb = pb.model_copy(update={"id": "pb_" + "a" * 12})
    return pbk.with_approval(pb) if approve else pb


def make(**kw):
    base = dict(name="Daily profile", playbook_id="pb_" + "a" * 12,
                items=[{"p": "https://www.linkedin.com/in/jane"}])
    base.update(kw)
    return Schedule(**base)


class NextRun(unittest.TestCase):
    def test_daily_later_today_or_tomorrow(self):
        self.assertEqual(sch.next_run(Cadence(kind="daily", at="15:30"), NOW), datetime(2026, 10, 1, 15, 30))
        self.assertEqual(sch.next_run(Cadence(kind="daily", at="09:00"), NOW), datetime(2026, 10, 2, 9, 0))

    def test_daily_at_exactly_now_is_tomorrow(self):
        self.assertEqual(sch.next_run(Cadence(kind="daily", at="12:00"), NOW), datetime(2026, 10, 2, 12, 0))

    def test_seconds_do_not_cause_a_double_fire(self):
        t = datetime(2026, 10, 1, 9, 0, 0, 500000)
        self.assertEqual(sch.next_run(Cadence(kind="daily", at="09:00"), t), datetime(2026, 10, 2, 9, 0))

    def test_weekly_picks_the_next_chosen_day(self):
        c = Cadence(kind="weekly", at="08:00", weekdays=[0, 4])                  # Mon, Fri
        self.assertEqual(sch.next_run(c, NOW), datetime(2026, 10, 2, 8, 0))     # Friday
        self.assertEqual(sch.next_run(c, datetime(2026, 10, 2, 8, 0)), datetime(2026, 10, 5, 8, 0))   # Monday
        only_thu = Cadence(kind="weekly", at="08:00", weekdays=[3])
        self.assertEqual(sch.next_run(only_thu, NOW), datetime(2026, 10, 8, 8, 0))   # a week on, not today

    def test_weekly_with_no_days_never_runs(self):
        self.assertIsNone(sch.next_run(Cadence(kind="weekly", at="08:00", weekdays=[]), NOW))

    def test_interval(self):
        self.assertEqual(sch.next_run(Cadence(kind="interval", every_minutes=90), NOW), datetime(2026, 10, 1, 13, 30))

    def test_once(self):
        c = Cadence(kind="once", run_at="2026-10-03T07:15:00")
        self.assertEqual(sch.next_run(c, NOW), datetime(2026, 10, 3, 7, 15))
        self.assertIsNone(sch.next_run(c, datetime(2026, 10, 3, 7, 15)))         # already happened
        self.assertIsNone(sch.next_run(Cadence(kind="once", run_at="garbage"), NOW))

    def test_month_and_year_roll_over(self):
        self.assertEqual(sch.next_run(Cadence(kind="daily", at="01:00"), datetime(2026, 12, 31, 23, 0)),
                         datetime(2027, 1, 1, 1, 0))

    def test_a_bad_time_never_runs(self):
        self.assertIsNone(sch.next_run(Cadence(kind="daily", at="25:99"), NOW))
        self.assertIsNone(sch.next_run(Cadence(kind="daily"), NOW))

    def test_iso_helpers(self):
        self.assertEqual(sch.parse_iso("2026-10-01T09:00:00"), datetime(2026, 10, 1, 9, 0))
        self.assertEqual(sch.parse_iso("2026-10-01T09:00:00+02:00"), datetime(2026, 10, 1, 9, 0))   # zone dropped
        for bad in (None, "", "nope"):
            self.assertIsNone(sch.parse_iso(bad))
        self.assertEqual(sch.iso(datetime(2026, 1, 2, 3, 4, 5, 999)), "2026-01-02T03:04:05")
        self.assertIsNone(sch.iso(None))


class Quiet(unittest.TestCase):
    def q(self, start, end, enabled=True):
        return sch.QuietHours(enabled=enabled, start=start, end=end)

    def test_a_daytime_window(self):
        q = self.q("13:00", "15:00")
        for hhmm, expected in (("12:59", False), ("13:00", True), ("14:30", True), ("14:59", True), ("15:00", False)):
            h, m = map(int, hhmm.split(":"))
            self.assertEqual(sch.in_quiet_hours(q, datetime(2026, 10, 1, h, m)), expected, hhmm)

    def test_a_window_that_runs_past_midnight(self):
        q = self.q("22:00", "07:00")
        for hhmm, expected in (("21:59", False), ("22:00", True), ("23:30", True), ("00:00", True), ("03:15", True),
                               ("06:59", True), ("07:00", False), ("12:00", False)):
            h, m = map(int, hhmm.split(":"))
            self.assertEqual(sch.in_quiet_hours(q, datetime(2026, 10, 1, h, m)), expected, hhmm)

    def test_off_or_nonsense_is_never_quiet(self):
        self.assertFalse(sch.in_quiet_hours(self.q("22:00", "07:00", enabled=False), datetime(2026, 10, 1, 23, 0)))
        self.assertFalse(sch.in_quiet_hours(self.q("late", "early"), datetime(2026, 10, 1, 23, 0)))
        self.assertFalse(sch.in_quiet_hours(self.q("08:00", "08:00"), datetime(2026, 10, 1, 8, 0)))

    def test_problems(self):
        self.assertEqual(sch.quiet_problems(self.q("22:00", "07:00")), [])
        self.assertTrue(sch.quiet_problems(self.q("25:00", "07:00")))
        self.assertTrue(sch.quiet_problems(self.q("22:00", "7am")))
        self.assertTrue(any("same time" in p for p in sch.quiet_problems(self.q("08:00", "08:00"))))
        with self.assertRaises(ValidationError):
            sch.QuietHours(enabled=True, start="22:00", end="07:00", note="x")


class Describing(unittest.TestCase):
    def test_in_words(self):
        self.assertEqual(sch.describe(Cadence(kind="daily", at="09:00")), "Every day at 09:00")
        self.assertEqual(sch.describe(Cadence(kind="weekly", at="08:30", weekdays=[4, 0])), "Mon, Fri at 08:30")
        self.assertEqual(sch.describe(Cadence(kind="interval", every_minutes=120)), "Every 2 h")
        self.assertEqual(sch.describe(Cadence(kind="interval", every_minutes=45)), "Every 45 min")
        self.assertEqual(sch.describe(Cadence(kind="once", run_at="2026-10-03T07:15:00")), "Once, Oct 03 at 07:15")


class CadenceRules(unittest.TestCase):
    def test_each_kind_needs_its_own_fields(self):
        bad = [Cadence(kind="interval"), Cadence(kind="daily"), Cadence(kind="daily", at="9am"),
               Cadence(kind="weekly", at="09:00"), Cadence(kind="weekly", at="09:00", weekdays=[7]),
               Cadence(kind="once"), Cadence(kind="once", run_at="2026-10-01T11:00:00")]
        for c in bad:
            self.assertTrue(sch.cadence_problems(c, NOW), c)
        good = [Cadence(kind="interval", every_minutes=60), Cadence(kind="daily", at="09:00"),
                Cadence(kind="weekly", at="09:00", weekdays=[1, 2]), Cadence(kind="once", run_at="2026-10-02T11:00:00")]
        for c in good:
            self.assertEqual(sch.cadence_problems(c, NOW), [], c)

    def test_fields_of_other_kinds_are_refused(self):
        self.assertTrue(sch.cadence_problems(Cadence(kind="daily", at="09:00", every_minutes=60), NOW))
        self.assertTrue(sch.cadence_problems(Cadence(kind="interval", every_minutes=60, at="09:00"), NOW))
        self.assertTrue(sch.cadence_problems(Cadence(kind="daily", at="09:00", weekdays=[1]), NOW))

    def test_the_interval_has_limits(self):
        with self.assertRaises(ValidationError):
            Cadence(kind="interval", every_minutes=1)                  # too often
        with self.assertRaises(ValidationError):
            Cadence(kind="interval", every_minutes=sch.MAX_INTERVAL_MINUTES + 1)

    def test_unknown_fields_are_refused(self):
        with self.assertRaises(ValidationError):
            Cadence(kind="daily", at="09:00", cron="* * * * *")


class Scheduling(unittest.TestCase):
    def test_a_good_schedule_has_no_problems(self):
        self.assertEqual(sch.validate_schedule(make(), approved_pb(), NOW), [])

    def test_the_playbook_must_be_approved(self):
        probs = sch.validate_schedule(make(), approved_pb(approve=False), NOW)
        self.assertTrue(any("Approve the playbook" in p for p in probs))

    def test_an_edited_playbook_is_no_longer_approved(self):
        pb = approved_pb()
        edited = pb.model_copy(update={"steps": [pb.steps[0].model_copy(update={"new_tab": True})]})
        self.assertTrue(sch.validate_schedule(make(), edited, NOW))

    def test_sensitive_steps_need_a_second_yes(self):
        fp = pbk.Fingerprint(node_name="button", ax_name="Send")
        pb = approved_pb(steps=[Step(id="s1", kind="open", url="https://a.com/"),
                                Step(id="s2", kind="click", target=fp, ask=True)], variables=[])
        probs = sch.validate_schedule(make(items=[{}]), pb, NOW)
        self.assertTrue(any("Step 2 sends something" in p for p in probs), probs)
        self.assertEqual(sch.validate_schedule(make(items=[{}], allow_sensitive=True), pb, NOW), [])
        # ...but only on top of the playbook's own approval
        self.assertTrue(sch.validate_schedule(make(items=[{}], allow_sensitive=True),
                                              approved_pb(steps=pb.steps, variables=[], approve=False), NOW))

    def test_a_send_that_asks_you_first_needs_no_blanket_permission(self):
        fp = pbk.Fingerprint(node_name="button", ax_name="Send")
        guarded = approved_pb(steps=[Step(id="s1", kind="open", url="https://a.com/"),
                                     Step(id="s2", kind="approve", message="Send it?"),
                                     Step(id="s3", kind="click", target=fp, ask=True)], variables=[])
        self.assertEqual(sch.validate_schedule(make(items=[{}]), guarded, NOW), [])
        mixed = approved_pb(steps=[Step(id="s1", kind="click", target=fp, ask=True),
                                   Step(id="s2", kind="approve", message="Send it?"),
                                   Step(id="s3", kind="click", target=fp, ask=True)], variables=[])
        probs = sch.validate_schedule(make(items=[{}]), mixed, NOW)
        self.assertTrue(any("Step 1 sends" in p and "ask me first" in p for p in probs), probs)    # only the unguarded one

    def test_steps_that_need_a_person_or_an_ai_are_refused(self):
        pb = approved_pb(steps=[Step(id="s1", kind="open", url="https://a.com/"),
                                Step(id="s2", kind="handoff", message="sign in"),
                                Step(id="s3", kind="ai", tag="ai", query="decide")], variables=[])
        probs = sch.validate_schedule(make(items=[{}]), pb, NOW)
        self.assertTrue(any("Step 2" in p and "by hand" in p for p in probs))
        self.assertTrue(any("Step 3" in p and "AI" in p for p in probs))

    def test_every_row_is_checked_and_named_by_number(self):
        items = [{"p": "https://www.linkedin.com/in/ok"}, {"p": "https://evil.com/in/x"}, {}]
        probs = sch.validate_schedule(make(items=items), approved_pb(), NOW)
        self.assertFalse(any(p.startswith("row 1") for p in probs))
        self.assertTrue(any(p.startswith("row 2") for p in probs))
        self.assertTrue(any(p.startswith("row 3") and "required" in p for p in probs))

    def test_a_playbook_without_inputs_takes_one_empty_row(self):
        pb = approved_pb(steps=[Step(id="s1", kind="open", url="https://a.com/")], variables=[])
        self.assertEqual(sch.validate_schedule(make(items=[{}]), pb, NOW), [])
        self.assertTrue(sch.validate_schedule(make(items=[]), pb, NOW))

    def test_name_playbook_and_cadence(self):
        probs = sch.validate_schedule(make(name="  ", playbook_id="nope",
                                           cadence=Cadence(kind="daily", at="9am")), approved_pb(), NOW)
        text = " ".join(probs)
        for word in ("needs a name", "choose a playbook", "when:"):
            self.assertIn(word, text)

    def test_a_missing_playbook(self):
        self.assertTrue(any("no longer exists" in p for p in sch.validate_schedule(make(), None, NOW)))

    def test_limits_on_rows(self):
        with self.assertRaises(ValidationError):
            make(items=[{}] * (sch.MAX_ITEMS + 1))

    def test_ids(self):
        self.assertTrue(sch.is_valid_id(sch.new_id()))
        for bad in ("../x", "sch_zz", "", None, "sch_" + "a" * 11):
            self.assertFalse(sch.is_valid_id(bad))

    def test_with_next_run(self):
        s = sch.with_next_run(make(cadence=Cadence(kind="daily", at="15:00")), NOW)
        self.assertEqual(s.state.next_run, "2026-10-01T15:00:00")
        done = sch.with_next_run(make(cadence=Cadence(kind="once", run_at="2026-09-01T00:00:00")), NOW)
        self.assertIsNone(done.state.next_run)


if __name__ == "__main__":
    unittest.main()
