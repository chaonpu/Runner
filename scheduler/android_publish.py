"""Manual signed-APK publication with durable exact-run binding.

One-click dispatch reuses CHAONPU_ACTIONS_READ_TOKEN, explicitly authorized by
the owner for the public release repository as well as existing private tasks.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from scheduler import android_production as build
from scheduler import apk_stage
from scheduler.apk_stage import CERT, VERSION, public_version, GuardError

REPO = "chaonpu/ziwuxilv"
WORKFLOW = "publish-android-release.yml"
STATE = "state/android-releases.json"
WORKFLOW_REF = "chaonpu/Runner/.github/workflows/android-publish.yml@refs/heads/main"


def need(ok, reason):
    if not ok:
        raise build.Hold(reason)


def public_api(path, token="", *, payload=None):
    # No arbitrary destination, no credential-bearing redirect, no POST retry.
    need(path in ("/contents/android-native/version.json?ref=main",
                  "/actions/workflows/" + WORKFLOW,
                  "/actions/workflows/" + WORKFLOW + "/dispatches")
         or re.fullmatch(r"/actions/runs/[1-9][0-9]*", path), "public_endpoint_forbidden")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ziwuxilv-manual-publish",
               "X-GitHub-Api-Version": "2026-03-10", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request("https://api.github.com/repos/" + REPO + path,
        headers=headers, method="GET" if payload is None else "POST",
        data=None if payload is None else json.dumps(payload).encode())
    try:
        with urllib.request.build_opener(build.NoRedirect()).open(req, timeout=25) as response:
            raw = response.read(2_000_001)
        need(len(raw) <= 2_000_000, "public_response_too_large")
        value = json.loads(raw) if raw else {}
        need(isinstance(value, dict), "public_response_invalid")
        return value
    except build.Hold:
        raise
    except Exception:
        raise build.Hold("public_api_unavailable_or_dispatch_unknown_no_retry") from None


def validate_context(env):
    need(env.get("GITHUB_REPOSITORY") == build.RUNNER
         and env.get("GITHUB_ACTOR") == "chaonpu"
         and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("GITHUB_WORKFLOW_REF") == WORKFLOW_REF
         and env.get("GITHUB_RUN_ATTEMPT") == "1", "manual_publish_context_required")


def signed_request(env, source, version, code, log):
    need(bool(build.SHA.fullmatch(source)) and bool(VERSION.fullmatch(version))
         and type(code) is int and code > 0 and 1 <= len(log.strip()) <= 6000,
         "invalid_source_version_or_update_log")
    state, _ = build.json_file(env["GH_TOKEN"], build.STATE_PATH)
    row = state.get("builds", {}).get(source, {})
    need(row.get("phase") == "signed_verified" and row.get("sourceSha") == source,
         "source_not_verified_signed")
    need(all(type(row.get(k)) is int and row[k] > 0 for k in ("testRunId", "signingRunId")),
         "exact_test_or_signing_run_missing")
    return {"sourceCommit": source, "versionName": version, "versionCode": code,
            "testRunId": row["testRunId"], "signingRunId": row["signingRunId"],
            "updateLog": log, "forceUpdate": False}


def candidate_version(env, row):
    """Read the version from the exact private signed APK, without publishing."""
    need(bool(env.get("CHAONPU_ACTIONS_READ_TOKEN")) and bool(env.get("TEST_ACTIONS_READ_TOKEN")),
         "private_read_credentials_missing")
    expected = {"sourceCommit": row["sourceSha"], "testRunId": row["testRunId"],
                "signingRunId": row["signingRunId"]}
    apk_stage.verify_provenance(dict(expected), env["CHAONPU_ACTIONS_READ_TOKEN"],
                                env["TEST_ACTIONS_READ_TOKEN"])
    with tempfile.TemporaryDirectory(prefix="signed-publication-preview-") as folder:
        root = Path(folder)
        private_env = dict(env, GH_TOKEN=env["CHAONPU_ACTIONS_READ_TOKEN"])
        result = subprocess.run(["gh", "run", "download", str(row["signingRunId"]),
            "--repo", build.SIGNER, "--name", "windows-signed-release", "--dir", str(root)],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=private_env, timeout=240)
        need(result.returncode == 0, "verified_private_signed_artifact_unavailable")
        path = root / "publish-candidate-metadata.json"
        need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384,
             "signed_candidate_metadata_invalid")
        metadata = json.loads(path.read_text(encoding="utf-8-sig"))
        need(isinstance(metadata, dict), "signed_candidate_metadata_invalid")
        version, code = metadata.get("versionName"), metadata.get("versionCode")
        need(isinstance(version, str) and VERSION.fullmatch(version)
             and type(code) is int and code > 0, "signed_candidate_version_invalid")
        expected.update(versionName=version, versionCode=code)
        apk, _, _ = apk_stage.assert_clean_artifact(root, expected)
        apk_stage.verify_apk_bytes(apk, expected)
        return version, code


def automatic_request(env, log=""):
    """Resume one known publication, otherwise use the newest registered build.

    Never fall back to an older signed build when the newest is incomplete.
    Uncertain stage/dispatch barriers still require original-run reconciliation.
    """
    validate_context(env)
    releases, _ = build.json_file(env["GH_TOKEN"], STATE)
    need(releases.get("version") == 1 and isinstance(releases.get("releases"), dict),
         "release_registry_invalid")
    rows = list(releases["releases"].values())
    phases = {"staging_reserved", "prepared", "dispatch_reserved", "publishing", "published"}
    need(all(isinstance(r, dict) and r.get("phase") in phases for r in rows),
         "release_registry_invalid")
    pending = [r for r in rows if r["phase"] != "published"]
    need(len(pending) <= 1, "multiple_publications_require_review")
    previous = pending[0] if pending else None
    if previous:
        need(previous["phase"] not in ("staging_reserved", "dispatch_reserved"),
             "publication_outcome_unknown_review_original_run")
    else:
        state, _ = build.json_file(env["GH_TOKEN"], build.STATE_PATH)
        need(state.get("version") == 1 and isinstance(state.get("builds"), dict)
             and bool(state["builds"]), "no_registered_android_build")
        dated = []
        for source, row in state["builds"].items():
            need(isinstance(row, dict) and isinstance(source, str) and build.SHA.fullmatch(source)
                 and row.get("sourceSha") == source and row.get("phase") in build.PHASES,
                 "registered_android_entry_invalid")
            try:
                created = datetime.fromisoformat(row["createdAt"])
            except (KeyError, TypeError, ValueError):
                raise build.Hold("build_creation_time_unverified") from None
            need(created.tzinfo is not None, "build_creation_time_unverified")
            dated.append((created, row))
        newest = max(at for at, _ in dated)
        latest = [row for at, row in dated if at == newest]
        need(len(latest) == 1, "latest_build_ambiguous")
        row = latest[0]
        need(row["phase"] == "signed_verified", "latest_build_not_verified_signed")
        need(all(type(row.get(k)) is int and row[k] > 0 for k in ("testRunId", "signingRunId")),
             "exact_test_or_signing_run_missing")
        matches = [r for r in rows if r.get("sourceCommit") == row["sourceSha"]]
        need(len(matches) <= 1, "source_publication_ambiguous")
        previous = matches[0] if matches else None
        if previous is None:
            version, code = candidate_version(env, row)
            req = signed_request(env, row["sourceSha"], version, code,
                                 log.strip() or f"更新至 {version}（{code}）。")
            need(all(req[k] == row[k] for k in ("testRunId", "signingRunId")),
                 "signed_build_changed_during_selection")
            return req
    # Use the original immutable identity and notes on continuation/repeated run.
    need(not log.strip() or log == previous.get("updateLog"),
         "existing_publication_update_log_conflict")
    req = signed_request(env, previous["sourceCommit"], previous["versionName"],
                         previous["versionCode"], previous["updateLog"])
    need(all(previous.get(k) == v for k, v in req.items()), "version_publication_identity_conflict")
    return req


def run_automatic(env, *, log="", execute=False):
    req = automatic_request(env, log)
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write("### 自动选择已签名 APK\n\n版本：" + req["versionName"] + "（" +
                      str(req["versionCode"]) + "）\n\n源码：`" + req["sourceCommit"] +
                      "`\n\nTest Run：" + str(req["testRunId"]) + "；签名 Run：" +
                      str(req["signingRunId"]) + "。\n\n" +
                      ("本次执行正式发布。" if execute else "本次仅检查，不公开 APK。") + "\n")
    return run(env, source=req["sourceCommit"], version=req["versionName"],
               code=req["versionCode"], log=req["updateLog"], mode="publish", execute=execute)


def save(env, key, row, previous):
    state, blob = build.json_file(env["GH_TOKEN"], STATE)
    need(state.get("version") == 1 and isinstance(state.get("releases"), dict), "release_registry_invalid")
    need(state["releases"].get(key) == previous, "release_registry_changed_no_retry")
    state["releases"][key] = row
    build.api(env["GH_TOKEN"], build.RUNNER, "/contents/" + STATE, method="PUT", payload={
        "branch": "main", "sha": blob, "message": "Record exact manual APK publication phase",
        "content": base64.b64encode((json.dumps(state, indent=2) + "\n").encode()).decode()})
    after, _ = build.json_file(env["GH_TOKEN"], STATE)
    need(after == state, "release_registry_confirmation_failed_no_retry")


def stage(env, req):
    args = [sys.executable, "-m", "scheduler.apk_stage", "--source-commit", req["sourceCommit"],
            "--version-name", req["versionName"], "--version-code", str(req["versionCode"]),
            "--test-run-id", str(req["testRunId"]), "--signing-run-id", str(req["signingRunId"]),
            "--approval", "STAGE-PUBLIC-APK-" + req["versionName"] + "-" + str(req["signingRunId"])]
    response = subprocess.run(args, env=dict(env), capture_output=True, text=True, timeout=900)
    need(response.returncode == 0, "signed_transport_stage_failed_review_original_release")
    try:
        receipt = json.loads(response.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise build.Hold("staging_receipt_unknown_no_repeat") from None
    need(receipt.get("status") == "public_staging_verified"
         and all(receipt.get(k) == req[k] for k in
                 ("sourceCommit", "versionName", "versionCode", "testRunId", "signingRunId"))
         and receipt.get("signingCertificateSha256") == CERT, "staging_receipt_mismatch")
    return {k: receipt[k] for k in ("sourceUrl", "artifactSha256", "sourceCommit", "versionName",
            "versionCode", "apkSha256", "signingCertificateSha256")} | {
            "updateLog": req["updateLog"], "forceUpdate": False}


def verify_public(req):
    item = public_api("/contents/android-native/version.json?ref=main")
    try:
        active = json.loads(base64.b64decode(item["content"]))
    except (ValueError, KeyError):
        raise build.Hold("public_manifest_unreadable") from None
    need(all(active.get(k) == req[k] for k in ("versionName", "versionCode", "sourceCommit",
         "apkSha256", "signingCertificateSha256", "updateLog", "forceUpdate")), "published_manifest_mismatch")
    url = f"https://github.com/{REPO}/releases/download/v{req['versionName']}/ziwuxilv-native-v{req['versionName']}.apk"
    need(active.get("downloadUrl") == url, "published_download_url_mismatch")
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=90) as response:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            need(size <= 200 * 1024 * 1024, "published_apk_oversized")
            digest.update(chunk)
    need(size >= 1024 and digest.hexdigest() == req["apkSha256"], "published_apk_digest_mismatch")
    return url


def wait_public(rid, token=""):
    deadline = time.monotonic() + 20 * 60
    while time.monotonic() < deadline:
        run = public_api("/actions/runs/" + str(rid), token)
        need(run.get("id") == rid and run.get("repository", {}).get("full_name") == REPO
             and run.get("path", "").split("@")[0] == ".github/workflows/" + WORKFLOW
             and run.get("event") == "workflow_dispatch" and run.get("head_branch") == "main"
             and run.get("run_attempt") == 1 and run.get("actor", {}).get("login") == "chaonpu",
             "publication_run_origin_mismatch")
        if run.get("status") == "completed":
            need(run.get("conclusion") == "success", "publication_failed_review_original_run")
            return
        time.sleep(20)
    raise build.Hold("publication_deadline_resume_original_run_only")


def run(env, *, source, version, code, log, mode="prepare", execute=False):
    validate_context(env)
    need(mode in ("prepare", "publish", "verify"), "invalid_publication_mode")
    req = signed_request(env, source, version, code, log)
    state, _ = build.json_file(env["GH_TOKEN"], STATE)
    key = str(code)
    row = state.get("releases", {}).get(key)
    if row:
        need(all(row.get(k) == req[k] for k in req), "version_publication_identity_conflict")
    if mode == "publish" and execute:
        need(bool(env.get("CHAONPU_ACTIONS_READ_TOKEN")), "chaonpu_actions_token_missing_use_prepare")
    if not row:
        need(mode != "verify", "no_prepared_publication_to_verify")
        need(code > public_version(), "publication_requires_newer_version")
    if not execute:
        result = {"status": "read_only", "mode": mode,
                  "registeredPhase": row.get("phase") if row else None,
                  "publicDispatchCredentialConfigured": bool(env.get("CHAONPU_ACTIONS_READ_TOKEN")),
                  "signedRunVerified": True, "published": False}
        print(json.dumps(result))
        return result
    if row is None:
        row = dict(req, phase="staging_reserved")
        save(env, key, row, None)  # A partial stage must never be blindly repeated.
        publish_request = stage(env, req)
        next_row = dict(row, phase="prepared", publishRequest=publish_request)
        save(env, key, next_row, row)
        row = next_row
    need(row["phase"] in ("prepared", "dispatch_reserved", "publishing", "published"),
         "staging_outcome_unknown_review_before_any_retry")
    publish_request = row["publishRequest"]
    Path("publication-output").mkdir(exist_ok=True)
    Path("publication-output/android-publish-request.json").write_text(
        json.dumps(publish_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if row["phase"] == "prepared" and mode == "verify":
        verify_public(publish_request)
        next_row = dict(row, phase="published")
        save(env, key, next_row, row)
        row = next_row
    if row["phase"] == "prepared" and mode == "publish":
        next_row = dict(row, phase="dispatch_reserved")
        save(env, key, next_row, row)
        row = next_row
        response = public_api("/actions/workflows/" + WORKFLOW + "/dispatches",
            env["CHAONPU_ACTIONS_READ_TOKEN"], payload={"ref": "main", "return_run_details": True,
            "inputs": {"publish_request": json.dumps(publish_request, ensure_ascii=False)}})
        rid = response.get("workflow_run_id")
        need(type(rid) is int and rid > 0, "publication_run_id_unknown_no_repeat")
        next_row = dict(row, phase="publishing", publicRunId=rid)
        save(env, key, next_row, row)
        row = next_row
    need(row["phase"] != "dispatch_reserved", "publication_dispatch_unknown_no_repeat")
    if row["phase"] == "publishing":
        wait_public(row["publicRunId"], env.get("CHAONPU_ACTIONS_READ_TOKEN", ""))
        verify_public(publish_request)
        next_row = dict(row, phase="published")
        save(env, key, next_row, row)
        row = next_row
    if row["phase"] == "published":
        verify_public(publish_request)
    published = row["phase"] == "published"
    result = {"status": row["phase"], "published": published,
              "publicRunId": row.get("publicRunId"), "versionName": version, "versionCode": code}
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write("### APK 发布结果\n\n" + ("正式发布、更新清单和下载哈希已核验。" if published else
                "已准备公开签名包，**尚未正式发布**。下载本次 `apk-publication-request`，将 JSON 完整粘贴到"
                " [发布仓库手动入口](https://github.com/chaonpu/ziwuxilv/actions/workflows/publish-android-release.yml) 的 `publish_request` 后运行。") + "\n")
    print(json.dumps(result))
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    try:
        run_automatic(os.environ, log=os.environ.get("UPDATE_LOG", ""), execute=a.execute)
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (build.Hold, GuardError)) else "publication_stopped_manual_review"
        print(json.dumps({"status": "hold", "reason": reason, "published": False, "safeToRepeat": False}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
