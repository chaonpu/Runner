from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from scheduler.probes import account_usage

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
SPEC = {"kind": "user", "billingSecret": "BILLING"}


class BillingPeriodTests(unittest.TestCase):
    def call(self, data, **kwargs):
        with patch("scheduler.probes.GitHubReadOnly.get", return_value=data) as get:
            result = account_usage("chaonpu", SPEC, {"BILLING": "synthetic"}, **kwargs)
        return result, get

    def test_request_and_response_must_match_current_utc_month(self):
        result, get = self.call({"timePeriod": {"year": 2026, "month": 10},
                                 "usageItems": [{"product": "Actions", "unitType": "minutes",
                                                 "sku": "actions_linux", "grossQuantity": 2000}]}, now=NOW)
        self.assertEqual(get.call_args.args[0],
                         "/users/chaonpu/settings/billing/usage/summary?product=Actions&year=2026&month=10")
        self.assertEqual(result["minuteUsageBySku"], {"actions_linux": 2000})
        self.assertTrue(result["reportingPeriodVerified"])
        self.assertFalse(result["independentlyVerified"])
        self.assertEqual(result["status"], "unknown")

    def test_year_only_or_wrong_month_is_not_trusted(self):
        for period in (None, {}, {"year": 2026}, {"year": 2026, "month": 9},
                       {"year": 2025, "month": 10}, {"year": "2026", "month": 10}):
            result, _ = self.call({"timePeriod": period, "usageItems": []}, now=NOW)
            self.assertEqual(result["read"], "invalid_response")
            self.assertEqual(result["status"], "unknown")
            self.assertNotIn("minuteUsageBySku", result)

    def test_timezone_conversion_and_no_ambiguous_clock(self):
        local = datetime.fromisoformat("2026-11-01T00:00:00+08:00")
        _, get = self.call({"timePeriod": {"year": 2026, "month": 10}, "usageItems": []}, now=local)
        self.assertIn("month=10", get.call_args.args[0])
        result, get = self.call({}, now=NOW.replace(tzinfo=None))
        self.assertEqual(result["read"], "invalid_response")
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
