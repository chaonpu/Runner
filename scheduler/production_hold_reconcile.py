"""Reconcile an independently attested held publication without re-running business.

Runner writes *only* its public task registry, after a separate successful
Stock recovery run proves the exact original immutable database effect.
No strategy dispatch, database reads/writes, mail or original-run mutation.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import urllib.request

from scheduler.production_recovery import reconcile, SCOPES
from scheduler.production_registry import RegistryGuard, validate

BASE = "https://api.github.com/repos/"
SHA = re.compile(r"^[a-f0-9]{40}$")


def require(ok, reason):
    if not ok:
        raise RegistryGuard(reason)


def request(repo, endpoint, token, method="GET", payload=None):
    require(repo in ("chaonpu/Runner", "chaonpu/Stock"), "recovery_repository_not_allowed")
    public = ("/branches/main", "/contents/state/tasks.json?ref=",
              "/contents/config/repositories.json?ref=")
    private = ("/actions/workflows/", "/actions/runs/")
    if repo == "chaonpu/Runner":
        allowed = (method == "GET" and (endpoint == public[0] or
                   any(endpoint.startswith(x) for x in public[1:]))) or (
                   method == "PUT" and endpoint == "/contents/state/tasks.json")
    else:
        allowed = (method == "GET" and endpoint.startswith(private)
                   and ("/runs" in endpoint or "/jobs" in endpoint))
    require(allowed and bool(token), "recovery_endpoint_or_credential_invalid")
    headers = {"Authorization": "Bearer " + token,
               "Accept": "application/vnd.github+json",
               "Content-Type": "application/json",
               "User-Agent": "ziwuxilv-held-publication-reconciler"}
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + repo + endpoint, data=data,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def decode_file(item, path):
    require(item.get("type") == "file" and item.get("path") == path
            and item.get("encoding") == "base64"
            and SHA.fullmatch(str(item.get("sha", ""))), "recovery_file_metadata_invalid")
    raw = base64.b64decode(item["content"].replace("\n", ""), validate=True)
    calculated = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    require(calculated == item["sha"], "recovery_file_blob_mismatch")
    return json.loads(raw), item["sha"]


def read_public(token):
    branch = request("chaonpu/Runner", "/branches/main", token)
    head = branch.get("commit", {}).get("sha", "")
    require(bool(SHA.fullmatch(head)), "recovery_public_head_unavailable")
    config, config_sha = decode_file(request("chaonpu/Runner",
        "/contents/config/repositories.json?ref=" + head, token),
        "config/repositories.json")
    state, state_sha = decode_file(request("chaonpu/Runner",
        "/contents/state/tasks.json?ref=" + head, token), "state/tasks.json")
    validate(config, state)
    return config, config_sha, state, state_sha


def candidates(row, token):
    scope = SCOPES.get((row.get("task"), row.get("publicationMode")))
    if scope is None:
        return []
    workflow = scope[1]
    listing = request("chaonpu/Stock", "/actions/workflows/" + workflow +
                      "/runs?branch=main&per_page=30", token)
    entries = listing.get("workflow_runs", [])
    require(isinstance(entries, list) and len(entries) <= 30,
            "recovery_workflow_listing_unverified")
    # A run must later pass the exact named job, step and original binding
    # checks in reconcile(); a green workflow name by itself proves nothing.
    return [x for x in entries if x.get("status") == "completed"
            and x.get("conclusion") == "success"
            and x.get("repository", {}).get("full_name") == "chaonpu/Stock"
            and x.get("path", "").split("@")[0] ==
               ".github/workflows/" + workflow
            and x.get("head_branch") == "main"]


def reconcile_one(config, state, private_token):
    held = [r for r in state["tasks"] if r.get("status") == "hold"
            and (r.get("task"), r.get("publicationMode")) in SCOPES]
    for row in held:
        original = request("chaonpu/Stock", "/actions/runs/" +
                           str(row["runId"]), private_token)
        original_jobs = request("chaonpu/Stock", "/actions/runs/" +
                                str(row["runId"]) + "/jobs?filter=latest&per_page=100",
                                private_token)
        matches = []
        for run in candidates(row, private_token):
            jobs = request("chaonpu/Stock", "/actions/runs/" +
                           str(run["id"]) + "/jobs?filter=latest&per_page=100",
                           private_token)
            try:
                after, proof = reconcile(
                    config, state, row["id"], original, original_jobs, run, jobs,
                    expected_source=run["head_sha"],
                    at=datetime.now(timezone.utc).isoformat())
            except (RegistryGuard, ValueError, KeyError, TypeError):
                continue
            if proof.get("businessVerified") is True:
                matches.append((after, run["id"]))
        # Multiple distinct passing recovery claims require manual review.
        if len(matches) > 1:
            raise RegistryGuard("multiple_recovery_claims_require_review")
        if len(matches) == 1:
            return row["id"], matches[0]
    return None


def context(env):
    require(env.get("GITHUB_REPOSITORY") == "chaonpu/Runner"
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_RUN_ATTEMPT") == "1"
            and env.get("GITHUB_EVENT_NAME") in ("schedule", "workflow_dispatch")
            and env.get("GITHUB_WORKFLOW_REF") ==
               "chaonpu/Runner/.github/workflows/reconcile-verified-holds.yml@refs/heads/main",
            "reconciler_origin_invalid")


def main():
    env = dict(os.environ)
    context(env)
    public_token, private_token = env["GH_TOKEN"], env["PRIVATE_ACTIONS_TOKEN"]
    config, config_sha, state, state_sha = read_public(public_token)
    selection = reconcile_one(config, state, private_token)
    if selection is None:
        print(json.dumps({"status": "no_independently_attested_hold",
                          "registryWrites": 0, "businessDispatches": 0}))
        return
    task_id, (after, recovery_id) = selection
    config_now, config_sha_now, state_now, sha_now = read_public(public_token)
    require(config_now == config and config_sha_now == config_sha
            and sha_now == state_sha and state_now == state,
            "registry_changed_during_reconciliation")
    payload = {"branch": "main", "sha": sha_now,
               "message": "Reconcile independently attested original held publication",
               "content": base64.b64encode(
                   (json.dumps(after, indent=2) + "\n").encode()).decode()}
    # Exactly one conditional PUT; never write a receipt or other repository.
    request("chaonpu/Runner", "/contents/state/tasks.json",
            public_token, method="PUT", payload=payload)
    _, _, confirmed, _ = read_public(public_token)
    require(confirmed == after, "recovery_registry_write_unconfirmed_no_retry")
    print(json.dumps({"status": "verified_recovered_original_commit",
                      "taskId": task_id, "recoveryRunId": recovery_id,
                      "registryWrites": 1, "businessDispatches": 0}))


if __name__ == "__main__":
    main()
