"""Offline safety regressions for exact-run Android coordination."""
import unittest
from unittest.mock import patch
from scheduler import android_production as a

class Tests(unittest.TestCase):
    def test_runner_readiness_reports_cause_without_dispatch_or_reservation(self):
        cases = [
            ({"read": "permission_denied"}, "state_unreadable", False),
            ({"read": "secret_missing"}, "state_unreadable", False),
            ({"status": "not_registered"}, "not_registered", False),
            ({"status": "offline"}, "offline", False),
            ({"status": "unknown"}, "status_unknown", False),
            ({"labelsVerified": False}, "labels_mismatch", False),
            ({"busy": True}, "busy", False),
            ({"busy": None}, "busy_state_unknown", False),
            ({}, "ready", True),
        ]
        for changes, expected, ready in cases:
            with self.subTest(expected=expected, changes=changes):
                probe = dict(read="readable", status="online", busy=False,
                             labelsVerified=True)
                probe.update(changes)
                with patch.object(a, "runner_for_repo", return_value=probe):
                    with patch.object(a, "record_public") as report:
                        with patch.object(a, "dispatch", side_effect=AssertionError("dispatch")):
                            with patch.object(a, "reserve", side_effect=AssertionError("reserve")):
                                self.assertIs(a.runner_ready(a.TEST, "ziwuxilv-test-win",
                                                            {}, {}), ready)
                        self.assertEqual(report.call_args.kwargs["reason"], expected)
                        self.assertEqual(report.call_args.kwargs["probe"], probe)

    def test_chart_push_ci_does_not_block_manual_apk_dispatch(self):
        with patch.object(a, "existing_attempt",
                          return_value={"status": "no_active_run",
                                        "read": "readable"}) as probe:
            a.check_idle(a.TEST, a.TEST_WORKFLOW, {}, {})
        probe.assert_called_once_with(a.TEST, a.TEST_WORKFLOW, {}, {},
                                      event="workflow_dispatch")

    def test_manual_apk_is_still_blocked_if_a_second_manual_apk_is_active(self):
        with patch.object(a, "existing_attempt",
                          return_value={"status": "active"}) as probe:
            with self.assertRaisesRegex(a.Hold, "active_or_unknown_private_workflow"):
                a.check_idle(a.TEST, a.TEST_WORKFLOW, {}, {})
        probe.assert_called_once_with(a.TEST, a.TEST_WORKFLOW, {}, {},
                                      event="workflow_dispatch")

    def test_signer_workflow_keeps_original_all_event_safety_probe(self):
        with patch.object(a, "existing_attempt",
                          return_value={"status": "no_active_run"}) as probe:
            a.check_idle(a.SIGNER, a.SIGN_WORKFLOW, {}, {})
        probe.assert_called_once_with(a.SIGNER, a.SIGN_WORKFLOW, {}, {},
                                      event=None)

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

    def test_already_signed_source_skips_without_dispatching(self):
        sha = "a" * 40
        cfg = {"accounts": {"anwpustudent-lab": {}, "chaonpu": {}},
               "tasks": {"android": {"repo": a.TEST, "signingRepo": a.SIGNER,
                   "windowsWorkflow": a.TEST_WORKFLOW,
                   "signingWindowsWorkflow": a.SIGN_WORKFLOW}}}
        row = {"sourceSha": sha, "phase": "signed_verified",
               "route": "windows", "testRunId": 321, "signingRunId": 456}
        def document(token, path):
            if path == a.CONFIG_PATH:
                return cfg, "config"
            return {"version": 1, "builds": {sha: row}}, "state"
        with patch.object(a, "json_file", side_effect=document):
            with patch.object(a, "current_source", return_value=sha):
                with patch.object(a, "dispatch", side_effect=AssertionError("duplicate dispatch")):
                    with patch.object(a, "reserve", side_effect=AssertionError("duplicate reservation")):
                        with patch.object(a, "record_public") as report:
                            a.run({"GH_TOKEN": "public", "TEST_ACTIONS_READ_TOKEN": "test"},
                                  execute=True, requested_sha=sha)
                            report.assert_called_once_with(
                                sha, "already_signed", status="skipped",
                                reason="source_already_signed_verified_no_rebuild",
                                priorPhase="signed_verified", testRunId=321,
                                signingRunId=456,
                                publication="not_authorized_no_public_apk")
                # Signed state without an original signer ID must NOT masquerade as verified.
                row.pop("signingRunId")
                with self.assertRaisesRegex(a.Hold, "registered_signed_proof_incomplete"):
                    a.run({"GH_TOKEN": "public", "TEST_ACTIONS_READ_TOKEN": "test"},
                          execute=True, requested_sha=sha)

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
            (a.TEST, "SOURCE_WORKTREE_FAILED - verified source could not be installed locally",
             "source_local_worktree_failed_manual_review"),
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

    def test_setup_only_failure_is_not_called_apk_compile_or_artifact_failure(self):
        for repo, expected in (
            (a.TEST, "android_test_job_setup_failed_manual_review"),
            (a.SIGNER, "android_signer_job_setup_failed_manual_review"),
        ):
            with self.subTest(repo=repo):
                response = {"total_count": 2, "jobs": [
                    {"conclusion": "failure",
                     "steps": [{"name": "Set up job",
                                "conclusion": "failure"}]},
                    {"conclusion": "skipped", "steps": []},
                ]}
                with patch.object(a, "api", return_value=response):
                    self.assertEqual(a.classify_failed_run("t", repo, 9), expected)

    def test_partial_business_execution_is_not_mistaken_for_setup_only_failure(self):
        response = {"total_count": 1, "jobs": [
            {"conclusion": "failure", "steps": [
                {"name": "Set up job", "conclusion": "failure"},
                {"name": "Build unsigned Release APK on Windows",
                 "conclusion": "failure"},
            ]},
        ]}
        with patch.object(a, "api", return_value=response):
            self.assertEqual(
                a.classify_failed_run("t", a.TEST, 9),
                "android_compile_failed_manual_review")

    def test_verified_build_handoff_failure_is_distinct_from_compile_failure(self):
        response = {"total_count": 1, "jobs": [{
            "conclusion": "failure",
            "steps": [
                {"name": "Build unsigned Release APK on Windows", "conclusion": "success"},
                {"name": "Run unit tests, release lint and debug APK build", "conclusion": "success"},
                {"name": "Publish verified Test handoff to PRIVATE Release Assets (not Actions Artifact)",
                 "conclusion": "failure"},
            ],
        }]}
        with patch.object(a, "api", return_value=response):
            self.assertEqual(a.classify_failed_run("token", a.TEST, 99),
                             "android_test_private_handoff_failed_manual_review")

    def test_checkout_failure_not_automatically_called_network_error(self):
        jobs = {"total_count": 1, "jobs": [{
            "conclusion": "failure", "steps": [
                {"name": "Check out private Test source - attempt 3",
                 "conclusion": "failure"}]}]}
        with patch.object(a, "api", return_value=jobs):
            self.assertEqual(a.classify_failed_run("t", a.TEST, 9),
                             "android_test_or_artifact_failed_manual_review")

    def test_cancelled_private_test_run_is_not_misclassified_as_build_failure(self):
        sha = "a" * 40
        item = self.record(a.TEST, a.TEST_WORKFLOW, 9, sha)
        item.update(status="completed", conclusion="cancelled")
        with patch.object(a, "api", return_value=item) as request:
            with self.assertRaisesRegex(a.Hold, "private_workflow_cancelled_manual_review"):
                a.wait_run("private", a.TEST, a.TEST_WORKFLOW, 9, sha)
            # Cancelled runs must never pass to signing or be diagnosed
            # as a genuine compile failure from incomplete step evidence.
            self.assertEqual(request.call_count, 1)

    def test_cancelled_private_signer_run_is_never_success(self):
        sha = "a" * 40
        item = self.record(a.SIGNER, a.SIGN_WORKFLOW, 9, sha)
        item.update(status="completed", conclusion="cancelled")
        with patch.object(a, "api", return_value=item):
            with self.assertRaisesRegex(a.Hold, "private_workflow_cancelled_manual_review"):
                a.wait_run("private", a.SIGNER, a.SIGN_WORKFLOW, 9)

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

    def test_publication_rejects_failed_or_unverified_build(self):
        from scheduler import android_publish as publication
        sha = "a" * 40
        for phase in ("reserved", "test_started", "test_verified",
                      "signer_reserved", "signer_started"):
            with self.subTest(phase=phase):
                state = {"version": 1, "builds": {sha: {
                    "sourceSha": sha, "phase": phase,
                    "testRunId": 9, "signingRunId": 11,
                    "lastFailure": {"stage": "test",
                                    "code": "network_retry_exhausted_same_run_no_publication"}}}}
                with patch.object(publication.build, "json_file",
                                  return_value=(state, "blob")):
                    with self.assertRaisesRegex(a.Hold, "source_not_verified_signed"):
                        publication.signed_request({"GH_TOKEN": "public"},
                                                   sha, "2.3.5", 118, "test log")

    def test_android_signing_billing_secret_is_passed_by_workflow(self):
        from pathlib import Path
        workflow = Path(".github/workflows/android-production.yml").read_text(encoding="utf-8")
        self.assertIn("CHAONPU_BILLING_READ_TOKEN: ${{ secrets.CHAONPU_BILLING_READ_TOKEN }}", workflow)
        self.assertIn("TEST_BILLING_READ_TOKEN: ${{ secrets.TEST_BILLING_READ_TOKEN }}", workflow)

    def test_signer_route_requires_verified_billing_and_ready_windows_runner(self):
        task = {"signingRunnerName": "ziwuxilv-sign-win"}
        account = {"billingSecret": "CHAONPU_BILLING_READ_TOKEN",
                   "runnerSecret": "RUNNER_READ_TOKEN"}
        with patch.object(a, "account_usage", return_value={}):
            with patch.object(a, "hosted_capacity", return_value="unknown"):
                with self.assertRaisesRegex(a.Hold, "signer_billing_read_token_missing"):
                    a.choose_signer_route(task, account, {})
                with self.assertRaisesRegex(a.Hold, "signer_allowance_unverified"):
                    a.choose_signer_route(task, account, {"CHAONPU_BILLING_READ_TOKEN": "present"})
        with patch.object(a, "account_usage", return_value={}):
            with patch.object(a, "hosted_capacity", return_value="insufficient"):
                with patch.object(a, "runner_ready", return_value=False):
                    with self.assertRaisesRegex(a.Hold, "signer_windows_runner_not_verified"):
                        a.choose_signer_route(task, account, {"CHAONPU_BILLING_READ_TOKEN": "present"})
                with patch.object(a, "runner_ready", return_value=True):
                    self.assertEqual(a.choose_signer_route(task, account, {"CHAONPU_BILLING_READ_TOKEN": "present"}),
                                     ("windows", a.SIGN_WORKFLOW))
            with patch.object(a, "hosted_capacity", return_value="available"):
                with patch.object(a, "runner_ready", side_effect=AssertionError("cloud signer must not require Windows")):
                    self.assertEqual(a.choose_signer_route(task, account, {"CHAONPU_BILLING_READ_TOKEN": "present"}),
                                     ("cloud", a.CLOUD_SIGN_WORKFLOW))


