"""Tests for the playbook format (playbook.py): validation, domains, approval, variables."""

import unittest

import playbook as pbk
from playbook import Check, Fingerprint, Playbook, Step, Variable


def linkedin_playbook(**over) -> Playbook:
    base = dict(
        name="Open a profile",
        variables=[Variable(name="profile_url", type="linkedin_url")],
        steps=[Step(id="s1", kind="open", url="{{profile_url}}",
                    check=Check(kind="url_host", hosts=["linkedin.com"]))],
    )
    base.update(over)
    return pbk.refresh(Playbook(**base))


class Validation(unittest.TestCase):
    def test_minimal_playbook_is_valid(self):
        self.assertEqual(pbk.validate(linkedin_playbook()), [])

    def test_needs_name_and_steps(self):
        probs = pbk.validate(Playbook())
        self.assertTrue(any("name" in p for p in probs))
        self.assertTrue(any("no steps" in p for p in probs))

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(Exception):
            Step(id="s1", kind="open", url="https://a.com", run_this="rm -rf")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(Exception):
            Step(id="s1", kind="repeat")

    def test_open_needs_http_url(self):
        for bad in ("javascript:alert(1)", "file:///c:/x", "ftp://a.com", "a.com", "https://u:p@a.com"):
            pb = Playbook(name="x", steps=[Step(id="s1", kind="open", url=bad)])
            self.assertTrue(pbk.validate(pb), bad)

    def test_variable_may_not_be_part_of_a_url(self):
        pb = Playbook(name="x", variables=[Variable(name="u", type="url", allowed_hosts=["a.com"])],
                      steps=[Step(id="s1", kind="open", url="https://evil.com/{{u}}")])
        self.assertTrue(any("whole URL" in p for p in pbk.validate(pb)))

    def test_undefined_variable_is_reported(self):
        pb = Playbook(name="x", steps=[Step(id="s1", kind="open", url="{{nope}}")])
        self.assertTrue(any("undefined" in p for p in pbk.validate(pb)))
        pb = Playbook(name="x", steps=[
            Step(id="s1", kind="type", target=Fingerprint(node_name="input"), text="hi {{nope}}")])
        self.assertTrue(any("undefined" in p for p in pbk.validate(pb)))

    def test_text_variable_cannot_be_an_open_url(self):
        pb = Playbook(name="x", variables=[Variable(name="t", type="text")],
                      steps=[Step(id="s1", kind="open", url="{{t}}")])
        self.assertTrue(any("URL is needed" in p for p in pbk.validate(pb)))

    def test_url_variable_needs_allowed_sites(self):
        pb = Playbook(name="x", variables=[Variable(name="u", type="url")],
                      steps=[Step(id="s1", kind="open", url="{{u}}")])
        self.assertTrue(any("allowed site" in p for p in pbk.validate(pb)))

    def test_secret_only_in_type_steps(self):
        fp = Fingerprint(node_name="input")
        ok = Playbook(name="x", steps=[Step(id="s1", kind="type", target=fp, text="<secret>pw</secret>")])
        self.assertEqual(pbk.validate(ok), [])
        for step in (Step(id="s1", kind="key", keys="<secret>pw</secret>"),
                     Step(id="s1", kind="extract", tag="ai", query="find <secret>pw</secret>")):
            self.assertTrue(pbk.validate(Playbook(name="x", steps=[step])))
        in_url = Playbook(name="x", steps=[Step(id="s1", kind="open", url="https://a.com/?k=<secret>pw</secret>")])
        self.assertTrue(any("never put in a URL" in p for p in pbk.validate(in_url)))

    def test_required_fields_per_kind(self):
        for step in (Step(id="s1", kind="click"), Step(id="s1", kind="type"),
                     Step(id="s1", kind="key"), Step(id="s1", kind="wait"),
                     Step(id="s1", kind="select", target=Fingerprint(node_name="select"))):
            self.assertTrue(pbk.validate(Playbook(name="x", steps=[step])), step.kind)

    def test_ai_tag_must_match_kind(self):
        self.assertTrue(pbk.validate(Playbook(name="x", steps=[Step(id="s1", kind="extract", query="q")])))
        self.assertTrue(pbk.validate(Playbook(
            name="x", steps=[Step(id="s1", kind="wait", seconds=1, tag="ai")])))
        self.assertEqual(pbk.validate(Playbook(
            name="x", steps=[Step(id="s1", kind="extract", tag="ai", query="q")])), [])

    def test_duplicate_ids_and_variable_names(self):
        pb = Playbook(name="x", variables=[Variable(name="a"), Variable(name="a")],
                      steps=[Step(id="s1", kind="back"), Step(id="s1", kind="back")])
        probs = " ".join(pbk.validate(pb))
        self.assertIn("duplicate", probs)
        self.assertIn("defined twice", probs)

    def test_bad_variable_names_and_default(self):
        pb = Playbook(name="x", variables=[Variable(name="Bad Name"),
                                           Variable(name="n", type="number", default="abc")],
                      steps=[Step(id="s1", kind="back")])
        probs = " ".join(pbk.validate(pb))
        self.assertIn("lowercase", probs)
        self.assertIn("must be a number", probs)

    def test_step_limit(self):
        pb = Playbook(name="x", steps=[Step(id=f"s{i}", kind="back") for i in range(pbk.MAX_STEPS + 1)])
        self.assertTrue(any("too many" in p for p in pbk.validate(pb)))

    def test_control_characters_rejected(self):
        pb = Playbook(name="x", steps=[Step(id="s1", kind="key", keys="a\x00b")])
        self.assertTrue(pbk.validate(pb))

    def test_other_schema_version_rejected(self):
        pb = linkedin_playbook(schema_version=99)
        self.assertTrue(any("schema version" in p for p in pbk.validate(pb)))


