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
import urllib.error

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
    need(path in ("/branches/main", "/contents/android-native/version.json?ref=main",
                  "/actions/workflows/" + WORKFLOW,
                  "/actions/workflows/" + WORKFLOW + "/dispatches")
         or re.fullmatch(r"/contents/android-native/version\.json\?ref=[0-9a-f]{40}", path)
         or re.fullmatch(r"/actions/runs/[1-9][0-9]*", path), "public_endpoint_forbidden")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ziwuxilv-manual-publish",
               "X-GitHub-Api-Version": "2026-03-10", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is None:
        headers["Cache-Control"] = "no-cache"
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
        private_env = dict(env, GH_TOKEN=env.get("CHAONPU_SOURCE_READ_TOKEN") or env["CHAONPU_ACTIONS_READ_TOKEN"])
        result = subprocess.run([sys.executable, "tools/private_release_transport.py",
            "download", "--kind", "signed", "--sha", row["sourceSha"],
            "--run-id", str(row["signingRunId"]), "--dir", str(root)],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=private_env, timeout=240)
        need(result.returncode == 0, "verified_private_signed_release_unavailable")
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


def source_release_notes(env, source, version, code):
    """Read only release metadata at the exact signed source commit."""
    need(isinstance(source, str) and bool(build.SHA.fullmatch(source)),
         "release_notes_source_invalid")
    token = env.get("TEST_SOURCE_READ_TOKEN") or env.get("TEST_ACTIONS_READ_TOKEN")
    need(bool(token), "release_notes_read_credential_missing")
    item = build.api(token, build.TEST,
                     "/contents/release/version.json?ref=" + source)
    need(item.get("type") == "file" and item.get("path") == "release/version.json"
         and item.get("encoding") == "base64", "release_notes_document_invalid")
    try:
        raw = base64.b64decode(item["content"].replace("\n", "").replace("\r", ""), validate=True)
        need(len(raw) <= 32768, "release_notes_document_too_large")
        digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        need(item.get("sha") == digest, "release_notes_blob_mismatch")
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (KeyError, ValueError, TypeError, AttributeError, UnicodeError):
        raise build.Hold("release_notes_document_invalid") from None
    need(isinstance(manifest, dict) and manifest.get("versionName") == version
         and type(manifest.get("versionCode")) is int and manifest["versionCode"] == code,
         "release_notes_version_mismatch")
    notes = manifest.get("updateLog")
    need(isinstance(notes, str) and 1 <= len(notes.strip()) <= 6000,
         "release_notes_missing_or_invalid")
    need(re.fullmatch(r"更新至\s*" + re.escape(version) + r"\s*（" + str(code) + r"）[。.]?",
                      notes.strip()) is None, "release_notes_require_specific_changes")
    return notes.strip()


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
            notes = log.strip() or source_release_notes(env, row["sourceSha"], version, code)
            req = signed_request(env, row["sourceSha"], version, code, notes)
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


def run_automatic(env, *, log="", execute=False, release_mode="formal"):
    need(release_mode in ("preview", "formal"), "invalid_release_mode")
    req = automatic_request(env, log)
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write("### 自动选择已签名 APK\n\n版本：" + req["versionName"] + "（" +
                      str(req["versionCode"]) + "）\n\n源码：`" + req["sourceCommit"] +
                      "`\n\nTest Run：" + str(req["testRunId"]) + "；签名 Run：" +
                      str(req["signingRunId"]) + "。\n\n" +
                      ("本次仅检查，不公开 APK。" if not execute else
                       "预览：仅在 Runner 保存签名验证包，不推送正式仓库。" if release_mode == "preview" else
                       "正式：核验 Runner 预览包并推送至 ziwuxilv。") +
                      "\n\n更新说明：\n\n" + req["updateLog"] + "\n")
    return run(env, source=req["sourceCommit"], version=req["versionName"],
               code=req["versionCode"], log=req["updateLog"],
               mode="prepare" if release_mode == "preview" else "publish", execute=execute)


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


