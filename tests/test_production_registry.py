import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from scheduler.production_registry import (
    RegistryGuard, bind_run, observe_run, reserve, validate
)

ROOT = Path(__file__).resolve().parents[1]
TIME = "2026-10-17T06:00:00+08:00"
SHA = "a" * 40


def configuration():
    cfg = json.loads((ROOT / "config/repositories.json").read_text(encoding="utf-8"))
    return cfg


def enabled_config():
    cfg = configuration()
    cfg["dispatchEnabled"] = True
    for task in cfg["tasks"].values():
        task["enabledForDispatch"] = True
    return cfg


def blank():
    return {"schema": 1, "tasks": []}


def pending(cfg=None, date="2026-10-19", task="stock_a"):
    cfg = cfg or enabled_config()
    return reserve(cfg, blank(), task, route="cloud",
                   coordinator_run_id=38967023305, at=TIME,
                   business_date=date)


def bound(cfg=None):
    cfg = cfg or enabled_config()
    s = pending(cfg)
    return bind_run(cfg, s, "stock_a:2026-10-19", run_id=38967039826, at=TIME)


def run(status="completed", conclusion="success", **kw):
    obj = {
        "id": 38967039826,
        "repository": {"full_name": "chaonpu/Stock"},
        "path": ".github/workflows/run-a-share.yml@main",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "run_attempt": 1,
        "status": status,
        "conclusion": conclusion,
    }
    obj.update(kw)
    return obj


