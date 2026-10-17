"""Offline safety regressions for exact-run Android coordination."""
import unittest
from unittest.mock import patch
from scheduler import android_production as a

class Tests(unittest.TestCase):
    def record(self, repo, workflow, rid, sha):
        return dict(id=rid, repository={"full_name": repo},
                    path=".github/workflows/" + workflow, head_branch="main",
                    head_sha=sha, event="workflow_dispatch", run_attempt=1)

    def test_exact_source(self):
        sha = "a" * 40
        row = self.record(a.TEST, a.TEST_WORKFLOW, 42, sha)
        a.validate_run(row, a.TEST, a.TEST_WORKFLOW, 42, sha)
        row["head_sha"] = "b" * 40
        with self.assertRaises(a.Hold):
            a.validate_run(row, a.TEST, a.TEST_WORKFLOW, 42, sha)

    def test_signing_origin(self):
        row = self.record(a.SIGNER, a.SIGN_WORKFLOW, 43, "c" * 40)
        a.validate_run(row, a.SIGNER, a.SIGN_WORKFLOW, 43)
        for field in ("repository", "path", "event", "run_attempt", "head_branch"):
            original = row[field]
            row[field] = "forged"
            with self.assertRaises(a.Hold):
                a.validate_run(row, a.SIGNER, a.SIGN_WORKFLOW, 43)
            row[field] = original

    def test_dispatch_allowlist(self):
        with patch.object(a, "api", return_value={"workflow_run_id": 111}):
            self.assertEqual(a.dispatch("token", a.TEST, a.TEST_WORKFLOW, {}), 111)
            with self.assertRaises(a.Hold):
                a.dispatch("token", a.RUNNER, "publish-android-release.yml", {})
        with patch.object(a, "api", return_value={}):
            with self.assertRaises(a.Hold):
                a.dispatch("token", a.TEST, a.TEST_WORKFLOW, {})

    def test_read_only_never_dispatches(self):
        sha = "d" * 40
        cfg = {"accounts": {"anwpustudent-lab": {"actionsSecret": "T", "runnerSecret": "R"}, "chaonpu": {"actionsSecret": "C", "runnerSecret": "S"}}, "tasks": {"android": {"repo": a.TEST, "signingRepo": a.SIGNER,
               "windowsWorkflow": a.TEST_WORKFLOW,
               "signingWindowsWorkflow": a.SIGN_WORKFLOW, "runnerName": "ziwuxilv-test-win", "signingRunnerName": "ziwuxilv-sign-win"}}}
        def document(token, path):
            if path == a.CONFIG_PATH:
                return cfg, "blob"
            return {"version": 1, "builds": {}}, "blob"
        with patch.object(a, "json_file", side_effect=document):
            with patch.object(a, "current_source", return_value=sha):
                with patch.object(a, "dispatch", side_effect=AssertionError("unsafe")):
                    with patch.object(a, "record_public") as report:
                        a.run({"GH_TOKEN": "p", "TEST_ACTIONS_READ_TOKEN": "t"})
                        report.assert_called_once()

    def test_invalid_transition(self):
        sha = "e" * 40
        prior = {"version": 1, "builds": {sha: {"sourceSha": sha, "phase": "reserved"}}}
        with patch.object(a, "json_file", return_value=(prior, "blob")):
            with patch.object(a, "api", side_effect=AssertionError("write")):
                with self.assertRaises(a.Hold):
                    a.put_state("token", sha, "signed_verified")

    def test_runner_execution_is_bound(self):
        jobs = {"total_count": 1, "jobs": [
            {"runner_name": "ziwuxilv-test-win", "conclusion": "success"}]}
        with patch.object(a, "api", return_value=jobs):
            a.verify_execution_runner("token", a.TEST, 100, "ziwuxilv-test-win")
            with self.assertRaises(a.Hold):
                a.verify_execution_runner("token", a.TEST, 100, "untrusted-runner")

    def test_resume_requires_exact_original_sha(self):
        sha = "a" * 40
        cfg = {"accounts": {"anwpustudent-lab": {"actionsSecret": "T", "runnerSecret": "R"}, "chaonpu": {"actionsSecret": "C", "runnerSecret": "S"}}, "tasks": {"android": {"repo": a.TEST, "signingRepo": a.SIGNER,
               "windowsWorkflow": a.TEST_WORKFLOW,
               "signingWindowsWorkflow": a.SIGN_WORKFLOW, "runnerName": "ziwuxilv-test-win", "signingRunnerName": "ziwuxilv-sign-win"}}}
        record = {"version": 1, "builds": {sha: {
            "sourceSha": sha, "phase": "test_started", "testRunId": 42}}}
        def doc(token, path):
            return (cfg, "blob") if path == a.CONFIG_PATH else (record, "blob")
        with patch.object(a, "json_file", side_effect=doc):
            with self.assertRaisesRegex(a.Hold, "resume_requires_original_source_sha"):
                a.run({"GH_TOKEN": "p", "TEST_ACTIONS_READ_TOKEN": "t"}, execute=True, resume=True)

    def test_source_read_permission_fallback(self):
        sha = "f" * 40
        denied = a.Hold("github_http_403_anwpustudent-lab_source_head_no_retry")
        def lookup(token, repo, endpoint):
            if token == "actions":
                raise denied
            return {"commit": {"sha": sha}}
        with patch.object(a, "api", side_effect=lookup):
            self.assertEqual(a.current_source(("actions", "read-only")), sha)
        with patch.object(a, "api", side_effect=denied):
            with self.assertRaisesRegex(a.Hold, "contents_read_permission_missing"):
                a.current_source(("actions",))

    def test_manual_source_pin_no_unverified_signer_dispatch(self):
        sha = "d" * 40
        cfg = {"accounts": {"anwpustudent-lab": {"actionsSecret": "T", "runnerSecret": "R"},
                            "chaonpu": {"actionsSecret": "C", "runnerSecret": "S"}},
               "tasks": {"android": {
                   "repo": a.TEST, "signingRepo": a.SIGNER,
                   "windowsWorkflow": a.TEST_WORKFLOW,
                   "signingWindowsWorkflow": a.SIGN_WORKFLOW,
                   "runnerName": "test", "signingRunnerName": "sign"}}}
        def document(token, path):
            return (cfg, "blob") if path == a.CONFIG_PATH else ({"version": 1, "builds": {}}, "blob")
        with patch.object(a, "json_file", side_effect=document):
            with patch.object(a, "current_source",
                              side_effect=a.Hold("test_main_contents_read_permission_missing")):
                with patch.object(a, "dispatch", side_effect=AssertionError("unexpected dispatch")):
                    with patch.object(a, "record_public") as report:
                        a.run({"GH_TOKEN": "public"}, requested_sha=sha)
                        self.assertEqual(report.call_args.kwargs["branchShaVerified"], False)
                        self.assertEqual(report.call_args.kwargs["manualSourceShaRequired"], False)
                    with self.assertRaisesRegex(a.Hold, "source_sha_required"):
                        a.run({"GH_TOKEN": "public"}, execute=True)

    def test_signing_must_reserve_intent_before_dispatch(self):
        sha = "b" * 40
        entry = {"version": 1, "builds": {sha: {
            "sourceSha": sha, "phase": "test_verified", "testRunId": 123}}}
        with patch.object(a, "json_file", return_value=(entry, "blob")):
            with patch.object(a, "api", side_effect=AssertionError("unexpected write")):
                with self.assertRaisesRegex(a.Hold, "state_transition_not_allowed"):
                    a.put_state("token", sha, "signer_started")

    def test_failed_run_categories_use_only_job_step_metadata(self):
        cases = (
            (a.TEST, "NETWORK_TRANSIENT_PREFLIGHT - verify HTTPS",
             "network_retry_exhausted_same_run_no_publication"),
            (a.TEST, "NETWORK_TRANSIENT_SOURCE_FETCH - bounded retries exhausted",
             "network_retry_exhausted_same_run_no_publication"),
            (a.SIGNER, "NETWORK_TRANSIENT_SOURCE_FETCH - signer fetch",
             "network_retry_exhausted_same_run_no_publication"),
            (a.TEST, "SOURCE_CHECKOUT_UNVERIFIED - investigate",
             "source_checkout_unverified_manual_review"),
            (a.TEST, "Build unsigned Release APK on Windows",
             "android_compile_failed_manual_review"),
            (a.TEST, "Run unit tests, release lint and debug APK build",
             "android_tests_or_lint_failed_manual_review"),
            (a.SIGNER, "Sign already-tested unsigned APK without executing Test code",
             "android_signing_failed_manual_review"),
            (a.SIGNER, "Verify certificate, package, version and signed APK digest",
             "android_signature_verification_failed_manual_review"),
        )
        for repo, failed, expected in cases:
            with self.subTest(repo=repo, failed=failed):
                steps = {"total_count": 1, "jobs": [{
                    "conclusion": "failure", "steps": [
                        {"name": failed, "conclusion": "failure"}]}]}
                with patch.object(a, "api", return_value=steps) as request:
                    self.assertEqual(a.classify_failed_run("private", repo, 321), expected)
                    self.assertIn("/jobs?per_page=100", request.call_args.args[2])

    def test_checkout_failure_not_automatically_called_network_error(self):
        jobs = {"total_count": 1, "jobs": [{
            "conclusion": "failure", "steps": [
                {"name": "Check out private Test source - attempt 3",
                 "conclusion": "failure"}]}]}
        with patch.object(a, "api", return_value=jobs):
            self.assertEqual(a.classify_failed_run("t", a.TEST, 9),
                             "android_test_or_artifact_failed_manual_review")

    def test_failed_exact_run_never_returns_success(self):
        sha = "a" * 40
        item = self.record(a.TEST, a.TEST_WORKFLOW, 9, sha)
        item.update(status="completed", conclusion="failure")
        jobs = {"total_count": 1, "jobs": [{
            "conclusion": "failure", "steps": [{
                "name": "NETWORK_TRANSIENT_PREFLIGHT - GitHub HTTPS",
                "conclusion": "failure"}]}]}
        with patch.object(a, "api", side_effect=[item, jobs]) as request:
            with self.assertRaisesRegex(a.Hold, "network_retry_exhausted"):
                a.wait_run("private", a.TEST, a.TEST_WORKFLOW, 9, sha)
            self.assertEqual(request.call_count, 2)  # both GET; never dispatch

    def test_failure_record_preserves_phase_and_bound_source(self):
        import base64
        import json
        sha = "e" * 40
        record = {"version": 1, "builds": {sha: {
            "phase": "test_started", "sourceSha": sha, "testRunId": 9}}}
        with patch.object(a, "json_file", return_value=(record, "blob")):
            with patch.object(a, "api", return_value={
                    "commit": {"sha": "f" * 40}}) as write:
                a.record_failed_stage("public", sha, "test", 9,
                                      "network_retry_exhausted_same_run_no_publication")
                body = write.call_args.kwargs["payload"]
                updated = json.loads(base64.b64decode(body["content"]))
                entry = updated["builds"][sha]
                self.assertEqual(entry["phase"], "test_started")
                self.assertNotEqual(entry["phase"], "signed_verified")
                self.assertEqual(entry["testRunId"], 9)
                self.assertEqual(entry["lastFailure"]["stage"], "test")
                self.assertEqual(body["sha"], "blob")
                self.assertEqual(write.call_args.kwargs["method"], "PUT")
        with patch.object(a, "json_file", return_value=(record, "blob")):
            with patch.object(a, "api", side_effect=AssertionError("unsafe write")):
                with self.assertRaisesRegex(a.Hold, "failure_run_binding_mismatch"):
                    a.record_failed_stage("public", sha, "signer", 9,
                                          "network_retry_exhausted_same_run_no_publication")

    def test_failed_stage_records_without_dispatching_or_signing(self):
        sha = "e" * 40
        with patch.object(a, "wait_run",
                          side_effect=a.Hold("android_compile_failed_manual_review")):
            with patch.object(a, "record_failed_stage") as saved:
                with patch.object(a, "dispatch",
                                  side_effect=AssertionError("unsafe redispatch")):
                    with patch.object(a, "record_public") as report:
                        with self.assertRaisesRegex(a.Hold, "android_compile_failed"):
                            a.wait_recorded("public", sha, "test", "private",
                                            a.TEST, a.TEST_WORKFLOW, 9, sha)
                        saved.assert_called_once()
                        self.assertEqual(report.call_args.kwargs["failureClass"],
                                         "android_compile_failed_manual_review")

if __name__ == "__main__":
    unittest.main()
