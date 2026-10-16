"""Public, read-only production coordinator entrypoint; NO dispatch mechanism.

A workflow with this filename is required for private receipt provenance.
This implementation intentionally has no registry writes, HTTP POST, direct
workflow dispatch, status transitions or compensation. All output is public-safe.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import sys
from pathlib import Path

from scheduler.market_clock import resolve_date
from scheduler.exchange_schedule import CalendarGuard, evaluate as audit_schedule
from scheduler.preflight import preflight
from scheduler.production_registry import RegistryGuard, validate
from scheduler.production_store import load

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "repositories.json"
WORKFLOW_REF = "chaonpu/Runner/.github/workflows/production-dispatch.yml@refs/heads/main"
SUPPORTED = frozenset(("stock_a", "stock_us"))


def context_allowed(env: dict) -> None:
    checks = (
        env.get("GITHUB_REPOSITORY") == "chaonpu/Runner",
        env.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
        env.get("GITHUB_REF") == "refs/heads/main",
        env.get("GITHUB_ACTOR") == "chaonpu",
        env.get("GITHUB_WORKFLOW_REF") == WORKFLOW_REF,
        env.get("GITHUB_RUN_ATTEMPT") == "1",
        bool(re.fullmatch(r"[1-9][0-9]{7,19}", str(env.get("GITHUB_RUN_ID", "")))),
    )
    if not all(checks):
        raise RegistryGuard("production_dry_run_context_untrusted")


def check(config: dict, registry: dict, task: str, business_date: str = "",
          *, env: dict | None = None, preflight_fn=preflight,
          clock: datetime | None = None) -> dict:
    """Calculate recommendation, but physically never authorize any side effect."""
    env = {} if env is None else env
    if task not in SUPPORTED or task not in config.get("tasks", {}):
        raise RegistryGuard("production_dry_run_task_not_allowed")
    instant = datetime.now(timezone.utc) if clock is None else clock
    try:
        session = resolve_date(task, business_date, now=instant)
        published = audit_schedule(task, business_date, now=instant)
    except CalendarGuard as exc:
        raise RegistryGuard("published_schedule_unavailable_or_invalid") from exc
    except (ValueError, TypeError) as exc:
        raise RegistryGuard("production_dry_run_date_invalid") from exc
    validate(config, registry)
    # Fail closed even if a later commit accidentally flips one of the flags.
    if config.get("dispatchEnabled") is not False or any(
        config["tasks"][name].get("enabledForDispatch") is not False
        for name in SUPPORTED
    ):
        raise RegistryGuard("production_dispatch_lock_is_not_off")
    if not published.get("businessDate") or published["calendarCoverageCurrent"] is not True:
        return {
            "status": "hold_calendar_coverage_unavailable",
            "businessDate": published.get("businessDate"),
            "dateMode": published["dateMode"],
            "marketTimezone": published["marketTimezone"],
            "marketLocalClock": published["marketLocalClock"],
            "calendarStatus": published["calendarStatus"],
            "publishedScheduleMatched": False,
            "scheduledSessionClosed": False,
            "exchangeSessionVerified": False,
            "marketDataReady": False,
            "dispatchAuthorized": False,
            "registryWriteAuthorized": False,
            "databaseWriteAuthorized": False,
            "automaticRetryAuthorized": False,
            "productionReady": False,
        }
    report = preflight_fn(config, task, business_date=published["businessDate"],
                          env=env, registry_state=registry)
    if (not isinstance(report, dict) or not isinstance(report.get("taskId"), str)
            or not isinstance(report.get("execution"), dict)
            or report["execution"].get("dispatchAuthorized") is not False):
        raise RegistryGuard("unexpected_preflight_authorization")
    # Never reproduce remotely-returned billing, tokens or private-run metadata.
    state = report.get("registeredTask", {})
    if not isinstance(state, dict):
        raise RegistryGuard("unexpected_registered_task")
    recommended = report.get("recommendation", {})
    if not isinstance(recommended, dict):
        raise RegistryGuard("unexpected_route_result")
    return {
        "status": "locked_read_only",
        "taskId": report["taskId"],
        "businessDate": published["businessDate"],
        "dateMode": published["dateMode"],
        "marketTimezone": published["marketTimezone"],
        "marketLocalClock": published["marketLocalClock"],
        "calendarStatus": published["calendarStatus"],
        "publishedScheduleMatched": published["publishedScheduleMatched"],
        "scheduledSessionClosed": published["scheduledSessionClosed"],
        "scheduledClose": published["scheduledClose"],
        "exchangeSessionVerified": False,
        "marketDataReady": False,
        "registeredTaskStatus": state.get("status", "unavailable"),
        "routeRecommendation": recommended.get("route", "hold"),
        "routeReason": recommended.get("reason", "not_ready"),
        "dispatchAuthorized": False,
        "registryWriteAuthorized": False,
        "databaseWriteAuthorized": False,
        "automaticRetryAuthorized": False,
        "productionReady": False,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task", choices=sorted(SUPPORTED), required=True)
    p.add_argument("--business-date", default="")
    args = p.parse_args()
    try:
        context_allowed(os.environ)
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        if not os.environ.get("GH_TOKEN"):
            raise RegistryGuard("missing_read_token")
        # Source is live public repository Contents, rather than a possibly
        # outdated checkout of state/tasks.json.
        registry, _ = load(config, os.environ["GH_TOKEN"])
        result = check(config, registry, args.task, args.business_date,
                       env=os.environ)
    except (RegistryGuard, ValueError, KeyError, TypeError, OSError):
        result = {
            "status": "hold_untrusted_or_unavailable",
            "dispatchAuthorized": False,
            "registryWriteAuthorized": False,
            "databaseWriteAuthorized": False,
            "automaticRetryAuthorized": False,
            "productionReady": False,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 子午汐律｜正式调度只读安全门\n\n")
            f.write(f"- 状态：{result['status']}\n")
            f.write(f"- 日期来源：{result.get('dateMode', 'unavailable')}\n")
            f.write(f"- 市场日期候选：{result.get('businessDate', 'unavailable')}\n")
            f.write(f"- 已公布交易日历判断：{result.get('calendarStatus', 'unavailable')}\n")
            f.write("- 仅核对 2026 年官方常规休市/提前收盘公告；并未实时确认临时休市及源数据就绪。\n")
            f.write("- **真实派发、任务登记写入、账本更新及自动补算均禁止**\n")
            f.write("- 本次工作流完成后退出，不启动后台监控或定时复核。\n")
    # Exit successfully for a deliberately blocked audit, with explicit status.
    return 0


if __name__ == "__main__":
    sys.exit(main())
