"""Tests for playbook_player.py with a fake page (no browser)."""

from approvals import Typed
import asyncio
import unittest
from types import SimpleNamespace as NS

import playbook as pbk
from playbook import Check, Fingerprint, Playbook, Step, Variable
from playbook_match import Candidate
from playbook_player import PlaybookPlayer, StepApprover, summarize
import playbook_scrape as ps


class Clock:
    """Time only moves when something sleeps, so timeouts are tested instantly."""
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    async def sleep(self, s): self.now += s


class Inner:
    """The user answering the guard's questions (attended)."""
    def __init__(self, answer=True, clock=None, thinks=0.0):
        self.answer, self.asked, self.clock, self.thinks = answer, [], clock, thinks
        self.details: list[str] = []
        self.text = "654321"                                # what the user types when asked for a value
        self.texts: list[str] = []
        self.remember = False                               # did the user tick "remember this for next runs"
        self.then = None                                    # what the user does in the browser before answering
    async def ask_text(self, title, detail="", url=""):
        self.texts.append(title)
        if self.text is None:
            return None
        typed = Typed(self.text)
        typed.remember = self.remember
        return typed

    async def ask(self, kind, title, detail="", url=""):
        self.asked.append((kind, title))
        self.details.append(detail)
        if self.clock is not None:
            await self.clock.sleep(self.thinks)             # how long the user took to answer
        if self.then is not None:
            self.then()
        return self.answer


class FakePage:
    def __init__(self, approver, elements=None, url="about:blank"):
        self.approver, self.elements, self.url_ = approver, list(elements or []), url
        self.actions, self.has_llm = [], True
        self.risky: dict[int, str] = {}           # element index -> what the guard would ask
        self.risky_keys: str | None = None        # what the guard would ask before pressing Enter
        self.blocked: set[int] = set()            # element index the guard would block
        self.after: dict[tuple, str] = {}         # (action, index) -> the url the page moves to
        self.redirect: dict[str, str] = {}
        self.missing_once: set[int] = set()       # index reports "not available" the first time
        self.raise_on: str | None = None
        self.extract_text = "Jane Doe, Engineer"
        self.tab_ids = ["AAAA", "BBBB", "CCCC"]
        self.scrape_answers: list = []            # what each read of the page gives (the last one repeats)
        self.scrape_calls: list = []
        self.settled = 0
        self.settle_error: BaseException | None = None

    async def scrape(self, row_selector, fields, max_rows):
        self.scrape_calls.append((row_selector, [dict(f) for f in fields], max_rows))
        answer = self.scrape_answers[0] if len(self.scrape_answers) <= 1 else self.scrape_answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def candidates(self): return list(self.elements)
    async def url(self): return self.url_
    async def tabs(self): return list(self.tab_ids)

    async def settle(self):
        self.settled += 1
        if self.settle_error is not None:
            raise self.settle_error

    async def act(self, name, params):
        if self.raise_on == name:
            raise RuntimeError("driver exploded")
        self.actions.append((name, dict(params)))
        idx = params.get("index")
        if name == "navigate":
            self.url_ = self.redirect.get(params["url"], params["url"])
        if idx in self.missing_once:
            self.missing_once.discard(idx)
            return NS(error=None, extracted_content=f"Element index {idx} not available - page may have changed.")
        if idx in self.blocked:
            return NS(error="Blocked by safety policy: Refused to type a credential. Do not retry this. "
                            "Continue without it or report it.", extracted_content=None)
        enter = name == "send_keys" and "Enter" in params.get("keys", "") and self.risky_keys
        if (idx is not None and idx in self.risky) or enter:
            title = self.risky_keys if enter else self.risky[idx]
            if not await self.approver.ask("approve", title, "", self.url_):
                return NS(error="The user declined this action (or did not answer in time).", extracted_content=None)
        if (name, idx) in self.after:
            self.url_ = self.after[(name, idx)]
        return NS(error=None, extracted_content=self.extract_text if name == "extract" else "ok")


def el(i, ax="Go", node="button", attrs=None, h=None, sh=None, xp=None):
    return Candidate(index=i, node_name=node, attributes=attrs or {}, ax_name=ax, x_path=xp,
                     element_hash=h, stable_hash=sh)


def fp(ax="Go", node="button", attrs=None, h=None, sh=None, xp=None):
    return Fingerprint(node_name=node, attributes=attrs or {}, ax_name=ax, x_path=xp, element_hash=h, stable_hash=sh)


def make_pb(steps, variables=None, approve=False, name="Test run"):
    pb = pbk.refresh(Playbook(name=name, steps=steps, variables=variables or []))
    return pbk.with_approval(pb) if approve else pb


def S(n, kind, **kw):
    kw.setdefault("tag", "ai" if kind in pbk.AI_KINDS else "deterministic")
    return Step(id=f"s{n}", kind=kind, **kw)


class PlayerCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.inner = Inner(True)
        self.msgs: list[str] = []

    def build(self, elements=None, attended=True, url="about:blank", **kw):
        self.approver = StepApprover(self.inner, attended, clock=self.clock)
        self.page = FakePage(self.approver, elements, url)
        async def notify(t): self.msgs.append(t)
        self.player = PlaybookPlayer(self.page, self.approver, notify=notify, settle=0, clock=self.clock,
                                     sleep=self.clock.sleep, **kw)
        return self.page

    async def run_pb(self, pb, values=None):
        return await self.player.run(pb, values)


class Basics(PlayerCase):
    async def test_a_simple_run_resolves_elements_and_fills_inputs(self):
        page = self.build([el(24, "Search", "input", {"id": "q"}, h=1, sh=2), el(25, "Go", h=3, sh=4)])
        pb = make_pb([
            S(1, "open", url="{{site}}"),
            S(2, "type", target=fp("Search", "input", {"id": "q"}, 1, 2), text="{{term}}"),
            S(3, "click", target=fp("Go", h=3, sh=4)),
            S(4, "scroll", down=True, pages=2),
            S(5, "key", keys="Tab"),
            S(6, "back"),
        ], variables=[Variable(name="site", type="url", allowed_hosts=["shop.example.com"]), Variable(name="term")])
        r = await self.run_pb(pb, {"site": "https://shop.example.com/", "term": "red shoes"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.actions, [
            ("navigate", {"url": "https://shop.example.com/", "new_tab": False}),
            ("input", {"index": 24, "text": "red shoes", "clear": True}),
            ("click", {"index": 25}),
            ("scroll", {"down": True, "pages": 2.0}),
            ("send_keys", {"keys": "Tab"}),
            ("go_back", {})])
        self.assertEqual([s.status for s in r.steps], ["ok"] * 6)
        self.assertEqual(r.steps[1].matched, "exact (high)")
        self.assertEqual(self.msgs[0], "▶ Step 1/6: Open a page")

    async def test_secrets_are_passed_through_untouched(self):
        page = self.build([el(1, "Password", "input", {"type": "password"}, h=1)])
        pb = make_pb([S(1, "open", url="https://a.com/login"),
                      S(2, "type", target=fp("Password", "input", {"type": "password"}, 1),
                        text="<secret>pw</secret>")])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.actions[1][1]["text"], "<secret>pw</secret>")

    async def test_select_and_targeted_scroll(self):
        page = self.build([el(7, "Size", "select", {"id": "size"}, h=1)])
        pb = make_pb([S(1, "open", url="https://a.com/"),
                      S(2, "select", target=fp("Size", "select", {"id": "size"}, 1), text="Large"),
                      S(3, "scroll", target=fp("Size", "select", {"id": "size"}, 1), down=False, pages=1)])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.actions[1], ("select_dropdown", {"index": 7, "text": "Large"}))
        self.assertEqual(page.actions[2], ("scroll", {"down": False, "pages": 1.0, "index": 7}))

    async def test_wait_uses_the_clock_and_is_capped(self):
        self.build()
        pb = make_pb([S(1, "wait", seconds=5), S(2, "wait", seconds=300)])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done")
        self.assertEqual(self.clock.now, 305)

    async def test_the_result_reports_timing_and_dict_form(self):
        self.build()
        r = await self.run_pb(make_pb([S(1, "wait", seconds=2)]))
        d = r.to_dict()
        self.assertEqual((d["status"], d["seconds"], len(d["steps"])), ("done", 2.0, 1))
        self.assertEqual(d["steps"][0]["kind"], "wait")


