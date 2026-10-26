"""Execute one explicitly requested task, monitor in foreground, attest, close permit."""
import argparse
from datetime import datetime, timezone
import json
import os
import time
from zoneinfo import ZoneInfo

from scheduler.exchange_schedule import evaluate
from scheduler.calendar_runtime import pin_latest
from pathlib import Path
from scheduler.production_ports import ProductionPorts, ReadOnlyPorts, PRIVATE, need
from scheduler.production_evidence import WORKFLOWS as READY_WORKFLOWS, CLOUD_WORKFLOWS, inspect
from scheduler.policies import hosted_capacity
from scheduler.production_lease import Lease
from scheduler.production_registry import RegistryGuard
from scheduler.production_session import start
from scheduler.production_watch import watch
from scheduler.probes import existing_attempt, runner_for_repo


def prepare(ports, cfg, task, day, mode, state):
    spec, account = cfg["tasks"][task], cfg["accounts"]["chaonpu"]
    for workflow in ("run-a-share.yml", "run-us.yml"):
        need(existing_attempt("chaonpu/Stock", workflow, account, ports.env)["status"] == "no_active_run",
             "existing_business_run_or_unknown")
    # First resolve the same verified allowance policy used for business execution.
    # Cloud readiness must never depend on a Windows runner being online.
    billing = ports.bill("chaonpu", account, ports.env)
    capacity = hosted_capacity(billing, spec, now=ports.clock())
    if capacity == "available":
        workflow, route = CLOUD_WORKFLOWS[task], "cloud"
    elif capacity in ("insufficient", "exhausted"):
        runner = runner_for_repo("chaonpu/Stock", spec["runnerName"], account, ports.env)
        need(runner.get("status") == "online" and runner.get("busy") is False
             and runner.get("labelsVerified") is True, "private_readiness_runner_unavailable")
        workflow, route = READY_WORKFLOWS[task], "windows"
    else:
        raise RegistryGuard("hosted_allowance_unverified_no_readiness_dispatch")
    # The public registry contains only source identities from confirmed runs.
    # A code/requirements/rules change forces the original FULL isolation
    # check; otherwise the daily selector only executes in the effect job.
    verified = sorted((row for row in state["tasks"]
                       if row.get("task") == task and row.get("status") == "verified"
                       and isinstance(row.get("id"), str)
                       and row["id"] < task + ":" + day), key=lambda row: row["id"])
    prior_sha = verified[-1].get("sourceSha", "") if verified else ""
    body = {"ref":"main","return_run_details":True,"inputs":{
        "business_date":day,"publication_mode":mode,
        "readiness_profile":"auto", "previous_verified_sha":prior_sha,
    }}
    if ports.env.get("ZIWU_CALENDAR_REVISION"):
        body["inputs"]["calendar_revision"] = ports.env["ZIWU_CALENDAR_REVISION"]
    response = ports.requester(ports.env["CHAONPU_ACTIONS_READ_TOKEN"], "POST",
        PRIVATE+"/actions/workflows/"+workflow+"/dispatches", body)
    rid = response.get("workflow_run_id")
    need(type(rid) is int and rid > 0, "readiness_dispatch_unknown_do_not_repeat")
    print(json.dumps({"phase":"private_readiness_started","runId":rid,"taskId":task+":"+day,"route":route}),flush=True)
    deadline = time.monotonic()+14400
    while time.monotonic() < deadline:
        run = ports.run("chaonpu/Stock",rid)
        need(run.get("event") == "workflow_dispatch" and run.get("head_branch") == "main"
             and run.get("run_attempt") == 1
             and run.get("path","").split("@")[0] == ".github/workflows/"+workflow,
             "readiness_run_origin_mismatch")
        if run.get("status") == "completed":
            need(run.get("conclusion") == "success", "private_readiness_failed")
            proof = inspect(ports.private_reader(),task,day,datetime.now(timezone.utc),mode,
                            expected_run_id=rid,expected_workflow=workflow,
                            expected_calendar_revision=ports.env.get("ZIWU_CALENDAR_REVISION") or None)
            need((proof["marketDataReady"] is True
                  or proof.get("sourceSamplesReady") is True
                  or proof.get("alreadyCurrent") is True)
                 and proof["sourceSha"] == run["head_sha"], "fresh_private_readiness_not_verified")
            return {**proof, "readinessRunId": rid, "readinessRoute": route}
        time.sleep(30)
    raise RegistryGuard("private_readiness_deadline")


