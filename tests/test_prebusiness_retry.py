"""Regression cases for one-shot attested pre-business Git-route recovery."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scheduler.production_ports import ProductionPorts
from scheduler.production_registry import RegistryGuard, validate
from scheduler import production_coordinator as c

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-10-26"
TASK = "stock_a"
KEY = TASK + ":" + DAY
SHA = "96d6f256f130aa1987d2af02ba3c808bac4e0885"
RUN = 40284175857
OWNER = 40283227241
ROUTE = "Prepare verified job-scoped GitHub route before Windows checkout"


def data():
    cfg = json.loads((ROOT / "config/repositories.json").read_text(encoding="utf-8"))
    assert cfg["dispatchEnabled"] is False
    row = {
        "id": KEY, "task": TASK, "repo": "chaonpu/Stock",
        "workflow": "run-a-share.yml", "route": "windows",
        "status": "hold", "coordinatorRunId": OWNER, "runId": RUN,
        "sourceSha": SHA, "publicationMode": "formal",
        "createdAt": "2026-10-26T07:10:11+00:00",
        "updatedAt": "2026-10-26T07:14:17+00:00",
    }
    state = {"schema": 1, "tasks": [row]}
    validate(cfg, state)
    return cfg, state


def run():
    return {
        "id": RUN, "repository": {"full_name": "chaonpu/Stock"},
        "path": ".github/workflows/run-a-share.yml@refs/heads/main",
        "event": "workflow_dispatch", "head_branch": "main",
        "head_sha": SHA, "run_attempt": 1,
        "actor": {"login": "chaonpu"},
        "status": "completed", "conclusion": "failure",
    }


def jobs():
    return {"total_count": 1, "jobs": [{
        "run_id": RUN, "name": f"effect-v1:{KEY}:{OWNER}:formal",
        "status": "completed", "conclusion": "failure",
        "steps": [
            {"name": ROUTE, "conclusion": "failure"},
            {"name": "Run A-share strategy with retry", "conclusion": "skipped"},
            {"name": "Publish verified snapshot and ledger to private database", "conclusion": "skipped"},
            {"name": "Save successful strategy state", "conclusion": "skipped"},
            {"name": "Verify and attest published ledger receipt", "conclusion": "skipped"},
            {"name": "Send A-share mail only after verified ledger publication", "conclusion": "skipped"},
            {"name": "Complete job", "conclusion": "success"},
        ],
    }]}


class RecoveryTests(unittest.TestCase):
    def fixture(self, *, state=None, run_value=None, job_value=None):
        cfg, original = data()
        memory = [deepcopy(original if state is None else state)]
        target = ProductionPorts.__new__(ProductionPorts)
        target.env = {"GH_TOKEN": "synthetic"}
        target.load = Mock(side_effect=lambda: (cfg, deepcopy(memory[0]), "a" * 40))
        target.run = Mock(return_value=run() if run_value is None else run_value)
        reader = Mock()
        reader.get.return_value = jobs() if job_value is None else job_value
        target.private_reader = Mock(return_value=reader)

        def requester(token, method, path, body):
            self.assertEqual(method, "PUT")
            self.assertEqual(body["sha"], "a" * 40)
            self.assertIn("/contents/state/tasks.json", path)
            memory[0] = json.loads(base64.b64decode(body["content"]).decode())
            return {"content": {"sha": "b" * 40}}

        target.requester = Mock(side_effect=requester)
        return cfg, target, memory

    def retire(self, obj):
        with patch("scheduler.production_ports.context", return_value=OWNER):
            return obj.retire_prebusiness_hold(TASK, DAY)

    def test_verified_route_failure_is_archived_and_retry_unblocked(self):
        cfg, obj, memory = self.fixture()
        result = self.retire(obj)
        self.assertTrue(result["archived"])
        self.assertEqual(result["dispatches"], 0)
        self.assertEqual(result["originalRunId"], RUN)
        self.assertEqual(memory[0]["tasks"], [])
        self.assertEqual(memory[0]["retiredPrebusinessFailures"], data()[1]["tasks"])
        self.assertIs(validate(cfg, memory[0]), memory[0])
        obj.requester.assert_called_once()

    def test_second_recovery_for_same_session_denied(self):
        cfg, obj, memory = self.fixture()
        self.retire(obj)
        memory[0]["tasks"] = [deepcopy(data()[1]["tasks"][0])]
        with self.assertRaisesRegex(RegistryGuard, "once_per_session"):
            self.retire(obj)
        self.assertEqual(obj.requester.call_count, 1)

    def test_denies_uncertain_or_changed_run_without_writes(self):
        scenarios = []
        for key, value in [("run_attempt", 2), ("head_sha", "f"*40),
                           ("status", "in_progress"), ("conclusion", "cancelled"),
                           ("path", ".github/workflows/run-us.yml@main")]:
            evidence = run()
            evidence[key] = value
            scenarios.append((key, evidence, jobs()))
        stale = jobs()
        stale["jobs"][0]["steps"][1]["conclusion"] = "success"
        scenarios.append(("strategy_executed", run(), stale))
        foreign = jobs()
        foreign["jobs"][0]["steps"][0]["name"] = "Unknown failed phase"
        scenarios.append(("unknown_failure", run(), foreign))
        post = jobs()
        post["jobs"][0]["steps"].insert(1, {
            "name": "Unexpected write", "conclusion": "success",
        })
        scenarios.append(("post_failure_work", run(), post))
        for label, evidence, job_trace in scenarios:
            with self.subTest(label=label):
                _, obj, memory = self.fixture(run_value=evidence, job_value=job_trace)
                with self.assertRaises(RegistryGuard):
                    self.retire(obj)
                obj.requester.assert_not_called()
                self.assertNotIn("retiredPrebusinessFailures", memory[0])

    def test_non_hold_does_not_recover(self):
        cfg, state = data()
        state["tasks"][0]["status"] = "verified"
        _, obj, _ = self.fixture(state=state)
        with self.assertRaises(RegistryGuard):
            self.retire(obj)
        obj.requester.assert_not_called()

    def test_coordinator_after_attested_retirement_still_checks_readiness(self):
        cfg, state = data()
        retired = deepcopy(state)
        retired["retiredPrebusinessFailures"] = retired.pop("tasks")
        retired["tasks"] = []
        ports = Mock()
        ports.load.side_effect = [
            (cfg, state, "a"*40), (cfg, retired, "b"*40),
        ]
        ports.retire_prebusiness_hold.return_value = {
            "status": "prebusiness_recovery_attested",
            "archived": True, "dispatches": 0, "businessVerified": False,
        }
        env = {
            "GITHUB_WORKFLOW_REF": "chaonpu/Runner/.github/workflows/production-dispatch.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_ACTOR": "chaonpu",
            "GITHUB_REF": "refs/heads/main",
        }
        with patch.object(c, "ProductionPorts", return_value=ports), \
             patch.object(c, "prepare", return_value={
                 "alreadyCurrent": True, "readinessRunId": 123,
             }) as ready, \
             patch("sys.stdout"):
            out = c.execute(env, TASK, DAY, "formal")
        self.assertEqual(out["status"], "skipped")
        ports.retire_prebusiness_hold.assert_called_once_with(TASK, DAY)
        ready.assert_called_once()
        self.assertEqual(ports.load.call_count, 2)


if __name__ == "__main__":
    unittest.main()
