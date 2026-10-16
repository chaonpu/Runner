"""Pure production-task lifecycle contract. No GitHub requests or business writes.

All mutations return a NEW public-safe registry. This module NEVER dispatches,
retries, marks a ledger verified, or publishes anything. A separate trusted
effect-verification implementation is required before enabling production.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import re

from scheduler.policies import request_id

FIELDS = frozenset({
    "id", "task", "repo", "workflow", "route", "status", "coordinatorRunId",
    "runId", "createdAt", "updatedAt"
})
STATES = frozenset({
    "reserved", "dispatched", "running", "awaiting_effect_verification",
    "hold", "verified"
})
ROUTES = {"cloud": "cloudWorkflow", "windows": "windowsWorkflow"}
PENDING = frozenset({"reserved", "dispatched", "running"})
RUN_STATUSES = frozenset({"queued", "pending", "waiting", "requested", "in_progress", "completed"})


class RegistryGuard(ValueError):
    """Fail closed without including private payloads in errors."""


def _identity(task_name: str, spec: dict, key: str) -> None:
    prefix = task_name + ":"
    if not isinstance(key, str) or not key.startswith(prefix):
        raise RegistryGuard("task_identity_mismatch")
    value = key[len(prefix):]
    scope = spec.get("idempotencyScope")
    if scope in ("date", "trade-date"):
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise RegistryGuard("invalid_task_calendar_date") from exc
    elif scope == "source-sha":
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise RegistryGuard("invalid_source_sha")
    else:
        raise RegistryGuard("unsupported_identity_scope")
    if request_id(
        task_name, spec,
        business_date=value if scope in ("date", "trade-date") else "",
        source_sha=value if scope == "source-sha" else ""
    ) != key:
        raise RegistryGuard("identity_not_canonical")


def _time(value: object) -> None:
    if not isinstance(value, str):
        raise RegistryGuard("missing_timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryGuard("invalid_timestamp") from exc
    if stamp.tzinfo is None:
        raise RegistryGuard("timestamp_timezone_required")


def validate(config: dict, state: dict) -> dict:
    if (not isinstance(state, dict)
            or not {"schema", "tasks"}.issubset(state)
            or not set(state).issubset({"schema", "tasks", "description"})
            or state["schema"] != 1 or not isinstance(state["tasks"], list)):
        raise RegistryGuard("registry_schema_invalid")
    if "description" in state and (
        not isinstance(state["description"], str)
        or len(state["description"]) > 240
    ):
        raise RegistryGuard("registry_description_invalid")
    if not isinstance(config.get("tasks"), dict):
        raise RegistryGuard("missing_task_allowlist")
    keys, runs = set(), set()
    for entry in state["tasks"]:
        if not isinstance(entry, dict) or not set(entry).issubset(FIELDS):
            raise RegistryGuard("invalid_public_task_fields")
        task_name = entry.get("task")
        if not isinstance(task_name, str) or task_name not in config["tasks"]:
            raise RegistryGuard("unapproved_task")
        spec = config["tasks"][task_name]
        _identity(task_name, spec, entry.get("id"))
        key = entry["id"]
        if key in keys:
            raise RegistryGuard("duplicate_task_identity")
        keys.add(key)
        if entry.get("route") not in ROUTES:
            raise RegistryGuard("unapproved_route")
        workflow = spec.get(ROUTES[entry["route"]])
        if not isinstance(workflow, str) or not workflow.endswith(".yml"):
            raise RegistryGuard("unverified_workflow_route")
        if (entry.get("repo") != spec["repo"] or entry.get("workflow") != workflow
                or entry["repo"] == "chaonpu/ziwuxilv"):
            raise RegistryGuard("target_mismatch")
        if entry.get("status") not in STATES:
            raise RegistryGuard("invalid_task_status")
        rid = entry.get("coordinatorRunId")
        if type(rid) is not int or rid < 1:
            raise RegistryGuard("invalid_coordinator_run")
        target_run = entry.get("runId")
        if target_run is not None:
            if type(target_run) is not int or target_run < 1 or target_run in runs:
                raise RegistryGuard("invalid_or_duplicate_target_run")
            runs.add(target_run)
        if entry["status"] not in ("reserved", "hold") and target_run is None:
            raise RegistryGuard("target_run_required")
        _time(entry.get("createdAt"))
        _time(entry.get("updatedAt"))
    return state


def reserve(config: dict, state: dict, task_name: str, *, route: str,
            coordinator_run_id: int, at: str, business_date: str = "",
            source_sha: str = "") -> dict:
    """Reserve one task identity; never retry after any earlier reservation."""
    validate(config, state)
    if task_name not in config["tasks"]:
        raise RegistryGuard("unapproved_task")
    spec = config["tasks"][task_name]
    if (config.get("dispatchEnabled") is not True
            or spec.get("enabledForDispatch") is not True):
        raise RegistryGuard("production_dispatch_disabled")
    if route not in ROUTES or not spec.get(ROUTES[route]):
        raise RegistryGuard("route_not_verified")
    if type(coordinator_run_id) is not int or coordinator_run_id < 1:
        raise RegistryGuard("invalid_coordinator_run")
    _time(at)
    try:
        key = request_id(task_name, spec, business_date=business_date,
                         source_sha=source_sha)
    except (ValueError, TypeError) as exc:
        raise RegistryGuard("task_identity_invalid") from exc
    _identity(task_name, spec, key)
    if any(e["id"] == key for e in state["tasks"]):
        raise RegistryGuard("already_reserved_no_automatic_redispatch")
    output = deepcopy(state)
    output["tasks"].append({
        "id": key, "task": task_name, "repo": spec["repo"],
        "workflow": spec[ROUTES[route]], "route": route,
        "status": "reserved", "coordinatorRunId": coordinator_run_id,
        "createdAt": at, "updatedAt": at
    })
    return validate(config, output)


def _row(output: dict, key: str) -> dict:
    matched = [x for x in output["tasks"] if x["id"] == key]
    if len(matched) != 1:
        raise RegistryGuard("registered_task_not_unique")
    return matched[0]


def bind_run(config: dict, state: dict, key: str, *, run_id: int, at: str) -> dict:
    """Bind the single Run ID returned by trusted dispatch; never redispatch."""
    validate(config, state)
    _time(at)
    if type(run_id) is not int or run_id < 1:
        raise RegistryGuard("run_id_invalid")
    output = deepcopy(state)
    row = _row(output, key)
    if row["status"] != "reserved" or row.get("runId") is not None:
        raise RegistryGuard("run_binding_not_allowed")
    if any(e.get("runId") == run_id for e in output["tasks"]):
        raise RegistryGuard("run_already_bound")
    row.update(status="dispatched", runId=run_id, updatedAt=at)
    return validate(config, output)


def observe_run(config: dict, state: dict, key: str, run: dict, *, at: str) -> dict:
    """Update run progress only; a successful workflow is NOT verified business data."""
    validate(config, state)
    _time(at)
    output = deepcopy(state)
    row = _row(output, key)
    if row["status"] not in ("dispatched", "running"):
        raise RegistryGuard("task_not_observable")
    if not isinstance(run, dict):
        raise RegistryGuard("unverified_target_run")
    scope = config["tasks"][row["task"]].get("idempotencyScope")
    expected_sha = row["id"].split(":", 1)[1] if scope == "source-sha" else None
    if (run.get("id") != row.get("runId")
            or run.get("repository", {}).get("full_name") != row["repo"]
            or run.get("path", "").split("@")[0] != ".github/workflows/" + row["workflow"]
            or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != "main"
            or type(run.get("run_attempt")) is not int
            or run["run_attempt"] != 1
            or (expected_sha and run.get("head_sha") != expected_sha)):
        raise RegistryGuard("run_provenance_unverified")
    status = run.get("status")
    if status not in RUN_STATUSES:
        raise RegistryGuard("unknown_run_state")
    if status == "in_progress":
        row["status"] = "running"
    elif status == "completed":
        if run.get("conclusion") == "success":
            row["status"] = "awaiting_effect_verification"
        else:
            row["status"] = "hold"
    # Pending/queued remains dispatched or running; never regress a running task.
    row["updatedAt"] = at
    return validate(config, output)