class NewKinds(unittest.TestCase):
    """search, tab and handoff steps."""
    def ok(self, *steps, variables=None):
        return Playbook(name="x", steps=list(steps), variables=variables or [])

    def test_a_search_step(self):
        pb = self.ok(Step(id="s1", kind="search", engine="google", query="red shoes"))
        self.assertEqual(pbk.validate(pb), [])
        self.assertEqual(pbk.compute_domains(pb), ["www.google.com"])

    def test_an_empty_search_is_refused(self):
        for q in ("", "   "):
            self.assertTrue(any("something to search for" in p for p in pbk.validate(
                self.ok(Step(id="s1", kind="search", engine="bing", query=q)))))

    def test_a_search_step_needs_a_query_and_an_engine(self):
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="search", engine="google"))))
        self.assertTrue(any("engine" in p for p in pbk.validate(self.ok(Step(id="s1", kind="search", query="x")))))
        with self.assertRaises(Exception):
            Step(id="s1", kind="search", engine="altavista", query="x")

    def test_a_search_query_can_be_a_variable_and_cannot_change_the_site(self):
        pb = self.ok(Step(id="s1", kind="search", engine="bing", query="{{term}}"), variables=[Variable(name="term")])
        self.assertEqual(pbk.validate(pb), [])
        url = pbk.search_url("bing", "a&b=c d#x")
        self.assertEqual(url, "https://www.bing.com/search?q=a%26b%3Dc+d%23x")
        evil = pbk.search_url("duckduckgo", "x&redirect=https://evil.org")
        self.assertEqual(urlparse_host(evil), "duckduckgo.com")
        self.assertNotIn("&redirect", evil)

    def test_an_undefined_search_variable_is_reported_and_a_secret_is_refused(self):
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="search", engine="bing", query="{{nope}}"))))
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="search", engine="bing", query="<secret>pw</secret>"))))

    def test_a_search_step_carries_only_its_own_fields(self):
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="search", engine="bing", query="x", url="https://a.com"))))
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="open", url="https://a.com", engine="bing"))))

    def test_a_tab_step(self):
        self.assertEqual(pbk.validate(self.ok(Step(id="s1", kind="tab", tab_index=1))), [])
        self.assertEqual(pbk.validate(self.ok(Step(id="s1", kind="tab", tab_index=0, close_tab=True))), [])
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="tab"))))
        with self.assertRaises(Exception):
            Step(id="s1", kind="tab", tab_index=-1)
        with self.assertRaises(Exception):
            Step(id="s1", kind="tab", tab_index=99)

    def test_a_handoff_step(self):
        self.assertEqual(pbk.validate(self.ok(Step(id="s1", kind="handoff", message="Sign in, then continue."))), [])
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="handoff"))))
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="handoff", message="   "))))
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="handoff", message="Type {{x}}"))))
        self.assertTrue(pbk.validate(self.ok(Step(id="s1", kind="handoff", message="use <secret>pw</secret>"))))
        with self.assertRaises(Exception):
            Step(id="s1", kind="handoff", message="x" * 501)

    def test_none_of_them_need_a_model(self):
        for step in (Step(id="s1", kind="search", engine="bing", query="x"), Step(id="s1", kind="tab", tab_index=0),
                     Step(id="s1", kind="handoff", message="m")):
            self.assertEqual(pbk.validate(self.ok(step)), [])
        bad = Step(id="s1", kind="tab", tab_index=0, tag="ai")
        self.assertTrue(pbk.validate(self.ok(bad)))

    def test_the_search_engine_is_part_of_the_approval(self):
        a = pbk.with_approval(self.ok(Step(id="s1", kind="search", engine="bing", query="x")))
        b = a.model_copy(update={"steps": [a.steps[0].model_copy(update={"engine": "google"})]})
        self.assertFalse(pbk.is_approved(b))