def read_public_manifest(token=""):
    """Read one immutable, hash-checked manifest from an observed main commit."""
    branch = public_api("/branches/main", token)
    head = branch.get("commit", {}).get("sha")
    need(isinstance(head, str) and build.SHA.fullmatch(head), "public_head_unverified")
    item = public_api("/contents/android-native/version.json?ref=" + head, token)
    try:
        need(item.get("type") == "file" and item.get("encoding") == "base64"
             and item.get("path") == "android-native/version.json", "public_manifest_unreadable")
        raw = base64.b64decode(item["content"].replace("\n", "").replace("\r", ""), validate=True)
        digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        need(item.get("sha") == digest, "public_manifest_blob_mismatch")
        active = json.loads(raw)
        need(isinstance(active, dict), "public_manifest_unreadable")
        return active
    except (ValueError, KeyError, TypeError, AttributeError):
        raise build.Hold("public_manifest_unreadable") from None


def verify_public(req, token=""):
    fields = ("versionName", "versionCode", "sourceCommit", "apkSha256",
              "signingCertificateSha256", "updateLog", "forceUpdate")
    for attempt in range(7):
        active = read_public_manifest(token)
        mismatch = [key for key in fields if active.get(key) != req[key]]
        if not mismatch:
            break
        # Only read again when a previous version is still visible after the
        # successful original publish run. Never retry staging or dispatch.
        observed = active.get("versionCode")
        need(type(observed) is int and observed < req["versionCode"],
             "published_manifest_mismatch:" + ",".join(mismatch))
        need(attempt < 6, "published_manifest_propagation_timeout")
        print(json.dumps({"phase": "public_manifest_waiting_for_version",
                          "expectedVersionCode": req["versionCode"],
                          "observedVersionCode": observed}), flush=True)
        time.sleep(5)
    url = f"https://github.com/{REPO}/releases/download/v{req['versionName']}/ziwuxilv-native-v{req['versionName']}.apk"
    need(active.get("downloadUrl") == url, "published_download_url_mismatch")
    digest, size = hashlib.sha256(), 0
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=90) as response:
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


def staging_release_api(env, path, *, method="GET"):
    """Only the coordinator's own generated staging releases can be removed."""
    need(method in ("GET", "DELETE") and (
        re.fullmatch(r"/releases/[1-9][0-9]*", path) or
        (method == "GET" and re.fullmatch(
            r"/releases/tags/staged-apk-v[0-9]+\.[0-9]+\.[0-9]+-signrun-[1-9][0-9]*", path))),
        "staging_cleanup_endpoint_forbidden")
    need(bool(env.get("GH_TOKEN")), "staging_cleanup_token_missing")
    request = urllib.request.Request("https://api.github.com/repos/" + build.RUNNER + path,
        method=method, headers={"Authorization": "Bearer " + env["GH_TOKEN"],
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "ziwuxilv-published-staging-cleanup", "Cache-Control": "no-cache"})
    try:
        with urllib.request.build_opener(build.NoRedirect()).open(request, timeout=25) as response:
            if method == "DELETE":
                need(response.status == 204, "staging_cleanup_delete_unconfirmed")
                return None
            raw = response.read(2_000_001)
        need(len(raw) <= 2_000_000, "staging_cleanup_response_too_large")
        value = json.loads(raw)
        need(isinstance(value, dict), "staging_cleanup_response_invalid")
        return value
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise build.Hold("staging_cleanup_api_unavailable") from None
    except build.Hold:
        raise
    except (OSError, ValueError):
        raise build.Hold("staging_cleanup_api_unavailable") from None


