"""Read only a dedicated PRIVATE readiness status and its Actions metadata.

Never download code, artifacts, logs, holdings or ledger data. The private
producer is not installed yet: absence means HOLD, never readiness by inference.
GitHub status API: https://docs.github.com/en/rest/commits/statuses
"""
from datetime import date, datetime, timezone
import re

from scheduler.production_registry import RegistryGuard

REPO = "chaonpu/Stock"
WORKFLOW = ".github/workflows/production-readiness.yml"
SHA = re.compile(r"^[0-9a-f]{40}$")
CLAIM = re.compile(r"^v1:(stock_a|stock_us):(\d{4}-\d{2}-\d{2}):([1-9][0-9]{7,19}):ready$")


def hold(reason):
    return {"evidenceStatus": reason, "exchangeSessionVerified": False,
            "marketDataReady": False, "selectionInputsVerified": False,
            "observedAt": None, "productionReady": False}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp_missing")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp_ambiguous")
    return result.astimezone(timezone.utc)


def inspect(reader, task, day, source_sha, now):
    """A private ready claim must attest ALL three private readiness checks.

    Producer contract: live session/close, live complete upstream quote inputs,
    and complete selection inputs, all for this task/date/source. Its workflow
    must only publish success after verifying these locally, and then complete
    successfully. Historical isolated validation workflows are not accepted.
    """
    if (task not in ("stock_a", "stock_us") or not SHA.fullmatch(str(source_sha))
            or not isinstance(now, datetime) or now.tzinfo is None):
        raise RegistryGuard("readiness_evidence_request_invalid")
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except (TypeError, ValueError):
        raise RegistryGuard("readiness_evidence_date_invalid") from None
    try:
        combined = reader.get(f"/repos/{REPO}/commits/{source_sha}/status?per_page=100")
        if (not isinstance(combined, dict) or combined.get("sha") != source_sha
                or not isinstance(combined.get("statuses"), list)
                or type(combined.get("total_count")) is not int
                or combined["total_count"] < len(combined["statuses"])):
            return hold("hold_evidence_response_invalid")
        context = "runner/production-readiness/" + task
        rows = [s for s in combined["statuses"]
                if isinstance(s, dict) and s.get("context") == context]
        # Combined status supplies the newest status per context. Never search
        # past a missing/failed claim to resurrect an older successful status.
        if not rows:
            return hold("hold_private_readiness_evidence_missing")
        if len(rows) != 1:
            return hold("hold_evidence_context_ambiguous")
        claim = rows[0]
        match = CLAIM.fullmatch(str(claim.get("description", "")))
        if not match or match.group(1, 2) != (task, day) or claim.get("state") != "success":
            return hold("hold_evidence_claim_unverified")
        rid = int(match.group(3))
        if claim.get("target_url") != f"https://github.com/{REPO}/actions/runs/{rid}":
            return hold("hold_evidence_target_unverified")
        observed = timestamp(claim.get("created_at"))
        # updated_at cannot refresh an older measurement or extend its TTL.
        if not 0 <= (now - observed).total_seconds() <= 300:
            return hold("hold_evidence_stale_or_future")
        run = reader.get(f"/repos/{REPO}/actions/runs/{rid}")
        if (not isinstance(run, dict) or type(run.get("id")) is not int
                or run["id"] != rid or run.get("repository", {}).get("full_name") != REPO
                or run.get("path", "").split("@")[0] != WORKFLOW
                or run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
                or run.get("head_sha") != source_sha or type(run.get("run_attempt")) is not int
                or run["run_attempt"] != 1 or run.get("actor", {}).get("login") != "chaonpu"
                or run.get("status") != "completed" or run.get("conclusion") != "success"):
            return hold("hold_evidence_run_unverified")
        started = timestamp(run.get("run_started_at"))
        finished = timestamp(run.get("updated_at"))
        if not started <= observed <= finished <= now:
            return hold("hold_evidence_run_time_unverified")
        inputs = run.get("inputs")
        if inputs is not None and (not isinstance(inputs, dict)
                or inputs.get("task") != task
                or inputs.get("business_date") not in ("", None, day)):
            return hold("hold_evidence_run_inputs_conflict")
        return {"evidenceStatus": "private_ready_status_and_original_run_match",
                "exchangeSessionVerified": True, "marketDataReady": True,
                "selectionInputsVerified": True, "observedAt": observed.isoformat(),
                "productionReady": False}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return hold("hold_evidence_unavailable")
