"""Prices the user enters (per million tokens) and the cost estimate built from them. The app ships no prices."""
import os as _os
import tempfile as _tf
_os.environ.setdefault("AIBA_DATA_DIR", _tf.mkdtemp())   # tests never touch real data

import pathlib
import unittest
from unittest import mock

from aiba.server import main as agent_ui
from aiba.llm import tiers as mt
from test_model_tiers_app import KEYS, Base


class CleanPrice(unittest.TestCase):
    def test_numbers_in_range_are_kept(self):
        for v, want in ((0, 0.0), (3, 3.0), (0.25, 0.25), (99999.5, 99999.5), (1.23456789, 1.234568)):
            self.assertEqual(mt.clean_price(v), want)

    def test_everything_else_is_refused(self):
        for v in (-1, -0.01, 100001, float("nan"), float("inf"), True, False, "3", None, [3], {"a": 1}):
            self.assertIsNone(mt.clean_price(v), v)


class PriceBook(unittest.TestCase):
    def setUp(self):
        self.path = pathlib.Path(_tf.mkdtemp()) / "p.json"

    def test_a_price_is_saved_and_survives_a_restart(self):
        mt.Prices(self.path).set("m1", 3, 15)
        self.assertEqual(mt.Prices(self.path).all(), {"m1": {"in": 3.0, "out": 15.0}})

    def test_both_prices_are_needed(self):
        book = mt.Prices(self.path)
        for a, b in ((3, None), (None, 3), (-1, 5), (3, "x"), (True, 2)):
            with self.assertRaises(ValueError, msg=(a, b)):
                book.set("m1", a, b)
        self.assertEqual(book.all(), {})

    def test_both_empty_removes_it(self):
        book = mt.Prices(self.path)
        book.set("m1", 1, 2)
        book.set("m1", None, None)
        self.assertEqual(mt.Prices(self.path).all(), {})

    def test_a_zero_price_is_a_real_price(self):
        book = mt.Prices(self.path)
        book.set("free-model", 0, 0)
        self.assertEqual(book.all(), {"free-model": {"in": 0.0, "out": 0.0}})

    def test_a_model_name_is_needed_and_bounded(self):
        book = mt.Prices(self.path)
        for name in ("", "   ", "x" * 101):
            with self.assertRaises(ValueError):
                book.set(name, 1, 1)

    def test_the_book_cannot_grow_without_limit(self):
        book = mt.Prices(self.path)
        for i in range(200):
            book.set(f"m{i}", 1, 1)
        with self.assertRaises(ValueError):
            book.set("one-more", 1, 1)
        book.set("m5", 2, 2)                                   # changing one that exists is still fine

    def test_a_damaged_file_means_no_prices_not_a_crash(self):
        for text in ("{not json", "[1,2]", '{"m": 5}', '{"m": {"in": "a", "out": 1}}', '{"m": {"in": -1, "out": 1}}', ""):
            self.path.write_text(text, encoding="utf-8")
            self.assertEqual(mt.Prices(self.path).all(), {}, text)

    def test_a_failed_write_leaves_the_book_as_it_was(self):
        book = mt.Prices(self.path)
        book.set("m1", 1, 2)
        with mock.patch.object(mt, "_atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                book.set("m1", 9, 9)
            with self.assertRaises(OSError):
                book.set("m2", 1, 1)
        self.assertEqual(book.all(), {"m1": {"in": 1.0, "out": 2.0}})

    def test_what_is_returned_cannot_be_used_to_change_the_book(self):
        book = mt.Prices(self.path)
        book.set("m1", 1, 2)
        book.all()["m1"]["in"] = 999
        self.assertEqual(book.all()["m1"]["in"], 1.0)


class Estimate(unittest.TestCase):
    def ledger(self):
        led = mt.Ledger(pathlib.Path(_tf.mkdtemp()) / "u.json")
        led.add("fast", "cheap", 2_000_000, 500_000)
        led.add("standard", "mid", 1_000_000, 100_000)
        led.add("strong", "big", 100_000, 10_000)
        return led

    def test_cost_is_tokens_times_the_users_prices(self):
        cost = mt.estimate_cost(self.ledger().summary(), {"cheap": {"in": 1.0, "out": 5.0}, "mid": {"in": 3.0, "out": 15.0},
                                                          "big": {"in": 15.0, "out": 75.0}})
        self.assertAlmostEqual(cost["per_tier"]["fast"], 2 * 1.0 + 0.5 * 5.0)
        self.assertAlmostEqual(cost["per_tier"]["standard"], 3.0 + 1.5)
        self.assertAlmostEqual(cost["per_tier"]["strong"], 1.5 + 0.75)
        self.assertAlmostEqual(cost["total"], 4.5 + 4.5 + 2.25)
        self.assertEqual(cost["unpriced_tokens"], 0)

    def test_a_model_without_a_price_is_counted_apart_never_guessed(self):
        cost = mt.estimate_cost(self.ledger().summary(), {"mid": {"in": 3.0, "out": 15.0}})
        self.assertAlmostEqual(cost["total"], 4.5)
        self.assertEqual(cost["unpriced_tokens"], 2_500_000 + 110_000)

    def test_no_prices_no_cost(self):
        cost = mt.estimate_cost(self.ledger().summary(), {})
        self.assertEqual((cost["total"], cost["unpriced_tokens"]), (0, 2_500_000 + 1_100_000 + 110_000))

    def test_the_same_model_in_two_tiers_is_priced_in_each(self):
        led = mt.Ledger(pathlib.Path(_tf.mkdtemp()) / "u.json")
        led.add("standard", "m", 1_000_000, 0)
        led.add("fast", "m", 1_000_000, 0)
        cost = mt.estimate_cost(led.summary(), {"m": {"in": 2.0, "out": 0.0}})
        self.assertEqual((cost["per_tier"]["standard"], cost["per_tier"]["fast"], cost["total"]), (2.0, 2.0, 4.0))

    def test_only_the_asked_days_are_priced(self):
        from datetime import date, timedelta
        today = [date(2026, 10, 10)]
        led = mt.Ledger(pathlib.Path(_tf.mkdtemp()) / "u.json", today=lambda: today[0])
        led.add("standard", "m", 1_000_000, 0)
        today[0] += timedelta(days=10)
        led.add("standard", "m", 1_000_000, 0)
        self.assertEqual(mt.estimate_cost(led.summary(days=3), {"m": {"in": 1.0, "out": 0.0}})["total"], 1.0)
        self.assertEqual(mt.estimate_cost(led.summary(days=30), {"m": {"in": 1.0, "out": 0.0}})["total"], 2.0)


class Endpoints(Base):
    def setUp(self):
        super().setUp()
        self.book = mt.Prices(self.tmp / "prices.json")
        p = mock.patch.object(agent_ui, "_prices", self.book)
        p.start()
        self.addCleanup(p.stop)
        from fastapi.testclient import TestClient
        self.c = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.c.get("/")
        self.env(**KEYS)

    def test_a_price_is_saved_and_the_usage_endpoint_estimates_with_it(self):
        self.ledger.add("standard", "claude-sonnet-5-5", 2_000_000, 1_000_000)
        r = self.c.post("/models/prices", json={"model": "claude-sonnet-5-5", "input": 3, "output": 15})
        self.assertEqual(r.status_code, 200, r.text)
        u = self.c.get("/models/usage").json()
        self.assertEqual(u["prices"], {"claude-sonnet-5-5": {"in": 3.0, "out": 15.0}})
        self.assertAlmostEqual(u["cost"]["total"], 6.0 + 15.0)
        self.assertEqual(u["cost"]["unpriced_tokens"], 0)

    def test_bad_prices_are_refused_with_a_reason(self):
        for body in ({"model": "m", "input": -1, "output": 1}, {"model": "m", "input": "3", "output": 1},
                     {"model": "m", "input": 1}, {"model": "", "input": 1, "output": 1}, {"model": 5, "input": 1, "output": 1},
                     {"input": 1, "output": 1}, {"model": "m", "input": True, "output": 1}):
            r = self.c.post("/models/prices", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertFalse(r.json()["ok"])
            self.assertTrue(r.json()["reason"])
        self.assertEqual(self.book.all(), {})

    def test_null_prices_remove_it(self):
        self.c.post("/models/prices", json={"model": "m", "input": 1, "output": 1})
        r = self.c.post("/models/prices", json={"model": "m", "input": None, "output": None})
        self.assertEqual(r.json()["prices"], {})

    def test_a_disk_problem_is_reported_not_a_crash(self):
        with mock.patch.object(self.book, "set", side_effect=OSError("disk full")):
            r = self.c.post("/models/prices", json={"model": "m", "input": 1, "output": 1})
        self.assertEqual(r.status_code, 500)
        self.assertFalse(r.json()["ok"])

    def test_the_endpoint_needs_the_app_cookie(self):
        from fastapi.testclient import TestClient
        stranger = TestClient(agent_ui.app, base_url="http://localhost:8000")
        self.assertIn(stranger.post("/models/prices", json={"model": "m", "input": 1, "output": 1}).status_code, (401, 403))
        self.assertEqual(self.book.all(), {})


if __name__ == "__main__":
    unittest.main()
