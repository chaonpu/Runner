"""Read-only cloud scheduling preflight for one explicitly named business task.

Run from the public Runner repository via:
  python -m scheduler.preflight --task stock_us --business-date 2026-10-16

No private checkout, dispatch, cancellation, external writes or signed release.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from scheduler.policies import decide, request_id
from scheduler.production_registry import validate as validate_production_registry
from scheduler.probes import account_usage, existing_attempt, runner_for_repo

CONFIG = Path(__file__).resolve().parents[1] / "config" / "repositories.json"
STATE = Path(__file__).resolve().parents[1] / "state" / "tasks.json"

def preflight(config: dict, task_name: str, *, business_date: str = "",
              source_sha: str = "", source_version: str = "",
              env: dict | None = None, registry_state: dict | None = None) -> dict:
    env = os.environ if env is None else env
    task = config["tasks"][task_name]
    key = request_id(task_name, task, business_date=business_date,
                     source_sha=source_sha, source_version=source_version)
    # Use durable public-safe task metadata only. The default disk file is
    # committed state; no private repo read or state write is performed here.
    state = (registry_state if registry_state is not None else
             json.loads(STATE.read_text(encoding="utf-8")))
    state = validate_production_registry(config, state)
    existing = next((r for r in state["tasks"] if r["id"] == key), None)
    owners = set()
    repository = task["repo"]
    owners.add(repository.split("/")[0])
    if task["kind"] == "android":
        owners.add(task["signingRepo"].split("/")[0])
    quota = {}
    for owner in sorted(owners):
        quota[owner] = account_usage(owner, config["accounts"][owner], env)

    runners = {}
    main_owner = repository.split("/")[0]
    if task.get("windowsWorkflow"):
        runners[repository] = runner_for_repo(
            repository, task.get("runnerName"),
            config["accounts"][main_owner], env)
    if task["kind"] == "android":
        signing_repo = task["signingRepo"]
        runners[signing_repo] = runner_for_repo(
            signing_repo, task.get("signingRunnerName"),
            config["accounts"][signing_repo.split("/")[0]], env)

    attempts = existing_attempt(
        repository, task.get("cloudWorkflow"), config["accounts"][main_owner], env)
    # Do not declare a task "not running" if Actions metadata cannot be read.
    active_or_unverifiable = (attempts["status"] != "no_active_run")
    # Any earlier reservation, uncertain dispatch, or incomplete effect check
    # blocks the same task. Green workflow status is never proof of ledger data.
    decision = decide(task_name, task, quota, runners,
                      active=active_or_unverifiable or (existing is not None and existing["status"] != "verified"),
                      already_verified=existing is not None and existing["status"] == "verified")
    # Workflows may be limited by policy, code readiness, upstream dependency
    # and permission independently of a route recommendation.
    allowed = bool(config.get("dispatchEnabled", False)
                   and task.get("enabledForDispatch", False))
    return {
        "taskId": key,
        "task": task_name,
        "requestedRepository": repository,
        "billing": quota,
        "runner": runners,
        "attempt": attempts,
        "registeredTask": {"status": existing["status"] if existing else "not_registered",
                           "hasTargetRun": bool(existing and existing.get("runId"))},
        "recommendation": {
            "route": decision.route,
            "reason": decision.reason,
            "repository": decision.repository,
            "workflow": decision.workflow,
        },
        "execution": {
            "dispatchAuthorized": False,  # No write code in this module.
            "configurationAllowsDispatch": allowed,
            "phase": "read_only_preflight",
            "nextAction": "none",
        },
    }

def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(config["tasks"]))
    parser.add_argument("--business-date", default="")
    parser.add_argument("--source-sha", default="")
    parser.add_argument("--source-version", default="")
    args = parser.parse_args()
    try:
        result = preflight(
            config, args.task,
            business_date=args.business_date, source_sha=args.source_sha,
            source_version=args.source_version)
    except (ValueError, KeyError, TypeError) as exc:
        # Never include raw exception data from remote sources in public logs.
        print(json.dumps({"status": "invalid_task_identity", "task": args.task,
                          "dispatchAuthorized": False}))
        return 2
    # GitHub-hosted jobs in this repository have PUBLIC logs.
    # Retain quantities in memory for future verified policy evaluation, but
    # never expose detailed personal/organization billing records publicly.
    public_result = dict(result)
    public_result["billing"] = {
        owner: {
            "accountType": report.get("accountType"),
            "read": report.get("read"),
            "status": report.get("status"),
            "independentlyVerified": report.get("independentlyVerified", False),
            "usageDataPresent": bool(report.get("minuteUsageBySku")),
        }
        for owner, report in result["billing"].items()
    }
    print(json.dumps(public_result, ensure_ascii=False, sort_keys=True))
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("## 子午汐律 · 按需云端额度预检（只读）\n\n")
            f.write(f"- 任务标识：{result['taskId']}\n")
            f.write(f"- 推荐路径：{result['recommendation']['route']}\n")
            f.write(f"- 说明：{result['recommendation']['reason']}\n")
            f.write("- **真实派发：关闭；未读取私有源码或账本。**\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
