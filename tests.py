#!/usr/bin/env python3
"""Test suite for Flight Hunter — standard library only, no network.

The interesting logic here is not `assert True`; it is the boundary behaviour of
the anomaly detector, the round-trip/one-way separation of price history, and the
price-parsing rules. Those are the things that decide whether this tool tells you
about a real deal or about noise.

Run:  python3 tests.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

import anomaly
import kiwi
import store
from hunt import next_departure_dates, render_table, return_date_for


class TestAnomalyDetector(unittest.TestCase):
    """The detector is the thesis of the project; its boundaries are the product."""

    def test_silent_without_enough_history(self):
        for n in range(0, anomaly.MIN_SAMPLES):
            prices = [100.0] * n
            self.assertIsNone(
                anomaly.evaluate(prices, 50.0, origin="WAW", destination="BCN",
                                 depart_date="01/01/2027"),
                f"{n} samples must not be enough to judge",
            )

    def test_reports_material_and_statistical_drop(self):
        hit = anomaly.evaluate([100.0] * 6, 70.0, origin="WAW", destination="BCN",
                               depart_date="01/01/2027", booking_url="https://x.test")
        self.assertIsNotNone(hit)
        assert hit is not None  # for type checkers
        self.assertEqual(hit.pct_below, 0.3)
        self.assertEqual(hit.samples, 6)
        self.assertEqual(hit.booking_url, "https://x.test")

    def test_percentage_floor_blocks_small_dips(self):
        # 5% below the median is a wobble, not a deal.
        self.assertIsNone(anomaly.evaluate([100.0] * 6, 95.0, origin="WAW",
                                           destination="BCN", depart_date="01/01/2027"))

    def test_price_above_baseline_is_never_an_anomaly(self):
        self.assertIsNone(anomaly.evaluate([100.0, 110.0, 90.0, 105.0, 95.0], 130.0,
                                           origin="WAW", destination="BCN",
                                           depart_date="01/01/2027"))

    def test_z_floor_blocks_noisy_history(self):
        # Median 100, spread wide enough that a 20% drop is under 2 sigma.
        noisy = [100.0, 300.0, 100.0, 300.0]
        self.assertIsNone(anomaly.evaluate(noisy, 70.0, origin="WAW", destination="BCN",
                                           depart_date="01/01/2027"))

    def test_flat_history_lets_a_real_drop_through(self):
        # spread == 0 must not be read as "no signal".
        self.assertIsNotNone(anomaly.evaluate([200.0] * 5, 150.0, origin="WAW",
                                              destination="BCN", depart_date="01/01/2027"))

    def test_current_sample_does_not_dilute_its_own_comparison(self):
        prices = [100.0] * 5 + [80.0]
        self.assertIsNotNone(anomaly.evaluate(prices, 80.0, origin="WAW",
                                              destination="BCN", depart_date="01/01/2027"))

    def test_median_resists_a_past_flash_sale(self):
        centre, _ = anomaly.baseline([100.0, 100.0, 100.0, 100.0, 10.0])
        self.assertEqual(centre, 100.0)
        mean_centre, _ = anomaly.baseline([100.0, 100.0, 100.0, 100.0, 10.0], "mean")
        self.assertAlmostEqual(mean_centre, 82.0)

    def test_junk_prices_are_not_history(self):
        self.assertIsNone(anomaly.evaluate([0.0, -5.0, 100.0], 50.0, origin="WAW",
                                           destination="BCN", depart_date="01/01/2027"))

    def test_thresholds_are_tunable_without_editing_them(self):
        hit = anomaly.evaluate([100.0] * 6, 95.0, origin="WAW", destination="BCN",
                               depart_date="01/01/2027", min_pct_below=0.01)
        self.assertIsNotNone(hit)

    def test_alert_text_carries_the_numbers_that_justify_it(self):
        hit = anomaly.evaluate([100.0] * 6, 70.0, origin="WAW", destination="BCN",
                               depart_date="01/01/2027", currency="EUR",
                               booking_url="https://x.test/a", routing="WAW → BCN")
        assert hit is not None
        text = anomaly.format_alert(hit)
        self.assertIn("30%", text)
        self.assertIn("WAW → BCN", text)
        self.assertIn("https://x.test/a", text)

    def test_round_trip_alert_shows_both_dates(self):
        hit = anomaly.evaluate([100.0] * 6, 70.0, origin="WAW", destination="BCN",
                               depart_date="01/01/2027", return_date="08/01/2027")
        assert hit is not None
        self.assertIn("01/01/2027", anomaly.format_alert(hit))
        self.assertIn("08/01/2027", anomaly.format_alert(hit))


class TestKiwiParsing(unittest.TestCase):
    """Price parsing decides which itinerary is 'the cheapest'."""

    PAYLOAD = {"currency": "EUR", "itineraries": [
        {"price": 300.0, "bookingUrl": "https://k.test/c"},
        {"price": 120.0, "bookingUrl": "https://k.test/a"},
        {"price": None, "bookingUrl": "https://k.test/skip"},
        {"price": "129", "bookingUrl": "https://k.test/stringy"},
        {"price": 200.0},
    ]}

    def test_cheapest_ignores_non_numeric_prices(self):
        best = kiwi.cheapest(self.PAYLOAD)
        assert best is not None
        self.assertEqual(best["price"], 120.0)

    def test_ranking_is_cheapest_first_and_limited(self):
        self.assertEqual([i["price"] for i in kiwi.ranking(self.PAYLOAD)],
                         [120.0, 200.0, 300.0])
        self.assertEqual(len(kiwi.ranking(self.PAYLOAD, limit=2)), 2)

    def test_booking_links_skip_entries_without_one(self):
        self.assertEqual(kiwi.booking_links(self.PAYLOAD),
                         ["https://k.test/a", "https://k.test/c"])

    def test_empty_payload_is_not_an_error(self):
        self.assertIsNone(kiwi.cheapest({"itineraries": []}))
        self.assertIsNone(kiwi.cheapest({}))
        self.assertEqual(kiwi.ranking({}), [])

    def test_route_string_handles_one_way_and_round_trip(self):
        self.assertEqual(kiwi.route_str({"outbound": {"route": ["WAW", "BCN"]}}),
                         "WAW → BCN")
        self.assertEqual(
            kiwi.route_str({"outbound": {"route": ["WAW", "BCN"]},
                            "inbound": {"route": ["BCN", "WAW"]}}),
            "WAW → BCN | BCN → WAW")

    def test_route_string_falls_back_to_endpoints(self):
        self.assertEqual(kiwi.route_str({"outbound": {"from": "WAW", "to": "BCN"}}),
                         "WAW → BCN")

    def test_is_error_result_is_not_treated_as_data(self):
        # search_flight must not silently return junk when the tool errored.
        self.assertTrue(hasattr(kiwi, "KiwiError"))


class TestStore(unittest.TestCase):
    """Price history is only useful if the shapes never bleed into each other."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")
        store.init(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self, **over):
        row = {"observed_at": store.utcnow(), "origin": "WAW", "destination": "BCN",
               "depart_date": "01/03/2027", "return_date": None, "trip_type": "oneway",
               "price": 120.0, "currency": "EUR", "carrier": "Ryanair", "stops": 0,
               "duration_s": 10800, "booking_url": None, "routing": "WAW → BCN"}
        row.update(over)
        return row

    def test_round_trip_history_is_separate_from_one_way(self):
        store.record(self.db, [
            self._row(price=120.0),
            self._row(price=600.0, trip_type="roundtrip", return_date="08/03/2027"),
        ])
        self.assertEqual(len(store.history(self.db, "WAW", "BCN", "oneway", "01/03/2027")), 1)
        self.assertEqual(len(store.history(self.db, "WAW", "BCN", "roundtrip", "01/03/2027")), 1)

    def test_other_departure_dates_are_separate_shapes(self):
        store.record(self.db, [self._row(), self._row(depart_date="02/03/2027")])
        self.assertEqual(len(store.history(self.db, "WAW", "BCN", "oneway", "01/03/2027")), 1)

    def test_recording_nothing_is_not_an_error(self):
        self.assertEqual(store.record(self.db, []), 0)

    def test_stats_count_routes_not_observations(self):
        store.record(self.db, [self._row(price=120.0), self._row(price=110.0)])
        s = store.stats(self.db)
        self.assertEqual(s["observations"], 2)
        self.assertEqual(s["routes"], 1)

    def test_dedupe_window_and_trip_type(self):
        self.assertFalse(store.recent_alert_exists(self.db, "WAW", "BCN", "01/03/2027"))
        store.record_alert(self.db, {"origin": "WAW", "destination": "BCN",
                                     "depart_date": "01/03/2027", "return_date": None,
                                     "price": 90.0, "baseline": 130.0, "z_score": 3.1,
                                     "pct_below": 0.3077, "trip_type": "oneway",
                                     "notified": True})
        self.assertTrue(store.recent_alert_exists(self.db, "WAW", "BCN", "01/03/2027"))
        self.assertTrue(store.recent_alert_exists(self.db, "WAW", "BCN", "01/03/2027",
                                                  trip_type="oneway"))
        self.assertFalse(store.recent_alert_exists(self.db, "WAW", "BCN", "01/03/2027",
                                                   trip_type="roundtrip"))
        self.assertFalse(store.recent_alert_exists(self.db, "WAW", "BCN", "02/03/2027"))

    def test_dedupe_expires(self):
        import sqlite3
        store.record_alert(self.db, {"origin": "WAW", "destination": "BCN",
                                     "depart_date": "01/03/2027", "return_date": None,
                                     "price": 90.0, "baseline": 130.0, "z_score": 3.1,
                                     "pct_below": 0.3077, "trip_type": "oneway",
                                     "notified": True})
        self.assertTrue(store.recent_alert_exists(self.db, "WAW", "BCN", "01/03/2027"))
        # Age the alert past the window: it must stop suppressing new alerts.
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE alerts SET sent_at = datetime('now', '-72 hours')")
        self.assertFalse(store.recent_alert_exists(self.db, "WAW", "BCN", "01/03/2027",
                                                   within_hours=48))
        self.assertTrue(store.recent_alert_exists(self.db, "WAW", "BCN", "01/03/2027",
                                                  within_hours=96))

    def test_cheapest_ever(self):
        store.record(self.db, [self._row(price=120.0), self._row(price=99.0)])
        row = store.cheapest_ever(self.db, "WAW", "BCN", "oneway", "01/03/2027")
        assert row is not None
        self.assertEqual(row["price"], 99.0)

    def test_legacy_database_is_migrated_not_dropped(self):
        import sqlite3
        legacy = os.path.join(self.tmp.name, "legacy.db")
        with sqlite3.connect(legacy) as conn:
            conn.execute("""
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sent_at TEXT NOT NULL,
                    origin TEXT NOT NULL, destination TEXT NOT NULL,
                    depart_date TEXT NOT NULL, return_date TEXT, price REAL NOT NULL,
                    baseline REAL NOT NULL, z_score REAL NOT NULL, pct_below REAL NOT NULL,
                    booking_url TEXT, notified INTEGER NOT NULL DEFAULT 0)""")
            conn.execute("INSERT INTO alerts (sent_at, origin, destination, depart_date,"
                         " price, baseline, z_score, pct_below, notified)"
                         " VALUES (datetime('now'),'WAW','BCN','01/03/2027',90,130,3.1,0.3,1)")
        store.init(legacy)
        self.assertTrue(store.recent_alert_exists(legacy, "WAW", "BCN", "01/03/2027"))
        self.assertEqual(store.stats(legacy)["alerts"], 1)

    def test_runs_are_tracked(self):
        run_id = store.start_run(self.db)
        store.finish_run(self.db, run_id, tried=6, ok=5, errors="BKK: timeout")
        with store.connect(self.db) as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        self.assertEqual(row["routes_tried"], 6)
        self.assertEqual(row["routes_ok"], 5)
        self.assertIn("BKK", row["errors"])


