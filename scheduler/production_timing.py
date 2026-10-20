"""Best-effort GitHub Actions step timing, never a business authorization.

The original strategy step combines source reading, industry queries, and
ranking; report those as an aggregate until native stage timers are available.
No private file contents, quotes, holdings, logs or credentials are requested.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path

REPO = "chaonpu/Stock"
CATEGORIES = {
    "components_setup": (
        "Set up job", "Setup original ledger Node runtime",
        "Set up Node 22 and Python 3.11",
        "Set up Python 3.13 with requirements-keyed pip cache",
        "Use verified cached Windows Node 22", "Setup hosted Node 22",
        "Select verified local Node 22 without downloading an action",
        "Select hosted Node 22 from the tool cache",
        "Use hosted Python 3.13 from the tool cache",
    ),
    "dependency_install": (
        "Install production runtime in a disposable environment",
        "Install isolated production dependencies",
        "Install dependencies", "Prepare isolated Windows Python strategy runtime",
        "Restore isolated cloud pip downloads",
    ),
    "git_route_verification": (
        "Prepare verified job-scoped GitHub route before Windows checkout",
        "Verify database route independently and pin its main commit",
    ),
    "data_and_selection_combined": ("Run A-share strategy with retry", "Run US strategy with retry"),
    "light_source_probes": (
        "Check authoritative inputs, complete native selection and live sources",
        "Check native selection, authoritative inputs and live feeds",
    ),
    "ledger_validation_and_publish": ("Publish verified snapshot and ledger to private database",),
    "mail_delivery": ("Send A-share mail only after verified ledger publication",),
    "receipt_confirmation": ("Verify and attest published ledger receipt",),
}


def seconds(step):
    start, end = step.get("started_at"), step.get("completed_at")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        value = (b - a).total_seconds()
        return round(value, 2) if a.tzinfo and b.tzinfo and value >= 0 else None
    except ValueError:
        return None


def summarize(jobs):
    if not isinstance(jobs, dict) or not isinstance(jobs.get("jobs"), list):
        raise ValueError("timing_jobs_unavailable")
    out = {key: 0.0 for key in CATEGORIES}
    names = {name: key for key, values in CATEGORIES.items() for name in values}
    for job in jobs["jobs"]:
        for step in job.get("steps", []) if isinstance(job, dict) else []:
            if not isinstance(step, dict):
                continue
            key = names.get(step.get("name"))
            duration = seconds(step)
            if key is not None and duration is not None and step.get("conclusion") == "success":
                out[key] = round(out[key] + duration, 2)
    return {**out,
            "market_quotes_seconds": None, "industry_lookup_seconds": None,
            "pure_selection_compute_seconds": None,
            "timingScope": "github_actions_successful_step_wall_time",
            "combinedStrategyStepCannotBeFurtherSeparated": True}


def publish_summary(summary, readiness_run_id, effect_run_id, env=os.environ, *, task="stock_a"):
    report = {"phase": "measured_action_step_timings", "readinessRunId": readiness_run_id,
              "effectRunId": effect_run_id, **summary}
    print(json.dumps(report, sort_keys=True), flush=True)
    location = env.get("GITHUB_STEP_SUMMARY")
    if location:
        parts = [
            "\n### " + ("美股" if task == "stock_us" else "A股") + "运行分段耗时（GitHub Actions 实测）\n",
            "| 分段 | 秒 |\n|---|---:|",
            *[f"| {key} | {summary[key]:.2f} |" for key in CATEGORIES],
            "\n*行情读取、行业查询与纯计算仍在同一正式策略步骤中，无法从 Actions 步骤时间独立精确拆分，未虚构分段值。*\n",
        ]
        with Path(location).open("a", encoding="utf-8") as stream:
            stream.write("\n".join(parts))


def collect(ports, readiness_run_id, effect_run_id):
    if not all(type(n) is int and n > 0 for n in (readiness_run_id, effect_run_id)):
        raise ValueError("timing_run_id_invalid")
    reader = ports.private_reader()
    endpoint = lambda rid: f"/repos/{REPO}/actions/runs/{rid}/jobs?filter=latest&per_page=100"
    pre = summarize(reader.get(endpoint(readiness_run_id)))
    effect = summarize(reader.get(endpoint(effect_run_id)))
    return {key: round(pre[key] + effect[key], 2) for key in CATEGORIES} | {
        "market_quotes_seconds": None,
        "industry_lookup_seconds": None,
        "pure_selection_compute_seconds": None,
        "timingScope": "github_actions_successful_step_wall_time",
        "combinedStrategyStepCannotBeFurtherSeparated": True,
    }

