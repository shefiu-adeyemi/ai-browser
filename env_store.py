"""
Reading, writing, validating and applying the app's settings file (.env).

Why this is its own small format handler rather than python-dotenv:
  * dotenv writes values in a way its own reader can get wrong (a value containing " #" is cut
    off as a comment; one ending in a backslash cannot be single-quoted at all, so a password like
    ``abc\\`` corrupted the file), and the old hand-written reader disagreed with dotenv about
    quotes. One reader and one writer, tested against each other, cannot disagree.
  * an empty value could never clear a setting (they were dropped), so a stored key or password
    could not be removed from the UI;
  * the environment kept the OLD value after a clear, or a stale shell variable beat the file.

Format written: KEY="value" with JSON escaping (so quotes, backslashes, # and spaces round-trip).
Still read: KEY=value, KEY='value', KEY="value", `export KEY=...`, comments, a UTF-8 BOM (Notepad
adds one) - so hand-editing the file keeps working. Comments and unrelated lines are preserved.
"""

import json
import os
import re
import secrets
from pathlib import Path
from typing import Iterable, Optional

import providers
import secret_box

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}


# ------------------------------------------------------------------------------ parsing
def _unescape(body: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), body)


def parse_value(raw: str) -> str:
    s = raw.lstrip()
    if s[:1] == '"':
        i, n = 1, len(s)
        while i < n and s[i] != '"':
            i += 2 if s[i] == "\\" else 1        # a backslash escapes the next character
        if i < n:
            token = s[: i + 1]
            try:
                return json.loads(token)
            except ValueError:
                return _unescape(token[1:-1])
    elif s[:1] == "'":
        out, i, n = [], 1, len(s)
        while i < n:
            c = s[i]
            if c == "\\" and i + 1 < n and s[i + 1] in ("\\", "'"):
                out.append(s[i + 1])
                i += 2
                continue
            if c == "'":
                return "".join(out)
            out.append(c)
            i += 1
    return re.split(r"\s+#", s, maxsplit=1)[0].strip()      # unquoted: stops at " #comment"


