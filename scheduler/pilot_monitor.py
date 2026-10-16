"""Read-only 5-minute reconciliation for persisted Stock no-op pilot runs.

Never rewrites Git, never reissues a pilot, never calls strategy workflows.
A successful no-op run is a coordinator test, NOT a verified strategy/ledger.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.error
from pathlib import Path

from scheduler.pilot_registry import validate_registry
from scheduler.probes import GitHubReadOnly,safe_error

ROOT=Path(__file__).resolve().parents[1]
REPO="chaonpu/Stock"
WORKFLOW=".github/workflows/runner-dispatch-canary.yml"

def classify(entry, run):
    if run.get("id")!=entry["runId"] or run.get("repository",{}).get("full_name")!=REPO:
        return "hold","run_repository_or_id_mismatch"
    if run.get("path","").split("@")[0]!=WORKFLOW or run.get("event")!="workflow_dispatch":
        return "hold","workflow_not_allowed"
    status=run.get("status")
    if status=="completed":
        if run.get("conclusion")=="success":
            return "pilot_completed","no_business_side_effects"
        return "hold","pilot_failed_check_runner"
    if status in ("queued","waiting","pending","requested"):
        return "queued","do_not_redispatch"
    if status=="in_progress":
        return "running","do_not_redispatch"
    return "hold","unknown_run_state"

def inspect(state,token,api_factory=GitHubReadOnly):
    entries=validate_registry(state)["tasks"]
    results=[]
    for e in entries:
        if e.get("runId") is None:
            results.append({"id":e["id"],"state":"hold",
                            "reason":"unconfirmed_dispatch_manual_reconciliation"})
            continue
        if not token:
            results.append({"id":e["id"],"state":"hold",
                            "reason":"private_actions_token_missing"})
            continue
        try:
            run=api_factory(token).get(f"/repos/{REPO}/actions/runs/{e['runId']}")
            state_,reason=classify(e,run)
            results.append({"id":e["id"],"state":state_,"reason":reason,
                            "runId":e["runId"]})
        except (OSError,urllib.error.HTTPError,urllib.error.URLError,
                ValueError,TypeError,KeyError) as exc:
            results.append({"id":e["id"],"state":"hold",
                            "reason":"read_"+safe_error(exc)})
    return {"mode":"read_only_noop_pilot_monitor","count":len(entries),
            "results":results,"dispatched":0,"retried":0,"published":0}

def main():
    state=json.loads((ROOT/"state/pilot_runs.json").read_text(encoding="utf-8"))
    try:
        result=inspect(state,os.environ.get("CHAONPU_ACTIONS_READ_TOKEN",""))
    except (ValueError,KeyError,TypeError) as exc:
        print('{"status":"invalid_registry","dispatched":0,"retried":0}')
        return 2
    print(json.dumps(result,sort_keys=True,ensure_ascii=False))
    return 0

if __name__=="__main__":
    sys.exit(main())
