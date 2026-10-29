"""Offline regression tests for immutable Git API private Release mirror."""
import base64
import os
from unittest import TestCase
from unittest.mock import patch
from tools import private_git_api_transport as m


class FakeGit:
    def __init__(self, source):
        self.source = source
        self.blobs = {}
        self.refs = {}
        self.tree = None
        self.commit = None
        self.original_label = "verified-test-" + source + "-run-42"
        self.refs[self.original_label] = {"ref": "refs/tags/" + self.original_label,
                                          "object": {"type": "commit", "sha": source}}
        self.blob_posts = 0
        self.ref_posts = 0

    def call(self, repo, path, token, *, payload=None, missing_ok=False):
        assert repo == m.REPOS["test"] and token == "fake-token"
        if path.startswith("/git/ref/tags/"):
            label = path.removeprefix("/git/ref/tags/")
            if missing_ok:
                return self.refs.get(label)
            return self.refs[label]
        if path == "/git/blobs" and payload is not None:
            assert payload["encoding"] == "base64"
            value = base64.b64decode(payload["content"])
            sha = m.git_hash(value)
            self.blobs[sha] = value
            self.blob_posts += 1
            return {"sha": sha}
        if path.startswith("/git/blobs/"):
            sha = path.removeprefix("/git/blobs/")
            value = self.blobs[sha]
            return {"sha": sha, "encoding": "base64", "size": len(value),
                    "content": base64.b64encode(value).decode()}
        if path == "/git/trees" and payload is not None:
            self.tree = {"sha": "b" * 40, "truncated": False,
                         "tree": list(payload["tree"])}
            return {"sha": "b" * 40}
        if path == "/git/trees/" + "b" * 40:
            return self.tree
        if path == "/git/commits" and payload is not None:
            assert payload["parents"] == [self.source] and payload["tree"] == "b" * 40
            self.commit = {"sha": "c" * 40, "tree": {"sha": "b" * 40},
                           "parents": [{"sha": self.source}]}
            return {"sha": "c" * 40}
        if path == "/git/commits/" + "c" * 40:
            return self.commit
        if path == "/git/refs" and payload is not None:
            label = payload["ref"].removeprefix("refs/tags/")
            self.ref_posts += 1
            entry = {"ref": payload["ref"],
                     "object": {"type": "commit", "sha": payload["sha"]}}
            self.refs[label] = entry
            return entry
        raise AssertionError("Unrecognized Git API call: " + path)


class Tests(TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.data = bytes(range(256)) * 1100
        self.asset = {"id": 123, "size": len(self.data),
                      "digest": "sha256:" + m.checksum(self.data)}
        self.server = FakeGit(self.sha)

    def test_full_chunked_publish_readback_and_idempotence(self):
        with patch.object(m, "call", side_effect=self.server.call):
            self.assertEqual(m.publish("test", self.sha, "42", self.asset,
                                       self.data, "fake-token", self.sha),
                             m.checksum(self.data))
            self.assertEqual(m.read("test", self.sha, "42", self.asset,
                                    "fake-token"), self.data)
            self.assertEqual(self.server.blob_posts, 2)
            self.assertEqual(self.server.ref_posts, 1)
            self.assertEqual(m.publish("test", self.sha, "42", self.asset,
                                       self.data, "fake-token", self.sha),
                             m.checksum(self.data))
            self.assertEqual(self.server.blob_posts, 2)
            self.assertEqual(self.server.ref_posts, 1)

    def test_digest_failure_blocks_all_writes(self):
        bad = dict(self.asset, digest="sha256:" + "0" * 64)
        with patch.object(m, "call", side_effect=self.server.call):
            with self.assertRaisesRegex(m.MirrorError, "release_digest_mismatch"):
                m.publish("test", self.sha, "42", bad,
                          self.data, "fake-token", self.sha)
        self.assertEqual(self.server.blob_posts, 0)

    def test_source_tag_mismatch_blocks_publishing(self):
        self.server.refs[self.server.original_label]["object"]["sha"] = "f" * 40
        with patch.object(m, "call", side_effect=self.server.call):
            with self.assertRaisesRegex(m.MirrorError, "original_release_tag_target_mismatch"):
                m.publish("test", self.sha, "42", self.asset,
                          self.data, "fake-token", self.sha)
        self.assertEqual(self.server.ref_posts, 0)

    def test_corrupted_blob_and_missing_part_fail_closed(self):
        with patch.object(m, "call", side_effect=self.server.call):
            m.publish("test", self.sha, "42", self.asset, self.data,
                      "fake-token", self.sha)
            first = self.server.tree["tree"][0]["sha"]
            self.server.blobs[first] = b"corruption"
            with self.assertRaisesRegex(m.MirrorError, "blob_bytes_invalid"):
                m.read("test", self.sha, "42", self.asset, "fake-token")
            self.server.tree["tree"].pop()
            with self.assertRaisesRegex(m.MirrorError, "tree_identity_invalid"):
                m.read("test", self.sha, "42", self.asset, "fake-token")

    def test_missing_ref_never_falls_back_to_an_unverified_source(self):
        with patch.object(m, "call", side_effect=self.server.call):
            with self.assertRaisesRegex(m.MirrorError, "ref_missing"):
                m.read("test", self.sha, "42", self.asset, "fake-token")

    def test_mirror_tag_identity_is_strict(self):
        with self.assertRaisesRegex(m.MirrorError, "identity_invalid"):
            m.tag("test", "../malicious", "42")
        with self.assertRaisesRegex(m.MirrorError, "identity_invalid"):
            m.tag("signed", self.sha, "0")


if __name__ == "__main__":
    import unittest
    unittest.main()
