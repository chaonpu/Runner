import copy
import json
from pathlib import Path
import unittest
from scheduler.production_recovery import reconcile,verify,STEP
from scheduler.production_registry import RegistryGuard,validate
from scheduler.production_store import verify_change

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads(Path('config/repositories.json').read_text())
        self.config['dispatchEnabled']=False;self.config.pop('activeLease',None)
        for spec in self.config['tasks'].values():spec['enabledForDispatch']=False
        self.row={'id':'stock_us:2026-10-16','task':'stock_us','repo':'chaonpu/Stock',
            'workflow':'run-us.yml','route':'windows','status':'hold',
            'coordinatorRunId':39028948173,'runId':39029549179,'sourceSha':'a'*40,
            'publicationMode':'theoretical_catchup','createdAt':'2026-10-17T11:39:19Z',
            'updatedAt':'2026-10-17T11:46:02Z'}
        self.state={'schema':1,'tasks':[self.row]}
        self.original={'id':self.row['runId'],'head_sha':'a'*40,'repository':{'full_name':'chaonpu/Stock'},
            'event':'workflow_dispatch','head_branch':'main','path':'.github/workflows/run-us.yml',
            'run_attempt':1,'status':'completed','conclusion':'failure'}
        phases={'Run US strategy with retry':'success','Verify private snapshot contract':'success',
            'Publish verified snapshot and ledger to private database':'failure',
            'Synchronize coordinated inputs to the confirmed settled ledger':'skipped',
            'Save successful strategy state':'skipped','Save failure-safe caches':'skipped',
            'Verify and attest published ledger receipt':'skipped'}
        self.jobs={'total_count':1,'jobs':[{'run_id':self.row['runId'],'status':'completed',
            'conclusion':'failure','name':'effect-v1:stock_us:2026-10-16:39028948173:theoretical_catchup',
            'steps':[{'name':k,'conclusion':v} for k,v in phases.items()]}]}
        self.recovery={'id':39031059755,'head_sha':'b'*40,'repository':{'full_name':'chaonpu/Stock'},
            'path':'.github/workflows/production-publish-recovery-us.yml','event':'push',
            'actor':{'login':'chaonpu'},'head_branch':'main','run_attempt':1,
            'status':'completed','conclusion':'success','run_started_at':'2026-10-17T12:00:00Z',
            'updated_at':'2026-10-17T12:05:01Z'}
        self.recovery_jobs={'total_count':1,'jobs':[{'run_id':self.recovery['id'],
            'status':'completed','conclusion':'success','completed_at':'2026-10-17T12:05:00Z',
            'name':'recovery-v1:stock_us:2026-10-16:39029549179:39028948173',
            'steps':[{'name':STEP,'status':'completed','conclusion':'success',
                      'completed_at':'2026-10-17T12:04:59Z'}]}]}

    def finish(self):
        return reconcile(self.config,self.state,self.row['id'],self.original,self.jobs,
            self.recovery,self.recovery_jobs,expected_source='b'*40,at='2026-10-17T12:06:00Z')

    def test_recovery_keeps_original_failed_run_and_separate_completion(self):
        result,proof=self.finish();row=result['tasks'][0]
        self.assertEqual(row['runId'],self.row['runId'])
        self.assertEqual(row['sourceSha'],'a'*40)
        self.assertEqual(row['completionRunId'],self.recovery['id'])
        self.assertEqual(row['status'],'verified')
        self.assertFalse(proof['originalRunSucceeded'])
        self.assertFalse(proof['formalReportVerified'])
        self.assertEqual(self.state['tasks'][0]['status'],'hold')
        with self.assertRaises(RegistryGuard):
            verify_change(self.config,self.state,result,self.row['coordinatorRunId'])

    def test_wrong_recovery_run_and_missing_final_attestation_fail_closed(self):
        for field,value in [('head_sha','c'*40),('conclusion','failure'),('status','in_progress'),
                            ('actor',{'login':'other'}),('run_attempt',2)]:
            old=self.recovery[field];self.recovery[field]=value
            with self.subTest(field=field),self.assertRaises(RegistryGuard):self.finish()
            self.recovery[field]=old
        self.recovery_jobs['jobs'][0]['steps'][0]['conclusion']='skipped'
        with self.assertRaises(RegistryGuard):self.finish()

    def test_failed_computation_or_open_permit_cannot_be_reconciled(self):
        self.jobs['jobs'][0]['steps'][0]['conclusion']='failure'
        with self.assertRaises(RegistryGuard):self.finish()
        self.jobs['jobs'][0]['steps'][0]['conclusion']='success'
        self.config['dispatchEnabled']=True
        with self.assertRaises(RegistryGuard):self.finish()

    def test_completion_metadata_requires_complete_verified_separate_run(self):
        for metadata in [{'completionMode':'recovered_original_commit'},
                         {'completionMode':'recovered_original_commit','completionRunId':self.row['runId'],
                          'completionSourceSha':'b'*40}]:
            altered=copy.deepcopy(self.state);altered['tasks'][0].update(metadata)
            with self.assertRaises(RegistryGuard):validate(self.config,altered)

    def make_a_share_formal(self):
        self.row.update(id='stock_a:2026-10-16', task='stock_a',
                        workflow='run-a-share.yml', publicationMode='formal')
        self.original['path']='.github/workflows/run-a-share.yml'
        self.jobs['jobs'][0]['name']='effect-v1:stock_a:2026-10-16:39028948173:formal'
        self.jobs['jobs'][0]['steps'][0]['name']='Run A-share strategy with retry'
        self.jobs['jobs'][0]['steps'].append({'name':'Verify approved mobile HTML full report',
                                            'conclusion':'success'})
        self.recovery['path']='.github/workflows/production-publish-recovery-a.yml'
        self.recovery_jobs['jobs'][0]['name']='recovery-v1:stock_a:2026-10-16:39029549179:39028948173'

    def test_a_share_formal_recovery_preserves_original_failed_run(self):
        self.make_a_share_formal()
        result,proof=self.finish()
        row=result['tasks'][0]
        self.assertEqual(row['status'],'verified')
        self.assertEqual(row['runId'],self.original['id'])
        self.assertEqual(row['sourceSha'],self.original['head_sha'])
        self.assertEqual(row['completionRunId'],self.recovery['id'])
        self.assertTrue(proof['formalReportVerified'])
        self.assertFalse(proof['strategyRecomputed'])
        self.assertFalse(proof['originalRunSucceeded'])
        with self.assertRaises(RegistryGuard):
            verify_change(self.config,self.state,result,self.row['coordinatorRunId'])

    def test_a_share_requires_original_html_and_exact_recovery_workflow(self):
        self.make_a_share_formal()
        self.jobs['jobs'][0]['steps'][-1]['conclusion']='failure'
        with self.assertRaises(RegistryGuard):self.finish()
        self.jobs['jobs'][0]['steps'][-1]['conclusion']='success'
        self.recovery['path']='.github/workflows/production-publish-recovery-us.yml'
        with self.assertRaises(RegistryGuard):self.finish()

    def test_unapproved_a_share_catchup_and_us_formal_recovery_rejected(self):
        self.row['publicationMode']='formal'
        with self.assertRaises(RegistryGuard):self.finish()
        self.make_a_share_formal()
        self.row['publicationMode']='theoretical_catchup'
        with self.assertRaises(RegistryGuard):self.finish()