class Refusals(PlayerCase):
    async def test_an_invalid_playbook_does_nothing(self):
        page = self.build()
        r = await self.run_pb(Playbook(name="", steps=[]))
        self.assertEqual(r.status, "failed")
        self.assertIn("can't run", r.error)
        self.assertEqual(page.actions, [])

    async def test_bad_inputs_do_nothing(self):
        page = self.build()
        pb = make_pb([S(1, "open", url="{{p}}")], variables=[Variable(name="p", type="linkedin_url")])
        for values in ({}, {"p": "https://evil.com/in/x"}, {"p": "https://www.linkedin.com/in/a", "zzz": "1"}):
            r = await self.run_pb(pb, values)
            self.assertEqual(r.status, "failed", values)
            self.assertIn("Check the inputs", r.error)
        self.assertEqual(page.actions, [])

    async def test_an_unapproved_site_is_refused_even_if_the_steps_ask_for_it(self):
        # A variable may only point at the sites it was approved for, whatever the stored list says.
        page = self.build()
        pb = make_pb([S(1, "open", url="{{u}}")], variables=[Variable(name="u", type="url", allowed_hosts=["a.com"])])
        pb = pb.model_copy(update={"domains": ["a.com", "evil.org"]})            # hand-edited file
        r = await self.run_pb(pb, {"u": "https://evil.org/"})
        self.assertEqual(r.status, "failed")
        self.assertEqual(page.actions, [])

    async def test_a_hand_edited_site_list_does_not_widen_what_a_run_may_visit(self):
        page = self.build([el(1, "Out", "a", h=1)])
        page.after[("click", 1)] = "https://evil.org/phish"
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Out", "a", h=1))])
        pb = pb.model_copy(update={"domains": ["a.com", "evil.org"]})            # the file says evil.org is fine
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "failed")
        self.assertIn("evil.org", r.error)
        self.assertTrue(r.steps[1].fatal)

    async def test_a_literal_open_outside_the_recomputed_sites_cannot_happen(self):
        page = self.build()
        r = await self.run_pb(make_pb([S(1, "open", url="https://a.com/")]))
        self.assertEqual(r.status, "done")

    async def test_without_an_open_step_the_starting_site_is_the_only_one(self):
        page = self.build([el(1, "Go", h=1)], url="https://a.com/start")
        pb = make_pb([S(1, "click", target=fp("Go", h=1))])
        page.after[("click", 1)] = "https://b.com/"
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "failed")
        self.assertIn("b.com", r.error)

    async def test_leaving_the_approved_sites_stops_the_run(self):
        page = self.build([el(1, "Out", "a", {"href": "https://evil.org"}, h=1), el(2, "Next", h=2)])
        page.after[("click", 1)] = "https://evil.org/phish"
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Out", "a", h=1)),
                      S(3, "click", target=fp("Next", h=2))])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "failed")
        self.assertIn("evil.org", r.error)
        self.assertTrue(r.steps[1].fatal)
        self.assertEqual(len(page.actions), 2)                    # the third step never ran

    async def test_a_recorded_landing_site_is_allowed(self):
        page = self.build([el(1, "Docs", h=1)])
        page.after[("click", 1)] = "https://docs.other.org/start"
        pb = make_pb([S(1, "open", url="https://a.com/"),
                      S(2, "click", target=fp("Docs", h=1), check=Check(hosts=["docs.other.org"]))])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done", r.error)

    async def test_a_non_web_address_cannot_get_through_a_variable(self):
        page = self.build()
        pb = make_pb([S(1, "open", url="{{u}}")], variables=[Variable(name="u", type="url", allowed_hosts=["a.com"])])
        r = await self.run_pb(pb, {"u": "javascript:alert(1)"})
        self.assertEqual(r.status, "failed")
        self.assertEqual(page.actions, [])

    async def test_the_guard_blocking_is_final_and_explained(self):
        page = self.build([el(1, "Name", "input", {"type": "text"}, h=1)])
        page.blocked.add(1)
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "type", target=fp("Name", "input", h=1), text="x"),
                      S(3, "wait", seconds=1)])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "failed")
        self.assertTrue(r.steps[1].fatal)
        self.assertIn("Blocked by safety policy", r.steps[1].detail)
        self.assertNotIn("Do not retry", r.steps[1].detail)
        self.assertEqual(len(r.steps), 2)


class Finding(PlayerCase):
    async def test_a_missing_element_stops_after_the_timeout(self):
        page = self.build([el(1, "Other", h=9)])
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Go", h=1))])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "failed")
        self.assertIn("Couldn't find Go", r.error)
        self.assertGreaterEqual(self.clock.now, 10)
        self.assertEqual([a[0] for a in page.actions], ["navigate"])

    async def test_a_miss_says_where_it_is_and_what_looks_closest(self):
        page = self.build([el(1, "Messaging", h=9), el(2, "Home", h=8)], url="https://a.com/profile")
        r = await self.run_pb(make_pb([S(1, "click", target=fp("Message", h=1))]))
        self.assertEqual(r.status, "failed")
        self.assertIn("Couldn't find Message on the page.", r.error)
        self.assertIn("The page is a.com. The closest elements on it: \"Messaging\".", r.error)

    async def test_a_miss_on_a_page_with_nothing_alike_says_to_check_the_page(self):
        page = self.build([el(1, "Totally different", h=9)], url="https://b.com/")
        r = await self.run_pb(make_pb([S(1, "click", target=fp("Message", h=1))]))
        self.assertIn("nothing on it looks like that element", r.error)
        self.assertIn("b.com", r.error)

    async def test_a_failure_while_hinting_still_reports_the_miss(self):
        self.build([], url="https://a.com/")
        from unittest import mock
        with mock.patch("playbook_player.closest", side_effect=RuntimeError("page closed")):
            r = await self.run_pb(make_pb([S(1, "click", target=fp("Message", h=1))]))
        self.assertEqual(r.status, "failed")
        self.assertIn("Couldn't find Message on the page.", r.error)
        self.assertNotIn("Unexpected error", r.error)

    async def test_an_element_that_appears_late_is_waited_for(self):
        page = self.build([])
        polls = {"n": 0}
        orig = page.candidates
        async def late():
            polls["n"] += 1
            if polls["n"] == 4:
                page.elements = [el(3, "Go", h=1)]
            return await orig()
        page.candidates = late
        r = await self.run_pb(make_pb([S(1, "click", target=fp("Go", h=1))]))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.actions, [("click", {"index": 3})])

    async def test_ambiguity_is_not_guessed(self):
        page = self.build([el(1, "Next"), el(2, "Next")])
        r = await self.run_pb(make_pb([S(1, "click", target=Fingerprint(node_name="button", ax_name="Next"))]))
        self.assertEqual(r.status, "failed")
        self.assertEqual(page.actions, [])

    async def test_a_stale_index_is_re_read_once(self):
        page = self.build([el(5, "Go", h=1)])
        page.missing_once.add(5)
        r = await self.run_pb(make_pb([S(1, "click", target=fp("Go", h=1))]))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(len(page.actions), 2)

    async def test_a_stale_index_twice_fails(self):
        page = self.build([el(5, "Go", h=1)])
        async def always_missing(name, params):
            page.actions.append((name, params))
            return NS(error=None, extracted_content="Element index 5 not available - page may have changed.")
        page.act = always_missing
        r = await self.run_pb(make_pb([S(1, "click", target=fp("Go", h=1))]))
        self.assertEqual(r.status, "failed")
        self.assertEqual(len(page.actions), 2)