def parse(text: str) -> dict[str, str]:
    """{KEY: value}. Later lines win, comments and junk lines are ignored."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if m:
            out[m.group(1)] = parse_value(m.group(2))
    return out


# Stored encrypted (Windows DPAPI, see secret_box.py). The email and the other settings are not secret.
SECRET_KEYS = tuple(p.key_env for p in providers.PROVIDERS) + ("LINKEDIN_PASSWORD",)


def read(path: Path) -> dict[str, str]:
    """The settings with secrets decrypted (values that were never encrypted are returned as they are)."""
    path = Path(path)
    if not path.exists():
        return {}
    values = parse(path.read_text(encoding="utf-8-sig"))
    return {k: secret_box.unprotect(v) if k in SECRET_KEYS else v for k, v in values.items()}


def unreadable_secrets(path: Path) -> list[str]:
    """Secrets present in the file that this Windows account cannot decrypt (the file was copied
    from another computer or user). They read as 'not set', so the UI must say so."""
    path = Path(path)
    if not path.exists():
        return []
    raw = parse(path.read_text(encoding="utf-8-sig"))
    return [k for k in SECRET_KEYS if raw.get(k, "").startswith(secret_box.PREFIX) and not secret_box.unprotect(raw[k])]


def _stored(key: str, value: str) -> str:
    return secret_box.protect(value) if key in SECRET_KEYS else value


# ------------------------------------------------------------------------------ writing
def encode_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write(path: Path, updates: dict[str, Optional[str]]) -> None:
    """Apply `updates` to the file: a non-empty value sets the key, None or "" REMOVES it.
    Everything else in the file (comments, other keys, their order and line endings) is kept.
    The write is atomic, so a crash can't leave a half-written file."""
    path = Path(path)
    # Read bytes, not text: text mode would silently turn Windows (CRLF) line endings into LF.
    lines = path.read_bytes().decode("utf-8-sig").splitlines(keepends=True) if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = _LINE_RE.match(line.rstrip("\r\n"))
        if m and m.group(1) in updates:
            key = m.group(1)
            if key in seen:
                continue                                     # a duplicate of an updated key: drop it
            seen.add(key)
            if updates[key]:
                eol = "\r\n" if line.endswith("\r\n") else "\n"
                out.append(f"{key}={encode_value(_stored(key, updates[key]))}{eol}")
            continue
        out.append(line if line.endswith(("\n", "\r")) else line + "\n")
    for key, value in updates.items():
        if key not in seen and value:
            out.append(f"{key}={encode_value(_stored(key, value))}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text("".join(out), encoding="utf-8", newline="")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ------------------------------------------------------------------------ the environment
def apply_to_environ(path: Path) -> list[str]:
    """Make the settings file win over stale shell variables: every non-empty value in the file
    replaces the process environment's value for that key. Returns the keys applied."""
    applied = []
    _encrypt_plaintext_secrets(path)
    for key, value in read(path).items():
        if value:
            os.environ[key] = value
            applied.append(key)
    return applied


def _encrypt_plaintext_secrets(path: Path) -> None:
    """Older files (or hand edits) hold secrets as plain text: encrypt them now, once."""
    if not secret_box.available() or not Path(path).exists():
        return
    try:
        raw = parse(Path(path).read_text(encoding="utf-8-sig"))
        plain = {k: v for k, v in raw.items() if k in SECRET_KEYS and v and not v.startswith(secret_box.PREFIX)}
        if plain:
            write(path, plain)
    except OSError:
        pass                                   # read-only location: keep working with plain text


def forget(keys: Iterable[str]) -> None:
    """A key that was cleared must also leave the running process, or the old value stays in use."""
    for key in keys:
        os.environ.pop(key, None)


# ---------------------------------------------------------------------------- validation
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_API_KEY_RE = re.compile(r"^[\x21-\x7e]{8,500}$")
API_KEYS = tuple(p.key_env for p in providers.PROVIDERS)


def _int_in(value, low: int, high: int, what: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a whole number")
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{what} must be a whole number from {low} to {high}") from None
    if not low <= number <= high:
        raise ValueError(f"{what} must be from {low} to {high}")
    return str(number)


def _clean_one(key: str, value) -> Optional[str]:
    """Validate and normalise ONE setting. Returns the value to store (None = clear it).
    Raises ValueError with a message meant for the user."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("must be text or a number")
    text = str(value)
    if key not in ("LINKEDIN_PASSWORD",):
        text = text.strip()
    if text == "":
        return None
    if _CONTROL.search(text):
        raise ValueError("must not contain line breaks or control characters")
    if len(text) > 2000:
        raise ValueError("is too long")

    if key == "LLM_PROVIDER":
        resolved = providers.resolve_provider_id(text)
        if resolved is None:
            raise ValueError("unknown provider; choose one of: " + ", ".join(p.id for p in providers.PROVIDERS))
        return resolved
    if key == "LLM_MODEL":
        model = providers.strip_model_prefix(text)
        if not _MODEL_RE.match(model):
            raise ValueError("is not a valid model name (letters, digits and . _ : / @ + - only)")
        return model
    if key in API_KEYS:
        if not _API_KEY_RE.match(text):
            raise ValueError("does not look like an API key (8-500 visible characters, no spaces)")
        return text
    if key == "LINKEDIN_EMAIL":
        if len(text) > 254 or not _EMAIL_RE.match(text):
            raise ValueError("is not a valid email address")
        return text
    if key == "LINKEDIN_PASSWORD":
        if len(text) > 200:
            raise ValueError("is too long")
        return text
    if key == "MAX_STEPS":
        return _int_in(text, 1, 500, "Max steps")
    if key == "CONTEXT_TURNS":
        return _int_in(text, 0, 20, "Context turns")
    if key == "APPROVAL_MODE":
        if text.lower() not in ("ask", "auto"):
            raise ValueError('must be "ask" or "auto"')
        return text.lower()
    raise ValueError("is not a setting")


def validate_settings(body: dict, allowed_keys: Iterable[str]) -> tuple[dict[str, Optional[str]], dict[str, str]]:
    """Check everything a save request contains BEFORE anything is written, so a bad value in one
    field can't leave the file half updated.
    Returns (updates, errors): updates maps key -> value to store (None = clear); errors maps
    key -> message. Unknown keys are ignored. If errors is not empty nothing should be written."""
    updates: dict[str, Optional[str]] = {}
    errors: dict[str, str] = {}
    for key in allowed_keys:
        if key not in body:
            continue
        try:
            updates[key] = _clean_one(key, body[key])
        except ValueError as e:
            errors[key] = str(e)
    return updates, errors
