"""Private, immutable-by-policy GitHub Release Assets transport for Android handoffs.

A release is transport only. The signing policy independently verifies the exact
Test run, source archive, unsigned APK, tests/lint and SHA sidecars. The
publication policy independently verifies the signed APK and certificate.
Never replace a release or asset, use an unrelated repository, or print tokens.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import subprocess
import time
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

REPOS = {"test": "anwpustudent-lab/Test", "signed": "chaonpu/ziwuxilv-android"}
ASSETS = {"test": "verified-test-bundle.zip", "signed": "verified-signed-bundle.zip"}
TEST_FILES = {
    "release-source.zip", "release-source.zip.sha256", "unsigned.apk",
    "unsigned.apk.sha256", "source.json", "verification.json",
}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"ziwuxilv-native-v[0-9]+\.[0-9]+\.[0-9]+\.apk\Z")
MAX_BYTES = 240 * 1024 * 1024

class TransportError(ValueError):
    pass

def check(ok, reason):
    if not ok:
        raise TransportError(reason)

def identity(kind, sha, run_id):
    check(kind in REPOS and isinstance(sha, str) and SHA40.fullmatch(sha)
          and isinstance(run_id, str) and re.fullmatch(r"[1-9][0-9]*", run_id),
          "release_transport_identity_invalid")
    return "verified-" + kind + "-" + sha + "-run-" + run_id

def entries(kind, folder):
    check(folder.is_dir(), "handoff_folder_missing")
    files = sorted(p.name for p in folder.iterdir())
    check(all(p.is_file() and not p.is_symlink() for p in folder.iterdir()),
          "handoff_non_regular_entry")
    if kind == "test":
        check(set(files) == TEST_FILES, "test_handoff_entries_invalid")
        check(all((folder / n).stat().st_size > 0 for n in TEST_FILES),
              "test_handoff_file_empty")
    else:
        apks = [x for x in files if NAME.fullmatch(x)]
        check(len(apks) == 1, "signed_apk_name_invalid")
        expected = {apks[0], apks[0] + ".sha256", "source-commit.txt",
                    "signing-cert-sha256.txt", "publish-candidate-metadata.json"}
        check(set(files) in (expected, expected | {apks[0] + ".idsig"}),
              "signed_handoff_entries_invalid")
        check(all((folder / n).stat().st_size > 0 for n in files),
              "signed_handoff_file_empty")
    check(sum((folder / n).stat().st_size for n in files) < MAX_BYTES,
          "handoff_too_large")
    return files

def sha256(raw):
    return hashlib.sha256(raw).hexdigest()

def build(kind, folder):
    names = entries(kind, folder)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.write(folder / name, name)
    raw = buffer.getvalue()
    check(1024 < len(raw) <= MAX_BYTES, "release_bundle_size_invalid")
    return raw

def unpack(raw, kind, folder):
    check(not folder.exists() or (folder.is_dir() and not any(folder.iterdir())),
          "handoff_destination_not_empty")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        check(len(names) == len(set(names)) and all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", n) and not n.startswith(".")
            and ((i.external_attr >> 16) & 0o170000) != 0o120000
            and i.file_size <= MAX_BYTES
            for i, n in ((i, i.filename) for i in z.infolist())),
            "release_archive_unsafe")
        check(sum(i.file_size for i in z.infolist()) <= MAX_BYTES,
              "release_archive_uncompressed_too_large")
        # Validate names and bytes before writing any output.
        check(set(names) == (TEST_FILES if kind == "test" else set(names)),
              "release_archive_entries_invalid")
        if kind == "signed":
            check(len([n for n in names if NAME.fullmatch(n)]) == 1,
                  "release_archive_signed_name_invalid")
            apk = next(n for n in names if NAME.fullmatch(n))
            required = {apk, apk + ".sha256", "source-commit.txt",
                        "signing-cert-sha256.txt", "publish-candidate-metadata.json"}
            check(set(names) in (required, required | {apk + ".idsig"}),
                  "release_archive_signed_files_invalid")
        files = {n: z.read(n) for n in names}
        check(all(files.values()), "release_archive_empty_file")
    folder.mkdir(parents=True, exist_ok=True)
    for name, raw_file in files.items():
        (folder / name).write_bytes(raw_file)
    entries(kind, folder)

def gh(url, token, *, method="GET", data=None, content_type="application/json", accept="application/vnd.github+json"):
    check(token, "release_token_missing")
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": content_type, "User-Agent": "ziwuxilv-verified-private-release-transport",
        "Cache-Control": "no-cache",
    })
    # Only safe GET reads can be repeated; POST creates immutable releases and
    # must never be blindly replayed after an ambiguous connection failure.
    attempts = 2 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=45 if method == "GET" else 150) as response:
                result = response.read(MAX_BYTES + 1)
                check(len(result) <= MAX_BYTES, "release_response_too_large")
                return result
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise TransportError("private_release_not_found") from None
            # Auth/rate-limit errors must not be misclassified as transients.
            raise TransportError("github_release_http_" + str(exc.code)) from None
        except (OSError, TimeoutError):
            if attempt + 1 < attempts:
                time.sleep(2)
    # github-cli is a separately implemented TLS/HTTP stack already used to
    # verify the Test run. Only the *same* allowlisted GitHub API read is tried.
    if method == "GET" and url.startswith("https://api.github.com/repos/"):
        endpoint = url.removeprefix("https://api.github.com/")
        parts = endpoint.split("/")
        if (len(parts) >= 5 and "/".join(parts[1:3]) in REPOS.values()
                and parts[3] in ("releases", "git")):
            env = dict(os.environ, GH_TOKEN=token)
            env.pop("GITHUB_TOKEN", None)
            try:
                result = subprocess.run(
                    ["gh", "api", "-X", "GET", "-H", "Accept: " + accept, endpoint],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=85, check=False,
                )
                if result.returncode == 0 and 0 < len(result.stdout) <= MAX_BYTES:
                    return result.stdout
            except (OSError, subprocess.TimeoutExpired):
                pass
    raise TransportError("github_release_transport_unavailable")

def gh_json(url, token, *, method="GET", body=None):
    raw = gh(url, token, method=method,
             data=json.dumps(body, separators=(",", ":")).encode() if body is not None else None)
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeError):
        raise TransportError("release_response_invalid") from None
    check(isinstance(obj, dict), "release_response_invalid")
    return obj

def release_url(kind, tag):
    return "https://api.github.com/repos/" + REPOS[kind] + "/releases/tags/" + tag

def verify_metadata(kind, sha, run_id, token):
    tag = identity(kind, sha, run_id)
    repo = REPOS[kind]
    item = gh_json(release_url(kind, tag), token)
    check(item.get("tag_name") == tag and item.get("prerelease") is True
          and item.get("draft") is False,
          "release_provenance_invalid")
    assets = item.get("assets")
    check(isinstance(assets, list) and len(assets) == 1,
          "release_asset_count_invalid")
    asset = assets[0]
    check(isinstance(asset, dict) and asset.get("name") == ASSETS[kind]
          and isinstance(asset.get("id"), int) and asset["id"] > 0
          and isinstance(asset.get("size"), int) and 1024 < asset["size"] <= MAX_BYTES
          and asset.get("state") == "uploaded",
          "release_asset_identity_invalid")
    digest = asset.get("digest")
    check(digest is None or (isinstance(digest, str)
          and digest.startswith("sha256:") and SHA64.fullmatch(digest[7:])),
          "release_asset_digest_invalid")
    if kind == "test":
        ref = gh_json("https://api.github.com/repos/" + repo + "/git/ref/tags/" + tag, token)
        check(ref.get("ref") == "refs/tags/" + tag
              and (ref.get("object") or {}).get("type") == "commit"
              and (ref.get("object") or {}).get("sha") == sha,
              "release_tag_source_sha_mismatch")
    return asset

def download(kind, sha, run_id, folder, token):
    asset = verify_metadata(kind, sha, run_id, token)
    try:
        raw = gh("https://api.github.com/repos/" + REPOS[kind] +
                 "/releases/assets/" + str(asset["id"]), token,
                 accept="application/octet-stream")
    except TransportError as exc:
        if str(exc) != "github_release_transport_unavailable":
            raise
        # CLI's release download has its own redirect handling, useful when a
        # self-hosted Windows runner cannot stream a GitHub asset with urllib.
        # It never selects a latest release or another repository/asset name.
        with tempfile.TemporaryDirectory(prefix="verified-release-cli-") as temp:
            env = dict(os.environ, GH_TOKEN=token)
            env.pop("GITHUB_TOKEN", None)
            try:
                result = subprocess.run(
                    ["gh", "release", "download", identity(kind, sha, run_id),
                     "--repo", REPOS[kind], "--pattern", ASSETS[kind],
                     "--dir", temp],
                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=120, check=False,
                )
                target = Path(temp) / ASSETS[kind]
                check(result.returncode == 0 and target.is_file()
                      and not target.is_symlink()
                      and target.stat().st_size == asset["size"],
                      "private_release_cli_download_unverified")
                raw = target.read_bytes()
            except (OSError, subprocess.TimeoutExpired):
                raise TransportError("private_release_cli_transport_unavailable") from None
    check(len(raw) == asset["size"], "release_asset_length_mismatch")
    if asset.get("digest") is not None:
        check(sha256(raw) == asset["digest"][7:], "release_asset_sha256_mismatch")
    unpack(raw, kind, folder)
    return sha256(raw)

def publish(kind, sha, run_id, folder, token, target):
    tag = identity(kind, sha, run_id)
    check(os.environ.get("GITHUB_REPOSITORY") == REPOS[kind]
          and os.environ.get("GITHUB_RUN_ID") == run_id
          and os.environ.get("GITHUB_SHA") == target
          and SHA40.fullmatch(target)
          and (kind == "signed" or target == sha),
          "release_publish_context_invalid")
    data = build(kind, folder)
    repo = REPOS[kind]
    body = {"tag_name": tag, "target_commitish": target,
            "name": "PRIVATE VERIFIED " + kind + " handoff " + run_id,
            "body": "Private CI handoff for exact run and source SHA. Not a public app release.",
            "draft": False, "prerelease": True, "make_latest": "false"}
    # One create and one asset upload: failures require inspection; never overwrite.
    result = gh_json("https://api.github.com/repos/" + repo + "/releases",
                     token, method="POST", body=body)
    check(result.get("tag_name") == tag and result.get("prerelease") is True
          and result.get("draft") is False and isinstance(result.get("id"), int),
          "private_release_creation_unverified")
    asset_url = ("https://uploads.github.com/repos/" + repo + "/releases/" +
                 str(result["id"]) + "/assets?name=" + ASSETS[kind])
    uploaded = gh(asset_url, token, method="POST", data=data,
                  content_type="application/zip")
    try:
        descriptor = json.loads(uploaded)
        check(descriptor.get("name") == ASSETS[kind] and
              descriptor.get("size") == len(data), "release_upload_unverified")
    except (ValueError, UnicodeError, AttributeError):
        raise TransportError("release_upload_unverified") from None
    stored = verify_metadata(kind, sha, run_id, token)
    check(stored["size"] == len(data), "release_readback_size_mismatch")
    if stored.get("digest"):
        check(stored["digest"] == "sha256:" + sha256(data),
              "release_readback_sha256_mismatch")
    # All-bytes readback; fail closed if upload response was premature.
    remote = gh("https://api.github.com/repos/" + repo + "/releases/assets/" +
                str(stored["id"]), token, accept="application/octet-stream")
    check(remote == data, "release_readback_bytes_mismatch")
    return sha256(data)

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("publish", "download", "inspect", "self-test"))
    parser.add_argument("--kind", choices=tuple(REPOS))
    parser.add_argument("--sha")
    parser.add_argument("--run-id")
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--target-sha")
    args = parser.parse_args(argv)
    if args.action == "self-test":
        assert identity("test", "a" * 40, "25") == "verified-test-" + "a" * 40 + "-run-25"
        try:
            identity("test", "../", "5")
            raise AssertionError("unsafe SHA accepted")
        except TransportError:
            pass
        with tempfile.TemporaryDirectory(prefix="release-transport-selftest-") as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            for name in TEST_FILES:
                (source / name).write_bytes(os.urandom(2048))
            archive = build("test", source)
            extracted = root / "downloaded"
            unpack(archive, "test", extracted)
            assert sorted(p.name for p in extracted.iterdir()) == sorted(TEST_FILES)
            assert all((source / n).read_bytes() == (extracted / n).read_bytes()
                       for n in TEST_FILES)
            bad = io.BytesIO()
            with zipfile.ZipFile(bad, "w") as z:
                z.writestr("../unsafe", b"x")
            try:
                unpack(bad.getvalue(), "test", root / "must-not-extract")
                raise AssertionError("unsafe archive accepted")
            except TransportError:
                pass
        print("Private Release archive roundtrip and unsafe-path tests passed.")
        return 0
    check(args.kind and args.sha and args.run_id, "release_parameters_required")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if args.action == "publish":
        check(args.dir is not None and args.target_sha is not None,
              "release_publish_arguments_missing")
        digest = publish(args.kind, args.sha, args.run_id, args.dir, token, args.target_sha)
    elif args.action == "download":
        check(args.dir is not None, "release_download_folder_missing")
        digest = download(args.kind, args.sha, args.run_id, args.dir, token)
    else:
        asset = verify_metadata(args.kind, args.sha, args.run_id, token)
        digest = asset.get("digest", "sha256:unavailable")
    print(json.dumps({"kind": args.kind, "runId": args.run_id, "sha": args.sha,
                      "transport": "private-release", "digest": digest},
                     sort_keys=True))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (TransportError, ValueError, OSError, zipfile.BadZipFile) as exc:
        print("::error::Private Release transport blocked: " +
              (str(exc) if isinstance(exc, TransportError) else "invalid_data"))
        sys.exit(2)
