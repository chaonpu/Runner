"""Fail-closed routing tests for hosted Android quota reporting."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from scheduler import android_route as r
from scheduler import android_production as p


def report(remaining=1200, *, period="2026-10"):
    return {
        "read": "readable", "period": period,
        "observedAt": datetime.now(timezone.utc).isoformat(), "reportingPeriodVerified": True,
        "allowanceEstimate": {
            "read": "readable", "period": period,
            "conversionSupported": True,
            "estimateStatus": "private_standard_linux_only",
            "publicStandardUsageExcluded": True,
            "remainingMinutesEstimate": remaining,
            "exactRemainingVerified": False,
        },
    }


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.task = {"reserveMinutes": 200}

    def test_valid_estimate_prefers_hosted(self):
        selection = r.choose(self.task, report(1200))
        self.assertEqual(selection["route"], "hosted")
        self.assertFalse(selection["hostedQuotaGuaranteed"])
        self.assertEqual(r.workflow("hosted"), "android-cloud-verified.yml")
        self.assertEqual(r.artifact("hosted"), "cloud-test-verified")

    def test_insufficient_estimated_allowance_uses_windows(self):
        for remaining in (0, 10, 99):
            self.assertEqual(r.choose(self.task, report(remaining))["route"], "windows")
        self.assertEqual(r.choose(self.task, report(100))["route"], "hosted")

    def test_stale_unknown_or_mixed_runner_usage_pauses(self):
        for bad in (
            {},
            {"read": "permission_denied"},
            {"read": "readable", "period": "2026-10"},
            {**report(), "period": "2026-09"},
            {**report(), "allowanceEstimate": {
                **report()["allowanceEstimate"], "conversionSupported": False}},
            {**report(), "allowanceEstimate": {
                **report()["allowanceEstimate"], "publicStandardUsageExcluded": False}},
            {**report(), "allowanceEstimate": {
                **report()["allowanceEstimate"], "remainingMinutesEstimate": 1200.4}},
        ):
            self.assertEqual(r.choose(self.task, bad)["route"], "hold")

    def test_sku_different_route_names_never_accepted(self):
        for x in ("other", "", "cloud", "published"):
            with self.assertRaises(ValueError):
                r.workflow(x)
            with self.assertRaises(ValueError):
                r.artifact(x)

    def test_dispatch_exact_hosted_workflow_allowed_but_not_public_release(self):
        with patch.object(p, "api", return_value={"workflow_run_id": 927}) as send:
            self.assertEqual(p.dispatch("test-token",p.TEST,r.HOSTED_WORKFLOW,{}),927)
            self.assertEqual(send.call_args.args[1],p.TEST)
        with self.assertRaises(p.Hold):
            p.dispatch("test-token",p.SIGNER,"publish-android-release.yml",{})

    def test_hosted_run_must_prove_standard_ubuntu_runner(self):
        good={"total_count":1,"jobs":[{
            "name":"verify-and-handoff", "conclusion":"success",
            "labels":["ubuntu-24.04"], "runner_name":"GitHub Actions 8",
        }]}
        with patch.object(p, "api", return_value=good):
            p.verify_hosted_execution("test-token",p.TEST,110)
        for labels in (["self-hosted","ubuntu-24.04"], ["ubuntu-latest"], []):
            bad={"total_count":1,"jobs":[{
                "name":"verify-and-handoff", "conclusion":"success","labels":labels}]}
            with patch.object(p, "api", return_value=bad):
                with self.assertRaises(p.Hold):
                    p.verify_hosted_execution("test-token",p.TEST,111)


if __name__ == "__main__":
    unittest.main()
