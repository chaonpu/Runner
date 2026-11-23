"""Fail-closed one-time recovery of an interrupted Windows Android build."""

import unittest
from unittest.mock import patch

from scheduler import android_production as a


class InterruptedBuildRecoveryTests(unittest.TestCase):
    sha = "1" * 40
    old = 12345
    fresh = 67890
    runner = "ziwuxilv-test-win"
    publish = "Publish verified Test handoff to PRIVATE Release Assets (not Actions Artifact)"

    def entry(self):
        return {
            "sourceSha": self.sha,
            "phase": "test_started",
            "route": "windows",
            "liveSmoke": False,
            "testRunId": self.old,
            "lastFailure": {
                "stage": "test",
                "runId": self.old,
                "code": "android_test_or_artifact_failed_manual_review",
            },
        }

    def original(self):
        return {
            "id": self.old,
            "repository": {"full_name": a.TEST},
            "path": ".github/workflows/" + a.TEST_WORKFLOW,
            "head_branch": "main",
            "head_sha": self.sha,
            "event": "workflow_dispatch",
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "failure",
        }

    def jobs(self):
        success = [
            "Verify Windows Runner", "Verify JDK 17", "Verify Git and Bash",
            "Verify Python", "Verify Android SDK", "Verify exact checked out commit",
            "Verify Gradle wrapper 9.5.0 and Android plugin",
        ]
        skipped = [
            "Verify release version and Test source guardrails",
            "Test live chart evidence verifier",
            "Clear prior test reports before validation",
            "Run unit tests, release lint and debug APK build",
            "Verify live chart execution evidence",
            "Verify actual public calendar network execution",
            "Verify release and debug notification manifests",
            "Prepare private verified Windows signing handoff",
            "Verify private Release transport policy",
            self.publish,
        ]
        steps = ([{"name": s, "conclusion": "success"} for s in success]
                 + [{"name": "Build unsigned Release APK on Windows",
                     "conclusion": "cancelled"}]
                 + [{"name": s, "conclusion": "skipped"} for s in skipped])
        return {"total_count": 2, "jobs": [
            {"name": "test", "run_id": self.old, "run_attempt": 1,
             "status": "completed", "conclusion": "cancelled",
             "runner_name": self.runner, "steps": steps},
            {"name": "Verify private Release handoff idempotence (no APK)",
             "status": "completed", "conclusion": "skipped", "steps": []},
        ]}

    def evidence_api(self, jobs=None, original=None, artifacts=None):
        jobs = self.jobs() if jobs is None else jobs
        original = self.original() if original is None else original
        artifacts = ({"total_count": 0, "artifacts": []}
                     if artifacts is None else artifacts)

        def fetch(token, repo, path, **kwargs):
            if path == f"/actions/runs/{self.old}":
                return original
            if path == f"/actions/runs/{self.old}/jobs?per_page=100":
                return jobs
            if path == f"/actions/runs/{self.old}/artifacts?per_page=100":
                return artifacts
            if path == "/contents/" + a.STATE_PATH and kwargs.get("method") == "PUT":
                return {"commit": {"sha": "a" * 40}}
            raise AssertionError("unexpected GitHub API: " + path)
        return fetch

    def call(self, row, **overrides):
        return a.recover_reviewed_interrupted_test(
            "public", "test", self.sha, row, {}, {}, 
            {"TEST_SOURCE_READ_TOKEN": "read"}, {"runnerName": self.runner},
            **overrides)

    def test_verified_cancellation_reserves_and_rebuilds_only_once(self):
        row = self.entry()
        state = {"version": 1, "builds": {self.sha: row}}
        with patch.object(a, "api", side_effect=self.evidence_api()) as api, \
             patch.object(a, "json_file", return_value=(state, "blob")), \
             patch.object(a, "current_source", return_value=self.sha), \
             patch.object(a, "check_idle") as idle, \
             patch.object(a, "runner_ready", return_value=True), \
             patch.object(a, "dispatch", return_value=self.fresh) as dispatch, \
             patch.object(a, "put_state") as saved, \
             patch.object(a, "record_public") as reported:
            self.assertEqual(self.call(row), self.fresh)
        self.assertEqual(idle.call_count, 4)
        self.assertEqual(dispatch.call_args.args[:3],
                         ("test", a.TEST, a.TEST_WORKFLOW))
        self.assertEqual(dispatch.call_args.args[3], {"live_smoke": "false"})
        saved.assert_called_once_with(
            "public", self.sha, "test_started", test_run_id=self.fresh)
        self.assertEqual(row["phase"], "test_retry_reserved")
        self.assertEqual(row["testInterruptedRetries"], 1)
        self.assertEqual(row["failedTestRunIds"], [self.old])
        self.assertEqual(row["testRunId"], self.old)
        self.assertEqual(row["testInterruptedRecovery"]["originalTestRunId"], self.old)
        self.assertEqual(row["lastFailure"]["runId"], self.old)
        self.assertNotIn("signingRunId", row)
        self.assertEqual(reported.call_args.args[1], "test_interrupted_recovery_started")
        self.assertEqual([x for x in api.call_args_list if x.kwargs.get("method") == "PUT"].__len__(), 1)

    def test_rejects_other_failure_and_wrong_job_before_dispatch(self):
        cases = []
        modified = self.jobs()
        modified["jobs"][0]["steps"][7]["conclusion"] = "failure"
        cases.append(("compile_failed", modified))
        modified = self.jobs()
        modified["jobs"][0]["steps"][-1]["conclusion"] = "success"
        cases.append(("handoff_started", modified))
        modified = self.jobs()
        modified["jobs"][0]["runner_name"] = "unexpected-windows-runner"
        cases.append(("other_runner", modified))
        modified = self.jobs()
        modified["jobs"][0]["conclusion"] = "failure"
        cases.append(("not_cancelled", modified))
        modified = self.jobs()
        modified["jobs"][0]["steps"][1]["conclusion"] = "cancelled"
        cases.append(("multiple_cancelled", modified))
        modified = self.jobs()
        modified["jobs"][0]["steps"].append(
            {"name": "Verify JDK 17", "conclusion": "success"})
        cases.append(("duplicate_steps", modified))
        for name, evidence in cases:
            with self.subTest(name=name), \
                 patch.object(a, "api", side_effect=self.evidence_api(jobs=evidence)), \
                 patch.object(a, "dispatch", side_effect=AssertionError("redispatch")), \
                 patch.object(a, "current_source", side_effect=AssertionError("unsafe")):
                with self.assertRaises(a.Hold):
                    self.call(self.entry())

    def test_fails_closed_for_artifacts_or_unbound_original(self):
        with patch.object(a, "api", side_effect=self.evidence_api(
                artifacts={"total_count": 1, "artifacts": [{"id": 1}]})), \
             patch.object(a, "dispatch", side_effect=AssertionError("dispatch")):
            with self.assertRaisesRegex(a.Hold, "artifacts_exist"):
                self.call(self.entry())
        other = dict(self.original(), head_sha="2" * 40)
        with patch.object(a, "api", side_effect=self.evidence_api(original=other)), \
             patch.object(a, "dispatch", side_effect=AssertionError("dispatch")):
            with self.assertRaisesRegex(a.Hold, "run_origin_mismatch|source_moved"):
                self.call(self.entry())

    def test_refuses_automatic_or_repeated_rebuild(self):
        for key, value in (
            ("testInterruptedRetries", 1),
            ("failedTestRunIds", [self.old]),
            ("signingRunId", 777),
            ("testLiveRecovery", {"originalTestRunId": self.old}),
        ):
            row = self.entry()
            row[key] = value
            with self.subTest(key=key), \
                 patch.object(a, "api", side_effect=AssertionError("GitHub call")), \
                 patch.object(a, "dispatch", side_effect=AssertionError("dispatch")):
                with self.assertRaises(a.Hold):
                    self.call(row)
        row = self.entry()
        row["lastFailure"]["code"] = "android_compile_failed_manual_review"
        with patch.object(a, "api", side_effect=AssertionError("GitHub call")):
            with self.assertRaisesRegex(a.Hold, "reviewed_failure_missing"):
                self.call(row)

    def test_current_main_must_match_and_runner_must_be_ready(self):
        with patch.object(a, "api", side_effect=self.evidence_api()), \
             patch.object(a, "current_source", return_value="2" * 40), \
             patch.object(a, "dispatch", side_effect=AssertionError("dispatch")):
            with self.assertRaisesRegex(a.Hold, "source_no_longer_main"):
                self.call(self.entry())
        with patch.object(a, "api", side_effect=self.evidence_api()), \
             patch.object(a, "current_source", return_value=self.sha), \
             patch.object(a, "check_idle"), \
             patch.object(a, "runner_ready", return_value=False), \
             patch.object(a, "dispatch", side_effect=AssertionError("dispatch")):
            with self.assertRaisesRegex(a.Hold, "runner_not_ready"):
                self.call(self.entry())

    def test_flag_requires_explicit_owner_resume_and_exact_sha(self):
        for kwargs in (
            {"recover_interrupted_test": True},
            {"execute": True, "recover_interrupted_test": True},
            {"resume": True, "recover_interrupted_test": True},
            {"execute": True, "resume": True,
             "recover_interrupted_test": True},
            {"execute": True, "resume": True, "requested_sha": self.sha,
             "recover_interrupted_test": True, "recover_live_validation": True},
            {"execute": True, "resume": True, "requested_sha": self.sha,
             "recover_interrupted_test": True, "recover_signer_prekey": True},
            {"execute": True, "resume": True, "requested_sha": self.sha,
             "recover_interrupted_test": True, "verified_test_run_id": 100},
        ):
            with self.subTest(kwargs=kwargs), \
                 patch.object(a, "json_file", side_effect=AssertionError("state read")):
                with self.assertRaisesRegex(a.Hold,
                                            "interrupted_recovery_requires_explicit_owner_resume"):
                    a.run({}, **kwargs)

    def test_explicit_resume_switches_to_replacement_run_before_signing(self):
        cfg = {"accounts": {"anwpustudent-lab": {}, "chaonpu": {}},
               "tasks": {"android": {
                   "repo": a.TEST, "signingRepo": a.SIGNER,
                   "windowsWorkflow": a.TEST_WORKFLOW,
                   "signingWindowsWorkflow": a.SIGN_WORKFLOW,
                   "runnerName": self.runner, "signingRunnerName": "ziwuxilv-sign-win"}}}
        row = self.entry()
        state = {"version": 1, "builds": {self.sha: row}}

        def document(token, path):
            return (cfg if path == a.CONFIG_PATH else state), "blob"

        def recover(*args):
            row["testRunId"] = self.fresh
            return self.fresh

        with patch.object(a, "json_file", side_effect=document), \
             patch.object(a, "recover_reviewed_interrupted_test", side_effect=recover) as recovery, \
             patch.object(a, "wait_recorded", side_effect=a.Hold("stopped_after_recovery")) as wait, \
             patch.object(a, "dispatch", side_effect=AssertionError("unintended dispatch")):
            with self.assertRaisesRegex(a.Hold, "stopped_after_recovery"):
                a.run({"GH_TOKEN": "public", "TEST_ACTIONS_READ_TOKEN": "test"},
                      execute=True, resume=True, requested_sha=self.sha,
                      recover_interrupted_test=True)
        recovery.assert_called_once()
        wait.assert_called_once_with("public", self.sha, "test", "test",
                                     a.TEST, a.TEST_WORKFLOW, self.fresh, self.sha)


if __name__ == "__main__":
    unittest.main()
