"""Pure tests: hosted signing fallback and release trust boundary."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scheduler import android_production as android

def report(minutes):
    moment = datetime.now(timezone.utc)
    return {"period": moment.strftime("%Y-%m"),
            "observedAt": moment.isoformat(),
            "status": "exhausted" if minutes == 0 else "available",
            "independentlyVerified": True, "remainingFreeMinutes": minutes}

class CloudSignerTests(unittest.TestCase):
    def test_cloud_signing_when_remaining_at_least_100(self):
        with patch.object(android, "account_usage", return_value=report(125)):
            with patch.object(android, "runner_ready", side_effect=AssertionError(
                    "cloud signer cannot require local Windows")):
                self.assertEqual(android.choose_signer_route({"signingRunnerName":"win"},
                    {}, {}), ("cloud", android.CLOUD_SIGN_WORKFLOW))

    def test_windows_only_below_100_and_online(self):
        with patch.object(android, "account_usage", return_value=report(99)):
            with patch.object(android, "runner_ready", return_value=True):
                self.assertEqual(android.choose_signer_route({"signingRunnerName":"win"},
                    {}, {}), ("windows", android.SIGN_WORKFLOW))
            with patch.object(android, "runner_ready", return_value=False):
                with self.assertRaises(android.Hold):
                    android.choose_signer_route({"signingRunnerName":"win"}, {}, {})

    def test_unknown_allowance_blocks_cloud_and_windows(self):
        with patch.object(android, "account_usage", return_value={}):
            with patch.object(android, "runner_ready", side_effect=AssertionError(
                    "unknown billing cannot silently select Windows")):
                with self.assertRaises(android.Hold):
                    android.choose_signer_route({"signingRunnerName":"win"}, {}, {})

    def test_signer_workflow_exact_allowlist(self):
        with patch.object(android, "api", return_value={"workflow_run_id": 42}):
            self.assertEqual(android.dispatch("t", android.SIGNER,
                             android.CLOUD_SIGN_WORKFLOW, {"test_run_id":"23",
                             "source_commit":"a"*40}), 42)
            with self.assertRaises(android.Hold):
                android.dispatch("t", android.SIGNER, "android.yml", {})

    def test_route_is_immutable_after_dispatch(self):
        self.assertEqual(android.sign_route_for({"signingRoute":"cloud"}),
                         ("cloud", android.CLOUD_SIGN_WORKFLOW))
        self.assertEqual(android.sign_route_for({"signingRoute":"windows"}),
                         ("windows", android.SIGN_WORKFLOW))
        with self.assertRaises(android.Hold):
            android.sign_route_for({"signingRoute":"other"})

if __name__ == "__main__":
    unittest.main()
