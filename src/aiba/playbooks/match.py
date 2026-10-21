"""
Finding a recorded element again on a live page.

A playbook stores a Fingerprint (hashes, attributes, accessible name, XPath), never a numeric index.
At replay time the page is read into Candidates and `find` picks the one the fingerprint describes.

It is a pure function of data (no browser), so every rule is unit-tested. The order matters:

  1. exact hash            the element is byte-for-byte what was recorded            -> high
  2. stable hash           same, ignoring classes that come and go (hover, focus)    -> high
  3. identity attributes   a unique id / data-testid / name on the same tag           -> high
  4. XPath                 the same place in the page; high only if the visible name  -> high/medium
                           also agrees (or there was none)
  5. attribute agreement   role, aria-label, placeholder, type, href path ...         -> medium
  6. accessible name       the visible text, last, because it is the most volatile    -> medium
                           ("Message Jane Doe" differs from profile to profile)

When a level matches several elements the later levels only look among those, so two identical
"Next" buttons are told apart by position instead of one being picked blindly. If it is still
ambiguous, nothing is returned: a wrong click is worse than a stopped run.

`confidence` is what the approval logic relies on: a step the user pre-approved ("click Send") is
only auto-approved when the element found is a HIGH confidence match for the one they approved.
"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from aiba.playbooks.model import Fingerprint

_IDENTITY_ATTRS = ("id", "data-testid", "data-test", "name")
_SOFT_ATTRS = ("role", "aria-label", "placeholder", "type", "title", "alt", "href", "for")


@dataclass
class Candidate:
    index: int
    node_name: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    ax_name: Optional[str] = None
    x_path: Optional[str] = None
    element_hash: Optional[str] = None
    stable_hash: Optional[str] = None

    def __post_init__(self) -> None:         # a fingerprint holds hashes as text (see playbook.Fingerprint)
        self.element_hash = None if self.element_hash is None else str(self.element_hash)
        self.stable_hash = None if self.stable_hash is None else str(self.stable_hash)


@dataclass
class Match:
    index: int
    level: str                     # exact | stable | identity | xpath | attributes | name
    confidence: str                # high | medium


def candidates_from_selector_map(selector_map: dict[int, Any]) -> list[Candidate]:
    """browser-use's {index: element} into Candidates. A node that cannot be read is skipped."""
    out: list[Candidate] = []
    for index, node in selector_map.items():
        try:
            ax = getattr(node, "ax_node", None)
            out.append(Candidate(
                index=int(index),
                node_name=str(getattr(node, "node_name", "") or "").lower(),
                attributes={str(k): str(v) for k, v in (getattr(node, "attributes", None) or {}).items()},
                ax_name=(ax.name if ax is not None and getattr(ax, "name", None) else None),
                x_path=getattr(node, "xpath", None),
                element_hash=hash(node),
                stable_hash=node.compute_stable_hash(),
            ))
        except Exception:                                   # one odd element must not hide the rest
            continue
    return out


def _path(href: str) -> str:
    try:
        p = urlparse(href)
        return (p.netloc + p.path).rstrip("/")
    except ValueError:
        return href.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def _same(key: str, a: str, b: str) -> bool:
    return _path(a) == _path(b) if key == "href" else a == b


def _attribute_score(fp: Fingerprint, c: Candidate) -> int:
    """Agreements minus twice the conflicts, over the attributes both sides have."""
    score = 0
    for key in _SOFT_ATTRS + _IDENTITY_ATTRS:
        if key in fp.attributes and key in c.attributes:
            score += 1 if _same(key, fp.attributes[key], c.attributes[key]) else -2
    return score


def closest(fp: Fingerprint, candidates: Iterable[Candidate], limit: int = 3) -> list[Candidate]:
    """The elements on the page that look most like the one that was recorded, best first. Used to help a
    person fix a playbook whose element can't be found ("did you mean ...?"), never to click anything."""
    scored: list[tuple[float, Candidate]] = []
    want = (fp.ax_name or "").strip().lower()
    for c in candidates:
        if fp.node_name and c.node_name != fp.node_name:
            continue
        name = (c.ax_name or "").strip().lower()
        similarity = SequenceMatcher(None, want, name).ratio() if want and name else 0.0
        score = similarity + 0.25 * max(0, _attribute_score(fp, c))
        if score >= 0.5 and (name or c.attributes):
            scored.append((score, c))
    scored.sort(key=lambda p: -p[0])
    return [c for _score, c in scored[:limit]]


def find(fp: Fingerprint, candidates: Iterable[Candidate]) -> Optional[Match]:
    pool = [c for c in candidates if not fp.node_name or c.node_name == fp.node_name]
    if not pool:
        return None

    def decide(level: str, confidence: str, matches: list[Candidate]) -> Optional[Match]:
        nonlocal pool
        if len(matches) == 1:
            return Match(matches[0].index, level, confidence)
        if len(matches) > 1:
            pool = matches                                   # later levels only choose among these
        return None

    if fp.element_hash is not None:
        hit = decide("exact", "high", [c for c in pool if c.element_hash == fp.element_hash])
        if hit:
            return hit
    if fp.stable_hash is not None:
        hit = decide("stable", "high", [c for c in pool if c.stable_hash == fp.stable_hash])
        if hit:
            return hit

    for key in _IDENTITY_ATTRS:
        want = fp.attributes.get(key)
        if want:
            hit = decide("identity", "high", [c for c in pool if c.attributes.get(key) == want])
            if hit:
                return hit

    if fp.x_path:
        same_place = [c for c in pool if c.x_path == fp.x_path]
        name_agrees = (not fp.ax_name) or (len(same_place) == 1 and same_place[0].ax_name == fp.ax_name)
        hit = decide("xpath", "high" if name_agrees else "medium", same_place)
        if hit:
            return hit

    if fp.attributes:
        scored = [(_attribute_score(fp, c), c) for c in pool]
        best = max((s for s, _ in scored), default=0)
        if best > 0:
            hit = decide("attributes", "medium", [c for s, c in scored if s == best])
            if hit:
                return hit

    if fp.ax_name:
        hit = decide("name", "medium", [c for c in pool if c.ax_name == fp.ax_name])
        if hit:
            return hit
    return None
