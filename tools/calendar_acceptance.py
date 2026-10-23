"""Dispatch only existing private validation workflows; no strategy/sign/publish.

Public logs contain immutable source identities and Actions results only.
Never fetch private source, artifacts, holdings or workflow logs.
"""
import argparse,json,os,re,time,urllib.request
from pathlib import Path

TARGETS={
 'app':('anwpustudent-lab/Test','test-self-hosted.yml','TEST_ACTIONS_READ_TOKEN',{'live_smoke':True}),
 'stock':('chaonpu/Stock','production-readiness-a.yml','CHAONPU_ACTIONS_READ_TOKEN',{'business_date':'2026-10-22','calendar_only':True,'readiness_profile':'full'}),
}

def request(token,method,url,body=None):
    req=urllib.request.Request(url,method=method,headers={'Authorization':'Bearer '+token,
        'Accept':'application/vnd.github+json','User-Agent':'ziwuxilv-calendar-acceptance/1'},
        data=None if body is None else json.dumps(body).encode())
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise ValueError('unexpected_github_redirect')
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=25) as response:
        raw=response.read(1_000_001)
    if len(raw)>1_000_000:raise ValueError('response_oversized')
    return json.loads(raw) if raw else {}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('target',choices=TARGETS);args=parser.parse_args()
    repo,workflow,secret,inputs=TARGETS[args.target]
    token=os.environ.get(secret,'')
    if not token:raise SystemExit('Existing validation token is unavailable')
    config=json.loads(Path('config/calendar-acceptance.json').read_text())
    if args.target=='stock':
        inputs={**inputs,'calendar_revision':config['calendarRevision']}
    expected=config[args.target+'SourceSha']
    if not re.fullmatch('[0-9a-f]{40}',expected):raise ValueError('invalid_source_sha')
    base='https://api.github.com/repos/'+repo
    # One dispatch only. An ambiguous response is not retried.
    reuse=config.get(args.target+'ValidationRunId')
    if reuse is not None:
        if type(reuse) is not int or reuse<=0:raise ValueError('invalid_original_validation_run')
        rid=reuse
    else:
        result=request(token,'POST',base+'/actions/workflows/'+workflow+'/dispatches',
                       {'ref':'main','return_run_details':True,'inputs':inputs})
        rid=result.get('workflow_run_id')
    if type(rid) is not int:raise ValueError('dispatch_run_identity_uncertain_no_retry')
    print(json.dumps({'target':args.target,'runId':rid,'sourceSha':expected,'readOnlyValidation':True}),flush=True)
    deadline=time.monotonic()+3600
    while time.monotonic()<deadline:
        run=request(token,'GET',base+'/actions/runs/'+str(rid))
        if (run.get('head_sha')!=expected or run.get('event')!='workflow_dispatch'
                or run.get('head_branch')!='main' or run.get('run_attempt')!=1
                or run.get('path','').split('@')[0]!='.github/workflows/'+workflow):
            raise ValueError('private_validation_source_or_workflow_mismatch')
        if run.get('status')=='completed':
            if run.get('conclusion')=='success':
                jobs=request(token,'GET',base+'/actions/runs/'+str(rid)+'/jobs?filter=latest&per_page=100')
                if jobs.get('total_count')!=len(jobs.get('jobs',[])) or not jobs.get('jobs'):raise ValueError('validation_jobs_incomplete')
                steps=[s for j in jobs['jobs'] for s in j.get('steps',[])]
                def passed(name):
                    matched=[s for s in steps if s.get('name')==name]
                    return len(matched)==1 and matched[0].get('status')=='completed' and matched[0].get('conclusion')=='success'
                if args.target=='app':
                    if not passed('Verify actual public calendar network execution') or not passed('Verify live chart execution evidence'):
                        raise ValueError('actual_calendar_or_chart_test_not_verified')
                else:
                    if not passed('Validate both markets against shared pinned calendar') or not passed('Attest calendar snapshot '+config['calendarRevision']):
                        raise ValueError('private_calendar_revision_or_comparisons_not_verified')
                    business=[s for s in steps if s.get('name','').startswith('Check authoritative inputs, complete native selection')]
                    if len(business)!=1 or business[0].get('conclusion')!='skipped':raise ValueError('calendar_only_validation_boundary_not_verified')
            public={'target':args.target,'runId':rid,'sourceSha':expected,'result':run.get('conclusion'),
                    'url':run.get('html_url'),'strategyExecutions':0,'databaseWrites':0,'reusedOriginalRun':reuse is not None}
            print(json.dumps(public),flush=True)
            if os.environ.get('GITHUB_STEP_SUMMARY'):
                with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:stream.write('### '+args.target+'日历接入验收\n\n'+json.dumps(public)+'\n')
            if run.get('conclusion')!='success':raise SystemExit(2)
            return
        time.sleep(30)
    raise SystemExit('Private validation is still running; inspect the original run, do not dispatch again')

if __name__=='__main__':main()