def manual_task_context(env, task):
    expected = {"stock_a": "production-dispatch.yml", "stock_us": "stock-us.yml"}
    need(env.get("GITHUB_WORKFLOW_REF") ==
         "chaonpu/Runner/.github/workflows/" + expected[task] + "@refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and env.get("GITHUB_ACTOR") == "chaonpu"
         and env.get("GITHUB_REF") == "refs/heads/main", "manual_market_context_mismatch")


def resolve_request(task, entered="", mode="auto", *, now=None):
    """Choose the native engine's latest closed session and publication window."""
    instant = datetime.now(timezone.utc) if now is None else now
    schedule = evaluate(task, entered, now=instant)
    day = schedule.get("businessDate")
    need(isinstance(day, str) and schedule.get("calendarCoverageCurrent") is True
         and schedule.get("scheduledSessionClosed") is True
         and schedule.get("publishedScheduleMatched") is True, "business_date_unavailable")
    # Native strategy inputs describe the latest session, never an arbitrary
    # historical universe. An internal explicit override must match that day.
    need(evaluate(task, now=instant)["businessDate"] == day,
         "native_historical_decision_replay_unsupported")
    local_day = instant.astimezone(ZoneInfo(schedule["marketTimezone"])).date().isoformat()
    automatic = "formal" if local_day == day else "theoretical_catchup"
    selected = automatic if mode == "auto" else mode
    need(selected == automatic, "native_publication_window_not_open")
    return {"businessDate": day, "publicationMode": selected,
            "dateMode": schedule["dateMode"], "marketTimezone": schedule["marketTimezone"],
            "calendarRevision": schedule.get("calendarRevision")}


def report_plan(env, task, plan, execute):
    print(json.dumps({"phase": "automatic_plan", "task": task, **plan,
                      "execute": execute, "businessVerified": False}), flush=True)
    if env.get("GITHUB_STEP_SUMMARY"):
        label = "正式运行" if plan["publicationMode"] == "formal" else "理论补算（无正式邮件）"
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("### 自动选择运行日期\n\n" + task + " / " + plan["businessDate"] +
                         " / " + label + "\n\n按市场时区与交易所日历选择最近已收盘交易日。" +
                         ("本次执行。" if execute else "本次仅预检，不执行策略。") + "\n")


def skip_result(env, task, day, reason, **extra):
    result = {"status": "skipped", "reason": reason, "taskId": task + ":" + day,
              "dispatches": 0, "databaseWrites": 0, "emailsSent": 0,
              "businessVerified": False, **extra}
    print(json.dumps(result, sort_keys=True), flush=True)
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("### 已有结果，已跳过\n\n" + task + " / " + day +
                         " 已有核验记录或权威数据，无需重复计算、调仓或入账。本次未生成新的正式报告。\n")
    return result


