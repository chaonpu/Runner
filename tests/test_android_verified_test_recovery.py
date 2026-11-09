"""Owner-approved recovery reuses only a fully proven independent same-SHA Test run."""
import base64
import json
import unittest
from unittest.mock import patch

from scheduler import android_production as a


class VerifiedTestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.old = 10
        self.verified = 11
        self.publish = "Publish verified Test handoff to PRIVATE Release Assets (not Actions Artifact)"
        self.compiled = "Run unit tests, release lint, and build unsigned and debug APKs"
        self.prepared = "Prepare immutable signed-chain compatible Test handoff"
        self.entry = {
            "sourceSha": self.sha, "phase": "test_started", "route": "hosted",
            "testRunId": self.old, "liveSmoke": False,
            "lastFailure": {
                "runId": self.old, "stage": "test",
                "code": "android_test_private_handoff_failed_manual_review",
                "recordedAt": "2026-11-09T20:21:10Z",
            },
        }
        self.state = {"version": 1, "builds": {self.sha: self.entry.copy()}}
        self.prior = self.run(self.old, "failure")
        self.passed = self.run(self.verified, "success")
        self.failed_steps = [
            {"name": self.compiled, "conclusion": "success"},
            {"name": self.prepared, "conclusion": "success"},
            {"name": self.publish, "conclusion": "failure"},
        ]
        self.passed_steps = [
            {"name": name, "conclusion": "success"} for name in (
                "Verify exact Test source commit and no signing secrets",
                "Verify source version, Android policy, and notification checks",
                self.compiled, self.prepared,
                "Verify private Release transport policy", self.publish,
            )
        ]

    def run(self, rid, conclusion):
        return {
            "id": rid, "repository": {"full_name": a.TEST},
            "event": "workflow_dispatch", "head_branch": "main",
            "run_attempt": 1, "path": ".github/workflows/" + a.HOSTED_WORKFLOW,
            "head_sha": self.sha, "status": "completed", "conclusion": conclusion,
        }

    def setup_responses(self, *, bad_sha=False, missing_step=False):
        passed = dict(self.passed)
        if bad_sha:
            passed["head_sha"] = "f" * 40
        good_steps = self.passed_steps[:-1] if missing_step else self.passed_steps
        writes = []
        def api(token, repo, path, *, method="GET", payload=None):
            if method == "PUT":
                assert repo == a.RUNNER and path == "/contents/" + a.STATE_PATH
                writes.append(payload)
                new = json.loads(base64.b64decode(payload["content"]))
                self.state = new
                return {"commit": {"sha": "b" * 40}}
            lookups = {
                f"/actions/runs/{self.old}": self.prior,
                f"/actions/runs/{self.old}/jobs?per_page=100": {
                    "total_count": 1,
                    "jobs": [{"conclusion": "failure", "steps": self.failed_steps}],
                },
                f"/actions/runs/{self.verified}": passed,
                f"/actions/runs/{self.verified}/jobs?per_page=100": {
                    "total_count": 1,
                    "jobs": [{"conclusion": "success", "steps": good_steps}],
                },
            }
            if path not in lookups:
                raise AssertionError("Unexpected API path: " + path)
            return lookups[path]
        def doc(token, path):
            assert path == a.STATE_PATH
            return self.state, "c" * 40
        return api, doc, writes

    def test_successful_recovery_is_explicit_once_and_audits_old_failure(self):
        api, doc, writes = self.setup_responses()
        with patch.object(a, "api", side_effect=api), \
             patch.object(a, "json_file", side_effect=doc), \
             patch.object(a, "verify_hosted_execution") as hosted, \
             patch.object(a, "verify_artifact") as proof, \
             patch.object(a, "record_public") as report:
            result = a.recover_independent_verified_test(
                "pub", "test", self.sha, self.entry,
                self.verified, {"TEST_SOURCE_READ_TOKEN": "read"})
        self.assertEqual(len(writes), 1)
        self.assertEqual(result["phase"], "test_verified")
        self.assertEqual(result["testRunId"], self.verified)
        self.assertEqual(result["verifiedTestRecovery"]["failedTestRunId"], self.old)
        self.assertEqual(result["verifiedTestRecovery"]["verifiedTestRunId"], self.verified)
        self.assertNotIn("lastFailure", result)
        hosted.assert_called_once_with("test", a.TEST, self.verified)
        proof.assert_called_once_with("read", a.TEST, self.verified,
                                      "cloud-test-verified", self.sha)
        report.assert_called_once()
        with self.assertRaisesRegex(a.Hold, "not_eligible"):
            a.recover_independent_verified_test(
                "pub", "test", self.sha, result, self.verified, {})

    def test_reject_different_sha_without_ever_writing(self):
        api, doc, writes = self.setup_responses(bad_sha=True)
        with patch.object(a, "api", side_effect=api), \
             patch.object(a, "json_file", side_effect=doc), \
             patch.object(a, "verify_hosted_execution") as hosted, \
             patch.object(a, "verify_artifact") as proof:
            with self.assertRaisesRegex(a.Hold, "source_moved_during_dispatch"):
                a.recover_independent_verified_test(
                    "pub", "test", self.sha, self.entry, self.verified, {})
        self.assertEqual(writes, [])
        hosted.assert_not_called()
        proof.assert_not_called()

    def test_missing_successful_publish_step_is_rejected(self):
        api, doc, writes = self.setup_responses(missing_step=True)
        with patch.object(a, "api", side_effect=api), \
             patch.object(a, "json_file", side_effect=doc), \
             patch.object(a, "verify_hosted_execution"), \
             patch.object(a, "verify_artifact") as proof:
            with self.assertRaisesRegex(a.Hold, "success_steps_incomplete"):
                a.recover_independent_verified_test(
                    "pub", "test", self.sha, self.entry, self.verified, {})
        self.assertEqual(writes, [])
        proof.assert_not_called()

    def test_only_isolated_private_handoff_failures_can_be_recovered(self):
        bad = dict(self.entry, lastFailure=dict(self.entry["lastFailure"],
                                                code="android_compile_failed_manual_review"))
        with patch.object(a, "api", side_effect=AssertionError("network called")):
            with self.assertRaisesRegex(a.Hold, "failure_not_proven"):
                a.recover_independent_verified_test(
                    "pub", "test", self.sha, bad, self.verified, {})

    def test_must_use_owner_explicit_execute_and_resume(self):
        for kwargs in (
            {"verified_test_run_id": self.verified},
            {"execute": True, "verified_test_run_id": self.verified},
            {"resume": True, "verified_test_run_id": self.verified},
            {"execute": True, "resume": True, "verified_test_run_id": self.verified},
            {"execute": True, "resume": True, "requested_sha": self.sha,
             "verified_test_run_id": self.verified, "recover_live_validation": True},
        ):
            with self.subTest(kwargs=kwargs):
                with patch.object(a, "json_file", side_effect=AssertionError("state accessed")):
                    with self.assertRaises(a.Hold):
                        a.run({}, **kwargs)

    def test_invalid_recovery_id_or_disabled_route_blocks_calls(self):
        for value in (-1, "11"):
            with self.subTest(value=value):
                with patch.object(a, "json_file", side_effect=AssertionError("state accessed")):
                    with self.assertRaisesRegex(a.Hold, "verified_test_run_id_invalid"):
                        a.run({}, verified_test_run_id=value)


if __name__ == "__main__":
    unittest.main()