class Approvals(PlayerCase):
    def risky_pb(self, approve, **fpk):
        return make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Send", **fpk), ask=True),
                        S(3, "wait", seconds=1)], approve=approve)

    async def test_a_pre_approved_step_with_a_high_confidence_match_runs_without_asking(self):
        page = self.build([el(4, "Send", h=7, sh=8)])
        page.risky[4] = "Click Send?"
        r = await self.run_pb(self.risky_pb(True, h=7, sh=8))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.asked, [])
        self.assertEqual(r.steps[1].approval, "preapproved")

    async def test_a_medium_confidence_match_is_asked_even_if_approved(self):
        page = self.build([el(4, "Send")])                  # only the name matches (medium)
        page.risky[4] = "Click Send?"
        r = await self.run_pb(self.risky_pb(True, h=7, sh=8))
        self.assertEqual(self.inner.asked, [("approve", "Click Send?")])
        self.assertEqual(r.status, "done")
        self.assertEqual(r.steps[1].approval, "asked")

    async def test_a_different_risky_element_is_not_covered_by_the_approval(self):
        page = self.build([el(4, "Delete account", h=99, sh=99), el(5, "Send", h=7, sh=8)])
        page.risky[4] = "Click Delete?"
        pb = make_pb([S(1, "open", url="https://a.com/"),
                      S(2, "click", target=fp("Delete account", h=99, sh=99))], approve=True)   # NOT flagged ask
        r = await self.run_pb(pb)
        self.assertEqual(self.inner.asked, [("approve", "Click Delete?")])      # asked: not pre-approved

    async def test_an_unapproved_playbook_asks_the_user(self):
        page = self.build([el(4, "Send", h=7, sh=8)])
        page.risky[4] = "Click Send?"
        r = await self.run_pb(self.risky_pb(False, h=7, sh=8))
        self.assertEqual(self.inner.asked, [("approve", "Click Send?")])
        self.assertEqual(r.status, "done")

    async def test_the_user_saying_no_stops_the_run(self):
        self.inner.answer = False
        page = self.build([el(4, "Send", h=7, sh=8)])
        page.risky[4] = "Click Send?"
        r = await self.run_pb(self.risky_pb(False, h=7, sh=8))
        self.assertEqual(r.status, "needs_you")
        self.assertIn("didn't approve", r.error)
        self.assertEqual(len(r.steps), 2)

    async def test_unattended_never_asks_and_stops(self):
        page = self.build([el(4, "Send")], attended=False)           # not even a high match
        page.risky[4] = "Click Send?"
        r = await self.run_pb(self.risky_pb(True, h=7, sh=8))
        self.assertEqual(r.status, "needs_you")
        self.assertEqual(self.inner.asked, [])
        self.assertIn("Needs your approval", r.error)

    async def test_unattended_runs_an_approved_high_confidence_step(self):
        page = self.build([el(4, "Send", h=7, sh=8)], attended=False)
        page.risky[4] = "Click Send?"
        r = await self.run_pb(self.risky_pb(True, h=7, sh=8))
        self.assertEqual(r.status, "done", r.error)

    async def test_unattended_and_unapproved_stops_even_for_the_exact_element(self):
        page = self.build([el(4, "Send", h=7, sh=8)], attended=False)
        page.risky[4] = "Click Send?"
        r = await self.run_pb(self.risky_pb(False, h=7, sh=8))
        self.assertEqual(r.status, "needs_you")

    async def test_enter_in_a_message_box_follows_the_same_rules(self):
        page = self.build([el(1, "Message", "div", {"role": "textbox"}, h=1, sh=2)], attended=False)
        page.risky_keys = "Press Enter?"
        steps = lambda: [S(1, "open", url="https://a.com/"),
                         S(2, "type", target=fp("Message", "div", {"role": "textbox"}, 1, 2), text="hi"),
                         S(3, "key", keys="Enter", ask=True)]
        r = await self.run_pb(make_pb(steps(), approve=True))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(r.steps[2].approval, "preapproved")
        page = self.build([el(1, "Message", "div", {"role": "textbox"}, h=1, sh=2)], attended=False)
        page.risky_keys = "Press Enter?"
        r2 = await self.run_pb(make_pb(steps(), approve=False))
        self.assertEqual(r2.status, "needs_you")

    async def test_enter_is_not_pre_approved_unless_the_step_before_it_was_matched_with_certainty(self):
        # the field is found only by its name (medium), so where Enter lands is not certain: ask instead
        page = self.build([el(1, "Message", "div")], attended=True)
        page.risky_keys = "Press Enter?"
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "type", target=fp("Message", "div", h=7, sh=8), text="hi"),
                      S(3, "key", keys="Enter", ask=True)], approve=True)
        r = await self.run_pb(pb)
        self.assertEqual(self.inner.asked, [("approve", "Press Enter?")])
        self.assertEqual(r.steps[2].approval, "asked")

    async def test_enter_straight_after_a_non_element_step_is_not_pre_approved(self):
        page = self.build([], attended=False)
        page.risky_keys = "Press Enter?"
        r = await self.run_pb(make_pb([S(1, "open", url="https://a.com/"), S(2, "key", keys="Enter", ask=True)], approve=True))
        self.assertEqual(r.status, "needs_you")

    async def test_approval_does_not_leak_into_the_next_step(self):
        page = self.build([el(4, "Send", h=7, sh=8), el(5, "Delete", h=1, sh=1)])
        page.risky[4], page.risky[5] = "Click Send?", "Click Delete?"
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Send", h=7, sh=8), ask=True),
                      S(3, "click", target=fp("Delete", h=1, sh=1))], approve=True)
        r = await self.run_pb(pb)
        self.assertEqual(self.inner.asked, [("approve", "Click Delete?")])      # step 3 was NOT pre-approved
        self.assertEqual([s.approval for s in r.steps], [None, "preapproved", "asked"])


class FailurePicture(PlayerCase):
    JPEG = b"\xff\xd8\xff-picture"

    async def test_a_run_that_stops_keeps_a_picture_of_the_page(self):
        page = self.build()
        async def shot(): return self.JPEG
        page.screenshot = shot
        r = await self.run_pb(make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Missing", h=1))]))
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.screenshot, self.JPEG)
        self.assertTrue(r.to_dict()["screenshot"])
        self.assertNotIn(self.JPEG, str(r.to_dict()).encode())                  # bytes are never put in the record itself

    async def test_a_needs_you_stop_also_keeps_one(self):
        page = self.build(attended=False)
        async def shot(): return self.JPEG
        page.screenshot = shot
        page.risky_keys = "Press Enter?"
        r = await self.run_pb(make_pb([S(1, "open", url="https://a.com/"), S(2, "key", keys="Enter", ask=True)]))
        self.assertEqual((r.status, r.screenshot), ("needs_you", self.JPEG))

    async def test_a_finished_run_takes_no_picture(self):
        page = self.build()
        calls = []
        async def shot(): calls.append(1); return self.JPEG
        page.screenshot = shot
        r = await self.run_pb(make_pb([S(1, "wait", seconds=1)]))
        self.assertEqual((r.status, r.screenshot, calls), ("done", None, []))
        self.assertFalse(r.to_dict()["screenshot"])

    async def test_no_picture_support_a_failing_camera_or_a_huge_picture_never_break_the_report(self):
        for behaviour in (None, "boom", "empty", "huge", "text"):
            page = self.build()
            if behaviour == "boom":
                async def shot(): raise RuntimeError("page is gone")
                page.screenshot = shot
            elif behaviour == "empty":
                async def shot(): return b""
                page.screenshot = shot
            elif behaviour == "huge":
                async def shot(): return b"x" * 500_000
                page.screenshot = shot
            elif behaviour == "text":
                async def shot(): return "not bytes"
                page.screenshot = shot
            r = await self.run_pb(make_pb([S(1, "ai", query="decide")]))
            self.assertEqual((r.status, r.screenshot), ("failed", None), behaviour)

    async def test_cancellation_while_taking_the_picture_is_not_swallowed(self):
        page = self.build()
        async def shot(): raise asyncio.CancelledError()
        page.screenshot = shot
        with self.assertRaises(asyncio.CancelledError):
            await self.run_pb(make_pb([S(1, "ai", query="decide")]))


