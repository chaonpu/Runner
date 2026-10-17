"""Restricted GitHub adapters; the ONLY CLI constructs ReadOnlyPorts.

ProductionPorts is not wired to any workflow. Its writes require the exact
original production context, enabled live configuration, confirmed reservation
and fresh private readiness evidence. No retry, cancellation or private files.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import urllib.request

from scheduler.exchange_schedule import evaluate
from scheduler.production_evidence import inspect, hold
from scheduler.production_registry import RegistryGuard, validate, observe_run
from scheduler.production_session import context, dispatch_inputs, readiness, WORKFLOWS, SHA, RUN_ID
from scheduler.production_store import save as registry_save, verify_change
from scheduler.probes import GitHubReadOnly, account_usage, runner_for_repo, existing_attempt, safe_error

PUBLIC = "/repos/chaonpu/Runner"
PRIVATE = "/repos/chaonpu/Stock"
CONFIG = "config/repositories.json"
REGISTRY = "state/tasks.json"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def bounded_open(req, timeout):
    # A fixed-repository request must not forward credentials or a write body
    # to a renamed repository, different endpoint or external host.
    return urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout)


def need(ok, code):
    if not ok:
        raise RegistryGuard(code)


def request(token, method, path, body=None, *, opener=bounded_open):
    """One request only, with an endpoint/method allowlist before credentials."""
    public_get = path == PUBLIC + "/branches/main" or bool(re.fullmatch(
        re.escape(PUBLIC) + r"/contents/(config/repositories\.json|state/tasks\.json)\?ref=[0-9a-f]{40}", path))
    public_put = path == PUBLIC + "/contents/" + REGISTRY
    private_post = path in {PRIVATE + "/actions/workflows/" + wf + "/dispatches"
                            for wf in WORKFLOWS.values()}
    need((method == "GET" and public_get and body is None)
         or (method == "PUT" and public_put and isinstance(body, dict))
         or (method == "POST" and private_post and isinstance(body, dict)),
         "ports_endpoint_not_allowed")
    need(isinstance(token, str) and bool(token), "ports_credential_missing")
    req = urllib.request.Request("https://api.github.com" + path, method=method,
        data=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
                 "Content-Type": "application/json", "User-Agent": "ziwuxilv-production-ports/1.0"})
    try:
        with opener(req, timeout=20) as response:
            raw = response.read(2_000_001)
        need(len(raw) <= 2_000_000, "ports_response_oversized")
        value = json.loads(raw) if raw else {}
        need(isinstance(value, dict), "ports_response_invalid")
        return value
    except Exception:
        raise RegistryGuard("ports_response_uncertain_no_retry") from None


def document(record, path):
    need(isinstance(record, dict) and record.get("type") == "file"
         and record.get("path") == path and record.get("encoding") == "base64"
         and bool(SHA.fullmatch(str(record.get("sha", ""))))
         and isinstance(record.get("content"), str) and len(record["content"]) <= 2_000_000,
         "ports_public_document_invalid")
    try:
        raw = base64.b64decode(record["content"].replace("\r", "").replace("\n", ""), validate=True)
        digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        need(digest == record["sha"], "ports_public_blob_mismatch")
        data = json.loads(raw)
        need(isinstance(data, dict), "ports_public_json_invalid")
        return data, record["sha"]
    except (ValueError, UnicodeError, TypeError):
        raise RegistryGuard("ports_public_document_invalid") from None


class ReadOnlyPorts:
    def __init__(self, env, *, requester=request, reader_factory=GitHubReadOnly,
                 bill=account_usage, runner=runner_for_repo, active=existing_attempt,
                 clock=lambda: datetime.now(timezone.utc)):
        self.env, self.requester, self.reader_factory = deepcopy(dict(env)), requester, reader_factory
        self.bill, self.runner, self.active, self.clock = bill, runner, active, clock
        self._audits = {}

    def load(self):
        token = self.env.get("GH_TOKEN", "")
        branch = self.requester(token, "GET", PUBLIC + "/branches/main")
        sha = branch.get("commit", {}).get("sha")
        need(bool(SHA.fullmatch(str(sha))), "ports_public_head_invalid")
        cfg, _ = document(self.requester(token, "GET", PUBLIC + "/contents/" + CONFIG + "?ref=" + sha), CONFIG)
        state, blob = document(self.requester(token, "GET", PUBLIC + "/contents/" + REGISTRY + "?ref=" + sha), REGISTRY)
        need(cfg.get("version") == 1 and isinstance(cfg.get("accounts"), dict), "ports_config_invalid")
        account = cfg["accounts"].get("chaonpu", {})
        need(account.get("actionsSecret") == "CHAONPU_ACTIONS_READ_TOKEN"
             and account.get("billingSecret") == "CHAONPU_BILLING_READ_TOKEN"
             and account.get("runnerSecret") == "RUNNER_READ_TOKEN", "ports_credential_mapping_invalid")
        validate(cfg, state)
        return cfg, state, blob

    def private_reader(self):
        return self.reader_factory(self.env.get("CHAONPU_ACTIONS_READ_TOKEN", ""))

    def run(self, repo, rid):
        need(repo == "chaonpu/Stock" and type(rid) is int and RUN_ID.fullmatch(str(rid)),
             "ports_run_target_invalid")
        result = self.private_reader().get(PRIVATE + f"/actions/runs/{rid}")
        need(isinstance(result, dict) and type(result.get("id")) is int and result["id"] == rid
             and result.get("repository", {}).get("full_name") == repo, "ports_run_response_invalid")
        return result

    def audit(self, config, state, task, day):
        need(task in WORKFLOWS and isinstance(config, dict), "ports_task_not_allowed")
        spec = config.get("tasks", {}).get(task, {})
        need(spec.get("repo") == "chaonpu/Stock" and spec.get("cloudWorkflow") == WORKFLOWS[task]
             and spec.get("idempotencyScope") == "trade-date", "ports_task_target_invalid")
        validate(config, state)
        calendar = evaluate(task, day, now=self.clock())
        need(calendar.get("businessDate") == day, "ports_task_date_invalid")
        account = config["accounts"]["chaonpu"]
        billing = self.bill("chaonpu", account, self.env)
        runners = self.runner("chaonpu/Stock", spec.get("runnerName"), account, self.env)
        # A/US share a private ledger publication lock: either active workflow
        # blocks a new production start, including untagged independent runs.
        attempts = [self.active("chaonpu/Stock", wf, account, self.env) for wf in WORKFLOWS.values()]
        active = "no_active_run" if all(x.get("status") == "no_active_run" for x in attempts) else (
            "active" if any(x.get("status") == "active" for x in attempts) else "unknown")
        evidence, source, source_read = hold("hold_source_metadata_unavailable"), "", "unknown"
        try:
            reader = self.private_reader()
            source = reader.get(PRIVATE + "/branches/main").get("commit", {}).get("sha", "")
            need(bool(SHA.fullmatch(str(source))), "ports_source_head_invalid")
            source_read = "readable"
            evidence = inspect(reader, task, day, source, self.clock())
            current = reader.get(PRIVATE + "/branches/main").get("commit", {}).get("sha")
            if current != source:
                evidence = hold("hold_source_changed_during_audit")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            source_read = safe_error(exc)
            evidence = hold("hold_source_metadata_unavailable")
        closed = calendar["calendarCoverageCurrent"] and calendar["scheduledSessionClosed"]
        report = {"taskId": task + ":" + day, "sourceSha": source, "sourceMetadataRead": source_read,
            "observedAt": evidence["observedAt"], "activeRunCheck": active,
            "calendarCoverageCurrent": calendar["calendarCoverageCurrent"],
            "scheduledSessionClosed": calendar["scheduledSessionClosed"],
            "exchangeSessionVerified": closed and evidence["exchangeSessionVerified"],
            "marketDataReady": closed and evidence["marketDataReady"],
            "selectionInputsVerified": evidence["selectionInputsVerified"],
            "evidenceStatus": evidence["evidenceStatus"],
            "billing": {"chaonpu": billing}, "runners": {"chaonpu/Stock": runners}}
        self._audits[report["taskId"]] = deepcopy(report)
        return report

    def save(self, *args):
        raise RegistryGuard("read_only_ports_cannot_write")

    def dispatch(self, *args):
        raise RegistryGuard("read_only_ports_cannot_dispatch")


class ProductionPorts(ReadOnlyPorts):
    """Unwired write adapters. A new instance cannot resend old reservations."""
    def __init__(self, env, **kwargs):
        context(dict(env))
        super().__init__(env, **kwargs)
        self._armed, self._accepted = {}, {}

    def save(self, config, before, after, sha, env):
        need(context(env) == context(self.env), "ports_owner_mismatch")
        live_config, live_state, live_sha = self.load()
        need((live_config, live_state, live_sha) == (config, before, sha), "ports_registry_drift")
        key = verify_change(config, before, after, context(self.env))
        row = next(x for x in after["tasks"] if x["id"] == key)
        need(config.get("dispatchEnabled") is True
             and config["tasks"][row["task"]].get("enabledForDispatch") is True,
             "ports_production_disabled")
        old = next((x for x in before["tasks"] if x["id"] == key), None)
        if old is None:
            route, source = readiness(self, config, before, row["task"], key.split(":")[1], self.clock)
            need(route == row["route"], "ports_reservation_route_changed")
        elif old.get("runId") is None:
            accepted = self._accepted.get(key)
            need(accepted is not None and accepted["runId"] == row.get("runId"),
                 "ports_original_dispatch_not_known")
            run = self.run(row["repo"], row["runId"])
            need(run.get("head_sha") == accepted["sourceSha"], "ports_binding_source_changed")
            dispatch_inputs(run, key, context(self.env), row["route"])
            observe_run(config, after, key, run, at=row["updatedAt"])
        else:
            # A caller cannot mark progress based on a fabricated green dict.
            run = self.run(row["repo"], row["runId"])
            expected = observe_run(config, before, key, run, at=row["updatedAt"])
            need(expected == after, "ports_run_transition_unverified")
        result = registry_save(config, before, after, sha, self.env, requester=self.requester)
        if old is None:
            # Arm only a reservation created by THIS adapter, after confirmed
            # storage. Existing reservations are intentionally never armed.
            confirmed_config, confirmed_state, confirmed_sha = self.load()
            need(confirmed_config == config and confirmed_state == after and confirmed_sha != sha,
                 "ports_reservation_confirmation_failed")
            self._armed[key] = {"sha": confirmed_sha, "sourceSha": source, "route": route}
        return result

    def dispatch(self, repo, workflow, payload):
        context(self.env)
        need(repo == "chaonpu/Stock" and workflow in WORKFLOWS.values()
             and isinstance(payload, dict) and set(payload) == {"ref", "inputs", "return_run_details"}
             and payload.get("ref") == "main" and payload.get("return_run_details") is True,
             "ports_dispatch_target_invalid")
        inputs = payload.get("inputs")
        need(isinstance(inputs, dict) and set(inputs) == {
            "task_id", "coordinator_run_id", "force_run", "runner_mode"}, "ports_dispatch_inputs_invalid")
        key = inputs.get("task_id")
        need(isinstance(key, str) and key in self._armed, "ports_reservation_not_armed")
        arm = self._armed.pop(key)  # Consume before any uncertain network call.
        task, day = key.split(":", 1)
        need(WORKFLOWS.get(task) == workflow and inputs.get("coordinator_run_id") == self.env["GITHUB_RUN_ID"]
             and inputs.get("force_run") == "false"
             and inputs.get("runner_mode") == ("ubuntu" if arm["route"] == "cloud" else "windows"),
             "ports_dispatch_inputs_conflict")
        cfg, state, sha = self.load()
        rows = [x for x in state["tasks"] if x["id"] == key]
        need(sha == arm["sha"] and len(rows) == 1 and rows[0]["status"] == "reserved"
             and rows[0]["coordinatorRunId"] == context(self.env) and rows[0].get("runId") is None,
             "ports_reservation_drift")
        need(readiness(self, cfg, state, task, day, self.clock) == (arm["route"], arm["sourceSha"]),
             "ports_dispatch_readiness_changed")
        response = self.requester(self.env.get("CHAONPU_ACTIONS_READ_TOKEN", ""), "POST",
            PRIVATE + "/actions/workflows/" + workflow + "/dispatches", payload)
        rid = response.get("workflow_run_id") if isinstance(response, dict) else None
        need(type(rid) is int and RUN_ID.fullmatch(str(rid)), "ports_dispatch_id_unknown_no_retry")
        self._accepted[key] = {"runId": rid, "sourceSha": arm["sourceSha"]}
        return {"workflow_run_id": rid}


def public_report(config, state, audit):
    """Fixed output shape; never copy remote response fields into public logs."""
    return {"mode": "live_read_only_production_ports", "taskId": audit["taskId"],
        "registeredProductionTasks": len(state["tasks"]), "sourceHeadRead": bool(SHA.fullmatch(audit["sourceSha"])),
        "sourceMetadataRead": audit["sourceMetadataRead"],
        "activeRunCheck": audit["activeRunCheck"], "calendarCoverageCurrent": audit["calendarCoverageCurrent"],
        "scheduledSessionClosed": audit["scheduledSessionClosed"], "evidenceStatus": audit["evidenceStatus"],
        "exchangeSessionVerified": audit["exchangeSessionVerified"], "marketDataReady": audit["marketDataReady"],
        "selectionInputsVerified": audit["selectionInputsVerified"],
        "productionSwitchOff": config.get("dispatchEnabled") is False,
        "dispatchAuthorized": False, "registryWriteAuthorized": False,
        "databaseWriteAuthorized": False, "automaticRetryAuthorized": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=sorted(WORKFLOWS), required=True)
    parser.add_argument("--business-date", default="")
    args = parser.parse_args()
    try:
        env = dict(os.environ)
        need(env.get("GITHUB_REPOSITORY") == "chaonpu/Runner" and env.get("GITHUB_ACTOR") == "chaonpu"
             and env.get("GITHUB_REF") == "refs/heads/main" and env.get("GITHUB_RUN_ATTEMPT") == "1"
             and env.get("GITHUB_EVENT_NAME") in ("push", "workflow_dispatch")
             and env.get("GITHUB_WORKFLOW_REF") in (
                "chaonpu/Runner/.github/workflows/validate.yml@refs/heads/main",
                "chaonpu/Runner/.github/workflows/production-dispatch.yml@refs/heads/main"),
             "ports_read_only_context_untrusted")
        ports = ReadOnlyPorts(env)  # Never instantiate ProductionPorts here.
        cfg, state, _ = ports.load()
        need(cfg.get("dispatchEnabled") is False and all(
            x.get("enabledForDispatch") is False for x in cfg["tasks"].values()), "ports_lock_not_off")
        day = evaluate(args.task, args.business_date).get("businessDate")
        need(isinstance(day, str), "ports_calendar_unavailable")
        result = public_report(cfg, state, ports.audit(cfg, state, args.task, day))
    except Exception:
        result = {"mode": "live_read_only_production_ports", "status": "hold_untrusted_or_unavailable",
                  "dispatchAuthorized": False, "registryWriteAuthorized": False,
                  "databaseWriteAuthorized": False, "automaticRetryAuthorized": False}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
