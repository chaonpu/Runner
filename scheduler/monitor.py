"""Read-only 5-minute reconciliation of public-safe task registry.

Never retries/cancels workflows; never treats completed/success as a verified
ledger or a published APK. No private repository contents or artifacts fetched.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import urllib.error
from pathlib import Path

from scheduler.probes import GitHubReadOnly, safe_error

ROOT = Path(__file__).resolve().parents[1]

def verify_registry(data: dict, config: dict) -> list:
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("Unknown registry schema")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("Invalid task array")
    identifiers = set()
    result = []
    for entry in tasks:
        if not isinstance(entry, dict):
            raise ValueError("Invalid registry entry")
        allowed = {"id", "task", "repo", "workflow", "runId", "status", "createdAt",
                   "effectVerified", "sourceVersion"}
        if not set(entry).issubset(allowed):
            raise ValueError("Registry contains unsupported possibly sensitive fields")
        task = config["tasks"].get(entry.get("task"))
        if not task:
            raise ValueError("Unknown registered task")
        repo = entry.get("repo")
        if repo not in (task["repo"], task.get("signingRepo")):
            raise ValueError("Non-allowlisted registry repository")
        workflows = {task.get("cloudWorkflow"), task.get("windowsWorkflow"),
                     task.get("signingCloudWorkflow"), task.get("signingWindowsWorkflow")}
        if entry.get("workflow") not in workflows:
            raise ValueError("Unrecognized workflow")
        if type(entry.get("runId")) is not int or entry["runId"] <= 0:
            raise ValueError("Missing run ID")
        key = entry.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z_]+:[a-zA-Z0-9_.:-]{1,100}", key):
            raise ValueError("Invalid public task identity")
        if (key, repo, entry["runId"]) in identifiers:
            raise ValueError("Duplicate registry record")
        identifiers.add((key, repo, entry["runId"]))
        result.append(entry)
    return result

def classify_run(entry: dict, run: dict, now: datetime.datetime) -> dict:
    if run.get("id") != entry["runId"]:
        return {"id": entry["id"], "state": "hold", "reason": "run_id_mismatch"}
    if run.get("path", "").split("@")[0] != ".github/workflows/" + entry["workflow"]:
        return {"id": entry["id"], "state": "hold", "reason": "workflow_mismatch"}
    status = run.get("status")
    conclusion = run.get("conclusion")
    if status in ("queued", "pending", "waiting", "requested"):
        created = run.get("created_at", "")
        try:
            then = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
            waiting = (now - then).total_seconds()
        except (TypeError, ValueError):
            waiting = None
        if waiting is not None and waiting >= 600:
            return {"id": entry["id"], "state": "hold", "reason": "queued_over_10m_verify_before_recovery"}
        return {"id": entry["id"], "state": "queued", "reason": "pending_no_reissue"}
    if status == "in_progress":
        return {"id": entry["id"], "state": "running", "reason": "do_not_duplicate"}
    if status == "completed" and conclusion == "success":
        return {"id": entry["id"], "state": "verified" if entry.get("effectVerified") is True
                else "awaiting_effect_verification",
                "reason": "success_not_proof_of_downstream_side_effect"}
    if status == "completed" and conclusion in ("failure", "cancelled", "timed_out", "action_required"):
        return {"id": entry["id"], "state": "hold", "reason": "inspect_side_effects_before_recovery"}
    return {"id": entry["id"], "state": "hold", "reason": "unknown_run_status"}

def reconcile(config: dict, state: dict, env: dict, api_factory=GitHubReadOnly, now=None) -> dict:
    entries = verify_registry(state, config)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    results = []
    for entry in entries:
        owner = entry["repo"].split("/")[0]
        acc = config["accounts"][owner]
        token = env.get(acc["actionsSecret"], "")
        if not token:
            results.append({"id": entry["id"], "state": "hold", "reason": "actions_token_missing"})
            continue
        try:
            response = api_factory(token).get(
                f"/repos/{entry['repo']}/actions/runs/{entry['runId']}")
            results.append(classify_run(entry, response, now))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                OSError, ValueError, TypeError, KeyError) as exc:
            results.append({"id": entry["id"], "state": "hold",
                            "reason": "read_" + safe_error(exc)})
    return {"mode": "monitor_only", "registryCount": len(entries), "results": results,
            "cancelled": 0, "retried": 0, "dispatched": 0, "published": 0}

def main() -> int:
    config = json.loads((ROOT / "config/repositories.json").read_text(encoding="utf-8"))
    state = json.loads((ROOT / "state/tasks.json").read_text(encoding="utf-8"))
    try:
        result = reconcile(config, state, os.environ)
    except (ValueError, KeyError, TypeError):
        print('{"mode":"monitor_only","status":"invalid_registry","dispatched":0}')
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    sys.exit(main())
