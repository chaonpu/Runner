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
from scheduler.production_evidence import inspect, WORKFLOWS as READY_WINDOWS, CLOUD_WORKFLOWS
from scheduler.policies import hosted_capacity
from scheduler.production_registry import RegistryGuard, validate, observe_run
from scheduler.production_session import context, dispatch_inputs, readiness, WORKFLOWS, SHA, RUN_ID
from scheduler.production_store import save as registry_save, verify_change
from scheduler.probes import GitHubReadOnly, account_usage, runner_for_repo, existing_attempt

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
        re.escape(PUBLIC) + r"/contents/(config/repositories\.json|config/launch-request\.json|state/tasks\.json)\?ref=[0-9a-f]{40}", path))
    public_put = path in (PUBLIC + "/contents/" + REGISTRY, PUBLIC + "/contents/" + CONFIG)
    private_post = path in {PRIVATE + "/actions/workflows/" + wf + "/dispatches"
                            for wf in (*WORKFLOWS.values(), *READY_WINDOWS.values(), *CLOUD_WORKFLOWS.values())}
    public_launch = path == PUBLIC + "/actions/workflows/production-dispatch.yml/dispatches"
    need((method == "GET" and public_get and body is None)
         or (method == "PUT" and public_put and isinstance(body, dict))
         or (method == "POST" and (private_post or public_launch) and isinstance(body, dict)),
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
        # Require evidence from the route actually chosen for this account.
        # A prior green Windows run cannot stand in for a new cloud preflight.
        capacity = hosted_capacity(billing, spec, now=self.clock())
        workflow = CLOUD_WORKFLOWS[task] if capacity == "available" else READY_WINDOWS[task]
        evidence = inspect(self.private_reader(), task, day, self.clock(),
                           spec.get("publicationMode", "formal"), expected_workflow=workflow,
                           expected_calendar_revision=self.env.get("ZIWU_CALENDAR_REVISION") or None)
        source = evidence["sourceSha"]
        closed = calendar["calendarCoverageCurrent"] and calendar["scheduledSessionClosed"]
        report = {"taskId": task + ":" + day, "sourceSha": source,
            "observedAt": evidence["observedAt"], "activeRunCheck": active,
            "calendarCoverageCurrent": calendar["calendarCoverageCurrent"],
            "scheduledSessionClosed": calendar["scheduledSessionClosed"],
            "exchangeSessionVerified": closed and evidence["exchangeSessionVerified"],
            "marketDataReady": closed and evidence["marketDataReady"],
            "selectionInputsVerified": evidence["selectionInputsVerified"],
            "sourceSamplesReady": closed and evidence.get("sourceSamplesReady") is True,
            "postRunValidationRequired": evidence.get("postRunValidationRequired") is True,
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
        from scheduler.production_origin import authorize
        authorize(self.env, self.requester, self.reader_factory)
        self._armed, self._accepted = {}, {}

    def retire_prebusiness_hold(self, task, day):
        """One strictly attested pre-strategy failure may be re-dispatched.

        The old coordinator/run/SHA row remains immutable in the public
        registry's append-only retiredPrebusinessFailures audit field. No
        uncertain run, unknown job, or post-calculation failure qualifies.
        """
        context(self.env)
        key = task + ":" + day
        cfg, state, blob = self.load()
        need(cfg.get("dispatchEnabled") is False and "activeLease" not in cfg
             and all(not x.get("enabledForDispatch") for x in cfg["tasks"].values()),
             "prebusiness_recovery_requires_closed_permits")
        old = [x for x in state["tasks"] if x.get("id") == key]
        need(len(old) == 1, "prebusiness_original_task_missing")
        row = old[0]
        need(row.get("status") == "hold" and row.get("task") in ("stock_a", "stock_us")
             and row["task"] == task and row.get("route") == "windows"
             and row.get("publicationMode") in ("formal", "theoretical_catchup")
             and type(row.get("runId")) is int
             and bool(SHA.fullmatch(str(row.get("sourceSha", "")))),
             "prebusiness_recovery_scope_invalid")
        need(not any(x.get("id") == key for x in state.get("retiredPrebusinessFailures", [])),
             "prebusiness_recovery_once_per_session")
        run = self.run(row["repo"], row["runId"])
        need(run.get("id") == row["runId"]
             and run.get("repository", {}).get("full_name") == "chaonpu/Stock"
             and run.get("path", "").split("@")[0] == ".github/workflows/" + row["workflow"]
             and run.get("event") == "workflow_dispatch"
             and run.get("head_branch") == "main"
             and run.get("head_sha") == row["sourceSha"]
             and run.get("run_attempt") == 1
             and run.get("status") == "completed" and run.get("conclusion") == "failure",
             "prebusiness_original_run_unverified")
        jobs = self.private_reader().get(
            PRIVATE + f"/actions/runs/{row['runId']}/jobs?filter=latest&per_page=100")
        need(jobs.get("total_count") == 1 and len(jobs.get("jobs", [])) == 1,
             "prebusiness_jobs_incomplete")
        job = jobs["jobs"][0]
        need(job.get("run_id") == row["runId"] and job.get("status") == "completed"
             and job.get("conclusion") == "failure"
             and job.get("name") ==
                 f"effect-v1:{key}:{row['coordinatorRunId']}:{row['publicationMode']}",
             "prebusiness_job_identity_unverified")
        steps = job.get("steps")
        need(isinstance(steps, list) and
             len({s.get("name") for s in steps if isinstance(s, dict)}) == len(steps),
             "prebusiness_step_trace_invalid")
        phases = {s["name"]: s.get("conclusion") for s in steps}
        route_phase = ("Prepare verified job-scoped GitHub route before Windows checkout",
                       "Bootstrap job-scoped Git route; probe only when source fetch is required")
        need(sum(phases.get(name) == "failure" for name in route_phase) == 1,
             "prebusiness_not_known_git_route_failure")
        # Require the entire calculation -> publication pipeline never began.
        no_effect = ("Run A-share strategy with retry", "Run US strategy with retry",
                     "Verify approved mobile HTML full report",
                     "Verify private snapshot contract",
                     "Publish verified snapshot and ledger to private database",
                     "Synchronize coordinated inputs to the confirmed settled ledger",
                     "Save successful strategy state",
                     "Verify and attest published ledger receipt",
                     "Send A-share mail only after verified ledger publication",
                     "Send US mail only after verified ledger publication")
        name = "Run A-share strategy with retry" if task == "stock_a" else "Run US strategy with retry"
        need(phases.get(name) == "skipped"
             and phases.get("Publish verified snapshot and ledger to private database") == "skipped"
             and phases.get("Save successful strategy state") == "skipped"
             and phases.get("Verify and attest published ledger receipt") == "skipped"
             and all(phases.get(p) in (None, "skipped") for p in no_effect),
             "prebusiness_effect_not_proven_absent")
        next_state = deepcopy(state)
        next_state["tasks"] = [x for x in state["tasks"] if x.get("id") != key]
        next_state.setdefault("retiredPrebusinessFailures", []).append(deepcopy(row))
        validate(cfg, next_state)
        # A single CAS write changes both active and archived metadata atomically.
        self.requester(self.env["GH_TOKEN"], "PUT", PUBLIC + "/contents/" + REGISTRY, {
            "branch": "main", "sha": blob,
            "message": "Archive verified prebusiness Git route failure for bounded retry",
            "content": base64.b64encode((json.dumps(next_state, indent=2) + "\\n").encode()).decode(),
        })
        c, saved, _ = self.load()
        need(c == cfg and saved == next_state, "prebusiness_recovery_commit_unverified")
        return {"status": "prebusiness_recovery_attested", "taskId": key,
                "originalRunId": row["runId"], "businessVerified": False,
                "archived": True, "dispatches": 0}

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
            dispatch_inputs(run, key, context(self.env), row["route"], row.get("publicationMode", "formal"))
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
            self._armed[key] = {"sha": confirmed_sha, "sourceSha": source, "route": route,
                                "publicationMode": row.get("publicationMode", "formal")}
        return result

    def dispatch(self, repo, workflow, payload):
        context(self.env)
        need(repo == "chaonpu/Stock" and workflow in WORKFLOWS.values()
             and isinstance(payload, dict) and set(payload) == {"ref", "inputs", "return_run_details"}
             and payload.get("ref") == "main" and payload.get("return_run_details") is True,
             "ports_dispatch_target_invalid")
        inputs = payload.get("inputs")
        need(isinstance(inputs, dict) and {"task_id", "coordinator_run_id", "force_run", "runner_mode"}.issubset(inputs)
             and set(inputs).issubset({"task_id", "coordinator_run_id", "force_run", "runner_mode", "publication_mode", "calendar_revision"}),
             "ports_dispatch_inputs_invalid")
        expected_calendar = self.env.get("ZIWU_CALENDAR_REVISION", "")
        need((bool(re.fullmatch(r"[0-9a-f]{64}", expected_calendar))
              and inputs.get("calendar_revision") == expected_calendar)
             if expected_calendar else "calendar_revision" not in inputs,
             "ports_calendar_revision_conflict")
        key = inputs.get("task_id")
        need(isinstance(key, str) and key in self._armed, "ports_reservation_not_armed")
        arm = self._armed.pop(key)  # Consume before any uncertain network call.
        task, day = key.split(":", 1)
        need(WORKFLOWS.get(task) == workflow and inputs.get("coordinator_run_id") == self.env["GITHUB_RUN_ID"]
             and inputs.get("force_run") == "false"
             and inputs.get("publication_mode", "formal") == arm.get("publicationMode", "formal")
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

    def finish(self, session):
        """The only verified transition: live original Run + private attestation."""
        from scheduler.production_effect import verify as verify_effect
        from copy import deepcopy
        context(self.env)
        config, state, sha = self.load()
        rows = [r for r in state["tasks"] if r["id"] == session["taskId"]]
        need(len(rows) == 1, "effect_task_not_unique")
        row = rows[0]
        need(row["status"] == "awaiting_effect_verification"
             and row["coordinatorRunId"] == context(self.env)
             and row["runId"] == session["runId"] and row.get("sourceSha") == session["sourceSha"],
             "effect_original_session_mismatch")
        run = self.run(row["repo"], row["runId"])
        jobs = self.private_reader().get(PRIVATE+f"/actions/runs/{row['runId']}/jobs?filter=latest&per_page=100")
        proof = verify_effect(run, jobs, row, expected_calendar_revision=self.env.get("ZIWU_CALENDAR_REVISION") or None)
        after = deepcopy(state)
        entry = next(x for x in after["tasks"] if x["id"] == row["id"])
        entry.update(status="verified", updatedAt=self.clock().isoformat())
        validate(config, after)
        # Generic save/verify_change still cannot write 'verified'. Only this
        # method obtains the required metadata independently from GitHub.
        response = self.requester(self.env["GH_TOKEN"], "PUT", PUBLIC+"/contents/"+REGISTRY, {
            "branch":"main", "sha":sha, "message":"Verify original production task effect",
            "content":base64.b64encode((json.dumps(after,indent=2)+"\n").encode()).decode()})
        need(isinstance(response.get("content"),dict), "effect_write_unconfirmed")
        current_cfg, current, _ = self.load()
        need(current_cfg == config and current == after, "effect_finalization_confirmation_failed")
        return dict(proof, taskId=row["id"], status="verified", businessVerified=True, safeToRetry=False)


def public_report(config, state, audit):
    """Fixed output shape; never copy remote response fields into public logs."""
    return {"mode": "live_read_only_production_ports", "taskId": audit["taskId"],
        "registeredProductionTasks": len(state["tasks"]),
        "sourceCommitEvidenceRead": bool(SHA.fullmatch(audit["sourceSha"])),
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

