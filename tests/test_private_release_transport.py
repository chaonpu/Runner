"""Ambiguous writes reconcile by exact metadata and bytes, never repeated POSTs."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from tools import private_release_transport as t


class Tests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.run = "42"
        self.data = bytes(range(256)) * 8
        self.calls = []
        self.create_error = self.upload_error = self.read_error = None
        self.existing = False
        self.lookup_error = None
        self.remote = self.data
        self.asset = dict(id=123, name=t.ASSETS["test"], size=len(self.data),
                          state="uploaded", digest="sha256:"+t.sha256(self.data))
        self.metadata = dict(tag_name=t.identity("test", self.sha, self.run),
                             prerelease=True, draft=False, assets=[self.asset])
        self.env = dict(GITHUB_REPOSITORY=t.REPOS["test"], GITHUB_RUN_ID=self.run,
                        GITHUB_SHA=self.sha)

    def gh(self, url, token, *, method="GET", data=None, **kwargs):
        self.calls.append((method, url))
        if method == "POST" and url.endswith("/releases"):
            body = json.loads(data)
            self.assertEqual(body["tag_name"], self.metadata["tag_name"])
            self.assertEqual(body["target_commitish"], self.sha)
            if self.create_error:
                if str(self.create_error) == "github_release_transport_unavailable":
                    self.existing = True
                raise self.create_error
            self.existing = True
            return json.dumps(dict(id=12, tag_name=body["tag_name"],
                                   prerelease=True, draft=False)).encode()
        if method == "POST" and url.startswith("https://uploads.github.com/"):
            self.assertEqual(data, self.data)
            self.assertTrue(url.endswith("/12/assets?name="+t.ASSETS["test"]))
            if self.upload_error: raise self.upload_error
            return json.dumps(dict(name=t.ASSETS["test"], size=len(self.data))).encode()
        if method == "GET" and "/releases/tags/" in url:
            if self.lookup_error:
                raise self.lookup_error
            if not self.existing:
                raise t.TransportError("private_release_not_found")
            return json.dumps(self.metadata).encode()
        if method == "GET" and "/git/ref/tags/" in url:
            return json.dumps(dict(ref="refs/tags/"+self.metadata["tag_name"],
                                   object=dict(type="commit", sha=self.sha))).encode()
        if method == "GET" and url.endswith("/releases/assets/123"):
            if self.read_error: raise self.read_error
            return self.remote
        self.fail((method, url))

    def publish(self, cli=None):
        with patch.dict(os.environ, self.env), patch.object(t, "build", return_value=self.data), \
             patch.object(t, "gh", side_effect=self.gh), \
             patch.object(t.subprocess, "run", side_effect=cli or AssertionError("unexpected CLI")):
            return t.publish("test", self.sha, self.run, Path("unused"), "token", self.sha)

    def posts(self):
        return [url for method, url in self.calls if method == "POST"]

    def test_existing_release_reuses_verified_bytes_without_post(self):
        self.existing = True
        self.assertEqual(self.publish(), t.sha256(self.data))
        self.assertEqual(self.posts(), [])

    def test_second_publish_of_same_run_never_reposts(self):
        self.assertEqual(self.publish(), t.sha256(self.data))
        self.assertEqual(self.publish(), t.sha256(self.data))
        self.assertEqual(len(self.posts()), 2)

    def test_existing_release_digest_mismatch_fails_closed(self):
        self.existing = True
        self.asset["digest"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(t.TransportError, "readback_sha256_mismatch"):
            self.publish()
        self.assertEqual(self.posts(), [])

    def test_existing_release_missing_asset_fails_closed(self):
        self.existing = True
        self.metadata["assets"] = []
        with self.assertRaisesRegex(t.TransportError, "asset_count_invalid"):
            self.publish()
        self.assertEqual(self.posts(), [])

    def test_existing_release_content_mismatch_fails_closed(self):
        self.existing = True
        self.asset["digest"] = None
        self.remote = b"x" * len(self.data)
        with self.assertRaisesRegex(t.TransportError, "bytes_mismatch"):
            self.publish()
        self.assertEqual(self.posts(), [])

    def test_preflight_permission_failure_never_creates(self):
        self.lookup_error = t.TransportError("github_release_http_403")
        with self.assertRaisesRegex(t.TransportError, "http_403"):
            self.publish()
        self.assertEqual(self.posts(), [])

    def test_normal_publish_reads_all_bytes_and_posts_once_each(self):
        self.assertEqual(self.publish(), t.sha256(self.data))
        self.assertEqual(len(self.posts()), 2)
        self.assertTrue(self.calls[-1][1].endswith("/releases/assets/123"))

    def test_upload_response_loss_reconciles_existing_asset_without_another_post(self):
        self.upload_error = t.TransportError("github_release_transport_unavailable")
        self.assertEqual(self.publish(), t.sha256(self.data))
        self.assertEqual(len(self.posts()), 2)

    def test_uncertain_create_never_repeats_create_or_upload(self):
        self.create_error = t.TransportError("github_release_transport_unavailable")
        self.assertEqual(self.publish(), t.sha256(self.data))
        self.assertEqual(len(self.posts()), 1)

    def test_uncertain_create_with_empty_release_is_not_filled_or_overwritten(self):
        self.create_error = t.TransportError("github_release_transport_unavailable")
        self.metadata["assets"] = []
        with self.assertRaisesRegex(t.TransportError, "asset_count_invalid"): self.publish()
        self.assertEqual(len(self.posts()), 1)

    def test_auth_rate_limit_and_duplicate_errors_are_hard_stops(self):
        for status in (401, 403, 422, 429):
            with self.subTest(status=status):
                self.calls = []
                self.existing = False
                self.upload_error = t.TransportError("github_release_http_"+str(status))
                with self.assertRaisesRegex(t.TransportError, "http_"+str(status)): self.publish()
                self.assertEqual(len(self.posts()), 2)
                self.assertEqual(len([method for method, _ in self.calls if method == "GET"]), 1)

    def test_wrong_digest_never_authorizes_lost_response_recovery(self):
        self.upload_error = t.TransportError("github_release_transport_unavailable")
        self.asset["digest"] = "sha256:" + "b"*64
        with self.assertRaisesRegex(t.TransportError, "readback_sha256_mismatch"): self.publish()
        self.assertEqual(len(self.posts()), 2)

    def test_wrong_length_or_incomplete_asset_is_rejected(self):
        for key, value in (("size", len(self.data)+1), ("state", "starter"),
                           ("name", "another.zip")):
            with self.subTest(key=key):
                self.calls=[]; self.existing=False; original=self.asset[key]; self.asset[key]=value
                self.upload_error=t.TransportError("github_release_transport_unavailable")
                with self.assertRaises(t.TransportError): self.publish()
                self.assertEqual(len(self.posts()), 2); self.asset[key]=original

    def test_full_bytes_must_match_even_without_github_digest(self):
        self.asset["digest"] = None
        self.remote = b"x" * len(self.data)
        with self.assertRaisesRegex(t.TransportError, "bytes_mismatch"): self.publish()

    def cli(self, args, **kwargs):
        self.assertEqual(args[:3], ["gh", "release", "download"])
        self.assertEqual(args[3], t.identity("test", self.sha, self.run))
        self.assertEqual(args[args.index("--repo")+1], t.REPOS["test"])
        self.assertEqual(args[args.index("--pattern")+1], t.ASSETS["test"])
        self.assertNotIn("token", args)
        self.assertEqual(kwargs["env"]["GH_TOKEN"], "token")
        self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
        target = Path(args[args.index("--dir")+1]) / t.ASSETS["test"]
        target.write_bytes(self.remote)
        return subprocess.CompletedProcess(args, 0)

    def test_publish_readback_can_use_exact_cli_download_and_still_compare_bytes(self):
        self.read_error = t.TransportError("github_release_transport_unavailable")
        self.assertEqual(self.publish(cli=self.cli), t.sha256(self.data))
        self.assertEqual(len(self.posts()), 2)

    def test_lost_upload_response_and_direct_read_failure_share_readonly_cli_recovery(self):
        self.upload_error = self.read_error = t.TransportError("github_release_transport_unavailable")
        self.assertEqual(self.publish(cli=self.cli), t.sha256(self.data))
        self.assertEqual(len(self.posts()), 2)

    def test_cli_must_match_size_and_digest_and_may_not_mask_http_denials(self):
        self.read_error = t.TransportError("github_release_transport_unavailable")
        self.remote = b"x" * len(self.data)
        with self.assertRaisesRegex(t.TransportError, "asset_sha256_mismatch"): self.publish(cli=self.cli)
        self.read_error = t.TransportError("github_release_http_403")
        with self.assertRaisesRegex(t.TransportError, "http_403"): self.publish()

    def test_cli_failure_or_timeout_cannot_complete_publish(self):
        self.read_error = t.TransportError("github_release_transport_unavailable")
        for cli in (lambda *a, **k: subprocess.CompletedProcess([], 1),
                    lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("gh", 120))):
            with self.subTest(cli=cli), self.assertRaises(t.TransportError): self.publish(cli=cli)

    def test_download_uses_the_same_checked_byte_reader_before_unpack(self):
        self.existing = True
        with patch.object(t, "gh", side_effect=self.gh), patch.object(t, "unpack") as unpack:
            self.assertEqual(t.download("test", self.sha, self.run, Path("output"), "token"),
                             t.sha256(self.data))
            unpack.assert_called_once_with(self.data, "test", Path("output"))

    def test_invalid_publish_context_stops_before_any_network_call(self):
        self.env["GITHUB_SHA"] = "b"*40
        with self.assertRaisesRegex(t.TransportError, "context_invalid"): self.publish()
        self.assertEqual(self.calls, [])

    def test_signed_upload_recovery_stays_in_private_signer_repository(self):
        env = dict(GITHUB_REPOSITORY=t.REPOS["signed"], GITHUB_RUN_ID=self.run,
                   GITHUB_SHA="b"*40)
        asset = dict(self.asset, name=t.ASSETS["signed"])
        calls=[]
        def gh(url, token, *, method="GET", data=None, **kwargs):
            calls.append((method,url))
            if method == "POST" and url.endswith("/releases"):
                body=json.loads(data)
                self.assertEqual(body["target_commitish"], "b"*40)
                return json.dumps(dict(id=12, tag_name=t.identity("signed",self.sha,self.run),
                                       prerelease=True,draft=False)).encode()
            if method == "POST":
                self.assertTrue(url.endswith("/12/assets?name="+t.ASSETS["signed"]))
                raise t.TransportError("github_release_transport_unavailable")
            self.assertEqual(url,"https://api.github.com/repos/"+t.REPOS["signed"]+"/releases/assets/123")
            return self.data
        with patch.dict(os.environ,env), patch.object(t,"build",return_value=self.data), \
             patch.object(t,"gh",side_effect=gh), \
             patch.object(t,"verify_metadata",side_effect=[
                 t.TransportError("private_release_not_found"), asset]) as metadata:
            self.assertEqual(t.publish("signed",self.sha,self.run,Path("unused"),"token","b"*40),
                             t.sha256(self.data))
            self.assertEqual(metadata.call_count, 2)
        self.assertEqual(len([x for x in calls if x[0]=="POST"]),2)
        self.assertTrue(all(t.REPOS["signed"] in url for _,url in calls))


    def test_explicit_git_api_mirror_publish_still_compares_complete_bytes(self):
        with patch.dict(os.environ, {"PREFER_GIT_API_MIRROR": "1"}), \
             patch.object(t, "_git_api_mirror_publish", return_value=self.data) as mirror:
            self.assertEqual(self.publish(), t.sha256(self.data))
            mirror.assert_called_once()

    def test_explicit_git_api_mirror_mismatch_never_authorizes_release(self):
        wrong = b"x" * len(self.data)
        with patch.dict(os.environ, {"PREFER_GIT_API_MIRROR": "1"}), \
             patch.object(t, "_git_api_mirror_publish", return_value=wrong):
            with self.assertRaisesRegex(t.TransportError, "bytes_mismatch"):
                self.publish()

    def test_explicit_git_api_mirror_read_avoids_broken_release_cdn(self):
        with patch.dict(os.environ, {"PREFER_GIT_API_MIRROR": "1"}), \
             patch.object(t, "_git_api_mirror_read", return_value=self.data) as mirror, \
             patch.object(t, "gh", side_effect=AssertionError("unexpected CDN GET")):
            self.assertEqual(
                t.read_asset("test", self.sha, self.run, self.asset, "token"),
                self.data)
            mirror.assert_called_once()


if __name__ == "__main__": unittest.main()
