"""Synthetic Actions-only evidence; no private source or logs."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from scheduler.production_evidence import inspect, STEP

HEAD, RID = "a" * 40, 90000000333
NOW = datetime(2026, 10, 17, 6, 0, 10, tzinfo=timezone.utc)


def fixtures():
    run = {"id": RID, "repository": {"full_name": "chaonpu/Stock"},
        "path": ".github/workflows/production-readiness-us.yml", "event": "workflow_dispatch",
        "head_branch": "main", "head_sha": HEAD, "run_attempt": 1,
        "actor": {"login": "chaonpu"}, "status": "completed", "conclusion": "success",
        "run_started_at": "2026-10-17T05:59:00Z", "updated_at": "2026-10-17T06:00:02Z"}
    jobs = {"total_count": 1, "jobs": [{"run_id": RID, "run_attempt": 1,
        "name": "readiness-v1:stock_us:2026-10-16", "status": "completed", "conclusion": "success",
        "completed_at": "2026-10-17T06:00:01Z", "steps": [{"name": STEP,
            "status": "completed", "conclusion": "success", "completed_at": "2026-10-17T06:00:00Z"}]}]}
    return {"total_count": 1, "workflow_runs": [run]}, jobs


class Reader:
    def __init__(self, listing=None, jobs=None):
        self.listing, self.jobs = fixtures() if listing is None else (listing, jobs)
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        if path == "/repos/chaonpu/Stock/actions/workflows/production-readiness-us.yml/runs?event=workflow_dispatch&branch=main&per_page=1":
            return deepcopy(self.listing)
        if path == f"/repos/chaonpu/Stock/actions/runs/{RID}/jobs?filter=latest&per_page=100":
            return deepcopy(self.jobs)
        raise AssertionError("unexpected non-Actions path")


class EvidenceTests(unittest.TestCase):
    def inspect(self, reader):
        return inspect(reader, "stock_us", "2026-10-16", NOW)

    def test_exact_fresh_run_dated_job_and_final_step_match(self):
        reader = Reader()
        result = self.inspect(reader)
        self.assertTrue(result["marketDataReady"])
        self.assertTrue(result["exchangeSessionVerified"])
        self.assertTrue(result["selectionInputsVerified"])
        self.assertEqual(result["sourceSha"], HEAD)
        self.assertFalse(result["productionReady"])
        self.assertEqual(len(reader.paths), 2)

    def test_no_run_does_not_use_unrelated_green_evidence(self):
        result = self.inspect(Reader({"total_count":0,"workflow_runs":[]}, {}))
        self.assertEqual(result["evidenceStatus"], "hold_private_readiness_evidence_missing")

    def test_failed_latest_run_never_falls_back_or_reads_jobs(self):
        for mode in ("failed", "duplicate"):
            listing, jobs = fixtures()
            if mode == "failed": listing["workflow_runs"][0]["conclusion"] = "failure"
            else: listing["workflow_runs"] *= 2
            reader = Reader(listing, jobs)
            self.assertFalse(self.inspect(reader)["marketDataReady"])
            self.assertEqual(len(reader.paths), 1)

    def test_exact_date_job_step_identity_and_complete_job_page_required(self):
        for mode in ("wrong_day", "missing_step", "duplicate_job", "partial_page", "wrong_run", "wrong_sha"):
            listing, jobs = fixtures()
            if mode == "wrong_day": jobs["jobs"][0]["name"] = "readiness-v1:stock_us:2026-10-15"
            elif mode == "missing_step": jobs["jobs"][0]["steps"] = []
            elif mode == "duplicate_job":
                jobs["jobs"] *= 2
                jobs["total_count"] = 2
            elif mode == "partial_page": jobs["total_count"] = 2
            elif mode == "wrong_run": jobs["jobs"][0]["run_id"] = RID + 1
            else: jobs["jobs"][0]["head_sha"] = "b" * 40
            self.assertFalse(self.inspect(Reader(listing, jobs))["marketDataReady"])

    def test_recent_run_finish_cannot_refresh_old_future_or_naive_attestation(self):
        for value in ("2026-10-17T05:55:09Z", "2026-10-17T06:01:00Z", "2026-10-17T06:00:00"):
            listing, jobs = fixtures()
            jobs["jobs"][0]["steps"][0]["completed_at"] = value
            self.assertFalse(self.inspect(Reader(listing, jobs))["marketDataReady"])

    def test_old_validation_push_rerun_wrong_actor_or_invalid_sha_rejected(self):
        for field, value in (("path", ".github/workflows/windows-isolated-strategy-validation.yml"),
                             ("event", "push"), ("run_attempt", 2), ("run_attempt", True),
                             ("actor", {"login": "other"}), ("head_sha", "invalid"),
                             ("conclusion", "failure"), ("status", "in_progress"),
                             ("head_branch", "other")):
            listing, jobs = fixtures()
            listing["workflow_runs"][0][field] = value
            self.assertFalse(self.inspect(Reader(listing, jobs))["marketDataReady"])

    def test_attestation_must_be_within_original_run_and_job_time(self):
        for changes in ({"run_started_at": "2026-10-17T06:00:01Z"},
                        {"updated_at": "2026-10-17T05:59:59Z"},
                        {"updated_at": "2026-10-17T06:00:11Z"}):
            listing, jobs = fixtures()
            listing["workflow_runs"][0].update(changes)
            self.assertFalse(self.inspect(Reader(listing, jobs))["marketDataReady"])

    def test_known_dispatch_date_conflict_rejected(self):
        listing, jobs = fixtures()
        listing["workflow_runs"][0]["inputs"] = {"business_date": "2026-10-15"}
        self.assertFalse(self.inspect(Reader(listing, jobs))["marketDataReady"])

    def test_api_failure_is_hold_without_private_error_text(self):
        class Broken:
            def get(self, path): raise OSError("private source or token must not escape")
        result = self.inspect(Broken())
        self.assertEqual(result["evidenceStatus"], "hold_evidence_unavailable")
        self.assertNotIn("token", str(result))


if __name__ == "__main__":
    unittest.main()