class Checkpoints(PlayerCase):
    """An approve step: the user is asked before the step that follows."""
    JPEG = b"\xff\xd8\xff-picture"

    def send_pb(self, approve=True, extra=()):
        steps = [S(1, "open", url="https://a.com/"),
                 S(2, "type", target=fp("Message", "div", {"role": "textbox"}, 1, 2), text="Hello {{who}}"),
                 S(3, "approve", message="Send to {{who}}?"),
                 S(4, "click", target=fp("Send", h=7, sh=8), ask=True), *extra]
        return make_pb(steps, variables=[Variable(name="who")], approve=approve)

    def page(self, **kw):
        page = self.build([el(1, "Message", "div", {"role": "textbox"}, h=1, sh=2), el(4, "Send", h=7, sh=8)], **kw)
        page.risky[4] = "Click Send?"
        async def shot(): return self.JPEG
        page.screenshot = shot
        return page

    # ------------------------------------------------------------------------ attended
    async def test_the_user_says_yes_and_the_send_goes_ahead_without_a_second_question(self):
        page = self.page()
        r = await self.run_pb(self.send_pb(), {"who": "Jane"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.asked, [("approve", "Send to Jane?")])          # the checkpoint, not the click too
        self.assertEqual(self.inner.details, ['Typed just before: "Hello Jane"'])   # exactly what will be sent
        self.assertEqual(page.actions[-1], ("click", {"index": 4}))
        self.assertEqual([s.approval for s in r.steps], [None, None, "asked", "preapproved"])

    async def test_the_user_says_no_and_nothing_is_sent(self):
        self.inner.answer = False
        page = self.page()
        r = await self.run_pb(self.send_pb(), {"who": "Jane"})
        self.assertEqual(r.status, "needs_you")
        self.assertIn("Not approved (you said no, or nobody answered in time): Send to Jane?", r.error)
        self.assertNotIn(("click", {"index": 4}), page.actions)

    async def test_it_works_for_a_playbook_that_was_never_approved(self):
        page = self.page()
        r = await self.run_pb(self.send_pb(approve=False), {"who": "Jane"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.asked, [("approve", "Send to Jane?")])          # the yes covers the click too

    async def test_a_yes_covers_only_the_next_step(self):
        page = self.build([el(4, "Send", h=7, sh=8), el(5, "Delete", h=1, sh=1)])
        page.risky[4], page.risky[5] = "Click Send?", "Click Delete?"
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "approve", message="Send?"),
                      S(3, "click", target=fp("Send", h=7, sh=8), ask=True),
                      S(4, "click", target=fp("Delete", h=1, sh=1), ask=True)], approve=False)
        r = await self.run_pb(pb)
        self.assertEqual(self.inner.asked, [("approve", "Send?"), ("approve", "Click Delete?")])   # step 4 was not covered
        self.assertEqual(r.status, "done")

    async def test_a_yes_does_not_excuse_an_unsure_match(self):
        page = self.build([el(1, "Message", "div", {"role": "textbox"}, h=1, sh=2), el(4, "Send")])   # only the name matches
        page.risky[4] = "Click Send?"
        r = await self.run_pb(self.send_pb(), {"who": "Jane"})
        self.assertEqual(self.inner.asked, [("approve", "Send to Jane?"), ("approve", "Click Send?")])  # asked AGAIN: not certain
        self.assertEqual(r.steps[3].approval, "asked")

    async def test_enter_after_a_yes_goes_through_only_when_the_field_was_found_with_certainty(self):
        page = self.build([el(1, "Message", "div", {"role": "textbox"}, h=1, sh=2)])
        page.risky_keys = "Press Enter?"
        steps = [S(1, "open", url="https://a.com/"), S(2, "type", target=fp("Message", "div", {"role": "textbox"}, 1, 2), text="hi"),
                 S(3, "approve", message="Send?"), S(4, "key", keys="Enter", ask=True)]
        r = await self.run_pb(make_pb(steps, approve=False))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.asked, [("approve", "Send?")])
        self.assertEqual(r.steps[3].approval, "preapproved")

    # ----------------------------------------------------------------------- nobody there
    async def test_with_nobody_there_the_run_is_parked_with_the_question_and_a_picture(self):
        page = self.page(attended=False)
        r = await self.run_pb(self.send_pb(), {"who": "Jane"})
        self.assertEqual(r.status, "waiting")
        self.assertEqual(self.inner.asked, [])                                        # never asks anyone
        self.assertEqual(r.waiting, {"kind": "approve", "on_timeout": "cancel", "guarded": "s4",
                                     "message": "Send to Jane?", "detail": 'Typed just before: "Hello Jane"',
                                     "url": "https://a.com/", "timeout_minutes": 1440, "step_id": "s3", "index": 2,
                                     "done": []})
        self.assertEqual(r.screenshot, self.JPEG)
        self.assertNotIn(("click", {"index": 4}), page.actions)                       # nothing was sent
        self.assertEqual(len(r.steps), 3)
        self.assertEqual(r.steps[-1].status, "waiting")
        self.assertIn("waiting for you", r.error)

    async def test_the_wait_the_playbook_asks_for_is_passed_on(self):
        self.page(attended=False)
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "approve", message="Go?", timeout_minutes=90), S(3, "wait", seconds=1)])
        self.assertEqual((await self.run_pb(pb)).waiting["timeout_minutes"], 90)

    async def test_after_the_user_says_yes_the_run_carries_on_and_the_send_goes_ahead_unattended(self):
        page = self.page(attended=False)
        r = await self.run_pb(self.send_pb(), {"who": "Jane"})
        self.assertEqual(r.status, "waiting")
        page.actions.clear()
        page.url_ = "about:blank"
        r2 = await self.player.run(self.send_pb(), {"who": "Jane"}, approved={r.waiting["step_id"]})
        self.assertEqual(r2.status, "done", r2.error)
        self.assertEqual(page.actions[-1], ("click", {"index": 4}))
        self.assertEqual(self.inner.asked, [])
        self.assertEqual([s.approval for s in r2.steps], [None, None, "asked", "preapproved"])

    # ------------------------------------------------- starting again must not repeat what was already done
    def two_sends(self):
        return make_pb([S(1, "open", url="https://a.com/"),
                        S(2, "click", target=fp("Connect", h=5, sh=6), ask=True),        # allowed by the playbook's own approval
                        S(3, "approve", message="Send the message?"),
                        S(4, "click", target=fp("Send", h=7, sh=8), ask=True)], approve=True)

    def two_sends_page(self):
        page = self.build([el(2, "Connect", h=5, sh=6), el(4, "Send", h=7, sh=8)], attended=False)
        page.risky[2], page.risky[4] = "Connect?", "Send?"
        return page

    async def test_a_risky_step_that_already_ran_is_remembered_when_the_run_parks(self):
        page = self.two_sends_page()
        r = await self.run_pb(self.two_sends())
        self.assertEqual(r.status, "waiting")
        self.assertEqual(r.waiting["done"], ["s2"])
        self.assertEqual([a for a in page.actions if a[0] == "click"], [("click", {"index": 2})])

    async def test_on_the_resume_that_step_is_skipped_not_repeated_and_the_rest_carries_on(self):
        page = self.two_sends_page()
        pb = self.two_sends()
        first = await self.run_pb(pb)
        page.actions.clear()
        r = await self.player.run(pb, approved={"s3"}, done=set(first.waiting["done"]))
        self.assertEqual(r.status, "done", r.error)
        clicks = [a for a in page.actions if a[0] == "click"]
        self.assertEqual(clicks, [("click", {"index": 4})])                               # Connect was NOT clicked again
        self.assertIn("not repeated", r.steps[1].detail)
        self.assertEqual([a[0] for a in page.actions][:1], ["navigate"])                  # harmless steps simply run again

    async def test_a_second_park_keeps_the_record_of_what_ran_before(self):
        page = self.two_sends_page()
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Connect", h=5, sh=6), ask=True),
                      S(3, "approve", message="First?"), S(4, "click", target=fp("Send", h=7, sh=8), ask=True),
                      S(5, "approve", message="Second?"), S(6, "wait", seconds=1)], approve=True)
        r1 = await self.run_pb(pb)
        self.assertEqual((r1.waiting["step_id"], r1.waiting["done"]), ("s3", ["s2"]))
        r2 = await self.player.run(pb, approved={"s3"}, done=set(r1.waiting["done"]))
        self.assertEqual((r2.waiting["step_id"], r2.waiting["done"]), ("s5", ["s2", "s4"]))
        page.actions.clear()
        r3 = await self.player.run(pb, approved={"s3", "s5"}, done=set(r2.waiting["done"]))
        self.assertEqual(r3.status, "done", r3.error)
        self.assertEqual([a for a in page.actions if a[0] == "click"], [])                # nothing risky repeated

    async def test_only_the_steps_named_are_skipped(self):
        page = self.two_sends_page()
        r = await self.player.run(self.two_sends(), approved={"s3"}, done={"s99"})
        self.assertEqual([a for a in page.actions if a[0] == "click"], [("click", {"index": 2}), ("click", {"index": 4})])

    async def test_a_risky_step_that_failed_is_not_marked_done(self):
        page = self.build([el(4, "Send", h=7, sh=8)], attended=False)
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Missing", h=1), ask=True),
                      S(3, "approve", message="x"), S(4, "wait", seconds=1)], approve=True)
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "failed")                                              # it never reached the checkpoint
        self.assertIsNone(r.waiting)

    async def test_an_earlier_yes_grants_the_next_step_even_without_the_playbooks_own_approval(self):
        # attended, playbook never approved: the yes given to the checkpoint is what lets the send through
        page = self.page(attended=True)
        r = await self.player.run(self.send_pb(approve=False), {"who": "Jane"}, approved={"s3"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.asked, [])                                        # no second question
        self.assertEqual([s.approval for s in r.steps], [None, None, "asked", "preapproved"])
        r2 = await self.player.run(self.send_pb(approve=False), {"who": "Jane"})      # without the answer it WOULD ask
        self.assertEqual(self.inner.asked[0][0], "approve")

    async def test_a_yes_to_the_wrong_checkpoint_changes_nothing(self):
        self.page(attended=False)
        r = await self.player.run(self.send_pb(), {"who": "Jane"}, approved={"s99"})
        self.assertEqual(r.status, "waiting")

    async def test_an_old_yes_does_not_excuse_an_unsure_match_on_the_resume(self):
        page = self.build([el(1, "Message", "div", {"role": "textbox"}, h=1, sh=2), el(4, "Send")], attended=False)
        page.risky[4] = "Click Send?"
        r = await self.player.run(self.send_pb(), {"who": "Jane"}, approved={"s3"})
        self.assertEqual(r.status, "needs_you")                                       # refused: not certain enough
        self.assertEqual(r.steps[3].status, "needs_you")

    async def test_two_checkpoints_are_answered_one_at_a_time(self):
        self.page(attended=False)
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "approve", message="First?"), S(3, "wait", seconds=1),
                      S(4, "approve", message="Second?"), S(5, "wait", seconds=1)])
        r1 = await self.run_pb(pb)
        self.assertEqual(r1.waiting["step_id"], "s2")
        r2 = await self.player.run(pb, approved={"s2"})
        self.assertEqual((r2.status, r2.waiting["step_id"]), ("waiting", "s4"))
        r3 = await self.player.run(pb, approved={"s2", "s4"})
        self.assertEqual(r3.status, "done")

    async def test_secrets_are_never_shown_in_the_question(self):
        page = self.build([el(1, "Password", "input", h=1)], attended=False)
        pb = make_pb([S(1, "open", url="https://a.com/login"),
                      S(2, "type", target=fp("Password", "input", h=1), text="<secret>pw</secret>"),
                      S(3, "approve", message="Log in?"), S(4, "wait", seconds=1)])
        r = await self.run_pb(pb)
        self.assertEqual(r.waiting["detail"], "")
        self.assertNotIn("pw", str(r.waiting))

    async def test_what_was_typed_is_forgotten_once_the_page_moves_on(self):
        self.build([el(1, "Message", "div", {"role": "textbox"}, h=1, sh=2), el(2, "Go", h=3, sh=4)], attended=False)
        pb = make_pb([S(1, "open", url="https://a.com/"),
                      S(2, "type", target=fp("Message", "div", {"role": "textbox"}, 1, 2), text="secret plan"),
                      S(3, "click", target=fp("Go", h=3, sh=4)), S(4, "approve", message="Go on?"), S(5, "wait", seconds=1)])
        self.assertEqual((await self.run_pb(pb)).waiting["detail"], "")

    async def test_a_checkpoint_in_a_playbook_run_without_a_driver_screenshot_still_parks(self):
        page = self.build(attended=False)
        r = await self.run_pb(make_pb([S(1, "approve", message="Go?"), S(2, "wait", seconds=1)]))
        self.assertEqual((r.status, r.screenshot), ("waiting", None))

    async def test_the_summary_says_it_is_waiting(self):
        self.page(attended=False)
        pb = self.send_pb()
        r = await self.run_pb(pb, {"who": "Jane"})
        self.assertIn('is waiting for you: Send to Jane?', summarize(pb, r))
        self.assertEqual(r.to_dict()["waiting"]["step_id"], "s3")

    async def test_the_last_step_is_never_a_checkpoint_so_nothing_is_granted_to_nothing(self):
        self.page(attended=False)
        pb = make_pb([S(1, "approve", message="Go?"), S(2, "wait", seconds=1)])
        r = await self.player.run(pb, approved={"s1"})
        self.assertEqual(r.status, "done")


