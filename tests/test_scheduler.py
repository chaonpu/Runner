import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from scheduler.policies import billing_state, runner_available, decide, request_id
from scheduler.probes import account_usage, runner_for_repo, safe_error, GitHubReadOnly
from scheduler.preflight import preflight
from scheduler.production_registry import reserve, RegistryGuard

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "config/repositories.json").read_text())

def quota(status):
    now = datetime.now(timezone.utc)
    return {"status": status, "independentlyVerified": True,
            "remainingFreeMinutes": 0 if status == "exhausted" else 10000,
            "period": now.strftime("%Y-%m"),
            "observedAt": now.isoformat()}

def online(name):
    return {"name": name, "status": "online", "busy": False, "labelsVerified": True}

class RouteTests(unittest.TestCase):
    def test_usage_never_claims_allowance(self):
        self.assertEqual(billing_state({"status": "available", "independentlyVerified": False}), "unknown")
        self.assertEqual(billing_state({"grossQuantity": 2000, "discountQuantity": 2000}), "unknown")

    def test_runner_busy_or_unknown_blocks(self):
        self.assertFalse(runner_available(dict(online("a"), busy=True), "a"))
        self.assertFalse(runner_available(dict(online("a"), status="offline"), "a"))
        self.assertFalse(runner_available(dict(online("a"), labelsVerified=False), "a"))
        self.assertTrue(runner_available(online("a"), "a"))

    def test_stock_cloud_with_verifiable_remaining_allowance(self):
        task = CONFIG["tasks"]["stock_us"]
        d = decide("stock_us", task, {"chaonpu": quota("available")}, {})
        self.assertEqual((d.route, d.workflow), ("cloud", "run-us.yml"))

    def test_stock_no_windows_implementation_does_not_use_windows(self):
        task = CONFIG["tasks"]["stock_us"]
        d = decide("stock_us", task, {"chaonpu": quota("exhausted")},
                   {task["repo"]: online("ziwuxilv-stock-win")})
        self.assertEqual(d.route, "deferred")

    def test_failed_or_queued_legacy_task_blocks_dispatch(self):
        task = CONFIG["tasks"]["stock_a"]
        self.assertEqual(decide("stock_a", task, {}, {}, active=True).route, "hold")
        self.assertEqual(decide("stock_a", task, {}, {}, already_verified=True).route, "skip")

    def test_android_prefers_test_cloud(self):
        task = CONFIG["tasks"]["android"]
        d = decide("android", task, {"anwpustudent-lab": quota("available")}, {})
        self.assertEqual((d.repository, d.route), ("anwpustudent-lab/Test", "cloud"))

    def test_android_signer_cloud_requires_verified_source(self):
        task = CONFIG["tasks"]["android"]
        quotas = {"anwpustudent-lab": quota("exhausted"), "chaonpu": quota("available")}
        self.assertEqual(decide("android", task, quotas, {}).route, "deferred")
        d = decide("android", task, quotas, {}, signing_source_verified=True)
        self.assertEqual((d.route, d.workflow), ("cloud-signing", "android.yml"))

    def test_android_windows_requires_both_runner_services(self):
        task = CONFIG["tasks"]["android"]
        runners = {task["repo"]: online("ziwuxilv-test-win"),
                   task["signingRepo"]: online("ziwuxilv-sign-win")}
        self.assertEqual(decide("android", task, {}, runners).route, "windows-chain")
        runners[task["signingRepo"]]["busy"] = True
        self.assertEqual(decide("android", task, {}, runners).route, "deferred")

    def test_database_and_public_release_are_not_dispatchable_tasks(self):
        self.assertNotIn("ZIWUXILV", CONFIG["accounts"])
        self.assertNotIn("database_sync", CONFIG["tasks"])
        self.assertNotIn("apk_publish", CONFIG["tasks"])
        self.assertTrue(all(task["repo"] != "chaonpu/ziwuxilv"
                            for task in CONFIG["tasks"].values()))

    def test_task_id_generation(self):
        self.assertEqual(request_id("stock_us", CONFIG["tasks"]["stock_us"],
                                    business_date="2026-10-16"), "stock_us:2026-10-16")
        with self.assertRaises(ValueError):
            request_id("android", CONFIG["tasks"]["android"], source_sha="latest")
        self.assertEqual(request_id("android", CONFIG["tasks"]["android"], source_sha="a" * 40),
                         "android:" + "a" * 40)

    def test_no_http_post_in_public_probe(self):
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_): return False
        with patch("scheduler.probes.urllib.request.urlopen", return_value=FakeResponse()), \
             patch("scheduler.probes.json.load", return_value={"usageItems": []}):
            client = GitHubReadOnly("fake")
            with patch("scheduler.probes.urllib.request.Request") as request:
                client.get("/users/chaonpu/settings/billing/usage/summary?product=Actions")
                self.assertEqual(request.call_args.kwargs["method"], "GET")
        with self.assertRaises(ValueError):
            GitHubReadOnly("fake").get("/repos/chaonpu/Stock/contents/main.py")
        with self.assertRaises(ValueError):
            GitHubReadOnly("fake").get("/repos/chaonpu/Stock/actions/workflows/run-us.yml/dispatches")

    def test_permission_failure_is_unknown(self):
        exc = HTTPError("https://example.invalid", 403, "private", {}, None)
        self.assertEqual(safe_error(exc), "permission_denied")

    def test_missing_billing_and_runner_tokens_not_offline_or_exhausted(self):
        acc = account_usage("chaonpu", CONFIG["accounts"]["chaonpu"], {})
        self.assertEqual(acc["status"], "unknown")
        self.assertEqual(acc["read"], "secret_missing")
        runner = runner_for_repo("chaonpu/Stock", "ziwuxilv-stock-win",
                                  CONFIG["accounts"]["chaonpu"], {})
        self.assertEqual((runner["status"], runner["read"]), ("unknown", "secret_missing"))

    def test_public_preflight_never_dispatches(self):
        result = preflight(CONFIG, "stock_us", business_date="2026-10-16", env={})
        self.assertEqual(result["taskId"], "stock_us:2026-10-16")
        self.assertFalse(result["execution"]["dispatchAuthorized"])
        self.assertEqual(result["recommendation"]["route"], "hold")

    def test_existing_reserved_production_task_blocks_public_preflight(self):
        from copy import deepcopy
        mock_cfg = deepcopy(CONFIG)
        mock_cfg["dispatchEnabled"] = True
        mock_cfg["tasks"]["stock_a"]["enabledForDispatch"] = True
        state = reserve(mock_cfg, {"schema":1,"tasks":[]}, "stock_a",
                        route="cloud", coordinator_run_id=123,
                        business_date="2026-10-19", at="2026-10-17T06:00:00+08:00")
        response = preflight(CONFIG, "stock_a", business_date="2026-10-19",
                             env={}, registry_state=state)
        self.assertEqual(response["registeredTask"]["status"], "reserved")
        self.assertEqual(response["recommendation"]["route"], "hold")
        self.assertFalse(response["execution"]["dispatchAuthorized"])

    def test_effect_verified_registry_id_is_skipped_not_reissued(self):
        from copy import deepcopy
        mock_cfg = deepcopy(CONFIG)
        mock_cfg["dispatchEnabled"] = True
        mock_cfg["tasks"]["stock_a"]["enabledForDispatch"] = True
        state = reserve(mock_cfg, {"schema":1,"tasks":[]}, "stock_a",
                        route="cloud", coordinator_run_id=123,
                        business_date="2026-10-19", at="2026-10-17T06:00:00+08:00")
        state["tasks"][0].update(status="verified", runId=456)
        response = preflight(CONFIG, "stock_a", business_date="2026-10-19",
                             env={}, registry_state=state)
        self.assertEqual(response["recommendation"]["route"], "skip")
        self.assertEqual(response["registeredTask"]["status"], "verified")

    def test_untrusted_production_registry_fails_closed(self):
        with self.assertRaises(RegistryGuard):
            preflight(CONFIG, "stock_us", business_date="2026-10-19", env={},
                      registry_state={"schema":1,"tasks":[{"privateToken":"no"}]})

if __name__ == "__main__":
    unittest.main()