def urlparse_host(url):
    from urllib.parse import urlparse
    return urlparse(url).hostname


class HashesAreText(unittest.TestCase):
    def test_a_64_bit_hash_survives_a_trip_through_javascript(self):
        import json
        big = 2 ** 63 + 12345
        fp = Fingerprint(node_name="a", element_hash=big, stable_hash=big - 1)
        self.assertEqual((fp.element_hash, fp.stable_hash), (str(big), str(big - 1)))
        wire = json.dumps(fp.model_dump(mode="json"))
        self.assertIn(f'"{big}"', wire)                       # a string on the wire, not a number
        back = Fingerprint.model_validate_json(wire)
        self.assertEqual(back, fp)
        # what a JS number would have done to it:
        self.assertNotEqual(int(float(big)), big)

    def test_none_and_bool_are_not_hashes(self):
        fp = Fingerprint(element_hash=None, stable_hash=True)
        self.assertEqual((fp.element_hash, fp.stable_hash), (None, None))

    def test_oversized_hash_text_is_refused(self):
        with self.assertRaises(Exception):
            Fingerprint(element_hash="1" * 41)


class UnusedFields(unittest.TestCase):
    """A step may only carry the fields its kind uses (nothing a later player could act on unseen)."""
    def test_each_kind_refuses_fields_it_does_not_use(self):
        fp = Fingerprint(node_name="input")
        cases = [
            Step(id="s1", kind="open", url="https://a.com", target=fp),
            Step(id="s1", kind="open", url="https://a.com", text="x"),
            Step(id="s1", kind="back", url="https://a.com"),
            Step(id="s1", kind="click", target=fp, text="x"),
            Step(id="s1", kind="click", target=fp, keys="Enter"),
            Step(id="s1", kind="key", keys="Enter", query="x"),
            Step(id="s1", kind="wait", seconds=1, target=fp),
            Step(id="s1", kind="type", target=fp, text="x", seconds=3),
            Step(id="s1", kind="extract", tag="ai", query="q", url="https://a.com"),
            Step(id="s1", kind="scroll", text="x"),
            Step(id="s1", kind="select", target=fp, text="x", query="q"),
        ]
        for step in cases:
            probs = pbk.validate(Playbook(name="x", steps=[step]))
            self.assertTrue(any("has no" in p for p in probs), (step.kind, probs))

    def test_the_fields_each_kind_does_use_are_accepted(self):
        fp = Fingerprint(node_name="input")
        ok = [Step(id="s1", kind="open", url="https://a.com"), Step(id="s2", kind="back"),
              Step(id="s3", kind="click", target=fp), Step(id="s4", kind="type", target=fp, text="x"),
              Step(id="s5", kind="select", target=fp, text="x"), Step(id="s6", kind="key", keys="Enter"),
              Step(id="s7", kind="scroll", target=fp), Step(id="s8", kind="scroll"),
              Step(id="s9", kind="wait", seconds=2),
              Step(id="s10", kind="extract", tag="ai", query="q"), Step(id="s11", kind="ai", tag="ai", query="q")]
        self.assertEqual(pbk.validate(Playbook(name="x", steps=ok)), [])