class InputsDuringTheRun(PlayerCase):
    JPEG = b"\xff\xd8\xff-picture"

    def code_pb(self, vtype="text", approve=True):
        return make_pb([S(1, "open", url="https://a.com/form"),
                        S(2, "input", message="What is the code for {{who}}?", variable="code", timeout_minutes=30),
                        S(3, "type", target=fp("Code", "input", h=1, sh=2), text="{{code}}"),
                        S(4, "wait", seconds=1)],
                       variables=[Variable(name="who"), Variable(name="code", type=vtype, runtime=True)], approve=approve)

    def form_page(self, **kw):
        page = self.build([el(1, "Code", "input", h=1, sh=2)], **kw)
        async def shot(): return self.JPEG
        page.screenshot = shot
        return page

    async def test_the_user_types_a_value_and_the_rest_of_the_run_uses_it(self):
        page = self.form_page()
        r = await self.run_pb(self.code_pb(), {"who": "Jane"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.texts, ["What is the code for Jane?"])
        self.assertEqual(page.actions[1], ("input", {"index": 1, "text": "654321", "clear": True}))
        self.assertEqual(r.steps[1].approval, "asked")

    async def test_a_remembered_answer_is_handed_over_to_be_kept(self):
        kept = []
        self.form_page()
        self.player._remember = lambda name, value: kept.append((name, value))
        self.inner.remember = True
        r = await self.run_pb(self.code_pb(), {"who": "Jane"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(kept, [("code", "654321")])

    async def test_an_answer_is_not_kept_unless_the_user_asked(self):
        kept = []
        self.form_page()
        self.player._remember = lambda name, value: kept.append((name, value))
        await self.run_pb(self.code_pb(), {"who": "Jane"})
        self.assertEqual(kept, [])

    async def test_a_number_answer_is_still_kept_when_asked(self):
        kept = []
        self.form_page()
        self.player._remember = lambda name, value: kept.append((name, value))
        self.inner.remember, self.inner.text = True, " 42 "
        await self.run_pb(self.code_pb("number"), {"who": "Jane"})
        self.assertEqual(len(kept), 1)

    async def test_failing_to_keep_it_does_not_fail_the_run(self):
        self.form_page()
        def boom(name, value): raise OSError("disk full")
        self.player._remember = boom
        self.inner.remember = True
        self.assertEqual((await self.run_pb(self.code_pb(), {"who": "Jane"})).status, "done")

    async def test_an_answer_that_was_kept_is_used_without_asking(self):
        self.form_page()
        r = await self.player.run(self.code_pb(), {"who": "Jane"}, answers={"s2": "kept-value"})
        self.assertEqual((r.status, self.inner.texts), ("done", []))

    async def test_the_typed_value_is_never_written_to_the_report(self):
        self.form_page()
        r = await self.run_pb(self.code_pb(), {"who": "Jane"})
        self.assertNotIn("654321", str(r.to_dict()))
        self.assertNotIn("654321", " ".join(self.msgs))

    async def test_no_answer_stops_the_run(self):
        self.inner.text = None
        page = self.form_page()
        r = await self.run_pb(self.code_pb(), {"who": "Jane"})
        self.assertEqual(r.status, "needs_you")
        self.assertIn("No answer was given", r.error)
        self.assertEqual(len(page.actions), 1)

    async def test_a_number_is_checked(self):
        self.inner.text = "twelve"
        self.form_page()
        r = await self.run_pb(self.code_pb("number"), {"who": "Jane"})
        self.assertEqual(r.status, "failed")
        self.assertIn("That answer must be a number", r.error)
        self.inner.text = " 42 "
        self.form_page()
        self.assertEqual((await self.run_pb(self.code_pb("number"), {"who": "Jane"})).status, "done")

    async def test_a_secret_placeholder_cannot_be_typed_in_as_the_answer(self):
        self.inner.text = "<secret>linkedin_password</secret>"
        self.form_page()
        r = await self.run_pb(self.code_pb(), {"who": "Jane"})
        self.assertEqual(r.status, "failed")

    async def test_a_value_is_only_good_for_one_run(self):
        self.form_page()
        await self.run_pb(self.code_pb(), {"who": "Jane"})
        self.inner.text = None
        r = await self.run_pb(self.code_pb(), {"who": "Jane"})                         # it asks AGAIN, it did not remember
        self.assertEqual(r.status, "needs_you")

    async def test_with_nobody_there_it_parks_and_asks_for_the_value_later(self):
        page = self.form_page(attended=False)
        r = await self.run_pb(self.code_pb(), {"who": "Jane"})
        self.assertEqual(r.status, "waiting")
        self.assertEqual(self.inner.texts, [])
        self.assertEqual(r.waiting, {"kind": "input", "message": "What is the code for Jane?", "detail": "", "url": "https://a.com/form",
                                     "variable": "code", "timeout_minutes": 30, "on_timeout": "cancel", "guarded": "",
                                     "step_id": "s2", "index": 1, "done": []})
        self.assertEqual(r.screenshot, self.JPEG)

    async def test_the_answer_given_later_is_used_when_the_run_starts_again(self):
        page = self.form_page(attended=False)
        r = await self.player.run(self.code_pb(), {"who": "Jane"}, answers={"s2": "987654"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.actions[1], ("input", {"index": 1, "text": "987654", "clear": True}))
        self.assertEqual(self.inner.texts, [])

    async def test_a_later_question_does_not_show_the_typed_answer_back(self):
        pb = make_pb([S(1, "open", url="https://a.com/form"),
                      S(2, "input", message="Code?", variable="code"),
                      S(3, "type", target=fp("Code", "input", h=1, sh=2), text="{{code}}"),
                      S(4, "approve", message="Send it?"),
                      S(5, "click", target=fp("Send", h=7, sh=8), ask=True)],
                     variables=[Variable(name="code", runtime=True)], approve=True)
        self.build([el(1, "Code", "input", h=1, sh=2), el(4, "Send", h=7, sh=8)], attended=False)
        r = await self.player.run(pb, {}, answers={"s2": "987654"})
        self.assertEqual(r.status, "waiting")
        self.assertNotIn("987654", str(r.waiting))
        self.assertIn("what you typed in", r.waiting["detail"])

    async def test_an_answer_stored_for_another_step_does_not_count(self):
        self.form_page(attended=False)
        r = await self.player.run(self.code_pb(), {"who": "Jane"}, answers={"s9": "987654"})
        self.assertEqual(r.status, "waiting")

    async def test_a_stored_answer_is_checked_again(self):
        self.form_page(attended=False)
        r = await self.player.run(self.code_pb("number"), {"who": "Jane"}, answers={"s2": "not a number"})
        self.assertEqual(r.status, "failed")


class ScrapeStep(PlayerCase):
    FIELDS = [pbk.ScrapeField(name="Name", selector=".n"), pbk.ScrapeField(name="Price", selector=".p", type="number")]

    def pb(self, **kw):
        step = dict(row_selector="tr.item", fields=self.FIELDS, note="The items")
        step.update(kw)
        return make_pb([S(1, "open", url="https://a.com/"), S(2, "scrape", **step)])

    GOOD = {"total": 2, "rows": [["Ann", "$5.00"], ["Bob", "7"]]}

    async def test_a_table_is_read_without_a_model_and_kept_in_the_results(self):
        page = self.build()
        page.has_llm = False
        page.scrape_answers = [self.GOOD]
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.scrape_calls, [("tr.item", [{"name": "Name", "selector": ".n", "type": "text", "attr": None},
                                                         {"name": "Price", "selector": ".p", "type": "number", "attr": None}], None)])
        (out,) = r.outputs
        self.assertEqual(out["table"]["columns"], ["Name", "Price"])
        self.assertEqual(out["table"]["rows"], [["Ann", "5"], ["Bob", "7"]])
        self.assertEqual((out["step_id"], out["query"], out["text"]), ("s2", "The items", "2 rows read"))
        self.assertNotIn("table", r.to_dict()["steps"][1])                       # the table is stored once, in the outputs

    async def test_it_is_not_an_action_so_nothing_is_asked(self):
        page = self.build(attended=False)
        page.scrape_answers = [self.GOOD]
        r = await self.run_pb(self.pb())
        self.assertEqual((r.status, self.inner.asked), ("done", []))
        self.assertEqual([a[0] for a in page.actions], ["navigate"])             # nothing was clicked or typed

    async def test_the_chat_shows_the_first_rows(self):
        page = self.build()
        page.scrape_answers = [{"total": 8, "rows": [[f"N{i}", str(i)] for i in range(8)]}]
        pb = self.pb()
        r = await self.run_pb(pb)
        text = summarize(pb, r)
        self.assertIn("Name | Price", text)
        self.assertIn("N0 | 0", text)
        self.assertIn("and 3 more", text)

    async def test_a_page_that_is_still_loading_gets_a_few_seconds(self):
        page = self.build()
        page.scrape_answers = [{"total": 0, "rows": []}, {"total": 0, "rows": []}, self.GOOD]
        r = await self.run_pb(self.pb())
        self.assertEqual((r.status, len(page.scrape_calls)), ("done", 3))

    async def test_rows_that_never_appear_fail_and_say_so(self):
        page = self.build()
        page.scrape_answers = [{"total": 0, "rows": []}]
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "failed")
        self.assertIn("No rows were found", r.steps[1].detail)
        self.assertEqual(r.outputs, [])

    async def test_a_selector_that_matches_nothing_names_it(self):
        page = self.build()
        page.scrape_answers = [{"total": 2, "rows": [["Ann", None], ["Bob", None]]}]
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "failed")
        self.assertIn('".p"', r.steps[1].detail)

    async def test_a_bad_selector_or_a_dead_page_is_a_plain_failure(self):
        for err in (ps.ScrapeError("A selector is not valid: x"), asyncio.TimeoutError()):
            page = self.build()
            page.scrape_answers = [err]
            r = await self.run_pb(self.pb())
            self.assertEqual(r.status, "failed")
            self.assertTrue(r.steps[1].detail)

    async def test_a_scrape_after_a_sign_in_wall_is_not_read(self):
        page = self.build(url="https://a.com/")
        page.scrape_answers = [self.GOOD]
        page.redirect["https://a.com/"] = "https://a.com/login"
        r = await self.run_pb(self.pb())
        self.assertNotEqual(r.status, "done")

    async def test_the_run_stops_if_the_page_left_the_approved_sites(self):
        page = self.build()
        page.scrape_answers = [self.GOOD]
        page.redirect["https://a.com/"] = "https://evil.com/"
        r = await self.run_pb(self.pb())
        self.assertEqual(r.status, "failed")
        self.assertIn("not approved to visit", str(r.error) + " ".join(s.detail for s in r.steps))


class SkipWhenUnanswered(PlayerCase):
    def pb(self):
        return make_pb([S(1, "open", url="https://a.com/"), S(2, "approve", message="Send it?", on_timeout="skip"),
                        S(3, "click", target=fp("Send", h=7, sh=8), ask=True), S(4, "wait", seconds=1)], approve=True)

    async def test_the_question_says_what_happens_without_an_answer(self):
        page = self.build([el(4, "Send", h=7, sh=8)], attended=False)
        r = await self.run_pb(self.pb())
        self.assertEqual((r.waiting["on_timeout"], r.waiting["guarded"]), ("skip", "s3"))

    async def test_when_nobody_answers_the_step_is_left_out_and_the_rest_carries_on(self):
        page = self.build([el(4, "Send", h=7, sh=8)], attended=False)
        page.risky[4] = "Send?"
        r = await self.player.run(self.pb(), approved={"s2"}, skipped={"s3"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual([a for a in page.actions if a[0] == "click"], [])             # nothing was sent
        self.assertIn("nobody answered", r.steps[2].detail)
        self.assertEqual(r.steps[3].status, "ok")                                      # the wait after it still ran

    async def test_a_later_failure_says_a_step_was_left_out_which_may_be_why(self):
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "approve", message="Send it?", on_timeout="skip"),
                      S(3, "click", target=fp("Send", h=7, sh=8), ask=True),
                      S(4, "click", target=fp("Sent confirmation", h=9, sh=10))], approve=True)
        self.build([el(4, "Send", h=7, sh=8)], attended=False)
        r = await self.player.run(pb, approved={"s2"}, skipped={"s3"})
        self.assertNotEqual(r.status, "done")
        self.assertIn("left out because nobody answered", r.steps[3].detail)

    async def test_a_failure_with_nothing_skipped_has_no_such_note(self):
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Missing", h=9, sh=10))], approve=True)
        self.build([el(4, "Send", h=7, sh=8)], attended=False)
        r = await self.player.run(pb)
        self.assertNotIn("left out", r.steps[1].detail)

    async def test_only_the_named_step_is_skipped(self):
        page = self.build([el(4, "Send", h=7, sh=8)], attended=False)
        r = await self.player.run(self.pb(), approved={"s2"}, skipped={"s99"})
        self.assertEqual([a for a in page.actions if a[0] == "click"], [("click", {"index": 4})])


