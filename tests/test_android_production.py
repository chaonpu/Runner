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
               "signingWindowsWorkflow": a.SIGN_WORKFLOW}}}
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
               "signingWindowsWorkflow": a.SIGN_WORKFLOW}}}
        record = {"version": 1, "builds": {sha: {
            "sourceSha": sha, "phase": "test_started", "testRunId": 42}}}
        def doc(token, path):
            return (cfg, "blob") if path == a.CONFIG_PATH else (record, "blob")
        with patch.object(a, "json_file", side_effect=doc):
            with self.assertRaisesRegex(a.Hold, "resume_requires_original_source_sha"):
                a.run({"GH_TOKEN": "p", "TEST_ACTIONS_READ_TOKEN": "t"}, execute=True, resume=True)

if __name__ == "__main__":
    unittest.main()
