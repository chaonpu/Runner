"""Pure recent Windows no-business canary evidence; NEVER a live runner-list claim.

The coordinator may dispatch the already-allowlisted Stock canary exactly once,
watch its single run in the foreground and then evaluate this result. A stale
successful job proves only its historical execution, not that the PC is online.
This module performs no network requests or dispatch itself.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

NAME = "ziwuxilv-stock-win"
REPO = "chaonpu/Stock"
WORKFLOW = ".github/workflows/runner-dispatch-canary.yml"
STEP = "Verify dispatch identity and Windows runner only"
LABELS = frozenset(("self-hosted", "Windows", "X64"))


class CanaryGuard(ValueError):
    pass


def check(registered: dict, run: dict, jobs: dict, *,
          observed_at: datetime, max_age_seconds: int = 90) -> dict:
    if (not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or type(max_age_seconds) is not int
            or not 15 <= max_age_seconds <= 180):
        raise CanaryGuard("invalid_observation_time")
    if not all(isinstance(x, dict) for x in (registered, run, jobs)):
        raise CanaryGuard("invalid_probe_evidence")
    coordinator = registered.get("coordinatorRunId")
    run_id = registered.get("runId")
    if (type(coordinator) is not int or coordinator <= 0
            or type(run_id) is not int or run_id <= 0
            or registered.get("canary") != f"runner-canary-{coordinator}-stock"
            or not re.fullmatch(r"pilot:stock:\d{4}-\d{2}-\d{2}",
                                str(registered.get("id", "")))
            or registered.get("repo") != REPO
            or registered.get("workflow") != "runner-dispatch-canary.yml"):
        raise CanaryGuard("untrusted_canary_registry_identity")
    if (run.get("id") != run_id
            or run.get("repository", {}).get("full_name") != REPO
            or run.get("path", "").split("@", 1)[0] != WORKFLOW
            or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != "main"
            or run.get("run_attempt") != 1
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"):
        raise CanaryGuard("run_identity_or_completion_not_confirmed")
    items = jobs.get("jobs")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise CanaryGuard("canary_job_count_invalid")
    job = items[0]
    if (job.get("runner_name") != NAME
            or type(job.get("runner_id")) is not int
            or job["runner_id"] <= 0
            or not isinstance(job.get("labels"), list)
            or not LABELS.issubset(set(job["labels"]))
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
            or job.get("run_id") not in (None, run_id)):
        raise CanaryGuard("wrong_or_failed_windows_runner")
    steps = job.get("steps")
    if (not isinstance(steps, list)
            or len([s for s in steps if isinstance(s, dict)
                    and s.get("name") == STEP and s.get("conclusion") == "success"]) != 1):
        raise CanaryGuard("canary_business_free_verification_missing")
    try:
        stamp = datetime.fromisoformat(
            job["completed_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CanaryGuard("missing_job_completed_timestamp") from exc
    age = (observed_at.astimezone(timezone.utc) -
           stamp.astimezone(timezone.utc)).total_seconds()
    if not 0 <= age <= max_age_seconds:
        raise CanaryGuard("canary_stale_or_future_timestamp")
    return {
        "status": "recent_no_business_windows_canary_confirmed",
        "runId": run_id,
        "runnerName": NAME,
        "runnerRecentlyExecuted": True,
        "runnerCurrentlyOnlineIndependentlyVerified": False,
        "strategySourceCompatible": False,
        "productionDispatchAuthorized": False,
        "automatedBusinessRetryAuthorized": False,
    }
