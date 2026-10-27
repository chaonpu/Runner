"""Manual reconciliation of an original computed commit's explicit recovery.

Pure metadata verifier, no dispatch, retry, private data access or remote write.
The original failed run, coordinator and source bindings remain immutable.
"""
from copy import deepcopy
import re
from scheduler.production_registry import RegistryGuard, validate, _time
from scheduler.production_evidence import timestamp

WORKFLOW='production-publish-recovery-us.yml'
STEP='Attest recovered original ledger publication'
SCOPES={
    ('stock_us','theoretical_catchup'):('run-us.yml',WORKFLOW,'Run US strategy with retry'),
    ('stock_a','formal'):('run-a-share.yml','production-publish-recovery-a.yml','Run A-share strategy with retry'),
}

def need(ok,reason):
    if not ok:raise RegistryGuard(reason)

def verify(original,original_jobs,recovery,recovery_jobs,row,expected_source):
    scope=SCOPES.get((row.get('task'),row.get('publicationMode')))
    need(scope is not None,'recovery_scope_unapproved')
    workflow,recovery_workflow,strategy_step=scope
    need(row.get('repo')=='chaonpu/Stock' and row.get('workflow')==workflow
         and row.get('route')=='windows','recovery_scope_unapproved')
    need(original.get('id')==row['runId'] and original.get('head_sha')==row['sourceSha']
         and original.get('repository',{}).get('full_name')==row['repo']
         and original.get('event')=='workflow_dispatch' and original.get('head_branch')=='main'
         and original.get('path','').split('@')[0]=='.github/workflows/'+workflow
         and original.get('run_attempt')==1 and original.get('status')=='completed'
         and original.get('conclusion')=='failure','original_failed_run_unverified')
    need(original_jobs.get('total_count')==1 and len(original_jobs.get('jobs',[]))==1,
         'original_job_unverified')
    job=original_jobs['jobs'][0]
    need(job.get('run_id')==row['runId'] and job.get('status')=='completed'
         and job.get('conclusion')=='failure'
         and job.get('name')==f"effect-v1:{row['id']}:{row['coordinatorRunId']}:{row['publicationMode']}",
         'original_job_scope_mismatch')
    phases={strategy_step:'success','Verify private snapshot contract':'success',
        'Publish verified snapshot and ledger to private database':'failure',
        'Synchronize coordinated inputs to the confirmed settled ledger':'skipped',
        'Save successful strategy state':'skipped','Save failure-safe caches':'skipped',
        'Verify and attest published ledger receipt':'skipped'}
    if row['task']=='stock_a':
        phases['Verify approved mobile HTML full report']='success'
    for name,result in phases.items():
        steps=[s for s in job.get('steps',[]) if s.get('name')==name]
        need(len(steps)==1 and steps[0].get('conclusion')==result,'original_phase_unverified')
    need(re.fullmatch(r'[a-f0-9]{40}',str(expected_source))
         and recovery.get('head_sha')==expected_source
         and recovery.get('repository',{}).get('full_name')==row['repo']
         and recovery.get('path','').split('@')[0]=='.github/workflows/'+recovery_workflow
         and recovery.get('event') in ('push','workflow_dispatch')
         and recovery.get('actor',{}).get('login')=='chaonpu'
         and recovery.get('head_branch')=='main' and recovery.get('run_attempt')==1
         and type(recovery.get('id')) is int and recovery['id']!=row['runId']
         and recovery.get('status')=='completed' and recovery.get('conclusion')=='success',
         'recovery_run_unverified')
    need(recovery_jobs.get('total_count')==1 and len(recovery_jobs.get('jobs',[]))==1,
         'recovery_jobs_incomplete')
    job=recovery_jobs['jobs'][0]
    steps=[s for s in job.get('steps',[]) if s.get('name')==STEP]
    need(job.get('run_id')==recovery['id'] and job.get('status')=='completed'
         and job.get('conclusion')=='success'
         and job.get('run_attempt',1)==1
         and job.get('name')==f"recovery-v1:{row['id']}:{row['runId']}:{row['coordinatorRunId']}"
         and len(steps)==1 and steps[0].get('status')=='completed'
         and steps[0].get('conclusion')=='success','recovery_attestation_unverified')
    need(timestamp(recovery['run_started_at'])<=timestamp(steps[0]['completed_at'])
         <=timestamp(job['completed_at'])<=timestamp(recovery['updated_at']),
         'recovery_attestation_time_invalid')
    return {'privateSameCommitReceiptAttested':True,'originalRunSucceeded':False,
        'recoveryRunSucceeded':True,'originalComputationReused':True,
        'strategyRecomputed':False,'formalReportVerified':row['task']=='stock_a','businessVerified':True}

def reconcile(config,state,key,original,original_jobs,recovery,recovery_jobs,*,expected_source,at):
    validate(config,state);_time(at)
    need(config.get('dispatchEnabled') is False and 'activeLease' not in config
         and all(x.get('enabledForDispatch') is False for x in config['tasks'].values()),
         'recovery_requires_closed_permits')
    output=deepcopy(state)
    rows=[r for r in output['tasks'] if r['id']==key]
    need(len(rows)==1 and rows[0]['status']=='hold','original_hold_required')
    row=rows[0]
    proof=verify(original,original_jobs,recovery,recovery_jobs,row,expected_source)
    need(timestamp(at)>=timestamp(recovery['updated_at']),'recovery_reconciliation_before_completion')
    row.update(status='verified',updatedAt=at,completionMode='recovered_original_commit',
               completionRunId=recovery['id'],completionSourceSha=expected_source)
    return validate(config,output),proof
