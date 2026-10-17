"""Manual, fail-closed Android production build/sign coordinator.

Runs in the public Runner repository. Only private workflow METADATA is read:
no Test source, signing key, unsigned APK, signed APK or private log is fetched.
Each dispatch is immediately bound to GitHub's exact returned run ID.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
import re
import time
import urllib.error
import urllib.request

from scheduler.probes import existing_attempt, runner_for_repo, account_usage
from scheduler.android_route import choose as choose_route, workflow as workflow_for, artifact as artifact_for, HOSTED_WORKFLOW
from scheduler.policies import hosted_capacity

RUNNER = "chaonpu/Runner"
TEST = "anwpustudent-lab/Test"
SIGNER = "chaonpu/ziwuxilv-android"
TEST_WORKFLOW = "test-self-hosted.yml"
SIGN_WORKFLOW = "windows-signing-verified-handoff.yml"
CLOUD_SIGN_WORKFLOW = "cloud-signing-verified-handoff.yml"
STATE_PATH = "state/android-production.json"
CONFIG_PATH = "config/repositories.json"
SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[1-9][0-9]*$")
PHASES = {"reserved", "test_started", "test_retry_reserved", "test_verified",
          "signer_reserved", "signer_started", "signed_verified"}
POLL_SECONDS = 30
MAX_WAIT_SECONDS = 3 * 60 * 60


class Hold(Exception):
    """A human must inspect the original attempt before any new dispatch."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require(ok, reason):
    if not ok:
        raise Hold(reason)


def api(token, repo, path, *, method="GET", payload=None):
    require(repo in (RUNNER, TEST, SIGNER), "repository_not_allowlisted")
    require(bool(token), "credential_missing")
    require(re.fullmatch(r"/[A-Za-z0-9_./?=&-]+", path) is not None
            and ".." not in path and "//" not in path,
            "unsafe_api_path")
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + path,
        data=data, method=method,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2026-03-10",
                 "Content-Type": "application/json",
                 "User-Agent": "ziwuxilv-android-controlled-coordinator/1.0"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            raw = response.read(2_000_001)
        require(len(raw) <= 2_000_000, "response_too_large")
        value = json.loads(raw) if raw else {}
        require(isinstance(value, dict), "invalid_api_response")
        return value
    except urllib.error.HTTPError as exc:
        # No response body, URL, header, token or private metadata is logged.
        # This remains a hard stop (POSTs are never blindly retried).
        kind = ("runner_state" if "/actions/runners" in path else
                "workflow_state" if "/actions/workflows/" in path else
                "run_state" if "/actions/runs/" in path else
                "source_head" if path == "/branches/main" else
                "registry" if path.startswith("/contents/") else
                "other")
        raise Hold(f"github_http_{exc.code}_{repo.split('/')[0]}_{kind}_no_retry") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise Hold("github_network_or_schema_unknown_no_retry") from None


def json_file(token, path):
    item = api(token, RUNNER, "/contents/" + path)
    require(item.get("type") == "file"
            and re.fullmatch(r"[0-9a-f]{40}", str(item.get("sha", "")))
            and item.get("encoding") == "base64",
            "state_document_invalid")
    try:
        data = json.loads(base64.b64decode(item["content"]).decode("utf-8"))
    except (KeyError, ValueError, UnicodeError):
        raise Hold("state_document_invalid") from None
    require(isinstance(data, dict), "state_document_invalid")
    return data, item["sha"]


