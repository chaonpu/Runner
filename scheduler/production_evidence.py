"""Actions-only private readiness metadata; no Contents/status permission.

The dedicated private producers are not installed yet. Absence is HOLD.
No private code, artifacts, ledger, holdings or logs are requested.
"""
from datetime import date, datetime, timezone
import re
from urllib.error import HTTPError

from scheduler.production_registry import RegistryGuard

REPO = "chaonpu/Stock"
WORKFLOWS = {"stock_a": "production-readiness-a.yml", "stock_us": "production-readiness-us.yml"}
STEP = "Attest live readiness"
SHA = re.compile(r"^[0-9a-f]{40}$")


def hold(reason):
    return {"evidenceStatus": reason, "exchangeSessionVerified": False,
            "marketDataReady": False, "selectionInputsVerified": False,
            "sourceSha": "", "observedAt": None, "productionReady": False}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp_missing")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp_ambiguous")
    return result.astimezone(timezone.utc)


def inspect(reader, task, day, now):
    """Require newest original readiness Run, exact dated job and final step.

    The future producer must independently check live session completion,
    complete quotes and complete selection inputs for this exact day/source.
    Returned SHA is a fresh SOURCE CANDIDATE, not a claim of current main.
    Actual production Run SHA must match before binding/private admission.
    """
    if task not in WORKFLOWS or not isinstance(now, datetime) or now.tzinfo is None:
        raise RegistryGuard("readiness_evidence_request_invalid")
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except (TypeError, ValueError):
        raise RegistryGuard("readiness_evidence_date_invalid") from None
    try:
        listing = reader.get(f"/repos/{REPO}/actions/workflows/{WORKFLOWS[task]}/runs"
                             "?event=workflow_dispatch&branch=main&per_page=1")
        if (not isinstance(listing, dict) or type(listing.get("total_count")) is not int
                or listing["total_count"] < 0 or not isinstance(listing.get("workflow_runs"), list)):
            return hold("hold_evidence_response_invalid")
        rows = listing["workflow_runs"]
        if listing["total_count"] == 0 and not rows:
            return hold("hold_private_readiness_evidence_missing")
        if listing["total_count"] < 1 or len(rows) != 1:
            return hold("hold_evidence_response_invalid")
        run = rows[0]
        if (not isinstance(run, dict) or type(run.get("id")) is not int
                or not re.fullmatch(r"[1-9][0-9]{7,19}", str(run["id"]))
                or run.get("repository", {}).get("full_name") != REPO
                or run.get("path", "").split("@")[0] != ".github/workflows/" + WORKFLOWS[task]
                or run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
                or not SHA.fullmatch(str(run.get("head_sha", "")))
                or type(run.get("run_attempt")) is not int or run["run_attempt"] != 1
                or run.get("actor", {}).get("login") != "chaonpu"
                or run.get("status") != "completed" or run.get("conclusion") != "success"):
            return hold("hold_evidence_run_unverified")
        # Never fall back to older green evidence after a failed/pending run.
        finished = timestamp(run.get("updated_at"))
        if not 0 <= (now - finished).total_seconds() <= 300:
            return hold("hold_evidence_stale_or_future")
        rid = run["id"]
        jobs = reader.get(f"/repos/{REPO}/actions/runs/{rid}/jobs?filter=latest&per_page=100")
        if (not isinstance(jobs, dict) or type(jobs.get("total_count")) is not int
                or not isinstance(jobs.get("jobs"), list)
                or not 1 <= jobs["total_count"] <= 100
                or jobs["total_count"] != len(jobs["jobs"])):
            return hold("hold_evidence_jobs_incomplete")
        matches = [j for j in jobs["jobs"] if isinstance(j, dict)
                   and j.get("name") == f"readiness-v1:{task}:{day}"]
        if len(matches) != 1:
            return hold("hold_evidence_dated_job_missing_or_ambiguous")
        job = matches[0]
        if (type(job.get("run_id")) is not int or job["run_id"] != rid
                or job.get("head_sha", run["head_sha"]) != run["head_sha"]
                or job.get("status") != "completed" or job.get("conclusion") != "success"
                or not isinstance(job.get("steps"), list)):
            return hold("hold_evidence_job_unverified")
        # Some REST job responses omit run_attempt; the original run is already
        # required to have attempt=1. Reject a conflicting known job attempt.
        if "run_attempt" in job and (type(job["run_attempt"]) is not int or job["run_attempt"] != 1):
            return hold("hold_evidence_job_unverified")
        steps = [s for s in job["steps"] if isinstance(s, dict) and s.get("name") == STEP]
        if (len(steps) != 1 or steps[0].get("status") != "completed"
                or steps[0].get("conclusion") != "success"):
            return hold("hold_evidence_attestation_step_unverified")
        observed = timestamp(steps[0].get("completed_at"))
        started = timestamp(run.get("run_started_at"))
        completed = timestamp(job.get("completed_at"))
        if not started <= observed <= completed <= finished <= now:
            return hold("hold_evidence_run_time_unverified")
        if not 0 <= (now - observed).total_seconds() <= 300:
            return hold("hold_evidence_stale_or_future")
        inputs = run.get("inputs")
        if inputs is not None and (not isinstance(inputs, dict)
                or inputs.get("business_date") not in ("", None, day)):
            return hold("hold_evidence_run_inputs_conflict")
        return {"evidenceStatus": "private_readiness_run_and_dated_job_match",
                "exchangeSessionVerified": True, "marketDataReady": True,
                "selectionInputsVerified": True, "sourceSha": run["head_sha"],
                "observedAt": observed.isoformat(), "productionReady": False}
    except HTTPError as exc:
        return hold("hold_private_readiness_workflow_missing_or_not_visible" if exc.code == 404
                    else "hold_evidence_permission_denied" if exc.code in (401, 403)
                    else "hold_evidence_unavailable")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return hold("hold_evidence_unavailable")