class SignerRetryResumeTests(unittest.TestCase):
    def evidence(self):
        rid = 43
        base = dict(id=rid, repository={"full_name": a.SIGNER},
                    path=".github/workflows/" + a.SIGN_WORKFLOW,
                    head_branch="main", head_sha="c" * 40, event="workflow_dispatch",
                    status="completed")
        latest = dict(base, run_attempt=2, conclusion="success")
        prior = dict(base, run_attempt=1, conclusion="failure")
        names = (
            "Download exact successful private Test run handoff",
            "Verify SHA, source archive, tests, lint and release policy",
            "Restore signing material only after private verification",
            "Sign already-tested unsigned APK without executing Test code",
            "Verify certificate, package, version and signed APK digest",
            "Prepare private release candidate metadata (does not publish)",
            "Publish verified signed APK to PRIVATE Release Assets (not Actions Artifact)",
        )
        preflight = "NETWORK_TRANSIENT_PREFLIGHT - verify and export GitHub HTTPS Git route"
        def jobs(attempt):
            steps = [dict(name=preflight, conclusion="failure" if attempt == 1 else "success")]
            steps += [dict(name=n, conclusion="skipped" if attempt == 1 else "success")
                      for n in names]
            # Checkout retries legitimately repeat unrelated step names.
            steps += [dict(name="Refresh verified HTTPS route", conclusion="skipped")] * 2
            return dict(total_count=1, jobs=[dict(
                run_id=rid, run_attempt=attempt, runner_name="ziwuxilv-sign-win",
                status="completed", conclusion="failure" if attempt == 1 else "success",
                steps=steps)])
        return latest, prior, jobs(1), jobs(2)

    def verify(self, evidence, workflow=None):
        latest, prior, first, second = evidence
        responses = [latest, prior, first, second, latest]
        with patch.object(a, "api", side_effect=responses) as request:
            with patch.object(a, "dispatch", side_effect=AssertionError("redispatch")):
                result = a.verify_completed_signer_retry(
                    "sign", workflow or a.SIGN_WORKFLOW, 43, "ziwuxilv-sign-win")
            self.assertTrue(all(not call.kwargs for call in request.call_args_list))
            return result

    def test_accepts_only_proven_preflight_failure_then_success_same_run(self):
        self.assertTrue(self.verify(self.evidence()))
        # The general origin validator remains strict for every other path.
        with self.assertRaisesRegex(a.Hold, "run_origin_mismatch"):
            a.validate_run(self.evidence()[0], a.SIGNER, a.SIGN_WORKFLOW, 43)

    def test_recover_same_signing_run_after_verified_pre_key_release_403(self):
        # First attempt reached private Test Release download, but never accessed
        # any signing material. Only a fully verified successful attempt 2 counts.
        import copy
        rows = list(copy.deepcopy(self.evidence()))
        first = rows[2]["jobs"][0]["steps"]
        first[0]["conclusion"] = "success"  # verified Git preflight
        first[1]["conclusion"] = "failure"  # private Test Release HTTP 403
        self.assertTrue(self.verify(rows))
        # Never permit recovery once keystore restoration could have started.
        dangerous = list(copy.deepcopy(rows))
        dangerous[2]["jobs"][0]["steps"][3]["conclusion"] = "success"
        with self.assertRaisesRegex(a.Hold, "signer_retry_preflight_or_signing_proof_missing"):
            self.verify(dangerous)
        # Evidence of a skipped download without a failing preflight is not enough.
        missing = list(copy.deepcopy(rows))
        missing[2]["jobs"][0]["steps"][1]["conclusion"] = "skipped"
        with self.assertRaises(a.Hold):
            self.verify(missing)
        # Repeated handoff failure on attempt 2 is never accepted.
        second_failure = list(copy.deepcopy(rows))
        second_failure[3]["jobs"][0]["steps"][1]["conclusion"] = "failure"
        with self.assertRaises(a.Hold):
            self.verify(second_failure)

    def test_rejects_key_access_missing_steps_wrong_runner_and_run(self):
        import copy
        for target, field, value in (
            (0, "run_attempt", 3),
            (0, "conclusion", "failure"),
            (0, "event", "push"),
            (0, "head_branch", "other"),
            (1, "head_sha", "d" * 40),
            (1, "conclusion", "success"),
        ):
            with self.subTest(target=target, field=field):
                rows = list(copy.deepcopy(self.evidence()))
                rows[target][field] = value
                with self.assertRaises(a.Hold):
                    self.verify(rows)
        for attempt in (2, 3):
            for field, value in (("runner_name", "other"), ("run_id", 99),
                                 ("run_attempt", 3), ("status", "in_progress")):
                with self.subTest(attempt=attempt, field=field):
                    rows = list(copy.deepcopy(self.evidence()))
                    rows[attempt]["jobs"][0][field] = value
                    with self.assertRaises(a.Hold):
                        self.verify(rows)
            rows = list(copy.deepcopy(self.evidence()))
            rows[attempt]["jobs"][0]["steps"][3]["conclusion"] = "success" if attempt == 2 else "skipped"
            with self.assertRaises(a.Hold):
                self.verify(rows)
        rows = list(copy.deepcopy(self.evidence()))
        rows[2]["jobs"][0]["steps"].pop(3)
        with self.assertRaises(a.Hold):
            self.verify(rows)
        rows = list(copy.deepcopy(self.evidence()))
        rows[3]["jobs"][0]["steps"].append(rows[3]["jobs"][0]["steps"][1])
        with self.assertRaises(a.Hold):
            self.verify(rows)

    def test_rejects_cloud_and_evidence_changed_during_reads(self):
        with self.assertRaises(a.Hold):
            self.verify(self.evidence(), a.CLOUD_SIGN_WORKFLOW)
        latest, prior, first, second = self.evidence()
        changed = dict(latest, run_attempt=3)
        with patch.object(a, "api", side_effect=[latest, prior, first, second, changed]):
            with self.assertRaisesRegex(a.Hold, "changed_during_verification"):
                a.verify_completed_signer_retry("s", a.SIGN_WORKFLOW, 43, "ziwuxilv-sign-win")

    def test_first_attempt_uses_existing_wait_path(self):
        latest = dict(self.evidence()[0], run_attempt=1)
        with patch.object(a, "api", return_value=latest) as request:
            self.assertFalse(a.verify_completed_signer_retry(
                "s", a.SIGN_WORKFLOW, 43, "ziwuxilv-sign-win"))
            request.assert_called_once()

    def test_explicit_resume_rechecks_original_test_and_never_dispatches(self):
        sha = "a" * 40
        task = dict(repo=a.TEST, signingRepo=a.SIGNER,
                    windowsWorkflow=a.TEST_WORKFLOW, signingWindowsWorkflow=a.SIGN_WORKFLOW,
                    runnerName="ziwuxilv-test-win", signingRunnerName="ziwuxilv-sign-win")
        cfg = dict(accounts={"anwpustudent-lab": {}, "chaonpu": {}},
                   tasks={"android": task})
        row = dict(sourceSha=sha, phase="signer_started", route="windows",
                   signingRoute="windows", testRunId=42, signingRunId=43, liveSmoke=True)
        def document(token, path):
            return (cfg if path == a.CONFIG_PATH else
                    dict(version=1, builds={sha: row})), "blob"
        with patch.object(a, "json_file", side_effect=document), \
             patch.object(a, "dispatch", side_effect=AssertionError("redispatch")), \
             patch.object(a, "wait_recorded") as wait, \
             patch.object(a, "verify_completed_signer_retry", return_value=True) as retry, \
             patch.object(a, "verify_execution_runner") as runner, \
             patch.object(a, "verify_artifact") as artifact, \
             patch.object(a, "verify_live_execution") as live, \
             patch.object(a, "put_state") as saved, \
             patch.object(a, "record_public"):
            a.run(dict(GH_TOKEN="p", TEST_ACTIONS_READ_TOKEN="t",
                       CHAONPU_ACTIONS_READ_TOKEN="s"),
                  execute=True, resume=True, requested_sha=sha)
            wait.assert_called_once_with("p", sha, "test", "t", a.TEST,
                                         a.TEST_WORKFLOW, 42, sha)
            live.assert_called_once_with("t", 42)
            retry.assert_called_once_with("s", a.SIGN_WORKFLOW, 43, "ziwuxilv-sign-win")
            self.assertEqual(runner.call_count, 2)
            self.assertEqual(artifact.call_count, 2)
            saved.assert_called_once_with("p", sha, "signed_verified")