def verify_staged_preview(env, req, publish_request):
    """Read back an existing Runner preview; registry state alone is not proof."""
    tag = "staged-apk-v" + req["versionName"] + "-signrun-" + str(req["signingRunId"])
    archive = "ziwuxilv-verified-v" + req["versionName"] + ".zip"
    url = "https://github.com/" + build.RUNNER + "/releases/download/" + tag + "/" + archive
    need(publish_request.get("sourceUrl") == url
         and isinstance(publish_request.get("artifactSha256"), str)
         and bool(apk_stage.HEX64.fullmatch(publish_request["artifactSha256"])),
         "preview_source_identity_unverified")
    release = staging_release_api(env, "/releases/tags/" + tag)
    need(isinstance(release, dict) and release.get("tag_name") == tag
         and release.get("prerelease") is True and release.get("draft") is False,
         "runner_preview_release_missing_or_changed")
    assets = release.get("assets")
    need(isinstance(assets, list) and len(assets) == 2
         and {a.get("name") for a in assets if isinstance(a, dict)} ==
             {archive, "staging-receipt.json"}
         and any(a.get("name") == archive and a.get("browser_download_url") == url
                 for a in assets if isinstance(a, dict)), "runner_preview_assets_changed")
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=90) as response:
            while block := response.read(1024 * 1024):
                size += len(block)
                need(size <= apk_stage.MAX_ARCHIVE_BYTES, "runner_preview_zip_oversized")
                digest.update(block)
    except (OSError, ValueError):
        raise build.Hold("runner_preview_download_unavailable") from None
    need(size >= 1024 and digest.hexdigest() == publish_request["artifactSha256"],
         "runner_preview_zip_hash_mismatch")


