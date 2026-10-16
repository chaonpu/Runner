"""Pure, fail-closed routing for the public Runner coordinator.

Actions usage is not remaining included minutes. Only independently verified
account allowance information may set billing status to available/exhausted.
Never infer Windows executable compatibility merely from a Runner online status.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re

SHA = re.compile(r"^[0-9a-f]{40}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

@dataclass(frozen=True)
class Decision:
    route: str
    reason: str
    repository: str | None = None
    workflow: str | None = None

def billing_state(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    state = value.get("status")
    if state in ("available", "exhausted") and value.get("independentlyVerified") is True:
        return state
    return "unknown"

def runner_available(obj: Any, name: str | None) -> bool:
    if not isinstance(obj, dict) or not name:
        return False
    return (obj.get("name") == name
            and obj.get("status") == "online"
            and obj.get("busy") is False
            and obj.get("labelsVerified") is True)

def request_id(task_name: str, task: dict, *, business_date: str = "",
               source_sha: str = "", source_version: str = "") -> str:
    scope = task.get("idempotencyScope")
    if scope == "source-sha":
        if not SHA.fullmatch(source_sha):
            raise ValueError("Android task requires a full source SHA")
        key = source_sha
    elif scope in ("trade-date", "date"):
        if not DATE.fullmatch(business_date):
            raise ValueError("Strategy task requires an ISO business date")
        key = business_date
    elif scope in ("source-version", "version"):
        if not source_version or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", source_version):
            raise ValueError("A verified, public-safe source/version key is required")
        key = source_version
    else:
        raise ValueError("Unknown idempotency scope")
    if not re.fullmatch(r"[a-z_]+", task_name):
        raise ValueError("Invalid task name")
    return f"{task_name}:{key}"

def decide(task_name: str, task: dict, account_states: dict,
           runner_states: dict, *, active=False, already_verified=False,
           signing_source_verified=False) -> Decision:
    if already_verified:
        return Decision("skip", "A verified result already exists for this task identity")
    if active:
        return Decision("hold", "A queued, running, or uncertain attempt already exists")
    kind = task["kind"]
    repo = task["repo"]
    owner = repo.split("/")[0]
    main_quota = billing_state(account_states.get(owner))

    # Never auto-publish or modify a formal database on a green workflow alone.
    if kind in ("publish", "data"):
        return Decision("hold", "Upstream artifact and side-effect verification not integrated")

    if kind == "android":
        if main_quota == "available":
            return Decision("cloud", "Test hosted quota independently verified available",
                            repo, task["cloudWorkflow"])
        signing_repo = task["signingRepo"]
        signing_owner = signing_repo.split("/")[0]
        signer_quota = billing_state(account_states.get(signing_owner))
        if (main_quota == "exhausted" and signer_quota == "available"
                and signing_source_verified):
            return Decision("cloud-signing", "Test hosted exhausted; verified source permits signing hosted",
                            signing_repo, task["signingCloudWorkflow"])
        # Unknown hosted quota never equals exhausted. Conservative local routing
        # is possible, but it requires both independent Windows runner services.
        if (runner_available(runner_states.get(repo), task.get("runnerName"))
                and runner_available(runner_states.get(signing_repo), task.get("signingRunnerName"))):
            return Decision("windows-chain", "Hosted capacity unconfirmed/unavailable; isolated Windows builder and signer ready",
                            repo, task["windowsWorkflow"])
        return Decision("deferred", "No verified cloud capacity and full Windows build/sign chain unavailable")

    if main_quota == "available" and task.get("cloudWorkflow"):
        return Decision("cloud", "Independent hosted allowance is available",
                        repo, task["cloudWorkflow"])

    if (task.get("windowsWorkflow")
            and runner_available(runner_states.get(repo), task.get("runnerName"))):
        return Decision("windows", "Hosted allowance unavailable/unknown; verified Windows workflow available",
                        repo, task["windowsWorkflow"])

    # Stock Linux steps require an explicit, tested Windows workflow; adding a
    # self-hosted label to ubuntu-latest bash steps is not a valid fallback.
    return Decision("deferred", "Hosted allowance unknown/exhausted or Windows workflow not verified")