class SiteNames(unittest.TestCase):
    def test_overbroad_or_malformed_sites_are_refused(self):
        for bad in ("com", "*.com", "co.uk", "com.au", "https://a.com", "a.com/path", "a.com:8080", "", " ",
                    "a..com", "-a.com", "a b.com", ".com", "a.c om"):
            self.assertIsNotNone(pbk.host_problem(bad), bad)

    def test_normal_sites_are_accepted(self):
        for ok in ("linkedin.com", "WWW.Example.org", "shop.example.co.uk", "localhost", "xn--bcher-kva.de",
                   "192.168.1.1", "example.com."):
            self.assertIsNone(pbk.host_problem(ok), ok)

    def test_validate_checks_variable_and_check_hosts(self):
        pb = Playbook(name="x", variables=[Variable(name="u", type="url", allowed_hosts=["com"])],
                      steps=[Step(id="s1", kind="open", url="{{u}}"),
                             Step(id="s2", kind="back", check=Check(hosts=["*.com"]))])
        probs = " ".join(pbk.validate(pb))
        self.assertIn("variable 'u'", probs)
        self.assertIn("step s2 check", probs)

    def test_a_bare_tld_cannot_widen_the_allowed_sites(self):
        # without the check, "com" would match every .com host via the subdomain rule
        self.assertTrue(pbk.host_allowed("evil.com", ["com"]))
        pb = Playbook(name="x", variables=[Variable(name="u", type="url", allowed_hosts=["com"])],
                      steps=[Step(id="s1", kind="open", url="{{u}}")])
        self.assertTrue(pbk.validate(pb))


class Sizes(unittest.TestCase):
    def test_oversized_fields_are_refused(self):
        with self.assertRaises(Exception):
            Variable(name="a", label="x" * 101)
        with self.assertRaises(Exception):
            Fingerprint(x_path="x" * 2001)
        with self.assertRaises(Exception):
            Check(hosts=["a.com"] * 21)
        with self.assertRaises(Exception):
            Playbook(name="x", variables=[Variable(name=f"v{i}") for i in range(pbk.MAX_VARIABLES + 1)])

    def test_big_element_description_is_refused(self):
        fp = Fingerprint(node_name="input", attributes={f"k{i}": "v" for i in range(pbk.MAX_ATTRS + 1)})
        pb = Playbook(name="x", steps=[Step(id="s1", kind="click", target=fp)])
        self.assertTrue(any("too large" in p for p in pbk.validate(pb)))
        fp = Fingerprint(node_name="input", attributes={"id": "v" * (pbk.MAX_ATTR_VALUE + 1)})
        pb = Playbook(name="x", steps=[Step(id="s1", kind="click", target=fp)])
        self.assertTrue(any("too large" in p for p in pbk.validate(pb)))


class Hosts(unittest.TestCase):
    def test_subdomains_allowed_lookalikes_not(self):
        self.assertTrue(pbk.host_allowed("www.linkedin.com", ["linkedin.com"]))
        self.assertTrue(pbk.host_allowed("linkedin.com", ["linkedin.com"]))
        self.assertFalse(pbk.host_allowed("evil-linkedin.com", ["linkedin.com"]))
        self.assertFalse(pbk.host_allowed("linkedin.com.evil.net", ["linkedin.com"]))
        self.assertFalse(pbk.host_allowed("", ["linkedin.com"]))
        self.assertFalse(pbk.host_allowed("a.com", []))

    def test_domains_from_literal_and_variable(self):
        pb = Playbook(
            name="x", variables=[Variable(name="u", type="url", allowed_hosts=["Shop.example.com"])],
            steps=[Step(id="s1", kind="open", url="https://www.Example.org/a"),
                   Step(id="s2", kind="open", url="{{u}}")])
        self.assertEqual(pbk.compute_domains(pb), ["shop.example.com", "www.example.org"])

    def test_a_site_covers_its_subdomains_in_the_list(self):
        pb = Playbook(name="x", steps=[
            Step(id="s1", kind="open", url="https://linkedin.com/in/a", check=Check(hosts=["www.linkedin.com"])),
            Step(id="s2", kind="open", url="https://notlinkedin.com/")])
        self.assertEqual(pbk.compute_domains(pb), ["linkedin.com", "notlinkedin.com"])

    def test_linkedin_variable_implies_linkedin(self):
        self.assertEqual(pbk.compute_domains(linkedin_playbook()), ["linkedin.com"])


