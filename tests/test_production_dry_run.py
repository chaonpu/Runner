"""No-side-effect production workflow safety gate tests."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scheduler.production_dry_run import (
    check, context_allowed, WORKFLOW_REF, SUPPORTED
)
from scheduler.production_registry import RegistryGuard

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/repositories.json").read_text())
STATE = json.loads((ROOT / "state/tasks.json").read_text())
DAY = "2026-10-19"


def ctx():
    return {
        "GITHUB_REPOSITORY": "chaonpu/Runner",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTOR": "chaonpu",
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": "38972769647",
    }


def fake_route(config, task, **kw):
    return {
        "taskId": task + ":" + kw["business_date"],
        "registeredTask": {"status": "not_registered"},
        "recommendation": {"route": "cloud", "reason": "synthetic_available"},
        "execution": {"dispatchAuthorized": False},
    }


class ProductionDryRunTests(unittest.TestCase):
    def test_context_must_be_manual_owner_main_and_exact_workflow(self):
        context_allowed(ctx())
        for name, value in (
            ("GITHUB_ACTOR", "attacker"),
            ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REF", "refs/heads/other"),
            ("GITHUB_REPOSITORY", "chaonpu/Stock"),
            ("GITHUB_WORKFLOW_REF", "chaonpu/Runner/.github/workflows/scheduler.yml@refs/heads/main"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_RUN_ID", "0"),
        ):
            with self.subTest(name=name):
                x = ctx()
                x[name] = value
                with self.assertRaisesRegex(RegistryGuard, "context_untrusted"):
                    context_allowed(x)

    def test_exactly_a_and_us_have_access_to_locked_gate(self):
        self.assertEqual(SUPPORTED, {"stock_a", "stock_us"})
        for task in ("android", "stock_etf", "database", "release"):
            with self.subTest(task=task):
                with self.assertRaises(RegistryGuard):
                    check(CONFIG, STATE, task, DAY, preflight_fn=fake_route)

    def test_legal_dates_only(self):
        for invalid in ("2026-02-30", "2026-10-1", "2026-13-04",
                        "today", "../2026-10-19"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(RegistryGuard, "date_invalid"):
                    check(CONFIG, STATE, "stock_a", invalid, preflight_fn=fake_route)

    def test_auto_date_uses_market_timezone_on_beijing_saturday(self):
        # 2026-10-17 06:00 Shanghai is 2026-10-16 18:00 New York.
        utc_clock = datetime(2026, 10, 16, 22, tzinfo=timezone.utc)
        for task, zone in (
            ("stock_a", "Asia/Shanghai"),
            ("stock_us", "America/New_York"),
        ):
            with self.subTest(task=task):
                result = check(CONFIG, STATE, task, "", clock=utc_clock,
                               preflight_fn=fake_route)
                self.assertEqual(result["businessDate"], "2026-10-16")
                self.assertEqual(result["taskId"], task + ":2026-10-16")
                self.assertEqual(result["dateMode"], "auto_official_schedule_candidate")
                self.assertEqual(result["marketTimezone"], zone)
                self.assertFalse(result["exchangeSessionVerified"])
                self.assertFalse(result["dispatchAuthorized"])

    def test_auto_before_market_close_uses_previous_weekday(self):
        before_china_close = datetime(2026, 10, 19, 6, tzinfo=timezone.utc)
        after_china_close = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
        first = check(CONFIG, STATE, "stock_a", "", clock=before_china_close,
                      preflight_fn=fake_route)
        second = check(CONFIG, STATE, "stock_a", "", clock=after_china_close,
                       preflight_fn=fake_route)
        self.assertEqual(first["businessDate"], "2026-10-16")
        self.assertEqual(second["businessDate"], "2026-10-19")

    def test_us_dst_transition_changes_nominal_close_utc(self):
        # America/New_York uses EST in January and EDT in October.
        january_preclose = datetime(2026, 1, 5, 20, 30, tzinfo=timezone.utc)
        january_postclose = datetime(2026, 1, 5, 21, 30, tzinfo=timezone.utc)
        october_postclose = datetime(2026, 10, 19, 20, 30, tzinfo=timezone.utc)
        self.assertEqual(check(CONFIG, STATE, "stock_us", "", clock=january_preclose,
                               preflight_fn=fake_route)["businessDate"], "2026-01-02")
        self.assertEqual(check(CONFIG, STATE, "stock_us", "", clock=january_postclose,
                               preflight_fn=fake_route)["businessDate"], "2026-01-05")
        self.assertEqual(check(CONFIG, STATE, "stock_us", "", clock=october_postclose,
                               preflight_fn=fake_route)["businessDate"], "2026-10-19")

    def test_exchange_holiday_manual_audit_never_approves(self):
        result = check(CONFIG, STATE, "stock_a", "2026-10-01",
                       clock=datetime(2026, 10, 18, 22, tzinfo=timezone.utc),
                       preflight_fn=fake_route)
        self.assertEqual(result["calendarStatus"], "exchange_scheduled_closed")
        self.assertEqual(result["routeRecommendation"], "hold")
        self.assertFalse(result["publishedScheduleMatched"])
        self.assertFalse(result["scheduledSessionClosed"])
        self.assertFalse(result["marketDataReady"])
        self.assertFalse(result["dispatchAuthorized"])

    def test_unsupported_calendar_year_holds_before_any_remote_preflight(self):
        preflight_fn = Mock()
        result = check(CONFIG, STATE, "stock_us", "",
                       clock=datetime(2029, 1, 5, 23, tzinfo=timezone.utc),
                       preflight_fn=preflight_fn)
        self.assertEqual(result["status"], "hold_calendar_coverage_unavailable")
        self.assertFalse(result["dispatchAuthorized"])
        self.assertFalse(result["marketDataReady"])
        preflight_fn.assert_not_called()

    def test_preclose_date_suppresses_possible_cloud_route(self):
        before = datetime(2026, 10, 19, 6, tzinfo=timezone.utc)
        result = check(CONFIG, STATE, "stock_a", "2026-10-19",
                       clock=before, preflight_fn=fake_route)
        self.assertEqual(result["calendarStatus"], "published_session_not_yet_closed")
        self.assertEqual(result["routeRecommendation"], "hold")
        self.assertFalse(result["dispatchAuthorized"])

    def test_manual_date_retains_override_and_disables_auto(self):
        actual_clock = datetime(2026, 10, 16, 22, tzinfo=timezone.utc)
        result = check(CONFIG, STATE, "stock_us", "2026-10-15",
                       clock=actual_clock, preflight_fn=fake_route)
        self.assertEqual(result["businessDate"], "2026-10-15")
        self.assertEqual(result["dateMode"], "manual_override")
        self.assertFalse(result["exchangeSessionVerified"])
        self.assertFalse(result["dispatchAuthorized"])

    def test_naive_datetime_is_not_trusted(self):
        with self.assertRaisesRegex(RegistryGuard, "date_invalid"):
            check(CONFIG, STATE, "stock_us", "",
                  clock=datetime(2026, 10, 17, 4, 30), preflight_fn=fake_route)

    def test_good_a_and_us_both_return_unconditionally_no_dispatch(self):
        for task in ("stock_a", "stock_us"):
            with self.subTest(task=task):
                result = check(
                    CONFIG, STATE, task, DAY,
                    clock=datetime(2026, 10, 19, 23, tzinfo=timezone.utc),
                    preflight_fn=fake_route
                )
                self.assertEqual(result["taskId"], task + ":" + DAY)
                self.assertEqual(result["status"], "locked_read_only")
                self.assertEqual(result["routeRecommendation"], "cloud")
                for field in ("dispatchAuthorized", "registryWriteAuthorized",
                              "databaseWriteAuthorized", "automaticRetryAuthorized",
                              "productionReady"):
                    self.assertIs(result[field], False)
                self.assertNotIn("billing", result)
                self.assertNotIn("private", result)

    def test_any_production_switch_flipped_makes_gate_abort(self):
        for switch in ("global", "stock_a", "stock_us"):
            with self.subTest(switch=switch):
                modified = copy.deepcopy(CONFIG)
                if switch == "global":
                    modified["dispatchEnabled"] = True
                else:
                    modified["tasks"][switch]["enabledForDispatch"] = True
                preflight_fn = Mock()
                with self.assertRaisesRegex(RegistryGuard, "lock_is_not_off"):
                    check(modified, STATE, "stock_a", DAY, preflight_fn=preflight_fn)
                preflight_fn.assert_not_called()

    def test_bad_registry_never_reads_private_actions(self):
        preflight_fn = Mock()
        malicious = {"schema": 1, "tasks": [{
            "id": "stock_a:2026-10-19", "privateHoldings": [{"code": "EXAMPLE"}]}]}
        with self.assertRaises(RegistryGuard):
            check(CONFIG, malicious, "stock_a", DAY, preflight_fn=preflight_fn)
        preflight_fn.assert_not_called()

    def test_unknown_preflight_authorization_fails(self):
        def fake(config, task, **kwargs):
            a = fake_route(config, task, **kwargs)
            a["execution"]["dispatchAuthorized"] = True
            return a
        with self.assertRaisesRegex(RegistryGuard, "unexpected_preflight_authorization"):
            check(CONFIG, STATE, "stock_a", DAY, preflight_fn=fake)

    def test_stage_never_mutates_registry(self):
        original = copy.deepcopy(STATE)
        check(CONFIG, STATE, "stock_a", DAY, preflight_fn=fake_route)
        self.assertEqual(original, STATE)


if __name__ == "__main__":
    unittest.main()