def put_state(public_token, source, phase, *, test_run_id=None, signing_run_id=None, signing_route=None):
    """Optimistic-SHA write. A conflict is never silently retried."""
    require(bool(SHA.fullmatch(source)) and phase in PHASES, "invalid_state_transition")
    record, blob = json_file(public_token, STATE_PATH)
    require(record.get("version") == 1 and isinstance(record.get("builds"), dict),
            "state_schema_invalid")
    entry = record["builds"].get(source)
    require(isinstance(entry, dict) and entry.get("phase") in PHASES,
            "reservation_missing")
    previous = entry["phase"]
    allowed = {
        "reserved": {"test_started"},
        "test_started": {"test_verified"},
        "test_retry_reserved": {"test_started"},
        "test_verified": {"signer_reserved"},
        "signer_reserved": {"signer_started"},
        "signer_started": {"signed_verified"},
        "signed_verified": set(),
    }
    require(phase in allowed[previous], "state_transition_not_allowed")
    if test_run_id is not None:
        require(type(test_run_id) is int and test_run_id > 0, "invalid_test_run_id")
        require(previous == "test_retry_reserved" or entry.get("testRunId") in (None, test_run_id), "test_run_binding_mismatch")
        entry["testRunId"] = test_run_id
    if signing_run_id is not None:
        require(type(signing_run_id) is int and signing_run_id > 0, "invalid_sign_run_id")
        require(entry.get("signingRunId") in (None, signing_run_id),
                "sign_run_binding_mismatch")
        entry["signingRunId"] = signing_run_id
    if signing_route is not None:
        require(phase == "signer_reserved" and signing_route in ("windows", "cloud")
                and not entry.get("signingRoute"), "signing_route_change_forbidden")
        entry["signingRoute"] = signing_route
    entry["phase"] = phase
    entry["updatedAt"] = datetime.now(timezone.utc).isoformat()
    body = {"message": "Track verified Android build/sign stage",
            "content": base64.b64encode((json.dumps(record, indent=2, sort_keys=True) + "\n").encode()).decode(),
            "sha": blob, "branch": "main"}
    result = api(public_token, RUNNER, "/contents/" + STATE_PATH,
                 method="PUT", payload=body)
    require(bool(result.get("commit", {}).get("sha")), "state_write_unknown")


