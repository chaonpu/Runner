"""Bounded, authenticated calendar transport regression tests (no network)."""
import base64
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from scheduler import calendar_runtime as runtime


class CalendarTransportTests(unittest.TestCase):
    path = "calendars/current.json"

    def response(self, raw=b'{"schemaVersion":1}'):
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        item = {"path": self.path, "type": "file", "encoding": "base64",
                "sha": blob, "content": base64.b64encode(raw).decode()}
        return io.BytesIO(json.dumps(item).encode())

    def error(self, code, reason="Forbidden", headers=None):
        return HTTPError("https://api.github.com/test", code, reason, headers or {}, io.BytesIO())

    def test_token_uses_dedicated_secret_and_never_leaks(self):
        with patch.dict(os.environ, {"ZIWU_CALENDAR_API_TOKEN": "test-secret", "GH_TOKEN": "other"}), \
                patch.object(runtime.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = self.response()
            self.assertEqual(runtime.fetch_public(self.path), b'{"schemaVersion":1}')
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(req.get_header("Authorization"), "Bearer test-secret")
            self.assertEqual(req.full_url,
                             "https://api.github.com/repos/chaonpu/ziwuxilv/contents/calendars/current.json?ref=main")

    def test_runner_token_is_supported_when_dedicated_token_absent(self):
        with patch.dict(os.environ, {"ZIWU_CALENDAR_API_TOKEN": "", "GH_TOKEN": "runner-token"}), \
                patch.object(runtime.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = self.response()
            runtime.fetch_public(self.path)
            self.assertEqual(build.return_value.open.call_args.args[0].get_header("Authorization"),
                             "Bearer runner-token")

    def test_rate_limited_403_retries_and_returns_verified_git_blob(self):
        limited = self.error(403, "rate limit exceeded", {"X-RateLimit-Remaining": "0"})
        with patch.object(runtime.urllib.request, "build_opener") as build, \
                patch.object(runtime.time, "sleep") as sleep, patch("builtins.print") as output:
            build.return_value.open.side_effect = [limited, self.response()]
            self.assertEqual(runtime.fetch_public(self.path), b'{"schemaVersion":1}')
            self.assertEqual(build.return_value.open.call_count, 2)
            sleep.assert_called_once_with(2)
            self.assertNotIn("Authorization", str(output.call_args_list))

    def test_unrelated_403_and_missing_files_fail_without_retry(self):
        for code in (403, 404, 401):
            with self.subTest(code=code), patch.object(runtime.urllib.request, "build_opener") as build, \
                    patch.object(runtime.time, "sleep") as sleep:
                build.return_value.open.side_effect = self.error(code)
                with self.assertRaises(HTTPError):
                    runtime.fetch_public(self.path)
                self.assertEqual(build.return_value.open.call_count, 1)
                sleep.assert_not_called()

    def test_retry_after_is_honored_but_never_waits_unbounded(self):
        for seconds, expected_calls in ((7, 2), (900, 1)):
            with self.subTest(seconds=seconds), \
                    patch.object(runtime.urllib.request, "build_opener") as build, \
                    patch.object(runtime.time, "sleep") as sleep, patch("builtins.print"):
                build.return_value.open.side_effect = [
                    self.error(429, "limited", {"Retry-After": str(seconds)}), self.response()]
                if expected_calls == 1:
                    with self.assertRaises(HTTPError):
                        runtime.fetch_public(self.path)
                    sleep.assert_not_called()
                else:
                    self.assertEqual(runtime.fetch_public(self.path), b'{"schemaVersion":1}')
                    sleep.assert_called_once_with(seconds)
                self.assertEqual(build.return_value.open.call_count, expected_calls)

    def test_transient_retries_are_bounded(self):
        with patch.object(runtime.urllib.request, "build_opener") as build, \
                patch.object(runtime.time, "sleep") as sleep, patch("builtins.print"):
            build.return_value.open.side_effect = self.error(
                403, "rate limit exceeded", {"X-RateLimit-Remaining": "0"})
            with self.assertRaises(HTTPError):
                runtime.fetch_public(self.path)
            self.assertEqual(build.return_value.open.call_count, 4)
            self.assertEqual([x.args[0] for x in sleep.call_args_list], [2, 5, 10])

    def test_cache_can_only_fallback_to_same_requested_digest(self):
        year = {"coverageStart": "2026-01-01", "coverageEnd": "2026-12-31",
                "verified": True, "sources": ["https://example.org"],
                "holidays": {}, "overrides": {}, "unknownDates": []}
        raw = runtime.canonical({"schemaVersion": 1, "calendars": {
            name: {"timezone": tz, "weekdays": [1, 2, 3, 4, 5], "years": {"2026": year}}
            for name, tz in (("CN_EQ", "Asia/Shanghai"), ("US_EQ", "America/New_York"))}})
        revision = hashlib.sha256(raw).hexdigest()

        def unavailable(_):
            raise OSError("remote unavailable")

        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ):
            cache = Path(root) / "calendar.json"
            cache.write_bytes(raw)
            previous = runtime._snapshot
            try:
                self.assertEqual(runtime.pin_latest(cache, revision=revision, fetch=unavailable)["calendarOrigin"],
                                 "verified_cache")
                with self.assertRaisesRegex(runtime.CalendarUnavailable,
                                            "calendar_requested_revision_unavailable"):
                    runtime.pin_latest(cache, revision="0" * 64, fetch=unavailable)
            finally:
                runtime._snapshot = previous


if __name__ == "__main__":
    unittest.main()
