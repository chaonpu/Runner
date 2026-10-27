"""Fail-closed coordinator tests for private Release handoffs (no network)."""
import unittest
from unittest.mock import patch
from scheduler import android_production as a

SHA = "a" * 40
RUN = 12345

def release(repo=a.TEST, sha=SHA, rid=RUN, asset=None):
    kind = "test" if repo == a.TEST else "signed"
    tag = f"verified-{kind}-{sha}-run-{rid}"
    return {
        "tag_name": tag, "prerelease": True, "draft": False,
        "assets": [asset or {
            "id": 34, "name": f"verified-{kind}-bundle.zip",
            "state": "uploaded", "size": 3000,
            "digest": "sha256:" + "b" * 64,
        }],
    }

class PrivateReleaseGateTests(unittest.TestCase):
    def test_test_release_requires_exact_source_tag_commit(self):
        correct = {"ref": f"refs/tags/verified-test-{SHA}-run-{RUN}",
                   "object": {"type": "commit", "sha": SHA}}
        with patch.object(a, "api", side_effect=[release(), correct]) as api:
            a.verify_artifact("token", a.TEST, RUN, "windows-test-verified", SHA)
            self.assertEqual(api.call_count, 2)
        for invalid in ("c" * 40, "", None):
            wrong = dict(correct, object={"type": "commit", "sha": invalid})
            with patch.object(a, "api", side_effect=[release(), wrong]):
                with self.assertRaisesRegex(a.Hold, "source_tag_mismatch"):
                    a.verify_artifact("token", a.TEST, RUN, "cloud-test-verified", SHA)

    def test_missing_ambiguous_or_unuploaded_asset_never_counts_as_verified(self):
        examples = [
            {"assets": []},
            {"assets": release()["assets"] * 2},
            {"assets": [{"id": 34, "name": "other.zip", "state": "uploaded", "size": 3000}]},
            {"assets": [{"id": 34, "name": "verified-test-bundle.zip", "state": "starter", "size": 3000}]},
        ]
        for bad in examples:
            with self.subTest(bad=bad), patch.object(a, "api", return_value={**release(), **bad}):
                with self.assertRaises(a.Hold):
                    a.verify_artifact("token", a.TEST, RUN, "windows-test-verified", SHA)

    def test_wrong_release_tag_or_untrusted_repo_is_rejected(self):
        other = {**release(), "tag_name": "verified-test-" + ("b" * 40) + "-run-12345"}
        with patch.object(a, "api", return_value=other):
            with self.assertRaises(a.Hold):
                a.verify_artifact("token", a.TEST, RUN, "windows-test-verified", SHA)
        with patch.object(a, "api", side_effect=AssertionError("network must not be called")):
            with self.assertRaises(a.Hold):
                a.verify_artifact("token", "someone/other", RUN, "windows-test-verified", SHA)
            with self.assertRaises(a.Hold):
                a.verify_artifact("token", a.TEST, RUN, "windows-signed-release", SHA)

    def test_signed_release_asset_stays_private_and_exact_run_bound(self):
        with patch.object(a, "api", return_value=release(a.SIGNER)) as api:
            a.verify_artifact("token", a.SIGNER, RUN, "windows-signed-release", SHA)
            self.assertEqual(api.call_count, 1)
        wrong = release(a.SIGNER, rid=RUN + 1)
        with patch.object(a, "api", return_value=wrong):
            with self.assertRaises(a.Hold):
                a.verify_artifact("token", a.SIGNER, RUN, "windows-signed-release", SHA)

if __name__ == "__main__":
    unittest.main()