def execute(env, task, day, mode):
    manual_task_context(env, task)
    env = dict(env, REQUEST_TASK=task, REQUEST_BUSINESS_DATE=env.get("ENTERED_BUSINESS_DATE", ""),
               REQUEST_PUBLICATION_MODE=mode)
    ports = ProductionPorts(env)
    config,state,_ = ports.load()
    registered = [r for r in state["tasks"] if r["id"] == task+":"+day]
    if registered and len(registered) == 1 and registered[0].get("status") == "hold":
        proof = ports.retire_prebusiness_hold(task, day)
        print(json.dumps({"phase": "bounded_prebusiness_recovery", **proof}, sort_keys=True), flush=True)
        config, state, _ = ports.load()
        registered = [r for r in state["tasks"] if r["id"] == task + ":" + day]
        need(not registered, "prebusiness_recovery_registry_still_active")
    if registered:
        need(len(registered) == 1 and registered[0].get("status") == "verified",
             "task_already_registered_no_redispatch")
        return skip_result(env, task, day, "previously_verified_task_no_redispatch",
                           priorRunVerified=True, priorRunId=registered[0].get("runId"),
                           existingPublicationMode=registered[0].get("publicationMode", "formal"),
                           completionMode=registered[0].get("completionMode", "original_run"))
    need(config.get('dispatchEnabled') is False and 'activeLease' not in config,
         'another_production_permit_is_active')
    schedule=evaluate(task,day)
    today=datetime.now(timezone.utc).astimezone(ZoneInfo(schedule['marketTimezone'])).date().isoformat()
    need((mode=='formal' and today==day) or (mode=='theoretical_catchup' and today>day),
         'native_publication_window_not_open')
    prepared = prepare(ports,config,task,day,mode,state)
    if prepared.get("alreadyCurrent") is True:
        return skip_result(env, task, day, "authoritative_market_already_current",
                           readinessRunId=prepared["readinessRunId"], authoritativeStateChecked=True)
    lease = Lease(ports)
    try:
        lease.arm(task,day,mode)
        session = start(ports,task,day,env)
        print(json.dumps({"phase":"business_started","taskId":session["taskId"],"runId":session["runId"],
                          "publicationMode":mode}),flush=True)
        watched = watch(ports,session,env,deadline_seconds=14400)
        need(watched["status"] == "awaiting_effect_verification", "business_run_failed_or_unverified")
        result = ports.finish(session)
        # Timing is diagnostic only. An Actions API hiccup must not change the
        # verified ledger result or cause a second business execution.
        if task in ("stock_a", "stock_us"):
            try:
                from scheduler.production_timing import collect, publish_summary
                measured = collect(ports, prepared["readinessRunId"], session["runId"])
                publish_summary(measured, prepared["readinessRunId"], session["runId"], env, task=task)
            except (OSError, TypeError, ValueError, KeyError, AttributeError):
                print(json.dumps({"phase":"measured_action_step_timings",
                                  "status":"unavailable_no_business_effect"}), flush=True)
        print(json.dumps(result,sort_keys=True),flush=True)
        return result
    finally:
        lease.release()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", choices=("stock_a","stock_us"),required=True)
    p.add_argument("--business-date",default="")
    p.add_argument("--publication-mode",choices=("auto","formal","theoretical_catchup"),default="auto")
    p.add_argument("--execute",action="store_true")
    a=p.parse_args()
    manual_task_context(dict(os.environ), a.task)
    try:
        pin_latest(Path(os.environ.get("RUNNER_TEMP", ".")) / "ziwuxilv-calendar" / "snapshot.json")
        plan=resolve_request(a.task,a.business_date,a.publication_mode)
        report_plan(os.environ,a.task,plan,a.execute)
        day,mode=plan["businessDate"],plan["publicationMode"]
        if not a.execute:
            from scheduler.production_ports import public_report
            env=dict(os.environ,REQUEST_TASK=a.task,REQUEST_BUSINESS_DATE=day,REQUEST_PUBLICATION_MODE=mode)
            ports=ReadOnlyPorts(env)
            cfg,state,_=ports.load()
            # Preview the same publication mode that execution would permit.
            from copy import deepcopy
            cfg=deepcopy(cfg)
            cfg["tasks"][a.task]["publicationMode"]=mode
            print(json.dumps({**public_report(cfg,state,ports.audit(cfg,state,a.task,day)),**plan},sort_keys=True))
            return 0
        env=dict(os.environ,ENTERED_BUSINESS_DATE=a.business_date)
        execute(env,a.task,day,mode)
        return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,RegistryGuard) else "coordinator_stopped_manual_review"
        print(json.dumps({"status":"hold","reason":code,"safeToRetry":False,"businessVerified":False}),flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

