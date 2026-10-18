"""One reviewed snapshot of retired private workflows; no source or artifact reads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

PLAN = 'config/retired-workflow-cleanup.json'
REPORT = 'state/remaining-history-cleanup-supplement.json'
OWNER = 'chaonpu/Runner'
KEEP = {
    'chaonpu/Stock': {'run-a-share.yml','run-us.yml','production-readiness-a.yml',
        'production-readiness-us.yml','production-readiness-cloud-a.yml','production-readiness-cloud-us.yml'},
    'anwpustudent-lab/Test': {'test-self-hosted.yml','android-cloud-verified.yml'},
    'chaonpu/ziwuxilv-android': {'windows-signing-verified-handoff.yml','cloud-signing-verified-handoff.yml'},
}

class Stop(Exception): pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None

def need(value, reason):
    if not value: raise Stop(reason)

def validate(plan):
    need(set(plan)=={'schemaVersion','approval','repositories'} and plan['schemaVersion']==1
         and plan['approval']=='DELETE-RETIRED-WORKFLOW-HISTORY-20261018', 'invalid_plan')
    need(isinstance(plan['repositories'],list) and len(plan['repositories'])==2, 'invalid_repositories')
    repos, ids = set(), set()
    total = 0
    for item in plan['repositories']:
        need(set(item)=={'repository','retiredWorkflows','runs'}, 'invalid_repository_plan')
        repo=item['repository']
        need(repo in ('chaonpu/Stock','anwpustudent-lab/Test') and repo not in repos, 'unapproved_repository')
        repos.add(repo)
        paths=item['retiredWorkflows']
        need(isinstance(paths,list) and len(paths)==len(set(paths)), 'duplicate_paths')
        for path in paths:
            need(bool(re.fullmatch(r'\.github/workflows/[a-z0-9][a-z0-9.-]*\.yml',path))
                 and path.rsplit('/',1)[-1] not in KEEP[repo], 'active_or_invalid_workflow')
        need(len(paths)=={'chaonpu/Stock':2,'anwpustudent-lab/Test':45}[repo],
             'retired_workflow_scope_changed')
        for run in item['runs']:
            need(set(run)=={'id','path','workflowId'} and type(run['id']) is int and run['id']>0
                 and type(run['workflowId']) is int and run['workflowId']>0
                 and run['path'] in paths and run['id'] not in ids, 'invalid_or_duplicate_run')
            ids.add(run['id']); total+=1
    need(total==126,'reviewed_snapshot_count_changed')
    return total

def context(env, raw):
    need(env.get('GITHUB_REPOSITORY')==OWNER and env.get('GITHUB_ACTOR')=='chaonpu'
         and env.get('GITHUB_EVENT_NAME')=='push' and env.get('GITHUB_REF')=='refs/heads/main'
         and env.get('GITHUB_WORKFLOW_REF')==OWNER+'/.github/workflows/android-production.yml@refs/heads/main',
         'untrusted_cleanup_context')
    need(hashlib.sha256(raw).hexdigest()==env.get('CLEANUP_PLAN_SHA256'), 'plan_hash_mismatch')
    before, after=env.get('GITHUB_EVENT_BEFORE',''),env.get('GITHUB_SHA','')
    need(all(re.fullmatch('[0-9a-f]{40}',x) for x in (before,after)), 'invalid_commit_range')
    changed=subprocess.check_output(['git','diff','--name-only','--no-renames',before,after],text=True).splitlines()
    need(changed==[PLAN], 'cleanup_requires_plan_only_commit')

def api(token, repo, path, method='GET', body=None):
    private=repo in KEEP and re.fullmatch(r'/actions/runs/[1-9][0-9]*',path)
    listing=repo in KEEP and re.fullmatch(r'/actions/runs\?per_page=100&page=[1-9][0-9]*',path)
    report=repo==OWNER and path=='/contents/'+REPORT
    need((private and method in ('GET','DELETE')) or (listing and method=='GET')
         or (report and method in ('GET','PUT')), 'endpoint_forbidden')
    need(bool(token), 'credential_missing')
    for attempt in range(5):
        request=urllib.request.Request('https://api.github.com/repos/'+repo+path,method=method,
            data=None if body is None else json.dumps(body).encode(),headers={
            'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
            'X-GitHub-Api-Version':'2026-03-10','User-Agent':'reviewed-retired-workflow-cleanup'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=30) as response:
                data=response.read(4_000_001)
                need(len(data)<=4_000_000, 'response_too_large')
                if method=='DELETE': need(response.status==204,'delete_unconfirmed')
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as error:
            if error.code==404: return None
            if error.code in (403,429,502,503) and attempt<4:
                if error.headers.get('X-RateLimit-Remaining')=='0':
                    delay=max(1,int(error.headers.get('X-RateLimit-Reset','0'))-int(time.time())+2)
                    need(delay<=3600,'rate_reset_unverified')
                else:
                    delay=max(15,min(120,int(error.headers.get('Retry-After','60'))))
                print(json.dumps({'phase':'rate_or_service_backoff','repository':repo,'seconds':delay}),flush=True)
                time.sleep(delay); continue
            raise Stop('github_http_'+str(error.code)) from None
        except (urllib.error.URLError,TimeoutError,OSError):
            if attempt==4: raise Stop('github_transport_unavailable') from None
            # GET and deletion of the same immutable Run ID are idempotent.
            time.sleep(5*(attempt+1))
    raise Stop('retry_limit')

def persist(env, report):
    import base64
    current=api(env['GH_TOKEN'],OWNER,'/contents/'+REPORT)
    body={'branch':'main','message':'Record reviewed retired workflow history cleanup progress',
          'content':base64.b64encode((json.dumps(report,indent=2)+'\n').encode()).decode()}
    if current: body['sha']=current['sha']
    result=api(env['GH_TOKEN'],OWNER,'/contents/'+REPORT,'PUT',body)
    need(result and result.get('commit',{}).get('sha'),'report_write_unconfirmed')

def cleanup(env, plan):
    report={'schemaVersion':1,'runId':int(env['GITHUB_RUN_ID']),'sourceCommit':env['GITHUB_SHA'],
            'status':'running','requestedRuns':validate(plan),'repositories':[]}
    persist(env,report)
    try:
        for item in plan['repositories']:
            repo=item['repository']
            token=env.get('TEST_ACTIONS_READ_TOKEN' if repo.startswith('anwpustudent-lab/') else 'CHAONPU_ACTIONS_READ_TOKEN','')
            row={'repository':repo,'requested':len(item['runs']),'deleted':0,'alreadyAbsent':0,'blocked':[],'verifiedAbsent':False}
            report['repositories'].append(row);persist(env,report)
            for number, expected in enumerate(item['runs'],1):
                path='/actions/runs/'+str(expected['id'])
                run=api(token,repo,path)
                if run is None:
                    row['alreadyAbsent']+=1
                else:
                    need(run.get('id')==expected['id'] and run.get('repository',{}).get('full_name')==repo
                         and run.get('path','').split('@')[0]==expected['path']
                         and run.get('workflow_id')==expected['workflowId'], 'run_identity_mismatch')
                    if run.get('status')!='completed':
                        row['blocked'].append(expected['id'])
                    else:
                        api(token,repo,path,'DELETE');row['deleted']+=1
                        time.sleep(0.5)
                if number%25==0:
                    print(json.dumps({'phase':'history_cleanup_progress',**row}),flush=True)
                    persist(env,report)
            remaining=set(); page=1
            while True:
                data=api(token,repo,'/actions/runs?per_page=100&page='+str(page))
                need(data and isinstance(data.get('workflow_runs'),list),'verification_listing_invalid')
                remaining.update(run['id'] for run in data['workflow_runs'])
                if len(data['workflow_runs'])<100: break
                page+=1;need(page<=100,'verification_page_limit')
            matched=remaining.intersection(r['id'] for r in item['runs'])
            row['verifiedAbsent']=not matched
            row['remainingSelectedRunIds']=sorted(matched)
            persist(env,report)
            need(not matched,'selected_runs_still_present')
        report['status']='completed'
    except Exception as error:
        report['status']='incomplete'
        report['reason']=str(error) if isinstance(error,Stop) else 'cleanup_stopped'
        raise
    finally:
        persist(env,report)
        print(json.dumps(report),flush=True)

def main():
    args=argparse.ArgumentParser();args.add_argument('--execute',action='store_true');options=args.parse_args()
    raw=Path(PLAN).read_bytes();plan=json.loads(raw);total=validate(plan)
    if not options.execute:
        print(json.dumps({'mode':'read_only','requestedRuns':total}));return
    context(os.environ,raw);cleanup(os.environ,plan)

if __name__=='__main__': main()
