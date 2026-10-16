"""Synthetic no-dispatch acceptance tests; actual GitHub read is CI-only."""
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from scheduler.live_readonly_acceptance import accept, EXPECTED_WORKFLOW
from scheduler.production_registry import RegistryGuard


def context():
    return {
        "GITHUB_REPOSITORY": "chaonpu/Runner",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": EXPECTED_WORKFLOW,
        "GITHUB_RUN_ATTEMPT": "1",
        "GH_TOKEN": "synthetic-only",
    }


def fake_get(config, token):
    assert token == "synthetic-only"
    return {"schema": 1, "tasks": []}, "a" * 40


def fake_audit(config, registry, task, date, *, env, clock):
    return {
        "status": "locked_read_only", "businessDate": "2026-10-16",
        "dateMode": "auto_official_schedule_candidate",
        "exchangeSessionVerified": False,
        "marketTimezone": "Asia/Shanghai" if task == "stock_a" else "America/New_York",
        "registeredTaskStatus": "not_registered", "routeRecommendation": "deferred",
        "dispatchAuthorized": False, "registryWriteAuthorized": False,
        "databaseWriteAuthorized": False, "automaticRetryAuthorized": False,
        "productionReady": False,
    }


class LiveReadOnlyAcceptanceTests(unittest.TestCase):
    def test_one_live_read_and_two_market_audits(self):
        reader = Mock(side_effect=fake_get)
        checker = Mock(side_effect=fake_audit)
        result = accept(context(), at=datetime(2026, 10, 16, 22, tzinfo=timezone.utc),
                        read_registry=reader, audit=checker)
        self.assertEqual(result["status"], "live_registry_read_only_confirmed")
        self.assertEqual(result["taskCount"], 0)
        self.assertEqual([x["market"] for x in result["marketAudits"]],
                         ["stock_a", "stock_us"])
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(checker.call_count, 2)
        for name in ("productionDispatch", "privateDatabaseRead",
                     "registryMutation", "scheduledFollowUp"):
            self.assertFalse(result[name])

    def test_invalid_context_blocks_before_get(self):
        for field, value in (
            ("GITHUB_REPOSITORY", "chaonpu/Stock"),
            ("GITHUB_EVENT_NAME", "workflow_dispatch"),
            ("GITHUB_REF", "refs/heads/other"),
            ("GITHUB_WORKFLOW_REF", "chaonpu/Runner/.github/workflows/production-dispatch.yml@refs/heads/main"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GH_TOKEN", ""),
        ):
            env = context()
            env[field] = value
            reader = Mock()
            with self.subTest(field=field), self.assertRaises(RegistryGuard):
                accept(env, read_registry=reader, audit=fake_audit)
            reader.assert_not_called()

    def test_untrustworthy_registry_sha_blocks(self):
        reader = Mock(return_value=({"schema":1,"tasks":[]}, "bad-sha"))
        with self.assertRaises(RegistryGuard):
            accept(context(), read_registry=reader, audit=fake_audit)

    def test_any_unsafe_audit_flag_is_rejected(self):
        for field, value in (
            ("dispatchAuthorized", True),
            ("registryWriteAuthorized", True),
            ("databaseWriteAuthorized", True),
            ("automaticRetryAuthorized", True),
            ("productionReady", True),
            ("exchangeSessionVerified", True),
            ("dateMode", "manual_override"),
            ("status", "production_ready"),
        ):
            def modified(*args, **kwargs):
                result = fake_audit(*args, **kwargs)
                result[field] = value
                return result
            with self.subTest(field=field), self.assertRaises(RegistryGuard):
                accept(context(), read_registry=fake_get, audit=modified)


if __name__ == "__main__":
    unittest.main()
