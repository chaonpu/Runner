"""Synthetic readiness metadata only; no credentials, private files or calls."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from scheduler.production_evidence import inspect

HEAD, RID = "a" * 40, 90000000333
NOW = datetime(2026, 10, 17, 6, 0, 10, tzinfo=timezone.utc)


def fixtures():
    status = {"sha": HEAD, "total_count": 1, "statuses": [{
        "context": "runner/production-readiness/stock_us", "state": "success",
        "description": "v1:stock_us:2026-10-16:" + str(RID) + ":ready",
        "target_url": f"https://github.com/chaonpu/Stock/actions/runs/{RID}",
        "created_at": "2026-10-17T06:00:00Z", "updated_at": "2026-10-17T06:00:00Z"}]}
    run = {"id": RID, "repository": {"full_name": "chaonpu/Stock"},
        "path": ".github/workflows/production-readiness.yml", "event": "workflow_dispatch",
        "head_branch": "main", "head_sha": HEAD, "run_attempt": 1,
        "actor": {"login": "chaonpu"}, "status": "completed", "conclusion": "success",
        "run_started_at": "2026-10-17T05:59:00Z", "updated_at": "2026-10-17T06:00:01Z"}
    return status, run


class Reader:
    def __init__(self, status=None, run=None):
        self.status, self.run = fixtures() if status is None else (status, run)
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        if path == f"/repos/chaonpu/Stock/commits/{HEAD}/status?per_page=100":
            return deepcopy(self.status)
        if path == f"/repos/chaonpu/Stock/actions/runs/{RID}":
            return deepcopy(self.run)
        raise AssertionError("unexpected path")


class EvidenceTests(unittest.TestCase):
    def inspect(self, reader):
        return inspect(reader, "stock_us", "2026-10-16", HEAD, NOW)

    def test_exact_fresh_status_binds_to_original_successful_readiness_run(self):
        reader = Reader()
        result = self.inspect(reader)
        self.assertTrue(result["marketDataReady"])
        self.assertTrue(result["exchangeSessionVerified"])
        self.assertTrue(result["selectionInputsVerified"])
        self.assertFalse(result["productionReady"])
        self.assertEqual(len(reader.paths), 2)

    def test_absent_context_does_not_use_an_unrelated_green_status(self):
        status, run = fixtures()
        status["statuses"][0]["context"] = "unrelated"
        result = self.inspect(Reader(status, run))
        self.assertEqual(result["evidenceStatus"], "hold_private_readiness_evidence_missing")
        self.assertFalse(result["marketDataReady"])

    def test_failed_or_ambiguous_latest_claim_never_falls_back(self):
        for mode in ("failed", "duplicate"):
            status, run = fixtures()
            if mode == "failed": status["statuses"][0]["state"] = "failure"
            else:
                status["statuses"] *= 2
                status["total_count"] = 2
            reader = Reader(status, run)
            self.assertFalse(self.inspect(reader)["marketDataReady"])
            self.assertEqual(len(reader.paths), 1)

    def test_exact_date_schema_target_and_source_required(self):
        for field, value in (("description", "v1:stock_us:2026-10-15:90000000333:ready"),
                             ("description", "old diagnostic passed"),
                             ("target_url", "https://untrusted.example/run")):
            status, run = fixtures()
            status["statuses"][0][field] = value
            self.assertFalse(self.inspect(Reader(status, run))["marketDataReady"])
        status, run = fixtures()
        status["sha"] = "b" * 40
        self.assertFalse(self.inspect(Reader(status, run))["marketDataReady"])

    def test_updated_timestamp_never_refreshes_stale_or_future_created_measurement(self):
        for timestamp in ("2026-10-17T05:55:09Z", "2026-10-17T06:01:00Z", "2026-10-17T06:00:00"):
            status, run = fixtures()
            status["statuses"][0].update(created_at=timestamp, updated_at="2026-10-17T06:00:09Z")
            self.assertFalse(self.inspect(Reader(status, run))["marketDataReady"])

    def test_historical_validation_push_event_rerun_wrong_actor_or_commit_rejected(self):
        for field, value in (("path", ".github/workflows/windows-isolated-strategy-validation.yml"),
                             ("event", "push"), ("run_attempt", 2), ("run_attempt", True),
                             ("actor", {"login": "other"}), ("head_sha", "b" * 40),
                             ("conclusion", "failure"), ("status", "in_progress"),
                             ("head_branch", "another"), ("id", RID + 1)):
            status, run = fixtures()
            run[field] = value
            self.assertFalse(self.inspect(Reader(status, run))["marketDataReady"])

    def test_status_must_be_created_during_the_named_run(self):
        for changes in ({"run_started_at": "2026-10-17T06:00:01Z"},
                        {"updated_at": "2026-10-17T05:59:59Z"},
                        {"updated_at": "2026-10-17T06:00:11Z"}):
            status, run = fixtures()
            run.update(changes)
            self.assertFalse(self.inspect(Reader(status, run))["marketDataReady"])

    def test_known_inputs_conflict_is_rejected_even_with_matching_metadata(self):
        status, run = fixtures()
        run["inputs"] = {"task": "stock_us", "business_date": "2026-10-15"}
        self.assertFalse(self.inspect(Reader(status, run))["marketDataReady"])

    def test_api_failure_is_hold_and_no_raw_error_is_returned(self):
        class Broken:
            def get(self, path): raise OSError("private source or token must not escape")
        result = self.inspect(Broken())
        self.assertEqual(result["evidenceStatus"], "hold_evidence_unavailable")
        self.assertNotIn("token", str(result))


if __name__ == "__main__":
    unittest.main()
