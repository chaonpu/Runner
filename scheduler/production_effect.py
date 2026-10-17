"""Public-safe proof of the private same-run receipt attestation after completion."""
from scheduler.production_registry import RegistryGuard
from scheduler.production_evidence import timestamp
from scheduler.production_session import WORKFLOWS

STEP = "Verify and attest published ledger receipt"


def verify(run, jobs, row):
    mode = row.get("publicationMode", "formal")
    if not (run.get("id") == row["runId"] and run.get("repository", {}).get("full_name") == row["repo"]
            and run.get("event") == "workflow_dispatch" and run.get("head_branch") == "main"
            and run.get("path", "").split("@")[0] == ".github/workflows/"+WORKFLOWS[row["task"]]
            and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and run.get("head_sha") == row.get("sourceSha")):
        raise RegistryGuard("private_effect_run_unverified")
    if not (isinstance(jobs.get("jobs"), list) and type(jobs.get("total_count")) is int
            and 1 <= jobs["total_count"] <= 100 and jobs["total_count"] == len(jobs["jobs"])):
        raise RegistryGuard("private_effect_jobs_incomplete")
    name = f"effect-v1:{row['id']}:{row['coordinatorRunId']}:{mode}"
    found = [j for j in jobs["jobs"] if j.get("name") == name]
    if len(found) != 1:
        raise RegistryGuard("private_effect_job_not_unique")
    job = found[0]
    steps = [s for s in job.get("steps", []) if s.get("name") == STEP]
    if not (job.get("run_id") == row["runId"] and job.get("status") == "completed"
            and job.get("conclusion") == "success" and len(steps) == 1
            and steps[0].get("status") == "completed" and steps[0].get("conclusion") == "success"):
        raise RegistryGuard("private_effect_attestation_failed")
    if "run_attempt" in job and (type(job["run_attempt"]) is not int or job["run_attempt"] != 1):
        raise RegistryGuard("private_effect_job_rerun")
    if not timestamp(run["run_started_at"]) <= timestamp(steps[0]["completed_at"]) <= timestamp(job["completed_at"]) <= timestamp(run["updated_at"]):
        raise RegistryGuard("private_effect_attestation_time_invalid")
    return {"privateSameCommitReceiptAttested": True, "originalRunSucceeded": True,
            "publicationMode": mode, "formalReportVerified": mode == "formal"}
