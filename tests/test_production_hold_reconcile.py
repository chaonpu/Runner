"""Local-only tests for evidence-gated recovery scheduling; no GitHub writes."""
import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from scheduler.production_hold_reconcile import (
    context, decode_file, reconcile_one, request)
from scheduler.production_registry import RegistryGuard


class AutomaticRecoveryTests(unittest.TestCase):
    def test_allow_only_approved_runner_contexts(self):
        env = {"GITHUB_REPOSITORY": "chaonpu/Runner",
               "GITHUB_REF": "refs/heads/main",
               "GITHUB_RUN_ATTEMPT": "1",
               "GITHUB_EVENT_NAME": "schedule",
               "GITHUB_WORKFLOW_REF":
                   "chaonpu/Runner/.github/workflows/reconcile-verified-holds.yml@refs/heads/main"}
        context(env)
        for key, value in (("GITHUB_REPOSITORY", "other/repo"),
                           ("GITHUB_EVENT_NAME", "push"),
                           ("GITHUB_RUN_ATTEMPT", "2"),
                           ("GITHUB_REF", "refs/heads/staging")):
            wrong = dict(env, **{key: value})
            with self.subTest(key=key), self.assertRaises(RegistryGuard):
                context(wrong)

    def test_public_json_blob_requires_actual_git_sha(self):
        data = b'{"schema":1,"tasks":[]}'
        sha = hashlib.sha1(b"blob " + str(len(data)).encode() +
                           b"\\0" + data).hexdigest()
        record = {"path": "state/tasks.json", "type": "file",
                  "encoding": "base64",
                  "sha": sha, "content": base64.b64encode(data).decode()}
        self.assertEqual(decode_file(record, "state/tasks.json")[0]["schema"], 1)
        record["sha"] = "a" * 40
        with self.assertRaises(RegistryGuard):
            decode_file(record, "state/tasks.json")

    def test_no_hold_and_unbound_hold_never_contact_github(self):
        with patch("scheduler.production_hold_reconcile.request",
                   side_effect=AssertionError("must not read private data")):
            self.assertIsNone(reconcile_one({}, {"tasks": []}, "unused"))
            self.assertIsNone(reconcile_one({}, {"tasks": [
                {"task": "stock_a", "publicationMode": "formal",
                 "status": "hold", "runId": None}]}, "unused"))

    def test_never_accept_unapproved_api_or_repository(self):
        for repo, path, method in (
            ("evil/Stock", "/actions/runs/123", "GET"),
            ("chaonpu/Stock", "/contents/secret", "GET"),
            ("chaonpu/Runner", "/contents/config/repositories.json", "PUT"),
            ("chaonpu/Runner", "/actions/workflows/something/dispatches", "POST")):
            with self.subTest(repo=repo, path=path), self.assertRaises(RegistryGuard):
                request(repo, path, "fake-test-credential", method=method)


if __name__ == "__main__":
    unittest.main()
