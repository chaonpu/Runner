"""Explicit, bounded full rebuilds retain failed evidence and every signing gate."""
import base64
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from scheduler import android_production as a


class Tests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.entry = dict(sourceSha=self.sha, phase="test_started", route="windows",
                          liveSmoke=True, testRunId=42,
                          lastFailure=dict(stage="test", runId=42,
                              code="android_live_validation_failed_or_skipped"))
        self.original = dict(id=42, repository={"full_name": a.TEST},
                             path=".github/workflows/" + a.TEST_WORKFLOW,
                             head_branch="main", head_sha=self.sha,
                             event="workflow_dispatch", run_attempt=1,
                             status="completed", conclusion="failure")
        good = ["Verify exact checked out commit", "Verify release version and Test source guardrails",
                "Build unsigned Release APK on Windows", "Test live chart evidence verifier",
                "Clear prior test reports before validation", "Verify actual public calendar network execution"]
        bad = ["Run unit tests, release lint and debug APK build", "Verify live chart execution evidence"]
        skipped = ["Verify release and debug notification manifests",
                   "Prepare private verified Windows signing handoff",
                   "Verify private Release transport policy",
                   "Publish verified Test handoff to PRIVATE Release Assets (not Actions Artifact)"]
        self.job = dict(name="test", status="completed", conclusion="failure",
                        runner_name="ziwuxilv-test-win", steps=[
                            {"name": name, "conclusion": result}
                            for names, result in ((good, "success"), (bad, "failure"), (skipped, "skipped"))
                            for name in names])
        self.jobs = dict(total_count=2, jobs=[self.job, dict(
            name="Verify real-source chart continuity without artifacts",
            status="completed", conclusion="skipped")])
        self.artifacts = dict(total_count=0, artifacts=[])
        self.state = dict(version=1, builds={self.sha: copy.deepcopy(self.entry)})
        self.saved = None
        self.events = []
        self.save_response = {"commit": {"sha": "saved"}}
        self.confirmation_mismatch = False

    def api(self, token, repo, path, **kwargs):
        self.events.append(("api", path))
        if repo == a.TEST:
            if path == "/actions/runs/42": return copy.deepcopy(self.original)
            if path == "/actions/runs/42/jobs?per_page=100": return copy.deepcopy(self.jobs)
            if path == "/actions/runs/42/artifacts?per_page=100": return copy.deepcopy(self.artifacts)
        if repo == a.RUNNER and path == "/contents/" + a.STATE_PATH:
            self.assertEqual(kwargs["method"], "PUT")
            self.assertEqual(kwargs["payload"]["sha"], "state-blob")
            self.saved = json.loads(base64.b64decode(kwargs["payload"]["content"]))
            self.events.append(("reserved", self.saved))
            return self.save_response
        self.fail((repo, path, kwargs))

    def document(self, token, path):
        self.assertEqual(path, a.STATE_PATH)
        doc = self.saved if self.saved is not None else self.state
        value = copy.deepcopy(doc)
        if self.saved is not None and self.confirmation_mismatch: value["changed"] = True
        return value, "state-blob"

    def exercise(self, *, source_error=None, fresh=43, dispatch_error=None):
        def dispatch(*args):
            self.assertIsNotNone(self.saved, "dispatch before durable reservation")
            self.events.append(("dispatch", args))
            if dispatch_error: raise dispatch_error
            return fresh
        with ExitStack() as stack:
            stack.enter_context(patch.object(a, "api", side_effect=self.api))
            stack.enter_context(patch.object(a, "json_file", side_effect=self.document))
            stack.enter_context(patch.object(a, "current_source", return_value=self.sha,
                                             side_effect=source_error))
            self.idle = stack.enter_context(patch.object(a, "check_idle"))
            self.ready = stack.enter_context(patch.object(a, "runner_ready", return_value=True))
            self.dispatch = stack.enter_context(patch.object(a, "dispatch", side_effect=dispatch))
            self.put = stack.enter_context(patch.object(a, "put_state"))
            self.report = stack.enter_context(patch.object(a, "record_public"))
            return a.recover_reviewed_live_test("pub", "test", self.sha, self.entry,
                    {}, {}, {}, {"runnerName": "ziwuxilv-test-win"})

    def rejected(self, reason=None, **kwargs):
        with self.assertRaises(a.Hold) as result: self.exercise(**kwargs)
        if reason: self.assertIn(reason, str(result.exception))
        self.dispatch.assert_not_called()
        self.put.assert_not_called()

    def test_reserves_before_post_retains_failure_and_forces_full_live_build(self):
        self.assertEqual(self.exercise(), 43)
        row = self.saved["builds"][self.sha]
        self.assertEqual(row["phase"], "test_retry_reserved")
        self.assertEqual(row["testRunId"], 42)
        self.assertEqual(row["failedTestRunIds"], [42])
        self.assertEqual(row["testLiveRetries"], 1)
        self.assertEqual(row["testLiveRecovery"]["reviewedFailure"], self.entry["lastFailure"])
        self.assertEqual(row["lastFailure"], self.entry["lastFailure"])
        self.dispatch.assert_called_once_with("test", a.TEST, a.TEST_WORKFLOW, a.test_inputs(True))
        self.put.assert_called_once_with("pub", self.sha, "test_started", test_run_id=43)
        self.assertEqual(self.idle.call_count, 4)
        self.assertNotIn("signingRunId", row)

    def test_manual_sha_fallback_still_uses_pinned_original_and_fresh_run(self):
        self.assertEqual(self.exercise(source_error=a.Hold(
            "test_main_contents_read_permission_missing")), 43)

    def test_recovery_flag_requires_execute_and_resume_even_in_preview(self):
        for execute, resume in ((False, False), (True, False), (False, True)):
            with self.subTest(execute=execute, resume=resume):
                with patch.object(a, "api", side_effect=AssertionError("unexpected network")):
                    with self.assertRaisesRegex(a.Hold, "explicit_execute_resume"):
                        a.run({}, execute=execute, resume=resume, recover_live_validation=True)

    def test_original_entry_and_live_option_are_mandatory(self):
        for field, bad in (("sourceSha", "b"*40), ("phase", "test_verified"),
                           ("route", "hosted"), ("liveSmoke", False)):
            with self.subTest(field=field):
                original = self.entry[field]; self.entry[field] = bad
                self.rejected("entry_invalid"); self.entry[field] = original

    def test_signing_or_previous_recovery_blocks_replacement(self):
        for field, value in (("signingRunId", 100), ("signingRoute", "windows"),
                             ("testRetries", 1), ("testLiveRetries", 1),
                             ("testLiveRetries", True), ("failedTestRunIds", [40]),
                             ("testLiveRecovery", {"originalTestRunId": 40})):
            with self.subTest(field=field):
                self.entry[field] = value; self.rejected(); del self.entry[field]

    def test_failure_record_must_bind_original_live_validation_failure(self):
        for field, value in (("stage", "signer"), ("runId", 41),
                             ("code", "android_tests_or_lint_failed_manual_review")):
            with self.subTest(field=field):
                original = self.entry["lastFailure"][field]
                self.entry["lastFailure"][field] = value; self.rejected("failure_missing")
                self.entry["lastFailure"][field] = original

    def test_changed_source_rerun_or_nonfailed_original_cannot_recover(self):
        for field, value in (("head_sha", "b"*40), ("run_attempt", 2), ("event", "push"),
                             ("status", "in_progress"), ("conclusion", "success")):
            with self.subTest(field=field):
                original = self.original[field]; self.original[field] = value
                self.rejected(); self.original[field] = original

    def test_main_change_is_a_hard_stop(self):
        self.rejected("source_no_longer_main", source_error=a.Hold(
            "live_recovery_source_no_longer_main"))

    def test_other_source_api_errors_do_not_dispatch(self):
        self.rejected(source_error=a.Hold("github_network_or_schema_unknown_no_retry"))

    def test_job_count_runner_and_companion_are_strict(self):
        self.jobs["total_count"] = 3; self.rejected("job_proof"); self.jobs["total_count"] = 2
        self.job["runner_name"] = "untrusted"; self.rejected("execution_mismatch")
        self.job["runner_name"] = "ziwuxilv-test-win"
        self.jobs["jobs"][1]["conclusion"] = "success"; self.rejected("execution_mismatch")

    def test_missing_duplicate_or_failed_critical_steps_block_recovery(self):
        steps = copy.deepcopy(self.job["steps"])
        self.job["steps"].append(copy.deepcopy(steps[0])); self.rejected("duplicate_steps")
        self.job["steps"] = copy.deepcopy(steps[1:]); self.rejected("boundary_unverified")
        self.job["steps"] = copy.deepcopy(steps)
        self.job["steps"][0]["conclusion"] = "failure"; self.rejected("boundary_unverified")

    def test_other_failed_step_is_not_treated_as_reviewed_live_failure(self):
        self.job["steps"].append({"name": "Unexpected error", "conclusion": "failure"})
        self.rejected("boundary_unverified")

    def test_started_handoff_or_existing_artifact_blocks_recovery(self):
        step = self.job["steps"][-1]; step["conclusion"] = "success"
        self.rejected("handoff_already_started"); step["conclusion"] = "skipped"
        self.artifacts = dict(total_count=1, artifacts=[{"id": 1}])
        self.rejected("artifacts_exist")

    def test_changed_state_cannot_be_reserved(self):
        self.state["builds"][self.sha]["testRunId"] = 41
        self.rejected("record_changed")

    def test_uncertain_reservation_is_not_followed_by_dispatch(self):
        self.save_response = {}
        self.rejected("reservation_uncertain")

    def test_unconfirmed_reservation_is_not_followed_by_dispatch(self):
        self.confirmation_mismatch = True
        self.rejected("reservation_unconfirmed")

    def test_uncertain_post_keeps_reserved_state_and_cannot_repeat(self):
        with self.assertRaisesRegex(a.Hold, "unknown_test_dispatch"):
            self.exercise(dispatch_error=a.Hold("unknown_test_dispatch"))
        self.assertEqual(self.saved["builds"][self.sha]["phase"], "test_retry_reserved")
        self.dispatch.assert_called_once(); self.put.assert_not_called()
        self.entry = copy.deepcopy(self.saved["builds"][self.sha]); self.saved = None
        self.rejected("entry_invalid")

    def test_old_run_id_from_dispatch_cannot_replace_the_binding(self):
        with self.assertRaisesRegex(a.Hold, "new_run_id_not_distinct"): self.exercise(fresh=42)
        self.put.assert_not_called()

    def integrated_run(self, *, recover=True, fail_live=False):
        task = dict(repo=a.TEST, signingRepo=a.SIGNER, windowsWorkflow=a.TEST_WORKFLOW,
                    signingWindowsWorkflow=a.SIGN_WORKFLOW, runnerName="ziwuxilv-test-win",
                    signingRunnerName="ziwuxilv-sign-win")
        config = dict(accounts={"anwpustudent-lab": {}, "chaonpu": {}}, tasks={"android": task})
        row = copy.deepcopy(self.entry)
        def document(token, path):
            return (config if path == a.CONFIG_PATH else
                    dict(version=1, builds={self.sha: copy.deepcopy(row)})), "blob"
        def rebuild(*args):
            row["testRunId"] = 43
            return 43
        with ExitStack() as stack:
            stack.enter_context(patch.object(a, "json_file", side_effect=document))
            self.rebuild = stack.enter_context(patch.object(a, "recover_reviewed_live_test", side_effect=rebuild))
            self.wait = stack.enter_context(patch.object(a, "wait_recorded"))
            self.sign = stack.enter_context(patch.object(a, "dispatch", return_value=44))
            self.live = stack.enter_context(patch.object(a, "verify_live_execution",
                side_effect=a.Hold("new_live_proof_failed") if fail_live else None))
            self.artifact = stack.enter_context(patch.object(a, "verify_artifact"))
            self.state_write = stack.enter_context(patch.object(a, "put_state"))
            for name in ("record_public", "verify_execution_runner", "check_idle"):
                stack.enter_context(patch.object(a, name))
            stack.enter_context(patch.object(a, "choose_signer_route", return_value=("windows", a.SIGN_WORKFLOW)))
            stack.enter_context(patch.object(a, "verify_completed_signer_retry", return_value=False))
            a.run(dict(GH_TOKEN="pub", TEST_ACTIONS_READ_TOKEN="test", CHAONPU_ACTIONS_READ_TOKEN="sign"),
                  execute=True, resume=True, requested_sha=self.sha,
                  live_smoke=False, recover_live_validation=recover)

    def test_integration_revalidates_only_new_run_and_keeps_live_enabled(self):
        self.integrated_run()
        self.rebuild.assert_called_once()
        test_waits = [call for call in self.wait.call_args_list if call.args[2] == "test"]
        self.assertTrue(test_waits)
        self.assertTrue(all(call.args[6] == 43 for call in test_waits))
        self.assertTrue(all(call.args == ("test", 43) for call in self.live.call_args_list))
        self.sign.assert_called_once_with("sign", a.SIGNER, a.SIGN_WORKFLOW,
                                         {"test_run_id": "43", "source_commit": self.sha})
        self.assertEqual(self.state_write.call_args.args, ("pub", self.sha, "signed_verified"))

    def test_integration_failed_replacement_live_proof_never_enters_signing(self):
        with self.assertRaisesRegex(a.Hold, "new_live_proof_failed"):
            self.integrated_run(fail_live=True)
        self.sign.assert_not_called()
        self.state_write.assert_not_called()

    def test_normal_resume_does_not_request_replacement(self):
        self.integrated_run(recover=False)
        self.rebuild.assert_not_called()
        self.assertTrue(all(call.args[1] == 42 for call in self.live.call_args_list))


