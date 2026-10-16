"""Foreground observation of one Stock no-op run; never issues a dispatch."""
import argparse
import json
import os
import sys
import time

from scheduler.pilot_registry import PilotGuard, key_for, load_state
from scheduler.pilot_monitor import classify
from scheduler.probes import GitHubReadOnly


def get_row(date, env, loader=load_state):
    state, _ = loader(env["GH_TOKEN"])
    row = next((r for r in state["tasks"] if r["id"] == key_for(date)), None)
    if row is None or row.get("status") not in (
        "dispatched", "reconciliation_required", "completed_success",
        "completed_failure"
    ):
        raise PilotGuard("registered_run_not_monitorable")
    if env.get("PILOT_MODE") == "dispatch" and str(row["coordinatorRunId"]) != env["GITHUB_RUN_ID"]:
        raise PilotGuard("dispatch_source_mismatch")
    return row


def observe(row, reader, pause=time.sleep, now=time.monotonic,
            interval=30, deadline_seconds=3000):
    if row["status"] in ("completed_success", "completed_failure"):
        return row["status"]
    rid = row.get("runId")
    if type(rid) is not int or rid <= 0:
        raise PilotGuard("run_id_missing_manual_reconciliation")
    if not 15 <= interval <= 120 or not 60 <= deadline_seconds <= 19800:
        raise PilotGuard("invalid_watch_settings")
    deadline = now() + deadline_seconds
    errors = 0
    last = None
    while now() < deadline:
        try:
            run = reader(f"/repos/chaonpu/Stock/actions/runs/{rid}")
            if run.get("head_branch") != "main":
                raise PilotGuard("run_branch_not_verified")
            status, reason = classify(row, run)
            if status == "hold" and reason != "pilot_failed_check_runner":
                raise PilotGuard("run_identity_or_state_unverified")
            errors = 0
        except PilotGuard:
            raise  # An untrusted run identity is not a transient API outage.
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors += 1
            if errors >= 3:
                raise PilotGuard("run_status_unavailable_manual_reconciliation") from exc
            pause(min(interval, max(0, deadline - now())))
            continue
        if status != last:
            print(json.dumps({"runId": rid, "watchState": status}), flush=True)
            last = status
        if status == "pilot_completed":
            return "completed_success"
        if (status == "hold" and reason == "pilot_failed_check_runner"
                and run.get("status") == "completed"
                and run.get("conclusion") in ("failure", "cancelled", "timed_out",
                                             "action_required", "startup_failure",
                                             "neutral", "skipped")):
            return "completed_failure"
        pause(min(interval, max(0, deadline - now())))
    raise PilotGuard("monitor_deadline_manual_reconciliation")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    args = p.parse_args()
    env = os.environ
    if (env.get("GITHUB_REPOSITORY") != "chaonpu/Runner"
        or env.get("GITHUB_REF") != "refs/heads/main"
        or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or env.get("GITHUB_ACTOR") != "chaonpu"
        or env.get("PILOT_MODE") not in ("dispatch", "monitor_only")
        or not env.get("GH_TOKEN") or not env.get("CHAONPU_ACTIONS_READ_TOKEN")):
        print('{"monitor":"blocked_untrusted_context"}')
        return 2
    try:
        row = get_row(args.date, env)
        state = observe(row, GitHubReadOnly(env["CHAONPU_ACTIONS_READ_TOKEN"]).get)
    except PilotGuard as exc:
        print(json.dumps({"monitor":"stopped_for_manual_review","reason":str(exc)}))
        return 2
    print(json.dumps({"monitor":"completed","status":state,"dispatched":0}))
    return 0 if state == "completed_success" else 2


if __name__ == "__main__":
    sys.exit(main())
