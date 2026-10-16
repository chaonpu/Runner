"""Published 2026 SSE/SZSE/NYSE/Nasdaq schedule audit regression tests."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scheduler.exchange_schedule import (
    CalendarGuard, SCHEDULE, evaluate, load_schedule
)

def utc(y, m, d, hh, minute=0):
    return datetime(y, m, d, hh, minute, tzinfo=timezone.utc)

class OfficialExchangeScheduleTests(unittest.TestCase):
    def test_validated_2026_official_sources_present(self):
        data = load_schedule()
        self.assertEqual(data["coverageYear"], 2026)
        self.assertEqual(len(data["markets"]["stock_a"]["sources"]), 2)
        self.assertEqual(len(data["markets"]["stock_us"]["sources"]), 2)
        self.assertIn("NYSE", data["markets"]["stock_us"]["exchanges"])
        self.assertIn("SZSE", data["markets"]["stock_a"]["exchanges"])

    def test_sse_szse_national_weeklong_closure(self):
        # 2026-10-07 16:00 China time is during National Day closure.
        day = evaluate("stock_a", "", now=utc(2026, 10, 7, 8))
        self.assertEqual(day["businessDate"], "2026-09-30")
        self.assertTrue(day["publishedScheduleMatched"])
        self.assertTrue(day["scheduledSessionClosed"])
        self.assertFalse(day["marketDataReady"])
        self.assertFalse(day["businessDateAuthorized"])

    def test_a_share_reopening_boundary(self):
        before = evaluate("stock_a", "", now=utc(2026, 10, 8, 6, 59))
        after = evaluate("stock_a", "", now=utc(2026, 10, 8, 7))
        self.assertEqual(before["businessDate"], "2026-09-30")
        self.assertEqual(after["businessDate"], "2026-10-08")
        self.assertTrue(after["scheduledSessionClosed"])

    def test_a_share_spring_festival_not_a_business_weekday(self):
        china_closed = evaluate("stock_a", "", now=utc(2026, 2, 23, 8))
        self.assertEqual(china_closed["businessDate"], "2026-02-13")
        return_day = evaluate("stock_a", "", now=utc(2026, 2, 24, 7))
        self.assertEqual(return_day["businessDate"], "2026-02-24")

    def test_us_thanksgiving_early_close(self):
        # 2026-11-27 12:59 ET = 17:59 UTC.
        prior = evaluate("stock_us", "", now=utc(2026, 11, 27, 17, 59))
        at = evaluate("stock_us", "", now=utc(2026, 11, 27, 18))
        self.assertEqual(prior["businessDate"], "2026-11-25")
        self.assertEqual(at["businessDate"], "2026-11-27")
        self.assertIn("13:00:00", at["scheduledClose"])
        self.assertTrue(at["publishedScheduleMatched"])

    def test_us_christmas_eve_early_close(self):
        before = evaluate("stock_us", "", now=utc(2026, 12, 24, 17, 59))
        at = evaluate("stock_us", "", now=utc(2026, 12, 24, 18))
        self.assertEqual(before["businessDate"], "2026-12-23")
        self.assertEqual(at["businessDate"], "2026-12-24")
        christmas = evaluate("stock_us", "", now=utc(2026, 12, 25, 22))
        self.assertEqual(christmas["businessDate"], "2026-12-24")

    def test_us_observed_independence_day(self):
        fourth = evaluate("stock_us", "", now=utc(2026, 7, 3, 22))
        self.assertEqual(fourth["businessDate"], "2026-07-02")
        self.assertTrue(fourth["scheduledSessionClosed"])

    def test_us_standard_and_daylight_time(self):
        est = evaluate("stock_us", "", now=utc(2026, 1, 5, 21))
        edt = evaluate("stock_us", "", now=utc(2026, 10, 19, 20))
        self.assertEqual(est["businessDate"], "2026-01-05")
        self.assertEqual(edt["businessDate"], "2026-10-19")
        self.assertIn("-05:00", est["scheduledClose"])
        self.assertIn("-04:00", edt["scheduledClose"])

    def test_explicit_closed_holiday_cannot_be_marked_session(self):
        for name, market in (
            ("2026-10-01", "stock_a"),
            ("2026-02-23", "stock_a"),
            ("2026-11-26", "stock_us"),
            ("2026-12-25", "stock_us"),
            ("2026-10-17", "stock_a"),
        ):
            with self.subTest(market=market, day=name):
                found = evaluate(market, name, now=utc(2026, 10, 18, 20))
                self.assertEqual(found["businessDate"], name)
                self.assertEqual(found["calendarStatus"], "exchange_scheduled_closed")
                self.assertFalse(found["publishedScheduleMatched"])
                self.assertFalse(found["scheduledSessionClosed"])
                self.assertFalse(found["businessDateAuthorized"])

    def test_manual_future_date_is_not_completed(self):
        result = evaluate("stock_us", "2026-12-24", now=utc(2026, 10, 19, 23))
        self.assertTrue(result["publishedScheduleMatched"])
        self.assertFalse(result["scheduledSessionClosed"])

    def test_outside_2026_cannot_silently_use_stale_calendar(self):
        for task in ("stock_a", "stock_us"):
            with self.subTest(task=task):
                found = evaluate(task, "", now=utc(2027, 1, 5, 22))
                self.assertIsNone(found["businessDate"])
                self.assertEqual(found["calendarStatus"], "outside_published_year")
                self.assertFalse(found["calendarCoverageCurrent"])
                self.assertFalse(found["businessDateAuthorized"])

    def test_first_day_before_known_session_blocks(self):
        result = evaluate("stock_us", "", now=utc(2026, 1, 1, 19))
        self.assertIsNone(result["businessDate"])
        self.assertEqual(result["calendarStatus"], "no_complete_session_in_coverage")

    def test_malformed_schedule_and_untrusted_clock_fail_closed(self):
        original = load_schedule()
        for mutation in ("year", "market", "duplicate", "bad-close", "bad-source"):
            with self.subTest(mutation=mutation):
                data = deepcopy(original)
                if mutation == "year":
                    data["coverageYear"] = 2027
                elif mutation == "market":
                    data["markets"]["stock_us"]["timezone"] = "UTC"
                elif mutation == "duplicate":
                    data["markets"]["stock_a"]["closedWeekdays"].append("2026-01-01")
                elif mutation == "bad-close":
                    data["markets"]["stock_us"]["earlyCloses"]["2026-12-24"] = "28:61"
                else:
                    data["markets"]["stock_us"]["sources"][0] = "http://example.invalid"
                with self.assertRaises(CalendarGuard):
                    evaluate("stock_us", "", now=utc(2026, 10, 19, 23), schedule=data)
        with self.assertRaises(CalendarGuard):
            evaluate("stock_a", "", now=datetime(2026, 10, 19))
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "not-installed.json"
            with self.assertRaises(CalendarGuard):
                load_schedule(target)

    def test_not_a_liveness_or_data_readiness_approval(self):
        for task in ("stock_a", "stock_us"):
            result = evaluate(task, "", now=utc(2026, 10, 19, 23))
            self.assertFalse(result["marketDataReady"])
            self.assertFalse(result["businessDateAuthorized"])

if __name__ == "__main__":
    unittest.main()