class StateReadTests(unittest.TestCase):
    def head(self, sha):
        return dict(ref="refs/heads/main", object=dict(type="commit", sha=sha))

    def file(self, value):
        return dict(type="file", sha="c"*40, encoding="base64",
                    content=base64.b64encode(json.dumps(value).encode()).decode())

    def test_reads_immutable_commit_instead_of_cached_branch_contents(self):
        with patch.object(a, "api", side_effect=[self.head("a"*40), self.file({"new": True})]) as api:
            self.assertEqual(a.json_file("token", a.STATE_PATH), ({"new": True}, "c"*40))
        self.assertEqual(api.call_args_list[0].args, ("token", a.RUNNER, "/git/ref/heads/main"))
        self.assertEqual(api.call_args_list[1].args,
                         ("token", a.RUNNER, "/contents/"+a.STATE_PATH+"?ref="+"a"*40))

    def test_resolves_head_again_for_read_after_write(self):
        responses = [self.head("a"*40), self.file({"phase": "test_started"}),
                     self.head("b"*40), self.file({"phase": "test_retry_reserved"})]
        with patch.object(a, "api", side_effect=responses) as api:
            a.json_file("token", a.STATE_PATH)
            value, _ = a.json_file("token", a.STATE_PATH)
            self.assertEqual(value["phase"], "test_retry_reserved")
            self.assertTrue(api.call_args.args[2].endswith("?ref="+"b"*40))

    def test_invalid_head_stops_before_contents_read(self):
        for head in ({}, {"object": None}, self.head("not-a-sha"), dict(ref="refs/heads/main", object=dict(type="tag", sha="a"*40))):
            with self.subTest(head=head), patch.object(a, "api", return_value=head) as api:
                with self.assertRaisesRegex(a.Hold, "branch_reference_invalid"):
                    a.json_file("token", a.STATE_PATH)
                api.assert_called_once()


if __name__ == "__main__": unittest.main()
