import copy
from datetime import datetime, timedelta, timezone
import unittest

from scheduler.policies import hosted_capacity, decide

NOW = datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)
TASK = {"kind": "strategy", "repo": "chaonpu/Stock",
        "estimatedMinutes": 180, "reserveMinutes": 200,
        "cloudWorkflow": "run-us.yml", "windowsWorkflow": "run-us.yml",
        "runnerName": "ziwuxilv-stock-win"}


def evidence(**changes):
    return dict(status="available", independentlyVerified=True,
                remainingFreeMinutes=380, period="2026-10",
                observedAt=NOW.isoformat(), **changes)


class HostedCapacityTests(unittest.TestCase):
    def test_exact_task_plus_reserve_boundary(self):
        for minutes, expected in [(379.99, "insufficient"), (380, "available"),
                                  (381, "available"), (0, "insufficient")]:
            data = evidence()
            data["remainingFreeMinutes"] = minutes
            self.assertEqual(hosted_capacity(data, TASK, now=NOW), expected)

    def test_usage_discounts_or_startup_do_not_prove_balance(self):
        for data in ({"status": "available", "independentlyVerified": True},
                     {"grossQuantity": 2000, "discountQuantity": 2000},
                     {"hostedRunnerStarted": True},
                     dict(evidence(), independentlyVerified=False)):
            self.assertEqual(hosted_capacity(data, TASK, now=NOW), "unknown")

    def test_freshness_month_boundary_and_timezone(self):
        for changes in ({"observedAt": (NOW - timedelta(seconds=301)).isoformat()},
                        {"observedAt": (NOW + timedelta(seconds=1)).isoformat()},
                        {"observedAt": "2026-10-17T01:00:00"},
                        {"observedAt": "invalid"}, {"period": "2026-09"}):
            self.assertEqual(hosted_capacity(dict(evidence(), **changes), TASK, now=NOW), "unknown")
        self.assertEqual(hosted_capacity(evidence(), TASK, now=NOW.replace(tzinfo=None)), "unknown")
        data = dict(evidence(), observedAt="2026-10-17T09:00:00+08:00")
        self.assertEqual(hosted_capacity(data, TASK, now=NOW), "available")
        next_month = datetime(2026, 11, 1, tzinfo=timezone.utc)
        data = dict(evidence(), observedAt=(next_month - timedelta(seconds=1)).isoformat())
        self.assertEqual(hosted_capacity(data, TASK, now=next_month), "unknown")

    def test_non_numeric_or_nonfinite_values_block(self):
        for field in ("remainingFreeMinutes", "estimatedMinutes", "reserveMinutes"):
            for invalid in (True, "380", None, -1, float("inf"), float("nan")):
                data, task = evidence(), copy.deepcopy(TASK)
                (data if field == "remainingFreeMinutes" else task)[field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    self.assertEqual(hosted_capacity(data, task, now=NOW), "unknown")

    def test_insufficient_cloud_can_fall_back_to_verified_windows(self):
        current = datetime.now(timezone.utc)
        data = dict(evidence(), remainingFreeMinutes=1,
                    observedAt=current.isoformat(), period=current.strftime("%Y-%m"))
        runners = {TASK["repo"]: {"name": TASK["runnerName"], "status": "online",
                                  "busy": False, "labelsVerified": True}}
        self.assertEqual(decide("stock_us", TASK, {"chaonpu": data}, runners).route, "windows")
        self.assertEqual(decide("stock_us", TASK, {"chaonpu": data}, {}).route, "deferred")
        self.assertEqual(decide("stock_us", TASK, {"chaonpu": data}, runners, active=True).route, "hold")

    def test_exhausted_evidence_also_expires_and_must_have_zero_balance(self):
        data = dict(evidence(), status="exhausted", remainingFreeMinutes=0)
        self.assertEqual(hosted_capacity(data, TASK, now=NOW), "exhausted")
        self.assertEqual(hosted_capacity(data, TASK, now=NOW + timedelta(seconds=301)), "unknown")
        self.assertEqual(hosted_capacity(dict(data, remainingFreeMinutes=1), TASK, now=NOW), "unknown")


if __name__ == "__main__":
    unittest.main()