class Values(unittest.TestCase):
    def setUp(self):
        self.pb = linkedin_playbook()

    def test_good_profile_urls(self):
        for url in ("https://www.linkedin.com/in/jane-doe", "https://linkedin.com/in/jane-doe/",
                    "https://uk.linkedin.com/in/jane-doe?trk=x", "https://www.linkedin.com/in/jane%C3%A9"):
            values, errors = pbk.bind_values(self.pb, {"profile_url": url})
            self.assertEqual(errors, [], url)
            self.assertEqual(values["profile_url"], url)

    def test_bad_profile_urls(self):
        for url in ("http://www.linkedin.com/in/jane", "https://www.linkedin.com/company/x",
                    "https://evil.com/in/jane", "https://linkedin.com.evil.net/in/jane",
                    "https://evil-linkedin.com/in/jane", "https://www.linkedin.com/in/", "javascript:alert(1)",
                    "https://www.linkedin.com/in/jane\nhttps://evil.com", "not a url"):
            _, errors = pbk.bind_values(self.pb, {"profile_url": url})
            self.assertTrue(errors, url)

    def test_missing_required_and_unknown(self):
        _, errors = pbk.bind_values(self.pb, {})
        self.assertTrue(any("required" in e for e in errors))
        _, errors = pbk.bind_values(self.pb, {"profile_url": "https://www.linkedin.com/in/a", "zzz": "1"})
        self.assertTrue(any("not a variable" in e for e in errors))

    def test_default_used_when_blank(self):
        pb = Playbook(name="x", variables=[Variable(name="q", default="cats")],
                      steps=[Step(id="s1", kind="key", keys="Enter")])
        self.assertEqual(pbk.bind_values(pb, {"q": ""}), ({"q": "cats"}, []))

    def test_optional_variable_may_be_empty(self):
        pb = Playbook(name="x", variables=[Variable(name="q", required=False)],
                      steps=[Step(id="s1", kind="key", keys="Enter")])
        self.assertEqual(pbk.bind_values(pb, {}), ({}, []))

    def test_url_variable_stays_on_its_sites(self):
        pb = Playbook(name="x", variables=[Variable(name="u", type="url", allowed_hosts=["a.com"])],
                      steps=[Step(id="s1", kind="open", url="{{u}}")])
        self.assertEqual(pbk.bind_values(pb, {"u": "https://www.a.com/x"})[1], [])
        self.assertTrue(pbk.bind_values(pb, {"u": "https://b.com/x"})[1])
        self.assertTrue(pbk.bind_values(pb, {"u": "https://a.com.evil.net"})[1])

    def test_number_and_secret_and_length(self):
        pb = Playbook(name="x", variables=[Variable(name="n", type="number"), Variable(name="t")],
                      steps=[Step(id="s1", kind="back")])
        self.assertTrue(pbk.bind_values(pb, {"n": "abc", "t": "x"})[1])
        self.assertEqual(pbk.bind_values(pb, {"n": "3.5", "t": "x"})[1], [])
        self.assertTrue(pbk.bind_values(pb, {"n": "1", "t": "<secret>pw</secret>"})[1])
        self.assertTrue(pbk.bind_values(pb, {"n": "1", "t": "x" * (pbk.MAX_TEXT + 1)})[1])

    def test_render(self):
        self.assertEqual(pbk.render("a {{x}} b {{ y }}", {"x": "1", "y": "2"}), "a 1 b 2")
        self.assertEqual(pbk.render("<secret>pw</secret> {{x}}", {"x": "1"}), "<secret>pw</secret> 1")
        self.assertEqual(pbk.render("{{missing}}", {}), "{{missing}}")
        self.assertIsNone(pbk.render(None, {}))

    def test_render_does_not_expand_inside_values(self):
        # A value containing {{other}} must not be re-expanded (no template injection).
        self.assertEqual(pbk.render("{{a}}", {"a": "{{b}}", "b": "SECRET"}), "{{b}}")


