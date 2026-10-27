"""Public Runner: manual, fail-closed staging of an approved signed Android APK.

The private token is used only to READ run metadata and download the
windows-signed-release artifact. The Runner's own GITHUB_TOKEN is used to
publish a publicly downloadable PRERELEASE in chaonpu/Runner.

Never downloads Test source, never runs APK code, never publishes the formal
chaonpu/ziwuxilv release, and never exposes tokens in URLs or summaries.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

SOURCE_REPO = "anwpustudent-lab/Test"
SIGN_REPO = "chaonpu/ziwuxilv-android"
PUBLIC_REPO = "chaonpu/Runner"
CERT = "bef3f5cbb2989734f20740eddaac3e105dfecb6c63ea9275e178eedd892ec105"
SOURCE_WORKFLOW = ".github/workflows/test-self-hosted.yml"
SIGN_WORKFLOW = ".github/workflows/windows-signing-verified-handoff.yml"
CLOUD_SIGN_WORKFLOW = ".github/workflows/cloud-signing-verified-handoff.yml"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
COUNT = re.compile(r"(?im)^\s*Number of signers:\s*(\d+)\s*$")
CERT_LINE = re.compile(
    r"(?im)^\s*(?:Signer\s+#\d+|V\d+(?:\.\d+)?\s+Signer:)"
    r"\s+certificate\s+SHA-256\s+digest:\s*([0-9a-f]{64})\s*$"
)
MAX_APK_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_BYTES = 240 * 1024 * 1024


class GuardError(ValueError):
    pass


def api_json(path: str, token: str) -> dict:
    if (not token or not re.fullmatch(
        r"/repos/(?:chaonpu/ziwuxilv-android|anwpustudent-lab/Test)"
        r"/actions/runs/[1-9][0-9]*", path
    )):
        raise GuardError("github_api_endpoint_not_allowlisted")
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "ziwuxilv-public-approved-apk-staging/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise GuardError("github_run_provenance_unavailable") from exc
    if not isinstance(data, dict):
        raise GuardError("github_run_metadata_invalid")
    return data


def validate_run(data: dict, repo: str, workflow: str, run_id: int,
                 source_sha: str | None = None) -> None:
    if (not isinstance(data, dict) or data.get("id") != run_id
            or (data.get("repository") or {}).get("full_name") != repo
            or str(data.get("path", "")).split("@")[0] != workflow
            or data.get("status") != "completed" or data.get("conclusion") != "success"
            or data.get("event") not in (
                ("push", "workflow_dispatch") if repo == SOURCE_REPO
                else ("workflow_dispatch",)
            )
            or data.get("head_branch") != "main"):
        raise GuardError("workflow_run_not_verified")
    if source_sha is not None and data.get("head_sha") != source_sha:
        raise GuardError("test_source_commit_mismatch")


def parse_request(args, env) -> dict:
    if not str(args.signing_run_id).isdigit() or int(args.signing_run_id) <= 0:
        raise GuardError("invalid_signer_run")
    if not str(args.test_run_id).isdigit() or int(args.test_run_id) <= 0:
        raise GuardError("invalid_test_run")
    if not HEX40.fullmatch(args.source_commit):
        raise GuardError("invalid_source_commit")
    if not VERSION.fullmatch(args.version_name):
        raise GuardError("invalid_version")
    if not str(args.version_code).isdigit() or int(args.version_code) <= 0:
        raise GuardError("invalid_version_code")
    expected = "STAGE-PUBLIC-APK-" + args.version_name + "-" + str(args.signing_run_id)
    if args.approval != expected:
        raise GuardError("explicit_staging_approval_missing")
    if env.get("GITHUB_ACTOR") != "chaonpu" or env.get("GITHUB_REF") != "refs/heads/main":
        raise GuardError("untrusted_workflow_actor_or_ref")
    if not env.get("CHAONPU_ACTIONS_READ_TOKEN") or not env.get("TEST_ACTIONS_READ_TOKEN"):
        raise GuardError("private_read_credentials_missing")
    if not env.get("GH_TOKEN"):
        raise GuardError("runner_release_credential_missing")
    return {
        "signingRunId": int(args.signing_run_id),
        "testRunId": int(args.test_run_id),
        "sourceCommit": args.source_commit,
        "versionName": args.version_name,
        "versionCode": int(args.version_code),
    }


def verify_provenance(req: dict, signer_token: str, test_token: str) -> None:
    signing = api_json(
        f"/repos/{SIGN_REPO}/actions/runs/{req['signingRunId']}", signer_token
    )
    signing_origin = str(signing.get("path", "")).split("@")[0]
    if signing_origin not in (SIGN_WORKFLOW, CLOUD_SIGN_WORKFLOW):
        raise GuardError("signing_workflow_not_allowlisted")
    validate_run(signing, SIGN_REPO, signing_origin, req["signingRunId"])
    source = api_json(
        f"/repos/{SOURCE_REPO}/actions/runs/{req['testRunId']}", test_token
    )
    origin = str(source.get("path", "")).split("@")[0]
    if origin not in (SOURCE_WORKFLOW, ".github/workflows/android-cloud-verified.yml"):
        raise GuardError("test_workflow_not_allowlisted")
    validate_run(source, SOURCE_REPO, origin,
                 req["testRunId"], source_sha=req["sourceCommit"])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sidecar(path: Path, apk_name: str, actual: str) -> None:
    if (not path.is_file() or
        path.read_text(encoding="utf-8-sig").strip() != actual + "  " + apk_name):
        raise GuardError("apk_sha_sidecar_mismatch")


def assert_clean_artifact(folder: Path, expected: dict) -> tuple[Path, dict, list[str]]:
    version = expected["versionName"]
    name = f"ziwuxilv-native-v{version}.apk"
    required = {
        name, name + ".sha256", "source-commit.txt",
        "signing-cert-sha256.txt", "publish-candidate-metadata.json",
    }
    entries = list(folder.iterdir())
    present = {entry.name for entry in entries if entry.is_file() and not entry.is_symlink()}
    # New Android build tools also produce a v4 incremental-install signature.
    # It is private input only: the public transport still contains exactly
    # the original five verified files, never this optional sidecar.
    idsig = name + ".idsig"
    if (present not in (required, required | {idsig})
            or any(not entry.is_file() or entry.is_symlink() for entry in entries)):
        raise GuardError("artifact_files_not_allowlisted")
    if idsig in present and not 0 < (folder / idsig).stat().st_size <= 8 * 1024 * 1024:
        raise GuardError("incremental_signature_sidecar_size_invalid")
    apk = folder / name
    if apk.stat().st_size > MAX_APK_BYTES or apk.stat().st_size < 1024:
        raise GuardError("unexpected_apk_size")
    digest = sha256(apk)
    verify_sidecar(folder / (name + ".sha256"), name, digest)
    if (folder / "source-commit.txt").read_text(encoding="utf-8-sig").strip() != expected["sourceCommit"]:
        raise GuardError("source_sidecar_mismatch")
    if (folder / "signing-cert-sha256.txt").read_text(encoding="utf-8-sig").strip() != CERT:
        raise GuardError("certificate_sidecar_mismatch")
    try:
        meta = json.loads((folder / "publish-candidate-metadata.json").read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise GuardError("candidate_metadata_invalid") from exc
    fields = {
        "schemaVersion": 1,
        "artifactKind": "verified-private-windows-signed-apk",
        "sourceRepository": SOURCE_REPO,
        "sourceCommit": expected["sourceCommit"],
        "testRunId": expected["testRunId"],
        "signingRepository": SIGN_REPO,
        "signingRunId": expected["signingRunId"],
        "apkName": name,
        "apkSha256": digest,
        "apkPackage": "com.ziwuxilv.nativeapp",
        "versionName": version,
        "versionCode": expected["versionCode"],
        "signingCertificateSha256": CERT,
        "publicationAuthorized": False,
        "publicTransportReady": False,
    }
    if not isinstance(meta, dict) or meta != fields:
        raise GuardError("candidate_run_or_digest_mismatch")
    return apk, meta, sorted(required)


def build_tools(root=None):
    root = root or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not root:
        raise GuardError("android_sdk_unavailable")
    folders = Path(root) / "build-tools"
    if not folders.is_dir():
        raise GuardError("android_build_tools_unavailable")
    for folder in sorted(folders.iterdir(), reverse=True):
        if folder.is_dir() and (folder / "apksigner").is_file() and (folder / "aapt").is_file():
            return str(folder / "apksigner"), str(folder / "aapt")
    raise GuardError("android_apk_verification_tools_missing")


def command_output(command):
    try:
        response = subprocess.run(command, capture_output=True, text=True,
                                  timeout=75, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GuardError("android_apk_verifier_unavailable") from exc
    if response.returncode != 0:
        raise GuardError("android_apk_verification_failed")
    return response.stdout


def verify_apk_bytes(apk: Path, expected: dict, tools=None, command=command_output):
    signer, aapt = tools if tools else build_tools()
    report = command([signer, "verify", "--verbose", "--print-certs", str(apk)])
    count = COUNT.search(report)
    certs = CERT_LINE.findall(report)
    if count is None or int(count.group(1)) != 1 or not certs:
        raise GuardError("android_signer_missing_or_not_unique")
    if any(f.lower() != CERT for f in certs):
        raise GuardError("android_production_certificate_mismatch")
    badging = command([aapt, "dump", "badging", str(apk)])
    package = next((line for line in badging.splitlines() if line.startswith("package: ")), "")
    for field, value in (
        ("name", "com.ziwuxilv.nativeapp"),
        ("versionCode", str(expected["versionCode"])),
        ("versionName", expected["versionName"]),
    ):
        if not re.search(r"(?:^|\s)" + re.escape(field) + r"='" +
                         re.escape(value) + r"'(?:\s|$)", package):
            raise GuardError("apk_package_or_version_mismatch")


def public_version() -> int:
    fixed = ("https://api.github.com/repos/chaonpu/ziwuxilv/"
             "contents/android-native/version.json?ref=main")
    request = urllib.request.Request(
        fixed,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "ziwuxilv-release-candidate-staging/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as result:
            item = json.load(result)
        if (item.get("type") != "file" or item.get("encoding") != "base64"):
            raise GuardError("public_version_unverified")
        manifest = json.loads(base64.b64decode(item["content"]))
        code = manifest.get("versionCode")
        if type(code) is not int or code <= 0:
            raise GuardError("public_version_unverified")
        return code
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise GuardError("public_version_unverified") from exc


def package_public_transport(folder: Path, file_names: list[str], out: Path) -> str:
    if out.exists():
        raise GuardError("transport_already_exists")
    with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in file_names:
            if not re.fullmatch(r"[a-zA-Z0-9._-]+", name):
                raise GuardError("unsafe_transport_file_name")
            archive.write(folder / name, arcname=name)
    if out.stat().st_size > MAX_ARCHIVE_BYTES:
        raise GuardError("transport_too_large")
    with zipfile.ZipFile(out) as archive:
        if sorted(archive.namelist()) != sorted(file_names):
            raise GuardError("public_zip_contents_changed")
    return sha256(out)


def guard_pre_release(req: dict, current_code: int) -> None:
    if req["versionCode"] <= current_code:
        raise GuardError("staging_requires_version_newer_than_public_release")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--signing-run-id", required=True)
    p.add_argument("--test-run-id", required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--version-name", required=True)
    p.add_argument("--version-code", required=True)
    p.add_argument("--approval", required=True)
    args = p.parse_args()
    try:
        req = parse_request(args, os.environ)
        guard_pre_release(req, public_version())
        verify_provenance(req, os.environ["CHAONPU_ACTIONS_READ_TOKEN"],
                          os.environ["TEST_ACTIONS_READ_TOKEN"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signed = root / "signed"
            signed.mkdir()
            # GitHub CLI authenticates using the existing CHAONPU private Actions
            # token; never reuses the Runner's GITHUB_TOKEN for private access.
            private_env = dict(os.environ)
            private_env["GH_TOKEN"] = os.environ.get("CHAONPU_SOURCE_READ_TOKEN") or os.environ["CHAONPU_ACTIONS_READ_TOKEN"]
            dl = subprocess.run(
                [sys.executable, "tools/private_release_transport.py", "download",
                 "--kind", "signed", "--sha", req["sourceCommit"],
                 "--run-id", str(req["signingRunId"]), "--dir", str(signed)],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=private_env, timeout=240,
            )
            if dl.returncode != 0:
                raise GuardError("verified_private_signed_release_unavailable")
            apk, candidate, names = assert_clean_artifact(signed, req)
            verify_apk_bytes(apk, req)
            # Recheck active version before any public action.
            guard_pre_release(req, public_version())
            tag = "staged-apk-v" + req["versionName"] + "-signrun-" + str(req["signingRunId"])
            archive_name = "ziwuxilv-verified-v" + req["versionName"] + ".zip"
            archive = root / archive_name
            archive_sha = package_public_transport(signed, names, archive)
            receipt = {
                "schemaVersion": 1, "tag": tag,
                "artifactSha256": archive_sha,
                "apkSha256": candidate["apkSha256"],
                "sourceCommit": req["sourceCommit"],
                "versionCode": req["versionCode"],
                "versionName": req["versionName"],
                "signingCertificateSha256": CERT,
                "testRunId": req["testRunId"],
                "signingRunId": req["signingRunId"],
                "sourceUrl": f"https://github.com/{PUBLIC_REPO}/releases/download/{tag}/{archive_name}",
                "formalReleasePublished": False,
            }
            receipt_file = root / "staging-receipt.json"
            receipt_file.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            release_env = dict(os.environ)
            release_env["GH_TOKEN"] = os.environ["GH_TOKEN"]
            # A second signer run for the SAME app version must never create
            # another publicly visible candidate, even with a different tag.
            existing = subprocess.run(
                ["gh", "api", "repos/" + PUBLIC_REPO + "/releases?per_page=100"],
                capture_output=True, text=True, env=release_env, timeout=25,
            )
            if existing.returncode != 0:
                raise GuardError("staging_release_registry_unavailable")
            try:
                history = json.loads(existing.stdout)
                if not isinstance(history, list):
                    raise ValueError("not a release list")
            except ValueError as exc:
                raise GuardError("staging_release_registry_unavailable") from exc
            if any(str(item.get("tag_name", "")).startswith(
                    "staged-apk-v" + req["versionName"] + "-signrun-"
                ) for item in history if isinstance(item, dict)):
                raise GuardError("version_already_has_public_staging_release")
            # Double-check the exact tag, including release drafts excluded
            # from unauthenticated lists. Never replace existing assets.
            exact = subprocess.run(
                ["gh", "release", "view", tag, "--repo", PUBLIC_REPO, "--json", "assets"],
                capture_output=True, text=True, env=release_env, timeout=25,
            )
            if exact.returncode == 0:
                raise GuardError("staging_release_already_exists_manual_reconciliation_required")
            notes = ("PUBLIC PRE-RELEASE STAGING ONLY; NOT FORMAL APP RELEASE. "
                     f"Version {req['versionName']} ({req['versionCode']}); "
                     f"Test run {req['testRunId']}; signing run {req['signingRunId']}. "
                     "Assets are signed APK transport ZIP and public-safe receipt. "
                     "Do not publish to user update channel without separate approval.")
            # Only the strictly allowlisted APK package and public-safe receipt
            # are staged; no private source, keystore or GitHub Actions token.
            created = subprocess.run(
                ["gh", "release", "create", tag, str(archive), str(receipt_file),
                 "--repo", PUBLIC_REPO, "--target", "main", "--prerelease",
                 "--title", "子午汐律 APK 待发布验证包 " + req["versionName"],
                 "--notes", notes],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=release_env, timeout=90,
            )
            if created.returncode != 0:
                raise GuardError("public_staging_release_creation_failed_check_for_partial_release")
            # Re-read the public URL anonymously and compare SHA-256.
            request = urllib.request.Request(receipt["sourceUrl"], method="GET",
                                             headers={"User-Agent": "ziwuxilv-release-staging-verify"})
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    remote_hash = hashlib.sha256()
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        remote_hash.update(block)
                if remote_hash.hexdigest() != archive_sha:
                    raise GuardError("public_staged_zip_digest_mismatch")
            except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
                raise GuardError("public_staged_url_unavailable_check_release_manually") from exc
            print(json.dumps({"status": "public_staging_verified", **receipt},
                             ensure_ascii=False, sort_keys=True))
    except (GuardError, OSError, subprocess.TimeoutExpired, ValueError,
            RuntimeError, KeyError, zipfile.BadZipFile) as exc:
        code = str(exc) if isinstance(exc, GuardError) else "unexpected_staging_validation_error"
        print(json.dumps({"status": "blocked_or_requires_manual_reconciliation",
                          "reason": code, "formalReleasePublished": False}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
