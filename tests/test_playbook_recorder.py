"""Tests for playbook_recorder.py (history -> draft playbook), using look-alike history objects."""

import unittest
from types import SimpleNamespace as NS

from aiba.playbooks import model as pbk
from aiba.playbooks import recorder as rec
def el(name="button", ax=None, attrs=None, h=1, sh=None):
    return NS(node_name=name.upper(), attributes=attrs or {}, ax_name=ax, x_path="/html/body/" + name,
              element_hash=h, stable_hash=sh if sh is not None else h + 1000)


def item(action, element=None, goal="", url="https://www.example.com/", error=None, tabs=None):
    name = next(iter(action))
    return NS(
        model_output=NS(action=[action], current_state=NS(next_goal=goal)),
        result=[NS(error=error)],
        state=NS(url=url, interacted_element=[element], tabs=tabs or []),
    )


def tab(target_id, url="https://www.example.com/"):
    return NS(target_id=target_id, url=url, title="t")


def history(*items):
    return NS(history=list(items))


class Mapping(unittest.TestCase):
    def draft(self, *items, task="", secrets=None):
        return rec.draft_from_history(history(*items), task=task, sensitive_data=secrets)

    def kinds(self, d):
        return [s.kind for s in d.playbook.steps]

    def test_basic_run(self):
        d = self.draft(
            item({"navigate": {"url": "https://www.example.com/", "new_tab": False}}),
            item({"input": {"index": 3, "text": "hello", "clear": True}}, el("input", attrs={"name": "q"}),
                 url="https://www.example.com/"),
            item({"send_keys": {"keys": "Enter"}}),
            item({"click": {"index": 5}}, el(ax="Next page", attrs={"aria-label": "Next page"})),
            item({"scroll": {"down": True, "pages": 2.0}}),
            item({"wait": {"seconds": 3}}),
            item({"go_back": {}}),
            item({"extract": {"query": "list the titles"}}),
            item({"done": {"text": "ok", "success": True}}),
        )
        self.assertEqual(self.kinds(d), ["open", "type", "key", "click", "scroll", "wait", "back", "extract"])
        self.assertEqual([s.id for s in d.playbook.steps], [f"s{i}" for i in range(1, 9)])
        tags = {s.kind: s.tag for s in d.playbook.steps}
        self.assertEqual(tags["extract"], "ai")
        self.assertTrue(all(t == "deterministic" for k, t in tags.items() if k != "extract"))
        self.assertEqual(pbk.validate(d.playbook), [])
        self.assertEqual(d.playbook.domains, ["www.example.com"])

    def test_elements_are_saved_as_fingerprints_not_indexes(self):
        d = self.draft(item({"click": {"index": 77}}, el(ax="Save", attrs={"id": "save", "class": "x y",
                                                                            "style": "color:red"}, h=42, sh=43)))
        s = d.playbook.steps[0]
        self.assertEqual((s.target.node_name, s.target.ax_name, s.target.stable_hash), ("button", "Save", "43"))
        self.assertEqual(s.target.attributes, {"id": "save"})              # class/style are not kept
        self.assertNotIn("77", s.model_dump_json())

    def test_observation_actions_are_dropped_silently(self):
        d = self.draft(item({"find_text": {"text": "x"}}), item({"search_page": {"pattern": "x"}}),
                       item({"screenshot": {}}), item({"go_back": {}}))
        self.assertEqual(self.kinds(d), ["back"])
        self.assertEqual(d.notes, [])

    def test_failed_steps_are_left_out_and_reported(self):
        d = self.draft(item({"click": {"index": 1}}, el(ax="A"), error="element not found"),
                       item({"click": {"index": 2}}, el(ax="B", h=2)))
        self.assertEqual([s.target.ax_name for s in d.playbook.steps], ["B"])
        self.assertTrue(any("failed click" in n for n in d.notes))

    def test_unrecordable_actions_are_reported(self):
        d = self.draft(item({"evaluate": {"code": "alert(1)"}}), item({"upload_file": {"index": 1, "path": "x"}}),
                       item({"write_file": {"file_name": "a"}}), item({"go_back": {}}))
        self.assertEqual(self.kinds(d), ["back"])
        text = " ".join(d.notes)
        for word in ("JavaScript", "uploaded a file", "wrote a file"):
            self.assertIn(word, text)

    def test_unknown_action_is_reported(self):
        d = self.draft(item({"teleport": {"where": "moon"}}), item({"go_back": {}}))
        self.assertTrue(any("teleport" in n for n in d.notes))

    def test_action_on_an_element_that_was_not_on_the_page_is_left_out(self):
        d = self.draft(item({"click": {"index": 4}}, None, goal="Press the blue button"),
                       item({"input": {"index": 5, "text": "x"}}, None),
                       item({"select_dropdown": {"index": 6, "text": "x"}}, None),
                       item({"go_back": {}}))
        self.assertEqual(self.kinds(d), ["back"])
        self.assertEqual(len(d.notes), 3)
        self.assertTrue(all("not on the page" in n for n in d.notes))

    def test_missing_element_message_counts_as_a_failure_even_with_an_element(self):
        # browser-use gives NO error for this, only a message in extracted_content
        bad = item({"click": {"index": 1}}, el(ax="Ghost"))
        bad.result = [NS(error=None, extracted_content="Element index 1 not available - page may have changed.")]
        d = self.draft(bad, item({"go_back": {}}))
        self.assertEqual(self.kinds(d), ["back"])
        self.assertTrue(any("failed click" in n for n in d.notes))

    def test_repeated_clicks_are_kept_but_flagged(self):
        e = el(ax="Next", h=9)
        d = self.draft(item({"click": {"index": 1}}, e), item({"click": {"index": 1}}, e),
                       item({"click": {"index": 1}}, e), item({"click": {"index": 1}}, el(ax="Other", h=10)))
        self.assertEqual(len(d.playbook.steps), 4)            # paging through results must stay intact
        self.assertEqual(sum("more than once" in n for n in d.notes), 1)

    def test_distinct_clicks_are_not_flagged(self):
        d = self.draft(item({"click": {"index": 1}}, el(ax="A", h=1)), item({"click": {"index": 2}}, el(ax="B", h=2)))
        self.assertEqual(d.notes, [])

    def test_the_same_page_opened_twice_in_a_row_is_kept_once(self):
        d = self.draft(item({"navigate": {"url": "https://a.com/"}}, url="about:blank"),
                       item({"navigate": {"url": "https://a.com/"}}, url="https://a.com/"),
                       item({"navigate": {"url": "https://b.com/"}}, url="https://a.com/"),
                       item({"navigate": {"url": "https://a.com/"}}, url="https://b.com/"))
        self.assertEqual([s.url for s in d.playbook.steps], ["https://a.com/", "https://b.com/", "https://a.com/"])
        self.assertIsNotNone(d.playbook.steps[0].check)

    def test_a_new_tab_open_is_not_merged_with_a_normal_one(self):
        d = self.draft(item({"navigate": {"url": "https://a.com/", "new_tab": False}}),
                       item({"navigate": {"url": "https://a.com/", "new_tab": True}}))
        self.assertEqual(len(d.playbook.steps), 2)

    def test_a_search_is_a_search_step_that_can_become_a_variable(self):
        d = self.draft(item({"search": {"query": "red shoes & socks", "engine": "duckduckgo"}}),
                       task="Search for red shoes & socks and read the first result")
        s = d.playbook.steps[0]
        self.assertEqual((s.kind, s.engine, s.query), ("search", "duckduckgo", "red shoes & socks"))
        self.assertEqual(pbk.validate(d.playbook), [])
        self.assertEqual(d.playbook.domains, ["duckduckgo.com"])
        self.assertEqual([(x["field"], x["name"]) for x in d.suggestions], [("query", "search_text")])
        pb = pbk.make_variable(d.playbook, "s1", "query", "search_text", "text")
        self.assertEqual(pb.steps[0].query, "{{search_text}}")
        self.assertEqual(pbk.validate(pb), [])

    def test_a_request_for_help_becomes_a_handoff_step(self):
        d = self.draft(item({"request_user_help": {"reason": "Solve the CAPTCHA"}}, goal="Wait for the user"),
                       item({"go_back": {}}))
        self.assertEqual(self.kinds(d), ["handoff", "back"])
        self.assertEqual(d.playbook.steps[0].message, "Solve the CAPTCHA")
        self.assertEqual(pbk.validate(d.playbook), [])
        self.assertEqual(d.notes, [])

    def test_a_handoff_without_a_reason_still_tells_the_user_what_to_do(self):
        d = self.draft(item({"request_user_help": {}}))
        self.assertIn("manual step", d.playbook.steps[0].message)

    def test_tab_switches_are_recorded_by_position(self):
        tabs = [tab("AAAA1111"), tab("BBBB2222"), tab("CCCC3333")]
        d = self.draft(item({"switch": {"tab_id": "2222"}}, tabs=tabs), item({"close": {"tab_id": "3333"}}, tabs=tabs),
                       item({"switch": {"tab_id": "1111"}}, tabs=tabs))
        self.assertEqual([(s.kind, s.tab_index, s.close_tab) for s in d.playbook.steps],
                         [("tab", 1, False), ("tab", 2, True), ("tab", 0, False)])
        self.assertEqual(pbk.validate(d.playbook), [])

    def test_tab_steps_warn_when_the_run_began_with_several_tabs(self):
        many = [tab("AAAA1111"), tab("BBBB2222")]
        d = self.draft(item({"go_back": {}}, tabs=many), item({"switch": {"tab_id": "2222"}}, tabs=many))
        self.assertTrue(any("began with 2 tabs" in n for n in d.notes))
        one = [tab("AAAA1111")]
        d = self.draft(item({"go_back": {}}, tabs=one), item({"switch": {"tab_id": "1111"}}, tabs=one))
        self.assertFalse(any("began with" in n for n in d.notes))

    def test_a_tab_that_is_not_in_the_list_is_left_out_with_a_note(self):
        d = self.draft(item({"switch": {"tab_id": "ZZZZ"}}, tabs=[tab("AAAA1111")]),
                       item({"switch": {"tab_id": "1111"}}, tabs=[]), item({"go_back": {}}))
        self.assertEqual(self.kinds(d), ["back"])
        self.assertEqual(len([n for n in d.notes if "tab" in n]), 1)          # the same note is said once

    def test_unknown_search_engine_is_not_recorded(self):
        d = self.draft(item({"search": {"query": "x", "engine": "altavista"}}))
        self.assertEqual(d.playbook.steps, [])
        self.assertTrue(any("altavista" in n for n in d.notes))

    def test_empty_run(self):
        d = self.draft(item({"done": {"text": "x"}}))
        self.assertEqual(d.playbook.steps, [])
        self.assertTrue(any("no steps" in n for n in d.notes))
        self.assertTrue(pbk.validate(d.playbook))             # can't be saved as it is

    def test_wait_is_capped(self):
        d = self.draft(item({"wait": {"seconds": 99999}}))
        self.assertEqual(d.playbook.steps[0].seconds, 300)

    def test_select_dropdown(self):
        d = self.draft(item({"select_dropdown": {"index": 2, "text": "Large"}}, el("select", ax="Size")))
        s = d.playbook.steps[0]
        self.assertEqual((s.kind, s.text), ("select", "Large"))

    def test_name_and_source_come_from_the_task(self):
        d = self.draft(item({"go_back": {}}), task="Open Jane's profile and read it")
        self.assertEqual(d.playbook.name, "Open Jane's profile and read it")
        self.assertEqual(d.playbook.source.task, "Open Jane's profile and read it")
        long = rec.draft_from_history(history(item({"go_back": {}})), task="word " * 100)
        self.assertLessEqual(len(long.playbook.name), 60)
        self.assertTrue(long.playbook.name.endswith("…"))
        self.assertEqual(long.playbook.source.task, ("word " * 100).strip())          # the full request is kept
        blank = rec.draft_from_history(history(item({"go_back": {}})), task="   ")
        self.assertEqual(blank.playbook.name, "Recorded run")