def cleanup_published_staging(env, row):
    # Caller has just verified the formal manifest AND downloaded APK hash.
    need(row.get("phase") == "published", "staging_cleanup_requires_published")
    req = row["publishRequest"]
    version, signing = row["versionName"], row["signingRunId"]
    need(bool(VERSION.fullmatch(version)) and type(signing) is int and signing > 0,
         "staging_cleanup_identity_invalid")
    tag = "staged-apk-v" + version + "-signrun-" + str(signing)
    archive = "ziwuxilv-verified-v" + version + ".zip"
    url = "https://github.com/" + build.RUNNER + "/releases/download/" + tag + "/" + archive
    need(req.get("sourceUrl") == url, "staging_cleanup_source_url_mismatch")
    release = staging_release_api(env, "/releases/tags/" + tag)
    if release is None:
        return {"status": "already_absent", "tag": tag}
    rid, assets = release.get("id"), release.get("assets")
    need(type(rid) is int and rid > 0 and release.get("tag_name") == tag
         and release.get("prerelease") is True and release.get("draft") is False
         and isinstance(assets, list) and len(assets) == 2
         and all(isinstance(a, dict) for a in assets)
         and {a.get("name") for a in assets} == {archive, "staging-receipt.json"}
         and any(a.get("name") == archive and a.get("browser_download_url") == url for a in assets),
         "staging_cleanup_release_identity_mismatch")
    staging_release_api(env, "/releases/" + str(rid), method="DELETE")
    need(staging_release_api(env, "/releases/" + str(rid)) is None,
         "staging_cleanup_delete_unconfirmed")
    return {"status": "deleted", "tag": tag, "releaseId": rid}


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
    if mode == "prepare" and execute:
        # Preview never operates on an already dispatched/published release.
        need(row is None or row.get("phase") == "prepared",
             "preview_requires_unpublished_prepared_version")
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
    staged_now = False
    if row is None:
        row = dict(req, phase="staging_reserved")
        save(env, key, row, None)  # A partial stage must never be blindly repeated.
        publish_request = stage(env, req)
        next_row = dict(row, phase="prepared", publishRequest=publish_request)
        save(env, key, next_row, row)
        row = next_row
        staged_now = True
    need(row["phase"] in ("prepared", "dispatch_reserved", "publishing", "published"),
         "staging_outcome_unknown_review_before_any_retry")
    publish_request = row["publishRequest"]
    publication_verified = False
    Path("publication-output").mkdir(exist_ok=True)
    Path("publication-output/android-publish-request.json").write_text(
        json.dumps(publish_request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mode == "prepare":
        # A real Runner Release now holds the signed APK transport ZIP.
        # Do not call the formal-release API, dispatch, or cleanup in preview.
        need(row["phase"] == "prepared", "preview_unverified_staging")
        if not staged_now:
            verify_staged_preview(env, req, publish_request)
        need(isinstance(publish_request.get("sourceUrl"), str)
             and publish_request["sourceUrl"].startswith(
                 "https://github.com/" + build.RUNNER + "/releases/download/staged-apk-v"),
             "preview_staging_url_unverified")
        result = {"status": "prepared", "previewStored": True, "stagedNow": staged_now,
                  "published": False, "stagingUrl": publish_request["sourceUrl"],
                  "versionName": version, "versionCode": code}
        if env.get("GITHUB_STEP_SUMMARY"):
            with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
                out.write("### APK 预览结果\n\n签名 APK 的核验传输包仅保存在 "
                          "[Runner 预发布 Release](" + publish_request["sourceUrl"] +
                          ")（ZIP 内含 APK）；**未向 ziwuxilv 派发正式发布**。\n\n" +
                          ("本次已新建 Runner 预览包。\n" if staged_now else
                           "复用已有的 Runner 预览包，没有重复暂存。\n"))
        print(json.dumps(result, ensure_ascii=False))
        return result
    if row["phase"] == "prepared" and mode == "verify":
        verify_public(publish_request, env.get("CHAONPU_ACTIONS_READ_TOKEN", ""))
        publication_verified = True
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
        verify_public(publish_request, env.get("CHAONPU_ACTIONS_READ_TOKEN", ""))
        publication_verified = True
        next_row = dict(row, phase="published")
        save(env, key, next_row, row)
        row = next_row
    if row["phase"] == "published" and not publication_verified:
        verify_public(publish_request, env.get("CHAONPU_ACTIONS_READ_TOKEN", ""))
    published = row["phase"] == "published"
    cleanup = None
    if published:
        try:
            cleanup = cleanup_published_staging(env, row)
            if row.get("stagingCleanup") != cleanup:
                next_row = dict(row, stagingCleanup=cleanup)
                save(env, key, next_row, row)
                row = next_row
        except Exception as exc:
            # A cleanup problem must never erase a verified publication or cause
            # it to be staged/dispatched again. A later manual run can retry.
            cleanup = {"status": "pending", "reason": str(exc) if isinstance(exc, build.Hold)
                       else "staging_cleanup_unavailable"}
            print("::warning::正式发布已核验，但 Runner 中转 Release 清理待重试。", flush=True)
    result = {"status": row["phase"], "published": published,
              "publicRunId": row.get("publicRunId"), "versionName": version, "versionCode": code}
    if cleanup is not None:
        result["stagingCleanup"] = cleanup
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write("### APK 发布结果\n\n" + ("正式发布、更新清单和下载哈希已核验。" if published else
                "已准备公开签名包，**尚未正式发布**。下载本次 `apk-publication-request`，将 JSON 完整粘贴到"
                " [发布仓库手动入口](https://github.com/chaonpu/ziwuxilv/actions/workflows/publish-android-release.yml) 的 `publish_request` 后运行。") + "\n")
            if cleanup is not None:
                out.write("\n" + ("Runner 本次中转 Release 已清除。" if cleanup["status"] != "pending"
                                  else "Runner 本次中转 Release 清理待重试；正式发布已完成。") + "\n")
    print(json.dumps(result))
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--release-mode", choices=("preview", "formal"), required=True)
    a = p.parse_args()
    try:
        run_automatic(os.environ, log=os.environ.get("UPDATE_LOG", ""),
                      release_mode=a.release_mode, execute=True)
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (build.Hold, GuardError)) else "publication_stopped_manual_review"
        print(json.dumps({"status": "hold", "reason": reason, "published": False, "safeToRepeat": False}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
