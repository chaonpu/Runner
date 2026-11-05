"""Transport regressions: retry reads but never uncertain strategy writes."""
import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

from scheduler.probes import GitHubReadOnly, retryable_get_error
from scheduler.production_ports import PUBLIC, PRIVATE, ReadOnlyPorts, request
from scheduler.production_registry import RegistryGuard


class Response:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, *_):
        return self.raw


class ReadTransportTests(unittest.TestCase):
    def test_private_actions_get_retries_single_transient_network_failure(self):
        with patch("scheduler.probes.urllib.request.urlopen",
                   side_effect=[TimeoutError("transient"), Response({"id": 12345678})]) as read, \
             patch("scheduler.probes.time.sleep") as nap:
            got = GitHubReadOnly("synthetic-token").get(
                "/repos/chaonpu/Stock/actions/runs/12345678")
        self.assertEqual(got["id"], 12345678)
        self.assertEqual(read.call_count, 2)
        self.assertEqual(nap.call_count, 1)
        for entry in read.call_args_list:
            self.assertEqual(entry.args[0].get_method(), "GET")
            self.assertIsNone(entry.args[0].data)

    def test_private_actions_get_never_retries_403_or_429(self):
        for status in (401, 403, 404, 429):
            with self.subTest(status=status), \
                 patch("scheduler.probes.urllib.request.urlopen",
                       side_effect=urllib.error.HTTPError(
                           "https://api.github.com/test", status,
                           "unavailable", None, None)) as read, \
                 patch("scheduler.probes.time.sleep") as nap:
                with self.assertRaises(urllib.error.HTTPError):
                    GitHubReadOnly("synthetic-token").get(
                        "/repos/chaonpu/Stock/actions/runs/12345678")
                read.assert_called_once()
                nap.assert_not_called()

    def test_safe_get_response_is_retried_on_server_failure(self):
        with patch("scheduler.probes.urllib.request.urlopen",
                   side_effect=[urllib.error.HTTPError(
                       "https://api.github.com/test", 503, "unavailable",
                       None, None), Response({"ok": True})]) as read, \
             patch("scheduler.probes.time.sleep"):
            self.assertTrue(GitHubReadOnly("synthetic-token").get(
                "/repos/chaonpu/Stock/actions/runs/12345678")["ok"])
            self.assertEqual(read.call_count, 2)

    def test_public_registry_get_retries_without_changing_commit_ref(self):
        opener = Mock(side_effect=[TimeoutError("temporary network"),
                                   Response({"commit": {"sha": "a" * 40}})])
        with patch("scheduler.production_ports.time.sleep") as nap:
            got = request("synthetic-token", "GET",
                          PUBLIC + "/branches/main", opener=opener)
        self.assertEqual(got["commit"]["sha"], "a" * 40)
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(nap.call_count, 1)
        for item in opener.call_args_list:
            self.assertEqual(item.args[0].get_method(), "GET")
            self.assertIsNone(item.args[0].data)

    def test_no_retry_of_uncertain_dispatch_or_public_write(self):
        for method, path in (
            ("POST", PRIVATE + "/actions/workflows/run-a-share.yml/dispatches"),
            ("PUT", PUBLIC + "/contents/state/tasks.json"),
        ):
            with self.subTest(method=method):
                opener = Mock(side_effect=TimeoutError("accepted, response lost"))
                with patch("scheduler.production_ports.time.sleep") as nap:
                    with self.assertRaisesRegex(RegistryGuard,
                                               "ports_response_uncertain_no_retry"):
                        request("synthetic-token", method, path,
                                {"intent": "synthetic"}, opener=opener)
                opener.assert_called_once()
                nap.assert_not_called()

    def test_private_run_read_returns_safe_transport_reason(self):
        class Reader:
            def get(self, _path):
                raise TimeoutError("sensitive trace must not leak")
        ports = ReadOnlyPorts(
            {"CHAONPU_ACTIONS_READ_TOKEN": "synthetic"},
            reader_factory=lambda *_: Reader())
        with self.assertRaisesRegex(RegistryGuard,
                                    "private_actions_read_network_unavailable") as caught:
            ports.run("chaonpu/Stock", 12345678)
        self.assertNotIn("sensitive", str(caught.exception))

    def test_untrusted_endpoint_is_rejected_before_request(self):
        with patch("scheduler.probes.urllib.request.urlopen") as read:
            with self.assertRaises(ValueError):
                GitHubReadOnly("synthetic").get(
                    "/repos/chaonpu/Stock/contents/private.json")
            read.assert_not_called()

    def test_coordinator_diagnostic_sanitizes_raw_transport_error(self):
        from scheduler import production_coordinator as coordinator
        plan = {"businessDate": "2026-11-05", "publicationMode": "formal"}
        with patch.object(coordinator, "manual_task_context"), \
             patch.object(coordinator, "pin_latest"), \
             patch.object(coordinator, "resolve_request", return_value=plan), \
             patch.object(coordinator, "report_plan"), \
             patch.object(coordinator, "execute",
                          side_effect=urllib.error.HTTPError(
                              "https://api.github.com/private", 503,
                              "private response", None, None)), \
             patch("sys.argv", ["runner", "--task", "stock_a", "--execute"]), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(coordinator.main(), 2)
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result["reason"], "coordinator_stopped_manual_review")
        self.assertEqual(result["failureKind"], "read_transport_github_unavailable")
        self.assertFalse(result["safeToRetry"])
        self.assertNotIn("private", output.getvalue())


if __name__ == "__main__":
    unittest.main()