class Robustness(unittest.TestCase):
    """One odd step must never lose the whole draft."""
    def draft(self, *items, **kw):
        return rec.draft_from_history(history(*items), **kw)

    def test_a_huge_accessible_name_is_clipped_not_fatal(self):
        d = self.draft(item({"click": {"index": 1}}, el(ax="card " * 1000, attrs={"id": "c"})))
        self.assertEqual(len(d.playbook.steps[0].target.ax_name), 500)
        self.assertEqual(pbk.validate(d.playbook), [])

    def test_huge_xpath_and_node_name_are_clipped(self):
        e = el(ax="x")
        e.x_path = "/div" * 2000
        e.node_name = "CUSTOM-ELEMENT-" * 10
        d = self.draft(item({"click": {"index": 1}}, e))
        fp = d.playbook.steps[0].target
        self.assertEqual((len(fp.x_path), len(fp.node_name)), (2000, 40))

    def test_text_too_long_for_a_step_leaves_that_step_out_only(self):
        d = self.draft(item({"input": {"index": 1, "text": "x" * 5000}}, el("input")),
                       item({"send_keys": {"keys": "a" * 500}}), item({"go_back": {}}))
        self.assertEqual([s.kind for s in d.playbook.steps], ["back"])
        self.assertEqual(sorted(n.split()[1] for n in d.notes if "too large or malformed" in n),
                         ["input", "send_keys"])

    def test_scroll_amounts_are_clamped(self):
        d = self.draft(item({"scroll": {"down": True, "pages": 0}}), item({"scroll": {"down": False, "pages": 9999}}),
                       item({"scroll": {"down": True, "pages": -3}}))
        self.assertEqual([s.pages for s in d.playbook.steps], [1.0, 50.0, 0.1])
        self.assertEqual(pbk.validate(d.playbook), [])

    def test_malformed_numbers_leave_that_step_out(self):
        d = self.draft(item({"wait": {"seconds": "soon"}}), item({"scroll": {"pages": "lots"}}),
                       item({"go_back": {}}))
        self.assertEqual([s.kind for s in d.playbook.steps], ["back"])

    def test_link_query_strings_are_not_saved(self):
        d = self.draft(item({"click": {"index": 1}}, el("a", ax="Profile", attrs={
            "href": "https://www.example.com/in/jane?token=abc123&utm=x#frag", "id": "p"})))
        href = d.playbook.steps[0].target.attributes["href"]
        self.assertEqual(href, "https://www.example.com/in/jane")
        self.assertNotIn("abc123", d.playbook.model_dump_json())

    def test_relative_and_odd_links_survive(self):
        d = self.draft(item({"click": {"index": 1}}, el("a", ax="Next", attrs={"href": "/next?page=2"})),
                       item({"click": {"index": 2}}, el("a", ax="Odd", attrs={"href": "http://[bad?x=1"}, h=2)))
        self.assertEqual(len(d.playbook.steps), 2)
        self.assertEqual(d.playbook.steps[0].target.attributes["href"], "/next")
        self.assertNotIn("?", d.playbook.steps[1].target.attributes["href"])

    def test_trailing_slash_does_not_hide_a_suggestion(self):
        for task in ("Go to https://shop.example.com and look", "Go to https://shop.example.com/ and look",
                     "Visit https://shop.example.com.", "(https://shop.example.com)", "https://shop.example.com"):
            d = self.draft(item({"navigate": {"url": "https://shop.example.com/"}}), task=task)
            self.assertEqual([x["field"] for x in d.suggestions], ["url"], task)

    def test_a_longer_address_is_not_mistaken_for_the_one_in_the_task(self):
        d = self.draft(item({"navigate": {"url": "https://shop.example.com/"}}),
                       task="Go to https://shop.example.com.evil.org now")
        self.assertEqual(d.suggestions, [])
        d = self.draft(item({"navigate": {"url": "https://a.com/x"}}), task="open https://a.com/xyz")
        self.assertEqual(d.suggestions, [])


