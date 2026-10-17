"""Restricted GitHub Contents storage for public-safe production task metadata.

No workflow currently invokes this module. It never dispatches a target, reads
private code, or verifies business effects. An uncertain PUT is a HOLD, not a
reason to repeat a dispatch. Caller MUST serialize by workflow concurrency.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request

from scheduler.production_registry import RegistryGuard, validate

API = "https://api.github.com"
CONTENTS_PATH = "/repos/chaonpu/Runner/contents/state/tasks.json"
SHA = re.compile(r"^[0-9a-f]{40}$")
PROTECTED = ("id", "task", "repo", "workflow", "route", "coordinatorRunId", "createdAt", "sourceSha", "publicationMode")
ALLOWED = {
    "reserved": {"dispatched", "hold"},
    "dispatched": {"running", "awaiting_effect_verification", "hold"},
    "running": {"awaiting_effect_verification", "hold"},
    "awaiting_effect_verification": {"hold"},
    "hold": set(),
    "verified": set(),
}


def context_allowed(env: dict) -> None:
    if (env.get("GITHUB_REPOSITORY") != "chaonpu/Runner"
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or env.get("GITHUB_ACTOR") not in ("chaonpu", "github-actions[bot]")
            or not re.fullmatch(r"[1-9][0-9]{7,19}", env.get("GITHUB_RUN_ID", ""))):
        raise RegistryGuard("untrusted_production_registry_context")


def _request(token: str, method: str, url: str, body=None, opener=urllib.request.urlopen) -> dict:
    if not isinstance(token, str) or not token:
        raise RegistryGuard("registry_token_missing")
    if method not in ("GET", "PUT") or not url.startswith(CONTENTS_PATH):
        raise RegistryGuard("invalid_registry_endpoint")
    if method == "PUT" and url != CONTENTS_PATH:
        raise RegistryGuard("invalid_registry_write_path")
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "Content-Type": "application/json",
        "User-Agent": "ziwuxilv-production-registry/1.0",
    }
    req = urllib.request.Request(
        API + url, method=method, headers=headers,
        data=None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    )
    try:
        with opener(req, timeout=20) as response:
            value = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RegistryGuard("registry_api_response_uncertain_no_retry") from exc
    if not isinstance(value, dict):
        raise RegistryGuard("invalid_registry_api_response")
    return value


def decode_record(record: dict, config: dict) -> tuple[dict, str]:
    if (not isinstance(record, dict) or record.get("type") != "file"
            or record.get("encoding") != "base64"
            or not isinstance(record.get("sha"), str)
            or not SHA.fullmatch(record["sha"])):
        raise RegistryGuard("registry_content_metadata_invalid")
    try:
        encoded = record["content"]
        if not isinstance(encoded, str) or len(encoded) > 2_000_000:
            raise RegistryGuard("registry_content_invalid")
        # The GitHub Contents API wraps base64 at line boundaries. Reject
        # non-alphabet characters other than the API's CR/LF formatting.
        raw = base64.b64decode(encoded.replace("\n", "").replace("\r", ""),
                               validate=True)
        state = json.loads(raw.decode("utf-8"))
    except (KeyError, ValueError, TypeError, UnicodeError) as exc:
        raise RegistryGuard("registry_content_invalid") from exc
    return validate(config, state), record["sha"]


def load(config: dict, token: str, requester=_request) -> tuple[dict, str]:
    return decode_record(requester(token, "GET", CONTENTS_PATH + "?ref=main"), config)


def verify_change(config: dict, before: dict, after: dict, source_run_id: int) -> str:
    """Permit one safe task addition/transition, forbid changing any others.

    No verified write is possible: private effect attestation is not
    integrated yet. This intentional stop prevents false completed ledgers.
    """
    validate(config, before)
    validate(config, after)
    if ({k: v for k, v in before.items() if k != "tasks"} !=
            {k: v for k, v in after.items() if k != "tasks"}):
        raise RegistryGuard("registry_root_metadata_immutable")
    if type(source_run_id) is not int or source_run_id <= 0:
        raise RegistryGuard("invalid_source_run")
    initial = {row["id"]: row for row in before["tasks"]}
    final = {row["id"]: row for row in after["tasks"]}
    changed = [k for k in final if k not in initial or final[k] != initial[k]]
    if set(initial) - set(final) or len(changed) != 1:
        raise RegistryGuard("registry_change_must_affect_one_task")
    key = changed[0]
    new = final[key]
    old = initial.get(key)
    if old is None:
        if (new["status"] != "reserved" or
                new["coordinatorRunId"] != source_run_id or
                new.get("runId") is not None):
            raise RegistryGuard("only_reservation_can_be_created")
        if (config.get("dispatchEnabled") is not True or
                config["tasks"][new["task"]].get("enabledForDispatch") is not True):
            raise RegistryGuard("production_dispatch_disabled")
    else:
        if old["coordinatorRunId"] != source_run_id:
            raise RegistryGuard("not_original_dispatch_owner")
        if new["status"] not in ALLOWED[old["status"]]:
            raise RegistryGuard("unsafe_registry_transition")
        for field in PROTECTED:
            if old.get(field) != new.get(field):
                raise RegistryGuard("immutable_task_metadata_modified")
        if old.get("runId") is not None and new.get("runId") != old.get("runId"):
            raise RegistryGuard("run_id_rebound")
        if old.get("runId") is None and new.get("runId") is not None:
            if (old["status"] != "reserved" or new["status"] != "dispatched"
                    or type(new["runId"]) is not int):
                raise RegistryGuard("run_id_bind_not_allowed")
        if new.get("updatedAt") == old.get("updatedAt"):
            raise RegistryGuard("timestamp_not_updated")
    return key


def save(config: dict, before: dict, after: dict, sha: str, env: dict,
         requester=_request) -> dict:
    context_allowed(env)
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise RegistryGuard("registry_sha_invalid")
    key = verify_change(config, before, after, int(env["GITHUB_RUN_ID"]))
    token = env.get("GH_TOKEN", "")
    # One PUT with expected source blob SHA. Never retry automatically after
    # a conflict, timeout, or an unparseable server response.
    value = requester(token, "PUT", CONTENTS_PATH, {
        "message": "Update safe production task state",
        "branch": "main",
        "sha": sha,
        "content": base64.b64encode(
            (json.dumps(after, sort_keys=True, indent=2) + "\n").encode("utf-8")
        ).decode("ascii"),
    })
    result = value.get("content")
    if not isinstance(result, dict) or not SHA.fullmatch(str(result.get("sha", ""))):
        raise RegistryGuard("registry_write_result_unconfirmed")
    return {"taskId": key, "recorded": True, "dispatched": False, "verified": False}

