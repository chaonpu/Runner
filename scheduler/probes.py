"""Cloud-side read-only Actions/Billing/Runner preflight.

The publicly visible coordinator never downloads repository contents and never
makes API POST/PATCH/DELETE requests. All error messages are sanitized.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

API_ROOT = "https://api.github.com"
VERSION = "2026-03-10"

def safe_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "permission_denied"
        if exc.code == 404:
            return "not_found"
        if exc.code == 429:
            return "rate_limited"
        if 500 <= exc.code <= 599:
            return "github_unavailable"
        return "http_error"
    if isinstance(exc, (TimeoutError, urllib.error.URLError, OSError)):
        return "network_unavailable"
    return "invalid_response"

class GitHubReadOnly:
    def __init__(self, token: str):
        self.token = token

    def get(self, path: str) -> dict[str, Any]:
        if not self.token:
            raise ValueError("missing read-only credential")
        if (not path.startswith("/repos/") and
            not path.startswith("/users/") and
            not path.startswith("/organizations/")):
            raise ValueError("outside approved GitHub API families")
        if "//" in path or ".." in path or " " in path:
            raise ValueError("unsafe path")
        # Explicitly forbid contents, code, artifacts or mutation endpoints.
        if any(x in path for x in ("/contents/", "/git/", "/zipball", "/tarball",
                                   "/artifacts", "/dispatches", "/rerun", "/cancel")):
            raise ValueError("endpoint disallowed in public coordinator")
        req = urllib.request.Request(
            API_ROOT + path,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": VERSION,
                "User-Agent": "ziwuxilv-public-runner-coordinator/1.0",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            result = json.load(resp)
        if not isinstance(result, dict):
            raise ValueError("invalid GitHub result type")
        return result

def account_usage(account: str, spec: dict, env: dict) -> dict:
    token = env.get(spec["billingSecret"], "")
    result = {"owner": account, "accountType": spec["kind"],
              "read": "secret_missing" if not token else "unknown",
              "status": "unknown", "independentlyVerified": False}
    if not token:
        return result
    try:
        if spec["kind"] == "user":
            path = f"/users/{account}/settings/billing/usage/summary?product=Actions"
        elif spec["kind"] == "organization":
            path = f"/organizations/{account}/settings/billing/usage/summary?product=Actions"
        else:
            raise ValueError("unsupported account kind")
        data = GitHubReadOnly(token).get(path)
        items = data.get("usageItems")
        if not isinstance(items, list):
            raise ValueError("missing usageItems")
        amounts = {}
        for item in items:
            if not isinstance(item, dict) or str(item.get("product", "")).lower() != "actions":
                continue
            sku, unit = str(item.get("sku", "")), str(item.get("unitType", ""))
            if unit.lower() != "minutes" or not sku.lower().startswith("actions_"):
                continue
            value = item.get("grossQuantity")
            if type(value) in (int, float) and value >= 0:
                amounts[sku[:48]] = round(amounts.get(sku[:48], 0) + value, 3)
        result.update(read="readable", minuteUsageBySku=amounts,
                      note="Usage is not free allowance, remaining balance, or dispatch eligibility")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError,
            ValueError, KeyError, TypeError) as exc:
        result["read"] = safe_error(exc)
    return result

def runner_for_repo(repo: str, expected_name: str | None, spec: dict, env: dict) -> dict:
    status = {"name": expected_name, "status": "unknown", "busy": None,
              "labelsVerified": False, "read": "not_applicable"}
    if not expected_name:
        return status
    token = env.get(spec["runnerSecret"], "")
    if not token:
        status["read"] = "secret_missing"
        return status
    try:
        data = GitHubReadOnly(token).get(f"/repos/{repo}/actions/runners?per_page=100")
        runners = data.get("runners")
        if not isinstance(runners, list):
            raise ValueError("runners list missing")
        for runner in runners:
            if isinstance(runner, dict) and runner.get("name") == expected_name:
                labels = {item.get("name") for item in runner.get("labels", [])
                          if isinstance(item, dict)}
                required = {"self-hosted", "Windows", "X64"}
                if expected_name == "ziwuxilv-sign-win":
                    required.add("ziwuxilv-signing")
                status.update(read="readable", status=runner.get("status", "unknown"),
                              busy=runner.get("busy"),
                              labelsVerified=required.issubset(labels))
                return status
        status.update(read="readable", status="not_registered")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError,
            ValueError, KeyError, TypeError) as exc:
        status["read"] = safe_error(exc)
    return status

ACTIVE_RUN_STATES = ("queued", "in_progress", "waiting", "pending", "requested")


def existing_attempt(repo: str, workflow: str | None, spec: dict, env: dict,
                     *, reader_factory=GitHubReadOnly) -> dict:
    """Query ALL active statuses explicitly; never infer absence from newest N runs.

    GitHub's unfiltered list only returns the newest page, so an older stalled
    run can be missed. Any one failed/malformed filtered query means UNKNOWN
    and fails closed. Public output contains status only, not private run IDs.
    """
    result = {"status": "unknown", "read": "not_configured",
              "matchingTaskVerified": False}
    if not workflow:
        return result
    token = env.get(spec["actionsSecret"], "")
    if not token:
        result["read"] = "secret_missing"
        return result
    try:
        reader = reader_factory(token)
        for status in ACTIVE_RUN_STATES:
            path = (f"/repos/{repo}/actions/workflows/{workflow}/runs"
                    f"?status={status}&per_page=1")
            record = reader.get(path)
            count = record.get("total_count")
            rows = record.get("workflow_runs")
            if type(count) is not int or count < 0 or not isinstance(rows, list):
                raise ValueError("untrusted_status_filtered_run_query")
            if count > 0:
                if not rows or not isinstance(rows[0], dict) or rows[0].get("status") != status:
                    raise ValueError("filtered_run_missing_confirmed_status")
                result.update(read="readable", status="active")
                return result
            if rows:
                raise ValueError("zero_count_but_runs_returned")
        result.update(read="readable", status="no_active_run")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError,
            ValueError, KeyError, TypeError) as exc:
        result.update(read=safe_error(exc), status="unknown")
    return result
