"""Pure, fail-closed routing for the public Runner coordinator.

Raw Actions usage alone is not remaining included minutes. Supported allowance
estimates and independently verified balances use the same 100-minute rule.
Never infer Windows executable compatibility merely from a Runner online status.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import math
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

SELF_HOSTED_BELOW_MINUTES = 100


def hosted_remaining(value: Any, *, now=None):
    """Fresh account balance or supported private-Linux allowance estimate.

    Estimates exclude public standard-runner usage and are not a guarantee of
    realtime billing. Raw usage totals alone never establish remaining minutes.
    """
    if not isinstance(value, dict):
        return None
    instant = datetime.now(timezone.utc) if now is None else now
    try:
        observed = datetime.fromisoformat(value["observedAt"])
        if instant.tzinfo is None or observed.tzinfo is None:
            return None
        instant = instant.astimezone(timezone.utc)
        if not 0 <= (instant - observed).total_seconds() <= 300:
            return None
        if value.get("period") != instant.strftime("%Y-%m"):
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    state = billing_state(value)
    if state in ("available", "exhausted"):
        remaining = value.get("remainingFreeMinutes")
        if (type(remaining) not in (int, float) or not math.isfinite(remaining)
                or remaining < 0 or (state == "exhausted" and remaining != 0)):
            return None
        return remaining
    estimate = value.get("allowanceEstimate")
    if (value.get("read") != "readable" or value.get("reportingPeriodVerified") is not True
            or not isinstance(estimate, dict) or estimate.get("read") != "readable"
            or estimate.get("period") != value["period"]
            or estimate.get("conversionSupported") is not True
            or estimate.get("estimateStatus") != "private_standard_linux_only"
            or estimate.get("publicStandardUsageExcluded") is not True):
        return None
    remaining = estimate.get("remainingMinutesEstimate")
    return remaining if type(remaining) is int and remaining >= 0 else None


def hosted_capacity(value: Any, task: dict, *, now=None) -> str:
    """User rule: cloud at >=100 minutes; self-hosted only below 100.

    Task runtime estimates and reserve minutes do not change this threshold.
    Missing, stale or unsupported billing evidence is unknown, never low quota.
    """
    remaining = hosted_remaining(value, now=now)
    if remaining is None:
        return "unknown"
    if remaining == 0 and billing_state(value) == "exhausted":
        return "exhausted"
    return "available" if remaining >= SELF_HOSTED_BELOW_MINUTES else "insufficient"

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
    main_quota = hosted_capacity(account_states.get(owner), task)

    # Never auto-publish or modify a formal database on a green workflow alone.
    if kind in ("publish", "data"):
        return Decision("hold", "Upstream artifact and side-effect verification not integrated")

    if kind == "android":
        if main_quota == "available":
            return Decision("cloud", "Test reported allowance at least 100 minutes",
                            repo, task["cloudWorkflow"])
        signing_repo = task["signingRepo"]
        signing_owner = signing_repo.split("/")[0]
        signer_quota = hosted_capacity(account_states.get(signing_owner), task)
        if (main_quota in ("exhausted", "insufficient") and signer_quota == "available"
                and signing_source_verified):
            return Decision("cloud-signing", "Test hosted exhausted; verified source permits signing hosted",
                            signing_repo, task["signingCloudWorkflow"])
        # Only a known balance below 100 permits self-hosted fallback.
        if (main_quota in ("exhausted", "insufficient")
                and runner_available(runner_states.get(repo), task.get("runnerName"))
                and runner_available(runner_states.get(signing_repo), task.get("signingRunnerName"))):
            return Decision("windows-chain", "Remaining allowance below 100 minutes; Windows builder and signer ready",
                            repo, task["windowsWorkflow"])
        return Decision("deferred", "No verified cloud capacity and full Windows build/sign chain unavailable")

    if main_quota == "available" and task.get("cloudWorkflow"):
        return Decision("cloud", "Reported allowance at least 100 minutes",
                        repo, task["cloudWorkflow"])

    if (main_quota in ("exhausted", "insufficient") and task.get("windowsWorkflow")
            and runner_available(runner_states.get(repo), task.get("runnerName"))):
        return Decision("windows", "Remaining allowance below 100 minutes; verified Windows workflow available",
                        repo, task["windowsWorkflow"])

    # Stock Linux steps require an explicit, tested Windows workflow; adding a
    # self-hosted label to ubuntu-latest bash steps is not a valid fallback.
    return Decision("deferred", "Hosted allowance unknown/exhausted or Windows workflow not verified")
