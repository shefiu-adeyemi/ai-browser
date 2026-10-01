"""Tests for browser_nav.py (address rules, toolbar controller) and its HTTP endpoints. No browser."""
import os as _os, tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import agent_ui
import browser_nav as bn

OWN = frozenset({"localhost:8000", "127.0.0.1:8000"})


class Normalize(unittest.TestCase):
    def ok(self, text, expected):
        self.assertEqual(bn.normalize(text, OWN), expected, text)

    def bad(self, text):
        with self.assertRaises(bn.BadAddress, msg=text):
            bn.normalize(text, OWN)

    def test_names_get_https_and_local_addresses_get_http(self):
        self.ok("example.com", "https://example.com")
        self.ok("example.com/a?b=1", "https://example.com/a?b=1")
        self.ok("https://example.com/x", "https://example.com/x")
        self.ok("localhost:3000", "http://localhost:3000")
        self.ok("192.168.1.5:8080/p", "http://192.168.1.5:8080/p")

    def test_anything_else_is_a_search(self):
        self.ok("python jobs lagos", bn.SEARCH_URL + "python+jobs+lagos")
        self.ok("what is 2.5 kg in lb", bn.SEARCH_URL + "what+is+2.5+kg+in+lb")

    def test_only_http_and_https_are_opened(self):
        for t in ("javascript:alert(1)", "JAVASCRIPT:alert(1)", "file:///C:/x", "data:text/html,hi",
                  "chrome://settings", "about:blank", "ftp://x.com", "mailto:a@b.co", "view-source:http://a.com"):
            self.bad(t)

    def test_numbers_and_dotted_words_are_searches_not_addresses(self):
        self.ok("3.5", bn.SEARCH_URL + "3.5")
        self.ok("notes.2", bn.SEARCH_URL + "notes.2")
        self.ok("münchen.de", "https://münchen.de")
        self.ok("example.co.uk/a", "https://example.co.uk/a")

    def test_the_apps_own_address_is_refused_in_every_spelling(self):
        for t in ("localhost:8000", "http://localhost:8000/x", "127.0.0.1:8000", "http://[::1]:8000",
                  "http://0.0.0.0:8000", "x.localhost:8000", "http://LOCALHOST:8000"):
            self.bad(t)
        self.ok("localhost:8001", "http://localhost:8001")           # other local ports are fine

    def test_disguised_and_broken_addresses_are_refused(self):
        self.bad("http://user:pw@a.com")
        self.bad("http://google.com@evil.com/")
        self.bad("a.com:99999")
        self.bad("   ")
        self.bad("x" * 3000)


class FakeEvent:
    def __init__(self, name, **kw):
        self.name, self.kw = name, kw

    def __await__(self):
        async def done():
            return self
        return done().__await__()

    async def event_result(self, **kw):
        return None


def fake_session(tabs=2, connected=True):
    events = []

    class Bus:
        def dispatch(self, ev):
            events.append(ev)
            return FakeEvent(type(ev).__name__, **ev.__dict__)
    cdp = SimpleNamespace(session_id="s", cdp_client=SimpleNamespace(send=SimpleNamespace(Page=SimpleNamespace(
        getNavigationHistory=mock.AsyncMock(return_value={"currentIndex": 1, "entries": [1, 2, 3]})))))
    infos = [SimpleNamespace(target_id=f"T{i}", title=f"Tab {i}", url=f"https://s{i}.test/") for i in range(tabs)]
    live = {"targetInfos": [{"targetId": f"T{i}", "title": f"Live {i}", "url": f"https://live{i}.test/"} for i in range(tabs)]}
    root = SimpleNamespace(send=SimpleNamespace(Target=SimpleNamespace(getTargets=mock.AsyncMock(return_value=live))))
    return SimpleNamespace(is_cdp_connected=connected, agent_focus_target_id="T0", event_bus=Bus(), cdp_client=root,
                           get_tabs=mock.AsyncMock(return_value=infos),
                           get_or_create_cdp_session=mock.AsyncMock(return_value=cdp)), events