class Safety(unittest.TestCase):
    def draft(self, *items, **kw):
        return rec.draft_from_history(history(*items), **kw)

    def test_real_secret_values_are_replaced(self):
        secrets = {"https://*.linkedin.com": {"linkedin_email": "me@example.com", "linkedin_password": "hunter2!"}}
        d = self.draft(
            item({"input": {"index": 1, "text": "me@example.com"}}, el("input", attrs={"type": "email"})),
            item({"input": {"index": 2, "text": "hunter2!"}}, el("input", attrs={"type": "text"})),
            sensitive_data=secrets)
        self.assertEqual([s.text for s in d.playbook.steps],
                         ["<secret>linkedin_email</secret>", "<secret>linkedin_password</secret>"])
        self.assertNotIn("hunter2", d.playbook.model_dump_json())
        self.assertNotIn("me@example.com", d.playbook.model_dump_json())

    def test_secret_placeholder_is_kept(self):
        d = self.draft(item({"input": {"index": 2, "text": "<secret>linkedin_password</secret>"}},
                             el("input", attrs={"type": "password"})))
        self.assertEqual(d.playbook.steps[0].text, "<secret>linkedin_password</secret>")
        self.assertEqual(d.playbook.steps[0].kind, "type")

    def test_literal_typed_into_a_password_field_is_not_recorded(self):
        d = self.draft(item({"input": {"index": 2, "text": "plain-password"}},
                             el("input", attrs={"type": "password"})))
        s = d.playbook.steps[0]
        self.assertEqual((s.kind, s.tag), ("handoff", "deterministic"))
        self.assertIn("password", s.message)
        self.assertNotIn("plain-password", d.playbook.model_dump_json())
        self.assertTrue(any("password" in n for n in d.notes))
        self.assertEqual(pbk.validate(d.playbook), [])

    def test_secret_in_a_url_or_fingerprint_is_redacted(self):
        d = self.draft(
            item({"navigate": {"url": "https://a.com/?k=topsecret"}}),
            item({"click": {"index": 1}}, el(ax="Hello topsecret", attrs={"title": "topsecret"})),
            sensitive_data={"a.com": {"api_key": "topsecret"}})
        self.assertNotIn("topsecret", d.playbook.model_dump_json())
        self.assertTrue(pbk.validate(d.playbook))                     # a secret in a URL is refused later

    def test_risky_clicks_and_enter_are_marked_ask(self):
        d = self.draft(item({"click": {"index": 1}}, el(ax="Send message")),
                       item({"click": {"index": 2}}, el(ax="Next", h=2)),
                       item({"send_keys": {"keys": "Enter"}}), item({"send_keys": {"keys": "Control+a"}}),
                       item({"click": {"index": 3}}, el(attrs={"aria-label": "Connect"}, h=3)))
        self.assertEqual([s.ask for s in d.playbook.steps], [True, False, True, False, True])
        self.assertEqual(len(pbk.ask_steps(d.playbook)), 3)

    def test_a_click_that_leaves_the_site_is_recorded_as_a_check(self):
        d = self.draft(
            item({"click": {"index": 1}}, el(ax="Docs"), url="https://www.example.com/"),
            item({"click": {"index": 2}}, el(ax="Next", h=2), url="https://docs.other.org/start"),
            item({"go_back": {}}, url="https://docs.other.org/next"))
        self.assertEqual(d.playbook.steps[0].check.hosts, ["docs.other.org"])
        self.assertIsNone(d.playbook.steps[1].check)                  # stayed on the site
        self.assertIn("docs.other.org", d.playbook.domains)           # so the approval covers it

    def test_open_records_where_it_landed(self):
        d = self.draft(item({"navigate": {"url": "https://short.ly/x"}}, url="about:blank"),
                       item({"go_back": {}}, url="https://real.example.com/page"))
        self.assertEqual(d.playbook.steps[0].check.hosts, ["real.example.com", "short.ly"])

    def test_dict_shaped_history_also_works(self):
        h = [{"model_output": {"action": [{"go_back": {}}], "next_goal": "back"},
              "result": [{"error": None}], "state": {"url": "https://a.com", "interacted_element": [None]}}]
        d = rec.draft_from_history(h)
        self.assertEqual([s.kind for s in d.playbook.steps], ["back"])


