"""Failure-oriented publication tests; no network or real release side effects."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scheduler import android_publish as p
from scheduler.production_coordinator import manual_task_context
from scheduler.production_registry import RegistryGuard
from scheduler import production_session

SOURCE='a'*40
ENV={'GITHUB_REPOSITORY':'chaonpu/Runner','GITHUB_ACTOR':'chaonpu',
     'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main',
     'GITHUB_WORKFLOW_REF':p.WORKFLOW_REF,'GITHUB_RUN_ATTEMPT':'1','GH_TOKEN':'fake'}
REQ={'sourceCommit':SOURCE,'versionName':'2.3.5','versionCode':118,
     'testRunId':123,'signingRunId':456,'updateLog':'Test update','forceUpdate':False}
PUBLISH=dict(REQ,sourceUrl='https://example.invalid/signed.zip',artifactSha256='b'*64,
             apkSha256='c'*64,signingCertificateSha256=p.CERT)

class Tests(unittest.TestCase):
    def test_owner_first_attempt_manual_only(self):
        p.validate_context(ENV)
        for key,value in [('GITHUB_ACTOR','github-actions[bot]'),('GITHUB_EVENT_NAME','push'),
                          ('GITHUB_REF','refs/heads/other'),('GITHUB_RUN_ATTEMPT','2')]:
            with self.assertRaises(p.build.Hold):
                p.validate_context(dict(ENV,**{key:value}))

    def test_market_workflow_cannot_launch_other_market(self):
        for task,name in [('stock_a','production-dispatch.yml'),('stock_us','stock-us.yml')]:
            env=dict(ENV,GITHUB_WORKFLOW_REF='chaonpu/Runner/.github/workflows/'+name+'@refs/heads/main')
            manual_task_context(env,task)
            with self.assertRaises(RegistryGuard):
                manual_task_context(env,'stock_us' if task=='stock_a' else 'stock_a')
            production_session.context(dict(env,GITHUB_RUN_ID='90000000111',GITHUB_SHA=SOURCE))

    def test_signed_state_required(self):
        for phase in ['test_verified','signer_started','signer_reserved']:
            with patch.object(p.build,'json_file',return_value=({'builds':{SOURCE:dict(REQ,sourceSha=SOURCE,phase=phase)}},'blob')):
                with self.assertRaisesRegex(p.build.Hold,'not_verified_signed'):
                    p.signed_request(ENV,SOURCE,'2.3.5',118,'Log')

    def invoke(self, state=None, mode='prepare', execute=False, token=False, fail_stage=False, bad_response=False):
        registry={'version':1,'releases':{}}
        if state: registry['releases']['118']=deepcopy(state)
        self.events=[]
        def document(*args):return deepcopy(registry),'blob'
        def save(env,key,row,previous):
            self.assertEqual(registry['releases'].get(key),previous)
            registry['releases'][key]=deepcopy(row)
            self.events.append(row['phase'])
        def api(*args,**kwargs):
            self.events.append('POST')
            return {} if bad_response else {'workflow_run_id':789}
        def stage(env,req):
            self.assertEqual(registry['releases']['118']['phase'],'staging_reserved')
            self.events.append('STAGE')
            if fail_stage: raise p.build.Hold('stage_failed')
            return PUBLISH
        env=dict(ENV)
        if token:env['ZIWUXILV_ACTIONS_WRITE_TOKEN']='fake-release-only'
        with tempfile.TemporaryDirectory() as directory:
            old=Path.cwd()
            import os
            os.chdir(directory)
            try:
                with patch.object(p,'signed_request',return_value=REQ),patch.object(p.build,'json_file',side_effect=document),\
                     patch.object(p,'public_version',return_value=117),patch.object(p,'save',side_effect=save),\
                     patch.object(p,'stage',side_effect=stage),patch.object(p,'public_api',side_effect=api),\
                     patch.object(p,'wait_public') as wait,patch.object(p,'verify_public') as verify:
                    result=p.run(env,source=SOURCE,version='2.3.5',code=118,log='Test update',mode=mode,execute=execute)
                    self.wait_count=wait.call_count
                    self.verify_count=verify.call_count
                    return result
            finally:os.chdir(old)

    def test_preview_has_no_effects(self):
        result=self.invoke()
        self.assertFalse(result['published'])
        self.assertEqual(self.events,[])

    def test_missing_public_token_stops_before_staging(self):
        with self.assertRaisesRegex(p.build.Hold,'token_missing'):
            self.invoke(mode='publish',execute=True)
        self.assertEqual(self.events,[])

    def test_prepare_without_extra_token_is_not_publication(self):
        result=self.invoke(execute=True)
        self.assertEqual(result['status'],'prepared')
        self.assertFalse(result['published'])
        self.assertEqual(self.events,['staging_reserved','STAGE','prepared'])

    def test_single_dispatch_exact_run_verified(self):
        result=self.invoke(mode='publish',execute=True,token=True)
        self.assertTrue(result['published'])
        self.assertEqual(result['publicRunId'],789)
        self.assertEqual(self.events,['staging_reserved','STAGE','prepared','dispatch_reserved','POST','publishing','published'])
        self.assertEqual(self.wait_count,1)

    def test_unknown_dispatch_never_succeeds_or_repeats(self):
        with self.assertRaisesRegex(p.build.Hold,'run_id_unknown'):
            self.invoke(mode='publish',execute=True,token=True,bad_response=True)
        self.assertEqual(self.events[-2:],['dispatch_reserved','POST'])
        with self.assertRaisesRegex(p.build.Hold,'dispatch_unknown'):
            self.invoke(state=dict(REQ,phase='dispatch_reserved',publishRequest=PUBLISH),execute=True)
        self.assertEqual(self.events,[])

    def test_original_publishing_resumes_without_redispatch_or_restage(self):
        result=self.invoke(state=dict(REQ,phase='publishing',publishRequest=PUBLISH,publicRunId=789),execute=True)
        self.assertTrue(result['published'])
        self.assertEqual(self.events,['published'])
        self.assertEqual(self.wait_count,1)

    def test_prepared_request_reusable_after_manual_backend_release(self):
        result=self.invoke(state=dict(REQ,phase='prepared',publishRequest=PUBLISH),mode='verify',execute=True)
        self.assertTrue(result['published'])
        self.assertEqual(self.events,['published'])

    def test_unknown_stage_cannot_be_repeated(self):
        with self.assertRaises(p.build.Hold):self.invoke(execute=True,fail_stage=True)
        self.assertEqual(self.events,['staging_reserved','STAGE'])
        with self.assertRaisesRegex(p.build.Hold,'staging_outcome_unknown'):
            self.invoke(state=dict(REQ,phase='staging_reserved'),execute=True)
        self.assertEqual(self.events,[])

    def test_version_identity_conflict_stops_before_effects(self):
        with self.assertRaisesRegex(p.build.Hold,'identity_conflict'):
            self.invoke(state=dict(REQ,sourceCommit='f'*40,phase='prepared'),execute=True)
        self.assertEqual(self.events,[])


if __name__=='__main__':unittest.main()