class Controller(unittest.IsolatedAsyncioTestCase):
    def nav(self, session, free=True):
        return bn.Navigator(lambda: session, OWN, lambda: free)

    async def test_state_reports_tabs_history_and_the_active_page(self):
        s, _ = fake_session()
        st = await self.nav(s).state()
        self.assertEqual((st["ready"], st["can_back"], st["can_forward"], st["active"]), (True, True, True, "T0"))
        # titles and addresses come from the live browser, not browser-use's stale cache
        self.assertEqual((st["url"], st["title"]), ("https://live0.test/", "Live 0"))
        self.assertEqual([t["id"] for t in st["tabs"]], ["T0", "T1"])

    async def test_a_failing_live_lookup_falls_back_to_the_cached_tabs(self):
        s, _ = fake_session()
        s.cdp_client.send.Target.getTargets = mock.AsyncMock(side_effect=RuntimeError("gone"))
        st = await self.nav(s).state()
        self.assertEqual((st["url"], st["title"]), ("https://s0.test/", "Tab 0"))

    async def test_the_history_lookup_can_be_skipped(self):
        s, _ = fake_session()
        st = await self.nav(s).state(with_history=False)
        self.assertEqual((st["can_back"], st["can_forward"]), (False, False))
        s.get_or_create_cdp_session.assert_not_called()
        self.assertTrue(st["ready"])                              # the rest is still reported

    async def test_state_without_a_browser_is_empty_not_an_error(self):
        st = await bn.Navigator(lambda: None, OWN, lambda: True).state()
        self.assertEqual((st["ready"], st["tabs"]), (False, []))

    async def test_go_normalises_then_dispatches(self):
        s, events = fake_session()
        self.assertEqual(await self.nav(s).go("example.com"), "https://example.com")
        self.assertEqual((type(events[-1]).__name__, events[-1].url, events[-1].new_tab),
                         ("NavigateToUrlEvent", "https://example.com", False))

    async def test_bad_addresses_dispatch_nothing(self):
        s, events = fake_session()
        with self.assertRaises(bn.BadAddress):
            await self.nav(s).go("javascript:alert(1)")
        self.assertEqual(events, [])

    async def test_back_forward_reload_and_new_tab(self):
        s, events = fake_session()
        n = self.nav(s)
        await n.back(); await n.forward(); await n.reload(); await n.new_tab()
        self.assertEqual([type(e).__name__ for e in events],
                         ["GoBackEvent", "GoForwardEvent", "RefreshEvent", "NavigateToUrlEvent"])
        self.assertTrue(events[-1].new_tab)

    async def test_switch_and_close_check_the_tab_exists(self):
        s, events = fake_session()
        n = self.nav(s)
        await n.switch("T1")
        self.assertEqual(type(events[-1]).__name__, "SwitchTabEvent")
        await n.close("T1")
        self.assertEqual(type(events[-1]).__name__, "CloseTabEvent")
        for call in (n.switch, n.close):
            with self.assertRaises(bn.BadAddress):
                await call("NOPE")

    async def test_the_last_tab_cannot_be_closed(self):
        s, events = fake_session(tabs=1)
        with self.assertRaises(bn.BadAddress):
            await self.nav(s).close("T0")
        self.assertEqual(events, [])

    async def test_while_the_agent_works_every_action_is_refused(self):
        s, events = fake_session()
        n = self.nav(s, free=False)
        for call, args in ((n.go, ("a.com",)), (n.back, ()), (n.forward, ()), (n.reload, ()),
                           (n.new_tab, ()), (n.switch, ("T1",)), (n.close, ("T1",))):
            with self.assertRaises(bn.Busy):
                await call(*args)
        self.assertEqual(events, [])
        self.assertTrue((await n.state())["ready"])         # looking is always allowed

    async def test_no_browser_means_a_clear_error(self):
        with self.assertRaises(RuntimeError):
            await bn.Navigator(lambda: None, OWN, lambda: True).reload()


class Policy(unittest.TestCase):
    """_may_browse: idle, or waiting only on a hand-off. Never working or waiting on an approval."""
    def check(self, running, paused, pending):
        with mock.patch.object(agent_ui._ctl, "running", running), mock.patch.object(agent_ui._ctl, "paused", paused), \
                mock.patch.object(agent_ui._broker, "snapshot", return_value=pending):
            return agent_ui._may_browse()

    def test_idle_is_free(self):
        self.assertTrue(self.check(False, False, []))

    def test_working_is_locked(self):
        self.assertFalse(self.check(True, False, []))

    def test_paused_is_locked(self):
        self.assertFalse(self.check(False, True, []))

    def test_an_approval_prompt_locks_it(self):
        self.assertFalse(self.check(True, False, [{"kind": "approve"}]))
        self.assertFalse(self.check(True, False, [{"kind": "handoff"}, {"kind": "approve"}]))

    def test_a_hand_off_unlocks_it(self):
        self.assertTrue(self.check(True, False, [{"kind": "handoff"}]))


