"""One-time controlled Android private signing recovery.

The original Test run is never rebuilt; a pre-signing failure must be proven
by GitHub's original job steps before any new request can be dispatched.
"""

from __future__ import annotations
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from scheduler.android_production import (
    Hold, RUNNER, TEST, SIGNER, TEST_WORKFLOW, SIGN_WORKFLOW, STATE_PATH,
    SHA, api, json_file, validate_run, verify_artifact, wait_run,
    verify_execution_runner, runner_ready, check_idle, dispatch,
    put_state, record_public,
)
from scheduler.android_launch_request import changed_paths

REQUEST = Path("config/android-signing-recovery.json")
WORKFLOW = (
    "chaonpu/Runner/.github/workflows/android-signing-recovery-launch.yml@refs/heads/main"
)
PREFLIGHT = "Verify private Release transport policy"
FORBIDDEN_STARTED = (
    "Restore signing material only after private verification",
    "Sign already-tested unsigned APK without executing Test code",
    "Verify certificate, package, version and signed APK digest",
    "Publish verified signed APK to PRIVATE Release Assets (not Actions Artifact)",
)


def approve(doc):
    keys = {"version", "enabled", "sourceSha", "testRunId", "failedSigningRunId", "approval"}
    if not isinstance(doc, dict) or set(doc) != keys or doc["version"] != 1:
        raise Hold("recovery_request_invalid")
    if doc["enabled"] is False:
        if (doc["sourceSha"] or doc["testRunId"] or doc["failedSigningRunId"] or doc["approval"]):
            raise Hold("disabled_request_not_empty")
        return None
    sha, test_id, failed_id = doc["sourceSha"], doc["testRunId"], doc["failedSigningRunId"]
    if (doc["enabled"] is not True or not isinstance(sha, str) or not SHA.fullmatch(sha)
            or type(test_id) is not int or test_id < 1
            or type(failed_id) is not int or failed_id < 1
            or doc["approval"] != f"RECOVER-SIGNER-{sha}-{failed_id}"):
        raise Hold("signing_recovery_not_approved")
    return sha, test_id, failed_id


def guard_context(env):
    if (env.get("GITHUB_REPOSITORY") != RUNNER or env.get("GITHUB_ACTOR") != "chaonpu"
            or env.get("GITHUB_EVENT_NAME") != "push"
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_RUN_ATTEMPT") != "1"
            or env.get("GITHUB_WORKFLOW_REF") != WORKFLOW):
        raise Hold("recovery_context_invalid")
    before, after = env.get("GITHUB_EVENT_BEFORE", ""), env.get("GITHUB_SHA", "")
    if not SHA.fullmatch(before) or not SHA.fullmatch(after) or before == after:
        raise Hold("recovery_git_range_invalid")
    if changed_paths(before, after) != ["config/android-signing-recovery.json"]:
        raise Hold("recovery_request_must_be_only_modified_file")


def verify_no_signing_started(response):
    jobs = response.get("jobs")
    if response.get("total_count") != 1 or not isinstance(jobs, list) or len(jobs) != 1:
        raise Hold("old_signer_jobs_not_proven")
    job = jobs[0]
    if job.get("conclusion") != "failure" or not isinstance(job.get("steps"), list):
        raise Hold("old_signer_result_not_failure")
    steps = {s.get("name"): s.get("conclusion") for s in job["steps"] if isinstance(s, dict)}
    allowed_early_failures = {
        PREFLIGHT,
        "Test official GitHub API uploader integrity and interrupted transfer recovery",
    }
    if not any(steps.get(name) == "failure" for name in allowed_early_failures):
        raise Hold("old_signer_not_pre_key_material_failure")
    if any(steps.get(name) != "skipped" for name in FORBIDDEN_STARTED):
        raise Hold("old_signer_could_have_accessed_keys")


