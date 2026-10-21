"""Tests for playbook_match.py: finding a recorded element again."""

import unittest
from types import SimpleNamespace as NS

from aiba.playbooks.model import Fingerprint
from aiba.playbooks.match import Candidate, candidates_from_selector_map, closest, find


def cand(i, node="button", ax=None, attrs=None, xp=None, h=None, sh=None):
    return Candidate(index=i, node_name=node, attributes=attrs or {}, ax_name=ax, x_path=xp,
                     element_hash=h, stable_hash=sh)


def fp(node="button", ax=None, attrs=None, xp=None, h=None, sh=None):
    return Fingerprint(node_name=node, attributes=attrs or {}, ax_name=ax, x_path=xp, element_hash=h,
                       stable_hash=sh)


class Levels(unittest.TestCase):
    def test_exact_hash(self):
        m = find(fp(h=1, sh=2), [cand(5, h=9, sh=9), cand(6, h=1, sh=2)])
        self.assertEqual((m.index, m.level, m.confidence), (6, "exact", "high"))

    def test_stable_hash_when_the_classes_changed(self):
        m = find(fp(h=1, sh=2), [cand(5, h=7, sh=2), cand(6, h=8, sh=3)])
        self.assertEqual((m.index, m.level, m.confidence), (5, "stable", "high"))

    def test_identity_attribute(self):
        m = find(fp(h=1, sh=2, attrs={"id": "save"}), [cand(1, attrs={"id": "cancel"}), cand(2, attrs={"id": "save"})])
        self.assertEqual((m.index, m.level, m.confidence), (2, "identity", "high"))

    def test_xpath_with_agreeing_name_is_high(self):
        m = find(fp(ax="Next", xp="/a/b"), [cand(1, ax="Prev", xp="/a/c"), cand(2, ax="Next", xp="/a/b")])
        self.assertEqual((m.index, m.level, m.confidence), (2, "xpath", "high"))

    def test_xpath_with_a_changed_name_is_only_medium(self):
        m = find(fp(ax="Message Jane", xp="/a/b"), [cand(2, ax="Message Bob", xp="/a/b")])
        self.assertEqual((m.index, m.level, m.confidence), (2, "xpath", "medium"))

    def test_xpath_without_a_recorded_name_is_high(self):
        m = find(fp(xp="/a/b"), [cand(2, xp="/a/b")])
        self.assertEqual(m.confidence, "high")

    def test_attribute_agreement(self):
        m = find(fp(attrs={"aria-label": "Search", "role": "button"}),
                 [cand(1, attrs={"aria-label": "Close"}), cand(2, attrs={"aria-label": "Search", "role": "button"})])
        self.assertEqual((m.index, m.level, m.confidence), (2, "attributes", "medium"))

    def test_conflicting_attributes_lose(self):
        m = find(fp(attrs={"aria-label": "Search", "type": "submit"}),
                 [cand(1, attrs={"aria-label": "Search", "type": "reset"})])
        self.assertIsNone(m)                                        # 1 agreement, 1 conflict: no positive score

    def test_href_ignores_the_query_string(self):
        m = find(fp("a", attrs={"href": "https://x.com/next"}), [cand(1, "a", attrs={"href": "https://x.com/next?p=2"}),
                                                                 cand(2, "a", attrs={"href": "https://x.com/prev"})])
        self.assertEqual(m.index, 1)

    def test_name_is_the_last_resort(self):
        m = find(fp(ax="Continue"), [cand(1, ax="Back"), cand(2, ax="Continue")])
        self.assertEqual((m.index, m.level, m.confidence), (2, "name", "medium"))