class ProductionRegistryTests(unittest.TestCase):
    def test_registry_now_empty_and_production_disabled(self):
        cfg = configuration()
        state = json.loads((ROOT / "state/tasks.json").read_text(encoding="utf-8"))
        # Preserve the current on-disk schema, including its public-safe description.
        self.assertEqual(state.get("tasks"), [])
        self.assertIs(validate(cfg, state), state)
        self.assertEqual(len(validate(cfg, blank())["tasks"]), 0)
        with self.assertRaisesRegex(RegistryGuard, "production_dispatch_disabled"):
            reserve(cfg, blank(), "stock_a", route="cloud", coordinator_run_id=1,
                    business_date="2026-10-19", at=TIME)

    def test_reservation_is_a_pure_copy_and_bound_to_single_task(self):
        cfg, old = enabled_config(), blank()
        current = pending(cfg)
        self.assertEqual(old["tasks"], [])
        e = current["tasks"][0]
        self.assertEqual(e["id"], "stock_a:2026-10-19")
        self.assertEqual(e["status"], "reserved")
        self.assertNotIn("runId", e)

    def test_duplicate_reservation_blocked_even_after_completion_or_hold(self):
        cfg = enabled_config()
        state = pending(cfg)
        for status in ("reserved", "hold", "verified"):
            clone = copy.deepcopy(state)
            clone["tasks"][0]["status"] = status
            if status == "verified":
                clone["tasks"][0]["runId"] = 39999999
            with self.subTest(status=status):
                with self.assertRaisesRegex(RegistryGuard, "already_reserved"):
                    reserve(cfg, clone, "stock_a", route="cloud",
                            coordinator_run_id=20, business_date="2026-10-19", at=TIME)

    def test_bad_dates_and_unvalidated_windows_route_rejected(self):
        cfg = enabled_config()
        for bad in ("2026-02-30", "2026-13-03", "2026-10-1"):
            with self.subTest(date=bad), self.assertRaises(RegistryGuard):
                pending(cfg, bad)
        with self.assertRaisesRegex(RegistryGuard, "route_not_verified"):
            reserve(cfg, blank(), "stock_a", route="windows",
                    coordinator_run_id=1, business_date="2026-10-19", at=TIME)
        with self.assertRaisesRegex(RegistryGuard, "unapproved_task"):
            reserve(cfg, blank(), "unknown", route="cloud",
                    coordinator_run_id=1, business_date="2026-10-19", at=TIME)

    def test_android_source_sha_identity(self):
        cfg = enabled_config()
        state = reserve(cfg, blank(), "android", route="cloud",
                        coordinator_run_id=1, source_sha=SHA, at=TIME)
        self.assertEqual(state["tasks"][0]["id"], "android:" + SHA)
        with self.assertRaises(RegistryGuard):
            reserve(cfg, blank(), "android", route="cloud",
                    coordinator_run_id=1, source_sha="bad", at=TIME)

    def test_bind_is_one_time_and_requires_unique_run_id(self):
        cfg = enabled_config()
        state = pending(cfg)
        next_state = bind_run(cfg, state, "stock_a:2026-10-19",
                              run_id=38967039826, at=TIME)
        self.assertEqual(state["tasks"][0]["status"], "reserved")
        self.assertEqual(next_state["tasks"][0]["status"], "dispatched")
        with self.assertRaisesRegex(RegistryGuard, "run_binding_not_allowed"):
            bind_run(cfg, next_state, "stock_a:2026-10-19", run_id=38967039827, at=TIME)
        with self.assertRaisesRegex(RegistryGuard, "run_id_invalid"):
            bind_run(cfg, state, "stock_a:2026-10-19", run_id=0, at=TIME)

    def test_complete_run_never_equals_verified_ledger(self):
        cfg, state = enabled_config(), bound()
        result = observe_run(cfg, state, "stock_a:2026-10-19", run(), at=TIME)
        self.assertEqual(result["tasks"][0]["status"], "awaiting_effect_verification")
        self.assertEqual(state["tasks"][0]["status"], "dispatched")
        with self.assertRaisesRegex(RegistryGuard, "task_not_observable"):
            observe_run(cfg, result, "stock_a:2026-10-19", run(), at=TIME)

    def test_running_then_successful_completion(self):
        cfg, state = enabled_config(), bound()
        running = observe_run(cfg, state, "stock_a:2026-10-19",
                              run("in_progress", None), at=TIME)
        self.assertEqual(running["tasks"][0]["status"], "running")
        queued = observe_run(cfg, running, "stock_a:2026-10-19",
                             run("queued", None), at=TIME)
        self.assertEqual(queued["tasks"][0]["status"], "running")
        done = observe_run(cfg, queued, "stock_a:2026-10-19", run(), at=TIME)
        self.assertEqual(done["tasks"][0]["status"], "awaiting_effect_verification")

    def test_failures_quarantine_without_redispatch(self):
        cfg = enabled_config()
        for conclusion in ("failure", "cancelled", "timed_out", "skipped"):
            with self.subTest(conclusion=conclusion):
                result = observe_run(cfg, bound(), "stock_a:2026-10-19",
                                     run("completed", conclusion), at=TIME)
                self.assertEqual(result["tasks"][0]["status"], "hold")
                with self.assertRaisesRegex(RegistryGuard, "already_reserved"):
                    reserve(cfg, result, "stock_a", route="cloud",
                            coordinator_run_id=99, business_date="2026-10-19", at=TIME)

    def test_untrusted_run_provenance_rejected_without_mutation(self):
        cfg, original = enabled_config(), bound()
        bad_fields = [
            {"id": 1},
            {"repository": {"full_name": "chaonpu/ziwuxilv"}},
            {"path": ".github/workflows/run-us.yml@main"},
            {"event": "push"},
            {"head_branch": "untrusted"},
            {"run_attempt": 2},
        ]
        for changes in bad_fields:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(RegistryGuard, "run_provenance_unverified"):
                    observe_run(cfg, original, "stock_a:2026-10-19",
                                run(**changes), at=TIME)
        self.assertEqual(original["tasks"][0]["status"], "dispatched")

    def test_wrong_android_source_commit_is_not_observable(self):
        cfg = enabled_config()
        state = reserve(cfg, blank(), "android", route="cloud",
                        coordinator_run_id=1, source_sha=SHA, at=TIME)
        state = bind_run(cfg, state, "android:" + SHA, run_id=555, at=TIME)
        signed = {
            "id": 555, "repository": {"full_name": "anwpustudent-lab/Test"},
            "path": ".github/workflows/android.yml@main",
            "event": "workflow_dispatch", "head_branch": "main",
            "head_sha": "b"*40, "run_attempt": 1, "status": "completed",
            "conclusion": "success"
        }
        with self.assertRaisesRegex(RegistryGuard, "run_provenance_unverified"):
            observe_run(cfg, state, "android:" + SHA, signed, at=TIME)

    def test_registry_refuses_private_fields_and_identity_tampering(self):
        cfg = enabled_config()
        for key, value in [
            ("holdings", [{"symbol": "private"}]),
            ("id", "stock_a:2026-02-30"),
            ("repo", "chaonpu/ziwuxilv"),
            ("workflow", "run-us.yml"),
            ("route", "windows"),
            ("createdAt", "2026-10-19"),
        ]:
            with self.subTest(field=key):
                s = pending(cfg)
                s["tasks"][0][key] = value
                with self.assertRaises(RegistryGuard):
                    validate(cfg, s)


if __name__ == "__main__":
    unittest.main()
