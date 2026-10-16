"""Single-run LIVE GitHub API acceptance for the locked production gate.

Only called from the public Runner's push validation workflow for a deliberate
one-time test. It has no HTTP mutation methods, private checkout, dispatch,
write of registry state, source download, recurring polling or retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from scheduler.production_dry_run import check
from scheduler.production_registry import RegistryGuard
from scheduler.production_store import load

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "repositories.json"
EXPECTED_WORKFLOW = (
    "chaonpu/Runner/.github/workflows/validate.yml@refs/heads/main"
)


def accept(env: dict, *, at: datetime | None = None,
           read_registry=load, audit=check) -> dict:
    if not all((
        env.get("GITHUB_REPOSITORY") == "chaonpu/Runner",
        env.get("GITHUB_EVENT_NAME") == "push",
        env.get("GITHUB_REF") == "refs/heads/main",
        env.get("GITHUB_WORKFLOW_REF") == EXPECTED_WORKFLOW,
        env.get("GITHUB_RUN_ATTEMPT") == "1",
        bool(env.get("GH_TOKEN")),
    )):
        raise RegistryGuard("live_audit_context_invalid")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("dispatchEnabled") is not False or any(
        entry.get("enabledForDispatch") is not False
        for entry in config["tasks"].values()
    ):
        raise RegistryGuard("production_gate_not_locked")
    registry, sha = read_registry(config, env["GH_TOKEN"])
    if not sha or len(sha) != 40:
        raise RegistryGuard("live_registry_sha_untrusted")
    # Use one instant for both markets. No exchange calendar evidence and
    # no business-result audit are implied by the date candidate.
    instant = datetime.now(timezone.utc) if at is None else at
    outcomes = []
    for task in ("stock_a", "stock_us"):
        out = audit(config, registry, task, "", env=env, clock=instant)
        if (out.get("status") != "locked_read_only"
                or out.get("dateMode") != "auto_market_clock_candidate"
                or out.get("exchangeSessionVerified") is not False
                or any(out.get(key) is not False for key in (
                    "dispatchAuthorized",
                    "registryWriteAuthorized",
                    "databaseWriteAuthorized",
                    "automaticRetryAuthorized",
                    "productionReady",
                ))):
            raise RegistryGuard("read_only_acceptance_not_confirmed")
        outcomes.append({
            "market": task,
            "candidateDate": out["businessDate"],
            "dateMode": out["dateMode"],
            "marketTimezone": out["marketTimezone"],
            "registryStatus": out["registeredTaskStatus"],
            "recommendation": out["routeRecommendation"],
            "dispatchAuthorized": False,
            "exchangeSessionVerified": False,
        })
    return {
        "status": "live_registry_read_only_confirmed",
        "registryApiRead": True,
        "taskCount": len(registry["tasks"]),
        "marketAudits": outcomes,
        "productionDispatch": False,
        "privateDatabaseRead": False,
        "registryMutation": False,
        "scheduledFollowUp": False,
    }


def main() -> int:
    try:
        result = accept(os.environ)
    except (OSError, ValueError, KeyError, TypeError):
        result = {
            "status": "hold_live_registry_unconfirmed",
            "registryApiRead": False,
            "productionDispatch": False,
            "privateDatabaseRead": False,
            "registryMutation": False,
            "scheduledFollowUp": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## RUNNER 一次性线上只读验收\n\n")
            f.write("- 读取实时公开任务登记：成功；未修改登记\n")
            for item in result["marketAudits"]:
                f.write(
                    f"- {item['market']}: {item['candidateDate']} "
                    f"({item['marketTimezone']}), "
                    f"现有任务={item['registryStatus']}, "
                    f"路由建议={item['recommendation']}\n"
                )
            f.write("- 生产派发、私有仓库检出、真实账本更新及重试：全部关闭\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
