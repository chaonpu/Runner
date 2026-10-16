import datetime
import json
import unittest
from pathlib import Path
from scheduler.monitor import verify_registry, classify_run, reconcile

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/repositories.json").read_text())

class MonitorTests(unittest.TestCase):
    def test_empty_registry_does_not_call_api_or_dispatch(self):
        state = {"schema": 1, "tasks": []}
        result = reconcile(CONFIG, state, {})
        self.assertEqual(result["registryCount"], 0)
        self.assertEqual(result["dispatched"], 0)
        self.assertEqual(result["retried"], 0)

    def item(self):
        return {"id": "stock_us:2026-10-16", "task": "stock_us",
                "repo": "chaonpu/Stock", "workflow": "run-us.yml",
                "runId": 123, "status": "dispatched", "effectVerified": False}

    def mockrun(self, **kwargs):
        return dict({"id": 123, "path": ".github/workflows/run-us.yml",
                     "status": "completed", "conclusion": "success"}, **kwargs)

    def test_green_workflow_is_not_verified_ledger(self):
        result = classify_run(self.item(), self.mockrun(), datetime.datetime.now(datetime.timezone.utc))
        self.assertEqual(result["state"], "awaiting_effect_verification")

    def test_verified_requires_explicit_effect_confirmation(self):
        item = self.item()
        item["effectVerified"] = True
        result = classify_run(item, self.mockrun(), datetime.datetime.now(datetime.timezone.utc))
        self.assertEqual(result["state"], "verified")

    def test_queued_over_10_minutes_requires_review(self):
        item = self.item()
        now = datetime.datetime(2026, 10, 17, 1, 0, tzinfo=datetime.timezone.utc)
        r = {"id": 123, "path": ".github/workflows/run-us.yml",
             "status": "queued", "conclusion": None,
             "created_at": "2026-10-17T00:49:00Z"}
        self.assertEqual(classify_run(item, r, now)["state"], "hold")

    def test_wrong_run_or_workflow_blocks(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        a = {"id": 999, "path": ".github/workflows/run-us.yml", "status": "completed"}
        b = {"id": 123, "path": ".github/workflows/not-run-us.yml", "status": "completed"}
        self.assertEqual(classify_run(self.item(), a, now)["reason"], "run_id_mismatch")
        self.assertEqual(classify_run(self.item(), b, now)["reason"], "workflow_mismatch")

    def test_registry_does_not_accept_private_content_fields(self):
        item = self.item()
        item["holdings"] = ["private"]
        with self.assertRaises(ValueError):
            verify_registry({"schema": 1, "tasks": [item]}, CONFIG)

    def test_missing_actions_credential_is_hold(self):
        record = reconcile(CONFIG, {"schema": 1, "tasks": [self.item()]}, {})
        self.assertEqual(record["results"][0]["reason"], "actions_token_missing")
        self.assertEqual(record["dispatched"], 0)

if __name__ == "__main__":
    unittest.main()