class Approval(unittest.TestCase):
    def test_approval_tracks_exact_steps(self):
        pb = pbk.with_approval(linkedin_playbook())
        self.assertTrue(pbk.is_approved(pb))
        edited = pb.model_copy(update={"steps": [pb.steps[0].model_copy(update={"new_tab": True})]})
        self.assertFalse(pbk.is_approved(edited))

    def test_renaming_and_notes_keep_approval(self):
        pb = pbk.with_approval(linkedin_playbook())
        renamed = pb.model_copy(update={"name": "Another name",
                                        "steps": [pb.steps[0].model_copy(update={"note": "hello"})]})
        self.assertTrue(pbk.is_approved(renamed))

    def test_widening_a_variable_drops_approval(self):
        pb = pbk.with_approval(Playbook(
            name="x", variables=[Variable(name="u", type="url", allowed_hosts=["a.com"])],
            steps=[Step(id="s1", kind="open", url="{{u}}")]))
        wider = pb.model_copy(update={"variables": [pb.variables[0].model_copy(
            update={"allowed_hosts": ["a.com", "b.com"]})]})
        self.assertFalse(pbk.is_approved(wider))

    def test_not_approved_by_default(self):
        self.assertFalse(pbk.is_approved(linkedin_playbook()))

    def test_refresh_drops_a_stale_approval(self):
        pb = pbk.with_approval(linkedin_playbook())
        edited = pb.model_copy(update={"steps": [pb.steps[0].model_copy(update={"new_tab": True})]})
        self.assertIsNone(pbk.refresh(edited).approval)
        self.assertIsNotNone(pbk.refresh(pb).approval)

    def test_ask_steps(self):
        pb = Playbook(name="x", steps=[Step(id="s1", kind="key", keys="Enter", ask=True),
                                       Step(id="s2", kind="back")])
        self.assertEqual([s.id for s in pbk.ask_steps(pb)], ["s1"])


class MakeVariable(unittest.TestCase):
    def base(self):
        return Playbook(name="x", steps=[
            Step(id="s1", kind="open", url="https://www.linkedin.com/in/jane"),
            Step(id="s2", kind="type", target=Fingerprint(node_name="input"), text="hello")])

    def test_url_becomes_linkedin_variable(self):
        out = pbk.make_variable(self.base(), "s1", "url", "profile_url", "linkedin_url")
        self.assertEqual(out.steps[0].url, "{{profile_url}}")
        self.assertEqual(out.variables[0].default, "https://www.linkedin.com/in/jane")
        self.assertEqual(out.domains, ["linkedin.com"])
        self.assertEqual(pbk.validate(out), [])

    def test_url_variable_defaults_allowed_host(self):
        pb = Playbook(name="x", steps=[Step(id="s1", kind="open", url="https://shop.example.com/a")])
        out = pbk.make_variable(pb, "s1", "url", "page", "url")
        self.assertEqual(out.variables[0].allowed_hosts, ["shop.example.com"])
        self.assertEqual(pbk.validate(out), [])

    def test_text_becomes_variable(self):
        out = pbk.make_variable(self.base(), "s2", "text", "greeting", "text")
        self.assertEqual(out.steps[1].text, "{{greeting}}")
        self.assertEqual(pbk.validate(out), [])

    def test_approval_is_dropped(self):
        pb = pbk.with_approval(self.base())
        self.assertIsNone(pbk.make_variable(pb, "s2", "text", "greeting", "text").approval)

    def test_original_is_not_changed(self):
        pb = self.base()
        pbk.make_variable(pb, "s2", "text", "greeting", "text")
        self.assertEqual(pb.steps[1].text, "hello")
        self.assertEqual(pb.variables, [])

    def test_refusals(self):
        pb = self.base()
        cases = [("s9", "url", "a", "text"), ("s1", "keys", "a", "text"), ("s1", "url", "Bad", "text"),
                 ("s1", "url", "a", "weird"), ("s2", "url", "a", "text"),
                 ("s2", "text", "a", "number")]                           # "hello" is not a number
        for sid, field, name, vtype in cases:
            with self.assertRaises(ValueError, msg=(sid, field, name, vtype)):
                pbk.make_variable(pb, sid, field, name, vtype)
        pbk.make_variable(pb, "s1", "url", "a", "linkedin_url")
        twice = pbk.make_variable(pb, "s2", "text", "a", "text")
        with self.assertRaises(ValueError):
            pbk.make_variable(twice, "s1", "url", "a", "linkedin_url")     # name taken
        with self.assertRaises(ValueError):
            pbk.make_variable(twice, "s2", "text", "b", "text")            # already a variable

    def test_secret_cannot_become_variable(self):
        pb = Playbook(name="x", steps=[Step(id="s1", kind="type", target=Fingerprint(node_name="input"),
                                            text="<secret>pw</secret>")])
        with self.assertRaises(ValueError):
            pbk.make_variable(pb, "s1", "text", "pw", "text")

    def test_linkedin_variable_rejects_other_site(self):
        pb = Playbook(name="x", steps=[Step(id="s1", kind="open", url="https://evil.com/in/x")])
        with self.assertRaises(ValueError):
            pbk.make_variable(pb, "s1", "url", "p", "linkedin_url")


if __name__ == "__main__":
    unittest.main()
