"""No-side-effect production workflow safety gate tests."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scheduler.production_dry_run import (
    check, context_allowed, WORKFLOW_REF, SUPPORTED
)
from scheduler.production_registry import RegistryGuard

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/repositories.json").read_text())
STATE = json.loads((ROOT / "state/tasks.json").read_text())
DAY = "2026-10-19"


def ctx():
    return {
        "GITHUB_REPOSITORY": "chaonpu/Runner",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTOR": "chaonpu",
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": "38972769647",
    }


def fake_route(config, task, **kw):
    return {
        "taskId": task + ":" + kw["business_date"],
        "registeredTask": {"status": "not_registered"},
        "recommendation": {"route": "cloud", "reason": "synthetic_available"},
        "execution": {"dispatchAuthorized": False},
    }


class ProductionDryRunTests(unittest.TestCase):
    def test_context_must_be_manual_owner_main_and_exact_workflow(self):
        context_allowed(ctx())
        for name, value in (
            ("GITHUB_ACTOR", "attacker"),
            ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REF", "refs/heads/other"),
            ("GITHUB_REPOSITORY", "chaonpu/Stock"),
            ("GITHUB_WORKFLOW_REF", "chaonpu/Runner/.github/workflows/scheduler.yml@refs/heads/main"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_RUN_ID", "0"),
        ):
            with self.subTest(name=name):
                x = ctx()
                x[name] = value
                with self.assertRaisesRegex(RegistryGuard, "context_untrusted"):
                    context_allowed(x)

    def test_exactly_a_and_us_have_access_to_locked_gate(self):
        self.assertEqual(SUPPORTED, {"stock_a", "stock_us"})
        for task in ("android", "stock_etf", "database", "release"):
            with self.subTest(task=task):
                with self.assertRaises(RegistryGuard):
                    check(CONFIG, STATE, task, DAY, preflight_fn=fake_route)

    def test_legal_dates_only(self):
        for invalid in ("2026-02-30", "2026-10-1", "2026-13-04",
                        "today", "", "../2026-10-19"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(RegistryGuard, "date_invalid"):
                    check(CONFIG, STATE, "stock_a", invalid, preflight_fn=fake_route)

    def test_good_a_and_us_both_return_unconditionally_no_dispatch(self):
        for task in ("stock_a", "stock_us"):
            with self.subTest(task=task):
                result = check(CONFIG, STATE, task, DAY, preflight_fn=fake_route)
                self.assertEqual(result["taskId"], task + ":" + DAY)
                self.assertEqual(result["status"], "locked_read_only")
                self.assertEqual(result["routeRecommendation"], "cloud")
                for field in ("dispatchAuthorized", "registryWriteAuthorized",
                              "databaseWriteAuthorized", "automaticRetryAuthorized",
                              "productionReady"):
                    self.assertIs(result[field], False)
                self.assertNotIn("billing", result)
                self.assertNotIn("private", result)

    def test_any_production_switch_flipped_makes_gate_abort(self):
        for switch in ("global", "stock_a", "stock_us"):
            with self.subTest(switch=switch):
                modified = copy.deepcopy(CONFIG)
                if switch == "global":
                    modified["dispatchEnabled"] = True
                else:
                    modified["tasks"][switch]["enabledForDispatch"] = True
                preflight_fn = Mock()
                with self.assertRaisesRegex(RegistryGuard, "lock_is_not_off"):
                    check(modified, STATE, "stock_a", DAY, preflight_fn=preflight_fn)
                preflight_fn.assert_not_called()

    def test_bad_registry_never_reads_private_actions(self):
        preflight_fn = Mock()
        malicious = {"schema": 1, "tasks": [{
            "id": "stock_a:2026-10-19", "privateHoldings": [{"code": "EXAMPLE"}]}]}
        with self.assertRaises(RegistryGuard):
            check(CONFIG, malicious, "stock_a", DAY, preflight_fn=preflight_fn)
        preflight_fn.assert_not_called()

    def test_unknown_preflight_authorization_fails(self):
        def fake(config, task, **kwargs):
            a = fake_route(config, task, **kwargs)
            a["execution"]["dispatchAuthorized"] = True
            return a
        with self.assertRaisesRegex(RegistryGuard, "unexpected_preflight_authorization"):
            check(CONFIG, STATE, "stock_a", DAY, preflight_fn=fake)

    def test_stage_never_mutates_registry(self):
        original = copy.deepcopy(STATE)
        check(CONFIG, STATE, "stock_a", DAY, preflight_fn=fake_route)
        self.assertEqual(original, STATE)


if __name__ == "__main__":
    unittest.main()
