import copy
import json
from pathlib import Path
import unittest

from scheduler.runtime_status import audit, billing_token_repository_access

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/repositories.json").read_text(encoding="utf-8"))
STATE = json.loads((ROOT / "state/tasks.json").read_text(encoding="utf-8"))


def billing(owner, spec, env):
    # A nonzero usage report is not sufficient to establish available minutes.
    return {"read": "readable", "status": "available",
            "independentlyVerified": False, "minuteUsageBySku": {"actions_linux": 999999}}


def runner(repo, name, spec, env):
    return {"name": name, "read": "readable", "status": "online",
            "busy": False, "labelsVerified": True}


def attempts(repo, workflow, spec, env):
    return {"read": "readable", "status": "no_active_run"}


class RuntimeStatusTests(unittest.TestCase):
    def invoke(self, config=None):
        return audit(config or CONFIG, STATE, {},
                     bill=billing, probe_runner=runner, active=attempts)

    def test_dedicated_stock_runner_secret_mapping(self):
        stock = CONFIG["accounts"]["chaonpu"]
        self.assertEqual(stock["runnerSecret"], "RUNNER_READ_TOKEN")
        self.assertNotEqual(stock["runnerSecret"], stock["billingSecret"])
        # Keep the other account unchanged until its own runner token is verified.
        other = CONFIG["accounts"]["anwpustudent-lab"]
        self.assertEqual(other["runnerSecret"], other["billingSecret"])

    def test_billing_token_repository_scope_is_reported_without_runner_claim(self):
        class Visible:
            def __init__(self, token):
                self.asserted = token
            def get(self, path):
                if path != "/repos/chaonpu/Stock":
                    raise AssertionError("Wrong diagnostic API path")
                return {"full_name": "chaonpu/Stock"}
        a = CONFIG["accounts"]["chaonpu"]
        env = {a["billingSecret"]: "synthetic"}
        self.assertEqual(
            billing_token_repository_access("chaonpu/Stock", a, env,
                                            reader_factory=Visible), "readable")
        self.assertEqual(
            billing_token_repository_access("chaonpu/Stock", a, {}),
            "secret_missing")
        class Forbidden:
            def __init__(self, token):
                pass
            def get(self, path):
                from urllib.error import HTTPError
                raise HTTPError("https://api.github.com",404,"synthetic",{},None)
        self.assertEqual(
            billing_token_repository_access("chaonpu/Stock", a, env,
                                            reader_factory=Forbidden), "not_found")

    def test_unverified_free_balance_not_inferred(self):
        obj = self.invoke()
        self.assertEqual(obj["status"], "production_locked")
        self.assertTrue(all(x["hostedDecision"] == "unknown"
                            for x in obj["accounts"].values()))
        self.assertTrue(all(x["verifiedRemainingFreeMinutes"] is False
                            for x in obj["accounts"].values()))
        self.assertFalse(obj["dispatchAuthorized"])
        self.assertFalse(obj["registryWriteAuthorized"])

    def test_online_runner_cannot_override_missing_workflow_allowlist(self):
        obj = self.invoke()
        for entry in obj["stock"].values():
            self.assertTrue(entry["windowsOnlineAndLabelsVerified"])
            self.assertFalse(entry["windowsWorkflowAllowlisted"])
            self.assertFalse(entry["productionRouteAuthorized"])

    def test_any_enabled_production_switch_blocks_diagnostic(self):
        for market in ("stock_a", "stock_us", "android"):
            config = copy.deepcopy(CONFIG)
            config["tasks"][market]["enabledForDispatch"] = True
            with self.subTest(market=market), self.assertRaises(ValueError):
                self.invoke(config)
        config = copy.deepcopy(CONFIG)
        config["dispatchEnabled"] = True
        with self.assertRaises(ValueError):
            self.invoke(config)

    def test_unreadable_network_remains_hold(self):
        obj = audit(CONFIG, STATE, {},
                    bill=lambda a, b, c: {"read": "secret_missing"},
                    probe_runner=lambda a, b, c, d: {"read": "permission_denied"},
                    active=lambda a, b, c, d: {"read": "rate_limited"})
        for entry in obj["stock"].values():
            self.assertFalse(entry["windowsOnlineAndLabelsVerified"])
            self.assertEqual(entry["activeRunStatus"], "unknown")
            self.assertFalse(entry["productionRouteAuthorized"])


if __name__ == "__main__":
    unittest.main()
