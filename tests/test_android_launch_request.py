"""The Android launcher cannot bypass explicit review or publish anything."""
import unittest
from unittest.mock import patch
from scheduler import android_launch_request as m
from scheduler.android_production import Hold

SHA = "6c13735a6d2dbb0f432a6115b79a9ab5a3fcf4d9"

class AndroidLaunchTests(unittest.TestCase):
    def approved(self):
        return {"version":1, "enabled":True, "sourceSha":SHA,
                "approval":"ANDROID-PRIVATE-BUILD-" + SHA}

    def context(self):
        return {"GITHUB_REPOSITORY":"chaonpu/Runner", "GITHUB_ACTOR":"chaonpu",
                "GITHUB_EVENT_NAME":"push","GITHUB_REF":"refs/heads/main",
                "GITHUB_RUN_ATTEMPT":"1", "GITHUB_WORKFLOW_REF":m.EXPECTED_WORKFLOW,
                "GITHUB_EVENT_BEFORE":"a"*40, "GITHUB_SHA":"b"*40}

    def test_exact_review(self):
        self.assertEqual(m.reviewed_request(self.approved()), SHA)
        for key in ("sourceSha", "approval", "enabled", "version"):
            item=self.approved()
            item[key]="tampered"
            with self.assertRaises(Hold):
                m.reviewed_request(item)
        item=self.approved()
        item["privateSource"]="must-not-appear"
        with self.assertRaises(Hold):
            m.reviewed_request(item)

    def test_default_disabled(self):
        self.assertIsNone(m.reviewed_request({
            "version":1, "enabled":False, "sourceSha":"", "approval":""}))
        with self.assertRaises(Hold):
            m.reviewed_request({"version":1,"enabled":False, "sourceSha":SHA, "approval":""})

    def test_rejects_unrelated_changed_files(self):
        m.check_context(self.context(), ["config/android-launch-request.json"])
        for files in ([], ["README.md"],
                      ["config/android-launch-request.json", "scheduler/android_production.py"]):
            with self.assertRaises(Hold):
                m.check_context(self.context(), files)

    def test_rejects_other_actor_or_retry(self):
        for field,value in (("GITHUB_ACTOR","github-actions[bot]"),
                            ("GITHUB_RUN_ATTEMPT","2"),
                            ("GITHUB_EVENT_NAME","workflow_dispatch"),
                            ("GITHUB_REF","refs/heads/test")):
            ctx=self.context()
            ctx[field]=value
            with self.assertRaises(Hold):
                m.check_context(ctx,["config/android-launch-request.json"])

    def test_read_only_default_never_dispatches(self):
        with patch.object(m, "changed_paths", return_value=["config/android-launch-request.json"]), \
             patch.object(m, "REQUEST") as request, \
             patch.object(m, "run", side_effect=AssertionError("unexpected execution")), \
             patch.dict(m.os.environ, self.context(), clear=True):
            request.read_text.return_value='{"version":1,"enabled":false,"sourceSha":"","approval":""}'
            self.assertEqual(m.main(), 0)

if __name__ == "__main__":
    unittest.main()
