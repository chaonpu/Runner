"""One-time, explicitly constrained canary dispatch ONLY.

Never accepts arbitrary repository/workflow/inputs; never launches any business
workflow. Distinct public-safety canary workflows do not checkout private code
or read business secrets. A lost HTTP response is UNKNOWN, never auto-retried.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
TARGETS = {
    "stock": ("chaonpu/Stock", "CHAONPU_ACTIONS_READ_TOKEN"),
    "test": ("anwpustudent-lab/Test", "TEST_ACTIONS_READ_TOKEN"),
    "signer": ("chaonpu/ziwuxilv-android", "CHAONPU_ACTIONS_READ_TOKEN"),
    "publish": ("chaonpu/ziwuxilv", "CHAONPU_ACTIONS_READ_TOKEN"),
}
WORKFLOW = "runner-dispatch-canary.yml"
USER_AGENT = "ziwuxilv-coordinator-permission-canary/1.0"
ID = re.compile(r"^runner-canary-[0-9]{8,20}-(stock|test|signer|publish)$")

def payload(target: str, github_run_id: str) -> tuple[str, dict, str]:
    if target not in TARGETS:
        raise ValueError("Canary target not approved")
    if not re.fullmatch(r"[0-9]{8,20}", github_run_id):
        raise ValueError("Missing real GitHub workflow run id")
    repo, secret = TARGETS[target]
    key = f"runner-canary-{github_run_id}-{target}"
    if not ID.fullmatch(key):
        raise ValueError("Invalid canary id")
    return repo, {"ref": "main", "inputs": {"dispatch_id": key}, "return_run_details": True}, secret

def run_canary(target: str, github_run_id: str, env: dict,
               opener=urllib.request.urlopen) -> dict:
    repo, body, secret = payload(target, github_run_id)
    token = env.get(secret, "")
    if not token:
        return {"target": target, "status": "missing_secret", "dispatched": False}
    url = f"{API}/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with opener(req, timeout=25) as response:
            status = response.status
            raw = response.read(100_000)
        if status not in (200, 201, 204):
            return {"target": target, "status": "unrecognized_response", "dispatched": "unknown"}
        returned = json.loads(raw) if raw else {}
        if not isinstance(returned, dict):
            returned = {}
        run_id = returned.get("workflow_run_id")
        if not isinstance(run_id, int) or run_id <= 0:
            # Pre-2026 behavior may return 204, but API has accepted request.
            # NEVER retry blindly; reconcile via workflow runs metadata.
            return {"target": target, "status": "accepted_run_id_unavailable",
                    "dispatched": True, "canary": body["inputs"]["dispatch_id"]}
        return {"target": target, "status": "accepted", "dispatched": True,
                "runId": run_id, "canary": body["inputs"]["dispatch_id"],
                "url": f"https://github.com/{repo}/actions/runs/{run_id}"}
    except urllib.error.HTTPError as exc:
        return {"target": target, "status": "permission_denied" if exc.code in (401,403)
                else "not_found_or_out_of_scope" if exc.code == 404
                else "github_api_error", "httpStatus": exc.code, "dispatched": False}
    except (TimeoutError, urllib.error.URLError, OSError):
        return {"target": target, "status": "network_response_unknown",
                "dispatched": "unknown"}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    args = parser.parse_args()
    # One invocation per target, keyed by this *actual* coordinator run ID.
    result = run_canary(args.target, os.environ.get("GITHUB_RUN_ID", ""), os.environ)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    # Always return nonzero when not conclusively accepted; no automatic retry.
    return 0 if result.get("dispatched") is True else 2

if __name__ == "__main__":
    sys.exit(main())