def reserve(public_token, source, route="windows"):
    record, blob = json_file(public_token, STATE_PATH)
    require(record.get("version") == 1 and isinstance(record.get("builds"), dict),
            "state_schema_invalid")
    require(source not in record["builds"], "source_already_registered_no_redispatch")
    require(route in ("hosted", "windows"), "unknown_android_route")
    record["builds"][source] = {
        "route": route,
        "phase": "reserved",
        "sourceSha": source,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    body = {"message": "Reserve one immutable Android source build",
            "content": base64.b64encode((json.dumps(record, indent=2, sort_keys=True) + "\n").encode()).decode(),
            "sha": blob, "branch": "main"}
    result = api(public_token, RUNNER, "/contents/" + STATE_PATH,
                 method="PUT", payload=body)
    require(bool(result.get("commit", {}).get("sha")), "reservation_unknown_no_redispatch")


def current_source(tokens):
    """Read only branch SHA using an authorized Test Contents:read credential.

    Actions:write alone is not Contents:read. Do not skip exact SHA pinning.
    Only permission-denied read attempts can fall back to another configured token.
    """
    seen = set()
    for token in tokens:
        if not token or token in seen:
            continue
        seen.add(token)
        try:
            result = api(token, TEST, "/branches/main")
        except Hold as exc:
            if str(exc) in ("github_http_401_anwpustudent-lab_source_head_no_retry",
                            "github_http_403_anwpustudent-lab_source_head_no_retry",
                            "github_http_404_anwpustudent-lab_source_head_no_retry"):
                continue
            raise
        source = result.get("commit", {}).get("sha")
        require(isinstance(source, str) and SHA.fullmatch(source),
                "test_main_sha_unavailable")
        return source
    raise Hold("test_main_contents_read_permission_missing")


def check_idle(repo, workflow, account, env):
    record = existing_attempt(repo, workflow, account, env)
    require(record.get("status") == "no_active_run",
            "active_or_unknown_private_workflow_no_dispatch")


def runner_account(repo, account, env):
    """Prefer a Test-only Administration:read token if configured."""
    if repo == TEST and env.get("TEST_RUNNER_READ_TOKEN"):
        return {**account, "runnerSecret": "TEST_RUNNER_READ_TOKEN"}
    return account


def runner_ready(repo, name, account, env):
    r = runner_for_repo(repo, name, runner_account(repo, account, env), env)
    return (r.get("status") == "online" and r.get("busy") is False
            and r.get("labelsVerified") is True)


def dispatch(token, repo, workflow, inputs):
    path = "/actions/workflows/" + workflow + "/dispatches"
    require((repo, workflow) in ((TEST, TEST_WORKFLOW), (TEST, HOSTED_WORKFLOW), (SIGNER, SIGN_WORKFLOW), (SIGNER, CLOUD_SIGN_WORKFLOW)),
            "dispatch_target_not_allowlisted")
    response = api(token, repo, path, method="POST",
                   payload={"ref": "main", "return_run_details": True, "inputs": inputs})
    rid = response.get("workflow_run_id")
    require(type(rid) is int and rid > 0, "dispatch_run_id_unknown_no_redispatch")
    return rid


def validate_run(item, repo, workflow, rid, source=None):
    """Reject different workflow, branch, SHA, attempts or trigger."""
    require(isinstance(item, dict) and item.get("id") == rid
            and isinstance(item.get("repository"), dict)
            and item["repository"].get("full_name") == repo
            and item.get("event") == "workflow_dispatch"
            and item.get("head_branch") == "main"
            and item.get("run_attempt") == 1
            and item.get("path", "").split("@")[0] == ".github/workflows/" + workflow,
            "run_origin_mismatch")
    if source is not None:
        require(item.get("head_sha") == source, "source_moved_during_dispatch")


def wait_run(token, repo, workflow, rid, source=None):
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        item = api(token, repo, "/actions/runs/" + str(rid))
        validate_run(item, repo, workflow, rid, source)
        if item.get("status") == "completed":
            if item.get("conclusion") != "success":
                raise Hold(classify_failed_run(token, repo, rid))
            return
        require(item.get("status") in ("queued", "waiting", "pending", "in_progress", "requested"),
                "unrecognized_run_state")
        time.sleep(POLL_SECONDS)
    raise Hold("run_deadline_original_id_must_be_reviewed")


def classify_failed_run(token, repo, rid):
    """Public step-name-only classification; never download private logs or artifacts."""
    require(repo in (TEST, SIGNER) and type(rid) is int and rid > 0,
            "failure_origin_invalid")
    value = api(token, repo, "/actions/runs/" + str(rid) + "/jobs?per_page=100")
    jobs, count = value.get("jobs"), value.get("total_count")
    require(type(count) is int and 0 < count <= 100
            and isinstance(jobs, list) and len(jobs) == count,
            "failure_steps_unavailable_no_retry")
    failures = [
        step.get("name", "") for job in jobs
        if isinstance(job, dict) and job.get("conclusion") == "failure"
        for step in (job.get("steps") or [])
        if isinstance(step, dict) and step.get("conclusion") == "failure"
        and isinstance(step.get("name"), str)
    ]
    # A network code is trusted only when a dedicated failing diagnosis step
    # recorded it, not merely because actions/checkout or a download failed.
    if any(x.startswith(("NETWORK_TRANSIENT_PREFLIGHT",
                          "NETWORK_TRANSIENT_SOURCE_FETCH")) for x in failures):
        return "network_retry_exhausted_same_run_no_publication"
    if any(x.startswith("SOURCE_CHECKOUT_UNVERIFIED") for x in failures):
        return "source_checkout_unverified_manual_review"
    if repo == TEST:
        if any(x.startswith("Build unsigned Release APK") for x in failures):
            return "android_compile_failed_manual_review"
        if any(x.startswith(("Run unit tests", "Verify release and debug",
                             "Verify release version", "Verify Gradle wrapper")) for x in failures):
            return "android_tests_or_lint_failed_manual_review"
        return "android_test_or_artifact_failed_manual_review"
    if any(x.startswith("Sign already-tested unsigned APK") for x in failures):
        return "android_signing_failed_manual_review"
    if any(x.startswith("Verify certificate, package") for x in failures):
        return "android_signature_verification_failed_manual_review"
    return "android_signer_setup_or_handoff_failed_manual_review"


def record_failed_stage(public_token, source, stage, run_id, reason):
    """Durably record a SAFE error code without changing the release gate."""
    require(bool(SHA.fullmatch(source)) and stage in ("test", "signer")
            and type(run_id) is int and run_id > 0
            and bool(re.fullmatch(r"[a-z0-9_]{4,100}", reason)),
            "failure_record_invalid")
    state, blob = json_file(public_token, STATE_PATH)
    require(state.get("version") == 1 and isinstance(state.get("builds"), dict),
            "failure_state_invalid")
    row = state["builds"].get(source)
    phase = "test_started" if stage == "test" else "signer_started"
    field = "testRunId" if stage == "test" else "signingRunId"
    require(isinstance(row, dict) and row.get("sourceSha") == source
            and row.get("phase") == phase and row.get(field) == run_id,
            "failure_run_binding_mismatch")
    row["lastFailure"] = {
        "stage": stage, "code": reason, "runId": run_id,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }
    body = {"message": "Record safe Android stage failure category",
            "content": base64.b64encode((
                json.dumps(state, indent=2, sort_keys=True) + "\n"
            ).encode()).decode(), "sha": blob, "branch": "main"}
    saved = api(public_token, RUNNER, "/contents/" + STATE_PATH,
                method="PUT", payload=body)
    require(bool(saved.get("commit", {}).get("sha")), "failure_state_write_unknown")


def wait_recorded(public_token, source, stage, private_token, repo, workflow,
                  run_id, pinned_sha=None):
    try:
        wait_run(private_token, repo, workflow, run_id, pinned_sha)
    except Hold as exc:
        reason = str(exc)
        written = False
        try:
            record_failed_stage(public_token, source, stage, run_id, reason)
            written = True
        except Hold:
            pass  # Preserve original failure; no signing/publication is authorized.
        record_public(source, stage + "_failed", runId=run_id,
                      failureClass=reason, failureRecorded=written,
                      publication="not_authorized")
        raise



def retry_checkout_before_business(pub, test, source, entry, test_account, env, source_workflow):
    """At most one same-SHA retry, only after an attested pre-build HTTPS failure.

    Reserve the retry intent before the second POST. Ambiguous reservation or
    dispatch cannot be retried blindly; no partial Test build is reused.
    """
    require(entry.get("phase") == "test_started" and entry.get("route") == "windows"
            and source_workflow == TEST_WORKFLOW, "checkout_retry_route_invalid")
    old = entry.get("testRunId")
    require(type(old) is int and old > 0, "checkout_retry_original_run_missing")
    original = api(test, TEST, "/actions/runs/" + str(old))
    validate_run(original, TEST, source_workflow, old, source)
    require(original.get("status") == "completed" and original.get("conclusion") == "failure",
            "checkout_retry_original_not_failed")
    require(classify_failed_run(test, TEST, old)
            == "network_retry_exhausted_same_run_no_publication",
            "checkout_retry_failure_not_proven_network")
    jobs = api(test, TEST, "/actions/runs/" + str(old) + "/jobs?per_page=100")
    require(jobs.get("total_count") == 1 and isinstance(jobs.get("jobs"), list)
            and len(jobs["jobs"]) == 1, "checkout_retry_job_proof_incomplete")
    job = jobs["jobs"][0]
    steps = job.get("steps", [])
    require(job.get("conclusion") == "failure" and isinstance(steps, list)
            and any(isinstance(x, dict) and x.get("name", "").startswith(
                ("NETWORK_TRANSIENT_PREFLIGHT", "NETWORK_TRANSIENT_SOURCE_FETCH"))
                    and x.get("conclusion") == "failure" for x in steps)
            and all(x.get("conclusion") in ("skipped", None)
                    for x in steps if isinstance(x, dict) and x.get("name", "").startswith(
                        ("Build unsigned Release APK", "Run unit tests", "Upload verified Windows source"))),
            "checkout_retry_prebuild_boundary_unverified")
    arts = api(test, TEST, "/actions/runs/" + str(old) + "/artifacts?per_page=100")
    require(arts.get("total_count") == 0 and arts.get("artifacts") == [],
            "checkout_retry_original_artifacts_exist")
    require(entry.get("testRetries", 0) == 0, "checkout_retry_limit_reached")
    # Dispatched workflow_dispatch on main must still be this exact original source.
    tokens = (env.get("TEST_SOURCE_READ_TOKEN", ""), test,
              env.get("TEST_RUNNER_READ_TOKEN", ""), env.get("TEST_BILLING_READ_TOKEN", ""))
    require(current_source(tokens) == source, "checkout_retry_source_no_longer_main")
    check_idle(TEST, TEST_WORKFLOW, test_account, env)
    require(runner_ready(TEST, env["_test_runner_name"], test_account, env),
            "checkout_retry_windows_runner_not_ready")
    state, blob = json_file(pub, STATE_PATH)
    row = state.get("builds", {}).get(source)
    require(row == entry, "checkout_retry_record_changed_no_repeat")
    row.update(phase="test_retry_reserved", testRetries=1,
               failedTestRunIds=[old], updatedAt=datetime.now(timezone.utc).isoformat())
    payload = {"branch": "main", "sha": blob,
               "message": "Reserve one same-SHA Android pre-build network recovery",
               "content": base64.b64encode((json.dumps(state, indent=2, sort_keys=True)+"\n").encode()).decode()}
    response = api(pub, RUNNER, "/contents/" + STATE_PATH, method="PUT", payload=payload)
    require(bool(response.get("commit", {}).get("sha")), "checkout_retry_reservation_uncertain")
    confirmed, _ = json_file(pub, STATE_PATH)
    require(confirmed == state, "checkout_retry_reservation_unconfirmed")
    fresh = dispatch(test, TEST, TEST_WORKFLOW, {})
    put_state(pub, source, "test_started", test_run_id=fresh)
    record_public(source, "test_checkout_recovered", previousTestRunId=old,
                  testRunId=fresh, retryCount=1, publication="not_authorized")
    return fresh

def verify_execution_runner(token, repo, rid, expected):
    """A successful job must actually have run on the approved private service."""
    response = api(token, repo, "/actions/runs/" + str(rid) + "/jobs?per_page=100")
    jobs = response.get("jobs")
    count = response.get("total_count")
    require(type(count) is int and 0 < count <= 100
            and isinstance(jobs, list) and len(jobs) == count,
            "private_run_jobs_incomplete")
    completed = [job for job in jobs
                 if job.get("runner_name") == expected
                 and job.get("conclusion") == "success"]
    require(len(completed) == 1, "expected_private_runner_not_verified")


def verify_hosted_execution(token, repo, rid):
    """Cloud results must be from exactly one successful standard Ubuntu job."""
    response = api(token, repo, "/actions/runs/" + str(rid) + "/jobs?per_page=100")
    jobs = response.get("jobs")
    require(response.get("total_count") == 1 and isinstance(jobs, list)
            and len(jobs) == 1, "hosted_run_jobs_ambiguous")
    job = jobs[0]
    labels = job.get("labels")
    require(job.get("conclusion") == "success" and isinstance(labels, list)
            and "ubuntu-24.04" in labels and "self-hosted" not in labels,
            "hosted_standard_ubuntu_not_verified")


def verify_artifact(token, repo, rid, expected):
    response = api(token, repo, "/actions/runs/" + str(rid) + "/artifacts?per_page=100")
    artifacts = response.get("artifacts")
    require(isinstance(artifacts, list), "artifact_list_unavailable")
    matches = [x for x in artifacts if x.get("name") == expected
               and x.get("expired") is False
               and type(x.get("size_in_bytes")) is int
               and x.get("size_in_bytes") > 0]
    require(len(matches) == 1, "verified_private_artifact_missing_or_ambiguous")


def choose_signer_route(task, account, env):
    """Independent verified allowance for chaonpu signing account."""
    usage = account_usage("chaonpu", account, env)
    capacity = hosted_capacity(usage, task)
    if capacity == "available":
        return "cloud", CLOUD_SIGN_WORKFLOW
    if capacity in ("insufficient", "exhausted") and runner_ready(
            SIGNER, task["signingRunnerName"], account, env):
        return "windows", SIGN_WORKFLOW
    raise Hold("no_verified_android_signing_route")


def sign_route_for(entry):
    route = entry.get("signingRoute", "windows")
    require(route in ("windows", "cloud"), "registered_signer_route_invalid")
    return route, CLOUD_SIGN_WORKFLOW if route == "cloud" else SIGN_WORKFLOW


def record_public(source, phase, **extra):
    print(json.dumps({"sourceSha": source, "phase": phase, **extra}, sort_keys=True), flush=True)


def run(env, *, execute=False, resume=False, requested_sha=""):
    pub = env.get("GH_TOKEN", "")
    test = env.get("TEST_ACTIONS_READ_TOKEN", "")
    sign = env.get("CHAONPU_ACTIONS_READ_TOKEN", "")
    config, _ = json_file(pub, CONFIG_PATH)
    task = config.get("tasks", {}).get("android", {})
    require(task.get("repo") == TEST and task.get("signingRepo") == SIGNER
            and task.get("windowsWorkflow") == TEST_WORKFLOW
            and task.get("signingWindowsWorkflow") == SIGN_WORKFLOW
            and task.get("signingCloudWorkflow") == CLOUD_SIGN_WORKFLOW,
            "android_workflow_configuration_changed")
    # A resumed exact-source run may be older than current Test/main.
    # New builds, however, must always start from the current main SHA.
    if resume:
        require(bool(requested_sha) and SHA.fullmatch(requested_sha),
                "resume_requires_original_source_sha")
    branch_verified = False
    source = requested_sha if resume else ""
    if not resume:
        try:
            source = current_source((
                env.get("TEST_SOURCE_READ_TOKEN", ""),
                env.get("TEST_ACTIONS_READ_TOKEN", ""),
                env.get("TEST_RUNNER_READ_TOKEN", ""),
                env.get("TEST_BILLING_READ_TOKEN", "")))
            branch_verified = True
            if requested_sha:
                require(source == requested_sha, "requested_sha_not_current_test_main")
        except Hold as exc:
            if str(exc) != "test_main_contents_read_permission_missing":
                raise
            # An operator-supplied SHA is a reservation key, NOT verified source.
            # The subsequent exact Test workflow_dispatch Run's head_sha must
            # prove it was truly main at dispatch time, before signing begins.
            source = requested_sha
            if execute and not source:
                raise Hold("source_sha_required_without_contents_read") from None
    if source:
        require(isinstance(source, str) and bool(SHA.fullmatch(source)), "invalid_source_sha")
    state, _ = json_file(pub, STATE_PATH)
    require(state.get("version") == 1 and isinstance(state.get("builds"), dict),
            "state_schema_invalid")
    entry = state["builds"].get(source)
    test_account = config["accounts"]["anwpustudent-lab"]
    sign_account = config["accounts"]["chaonpu"]
    if entry is not None:
        require(isinstance(entry, dict), "registered_android_entry_invalid")
        route = entry.get("route", "windows")
        require(route in ("windows", "hosted"), "registered_android_route_unknown")
        plan = {"route": route, "reason": "registered_original_route",
                "hostedQuotaGuaranteed": False}
    else:
        # Use the shared 100-minute threshold before any dispatch. Unknown
        # billing pauses execution rather than assuming self-hosted eligibility.
        billing = (account_usage("anwpustudent-lab", test_account, env)
                   if test_account.get("billingSecret") else {})
        plan = choose_route(task, billing)
        route = plan["route"]
    source_workflow = workflow_for(route) if route in ("windows", "hosted") else None
    source_artifact = artifact_for(route) if route in ("windows", "hosted") else None
    if not execute:
        test_probe = runner_for_repo(TEST, task["runnerName"],
                                       runner_account(TEST, test_account, env), env)
        sign_probe = runner_for_repo(SIGNER, task["signingRunnerName"], sign_account, env)
        def is_ready(item):
            return (item.get("status") == "online" and item.get("busy") is False
                    and item.get("labelsVerified") is True)
        record_public(source or None, "read_only",
                      priorPhase=entry.get("phase") if entry else None,
                      branchShaVerified=branch_verified,
                      manualSourceShaRequired=not bool(source),
                      route=route, routeReason=plan["reason"],
                      hostedQuotaGuaranteed=False,
                      testRunnerReady=is_ready(test_probe),
                      signerRunnerReady=is_ready(sign_probe),
                      testRunnerProbe={"read": test_probe.get("read"),
                                       "status": test_probe.get("status")},
                      signerRunnerProbe={"read": sign_probe.get("read"),
                                         "status": sign_probe.get("status")},
                      testWorkflowActiveStatus=existing_attempt(
                          TEST, TEST_WORKFLOW, test_account, env).get("status"),
                      signingWorkflowActiveStatus=existing_attempt(
                          SIGNER, SIGN_WORKFLOW, sign_account, env).get("status"),
                      hostedTestWorkflowStatus=existing_attempt(
                          TEST, HOSTED_WORKFLOW, test_account, env).get("status"))
        return

    require(route in ("windows", "hosted"), "hosted_allowance_unverified_dispatch_paused")
    if resume:
        require(bool(requested_sha) and SHA.fullmatch(requested_sha),
                "resume_requires_original_source_sha")
        require(isinstance(entry, dict) and entry.get("sourceSha") == source
                and entry.get("phase") in ("test_started", "test_verified", "signer_started"),
                "no_resumable_original_attempt")
    else:
        require(entry is None, "source_already_registered_no_redispatch")
        check_idle(TEST, TEST_WORKFLOW, test_account, env)
        check_idle(TEST, HOSTED_WORKFLOW, test_account, env)
        check_idle(SIGNER, SIGN_WORKFLOW, sign_account, env)
        check_idle(SIGNER, CLOUD_SIGN_WORKFLOW, sign_account, env)
        choose_signer_route(task, sign_account, env)
        require(route == "hosted" or runner_ready(
                    TEST, task["runnerName"], test_account, env),
                "required_private_test_runner_not_ready")
        reserve(pub, source, route=route)
        # This exact route is fixed before the first potentially irreversible POST.
        # A failed cloud job must NOT be redispatched to Windows after it starts.
        test_run = dispatch(test, TEST, source_workflow, {})
        put_state(pub, source, "test_started", test_run_id=test_run)
        record_public(source, "test_started", testRunId=test_run,
                      route=route, quotaBasis=plan["reason"])
        entry = {"phase": "test_started", "testRunId": test_run, "route": route}

    if entry["phase"] == "test_started":
        test_run = entry.get("testRunId")
        require(type(test_run) is int and test_run > 0, "unknown_test_dispatch_no_retry")
        try:
            wait_recorded(pub, source, "test", test, TEST,
                          source_workflow, test_run, source)
        except Hold as failure:
            if str(failure) != "network_retry_exhausted_same_run_no_publication":
                raise
            latest, _ = json_file(pub, STATE_PATH)
            recorded = latest["builds"].get(source, {})
            retry_env = dict(env, _test_runner_name=task["runnerName"])
            test_run = retry_checkout_before_business(pub, test, source, recorded,
                                                      test_account, retry_env, source_workflow)
            wait_recorded(pub, source, "test", test, TEST,
                          source_workflow, test_run, source)
        if route == "hosted":
            verify_hosted_execution(test, TEST, test_run)
        else:
            verify_execution_runner(test, TEST, test_run, task["runnerName"])
        verify_artifact(test, TEST, test_run, source_artifact)
        put_state(pub, source, "test_verified")
        record_public(source, "test_verified", testRunId=test_run)
        entry = {"phase": "test_verified", "testRunId": test_run}

    if entry["phase"] == "test_verified":
        test_run = entry["testRunId"]
        # Never create another Test job on resume; revalidate the original run.
        wait_recorded(pub, source, "test", test, TEST,
                      source_workflow, test_run, source)
        verify_artifact(test, TEST, test_run, source_artifact)
        check_idle(SIGNER, SIGN_WORKFLOW, sign_account, env)
        check_idle(SIGNER, CLOUD_SIGN_WORKFLOW, sign_account, env)
        signing_route, signing_workflow = choose_signer_route(task, sign_account, env)
        # Persist chosen signing route AND irreversible intent before POST.
        put_state(pub, source, "signer_reserved", signing_route=signing_route)
        sign_run = dispatch(sign, SIGNER, signing_workflow,
                            {"test_run_id": str(test_run), "source_commit": source})
        put_state(pub, source, "signer_started", signing_run_id=sign_run)
        record_public(source, "signer_started", signingRunId=sign_run,
                      signingRoute=signing_route)
        entry = {"phase": "signer_started", "testRunId": test_run,
                 "signingRunId": sign_run, "signingRoute": signing_route}

    if entry["phase"] == "signer_started":
        sign_run = entry.get("signingRunId")
        require(type(sign_run) is int and sign_run > 0,
                "unknown_signing_dispatch_no_retry")
        signing_route, signing_workflow = sign_route_for(entry)
        wait_recorded(pub, source, "signer", sign, SIGNER,
                      signing_workflow, sign_run)
        if signing_route == "cloud":
            verify_hosted_execution(sign, SIGNER, sign_run)
        else:
            verify_execution_runner(sign, SIGNER, sign_run, task["signingRunnerName"])
        verify_artifact(sign, SIGNER, sign_run, "windows-signed-release")
        put_state(pub, source, "signed_verified")
        record_public(source, "signed_verified", testRunId=entry["testRunId"],
                      signingRunId=sign_run,
                      publication="not_authorized_no_public_apk")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--source-sha", default="")
    args = parser.parse_args()
    try:
        require(not args.resume or args.execute, "resume_requires_execute")
        run(os.environ, execute=args.execute, resume=args.resume,
            requested_sha=args.source_sha)
        return 0
    except Hold as exc:
        reason = str(exc)
        retry_exhausted = reason == "network_retry_exhausted_same_run_no_publication"
        print(json.dumps({
            "status": "network_retry_exhausted" if retry_exhausted else "hold",
            "reason": reason,
            "retryScope": "original_run_only_no_new_dispatch",
            "safeToRedispatch": False,
            "publication": "not_authorized",
        }), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
