"""Exact-task Stock coordinator core.

There is intentionally no CLI, HTTP client, credential lookup or default port.
Explicit coordinator execution uses a temporary exact-task permit. Defaults
remain OFF between runs. Native theoretical catchup is distinguished from
formal report generation; business is never repeated after an uncertain call.

Future trusted ports must provide load(), save(), audit(), dispatch(), run().
Each uncertain effect aborts without retry. A durable reservation survives an
ambiguous dispatch so a second invocation cannot launch the same task again.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re

from scheduler.policies import hosted_capacity, runner_available
from scheduler.production_registry import (
    RegistryGuard, bind_run, observe_run, reserve, validate,
)
from scheduler.production_store import verify_change

WORKFLOWS = {"stock_a": "run-a-share.yml", "stock_us": "run-us.yml"}
WORKFLOW_REF = "chaonpu/Runner/.github/workflows/production-dispatch.yml@refs/heads/main"
SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[1-9][0-9]{7,19}$")


def need(ok, code):
    if not ok:
        raise RegistryGuard(code)


def context(env):
    need(isinstance(env, dict)
         and env.get("GITHUB_REPOSITORY") == "chaonpu/Runner"
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and env.get("GITHUB_WORKFLOW_REF") == WORKFLOW_REF
         and (env.get("GITHUB_ACTOR") == "chaonpu" or
              (env.get("GITHUB_ACTOR") == "github-actions[bot]"
               and bool(RUN_ID.fullmatch(str(env.get("BOOTSTRAP_RUN_ID", ""))))))
         and env.get("GITHUB_RUN_ATTEMPT") == "1"
         and bool(SHA.fullmatch(str(env.get("GITHUB_SHA", ""))))
         and bool(RUN_ID.fullmatch(str(env.get("GITHUB_RUN_ID", "")))),
         "session_context_untrusted")
    return int(env["GITHUB_RUN_ID"])


def call(method, *args):
    try:
        return method(*deepcopy(args))
    except Exception:
        # Never expose private API errors, payloads or credentials.
        raise RegistryGuard("session_port_uncertain_no_retry") from None


def load(ports):
    config, state, sha = call(ports.load)
    validate(config, state)
    need(isinstance(sha, str) and SHA.fullmatch(sha), "session_registry_sha_invalid")
    return config, state, sha


def stamp(clock):
    instant = clock()
    need(isinstance(instant, datetime) and instant.tzinfo is not None,
         "session_clock_invalid")
    return instant.astimezone(timezone.utc)


def readiness(ports, config, state, task, day, clock):
    """Require fresh exact-task evidence, then independently apply route policy.

    Existing production_dry_run reports deliberately cannot pass this gate:
    marketDataReady and exchangeSessionVerified are always false there.
    """
    spec = config["tasks"].get(task)
    need(task in WORKFLOWS and isinstance(spec, dict)
         and config.get("dispatchEnabled") is True
         and spec.get("enabledForDispatch") is True
         and spec.get("repo") == "chaonpu/Stock"
         and spec.get("idempotencyScope") == "trade-date"
         and spec.get("cloudWorkflow") == WORKFLOWS[task],
         "session_production_disabled_or_target_invalid")
    audit = call(ports.audit, config, state, task, day)
    need(isinstance(audit, dict)
         and audit.get("taskId") == task + ":" + day
         and audit.get("activeRunCheck") == "no_active_run"
         and all(audit.get(k) is True for k in (
             "calendarCoverageCurrent", "scheduledSessionClosed",
             "exchangeSessionVerified", "marketDataReady"))
         and bool(SHA.fullmatch(str(audit.get("sourceSha", "")))),
         "session_readiness_unverified")
    now = stamp(clock)
    try:
        observed = datetime.fromisoformat(audit["observedAt"])
        need(observed.tzinfo is not None
             and 0 <= (now - observed).total_seconds() <= 300,
             "session_readiness_stale")
    except (KeyError, TypeError, ValueError):
        raise RegistryGuard("session_readiness_stale") from None
    billing, runners = audit.get("billing"), audit.get("runners")
    need(isinstance(billing, dict) and isinstance(runners, dict),
         "session_capacity_evidence_invalid")
    if hosted_capacity(billing.get("chaonpu"), spec, now=now) == "available":
        route = "cloud"
    elif (spec.get("windowsWorkflow") == WORKFLOWS[task]
          and runner_available(runners.get(spec["repo"]), spec.get("runnerName"))):
        route = "windows"
    else:
        raise RegistryGuard("session_no_verified_route")
    return route, audit["sourceSha"]


def persist(ports, config, before, after, sha, env):
    verify_change(config, before, after, int(env["GITHUB_RUN_ID"]))
    call(ports.save, config, before, after, sha, env)
    # A successful PUT response alone cannot authorize the next effect.
    current_config, current, current_sha = load(ports)
    need(current_config == config and current == after and current_sha != sha,
         "session_registry_confirmation_mismatch")
    return current, current_sha


def dispatch_inputs(run, key, owner, route, publication_mode="formal"):
    # REST may omit workflow inputs. Missing is unknown; a known conflict is
    # never acceptable, even when repository/workflow/source SHA all match.
    if "inputs" not in run:
        return
    inputs = run["inputs"]
    need(isinstance(inputs, dict) and inputs.get("task_id") == key
         and str(inputs.get("coordinator_run_id", "")) == str(owner)
         and inputs.get("force_run") in (False, "false")
         and inputs.get("runner_mode") == ("ubuntu" if route == "cloud" else "windows"),
         "session_known_dispatch_inputs_conflict")
    need(inputs.get("publication_mode", "formal") == publication_mode,
         "session_publication_inputs_conflict")


def start(ports, task, business_date, env, *, clock=lambda: datetime.now(timezone.utc)):
    """Reserve -> confirm -> re-audit -> one dispatch -> bind -> confirm.

    Production adapters remain unwired. Never invoke against a real system
    until the private readiness producer and production enablement are ready.
    """
    owner = context(env)
    need(task in WORKFLOWS and isinstance(business_date, str), "session_task_invalid")
    config, state, sha = load(ports)
    route, source_sha = readiness(ports, config, state, task, business_date, clock)
    publication_mode = config["tasks"][task].get("publicationMode", "formal")
    key = task + ":" + business_date
    reserved = reserve(config, state, task, route=route,
                       coordinator_run_id=owner, at=stamp(clock).isoformat(),
                       business_date=business_date, source_sha=source_sha, publication_mode=publication_mode)
    current, current_sha = persist(ports, config, state, reserved, sha, env)
    need(readiness(ports, config, current, task, business_date, clock)
         == (route, source_sha), "session_route_or_source_changed")
    payload = {"ref": "main", "return_run_details": True, "inputs": {
        "task_id": key, "coordinator_run_id": str(owner),
        "force_run": "false", "runner_mode": "ubuntu" if route == "cloud" else "windows",
    }}
    if publication_mode != "formal":
        payload["inputs"]["publication_mode"] = publication_mode
    # No retry on 204/missing Run ID, timeout, server error or later bind failure.
    response = call(ports.dispatch, "chaonpu/Stock", WORKFLOWS[task], payload)
    rid = response.get("workflow_run_id") if isinstance(response, dict) else None
    need(type(rid) is int and RUN_ID.fullmatch(str(rid)),
         "session_dispatch_run_id_unknown_no_retry")
    bound = bind_run(config, current, key, run_id=rid, at=stamp(clock).isoformat())
    run = call(ports.run, "chaonpu/Stock", rid)
    need(isinstance(run, dict) and run.get("head_sha") == source_sha,
         "session_source_changed_no_retry")
    dispatch_inputs(run, key, owner, route, publication_mode)
    # Validate the exact returned run without prematurely advancing state.
    observe_run(config, bound, key, run, at=stamp(clock).isoformat())
    persist(ports, config, current, bound, current_sha, env)
    return {"taskId": key, "runId": rid, "sourceSha": source_sha,
            "coordinatorRunId": owner, "status": "dispatched",
            "publicationMode": publication_mode,
            "businessVerified": False, "safeToRetry": False}


def observe(ports, session, env, *, clock=lambda: datetime.now(timezone.utc)):
    """One foreground observation only. Success stops at private effect review.

    Caller may explicitly repeat READ observation in the same foreground job;
    this function cannot dispatch or cancel, and starts no background watcher.
    """
    owner = context(env)
    need(isinstance(session, dict) and session.get("coordinatorRunId") == owner
         and bool(SHA.fullmatch(str(session.get("sourceSha", "")))),
         "session_owner_or_source_invalid")
    config, state, sha = load(ports)
    matching = [x for x in state["tasks"] if x["id"] == session.get("taskId")]
    need(len(matching) == 1, "session_registered_task_missing")
    row = matching[0]
    need(row["coordinatorRunId"] == owner and row.get("runId") == session.get("runId"),
         "session_registered_run_mismatch")
    if row["status"] in ("awaiting_effect_verification", "hold", "verified"):
        return {"status": row["status"], "businessVerified": False, "safeToRetry": False}
    run = call(ports.run, row["repo"], row["runId"])
    need(isinstance(run, dict) and run.get("head_sha") == session["sourceSha"],
         "session_observed_source_mismatch")
    dispatch_inputs(run, row["id"], owner, row["route"], row.get("publicationMode", "formal"))
    after = observe_run(config, state, row["id"], run, at=stamp(clock).isoformat())
    new_row = next(x for x in after["tasks"] if x["id"] == row["id"])
    # Queued observations do not create heartbeat commits or a second dispatch.
    if new_row["status"] != row["status"]:
        persist(ports, config, state, after, sha, env)
    return {"status": new_row["status"], "businessVerified": False, "safeToRetry": False}