class Safety(unittest.TestCase):
    def test_wrong_tag_is_never_matched(self):
        self.assertIsNone(find(fp("button", h=1), [cand(1, "a", h=1)]))
        self.assertIsNone(find(fp("button", ax="Go"), [cand(1, "div", ax="Go")]))

    def test_nothing_on_the_page(self):
        self.assertIsNone(find(fp(h=1), []))

    def test_ambiguity_is_not_guessed(self):
        self.assertIsNone(find(fp(ax="Next"), [cand(1, ax="Next"), cand(2, ax="Next")]))
        self.assertIsNone(find(fp(attrs={"aria-label": "Like"}),
                               [cand(1, attrs={"aria-label": "Like"}), cand(2, attrs={"aria-label": "Like"})]))

    def test_two_identical_buttons_are_told_apart_by_position(self):
        a, b = cand(1, ax="Next", xp="/p/1", h=5, sh=5), cand(2, ax="Next", xp="/p/2", h=5, sh=5)
        m = find(fp(ax="Next", xp="/p/2", h=5, sh=5), [a, b])
        self.assertEqual((m.index, m.level), (2, "xpath"))

    def test_narrowing_does_not_widen(self):
        # the hash matches two elements; the id then picks exactly one of THOSE, not another element
        a, b, c = cand(1, h=5, attrs={"id": "x"}), cand(2, h=5, attrs={"id": "y"}), cand(3, h=9, attrs={"id": "y"})
        m = find(fp(h=5, attrs={"id": "y"}), [a, b, c])
        self.assertEqual(m.index, 2)

    def test_a_fingerprint_with_no_node_name_still_matches(self):
        m = find(Fingerprint(element_hash=3), [cand(7, "div", h=3)])
        self.assertEqual(m.index, 7)

    def test_hashes_compare_as_text_whatever_type_either_side_used(self):
        big = 2 ** 63 + 12345                                   # more than JavaScript can hold exactly
        m = find(Fingerprint(node_name="button", element_hash=str(big)), [cand(1, h=big)])
        self.assertEqual(m.level, "exact")
        self.assertEqual(find(Fingerprint(node_name="button", stable_hash=big), [cand(2, sh=str(big))]).index, 2)

    def test_an_empty_fingerprint_matches_nothing(self):
        self.assertIsNone(find(Fingerprint(node_name="button"), [cand(1), cand(2)]))

    def test_the_person_specific_name_does_not_beat_attributes(self):
        # recorded on Jane's profile; the same button on Bob's has another name but the same attributes
        m = find(fp(ax="Message Jane", attrs={"aria-label": "Message", "role": "button"}),
                 [cand(1, ax="Message Bob", attrs={"aria-label": "Message", "role": "button"}),
                  cand(2, ax="Connect", attrs={"aria-label": "Connect", "role": "button"})])
        self.assertEqual((m.index, m.level), (1, "attributes"))


class Closest(unittest.TestCase):
    def test_the_most_similar_names_come_first(self):
        near = closest(fp(ax="Message"), [cand(1, ax="Connect"), cand(2, ax="Messaging"), cand(3, ax="Message Jane"),
                                          cand(4, ax="Home")])
        self.assertEqual([c.index for c in near][:2], [2, 3])
        self.assertNotIn(4, [c.index for c in near])                       # nothing like it

    def test_attributes_count_too(self):
        near = closest(fp(ax="Send", attrs={"aria-label": "Send message", "role": "button"}),
                       [cand(1, ax="Post", attrs={"aria-label": "Send message", "role": "button"}), cand(2, ax="Cancel")])
        self.assertEqual([c.index for c in near], [1])

    def test_only_the_same_kind_of_element(self):
        self.assertEqual(closest(fp("button", ax="Next"), [cand(1, "a", ax="Next"), cand(2, "div", ax="Next page")]), [])

    def test_limit_and_empty_page(self):
        many = [cand(i, ax=f"Message {i}") for i in range(10)]
        self.assertEqual(len(closest(fp(ax="Message"), many)), 3)
        self.assertEqual(len(closest(fp(ax="Message"), many, limit=5)), 5)
        self.assertEqual(closest(fp(ax="Message"), []), [])

    def test_a_fingerprint_with_no_name_or_attributes_suggests_nothing(self):
        self.assertEqual(closest(Fingerprint(node_name="button"), [cand(1, ax="Anything")]), [])


class FromSelectorMap(unittest.TestCase):
    class Node:
        def __init__(self, name, attrs, ax=None, xp="/x", h=1, sh=2):
            self.node_name, self.attributes, self.xpath = name, attrs, xp
            self.ax_node = NS(name=ax) if ax is not None else None
            self._h, self._sh = h, sh
        def __hash__(self): return self._h
        def compute_stable_hash(self): return self._sh

    def test_conversion(self):
        out = candidates_from_selector_map({24: self.Node("INPUT", {"id": "q"}, ax="Search", h=11, sh=12)})
        (c,) = out
        self.assertEqual((c.index, c.node_name, c.attributes, c.ax_name, c.x_path, c.element_hash, c.stable_hash),
                         (24, "input", {"id": "q"}, "Search", "/x", "11", "12"))

    def test_missing_pieces_and_a_broken_node(self):
        class Broken(self.Node):
            def compute_stable_hash(self): raise RuntimeError("boom")
        out = candidates_from_selector_map({1: self.Node("A", {}, ax=None), 2: Broken("B", {}), 3: self.Node("C", {})})
        self.assertEqual([c.index for c in out], [1, 3])
        self.assertIsNone(out[0].ax_name)


if __name__ == "__main__":
    unittest.main()
