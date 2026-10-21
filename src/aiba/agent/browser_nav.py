"""
Browsing by hand in the agent's browser: the address bar, back / forward / reload and the tab list
of the web UI. Pure URL handling plus a thin controller over a browser-use session, so both can be
tested without a browser.

Rules:
  * only http and https addresses are ever opened (never javascript:, file:, data:, chrome:, ...);
  * the app's own address is refused (the agent's browser must never reach this app's control UI);
  * a web address with embedded credentials (user:pass@host) is refused: it is how look-alike links
    are disguised;
  * text that doesn't look like an address is a web search instead.
"""

import os
import re
from typing import Any, Callable
from urllib.parse import quote_plus, urlsplit

MAX_URL = 2048
# Where text that is not an address is searched. Set AIBA_SEARCH_URL to change it (it must end where the
# query goes, e.g. https://duckduckgo.com/?q= ). Google can show a CAPTCHA to automated browsers; if that
# bothers you, DuckDuckGo rarely does.
SEARCH_URL = os.environ.get("AIBA_SEARCH_URL", "").strip() or "https://www.google.com/search?q="
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
# localhost, an IPv4/IPv6 address, or a dotted name whose last part is a real top-level name (so "3.5" or
# "notes.2" are searches, not addresses). \w lets internationalised names like "münchen.de" through.
_HOSTISH = re.compile(r"^(localhost|(\d{1,3}\.){3}\d{1,3}|\[[0-9a-fA-F:]+\]|([\w-]+\.)+[^\W\d_][\w-]+)(:\d{1,5})?([/?#].*)?$")
_LOCAL = re.compile(r"^(localhost|(\d{1,3}\.){3}\d{1,3}|\[[0-9a-fA-F:]+\])(:|/|$)", re.I)


class BadAddress(ValueError):
    """The text can't be opened; the message is meant for the user."""


def normalize(text: str, own_hosts: frozenset[str] = frozenset()) -> str:
    """The URL to open for what the user typed, or raise BadAddress."""
    text = " ".join(str(text).split())
    if not text:
        raise BadAddress("Type an address or a search.")
    if len(text) > MAX_URL:
        raise BadAddress("That address is too long.")

    if _SCHEME.match(text) and not re.match(r"^[A-Za-z0-9.-]+:\d{1,5}([/?#]|$)", text):
        # Has a scheme (http:, https:, javascript:, file: ...). Only http(s):// is allowed.
        if not re.match(r"^https?://", text, re.I):
            raise BadAddress("Only http and https addresses can be opened.")
        url = text
    elif _HOSTISH.match(text):
        # No scheme: https for names, http for IP addresses and localhost (they rarely have TLS).
        url = ("http://" if _LOCAL.match(text) else "https://") + text
    else:
        return SEARCH_URL + quote_plus(text)

    parts = urlsplit(url)
    if not parts.hostname:
        raise BadAddress("That doesn't look like a web address.")
    if parts.username or parts.password:
        raise BadAddress("Addresses with a username or password in them aren't opened.")
    try:
        port = parts.port
    except ValueError:
        raise BadAddress("That port number isn't valid.") from None
    host = parts.hostname.lower().rstrip(".")
    own_ports = {int(h.rsplit(":", 1)[1]) for h in own_hosts if h.rsplit(":", 1)[-1].isdigit()}
    on_this_computer = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".localhost")
    default_port = 443 if parts.scheme.lower() == "https" else 80
    if on_this_computer and (port or default_port) in own_ports:
        raise BadAddress("That is this app's own address; the browser can't open it.")
    return url


class Busy(RuntimeError):
    """The agent is working; browsing by hand now would change the page under it."""


