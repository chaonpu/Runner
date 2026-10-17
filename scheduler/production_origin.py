"""Verify a same-repository bot launch against its immutable human push request."""
from datetime import datetime, timezone

from scheduler.production_registry import RegistryGuard
from scheduler.production_session import context


def authorize(env, requester, reader_factory):
    context(env)
    if env["GITHUB_ACTOR"] == "chaonpu":
        return
    from scheduler.production_ports import document, PUBLIC
    token = env.get("GH_TOKEN", "")
    parent = reader_factory(token).get(PUBLIC + "/actions/runs/" + env["BOOTSTRAP_RUN_ID"])
    good = (parent.get("repository", {}).get("full_name") == "chaonpu/Runner"
        and str(parent.get("id")) == env["BOOTSTRAP_RUN_ID"]
        and parent.get("path", "").split("@")[0] == ".github/workflows/production-launch.yml"
        and parent.get("event") == "push" and parent.get("head_branch") == "main"
        and parent.get("head_sha") == env["GITHUB_SHA"] and parent.get("run_attempt") == 1
        and parent.get("actor", {}).get("login") == "chaonpu"
        and parent.get("status") in ("in_progress", "completed")
        and parent.get("conclusion") in (None, "success"))
    if not good:
        raise RegistryGuard("bootstrap_parent_untrusted")
    created = datetime.fromisoformat(parent["created_at"].replace("Z", "+00:00"))
    if created.tzinfo is None or not 0 <= (datetime.now(timezone.utc)-created).total_seconds() <= 900:
        raise RegistryGuard("bootstrap_parent_stale")
    path = "config/launch-request.json"
    record = requester(token, "GET", PUBLIC + "/contents/" + path + "?ref=" + env["GITHUB_SHA"])
    request, _ = document(record, path)
    if not (request.get("enabled") is True and request.get("task") == env.get("REQUEST_TASK")
            and request.get("publicationMode") == env.get("REQUEST_PUBLICATION_MODE")
            and request.get("businessDate", "") == env.get("REQUEST_BUSINESS_DATE", "")):
        raise RegistryGuard("bootstrap_request_scope_mismatch")
