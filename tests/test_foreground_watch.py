import unittest
from unittest.mock import Mock
from scheduler.foreground_watch import observe, get_row
from scheduler.pilot_registry import PilotGuard, reserve

def row(status="dispatched"):
    s = reserve({"schema": 1, "tasks": []}, "2026-10-18", 77777777, "now")
    s["tasks"][0]["status"] = status
    s["tasks"][0]["runId"] = 88888888
    return s["tasks"][0]

def gh_run(status="completed", conclusion="success"):
    return {"id": 88888888, "repository": {"full_name": "chaonpu/Stock"},
            "path": ".github/workflows/runner-dispatch-canary.yml@main",
            "event": "workflow_dispatch", "head_branch": "main",
            "status": status, "conclusion": conclusion}

class FakeClock:
    def __init__(self): self.value = 0
    def now(self): return self.value
    def sleep(self, duration): self.value += duration

class ForegroundWatchTests(unittest.TestCase):
    def test_completed_success_returns_in_same_job(self):
        timer = FakeClock()
        get = Mock(side_effect=[gh_run("queued", None),
                                gh_run("in_progress", None), gh_run()])
        result = observe(row(), get, sleep=timer.sleep, now=timer.now,
                         interval=15, deadline_seconds=60)
        self.assertEqual(result, "completed_success")
        self.assertEqual(get.call_count, 3)
        self.assertEqual(timer.value, 30)

    def test_failed_run_exits_without_restart(self):
        timer = FakeClock()
        get = Mock(return_value=gh_run("completed", "failure"))
        result = observe(row(), get, sleep=timer.sleep, now=timer.now,
                         interval=15, deadline_seconds=60)
        self.assertEqual(result, "completed_failure")
        get.assert_called_once()

    def test_cancelled_run_finishes_failure(self):
        for conclusion in ("cancelled", "timed_out", "action_required"):
            timer = FakeClock()
            result = observe(row(), lambda _: gh_run("completed", conclusion),
                             sleep=timer.sleep, now=timer.now,
                             interval=15, deadline_seconds=60)
            self.assertEqual(result, "completed_failure")

    def test_wrong_workflow_cannot_appear_completed(self):
        bad = gh_run()
        bad["path"] = ".github/workflows/run-us.yml@main"
        timer = FakeClock()
        with self.assertRaisesRegex(PilotGuard, "run_status_unavailable|identity"):
            observe(row(), lambda _: bad, sleep=timer.sleep, now=timer.now,
                    interval=15, deadline_seconds=60)

    def test_api_outage_stops_no_dispatch(self):
        timer = FakeClock()
        def fail(_): raise OSError("token never printed")
        with self.assertRaisesRegex(PilotGuard, "run_status_unavailable"):
            observe(row(), fail, sleep=timer.sleep, now=timer.now,
                    interval=15, deadline_seconds=60)
        self.assertEqual(timer.value, 30)

    def test_wait_timeout_exits_no_background_process(self):
        timer = FakeClock()
        with self.assertRaisesRegex(PilotGuard, "monitor_deadline"):
            observe(row(), lambda _: gh_run("queued", None),
                    sleep=timer.sleep, now=timer.now,
                    interval=15, deadline_seconds=60)
        self.assertEqual(timer.value, 60)

    def test_idempotent_completed_status_returns_immediately(self):
        get = Mock()
        self.assertEqual(observe(row("completed_success"), get),
                         "completed_success")
        get.assert_not_called()

    def test_dispatch_owner_binds_current_run_id(self):
        registry = {"schema":1, "tasks":[row()]}
        loader = lambda _: (registry, "x" * 40)
        env = {"GH_TOKEN":"fake","PILOT_MODE":"dispatch",
               "GITHUB_RUN_ID":"77777777"}
        self.assertEqual(get_row("2026-10-18", env, loader)["runId"],88888888)
        env["GITHUB_RUN_ID"]="99999999"
        with self.assertRaisesRegex(PilotGuard, "dispatch_source_mismatch"):
            get_row("2026-10-18", env, loader)
        env["PILOT_MODE"]="monitor_only"
        self.assertEqual(get_row("2026-10-18", env, loader)["runId"],88888888)

if __name__=="__main__":
    unittest.main()
