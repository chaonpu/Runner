"""Tests proving signed-only recovery never ignores prior signer provenance."""
import unittest
from unittest.mock import patch
from scheduler import android_signing_recovery as r
from scheduler.android_production import Hold

SOURCE = "ce0be22bf758f2621235a165bfc07c059cca5de1"

class RecoveryTests(unittest.TestCase):
    def request(self):
        return {"version": 1, "enabled": True, "sourceSha": SOURCE,
                "testRunId": 39039852886, "failedSigningRunId": 39041021094,
                "approval": "RECOVER-SIGNER-" + SOURCE + "-39041021094"}

    def test_requires_explicit_approval_and_exact_ids(self):
        self.assertEqual(r.approve(self.request()), (SOURCE, 39039852886, 39041021094))
        for k,v in (("approval","bad"),("testRunId",0),
                    ("failedSigningRunId",False),("sourceSha","new")):
            q=self.request()
            q[k]=v
            with self.assertRaises(Hold):
                r.approve(q)
        self.assertIsNone(r.approve({"version":1,"enabled":False,"sourceSha":"",
                                    "testRunId":0,"failedSigningRunId":0,"approval":""}))

    def test_refuse_suspected_key_material_use(self):
        steps=[{"name":r.PREFLIGHT,"conclusion":"failure"}]
        steps.extend({"name":name,"conclusion":"skipped"} for name in r.FORBIDDEN_STARTED)
        data={"total_count":1,"jobs":[{"conclusion":"failure","steps":steps}]}
        r.verify_no_signing_started(data)
        for name in r.FORBIDDEN_STARTED:
            modified={"total_count":1,"jobs":[{"conclusion":"failure","steps":[
                {"name":x["name"],"conclusion":"success" if x["name"] == name else x["conclusion"]}
                for x in steps]}]}
            with self.assertRaises(Hold):
                r.verify_no_signing_started(modified)

    def test_two_prior_attempts_are_proven_pre_key_release_download_failures(self):
        source = "Download exact successful private Test run handoff"
        early = "NETWORK_TRANSIENT_PREFLIGHT - verify and export GitHub HTTPS Git route"
        skipped = list(r.FORBIDDEN_STARTED)
        row = {"run_id": 40566493488, "run_attempt": 2, "runner_name": "ziwuxilv-sign-win",
               "status": "completed", "conclusion": "failure", "steps": [
                   {"name": early, "conclusion": "success"},
                   {"name": source, "conclusion": "failure"},
               ] + [{"name": name, "conclusion": "skipped"} for name in skipped]}
        response = {"total_count": 1, "jobs": [row]}
        r.verify_no_signing_started(response, run_id=40566493488, attempt=2,
                                    expected_runner="ziwuxilv-sign-win")
        for stage in skipped:
            changed = dict(row, steps=[
                dict(x, conclusion="success") if x["name"] == stage else x
                for x in row["steps"]])
            with self.assertRaisesRegex(Hold, "could_have_accessed_keys"):
                r.verify_no_signing_started({"total_count": 1, "jobs": [changed]},
                                            run_id=40566493488, attempt=2,
                                            expected_runner="ziwuxilv-sign-win")
        with self.assertRaisesRegex(Hold, "execution_unverified"):
            r.verify_no_signing_started(response, run_id=40566493488, attempt=1,
                                        expected_runner="ziwuxilv-sign-win")
        with self.assertRaisesRegex(Hold, "execution_unverified"):
            r.verify_no_signing_started(response, run_id=40566493488, attempt=2,
                                        expected_runner="incorrect")

    def test_cas_stops_any_unsigned_recovery_without_exact_prior_state(self):
        document={"builds":{SOURCE:{"phase":"signed_verified",
                                   "sourceSha":SOURCE,
                                   "testRunId":39039852886,
                                   "signingRunId":39041021094}}}
        with patch.object(r,"json_file",return_value=(document,"etag")), \
             patch.object(r,"api",side_effect=AssertionError("should not write")):
            with self.assertRaises(Hold):
                r.change_state("token",SOURCE,39039852886,39041021094,"sign_retry_reserved")

    def test_context_requires_owner_and_one_changed_file(self):
        e={"GITHUB_REPOSITORY":"chaonpu/Runner","GITHUB_ACTOR":"chaonpu",
           "GITHUB_EVENT_NAME":"push","GITHUB_REF":"refs/heads/main",
           "GITHUB_RUN_ATTEMPT":"1","GITHUB_WORKFLOW_REF":r.WORKFLOW,
           "GITHUB_EVENT_BEFORE":"a"*40,"GITHUB_SHA":"b"*40}
        with patch.object(r,"changed_paths",return_value=["config/android-signing-recovery.json"]):
            r.guard_context(e)
            e["GITHUB_ACTOR"]="unknown"
            with self.assertRaises(Hold):
                r.guard_context(e)
        e["GITHUB_ACTOR"]="chaonpu"
        with patch.object(r,"changed_paths",return_value=["README.md"]):
            with self.assertRaises(Hold):
                r.guard_context(e)

if __name__ == "__main__":
    unittest.main()