def change_state(token, source, test_id, prior_id, next_phase, new_id=None):
    """CAS transition reserves signing retry BEFORE dispatch; no blind repeat."""
    document, etag = json_file(token, STATE_PATH)
    entry = document.get("builds", {}).get(source)
    if (not isinstance(entry, dict) or entry.get("sourceSha") != source
            or entry.get("testRunId") != test_id):
        raise Hold("recovery_state_not_bound_to_original_test")
    if next_phase == "sign_retry_reserved":
        prior_history = entry.get("signingRecoveryHistory")
        if prior_history is None:
            prior_history = ([entry["recoveryFromSigningRunId"]]
                             if entry.get("recoveryFromSigningRunId") else [])
        if (not isinstance(prior_history, list) or len(prior_history) >= 2
                or entry.get("phase") != "signer_started"
                or entry.get("signingRunId") != prior_id
                or prior_id in prior_history):
            raise Hold("recovery_limit_or_signer_state_mismatch")
        entry["phase"] = next_phase
        entry["signingRecoveryHistory"] = prior_history + [prior_id]
        entry["recoveryFromSigningRunId"] = prior_id
    elif next_phase == "signer_started":
        if (entry.get("phase") != "sign_retry_reserved"
                or entry.get("recoveryFromSigningRunId") != prior_id
                or entry.get("signingRunId") != prior_id
                or type(new_id) is not int or new_id <= 0 or new_id == prior_id):
            raise Hold("recovery_pre_dispatch_reservation_missing")
        entry["phase"] = next_phase
        entry["signingRunId"] = new_id
    else:
        raise Hold("invalid_recovery_state_transition")
    entry["updatedAt"] = datetime.now(timezone.utc).isoformat()
    encoded = base64.b64encode((json.dumps(document, sort_keys=True, indent=2) + "\n").encode()).decode()
    result = api(token, RUNNER, "/contents/" + STATE_PATH, method="PUT",
                 payload={"message": "Track private Android signer recovery",
                          "content": encoded, "sha": etag, "branch": "main"})
    if not result.get("commit", {}).get("sha"):
        raise Hold("recovery_state_write_unknown")



def perform(env, source, test_id, failed_id):
    public = env.get("GH_TOKEN", "")
    test_token = env.get("TEST_ACTIONS_READ_TOKEN", "")
    signer_token = env.get("CHAONPU_ACTIONS_READ_TOKEN", "")
    document, _ = json_file(public, STATE_PATH)
    entry = document.get("builds", {}).get(source)
    if (not isinstance(entry, dict) or entry.get("phase") != "signer_started"
            or entry.get("testRunId") != test_id
            or entry.get("signingRunId") != failed_id
            or entry.get("recoveryFromSigningRunId") == failed_id
            or len(entry.get("signingRecoveryHistory") or
                   ([entry["recoveryFromSigningRunId"]]
                    if entry.get("recoveryFromSigningRunId") else [])) >= 2):
        raise Hold("signing_retry_limit_or_state_mismatch")

    failed = api(signer_token, SIGNER, "/actions/runs/" + str(failed_id))
    validate_run(failed, SIGNER, SIGN_WORKFLOW, failed_id)
    if failed.get("status") != "completed" or failed.get("conclusion") != "failure":
        raise Hold("old_signing_run_not_completed_failure")
    jobs = api(signer_token, SIGNER, "/actions/runs/" + str(failed_id) + "/jobs?per_page=100")
    verify_no_signing_started(jobs)

    test = api(test_token, TEST, "/actions/runs/" + str(test_id))
    validate_run(test, TEST, TEST_WORKFLOW, test_id, source)
    if test.get("status") != "completed" or test.get("conclusion") != "success":
        raise Hold("test_run_not_verified")
    verify_artifact(env.get("TEST_SOURCE_READ_TOKEN") or test_token, TEST, test_id, "windows-test-verified", source)

    config, _ = json_file(public, "config/repositories.json")
    account = config["accounts"]["chaonpu"]
    runner_name = config["tasks"]["android"]["signingRunnerName"]
    check_idle(SIGNER, SIGN_WORKFLOW, account, env)
    if not runner_ready(SIGNER, runner_name, account, env):
        raise Hold("signing_runner_not_ready")
    change_state(public, source, test_id, failed_id, "sign_retry_reserved")
    new_id = dispatch(signer_token, SIGNER, SIGN_WORKFLOW,
                      {"test_run_id": str(test_id), "source_commit": source})
    change_state(public, source, test_id, failed_id, "signer_started", new_id)
    record_public(source, "signer_started", signingRunId=new_id,
                  previousSigningRunId=failed_id, testRunId=test_id)
    wait_run(signer_token, SIGNER, SIGN_WORKFLOW, new_id)
    verify_execution_runner(signer_token, SIGNER, new_id, runner_name)
    verify_artifact(env.get("CHAONPU_SOURCE_READ_TOKEN") or signer_token, SIGNER, new_id, "windows-signed-release", source)
    put_state(public, source, "signed_verified")
    record_public(source, "signed_verified", signingRunId=new_id,
                  testRunId=test_id, publication="not_authorized_no_public_apk")


def main():
    try:
        guard_context(os.environ)
        values = approve(json.loads(REQUEST.read_text(encoding="utf-8")))
        if values is None:
            print('{"recovery":"disabled","dispatches":0}', flush=True)
            return 0
        perform(os.environ, *values)
        return 0
    except (Hold, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, Hold) else "recovery_input_error"
        print(json.dumps({"recovery": "hold", "reason": reason,
                          "safeToRedispatch": False,
                          "publicRelease": False}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