class Timing(PlayerCase):
    async def test_time_spent_waiting_for_the_user_is_not_counted(self):
        page = self.build([el(4, "Send")], max_seconds=10)
        page.risky[4] = "Click Send?"
        self.inner.clock, self.inner.thinks = self.clock, 500       # the user took ~8 minutes to answer
        pb = make_pb([S(1, "wait", seconds=6), S(2, "click", target=Fingerprint(node_name="button", ax_name="Send"), ask=True),
                      S(3, "wait", seconds=3)])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done", r.error)

    async def test_the_open_guard_refuses_a_site_outside_the_approved_list_before_navigating(self):
        # defence in depth: reachable only if the allowed list and the steps ever disagree
        page = self.build()
        self.player._allowed, self.player._values, self.player._variables = ["a.com"], {}, {}
        res = await self.player._open(S(1, "open", url="https://b.com/"))
        self.assertEqual((res.status, res.fatal), ("failed", True))
        self.assertEqual(page.actions, [])
        res = await self.player._open(S(1, "open", url="ftp://a.com/"))
        self.assertEqual((res.status, res.fatal), ("failed", True))

    async def test_without_user_waiting_the_limit_still_applies(self):
        self.build(max_seconds=10)
        r = await self.run_pb(make_pb([S(1, "wait", seconds=6), S(2, "wait", seconds=6), S(3, "wait", seconds=1)]))
        self.assertEqual(r.status, "failed")


