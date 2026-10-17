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
PREFLIGHT = "Download and verify pinned official artifact uploader from GitHub API"
FORBIDDEN_STARTED = (
    "Restore signing material only after private verification",
    "Sign already-tested unsigned APK without executing Test code",
    "Verify certificate, package, version and signed APK digest",
    "Upload verified signed APK to private signing repository only",
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
    if steps.get(PREFLIGHT) != "failure":
        raise Hold("old_signer_not_preflight_failure")
    if any(steps.get(name) != "skipped" for name in FORBIDDEN_STARTED):
        raise Hold("old_signer_could_have_accessed_keys")

