"""Execute one explicitly requested task, monitor in foreground, attest, close permit."""
import argparse
from datetime import datetime, timezone
import json
import os
import time
from zoneinfo import ZoneInfo

from scheduler.exchange_schedule import evaluate
from scheduler.production_ports import ProductionPorts, ReadOnlyPorts, PRIVATE, need
from scheduler.production_evidence import WORKFLOWS as READY_WORKFLOWS, inspect
from scheduler.production_lease import Lease
from scheduler.production_registry import RegistryGuard
from scheduler.production_session import start
from scheduler.production_watch import watch
from scheduler.probes import existing_attempt, runner_for_repo


def prepare(ports, cfg, task, day, mode):
    spec, account = cfg["tasks"][task], cfg["accounts"]["chaonpu"]
    for workflow in ("run-a-share.yml", "run-us.yml"):
        need(existing_attempt("chaonpu/Stock", workflow, account, ports.env)["status"] == "no_active_run",
             "existing_business_run_or_unknown")
    # Private readiness itself needs Windows; cloud business routing is decided
    # separately using verified allowance after the proof is generated.
    runner = runner_for_repo("chaonpu/Stock", spec["runnerName"], account, ports.env)
    need(runner.get("status") == "online" and runner.get("busy") is False
         and runner.get("labelsVerified") is True, "private_readiness_runner_unavailable")
    body = {"ref":"main","return_run_details":True,"inputs":{"business_date":day,"publication_mode":mode}}
    response = ports.requester(ports.env["CHAONPU_ACTIONS_READ_TOKEN"], "POST",
        PRIVATE+"/actions/workflows/"+READY_WORKFLOWS[task]+"/dispatches", body)
    rid = response.get("workflow_run_id")
    need(type(rid) is int and rid > 0, "readiness_dispatch_unknown_do_not_repeat")
    print(json.dumps({"phase":"private_readiness_started","runId":rid,"taskId":task+":"+day}),flush=True)
    deadline = time.monotonic()+14400
    while time.monotonic() < deadline:
        run = ports.run("chaonpu/Stock",rid)
        need(run.get("event") == "workflow_dispatch" and run.get("head_branch") == "main"
             and run.get("run_attempt") == 1
             and run.get("path","").split("@")[0] == ".github/workflows/"+READY_WORKFLOWS[task],
             "readiness_run_origin_mismatch")
        if run.get("status") == "completed":
            need(run.get("conclusion") == "success", "private_readiness_failed")
            proof = inspect(ports.private_reader(),task,day,datetime.now(timezone.utc),mode)
            need(proof["marketDataReady"] is True and proof["sourceSha"] == run["head_sha"],
                 "fresh_private_readiness_not_verified")
            return
        time.sleep(30)
    raise RegistryGuard("private_readiness_deadline")


def manual_task_context(env, task):
    expected = {"stock_a": "production-dispatch.yml", "stock_us": "stock-us.yml"}
    need(env.get("GITHUB_WORKFLOW_REF") ==
         "chaonpu/Runner/.github/workflows/" + expected[task] + "@refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
         and env.get("GITHUB_ACTOR") == "chaonpu"
         and env.get("GITHUB_REF") == "refs/heads/main", "manual_market_context_mismatch")


def execute(env, task, day, mode):
    manual_task_context(env, task)
    env = dict(env, REQUEST_TASK=task, REQUEST_BUSINESS_DATE=env.get("ENTERED_BUSINESS_DATE", ""),
               REQUEST_PUBLICATION_MODE=mode)
    ports = ProductionPorts(env)
    config,state,_ = ports.load()
    need(not any(r["id"] == task+":"+day for r in state["tasks"]), "task_already_registered_no_redispatch")
    need(config.get('dispatchEnabled') is False and 'activeLease' not in config,
         'another_production_permit_is_active')
    schedule=evaluate(task,day)
    today=datetime.now(timezone.utc).astimezone(ZoneInfo(schedule['marketTimezone'])).date().isoformat()
    need((mode=='formal' and today==day) or (mode=='theoretical_catchup' and today>day),
         'native_publication_window_not_open')
    prepare(ports,config,task,day,mode)
    lease = Lease(ports)
    try:
        lease.arm(task,day,mode)
        session = start(ports,task,day,env)
        print(json.dumps({"phase":"business_started","taskId":session["taskId"],"runId":session["runId"],
                          "publicationMode":mode}),flush=True)
        watched = watch(ports,session,env,deadline_seconds=14400)
        need(watched["status"] == "awaiting_effect_verification", "business_run_failed_or_unverified")
        result = ports.finish(session)
        print(json.dumps(result,sort_keys=True),flush=True)
        return result
    finally:
        lease.release()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", choices=("stock_a","stock_us"),required=True)
    p.add_argument("--business-date",default="")
    p.add_argument("--publication-mode",choices=("formal","theoretical_catchup"),default="formal")
    p.add_argument("--execute",action="store_true")
    a=p.parse_args()
    manual_task_context(dict(os.environ), a.task)
    if not a.execute:
        from scheduler.production_ports import public_report
        ports=ReadOnlyPorts(os.environ)
        cfg,state,_=ports.load()
        day=evaluate(a.task,a.business_date)["businessDate"]
        print(json.dumps(public_report(cfg,state,ports.audit(cfg,state,a.task,day)),sort_keys=True))
        return 0
    try:
        day=evaluate(a.task,a.business_date)["businessDate"]
        need(isinstance(day,str),"business_date_unavailable")
        env=dict(os.environ,ENTERED_BUSINESS_DATE=a.business_date)
        execute(env,a.task,day,a.publication_mode)
        return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,RegistryGuard) else "coordinator_stopped_manual_review"
        print(json.dumps({"status":"hold","reason":code,"safeToRetry":False,"businessVerified":False}),flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