class NewSteps(PlayerCase):
    async def test_a_search_goes_to_the_engine_with_the_query_encoded(self):
        page = self.build()
        pb = make_pb([S(1, "search", engine="duckduckgo", query="{{term}}")], variables=[Variable(name="term")])
        r = await self.run_pb(pb, {"term": "red shoes & socks=1"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.actions, [("navigate", {"url": "https://duckduckgo.com/?q=red+shoes+%26+socks%3D1", "new_tab": False})])

    async def test_a_search_value_cannot_change_the_site(self):
        page = self.build()
        pb = make_pb([S(1, "search", engine="bing", query="{{term}}")], variables=[Variable(name="term")])
        await self.run_pb(pb, {"term": "x&redirect=https://evil.org/#"})
        self.assertEqual(urlparse_host(page.actions[0][1]["url"]), "www.bing.com")

    async def test_a_tab_step_switches_by_position(self):
        page = self.build()
        r = await self.run_pb(make_pb([S(1, "tab", tab_index=1), S(2, "tab", tab_index=2, close_tab=True)]))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.actions, [("switch", {"tab_id": "BBBB"}), ("close", {"tab_id": "CCCC"})])

    async def test_a_missing_tab_stops_the_run(self):
        page = self.build()
        r = await self.run_pb(make_pb([S(1, "tab", tab_index=5)]))
        self.assertEqual(r.status, "failed")
        self.assertIn("no tab number 6 (only 3 open)", r.error)
        self.assertEqual(page.actions, [])

    async def test_a_handoff_waits_for_the_user_and_carries_on(self):
        page = self.build()
        r = await self.run_pb(make_pb([S(1, "handoff", message="Solve the CAPTCHA"), S(2, "wait", seconds=1)]))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.asked, [("handoff", "Your turn")])
        self.assertEqual(self.inner.details, ["Solve the CAPTCHA"])
        self.assertEqual(r.steps[0].approval, "asked")

    async def test_a_handoff_the_user_declines_stops_the_run(self):
        self.inner.answer = False
        self.build()
        r = await self.run_pb(make_pb([S(1, "handoff", message="Solve the CAPTCHA"), S(2, "wait", seconds=1)]))
        self.assertEqual(r.status, "needs_you")
        self.assertIn("You didn't continue: Solve the CAPTCHA", r.error)
        self.assertEqual(len(r.steps), 1)

    async def test_an_unattended_handoff_never_asks_and_stops(self):
        self.build(attended=False)
        r = await self.run_pb(make_pb([S(1, "handoff", message="Solve the CAPTCHA")]))
        self.assertEqual(r.status, "needs_you")
        self.assertIn("Needs you: Solve the CAPTCHA", r.error)
        self.assertEqual(self.inner.asked, [])

    async def test_a_handoff_is_never_covered_by_an_approval(self):
        self.inner.answer = False
        self.build()
        r = await self.run_pb(make_pb([S(1, "handoff", message="m")], approve=True))
        self.assertEqual(r.status, "needs_you")                    # approving the playbook did not answer for the user

    async def test_the_user_leaving_the_approved_sites_during_a_handoff_stops_the_run(self):
        page = self.build(url="https://a.com/")
        self.inner.then = lambda: setattr(page, "url_", "https://evil.org/")
        r = await self.run_pb(make_pb([S(1, "open", url="https://a.com/"), S(2, "handoff", message="log in"),
                                       S(3, "wait", seconds=1)]))
        self.assertEqual(r.status, "failed")
        self.assertTrue(r.steps[1].fatal)


class SignInWalls(PlayerCase):
    WALL = "https://www.linkedin.com/authwall?trk=x"

    def profile_pb(self):
        return make_pb([S(1, "open", url="{{p}}"), S(2, "wait", seconds=1)], variables=[Variable(name="p", type="linkedin_url")])

    async def test_the_user_signs_in_and_the_run_carries_on(self):
        page = self.build()
        page.redirect["https://www.linkedin.com/in/jane"] = self.WALL
        self.inner.then = lambda: setattr(page, "url_", "https://www.linkedin.com/in/jane")      # they signed in
        r = await self.run_pb(self.profile_pb(), {"p": "https://www.linkedin.com/in/jane"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.asked, [("handoff", "Sign in needed")])
        self.assertEqual(r.steps[0].approval, "asked")
        self.assertEqual(len(r.steps), 2)

    async def test_pressing_continue_without_signing_in_stops(self):
        page = self.build()
        page.redirect["https://www.linkedin.com/in/jane"] = self.WALL
        r = await self.run_pb(self.profile_pb(), {"p": "https://www.linkedin.com/in/jane"})
        self.assertEqual(r.status, "needs_you")
        self.assertIn("still asking you to sign in", r.error)

    async def test_skipping_the_hand_off_stops(self):
        self.inner.answer = False
        page = self.build()
        page.redirect["https://www.linkedin.com/in/jane"] = self.WALL
        r = await self.run_pb(self.profile_pb(), {"p": "https://www.linkedin.com/in/jane"})
        self.assertEqual(r.status, "needs_you")
        self.assertIn("run again", r.error)

    async def test_unattended_stops_without_asking(self):
        page = self.build(attended=False)
        page.redirect["https://www.linkedin.com/in/jane"] = self.WALL
        r = await self.run_pb(self.profile_pb(), {"p": "https://www.linkedin.com/in/jane"})
        self.assertEqual(r.status, "needs_you")
        self.assertEqual(self.inner.asked, [])

    async def test_a_wall_reached_by_a_click_is_noticed_too(self):
        page = self.build([el(1, "Profile", h=1)])
        page.after[("click", 1)] = "https://a.com/login?next=/x"
        r = await self.run_pb(make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Profile", h=1)),
                                       S(3, "wait", seconds=1)]))
        self.assertEqual(r.status, "needs_you")
        self.assertEqual(len(r.steps), 2)

    async def test_a_playbook_with_a_handoff_step_handles_sign_in_itself(self):
        page = self.build()
        page.redirect["https://a.com/"] = "https://a.com/login"
        r = await self.run_pb(make_pb([S(1, "open", url="https://a.com/"), S(2, "handoff", message="Sign in"),
                                       S(3, "wait", seconds=1)]))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.asked, [("handoff", "Your turn")])          # asked once, by the step, not twice

    async def test_a_playbook_that_signs_in_itself_is_left_alone(self):
        page = self.build([el(1, "Email", "input", h=1)])
        page.redirect["https://a.com/"] = "https://a.com/login"
        r = await self.run_pb(make_pb([S(1, "open", url="https://a.com/", check=Check(hosts=["a.com"])),
                                       S(2, "type", target=fp("Email", "input", h=1), text="<secret>user</secret>")]))
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(self.inner.asked, [])


class Settling(PlayerCase):
    async def test_the_page_is_settled_after_each_action_but_not_after_a_plain_wait(self):
        page = self.build([el(1, "Go", h=1)])
        r = await self.run_pb(make_pb([S(1, "open", url="https://a.com/"), S(2, "click", target=fp("Go", h=1)),
                                       S(3, "back"), S(4, "wait", seconds=1)]))
        self.assertEqual(r.status, "done")
        self.assertEqual(page.settled, 3)

    async def test_a_driver_without_settle_still_works(self):
        page = self.build()
        del type(page).settle
        try:
            r = await self.run_pb(make_pb([S(1, "back")]))
        finally:
            async def settle(self_):
                self_.settled += 1
                if self_.settle_error is not None:
                    raise self_.settle_error
            type(page).settle = settle
        self.assertEqual(r.status, "done")

    async def test_a_failing_settle_does_not_fail_the_step(self):
        page = self.build()
        page.settle_error = RuntimeError("page is gone")
        r = await self.run_pb(make_pb([S(1, "back")]))
        self.assertEqual(r.status, "done")

    async def test_cancellation_during_settle_is_not_swallowed(self):
        page = self.build()
        page.settle_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_pb(make_pb([S(1, "back")]))