class WindowsTestHandoffRerunTests(unittest.TestCase):
    """No new dispatch: accept only the original isolated private handoff failure."""

    sha = "a" * 40
    rid = 812
    runner = "ziwuxilv-test-win"
    publish = "Publish verified Test handoff to PRIVATE Release Assets (not Actions Artifact)"
    required = (
        "Verify exact checked out commit",
        "Build unsigned Release APK on Windows",
        "Verify release version and Test source guardrails",
        "Run unit tests, release lint and debug APK build",
        "Verify release and debug notification manifests",
        "Prepare private verified Windows signing handoff",
        "Verify private Release transport policy",
        publish,
    )

    def run_record(self, attempt, conclusion):
        return dict(id=self.rid, repository={"full_name": a.TEST},
                    head_sha=self.sha, head_branch="main",
                    event="workflow_dispatch", run_attempt=attempt,
                    path=".github/workflows/" + a.TEST_WORKFLOW,
                    status="completed", conclusion=conclusion)

    def jobs(self, attempt, conclusion):
        steps = [{"name": name,
                  "conclusion": "failure" if name == self.publish and attempt == 1
                  else "success"} for name in self.required]
        return {"total_count": 2, "jobs": [
            {"name": "test", "run_id": self.rid, "run_attempt": attempt,
             "runner_name": self.runner, "status": "completed",
             "conclusion": conclusion, "steps": steps},
            {"name": "Verify private Release handoff idempotence (no APK)",
             "status": "completed", "conclusion": "skipped", "steps": []},
        ]}

    def evidence(self):
        latest = self.run_record(2, "success")
        return [latest, self.run_record(1, "failure"),
                self.jobs(1, "failure"), self.jobs(2, "success"),
                dict(latest)]

    def verify(self, evidence=None):
        with patch.object(a, "api", side_effect=evidence or self.evidence()) as api, \
             patch.object(a, "dispatch", side_effect=AssertionError("redispatch")):
            result = a.verify_completed_test_handoff_retry(
                "test_token", a.TEST_WORKFLOW, self.rid, self.sha, self.runner)
        return result, api.call_count

    def test_second_attempt_full_handoff_verified_without_dispatch(self):
        result, calls = self.verify()
        self.assertTrue(result)
        self.assertEqual(calls, 5)

    def test_wrong_origin_or_attempt_never_authorizes(self):
        import copy
        for index, key, value in (
                (0, "run_attempt", 3),
                (0, "conclusion", "failure"),
                (0, "event", "push"),
                (0, "head_sha", "b" * 40),
                (1, "conclusion", "success"),
                (1, "head_branch", "foreign")):
            rows = copy.deepcopy(self.evidence())
            rows[index][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(a.Hold):
                self.verify(rows)

    def test_other_failures_missing_steps_or_wrong_runner_are_rejected(self):
        import copy
        changes = [
            (2, "runner_name", "untrusted-runner"),
            (3, "run_attempt", 3),
            (3, "status", "in_progress"),
        ]
        for index, key, value in changes:
            rows = copy.deepcopy(self.evidence())
            rows[index]["jobs"][0][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(a.Hold):
                self.verify(rows)
        rows = copy.deepcopy(self.evidence())
        rows[2]["jobs"][0]["steps"][1]["conclusion"] = "failure"
        with self.assertRaises(a.Hold):
            self.verify(rows)
        rows = copy.deepcopy(self.evidence())
        rows[3]["jobs"][0]["steps"].pop()
        with self.assertRaises(a.Hold):
            self.verify(rows)
        rows = copy.deepcopy(self.evidence())
        rows[3]["jobs"][1]["conclusion"] = "success"
        with self.assertRaises(a.Hold):
            self.verify(rows)
        rows = copy.deepcopy(self.evidence())
        rows[4]["run_attempt"] = 3
        with self.assertRaisesRegex(a.Hold, "changed_during_verification"):
            self.verify(rows)

    def test_recorded_proof_must_match_exact_same_run(self):
        record = dict(route="windows", liveSmoke=False, testRunId=self.rid,
                      verifiedTestRetry={
                          "testRunId": self.rid, "originalAttempt": 1,
                          "successfulAttempt": 2,
                          "originalFailure": "android_test_private_handoff_failed_manual_review",
                          "method": "same_run_isolated_private_handoff_retry"})
        self.assertTrue(a.registered_test_handoff_retry(record, self.rid))
        with self.assertRaises(a.Hold):
            a.registered_test_handoff_retry(record, 999)
        record["lastFailure"] = {"code": "stale"}
        with self.assertRaises(a.Hold):
            a.registered_test_handoff_retry(record, self.rid)

    def test_test_verified_state_preserves_attempt_one_failure_history(self):
        row = dict(sourceSha=self.sha, phase="test_started", route="windows",
                   liveSmoke=False, testRunId=self.rid,
                   lastFailure=dict(stage="test", runId=self.rid,
                                    code="android_test_private_handoff_failed_manual_review"))
        state = dict(version=1, builds={self.sha: row})
        with patch.object(a, "json_file", return_value=(state, "blob")), \
             patch.object(a, "api", return_value={"commit": {"sha": "abc"}}):
            a.put_state("public", self.sha, "test_verified",
                        verified_test_retry=True)
        self.assertEqual(row["phase"], "test_verified")
        self.assertNotIn("lastFailure", row)
        self.assertTrue(a.registered_test_handoff_retry(row, self.rid))

    def test_resume_signs_only_after_attempt_and_asset_are_reverified(self):
        cfg = dict(accounts={"anwpustudent-lab": {}, "chaonpu": {}},
                   tasks={"android": {
                       "repo": a.TEST, "signingRepo": a.SIGNER,
                       "windowsWorkflow": a.TEST_WORKFLOW,
                       "signingWindowsWorkflow": a.SIGN_WORKFLOW,
                       "runnerName": self.runner,
                       "signingRunnerName": "ziwuxilv-sign-win"}})
        row = dict(sourceSha=self.sha, phase="test_started", route="windows",
                   liveSmoke=False, testRunId=self.rid,
                   lastFailure=dict(stage="test", runId=self.rid,
                                    code="android_test_private_handoff_failed_manual_review"))
        def documents(token, path):
            if path == a.CONFIG_PATH:
                return cfg, "config"
            return dict(version=1, builds={self.sha: row}), "state"
        with patch.object(a, "json_file", side_effect=documents), \
             patch.object(a, "verify_completed_test_handoff_retry", return_value=True) as verified, \
             patch.object(a, "wait_recorded") as wait, \
             patch.object(a, "verify_execution_runner"), \
             patch.object(a, "verify_artifact"), \
             patch.object(a, "check_idle"), \
             patch.object(a, "choose_signer_route", return_value=("cloud", a.CLOUD_SIGN_WORKFLOW)), \
             patch.object(a, "dispatch", return_value=930) as dispatch, \
             patch.object(a, "verify_hosted_execution"), \
             patch.object(a, "put_state") as state, \
             patch.object(a, "record_public"):
            a.run({"GH_TOKEN": "p", "TEST_ACTIONS_READ_TOKEN": "t",
                   "CHAONPU_ACTIONS_READ_TOKEN": "s"},
                  execute=True, resume=True, requested_sha=self.sha)
        self.assertEqual(verified.call_count, 2)
        self.assertEqual(wait.call_count, 1)
        self.assertEqual(wait.call_args.args[2], "signer")
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(dispatch.call_args.args[1], a.SIGNER)
        self.assertEqual(state.call_args_list[0].args,
                         ("p", self.sha, "test_verified"))
        self.assertIs(state.call_args_list[0].kwargs["verified_test_retry"], True)



class SignerPrekeyRecoveryTests(unittest.TestCase):
    sha = "a" * 40
    run_id = 9876
    test_id = 4567
    test_source_proof = dict(
        testRunId=4567, originalAttempt=1, successfulAttempt=2,
        originalFailure="android_test_private_handoff_failed_manual_review",
        method="same_run_isolated_private_handoff_retry")

    def signer_evidence(self):
        run = dict(
            id=self.run_id, repository={"full_name": a.SIGNER},
            event="workflow_dispatch", head_branch="main", run_attempt=1,
            path=".github/workflows/" + a.CLOUD_SIGN_WORKFLOW,
            status="completed", conclusion="failure", head_sha="b" * 40)
        good = [
            "Checkout private signing policy only",
            "Verify immutable private signing policy SHA",
            "Run actions/setup-java@v4",
            "Verify signing policy tests and install Android build-tools",
        ]
        failed = "Verify exact Test run and download only approved private handoff"
        guarded = [
            "Independently verify exact source archive, tests, lint and unsigned APK",
            "Restore signing key in trusted ephemeral signer only",
            "Sign verified unsigned APK without executing Test source",
            "Verify exact production certificate, APK package/version and SHA",
            "Build verified private release candidate metadata (no publication)",
            "Verify private Release transport policy",
            "Publish verified signed APK to PRIVATE Release Assets (not Actions Artifact)",
        ]
        steps = ([dict(name=n, conclusion="success") for n in good]
                 + [dict(name=failed, conclusion="failure")]
                 + [dict(name=n, conclusion="skipped") for n in guarded])
        job = dict(name="Verify Test handoff and sign exact APK on trusted hosted runner",
                   run_id=self.run_id, run_attempt=1,
                   labels=["ubuntu-24.04"], status="completed",
                   conclusion="failure", steps=steps)
        return run, dict(total_count=1, jobs=[job]), dict(run)

    def row(self):
        return dict(
            sourceSha=self.sha, phase="signer_started", route="windows",
            signingRoute="cloud", liveSmoke=False,
            testRunId=self.test_id, signingRunId=self.run_id,
            verifiedTestRetry=dict(self.test_source_proof),
            lastFailure=dict(stage="signer", runId=self.run_id,
                             code="android_signer_setup_or_handoff_failed_manual_review"))

    def test_only_before_key_failure_is_eligible(self):
        with patch.object(a, "api", side_effect=self.signer_evidence()) as api:
            self.assertTrue(a.verify_cloud_signer_prekey_failure("s", self.run_id))
        self.assertEqual(api.call_count, 3)

    def test_signing_or_missing_proof_never_retries(self):
        import copy
        for i, mutation in enumerate((
            lambda e: e[0].update(run_attempt=2),
            lambda e: e[0].update(event="push"),
            lambda e: e[1]["jobs"][0].update(labels=["self-hosted"]),
            lambda e: e[1]["jobs"][0]["steps"][5].update(conclusion="success"),
            lambda e: e[1]["jobs"][0]["steps"][7].update(conclusion="success"),
            lambda e: e[1]["jobs"][0]["steps"].pop(5),
            lambda e: e[2].update(conclusion="success"),
        )):
            evidence = copy.deepcopy(self.signer_evidence())
            mutation(evidence)
            with self.subTest(i=i), self.assertRaises(a.Hold), \
                 patch.object(a, "api", side_effect=evidence):
                a.verify_cloud_signer_prekey_failure("s", self.run_id)

    def test_state_reservation_is_one_time_and_preserves_failure(self):
        row = self.row()
        data = dict(version=1, builds={self.sha: row})
        with patch.object(a, "json_file", return_value=(data, "state_blob")), \
             patch.object(a, "api", return_value={"commit": {"sha": "xyz"}}):
            a.reserve_signer_prekey_recovery("pub", self.sha, dict(row))
        self.assertEqual(row["phase"], "signer_retry_reserved")
        self.assertNotIn("lastFailure", row)
        self.assertEqual(row["signingPrekeyRecovery"]["oldSigningRunId"],
                         self.run_id)
        self.assertEqual(row["signingPrekeyRecovery"]["originalFailure"]["stage"],
                         "signer")
        with patch.object(a, "json_file", return_value=(data, "state_blob")), \
             patch.object(a, "api", side_effect=AssertionError("write duplicate")):
            with self.assertRaises(a.Hold):
                a.reserve_signer_prekey_recovery("pub", self.sha, dict(row))

    def test_state_transition_only_rebinds_reserved_new_signer(self):
        row = self.row()
        row["phase"] = "signer_retry_reserved"
        row["signingPrekeyRecovery"] = {
            "oldSigningRunId": self.run_id, "recoveryCount": 1,
            "originalFailure": {"stage": "signer"}}
        state = dict(version=1, builds={self.sha: row})
        with patch.object(a, "json_file", return_value=(state, "state_blob")), \
             patch.object(a, "api", return_value={"commit": {"sha": "xyz"}}):
            with self.assertRaises(a.Hold):
                a.put_state("pub", self.sha, "signer_started",
                            signing_run_id=self.run_id)
            a.put_state("pub", self.sha, "signer_started",
                        signing_run_id=self.run_id + 1)
        self.assertEqual(row["signingRunId"], self.run_id + 1)

    def test_explicit_resume_never_rebuilds_test_and_dispatches_one_signer(self):
        row = self.row()
        task = dict(repo=a.TEST, signingRepo=a.SIGNER,
                    windowsWorkflow=a.TEST_WORKFLOW,
                    signingWindowsWorkflow=a.SIGN_WORKFLOW,
                    runnerName="ziwuxilv-test-win",
                    signingRunnerName="ziwuxilv-sign-win")
        cfg = dict(accounts={"anwpustudent-lab": {}, "chaonpu": {}},
                   tasks={"android": task})
        def json_doc(token, path):
            return (cfg if path == a.CONFIG_PATH else
                    dict(version=1, builds={self.sha: row})), "blob"
        with patch.object(a, "json_file", side_effect=json_doc), \
             patch.object(a, "verify_completed_test_handoff_retry", return_value=True) as test, \
             patch.object(a, "verify_cloud_signer_prekey_failure", return_value=True) as prekey, \
             patch.object(a, "verify_execution_runner"), \
             patch.object(a, "verify_artifact"), \
             patch.object(a, "check_idle"), \
             patch.object(a, "choose_signer_route", return_value=("cloud", a.CLOUD_SIGN_WORKFLOW)), \
             patch.object(a, "reserve_signer_prekey_recovery") as reserve, \
             patch.object(a, "dispatch", return_value=self.run_id + 1) as dispatch, \
             patch.object(a, "wait_recorded") as wait, \
             patch.object(a, "verify_hosted_execution"), \
             patch.object(a, "put_state") as state, \
             patch.object(a, "record_public"):
            a.run(dict(GH_TOKEN="p", TEST_ACTIONS_READ_TOKEN="t",
                       CHAONPU_ACTIONS_READ_TOKEN="s"),
                  execute=True, resume=True, requested_sha=self.sha,
                  recover_signer_prekey=True)
        test.assert_called_once()
        prekey.assert_called_once()
        reserve.assert_called_once()
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[1:3],
                         (a.SIGNER, a.CLOUD_SIGN_WORKFLOW))
        self.assertEqual(wait.call_count, 1)
        self.assertEqual(wait.call_args.args[2], "signer")
        state.assert_any_call("p", self.sha, "signer_started",
                              signing_run_id=self.run_id + 1)

    def test_recovery_flag_requires_explicit_execute_resume(self):
        for execute, resume, source in (
            (False, True, self.sha), (True, False, self.sha),
            (True, True, "")):
            with self.assertRaises(a.Hold), \
                 patch.object(a, "dispatch", side_effect=AssertionError("POST")):
                a.run({}, execute=execute, resume=resume,
                      requested_sha=source, recover_signer_prekey=True)



class RunnerBusyWaitTests(unittest.TestCase):
    def ready(self, busy):
        return dict(read="readable", status="online", busy=busy,
                    labelsVerified=True)

    def test_busy_then_ready_waits_without_reserving_or_dispatching(self):
        sequence = [self.ready(True), self.ready(True), self.ready(False)]
        with patch.object(a, "runner_for_repo", side_effect=sequence) as probe, \
             patch.object(a.time, "sleep") as sleep, \
             patch.object(a, "record_public") as report, \
             patch.object(a, "reserve", side_effect=AssertionError("reserve")), \
             patch.object(a, "dispatch", side_effect=AssertionError("dispatch")):
            self.assertTrue(a.runner_ready(
                a.TEST, "ziwuxilv-test-win", {}, {},
                wait_if_busy=True, busy_wait_seconds=100))
        self.assertEqual(probe.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(report.call_args.kwargs["reason"], "ready")
        self.assertEqual(report.call_args_list[0].args[1], "runner_busy_bounded_wait")

    def test_offline_unknown_or_labels_mismatch_never_waits(self):
        for bad in (
                dict(read="readable", status="offline", busy=True, labelsVerified=True),
                dict(read="missing", status="online", busy=True, labelsVerified=True),
                dict(read="readable", status="online", busy=None, labelsVerified=True),
                dict(read="readable", status="online", busy=True, labelsVerified=False)):
            with self.subTest(bad=bad), \
                 patch.object(a, "runner_for_repo", return_value=bad) as probe, \
                 patch.object(a.time, "sleep") as sleep, \
                 patch.object(a, "record_public"):
                self.assertFalse(a.runner_ready(
                    a.TEST, "ziwuxilv-test-win", {}, {},
                    wait_if_busy=True, busy_wait_seconds=2700))
                probe.assert_called_once()
                sleep.assert_not_called()

    def test_permanently_busy_times_out_without_any_side_effect(self):
        with patch.object(a, "runner_for_repo", return_value=self.ready(True)) as probe, \
             patch.object(a.time, "sleep") as sleep, \
             patch.object(a, "record_public"), \
             patch.object(a, "reserve", side_effect=AssertionError("reserve")), \
             patch.object(a, "dispatch", side_effect=AssertionError("dispatch")):
            with self.assertRaisesRegex(a.Hold, "private_test_runner_busy_timeout_no_dispatch"):
                a.runner_ready(a.TEST, "ziwuxilv-test-win", {}, {},
                               wait_if_busy=True, busy_wait_seconds=0)
            probe.assert_called_once()
            sleep.assert_not_called()
        for timeout in (-1, 2701, 0.5):
            with self.subTest(timeout=timeout), self.assertRaises(a.Hold):
                a.runner_ready(a.TEST, "ziwuxilv-test-win", {}, {},
                               wait_if_busy=True, busy_wait_seconds=timeout)

    def test_active_manual_run_appearing_after_wait_blocks_reservation(self):
        sha = "a" * 40
        config = dict(accounts={"anwpustudent-lab": {}, "chaonpu": {}},
                      tasks={"android": dict(
                          repo=a.TEST, signingRepo=a.SIGNER,
                          windowsWorkflow=a.TEST_WORKFLOW,
                          signingWindowsWorkflow=a.SIGN_WORKFLOW,
                          runnerName="ziwuxilv-test-win",
                          signingRunnerName="ziwuxilv-sign-win")})
        def json_doc(token, path):
            return (config if path == a.CONFIG_PATH else
                    dict(version=1, builds={})), "blob"
        with patch.object(a, "json_file", side_effect=json_doc), \
             patch.object(a, "current_source", return_value=sha), \
             patch.object(a, "choose_route", return_value=dict(
                 route="windows", reason="verified_fixture")), \
             patch.object(a, "check_idle", side_effect=[
                 None, None, None, None,
                 a.Hold("active_or_unknown_private_workflow_no_dispatch")
             ]) as idle, \
             patch.object(a, "choose_signer_route", return_value=(
                 "cloud", a.CLOUD_SIGN_WORKFLOW)), \
             patch.object(a, "runner_ready", return_value=True) as ready, \
             patch.object(a, "reserve", side_effect=AssertionError("reserve")), \
             patch.object(a, "dispatch", side_effect=AssertionError("dispatch")):
            with self.assertRaisesRegex(a.Hold, "active_or_unknown_private_workflow"):
                a.run({"GH_TOKEN": "p", "TEST_ACTIONS_READ_TOKEN": "t"},
                      execute=True, requested_sha=sha)
            self.assertEqual(idle.call_count, 5)
            self.assertTrue(ready.call_args.kwargs.get("wait_if_busy"))


if __name__ == "__main__":
    unittest.main()

