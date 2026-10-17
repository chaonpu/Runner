"""One explicitly reviewed public configuration-file push starts one private Android chain.

Only the exact Runner request JSON is read. Source SHA is public metadata.
No private repository is cloned and neither unsigned APK nor keys are touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

from scheduler.android_production import Hold, SHA, run

REQUEST = Path("config/android-launch-request.json")
EXACT_KEYS = {"version", "enabled", "sourceSha", "approval"}
EXPECTED_WORKFLOW = (
    "chaonpu/Runner/.github/workflows/android-production-launch.yml@refs/heads/main"
)


def reviewed_request(data):
    if not isinstance(data, dict) or set(data) != EXACT_KEYS or data.get("version") != 1:
        raise Hold("android_launch_schema_invalid")
    if data["enabled"] is False:
        if data["sourceSha"] != "" or data["approval"] != "":
            raise Hold("disabled_request_must_be_empty")
        return None
    source = data["sourceSha"]
    if (data["enabled"] is not True or not isinstance(source, str)
            or not SHA.fullmatch(source)
            or data["approval"] != "ANDROID-PRIVATE-BUILD-" + source):
        raise Hold("android_explicit_approval_invalid")
    return source


def check_context(env, changed):
    if (env.get("GITHUB_REPOSITORY") != "chaonpu/Runner"
            or env.get("GITHUB_ACTOR") != "chaonpu"
            or env.get("GITHUB_EVENT_NAME") != "push"
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_RUN_ATTEMPT") != "1"
            or env.get("GITHUB_WORKFLOW_REF") != EXPECTED_WORKFLOW):
        raise Hold("android_launch_context_untrusted")
    before = env.get("GITHUB_EVENT_BEFORE", "")
    after = env.get("GITHUB_SHA", "")
    if not SHA.fullmatch(before) or not SHA.fullmatch(after) or before == after:
        raise Hold("android_launch_commit_range_invalid")
    if changed != ["config/android-launch-request.json"]:
        raise Hold("android_launch_must_change_only_reviewed_request")


def changed_paths(before, after):
    process = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", before, after],
        capture_output=True, text=True, check=False, timeout=15)
    if process.returncode != 0:
        raise Hold("android_launch_git_diff_unverifiable")
    return sorted(line for line in process.stdout.splitlines() if line)


def main():
    env = dict(os.environ)
    try:
        before, after = env.get("GITHUB_EVENT_BEFORE", ""), env.get("GITHUB_SHA", "")
        if not SHA.fullmatch(before) or not SHA.fullmatch(after):
            raise Hold("android_launch_commit_range_invalid")
        check_context(env, changed_paths(before, after))
        data = json.loads(REQUEST.read_text(encoding="utf-8"))
        source = reviewed_request(data)
        if not source:
            print('{"launch":"disabled","dispatches":0}', flush=True)
            return 0
        print(json.dumps({"launch":"approved_private_build_only",
                          "sourceSha":source,"publicRelease":False}), flush=True)
        run(env, execute=True, resume=False, requested_sha=source)
        print(json.dumps({"launch":"private_signing_chain_verified",
                          "sourceSha":source,"publicRelease":False}), flush=True)
        return 0
    except (Hold, OSError, ValueError, json.JSONDecodeError) as exc:
        reason = str(exc) if isinstance(exc, Hold) else "request_parse_or_git_error"
        print(json.dumps({"launch":"hold","reason":reason,
                          "safeToRepeat":False,"publicRelease":False}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
