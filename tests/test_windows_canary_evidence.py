import copy
from datetime import datetime, timezone, timedelta
import unittest

from scheduler.windows_canary_evidence import check, CanaryGuard

NOW = datetime(2026, 10, 17, 0, 40, tzinfo=timezone.utc)


def fixtures():
    row = {"id": "pilot:stock:2026-10-17", "repo": "chaonpu/Stock",
           "workflow": "runner-dispatch-canary.yml",
           "coordinatorRunId": 38961235915, "runId": 38961249562,
           "canary": "runner-canary-38961235915-stock"}
    run = {"id": 38961249562, "repository": {"full_name": "chaonpu/Stock"},
           "path": ".github/workflows/runner-dispatch-canary.yml",
           "event": "workflow_dispatch", "head_branch": "main",
           "run_attempt": 1, "status": "completed", "conclusion": "success"}
    jobs = {"jobs": [{"id": 117176577729, "run_id": 38961249562,
                      "runner_name": "ziwuxilv-stock-win", "runner_id": 21,
                      "labels": ["self-hosted", "Windows", "X64"],
                      "status": "completed", "conclusion": "success",
                      "completed_at": (NOW - timedelta(seconds=15)).isoformat(),
                      "steps": [{"name": "Verify dispatch identity and Windows runner only",
                                 "conclusion": "success"}]}]}
    return row, run, jobs


class WindowsCanaryEvidenceTests(unittest.TestCase):
    def test_recent_exact_immutable_canary_is_not_business_or_current_status(self):
        row, run, jobs = fixtures()
        result = check(row, run, jobs, observed_at=NOW)
        self.assertTrue(result["runnerRecentlyExecuted"])
        self.assertFalse(result["runnerCurrentlyOnlineIndependentlyVerified"])
        self.assertFalse(result["productionDispatchAuthorized"])
        self.assertFalse(result["automatedBusinessRetryAuthorized"])

    def test_runner_identity_and_labels_required(self):
        for field, val in (("runner_name", "attacker"),
                           ("runner_id", 0),
                           ("labels", ["self-hosted", "Windows"]),
                           ("conclusion", "failure")):
            with self.subTest(field=field):
                row, run, jobs = fixtures()
                jobs["jobs"][0][field] = val
                with self.assertRaises(CanaryGuard):
                    check(row, run, jobs, observed_at=NOW)

    def test_original_registered_stock_run_must_match(self):
        for place, field, value in (
            ("row", "runId", 38961249563),
            ("row", "coordinatorRunId", 38961235916),
            ("run", "head_branch", "other"),
            ("run", "path", ".github/workflows/run-a-share.yml"),
            ("run", "run_attempt", 2),
            ("run", "event", "push"),
        ):
            with self.subTest(place=place, field=field):
                row, run, jobs = fixtures()
                (row if place == "row" else run)[field] = value
                with self.assertRaises(CanaryGuard):
                    check(row, run, jobs, observed_at=NOW)

    def test_old_historical_success_is_not_online_evidence(self):
        row, run, jobs = fixtures()
        jobs["jobs"][0]["completed_at"] = (NOW - timedelta(minutes=20)).isoformat()
        with self.assertRaisesRegex(CanaryGuard, "stale"):
            check(row, run, jobs, observed_at=NOW)
        jobs["jobs"][0]["completed_at"] = (NOW + timedelta(seconds=5)).isoformat()
        with self.assertRaises(CanaryGuard):
            check(row, run, jobs, observed_at=NOW)

    def test_rejected_if_non_unique_or_missing_verification_step(self):
        row, run, jobs = fixtures()
        jobs["jobs"].append(copy.deepcopy(jobs["jobs"][0]))
        with self.assertRaises(CanaryGuard):
            check(row, run, jobs, observed_at=NOW)
        jobs["jobs"].pop()
        jobs["jobs"][0]["steps"] = []
        with self.assertRaises(CanaryGuard):
            check(row, run, jobs, observed_at=NOW)


if __name__ == "__main__":
    unittest.main()