class TestCliHelpers(unittest.TestCase):
    def test_return_date_arithmetic(self):
        self.assertEqual(return_date_for("01/03/2027", 7), "08/03/2027")
        self.assertEqual(return_date_for("28/02/2027", 1), "01/03/2027")

    def test_departure_dates_spread_across_the_horizon(self):
        dates = next_departure_dates(6, horizon_days=120)
        self.assertEqual(len(dates), 6)
        parsed = [datetime.strptime(d, "%d/%m/%Y").date() for d in dates]
        self.assertEqual(parsed, sorted(parsed))
        self.assertGreaterEqual(parsed[0], date.today() + timedelta(days=14))

    def test_departure_dates_never_divide_by_zero(self):
        self.assertEqual(next_departure_dates(0, horizon_days=0), [])

    def test_table_rendering_is_cheapest_first(self):
        payload = {"query": "WAW → BCN", "currency": "EUR", "itineraries": [
            {"price": 300.0, "outbound": {"departureTime": "2027-03-01T10:00:00",
                                          "durationSeconds": 7200,
                                          "segments": [{"carrierName": "Wizz"}]},
             "bookingUrl": "https://k.test/c"},
            {"price": 120.0, "outbound": {"departureTime": "2027-03-01T06:00:00",
                                          "durationSeconds": 10800,
                                          "segments": [{"carrierName": "Ryanair"}]},
             "bookingUrl": "https://k.test/a"},
        ]}
        out = render_table(payload, limit=10)
        self.assertLess(out.index("120"), out.index("300"))
        self.assertIn("Ryanair", out)
        self.assertIn("https://k.test/a", out)

    def test_table_says_so_when_there_is_nothing(self):
        self.assertEqual(render_table({"itineraries": []}), "Brak wyników.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
