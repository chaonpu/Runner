"""Read-only live production readiness: public summaries, never dispatch.

Billing usage is NOT remaining included minutes. A status of "unknown"
must not be promoted to available/exhausted based on totals or plan guesses.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from scheduler.probes import account_usage, existing_attempt, runner_for_repo, GitHubReadOnly, safe_error
from scheduler.production_registry import validate

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "repositories.json"
STATE = ROOT / "state" / "tasks.json"


def billing_token_repository_access(repo: str, account: dict, env: dict,
                                    *, reader_factory=GitHubReadOnly) -> str:
    """Only test whether Billing token can see this repository (GET, no code)."""
    token = env.get(account.get("billingSecret", ""), "")
    if not token:
        return "secret_missing"
    try:
        record = reader_factory(token).get(f"/repos/{repo}")
        if record.get("full_name") != repo:
            return "invalid_response"
        return "readable"
    except (ValueError, KeyError, TypeError, OSError,
            TimeoutError) as exc:
        return safe_error(exc)



def audit(config: dict, registry: dict, env: dict, *,
          bill=account_usage, probe_runner=runner_for_repo,
          active=existing_attempt) -> dict:
    validate(config, registry)
    if config.get("dispatchEnabled") is not False or any(
        t.get("enabledForDispatch") is not False
        for t in config["tasks"].values()
    ):
        raise ValueError("read_only_lock_required")
    accounts = {}
    for owner, spec in config["accounts"].items():
        entry = bill(owner, spec, env)
        accounts[owner] = {
            "usageEndpointRead": entry.get("read", "unknown"),
            "verifiedRemainingFreeMinutes": False,
            "hostedDecision": "unknown",
            "totalsNotUsedAsRemaining": True,
        }
    stocks = {}
    for market in ("stock_a", "stock_us"):
        spec = config["tasks"][market]
        owner = spec["repo"].split("/")[0]
        account = config["accounts"][owner]
        runner = probe_runner(spec["repo"], spec.get("runnerName"), account, env)
        # A preexisting Actions token may have repo Administration(read) as
        # well. Try it only as a READ-ONLY diagnostic, never as a silent
        # production routing credential substitute.
        alternate = None
        if (runner.get("read") != "readable"
                and account.get("actionsSecret") != account.get("runnerSecret")):
            alt_spec = dict(account, runnerSecret=account["actionsSecret"])
            alternate = probe_runner(spec["repo"], spec.get("runnerName"), alt_spec, env)
        repo_scope = billing_token_repository_access(spec["repo"], account, env)
        attempt = active(spec["repo"], spec.get("cloudWorkflow"), account, env)
        registry_entries = [v for v in registry["tasks"] if v["task"] == market]
        online_verified = (
            runner.get("status") == "online"
            and runner.get("busy") is False
            and runner.get("labelsVerified") is True
        )
        stocks[market] = {
            "runnerRead": runner.get("read", "unknown"),
            "billingTokenCanSeePrivateRepository": repo_scope,
            "runnerCredentialUsesExistingBillingSecret": (
                account.get("runnerSecret") == account.get("billingSecret")),

            "alternateActionsTokenRunnerRead": (
                alternate.get("read", "unknown") if alternate is not None
                else "not_checked"),
            "windowsOnlineAndLabelsVerified": online_verified,
            "windowsWorkflowAllowlisted": isinstance(spec.get("windowsWorkflow"), str),
            "cloudWorkflow": spec.get("cloudWorkflow"),
            "activeRunRead": attempt.get("read", "unknown"),
            "activeRunStatus": attempt.get("status", "unknown"),
            "registeredProductionRuns": len(registry_entries),
            "hostedRemainingMinutesVerified": False,
            "productionRouteAuthorized": False,
        }
    return {
        "mode": "read_only_live_capacity_audit",
        "status": "production_locked",
        "accounts": accounts,
        "stock": stocks,
        "businessDateAuthorized": False,
        "marketDataReady": False,
        "dispatchAuthorized": False,
        "registryWriteAuthorized": False,
        "databaseWriteAuthorized": False,
        "automaticRetryAuthorized": False,
    }


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8"))
    try:
        if not (os.getenv("GITHUB_REPOSITORY") == "chaonpu/Runner"
                and os.getenv("GITHUB_REF") == "refs/heads/main"
                and os.getenv("GITHUB_ACTOR") == "chaonpu"):
            raise ValueError("unsafe_audit_context")
        result = audit(config, state, os.environ)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError):
        print(json.dumps({"status": "hold_unverified_capacity", "dispatchAuthorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
