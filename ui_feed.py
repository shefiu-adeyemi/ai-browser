"""
What the browser-use log stream is allowed to show in the chat.

The UI used to receive EVERY log line browser-use produced (browser internals, CDP chatter, page
content, typed text) and show them all. Now only the agent's own narration reaches the chat, and
it is cleaned first:

  * allowlist: only the agent and its tools (the lines that explain what it is doing);
    everything else (browser session, event bus, CDP) stays in the server log;
  * secrets are redacted: any stored API key, the LinkedIn password or email, and anything shaped
    like a key, so a credential can't be displayed even if some library logs it;
  * lines are capped and flattened, so a page's text can't flood or restyle the chat;
  * real step markers ("📍 Step 3:") are tagged, so the UI counts steps instead of log lines.

Pure functions plus one small class, no I/O; unit-tested directly.
"""

import logging
import os
import re
from typing import Callable, Iterable, Optional

MAX_LINE = 400
REDACTED = "••••"
_MIN_SECRET_LEN = 6          # don't blank out short common strings

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_STEP = re.compile(r"^\W*Step\s+(\d+)\b")
_KEY_SHAPES = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{25,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}"
    r"|xox[baprs]-[0-9A-Za-z-]{10,}|Bearer\s+[A-Za-z0-9._~+/=-]{20,})")

_ALLOWED_PREFIXES = ("browser_use.Agent", "browser_use.agent", "browser_use.tools", "browser_use.Tools")

SECRET_ENV_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY",
                   "LINKEDIN_PASSWORD", "LINKEDIN_EMAIL")


def env_secrets() -> list[str]:
    """The current values of every credential the app stores."""
    return [v.strip() for k in SECRET_ENV_KEYS if (v := os.environ.get(k, "")) and len(v.strip()) >= _MIN_SECRET_LEN]


def redact(text: str, secrets: Iterable[str]) -> str:
    # Longest first, so a secret that contains another is replaced as a whole.
    for secret in sorted({s for s in secrets if s and len(s) >= _MIN_SECRET_LEN}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return _KEY_SHAPES.sub(REDACTED, text)


def clean(text: str, secrets: Iterable[str] = ()) -> str:
    raw = redact(_ANSI.sub("", str(text)), secrets)        # before flattening: a secret may contain spaces
    flat = " ".join(raw.split())
    return flat if len(flat) <= MAX_LINE else flat[: MAX_LINE - 1].rstrip() + "…"


class UIFeed:
    def __init__(self, secrets: Callable[[], Iterable[str]] = env_secrets) -> None:
        self._secrets = secrets

    def prepare(self, record: logging.LogRecord) -> Optional[dict]:
        """The chat message for this log record, or None to keep it out of the UI."""
        if record.levelno < logging.INFO:
            return None
        # The agent's own narration at INFO; warnings and errors from anywhere in browser-use too,
        # so "the browser crashed" is visible instead of only in the server log.
        if not (record.name.startswith(_ALLOWED_PREFIXES)
                or (record.levelno >= logging.WARNING and record.name.startswith("browser_use"))):
            return None
        text = clean(record.getMessage(), self._secrets())
        if not text:
            return None
        msg: dict = {"type": "action", "text": text}
        m = _STEP.match(text)
        if m:
            msg["step"] = int(m.group(1))
        return msg
