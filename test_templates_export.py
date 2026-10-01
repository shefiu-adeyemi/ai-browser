"""Tests for playbook_templates.py and results_export.py."""

import csv
import io
import unittest
from datetime import datetime

import playbook as pbk
import playbook_templates as tpl
from results_export import runs_to_csv


class Templates(unittest.TestCase):
    def test_every_template_is_valid_unapproved_and_fresh(self):
        for t in tpl.list_templates():
            pb = tpl.build(t["id"])
            self.assertEqual(pbk.validate(pb), [], t["id"])
            self.assertFalse(pbk.is_approved(pb), "a template must be approved by the user, never pre-approved")
            self.assertEqual(pb.id, "")
            self.assertIsNot(tpl.build(t["id"]), pb)

    def test_the_list_describes_them(self):
        ids = {t["id"]: t for t in tpl.list_templates()}
        self.assertEqual(set(ids), {"linkedin_profile", "web_search"})
        self.assertTrue(all(t["name"] and t["description"] for t in ids.values()))

    def test_the_linkedin_template_only_opens_linkedin_profiles(self):
        pb = tpl.build("linkedin_profile")
        self.assertEqual(pb.domains, ["linkedin.com"])
        values, errors = pbk.bind_values(pb, {"profile_url": "https://www.linkedin.com/in/jane"})
        self.assertEqual(errors, [])
        self.assertTrue(pbk.bind_values(pb, {"profile_url": "https://evil.com/in/jane"})[1])

    def test_the_search_template(self):
        pb = tpl.build("web_search")
        self.assertEqual(pb.domains, ["duckduckgo.com"])
        self.assertEqual(pbk.bind_values(pb, {"term": "red shoes"})[1], [])

    def test_an_unknown_template(self):
        with self.assertRaises(KeyError):
            tpl.build("nope")


class Csv(unittest.TestCase):
    def rows(self, records):
        return list(csv.reader(io.StringIO(runs_to_csv(records))))

    def rec(self, **kw):
        base = {"started_at": datetime(2026, 10, 1, 9, 30, 0).timestamp(), "status": "done", "attended": True,
                "inputs": {"p": "https://www.linkedin.com/in/jane"}, "outputs": [{"step_id": "s2", "text": "Jane, Engineer"}], "error": ""}
        base.update(kw)
        return base

    def test_one_row_per_run_oldest_first_with_the_inputs_as_columns(self):
        rows = self.rows([self.rec(started_at=datetime(2026, 10, 2).timestamp(), inputs={"p": "b"}),
                          self.rec(started_at=datetime(2026, 10, 1).timestamp(), inputs={"p": "a", "q": "x"})])
        self.assertEqual(rows[0], ["Started", "Status", "How", "p", "q", "Result", "Error"])
        self.assertEqual([r[3] for r in rows[1:]], ["a", "b"])
        self.assertEqual(rows[1][4], "x")
        self.assertEqual(rows[2][4], "")                                     # a run without that input
        self.assertEqual(rows[1][0], "2026-10-01 00:00:00")

    def test_what_it_read_how_it_started_and_the_error(self):
        rows = self.rows([self.rec(outputs=[{"text": "one"}, {"text": "two"}], attended=False, status="failed", error="Step 2 stopped")])
        self.assertEqual(rows[1][1:3], ["failed", "scheduled"])
        self.assertEqual(rows[1][-2:], ["one | two", "Step 2 stopped"])
        self.assertEqual(self.rows([self.rec()])[1][2], "by hand")

    def test_a_page_cannot_plant_a_spreadsheet_formula(self):
        rows = self.rows([self.rec(outputs=[{"text": "=HYPERLINK(\"http://evil\",\"click\")"}],
                                   inputs={"p": "+1+1"}, error="@SUM(A1)")])
        self.assertEqual(rows[1][3], "'+1+1")
        self.assertTrue(rows[1][-2].startswith("'="))
        self.assertTrue(rows[1][-1].startswith("'@"))
        for text in ("-2+3", "\t=x", "\r=x"):
            self.assertTrue(self.rows([self.rec(outputs=[{"text": text}])])[1][-2].startswith("'"), repr(text))
        self.assertEqual(self.rows([self.rec(outputs=[{"text": "a = b"}])])[1][-2], "a = b")      # an ordinary = is fine

    def test_commas_quotes_newlines_and_unicode_survive(self):
        text = 'He said "hi", then\nleft — José 日本'
        self.assertEqual(self.rows([self.rec(outputs=[{"text": text}])])[1][-2], text)

    def test_nothing_to_export_is_just_a_header(self):
        self.assertEqual(self.rows([]), [["Started", "Status", "How", "Result", "Error"]])

    def test_odd_records_do_not_break_it(self):
        rows = self.rows([{}, {"started_at": "soon", "inputs": None, "outputs": None}, self.rec(outputs=[{"nope": 1}])])
        self.assertEqual(len(rows), 4)

    def test_a_huge_cell_is_cut(self):
        rows = self.rows([self.rec(outputs=[{"text": "x" * 100000}])])
        self.assertEqual(len(rows[1][-2]), 32000)


if __name__ == "__main__":
    unittest.main()
