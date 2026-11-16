"""The public APK version check must remain fixed-source, read-only and fail closed."""
import base64
import hashlib
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import Mock, patch

from scheduler import apk_stage as a


def version_reply(code=190):
    raw = json.dumps({"versionCode": code, "versionName": "3.0.7"}).encode()
    encoded = base64.b64encode(raw).decode()
    item = {
        "type": "file",
        "path": "android-native/version.json",
        "encoding": "base64",
        "content": encoded[:30] + "\n" + encoded[30:],
        "sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
    }
    return item


class PublicVersionReadTests(unittest.TestCase):
    def call(self, payloads, token="read-token"):
        responses = list(payloads)
        fake = Mock()
        requests = []

        def open_request(request, timeout):
            requests.append(request)
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return io.BytesIO(json.dumps(item).encode())

        fake.open.side_effect = open_request
        env = {"CHAONPU_SOURCE_READ_TOKEN": token, "CHAONPU_ACTIONS_READ_TOKEN": "backup"}
        with patch.dict(os.environ, env, clear=True), patch.object(
                a.urllib.request, "build_opener", return_value=fake):
            try:
                result = a.public_version()
                error = None
            except a.GuardError as exc:
                result = None
                error = str(exc)
        return result, error, requests

    def test_exact_authenticated_public_version_check(self):
        code, error, requests = self.call([version_reply()])
        self.assertEqual(190, code)
        self.assertIsNone(error)
        self.assertEqual(1, len(requests))
        self.assertEqual("GET", requests[0].get_method())
        self.assertEqual(
            "https://api.github.com/repos/chaonpu/ziwuxilv/"
            "contents/android-native/version.json?ref=main",
            requests[0].full_url)
        self.assertEqual("Bearer read-token", requests[0].get_header("Authorization"))

    def test_transient_http_429_retries_only_fixed_get(self):
        denied = urllib.error.HTTPError("https://api.github.com", 429, "limited", {}, None)
        code, error, requests = self.call([denied, version_reply()])
        self.assertEqual(190, code)
        self.assertIsNone(error)
        self.assertEqual(2, len(requests))
        self.assertEqual(requests[0].full_url, requests[1].full_url)

    def test_network_errors_have_bounded_retry(self):
        code, error, requests = self.call([
            urllib.error.URLError("unreachable") for _ in range(3)
        ])
        self.assertIsNone(code)
        self.assertEqual("public_version_unverified_network", error)
        self.assertEqual(3, len(requests))

    def test_permission_denial_does_not_retry_or_replace_version(self):
        denied = urllib.error.HTTPError("https://api.github.com", 403, "denied", {}, None)
        code, error, requests = self.call([denied, version_reply()])
        self.assertIsNone(code)
        self.assertEqual("public_version_unverified_http_403", error)
        self.assertEqual(1, len(requests))

    def test_malformed_or_tampered_content_fails_closed(self):
        for item, reason in [
            (dict(version_reply(), sha="0" * 40), "public_version_unverified_blob"),
            (dict(version_reply(), encoding="none"), "public_version_unverified_schema"),
            (dict(version_reply(), path="different/version.json"), "public_version_unverified_schema"),
            (version_reply(code=0), "public_version_unverified_schema"),
        ]:
            with self.subTest(reason=reason, item=item):
                code, error, requests = self.call([item])
                self.assertIsNone(code)
                self.assertEqual(reason, error)
                self.assertEqual(1, len(requests))


if __name__ == "__main__":
    unittest.main()