def urlparse_host(url):
    from urllib.parse import urlparse
    return urlparse(url).hostname


class OpenChecks(PlayerCase):
    async def test_a_sign_in_wall_needs_you(self):
        page = self.build()
        page.redirect["https://www.linkedin.com/in/jane"] = "https://www.linkedin.com/authwall?trk=x"
        pb = make_pb([S(1, "open", url="{{p}}"), S(2, "wait", seconds=1)],
                     variables=[Variable(name="p", type="linkedin_url")])
        r = await self.run_pb(pb, {"p": "https://www.linkedin.com/in/jane"})
        self.assertEqual(r.status, "needs_you")
        self.assertIn("asking you to sign in", r.error)
        self.assertEqual(len(r.steps), 1)

    async def test_ordinary_pages_that_merely_contain_login_words_are_not_stopped(self):
        for path in ("/session-info", "/sso-help", "/sessions", "/loginhelp", "/blog/login-tips", "/signing"):
            page = self.build()
            r = await self.run_pb(make_pb([S(1, "open", url="https://a.com" + path)]))
            self.assertEqual(r.status, "done", path)
        for path in ("/login", "/login/", "/uas/login?x=1", "/checkpoint/challenge", "/authwall?trk=1", "/signin"):
            self.build()
            r = await self.run_pb(make_pb([S(1, "open", url="https://a.com" + path)]))
            self.assertEqual(r.status, "needs_you", path)

    async def test_a_login_playbook_may_open_a_login_page(self):
        page = self.build([el(1, "Email", "input", h=1)])
        pb = make_pb([S(1, "open", url="https://a.com/login"),
                      S(2, "type", target=fp("Email", "input", h=1), text="<secret>user</secret>")])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done", r.error)

    async def test_landing_on_the_wrong_site_fails_the_check(self):
        page = self.build()
        page.redirect["https://a.com/"] = "https://tracker.example.net/"
        pb = make_pb([S(1, "open", url="https://a.com/", check=Check(hosts=["a.com", "tracker.example.net"]))])
        self.assertEqual((await self.run_pb(pb)).status, "done")
        self.build().redirect["https://a.com/"] = "https://elsewhere.org/"
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "failed")

    async def test_a_url_variable_may_land_on_any_of_its_allowed_sites(self):
        page = self.build()
        pb = make_pb([S(1, "open", url="{{u}}", check=Check(hosts=["a.com"]))],
                     variables=[Variable(name="u", type="url", allowed_hosts=["a.com", "b.com"])])
        r = await self.run_pb(pb, {"u": "https://b.com/page"})
        self.assertEqual(r.status, "done", r.error)

    async def test_linkedin_profile_url_end_to_end(self):
        page = self.build()
        pb = make_pb([S(1, "open", url="{{profile_url}}", check=Check(hosts=["www.linkedin.com"]))],
                     variables=[Variable(name="profile_url", type="linkedin_url")], approve=True)
        r = await self.run_pb(pb, {"profile_url": "https://www.linkedin.com/in/jane-doe"})
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(page.actions, [("navigate", {"url": "https://www.linkedin.com/in/jane-doe", "new_tab": False})])
        self.assertEqual(page.url_, "https://www.linkedin.com/in/jane-doe")


class AiSteps(PlayerCase):
    async def test_extract_reads_the_page_and_reports_it(self):
        page = self.build()
        pb = make_pb([S(1, "open", url="https://a.com/"), S(2, "extract", query="the name and title")])
        r = await self.run_pb(pb)
        self.assertEqual(r.status, "done", r.error)
        self.assertEqual(r.outputs, [{"step_id": "s2", "query": "the name and title", "text": "Jane Doe, Engineer"}])
        self.assertIn("Jane Doe, Engineer", summarize(pb, r))
        self.assertEqual(page.actions[1], ("extract", {"query": "the name and title"}))

    async def test_extract_without_a_model_fails_clearly(self):
        page = self.build()
        page.has_llm = False
        r = await self.run_pb(make_pb([S(1, "extract", query="q")]))
        self.assertEqual(r.status, "failed")
        self.assertIn("none is set up", r.error)
        self.assertEqual(page.actions, [])

    async def test_a_long_extract_is_clipped(self):
        page = self.build()
        page.extract_text = "x" * 50000
        r = await self.run_pb(make_pb([S(1, "extract", query="q")]))
        self.assertEqual(len(r.outputs[0]["text"]), 20000)

    async def test_an_ai_step_is_not_guessed_at(self):
        page = self.build()
        r = await self.run_pb(make_pb([S(1, "ai", query="decide what to do"), S(2, "wait", seconds=1)]))
        self.assertEqual(r.status, "failed")
        self.assertIn("needs an AI", r.error)
        self.assertEqual(len(r.steps), 1)

    async def test_extract_query_uses_the_inputs(self):
        page = self.build()
        pb = make_pb([S(1, "extract", query="what does {{who}} do")], variables=[Variable(name="who")])
        await self.run_pb(pb, {"who": "Jane"})
        self.assertEqual(page.actions[0][1]["query"], "what does Jane do")


class Robustness(PlayerCase):
    async def test_a_driver_crash_is_a_failed_step_not_a_crash(self):
        page = self.build()
        page.raise_on = "go_back"
        r = await self.run_pb(make_pb([S(1, "back")]))
        self.assertEqual(r.status, "failed")
        self.assertIn("driver exploded", r.error)

    async def test_cancellation_is_not_swallowed(self):
        page = self.build()
        async def hang(name, params): raise asyncio.CancelledError()
        page.act = hang
        with self.assertRaises(asyncio.CancelledError):
            await self.run_pb(make_pb([S(1, "back")]))

    async def test_a_run_that_takes_too_long_stops(self):
        self.build(max_seconds=10)
        r = await self.run_pb(make_pb([S(1, "wait", seconds=8), S(2, "wait", seconds=8), S(3, "wait", seconds=1)]))
        self.assertEqual(r.status, "failed")
        self.assertIn("took longer", r.error)
        self.assertEqual(len(r.steps), 2)

    async def test_a_page_with_no_url_does_not_break_the_checks(self):
        page = self.build()
        async def broken_url(): raise RuntimeError("no page")
        page.url = broken_url
        r = await self.run_pb(make_pb([S(1, "wait", seconds=1)]))
        self.assertEqual(r.status, "done")

    async def test_the_summary_for_each_outcome(self):
        page = self.build()
        pb = make_pb([S(1, "wait", seconds=1), S(2, "ai", query="x")], name="My run")
        r = await self.run_pb(pb)
        self.assertIn('"My run" stopped after 1 of 2 steps', summarize(pb, r))
        ok = await self.run_pb(make_pb([S(1, "wait", seconds=1)], name="My run"))
        self.assertIn("all 1 step done", summarize(make_pb([S(1, "wait", seconds=1)], name="My run"), ok))
        needs = self.build([el(4, "Send")], attended=False)
        needs.risky[4] = "Click Send?"
        r3 = await self.run_pb(make_pb([S(1, "click", target=fp("Send"), ask=True)]))
        self.assertIn("needs you", summarize(make_pb([S(1, "click", target=fp("Send"), ask=True)]), r3))


class ApproverUnit(unittest.IsolatedAsyncioTestCase):
    async def test_a_handoff_is_never_pre_approved(self):
        inner = Inner(True)
        a = StepApprover(inner, attended=True)
        a.begin(True)
        self.assertTrue(await a.ask("handoff", "Solve it"))
        self.assertEqual(inner.asked, [("handoff", "Solve it")])

    async def test_no_inner_approver_fails_closed(self):
        a = StepApprover(None, attended=True)
        a.begin(False)
        self.assertFalse(await a.ask("approve", "x"))
        self.assertEqual(a.last, "unattended")

    async def test_begin_resets_the_decision(self):
        a = StepApprover(Inner(True), attended=True)
        a.begin(True); await a.ask("approve", "x"); self.assertEqual(a.last, "preapproved")
        a.begin(False); self.assertIsNone(a.last)


if __name__ == "__main__":
    unittest.main()
