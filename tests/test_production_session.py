"""Synthetic end-to-end failure tests. No network, credentials or private data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from scheduler.production_registry import RegistryGuard
from scheduler.production_session import start, observe, WORKFLOW_REF
from scheduler.production_store import verify_change

ROOT = Path(__file__).resolve().parents[1]
OWNER, TARGET = 90000000111, 90000000222
SOURCE, DAY = "a" * 40, "2026-10-16"


def environment():
    return {"GITHUB_REPOSITORY": "chaonpu/Runner", "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
            "GITHUB_ACTOR": "chaonpu", "GITHUB_RUN_ID": str(OWNER),
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "b" * 40}


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.value += timedelta(seconds=1)
        return self.value


class MemoryPorts:
    """Only adapter used by the new core. Intentionally in the TEST file."""
    def __init__(self):
        self.config = json.loads((ROOT / "config/repositories.json").read_text())
        self.config["dispatchEnabled"] = True
        self.config["tasks"]["stock_us"]["enabledForDispatch"] = True
        self.config["tasks"]["stock_us"]["windowsWorkflow"] = "run-us.yml"
        self.state = {"schema": 1, "tasks": []}
        self.sha = "0" * 40
        self.events, self.writes, self.dispatches = [], 0, []
        self.audit_count = 0
        self.audit_value = {
            "taskId": "stock_us:" + DAY, "activeRunCheck": "no_active_run",
            "calendarCoverageCurrent": True, "scheduledSessionClosed": True,
            "exchangeSessionVerified": True, "marketDataReady": True,
            "sourceSha": SOURCE, "observedAt": "2026-10-17T06:00:00+00:00",
            "billing": {"chaonpu": {"status": "exhausted", "independentlyVerified": True,
                "remainingFreeMinutes": 0, "period": "2026-10", "observedAt": "2026-10-17T06:00:00+00:00"}}, "runners": {"chaonpu/Stock": {
                "name": "ziwuxilv-stock-win", "status": "online",
                "busy": False, "labelsVerified": True}},
        }
        self.run_value = {
            "id": TARGET, "repository": {"full_name": "chaonpu/Stock"},
            "path": ".github/workflows/run-us.yml", "event": "workflow_dispatch",
            "head_branch": "main", "run_attempt": 1, "head_sha": SOURCE,
            "status": "queued", "conclusion": None,
        }
        self.response = {"workflow_run_id": TARGET}
        self.fail_write, self.confirm_drift = None, False
        self.post_error, self.read_error, self.audit_drift = False, False, None

    def load(self):
        self.events.append("load")
        return deepcopy((self.config, self.state, self.sha))

    def save(self, config, before, after, sha, env):
        self.events.append("save")
        self.writes += 1
        verify_change(config, before, after, int(env["GITHUB_RUN_ID"]))
        if self.fail_write == self.writes or sha != self.sha:
            raise OSError("private raw error must never escape")
        self.state = deepcopy(after)
        self.sha = str(self.writes) * 40
        if self.confirm_drift:
            self.config["dispatchEnabled"] = False

    def audit(self, config, state, task, day):
        self.events.append("audit")
        self.audit_count += 1
        audit = deepcopy(self.audit_value)
        if self.audit_count == 2 and self.audit_drift:
            audit.update(self.audit_drift)
        return audit

    def dispatch(self, repo, workflow, payload):
        self.events.append("dispatch")
        self.dispatches.append((repo, workflow, deepcopy(payload)))
        if self.post_error:
            raise TimeoutError("do not print token or private response")
        return deepcopy(self.response)

    def run(self, repo, rid):
        self.events.append("run")
        if self.read_error:
            raise OSError("private response")
        return deepcopy(self.run_value)


class ProductionSessionTests(unittest.TestCase):
    def setUp(self):
        self.ports, self.clock, self.env = MemoryPorts(), Clock(), environment()

    def begin(self):
        return start(self.ports, "stock_us", DAY, self.env, clock=self.clock)

    def test_end_to_end_reserve_bind_running_success_stops_before_verified(self):
        session = self.begin()
        self.assertEqual(self.ports.events, ["load", "audit", "save", "load", "audit",
                                            "dispatch", "run", "save", "load"])
        self.assertEqual(self.ports.dispatches, [("chaonpu/Stock", "run-us.yml", {
            "ref": "main", "return_run_details": True, "inputs": {
                "task_id": "stock_us:" + DAY, "coordinator_run_id": str(OWNER),
                "force_run": "false", "runner_mode": "windows"}})])
        for state, conclusion, expected in (("in_progress", None, "running"),
                                             ("completed", "success", "awaiting_effect_verification")):
            self.ports.run_value.update(status=state, conclusion=conclusion)
            result = observe(self.ports, session, self.env, clock=self.clock)
            self.assertEqual(result, {"status": expected, "businessVerified": False,
                                      "safeToRetry": False})
        self.assertEqual(self.ports.writes, 4)
        events = len(self.ports.events)
        observe(self.ports, session, self.env, clock=self.clock)
        self.assertEqual(self.ports.events[events:], ["load"])
        self.assertEqual(len(self.ports.dispatches), 1)

    def test_actual_disabled_configuration_never_writes_or_dispatches(self):
        self.ports.config = json.loads((ROOT / "config/repositories.json").read_text())
        with self.assertRaisesRegex(RegistryGuard, "production_disabled"):
            self.begin()
        self.assertEqual(self.ports.events, ["load"])

    def test_untrusted_context_never_touches_a_port(self):
        for field, value in (("GITHUB_EVENT_NAME", "push"), ("GITHUB_ACTOR", "other"),
                             ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_WORKFLOW_REF", "other"),
                             ("GITHUB_SHA", "bad"), ("GITHUB_RUN_ID", "1")):
            with self.subTest(field=field):
                env = dict(self.env, **{field: value})
                with self.assertRaisesRegex(RegistryGuard, "context_untrusted"):
                    start(self.ports, "stock_us", DAY, env, clock=self.clock)
        self.assertEqual(self.ports.events, [])

    def test_reserved_task_blocks_second_invocation(self):
        self.begin()
        with self.assertRaisesRegex(RegistryGuard, "already_reserved"):
            self.begin()
        self.assertEqual(len(self.ports.dispatches), 1)

    def test_failed_reservation_cas_never_posts(self):
        self.ports.fail_write = 1
        with self.assertRaisesRegex(RegistryGuard, "port_uncertain_no_retry"):
            self.begin()
        self.assertEqual(self.ports.writes, 1)
        self.assertEqual(self.ports.dispatches, [])

    def test_reservation_confirmation_drift_never_posts(self):
        self.ports.confirm_drift = True
        with self.assertRaisesRegex(RegistryGuard, "confirmation_mismatch"):
            self.begin()
        self.assertEqual(self.ports.state["tasks"][0]["status"], "reserved")
        self.assertEqual(self.ports.dispatches, [])

    def test_source_or_route_change_after_reservation_never_posts(self):
        for drift in ({"sourceSha": "c" * 40}, {"runners": {}},
                      {"activeRunCheck": "active_or_unknown"},
                      {"marketDataReady": False}):
            self.setUp()
            self.ports.audit_drift = drift
            with self.assertRaises(RegistryGuard):
                self.begin()
            self.assertEqual(self.ports.state["tasks"][0]["status"], "reserved")
            self.assertEqual(self.ports.dispatches, [])

    def test_dispatch_timeout_remains_reserved_and_cannot_retry(self):
        self.ports.post_error = True
        with self.assertRaisesRegex(RegistryGuard, "port_uncertain_no_retry"):
            self.begin()
        self.assertEqual(self.ports.state["tasks"][0]["status"], "reserved")
        with self.assertRaisesRegex(RegistryGuard, "already_reserved"):
            self.begin()
        self.assertEqual(len(self.ports.dispatches), 1)

    def test_missing_run_id_or_boolean_run_id_never_triggers_lookup_or_retry(self):
        for response in ({}, {"workflow_run_id": True}, {"workflow_run_id": "90000000222"}):
            self.setUp()
            self.ports.response = response
            with self.assertRaisesRegex(RegistryGuard, "run_id_unknown_no_retry"):
                self.begin()
            self.assertEqual(len(self.ports.dispatches), 1)
            self.assertNotIn("run", self.ports.events)
            self.assertEqual(self.ports.writes, 1)

    def test_source_drift_or_wrong_run_origin_after_post_is_never_bound(self):
        for changes in ({"head_sha": "c" * 40}, {"id": TARGET + 1},
                        {"event": "push"}, {"run_attempt": 2},
                        {"path": ".github/workflows/run-a-share.yml"},
                        {"repository": {"full_name": "another/repo"}}):
            self.setUp()
            self.ports.run_value.update(changes)
            with self.assertRaises(RegistryGuard):
                self.begin()
            self.assertEqual(self.ports.writes, 1)
            self.assertNotIn("runId", self.ports.state["tasks"][0])
            self.assertEqual(len(self.ports.dispatches), 1)

    def test_read_failure_after_post_does_not_relaunch(self):
        self.ports.read_error = True
        with self.assertRaisesRegex(RegistryGuard, "port_uncertain_no_retry"):
            self.begin()
        self.assertEqual(self.ports.writes, 1)
        self.assertEqual(len(self.ports.dispatches), 1)

    def test_known_conflicting_dispatch_inputs_are_never_bound(self):
        good = {"task_id": "stock_us:" + DAY, "coordinator_run_id": str(OWNER),
                "force_run": "false", "runner_mode": "windows"}
        for changes in ({"task_id": "stock_us:2026-10-15"}, {"coordinator_run_id": "1"},
                        {"force_run": "true"}, {"runner_mode": "ubuntu"}):
            self.setUp()
            self.ports.run_value["inputs"] = dict(good, **changes)
            with self.assertRaisesRegex(RegistryGuard, "dispatch_inputs_conflict"):
                self.begin()
            self.assertEqual(self.ports.writes, 1)
            self.assertEqual(len(self.ports.dispatches), 1)

    def test_binding_cas_failure_does_not_relaunch(self):
        self.ports.fail_write = 2
        with self.assertRaisesRegex(RegistryGuard, "port_uncertain_no_retry"):
            self.begin()
        with self.assertRaisesRegex(RegistryGuard, "already_reserved"):
            self.begin()
        self.assertEqual(self.ports.writes, 2)
        self.assertEqual(len(self.ports.dispatches), 1)

    def test_queued_observation_creates_no_heartbeat_commits(self):
        session = self.begin()
        for _ in range(3):
            self.assertEqual(observe(self.ports, session, self.env, clock=self.clock)["status"],
                             "dispatched")
        self.assertEqual(self.ports.writes, 2)
        self.assertEqual(len(self.ports.dispatches), 1)

    def test_failure_is_held_without_retry(self):
        for conclusion in ("failure", "cancelled", "timed_out", "skipped"):
            self.setUp()
            session = self.begin()
            self.ports.run_value.update(status="completed", conclusion=conclusion)
            self.assertEqual(observe(self.ports, session, self.env, clock=self.clock)["status"],
                             "hold")
            with self.assertRaisesRegex(RegistryGuard, "already_reserved"):
                self.begin()
            self.assertEqual(len(self.ports.dispatches), 1)

    def test_a_market_uses_its_own_workflow_and_task_identity(self):
        self.ports.config["tasks"]["stock_a"].update(
            enabledForDispatch=True, windowsWorkflow="run-a-share.yml")
        self.ports.audit_value["taskId"] = "stock_a:" + DAY
        self.ports.run_value["path"] = ".github/workflows/run-a-share.yml"
        start(self.ports, "stock_a", DAY, self.env, clock=self.clock)
        repo, workflow, payload = self.ports.dispatches[0]
        self.assertEqual((repo, workflow), ("chaonpu/Stock", "run-a-share.yml"))
        self.assertEqual(payload["inputs"]["task_id"], "stock_a:" + DAY)

    def test_fast_completion_is_bound_before_effect_review(self):
        self.ports.run_value.update(status="completed", conclusion="success")
        session = self.begin()
        self.assertEqual(self.ports.state["tasks"][0]["status"], "dispatched")
        result = observe(self.ports, session, self.env, clock=self.clock)
        self.assertEqual(result["status"], "awaiting_effect_verification")
        self.assertFalse(result["businessVerified"])

    def test_observation_write_failure_never_dispatches_again(self):
        session = self.begin()
        self.ports.run_value.update(status="completed", conclusion="success")
        self.ports.fail_write = 3
        with self.assertRaisesRegex(RegistryGuard, "port_uncertain_no_retry"):
            observe(self.ports, session, self.env, clock=self.clock)
        self.assertEqual(self.ports.state["tasks"][0]["status"], "dispatched")
        self.assertEqual(len(self.ports.dispatches), 1)

    def test_wrong_owner_or_bound_id_cannot_observe(self):
        session = self.begin()
        for changes in ({"coordinatorRunId": OWNER + 1}, {"runId": TARGET + 1}):
            with self.assertRaises(RegistryGuard):
                observe(self.ports, dict(session, **changes), self.env, clock=self.clock)
        self.assertEqual(self.ports.writes, 2)

    def test_observed_source_drift_never_updates_registry(self):
        session = self.begin()
        self.ports.run_value.update(head_sha="c" * 40, status="completed", conclusion="success")
        with self.assertRaisesRegex(RegistryGuard, "observed_source_mismatch"):
            observe(self.ports, session, self.env, clock=self.clock)
        self.assertEqual(self.ports.writes, 2)

    def test_missing_stale_future_or_wrong_date_readiness_is_rejected(self):
        for changes in ({"taskId": "stock_us:2026-10-15"}, {"marketDataReady": False},
                        {"exchangeSessionVerified": False}, {"calendarCoverageCurrent": False},
                        {"activeRunCheck": "unknown"}, {"sourceSha": "bad"},
                        {"observedAt": "2026-10-17T05:54:59+00:00"},
                        {"observedAt": "2026-10-17T07:00:00+00:00"},
                        {"observedAt": "2026-10-17T06:00:00"}, {"observedAt": None}):
            self.setUp()
            self.ports.audit_value.update(changes)
            with self.assertRaises(RegistryGuard):
                self.begin()
            self.assertEqual(self.ports.writes, 0)
            self.assertEqual(self.ports.dispatches, [])

    def test_cloud_requires_verified_fresh_task_budget(self):
        self.ports.audit_value["billing"] = {"chaonpu": {
            "status": "available", "independentlyVerified": True,
            "remainingFreeMinutes": 100, "period": "2026-10",
            "observedAt": "2026-10-17T06:00:00+00:00"}}
        self.begin()
        self.assertEqual(self.ports.dispatches[0][2]["inputs"]["runner_mode"], "ubuntu")
        self.assertEqual(self.ports.state["tasks"][0]["route"], "cloud")

    def test_unknown_or_insufficient_cloud_budget_never_implies_available(self):
        for budget in ({"status": "available", "remainingFreeMinutes": 2000},
                       {"status": "available", "independentlyVerified": True,
                        "remainingFreeMinutes": 99, "period": "2026-10",
                        "observedAt": "2026-10-17T06:00:00+00:00"}):
            self.setUp()
            self.ports.audit_value["runners"] = {}
            self.ports.audit_value["billing"] = {"chaonpu": budget}
            with self.assertRaisesRegex(RegistryGuard, "no_verified_route"):
                self.begin()
            self.assertEqual(self.ports.writes, 0)

    def test_only_exact_stock_workflows_supported(self):
        for task in ("android", "stock_etf", "unknown"):
            with self.assertRaisesRegex(RegistryGuard, "task_invalid"):
                start(self.ports, task, DAY, self.env, clock=self.clock)
        self.assertEqual(self.ports.events, [])

    def test_workflow_execution_requires_explicit_flag_and_always_closes_lease(self):
        workflow = (ROOT / ".github/workflows/production-dispatch.yml").read_text()
        self.assertNotIn("production_session", workflow)
        self.assertIn("execute:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("scheduler.production_coordinator", workflow)
        self.assertIn("if: always() && inputs.execute == true", workflow)
        self.assertIn("scheduler.production_lease", workflow)


if __name__ == "__main__":
    unittest.main()