class Navigator:
    """What the UI's browser toolbar does. `get_session` returns the live BrowserSession (or None)."""

    def __init__(self, get_session: Callable[[], Any], own_hosts: frozenset[str],
                 may_browse: Callable[[], bool]) -> None:
        self._get_session = get_session
        self._own_hosts = own_hosts
        self._may_browse = may_browse

    # ---------------------------------------------------------------- helpers
    def _session(self) -> Any:
        s = self._get_session()
        if s is None or not getattr(s, "is_cdp_connected", False):
            raise RuntimeError("The browser isn't running yet.")
        return s

    def _check_free(self) -> None:
        if not self._may_browse():
            raise Busy("The agent is working. Stop it first, then you can browse yourself.")

    @staticmethod
    async def _run(session: Any, event: Any) -> Any:
        done = session.event_bus.dispatch(event)
        await done
        await done.event_result(raise_if_any=True, raise_if_none=False)
        return done

    # ----------------------------------------------------------------- state
    async def state(self, with_history: bool = True) -> dict:
        """Tabs plus whether back / forward are possible, for the toolbar. Read-only: always allowed.
        with_history=False skips the history lookup (the buttons are disabled while the agent works)."""
        s = self._get_session()
        if s is None or not getattr(s, "is_cdp_connected", False):
            return {"ready": False, "tabs": [], "active": None, "can_back": False, "can_forward": False,
                    "url": "", "title": ""}
        active = getattr(s, "agent_focus_target_id", None)
        tabs = []
        try:
            for t in await s.get_tabs():
                tabs.append({"id": t.target_id, "title": (t.title or "")[:120], "url": (t.url or "")[:600]})
        except Exception:
            pass
        try:   # browser-use caches tab titles and never refreshes them; ask the browser for the live ones
            live = await s.cdp_client.send.Target.getTargets()
            by_id = {i["targetId"]: i for i in live.get("targetInfos", [])}
            for t in tabs:
                info = by_id.get(t["id"])
                if info:
                    t["title"] = (info.get("title") or "")[:120]
                    t["url"] = (info.get("url") or t["url"])[:600]
        except Exception:
            pass
        can_back = can_forward = False
        try:
            if not with_history:
                raise LookupError
            cdp = await s.get_or_create_cdp_session(focus=False)
            hist = await cdp.cdp_client.send.Page.getNavigationHistory(session_id=cdp.session_id)
            can_back = hist["currentIndex"] > 0
            can_forward = hist["currentIndex"] < len(hist["entries"]) - 1
        except Exception:
            pass
        here = next((t for t in tabs if t["id"] == active), None) or {"url": "", "title": ""}
        return {"ready": True, "tabs": tabs, "active": active, "can_back": can_back,
                "can_forward": can_forward, "url": here["url"], "title": here["title"]}

    # --------------------------------------------------------------- actions
    async def go(self, text: str, new_tab: bool = False) -> str:
        from browser_use.browser.events import NavigateToUrlEvent
        self._check_free()
        url = normalize(text, self._own_hosts)
        await self._run(self._session(), NavigateToUrlEvent(url=url, new_tab=new_tab))
        return url

    async def back(self) -> None:
        from browser_use.browser.events import GoBackEvent
        self._check_free()
        await self._run(self._session(), GoBackEvent())

    async def forward(self) -> None:
        from browser_use.browser.events import GoForwardEvent
        self._check_free()
        await self._run(self._session(), GoForwardEvent())

    async def reload(self) -> None:
        from browser_use.browser.events import RefreshEvent
        self._check_free()
        await self._run(self._session(), RefreshEvent())

    async def new_tab(self) -> None:
        from browser_use.browser.events import NavigateToUrlEvent
        self._check_free()
        await self._run(self._session(), NavigateToUrlEvent(url="about:blank", new_tab=True))

    async def switch(self, target_id: str) -> None:
        from browser_use.browser.events import SwitchTabEvent
        self._check_free()
        s = self._session()
        if target_id not in {t.target_id for t in await s.get_tabs()}:
            raise BadAddress("That tab no longer exists.")
        await self._run(s, SwitchTabEvent(target_id=target_id))

    async def close(self, target_id: str) -> None:
        from browser_use.browser.events import CloseTabEvent
        self._check_free()
        s = self._session()
        tabs = await s.get_tabs()
        if target_id not in {t.target_id for t in tabs}:
            raise BadAddress("That tab no longer exists.")
        if len(tabs) <= 1:
            raise BadAddress("The last tab can't be closed.")
        await self._run(s, CloseTabEvent(target_id=target_id))
