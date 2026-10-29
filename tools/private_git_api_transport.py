"""Immutable verified GitHub Git-blob mirror for private Release assets.

Only used when the GitHub Release CDN cannot stream a verified asset.
Release metadata and digest remain mandatory; this is a second transport,
NOT a substitute for the trusted Test run or signing policy.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

CHUNK_SIZE = 256 * 1024
MAX_ASSET = 240 * 1024 * 1024
REPOS = {"test": "anwpustudent-lab/Test", "signed": "chaonpu/ziwuxilv-android"}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[1-9][0-9]*\Z")

class MirrorError(ValueError):
    pass

def require(ok, reason):
    if not ok:
        raise MirrorError(reason)

def checksum(data):
    return hashlib.sha256(data).hexdigest()

def git_hash(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def tag(kind, sha, run):
    require(kind in REPOS and SHA40.fullmatch(sha) and RUN_ID.fullmatch(run),
            "identity_invalid")
    return "verified-api-mirror-v1-" + kind + "-" + sha + "-run-" + run

def api(repo, path):
    require(repo in REPOS.values() and path.startswith("/git/"),
            "endpoint_not_allowlisted")
    return "https://api.github.com/repos/" + repo + path

def call(repo, path, token, *, payload=None, missing_ok=False):
    """Single authenticated GitHub API call with a hard process deadline."""
    url = api(repo, path)
    require(bool(token) and not any(c in token for c in '\r\n"\x00'),
            "token_invalid")
    curl = shutil.which("curl.exe") or shutil.which("curl")
    require(curl is not None, "curl_not_available")
    config = ('url = "' + url + '"\n'
              'header = "Authorization: Bearer ' + token + '"\n'
              'header = "Accept: application/vnd.github+json"\n'
              'header = "Content-Type: application/json"\n'
              'header = "X-GitHub-Api-Version: 2022-11-28"\n'
              'header = "User-Agent: verified-private-git-mirror-v1"\n')
    with tempfile.TemporaryDirectory(prefix="git-api-private-mirror-") as tmp:
        out = Path(tmp) / "response.json"
        argv = [curl, "--config", "-", "--silent", "--http1.1", "-4",
                "--noproxy", "*", "--proto", "=https", "--connect-timeout", "8",
                "--max-time", "48", "--output", str(out),
                "--write-out", "%{http_code}", "--request",
                "POST" if payload is not None else "GET"]
        if payload is not None:
            body = Path(tmp) / "payload.json"
            body.write_bytes(json.dumps(payload, separators=(",", ":")).encode())
            argv.extend(["--data-binary", "@" + str(body)])
        try:
            result = subprocess.run(argv, input=config.encode(), stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=55, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise MirrorError("transport_unavailable") from None
        status = result.stdout.decode("ascii", "replace").strip()
        if missing_ok and status == "404":
            return None
        if status in ("401", "403", "404", "409", "422", "429"):
            # Endpoint class is safe to log; never log private object URLs,
            # signed redirects, tokens or the actual blob body.
            endpoint = (
                "tag_read" if path.startswith("/git/ref/tags/") else
                "blob_write" if path == "/git/blobs" else
                "blob_read" if path.startswith("/git/blobs/") else
                "tree_write" if path == "/git/trees" else
                "tree_read" if path.startswith("/git/trees/") else
                "commit_write" if path == "/git/commits" else
                "commit_read" if path.startswith("/git/commits/") else
                "ref_write" if path == "/git/refs" else "other"
            )
            raise MirrorError("http_" + status + "_" + endpoint)
        require(result.returncode == 0 and status in
                (("201",) if payload is not None else ("200",)),
                "transport_unavailable")
        require(out.is_file() and out.stat().st_size <= 2 * 1024 * 1024,
                "response_size_invalid")
        try:
            obj = json.loads(out.read_bytes())
        except (ValueError, UnicodeError):
            raise MirrorError("response_invalid") from None
        require(isinstance(obj, dict), "response_invalid")
        return obj

def get_blob(repo, blob_sha, token):
    require(bool(SHA40.fullmatch(blob_sha)), "blob_sha_invalid")
    item = call(repo, "/git/blobs/" + blob_sha, token)
    require(item.get("sha") == blob_sha and item.get("encoding") == "base64",
            "blob_identity_invalid")
    try:
        data = base64.b64decode(item["content"], validate=False)
    except (KeyError, ValueError, TypeError):
        raise MirrorError("blob_encoding_invalid") from None
    require(git_hash(data) == blob_sha and item.get("size") == len(data),
            "blob_bytes_invalid")
    return data

def put_blob(repo, data, token):
    expected = git_hash(data)
    body = {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"}
    try:
        obj = call(repo, "/git/blobs", token, payload=body)
    except MirrorError as exc:
        if str(exc) != "transport_unavailable":
            raise
        # Git blobs are content-addressed. Never repeat an uncertain POST.
        if get_blob(repo, expected, token) != data:
            raise MirrorError("uncertain_blob_write_not_verified")
        return expected
    require(obj.get("sha") == expected, "blob_upload_hash_mismatch")
    return expected

def validate_asset(asset, data=None):
    require(isinstance(asset, dict) and isinstance(asset.get("id"), int)
            and asset["id"] > 0 and isinstance(asset.get("size"), int)
            and 1024 < asset["size"] <= MAX_ASSET,
            "asset_identity_invalid")
    digest = asset.get("digest")
    require(isinstance(digest, str) and digest.startswith("sha256:")
            and SHA64.fullmatch(digest[7:]), "asset_digest_required")
    if data is not None:
        require(len(data) == asset["size"] and checksum(data) == digest[7:],
                "release_digest_mismatch")
    return digest[7:]

def read(kind, sha, run, asset, token):
    """Read exact immutable Git tag, parent commit, tree and every chunk."""
    label = tag(kind, sha, run)
    expected = validate_asset(asset)
    repo = REPOS[kind]
    ref = call(repo, "/git/ref/tags/" + label, token, missing_ok=True)
    require(ref is not None, "ref_missing")
    require(ref.get("ref") == "refs/tags/" + label
            and (ref.get("object") or {}).get("type") == "commit",
            "ref_identity_invalid")
    commit_sha = (ref["object"]).get("sha")
    require(isinstance(commit_sha, str) and SHA40.fullmatch(commit_sha),
            "commit_sha_invalid")
    obj = call(repo, "/git/commits/" + commit_sha, token)
    require(obj.get("sha") == commit_sha, "commit_identity_invalid")
    parents = obj.get("parents")
    require(isinstance(parents, list) and len(parents) == 1
            and isinstance(parents[0].get("sha"), str)
            and SHA40.fullmatch(parents[0]["sha"]), "commit_parent_invalid")
    # The mirrored commit must derive from the original Release tag target.
    origin = call(repo, "/git/ref/tags/verified-" + kind + "-" + sha +
                  "-run-" + run, token)
    require(origin.get("ref") == "refs/tags/verified-" + kind + "-" + sha +
            "-run-" + run and origin.get("object", {}).get("type") == "commit"
            and parents[0]["sha"] == origin["object"]["sha"],
            "release_source_tag_mismatch")
    if kind == "test":
        require(parents[0]["sha"] == sha, "test_source_sha_mismatch")
    tree_sha = (obj.get("tree") or {}).get("sha")
    require(isinstance(tree_sha, str) and SHA40.fullmatch(tree_sha),
            "tree_sha_invalid")
    tree = call(repo, "/git/trees/" + tree_sha, token)
    entries = tree.get("tree")
    count = (asset["size"] + CHUNK_SIZE - 1) // CHUNK_SIZE
    require(tree.get("sha") == tree_sha and tree.get("truncated") is not True
            and isinstance(entries, list) and len(entries) == count
            and 0 < count <= 1024, "tree_identity_invalid")
    result = bytearray()
    for n, entry in enumerate(sorted(entries, key=lambda x: x.get("path", ""))):
        require(entry.get("path") == "bundle-%05d.bin" % n
                and entry.get("mode") == "100644"
                and entry.get("type") == "blob"
                and isinstance(entry.get("sha"), str)
                and SHA40.fullmatch(entry["sha"]),
                "tree_entry_invalid")
        part = get_blob(repo, entry["sha"], token)
        expected_length = min(CHUNK_SIZE, asset["size"] - n * CHUNK_SIZE)
        require(len(part) == expected_length, "chunk_length_mismatch")
        result.extend(part)
        if (n + 1) % 12 == 0:
            print("Private Git API mirror read:", n + 1, "/", count, flush=True)
    require(len(result) == asset["size"] and checksum(result) == expected,
            "mirror_sha256_mismatch")
    return bytes(result)

def publish(kind, sha, run, asset, data, token, target):
    """Create exact immutable mirror ref; never overwrite or repeat ambiguous writes."""
    label = tag(kind, sha, run)
    validate_asset(asset, data)
    require(SHA40.fullmatch(target) and
            (kind == "signed" or target == sha), "target_invalid")
    repo = REPOS[kind]
    existing = call(repo, "/git/ref/tags/" + label, token, missing_ok=True)
    if existing is not None:
        require(read(kind, sha, run, asset, token) == data,
                "existing_mirror_bytes_mismatch")
        return checksum(data)
    print("Private Git API mirror: original Release metadata validated; starting immutable blob transfer", flush=True)
    entries = []
    for n, begin in enumerate(range(0, len(data), CHUNK_SIZE)):
        chunk = data[begin:begin + CHUNK_SIZE]
        blob_sha = put_blob(repo, chunk, token)
        entries.append({"path": "bundle-%05d.bin" % n,
                        "mode": "100644", "type": "blob", "sha": blob_sha})
        if n == 0 or (n + 1) % 12 == 0:
            print("Private Git API mirror uploaded:", n + 1, "/", 
                  (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE, flush=True)
    tree = call(repo, "/git/trees", token, payload={"tree": entries})
    tree_sha = tree.get("sha")
    require(isinstance(tree_sha, str) and SHA40.fullmatch(tree_sha),
            "created_tree_invalid")
    # Parent is the exact original Release tag target, not mutable main.
    origin = call(repo, "/git/ref/tags/verified-" + kind + "-" + sha +
                  "-run-" + run, token)
    require(origin.get("object", {}).get("type") == "commit"
            and origin.get("object", {}).get("sha") == target,
            "original_release_tag_target_mismatch")
    commit = call(repo, "/git/commits", token, payload={
        "message": "Immutable private Git API mirror v1 for exact " + label,
        "tree": tree_sha, "parents": [target]})
    commit_sha = commit.get("sha")
    require(isinstance(commit_sha, str) and SHA40.fullmatch(commit_sha),
            "created_commit_invalid")
    try:
        ref = call(repo, "/git/refs", token, payload={
            "ref": "refs/tags/" + label, "sha": commit_sha})
    except MirrorError as exc:
        if str(exc) != "transport_unavailable":
            raise
        # Reconcile ambiguous ref POST with read-only full-byte verification.
        require(read(kind, sha, run, asset, token) == data,
                "uncertain_ref_creation_not_verified")
        return checksum(data)
    require(ref.get("ref") == "refs/tags/" + label and
            ref.get("object", {}).get("sha") == commit_sha,
            "created_ref_invalid")
    require(read(kind, sha, run, asset, token) == data,
            "post_publish_mirror_bytes_mismatch")
    return checksum(data)
