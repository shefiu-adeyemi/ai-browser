"""
Encrypts stored secrets with Windows DPAPI, so API keys and the LinkedIn password are not readable
plain text in the settings file. The key belongs to the signed-in Windows account: only that account
on this computer can decrypt, and nothing extra has to be installed or remembered.

A protected value is written as  dpapi:<base64>.  Anything else is treated as plain text, so a
hand-edited or older file keeps working and is encrypted the next time it is saved. Off Windows
(or if DPAPI fails) values are simply stored as they are.

This protects the file at rest (backups, sync, a zipped folder, another Windows user). It does not
hide the key from programs already running as you.
"""

import base64
import sys
from typing import Optional

PREFIX = "dpapi:"
_ENTROPY = b"AIBrowserAgent/settings/v1"


def available() -> bool:
    return sys.platform == "win32"


def _crypt(data: bytes, encrypt: bool) -> Optional[bytes]:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes) -> tuple[Blob, ctypes.Array]:
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    src, keep1 = blob(data)
    ent, keep2 = blob(_ENTROPY)
    out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    args = (ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))  # 0x1 = no UI prompt
    if not fn(*args):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(text: str) -> str:
    """The value to store. Falls back to the plain text when encryption isn't possible."""
    if not text or text.startswith(PREFIX) or not available():
        return text
    try:
        sealed = _crypt(text.encode("utf-8"), True)
    except Exception:
        sealed = None
    return PREFIX + base64.b64encode(sealed).decode("ascii") if sealed else text


def unprotect(stored: str) -> str:
    """The real value. A value that isn't protected is returned unchanged; one that can't be
    decrypted (another Windows account, another computer) comes back empty, i.e. 'not set'."""
    if not stored.startswith(PREFIX):
        return stored
    try:
        plain = _crypt(base64.b64decode(stored[len(PREFIX):], validate=True), False) if available() else None
    except Exception:
        plain = None
    return plain.decode("utf-8") if plain is not None else ""
