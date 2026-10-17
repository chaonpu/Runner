from datetime import datetime, timezone
import unittest
from urllib.error import HTTPError

from scheduler.allowance_usage import calculate, probe

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def row(repo, quantity, sku="Actions Linux", **extra):
    return dict(date="2026-10-16", product="Actions", unitType="minutes",
                repositoryName=repo, quantity=quantity, sku=sku, **extra)


def metadata(path):
    repo = path.removeprefix("/repos/")
    return {"full_name": repo, "private": repo.endswith("/Private")}


class AllowanceUsageTests(unittest.TestCase):
    def calc(self, rows, getter=metadata):
        return calculate("owner", 2000, {"usageItems": rows}, getter, NOW)

    def test_public_overflow_never_consumes_private_allowance(self):
        result = self.calc([row("owner/Public", 9000), row("owner/Private", 1500)])
        self.assertEqual(result["remainingMinutesEstimate"], 500)
        self.assertEqual(result["privateLinuxMinutes"], 1500)
        self.assertEqual(result["excludedPublicStandardMinutes"], 9000)
        self.assertTrue(result["publicStandardUsageExcluded"])
        self.assertFalse(result["exactRemainingVerified"])
        self.assertEqual(result["status"], "unknown")

    def test_zero_balance_estimate_never_proves_exhaustion(self):
        result = self.calc([row("owner/Private", 2001)])
        self.assertEqual(result["remainingMinutesEstimate"], 0)
        self.assertEqual(result["status"], "unknown")

    def test_private_discount_is_not_subtracted_as_public_free_usage(self):
        result = self.calc([row("owner/Private", 1700, discountAmount=999, netAmount=0)])
        self.assertEqual(result["remainingMinutesEstimate"], 300)

    def test_other_runner_rates_and_public_larger_runners_do_not_get_guessed(self):
        for repo, sku in (("owner/Private", "Actions Windows"),
                          ("owner/Private", "actions_macos"),
                          ("owner/Public", "actions_linux_64_core")):
            result = self.calc([row(repo, 5, sku)])
            self.assertFalse(result["conversionSupported"])
            self.assertNotIn("remainingMinutesEstimate", result)
        result = self.calc([row("owner/Public", 5000, "actions_windows")])
        self.assertEqual(result["remainingMinutesEstimate"], 2000)

    def test_unknown_private_repositories_are_included_not_only_task_allowlist(self):
        result = self.calc([row("owner/Other", 300)],
                           lambda p: {"full_name": "owner/Other", "private": True})
        self.assertEqual(result["remainingMinutesEstimate"], 1700)

    def test_metadata_is_cached_and_invalid_identity_period_quantity_fail(self):
        calls = []
        def getter(path):
            calls.append(path)
            return metadata(path)
        self.calc([row("owner/Private", 10), row("owner/Private", 20)], getter)
        self.assertEqual(calls, ["/repos/owner/Private"])
        invalid = [dict(row("other/Private", 1)), row("owner/Private", -1),
                   row("owner/Private", True), row("owner/Private", float("nan")),
                   dict(row("owner/Private", 1), date="2026-09-30"),
                   dict(row("owner/Private", 1), date="2026-10-18")]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.calc([item])
        with self.assertRaises(ValueError):
            self.calc([row("owner/Private", 1)], lambda p: {"full_name": "owner/Private"})

    def test_probe_only_gets_usage_and_metadata_and_does_not_leak_errors(self):
        paths = []
        class Reader:
            def __init__(self, token):
                pass
            def get(self, path):
                paths.append(path)
                if path.startswith("/users/"):
                    return {"usageItems": [row("owner/Private", 100)]}
                raise HTTPError("private-url", 404, "sensitive", {}, None)
        result = probe("owner", {"kind": "user", "includedMinutes": 2000,
                                 "billingSecret": "B", "actionsSecret": "A"},
                       {"B": "synthetic", "A": "synthetic"}, NOW, reader_factory=Reader)
        self.assertEqual(result["read"], "not_found")
        self.assertNotIn("sensitive", str(result))
        self.assertEqual(paths, ["/users/owner/settings/billing/usage?year=2026&month=10",
                                 "/repos/owner/Private"])


if __name__ == "__main__":
    unittest.main()
