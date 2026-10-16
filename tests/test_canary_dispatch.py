import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from scheduler.canary_dispatch import payload, run_canary

class Response:
    def __init__(self, body=b'{"workflow_run_id":123456}'):
        self.body = body
        self.status = 200
    def read(self, n):
        return self.body[:n]
    def __enter__(self): return self
    def __exit__(self, *args): return False

class DispatchPermissionTests(unittest.TestCase):
    def test_exact_targets_only(self):
        for target in ("stock", "test", "signer", "publish"):
            repo, body, key = payload(target, "123456789")
            self.assertIn("actions", key.lower())
            self.assertEqual(body["ref"], "main")
            self.assertTrue(body["inputs"]["dispatch_id"].endswith("-" + target))
            self.assertEqual(body["return_run_details"], True)
        with self.assertRaises(ValueError):
            payload("database", "123456789")
        with self.assertRaises(ValueError):
            payload("stock", "arbitrary-name")

    def test_same_chaonpu_token_is_scoped_to_three_noop_workflows(self):
        for target, suffix in (("stock", "Stock"),
                               ("signer", "ziwuxilv-android"),
                               ("publish", "ziwuxilv")):
            repo, body, secret = payload(target, "123456789")
            self.assertEqual(repo, "chaonpu/" + suffix)
            self.assertEqual(secret, "CHAONPU_ACTIONS_READ_TOKEN")
            self.assertEqual(body["inputs"]["dispatch_id"],
                             "runner-canary-123456789-" + target)

    def test_no_post_without_secret(self):
        def blocked(*a, **kw):
            raise AssertionError("HTTP called without secret")
        result = run_canary("test", "123456789", {}, opener=blocked)
        self.assertEqual(result["status"], "missing_secret")
        self.assertFalse(result["dispatched"])

    def test_real_call_is_only_allowlisted_canary(self):
        captured = []
        def fake(req, timeout):
            captured.append(req)
            return Response()
        result = run_canary("stock", "123456789",
                            {"CHAONPU_ACTIONS_READ_TOKEN": "fake_secret"},
                            opener=fake)
        self.assertTrue(result["dispatched"])
        self.assertEqual(result["runId"], 123456)
        req = captured[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertTrue(req.full_url.endswith(
            "/repos/chaonpu/Stock/actions/workflows/runner-dispatch-canary.yml/dispatches"))
        body = json.loads(req.data)
        self.assertEqual(body["inputs"], {"dispatch_id": "runner-canary-123456789-stock"})
        self.assertNotIn("fake_secret", json.dumps(result))

    def test_old_204_response_never_retries(self):
        def fake(*a, **kw):
            response = Response(b"")
            response.status = 204
            return response
        result = run_canary("test", "123456789",
                            {"TEST_ACTIONS_READ_TOKEN": "secret"}, opener=fake)
        self.assertEqual(result["status"], "accepted_run_id_unavailable")
        self.assertEqual(result["dispatched"], True)

    def test_403_is_not_runner_offline(self):
        def fake(*a, **kw):
            raise HTTPError("redacted", 403, "secret", {}, None)
        result = run_canary("test", "123456789",
                            {"TEST_ACTIONS_READ_TOKEN": "secret"}, opener=fake)
        self.assertEqual(result["status"], "permission_denied")
        self.assertEqual(result["httpStatus"], 403)
        self.assertFalse(result["dispatched"])

    def test_timeout_is_unknown_not_a_failure_safe_to_retry(self):
        def fake(*a, **kw):
            raise TimeoutError("secret")
        result = run_canary("test", "123456789",
                            {"TEST_ACTIONS_READ_TOKEN": "secret"}, opener=fake)
        self.assertEqual(result["dispatched"], "unknown")
        self.assertEqual(result["status"], "network_response_unknown")

if __name__ == "__main__":
    unittest.main()
