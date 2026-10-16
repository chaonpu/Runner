"""Finalize successful Stock no-op pilot runs in the PUBLIC registry only.

Safe write: update existing pilot state from 'dispatched' to
'completed_success' after live GitHub Actions run provenance checks.
There are no calls to Stock strategy, database, release or redispatch APIs.
GitHub Contents API SHA compare-and-swap prevents lost updates.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

from scheduler.pilot_registry import PilotGuard, load_state, save_state, validate_registry
from scheduler.pilot_monitor import classify, REPO
from scheduler.probes import GitHubReadOnly


def finalizable(row: dict, run: dict) -> bool:
    if row.get("status") not in ("dispatched", "reconciliation_required"):
        return False
    if run.get("head_branch") != "main":
        return False
    outcome, _ = classify(row, run)
    return outcome == "pilot_completed"


def plan_completion(state: dict, run_getter, now: str) -> tuple[dict, list[str]]:
    state = validate_registry(state)
    result = json.loads(json.dumps(state))
    verified = []
    for row in result["tasks"]:
        if row["status"] not in ("dispatched", "reconciliation_required"):
            continue
        rid = row.get("runId")
        if type(rid) is not int or rid < 1:
            continue
        # API outages/errors abort before writes. Never infer success from
        # timestamps, failed queries, or another workflow's green status.
        run = run_getter(f"/repos/{REPO}/actions/runs/{rid}")
        if finalizable(row, run):
            row["status"] = "completed_success"
        elif (run.get("head_branch") == "main"
              and classify(row, run) == ("hold", "pilot_failed_check_runner")
              and run.get("status") == "completed"
              and run.get("conclusion") in (
                  "failure", "cancelled", "timed_out",
                  "startup_failure", "action_required", "skipped", "neutral"
              )):
            row["status"] = "completed_failure"
        else:
            continue
        row["updatedAt"] = now
        verified.append(row["id"])
    return validate_registry(result), verified


def finalize(env: dict,
             loader=load_state, saver=save_state,
             client_factory=GitHubReadOnly) -> dict:
    if (env.get("GITHUB_REPOSITORY") != "chaonpu/Runner" or
        env.get("GITHUB_REF") != "refs/heads/main" or
        env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"):
        raise PilotGuard("untrusted_finalize_context")
    public_token = env.get("GH_TOKEN")
    private_token = env.get("CHAONPU_ACTIONS_READ_TOKEN")
    if not public_token or not private_token:
        raise PilotGuard("finalize_credentials_missing")
    previous, sha = loader(public_token)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    output, done = plan_completion(previous, client_factory(private_token).get, now)
    if not done:
        return {"mode":"no_op_pilot_finalization", "updated":0, "dispatches":0}
    # A race / previous uncertain PUT remains an error, never blindly retries.
    saver(public_token, output, sha, "Finish verified Stock pilot tasks")
    return {"mode":"no_op_pilot_finalization", "updated":len(done),
            "completedTaskIds":done,"dispatches":0}


def main() -> int:
    try:
        result=finalize(os.environ)
        print(json.dumps(result,sort_keys=True,ensure_ascii=False))
        return 0
    except (PilotGuard,OSError,ValueError,TypeError,KeyError) as exc:
        code = str(exc) if isinstance(exc,PilotGuard) else "finalize_unverified_or_unavailable"
        print(json.dumps({"status":"hold","reason":code,"dispatches":0}))
        return 2

if __name__=="__main__":
    sys.exit(main())
