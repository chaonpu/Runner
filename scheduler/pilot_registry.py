"""Durable, side-effect-free Stock dispatch pilot.

Writes ONLY public-safe metadata in chaonpu/Runner/state/pilot_runs.json.
The only permitted external POST is Stock/runner-dispatch-canary.yml.
Does not call A/US strategy, touch ZIWUXILV/database, or publish an APK.

Reserve -> dispatch -> record returned run ID; uncertain network responses
remain reserved/unknown and MUST NOT be blindly retried. The GitHub Contents
API SHA precondition rejects competing writes; workflow concurrency serializes
normal pilot runs.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from copy import deepcopy

from scheduler.canary_dispatch import run_canary

API="https://api.github.com"
REGISTRY="/repos/chaonpu/Runner/contents/state/pilot_runs.json"
KEY=re.compile(r"^pilot:stock:(\d{4}-\d{2}-\d{2})$")
ALLOWED={"id", "repo", "workflow", "status", "coordinatorRunId",
         "runId", "createdAt", "updatedAt", "canary"}
STATES={"reserved", "dispatched", "accepted_without_run_id", "dispatch_uncertain",
        "permission_denied", "unknown_api_error", "completed_success",
        "completed_failure", "reconciliation_required"}
EXPECTED_REPO="chaonpu/Stock"
EXPECTED_WORKFLOW="runner-dispatch-canary.yml"

class PilotGuard(ValueError):
    pass

def validated_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise PilotGuard("invalid_date")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise PilotGuard("invalid_calendar_date") from exc
    return value

def key_for(date):
    return "pilot:stock:" + validated_date(date)

def validate_registry(state):
    if not isinstance(state, dict) or state.get("schema")!=1 or not isinstance(state.get("tasks"), list):
        raise PilotGuard("invalid_registry_schema")
    seen=set()
    for e in state["tasks"]:
        if not isinstance(e, dict) or not set(e).issubset(ALLOWED):
            raise PilotGuard("invalid_registry_fields")
        key=e.get("id")
        if not isinstance(key,str) or not KEY.fullmatch(key) or key in seen:
            raise PilotGuard("invalid_or_duplicate_key")
        seen.add(key)
        if e.get("repo")!=EXPECTED_REPO or e.get("workflow")!=EXPECTED_WORKFLOW:
            raise PilotGuard("invalid_public_registry_target")
        if e.get("status") not in STATES:
            raise PilotGuard("invalid_registry_state")
        if type(e.get("coordinatorRunId")) is not int or e["coordinatorRunId"]<=0:
            raise PilotGuard("invalid_source_run_id")
        rid=e.get("runId")
        if rid is not None and (type(rid) is not int or rid<=0):
            raise PilotGuard("invalid_private_canary_run_id")
        if not isinstance(e.get("createdAt"),str):
            raise PilotGuard("missing_creation_time")
        if e.get("canary")!=f"runner-canary-{e['coordinatorRunId']}-stock":
            raise PilotGuard("canary_identity_mismatch")
    return state

def reserve(state,date,source_run_id,now):
    state=deepcopy(validate_registry(state))
    name=key_for(date)
    if any(x["id"]==name for x in state["tasks"]):
        raise PilotGuard("pilot_already_reserved_never_redispatch")
    if type(source_run_id) is not int or source_run_id<=0:
        raise PilotGuard("invalid_coordinator_run_id")
    state["tasks"].append({
        "id":name, "repo":EXPECTED_REPO,"workflow":EXPECTED_WORKFLOW,
        "status":"reserved","coordinatorRunId":source_run_id,
        "createdAt":now,"updatedAt":now,
        "canary":f"runner-canary-{source_run_id}-stock",
    })
    return validate_registry(state)

def set_dispatch_result(state,date,source_run_id,result,now):
    state=deepcopy(validate_registry(state))
    name=key_for(date)
    item=next((x for x in state["tasks"] if x["id"]==name),None)
    if item is None or item["coordinatorRunId"]!=source_run_id or item["status"]!="reserved":
        raise PilotGuard("pilot_reservation_mismatch")
    if result.get("dispatched") is True:
        if type(result.get("runId")) is int and result["runId"]>0:
            item["status"]="dispatched"
            item["runId"]=result["runId"]
        else:
            item["status"]="accepted_without_run_id"
    elif result.get("dispatched")=="unknown":
        item["status"]="dispatch_uncertain"
    elif result.get("status")=="permission_denied":
        item["status"]="permission_denied"
    else:
        item["status"]="unknown_api_error"
    item["updatedAt"]=now
    return validate_registry(state)

def request(url,method,token,data=None):
    if not token:
        raise PilotGuard("registry_write_token_missing")
    headers={
        "Authorization":"Bearer "+token,
        "Accept":"application/vnd.github+json",
        "X-GitHub-Api-Version":"2026-03-10",
        "Content-Type":"application/json",
        "User-Agent":"ziwuxilv-public-pilot-registry/1.0",
    }
    req=urllib.request.Request(API+url, data=(json.dumps(data).encode("utf-8") if data is not None else None),
                                headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=20) as response:
            return json.load(response)
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,OSError) as exc:
        raise PilotGuard("github_registry_write_or_read_unconfirmed") from exc

def load_state(token):
    item=request(REGISTRY+"?ref=main","GET",token)
    if item.get("type")!="file" or item.get("encoding")!="base64":
        raise PilotGuard("registry_metadata_invalid")
    try:
        registry=json.loads(base64.b64decode(item["content"]))
    except (ValueError,KeyError,TypeError) as exc:
        raise PilotGuard("registry_bytes_invalid") from exc
    sha=item.get("sha")
    if not isinstance(sha,str) or not re.fullmatch(r"[0-9a-f]{40}",sha):
        raise PilotGuard("registry_sha_missing")
    return validate_registry(registry),sha

def save_state(token,state,sha,commit_msg):
    if not re.fullmatch(r"[0-9a-f]{40}",sha) or not re.fullmatch(r"[A-Za-z0-9 ():-]{1,90}",commit_msg):
        raise PilotGuard("invalid_registry_write_request")
    validate_registry(state)
    body={"message":commit_msg,
          "content":base64.b64encode((json.dumps(state,sort_keys=True,indent=2)+"\n").encode("utf-8")).decode("ascii"),
          "sha":sha,"branch":"main"}
    result=request(REGISTRY,"PUT",token,body)
    if not isinstance(result,dict) or not isinstance(result.get("content"),dict):
        raise PilotGuard("registry_commit_unconfirmed")
    return result

def perform(date,env):
    if env.get("GITHUB_REPOSITORY")!="chaonpu/Runner" or env.get("GITHUB_REF")!="refs/heads/main":
        raise PilotGuard("untrusted_execution_context")
    if env.get("GITHUB_EVENT_NAME")!="workflow_dispatch":
        raise PilotGuard("manual_pilot_only")
    if env.get("GITHUB_ACTOR")!="chaonpu":
        raise PilotGuard("actor_not_allowed")
    run=env.get("GITHUB_RUN_ID","")
    if not re.fullmatch(r"[1-9]\d{7,19}",run):
        raise PilotGuard("source_run_id_unavailable")
    registry_token=env.get("GH_TOKEN","")
    action_token=env.get("CHAONPU_ACTIONS_READ_TOKEN","")
    if not registry_token or not action_token:
        raise PilotGuard("pilot_tokens_missing")
    name=key_for(date)
    state,sha=load_state(registry_token)
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    reserved=reserve(state,date,int(run),now)
    save_state(registry_token,reserved,sha,"Reserve safe Stock pilot "+date)
    # If dispatch API times out after receiving the request, the reservation
    # remains. Another run cannot dispatch the same date without manual review.
    result=run_canary("stock",run,env)
    state2,sha2=load_state(registry_token)
    completed=set_dispatch_result(state2,date,int(run),result,
                                  dt.datetime.now(dt.timezone.utc).isoformat())
    save_state(registry_token,completed,sha2,"Record Stock pilot "+date)
    entry=next(x for x in completed["tasks"] if x["id"]==name)
    return {"pilotId":name,"state":entry["status"],"runId":entry.get("runId"),
            "target":"Stock no-op canary only","strategyExecuted":False}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--date",required=True)
    args=parser.parse_args()
    try:
        result=perform(args.date,os.environ)
        print(json.dumps(result,sort_keys=True))
        return 0 if result["state"]=="dispatched" else 2
    except (PilotGuard,ValueError,TypeError,KeyError) as exc:
        # Do not leak exception traces or tokens in PUBLIC Runner logs.
        code=str(exc) if isinstance(exc,PilotGuard) else "pilot_unexpected_state"
        print(json.dumps({"status":"blocked","reason":code,"strategyExecuted":False}))
        return 2

if __name__=="__main__":
    sys.exit(main())