class Suggestions(unittest.TestCase):
    def draft(self, items, task):
        return rec.draft_from_history(history(*items), task=task)

    def test_linkedin_profile_url(self):
        d = self.draft([item({"navigate": {"url": "https://www.linkedin.com/in/jane-doe"}})],
                       "Open https://www.linkedin.com/in/jane-doe")
        (sug,) = d.suggestions
        self.assertEqual((sug["field"], sug["type"], sug["name"]), ("url", "linkedin_url", "profile_url"))

    def test_url_from_the_task_and_text_from_the_task(self):
        d = self.draft([item({"navigate": {"url": "https://shop.example.com/"}}),
                        item({"input": {"index": 1, "text": "red shoes"}}, el("input", attrs={"name": "q"}))],
                       "Go to https://shop.example.com/ and search for red shoes")
        by_field = {s["field"]: s for s in d.suggestions}
        self.assertEqual(by_field["url"]["allowed_hosts"], ["shop.example.com"])
        self.assertEqual(by_field["text"]["name"], "q")
        self.assertEqual(by_field["text"]["value"], "red shoes")

    def test_text_not_in_the_task_is_not_suggested(self):
        d = self.draft([item({"input": {"index": 1, "text": "something else"}}, el("input"))], "Do the thing")
        self.assertEqual(d.suggestions, [])

    def test_secrets_are_never_suggested(self):
        d = self.draft([item({"input": {"index": 1, "text": "<secret>pw</secret>"}}, el("input"))],
                       "type <secret>pw</secret>")
        self.assertEqual(d.suggestions, [])

    def test_names_are_unique_and_valid(self):
        d = self.draft([item({"input": {"index": 1, "text": "aa"}}, el("input", attrs={"name": "Q Box!"})),
                        item({"input": {"index": 2, "text": "bb"}}, el("input", attrs={"name": "Q Box!"}, h=2))],
                       "enter aa and bb")
        names = [s["name"] for s in d.suggestions]
        self.assertEqual(names, ["q_box", "q_box_2"])
        for s in d.suggestions:
            self.assertTrue(pbk.VAR_NAME_RE.fullmatch(s["name"]))

    def test_numeric_text_suggests_number(self):
        d = self.draft([item({"input": {"index": 1, "text": "42"}}, el("input"))], "enter 42")
        self.assertEqual(d.suggestions[0]["type"], "number")

    def test_suggestions_apply_cleanly(self):
        d = self.draft([item({"navigate": {"url": "https://www.linkedin.com/in/jane-doe"}}),
                        item({"input": {"index": 1, "text": "hello"}}, el("input", attrs={"name": "msg"}))],
                       "Open https://www.linkedin.com/in/jane-doe and type hello")
        pb = d.playbook
        for s in d.suggestions:
            pb = pbk.make_variable(pb, s["step_id"], s["field"], s["name"], s["type"], s["allowed_hosts"])
        self.assertEqual(pbk.validate(pb), [])
        self.assertEqual([v.name for v in pb.variables], ["profile_url", "msg"])
        self.assertEqual(pb.steps[0].url, "{{profile_url}}")


if __name__ == "__main__":
    unittest.main()