class Endpoints(unittest.TestCase):
    def client(self):
        from fastapi.testclient import TestClient
        c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        c.cookies.set(agent_ui._COOKIE_NAME, agent_ui._AUTH_TOKEN)
        return c

    def run_app(self, fn):
        with mock.patch.object(agent_ui, "_startup", mock.AsyncMock()), mock.patch.object(agent_ui, "_shutdown", mock.AsyncMock()):
            with self.client() as c:
                return fn(c)

    def test_state_and_actions_over_http(self):
        s, events = fake_session()
        with mock.patch.object(agent_ui, "_session", s), mock.patch.object(agent_ui, "_may_browse", return_value=True):
            def go(c):
                st = c.get("/browser/state").json()
                self.assertEqual((st["ready"], st["locked"]), (True, False))
                r = c.post("/browser/go", json={"text": "example.com"})
                self.assertEqual((r.status_code, r.json()), (200, {"ok": True, "url": "https://example.com"}))
                self.assertEqual(c.post("/browser/back").json(), {"ok": True})
                bad = c.post("/browser/go", json={"text": "file:///etc/passwd"})
                self.assertEqual(bad.status_code, 400)
                own = c.post("/browser/go", json={"text": "localhost:8000"})
                self.assertEqual(own.status_code, 400)
                self.assertEqual(c.post("/browser/tabs/switch", json={"id": "T1"}).json(), {"ok": True})
                self.assertEqual(c.post("/browser/tabs/close", json={"id": "ZZ"}).status_code, 400)
            self.run_app(go)

    def test_state_is_reused_for_a_second_and_dropped_after_an_action(self):
        s, events = fake_session()
        agent_ui._state_cache.update(at=0.0, locked=None, value=None)
        with mock.patch.object(agent_ui, "_session", s), mock.patch.object(agent_ui, "_may_browse", return_value=True):
            def go(c):
                c.get("/browser/state"); c.get("/browser/state"); c.get("/browser/state")
                self.assertEqual(s.get_tabs.await_count, 1)             # three polls, one set of lookups
                c.post("/browser/reload")
                c.get("/browser/state")
                self.assertEqual(s.get_tabs.await_count, 2)             # an action forces a fresh look
            self.run_app(go)

    def test_while_locked_the_history_lookup_is_not_made(self):
        s, _ = fake_session()
        agent_ui._state_cache.update(at=0.0, locked=None, value=None)
        with mock.patch.object(agent_ui, "_session", s), mock.patch.object(agent_ui, "_may_browse", return_value=False):
            def go(c):
                st = c.get("/browser/state").json()
                self.assertEqual((st["locked"], st["can_back"]), (True, False))
                s.get_or_create_cdp_session.assert_not_called()
            self.run_app(go)

    def test_locked_while_working_is_a_409_with_the_reason(self):
        s, events = fake_session()
        with mock.patch.object(agent_ui, "_session", s), mock.patch.object(agent_ui, "_may_browse", return_value=False):
            def go(c):
                r = c.post("/browser/go", json={"text": "example.com"})
                self.assertEqual(r.status_code, 409)
                self.assertTrue(r.json()["busy"])
                self.assertIn("Stop it first", r.json()["reason"])
                self.assertTrue(c.get("/browser/state").json()["locked"])
            self.run_app(go)
        self.assertEqual(events, [])

    def test_without_the_session_cookie_nothing_works(self):
        from fastapi.testclient import TestClient
        with mock.patch.object(agent_ui, "_startup", mock.AsyncMock()), mock.patch.object(agent_ui, "_shutdown", mock.AsyncMock()):
            with TestClient(agent_ui.app, base_url="http://localhost:8000") as c:
                for method, path in (("get", "/browser/state"), ("post", "/browser/go"), ("post", "/browser/back")):
                    r = getattr(c, method)(path, **({"json": {"text": "a.com"}} if path.endswith("go") else {}))
                    self.assertEqual(r.status_code, 401, path)

    def test_a_foreign_origin_is_refused(self):
        s, events = fake_session()
        with mock.patch.object(agent_ui, "_session", s), mock.patch.object(agent_ui, "_may_browse", return_value=True):
            def go(c):
                r = c.post("/browser/go", json={"text": "example.com"}, headers={"Origin": "http://evil.test"})
                self.assertEqual(r.status_code, 403)
            self.run_app(go)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
