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
PUBLISH=dict(REQ,sourceUrl='https://github.com/chaonpu/Runner/releases/download/staged-apk-v2.3.5-signrun-456/ziwuxilv-verified-v2.3.5.zip',artifactSha256='b'*64,
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

    def invoke(self, state=None, mode='prepare', execute=False, token=False, fail_stage=False, bad_response=False,
               fail_verify=False, fail_cleanup=False):
        registry={'version':1,'releases':{}}
        if state: registry['releases']['118']=deepcopy(state)
        self.events=[]
        def document(*args):return deepcopy(registry),'blob'
        def save(env,key,row,previous):
            self.assertEqual(registry['releases'].get(key),previous)
            registry['releases'][key]=deepcopy(row)
            self.events.append('cleanup_recorded' if 'stagingCleanup' in row else row['phase'])
        def api(*args,**kwargs):
            self.assertEqual(args[1],'fake-chaonpu-actions')
            self.events.append('POST')
            return {} if bad_response else {'workflow_run_id':789}
        def stage(env,req):
            self.assertEqual(registry['releases']['118']['phase'],'staging_reserved')
            self.events.append('STAGE')
            if fail_stage: raise p.build.Hold('stage_failed')
            return PUBLISH
        def cleanup(env, row):
            self.assertGreater(verify.call_count, 0)
            self.assertEqual(row['phase'], 'published')
            if fail_cleanup: raise p.build.Hold('staging_cleanup_api_unavailable')
            return {'status':'deleted','releaseId':999}
        env=dict(ENV)
        if token:env['CHAONPU_ACTIONS_READ_TOKEN']='fake-chaonpu-actions'
        with tempfile.TemporaryDirectory() as directory:
            old=Path.cwd()
            import os
            os.chdir(directory)
            try:
                with patch.object(p,'signed_request',return_value=REQ),patch.object(p.build,'json_file',side_effect=document),\
                     patch.object(p,'public_version',return_value=117),patch.object(p,'save',side_effect=save),\
                     patch.object(p,'stage',side_effect=stage),patch.object(p,'public_api',side_effect=api),\
                     patch.object(p,'wait_public') as wait,\
                     patch.object(p,'verify_public',side_effect=p.build.Hold('bad_public_hash') if fail_verify else None) as verify,\
                     patch.object(p,'cleanup_published_staging',side_effect=cleanup) as clean:
                    self.cleanup_mock=clean
                    result=p.run(env,source=SOURCE,version='2.3.5',code=118,log='Test update',mode=mode,execute=execute)
                    self.wait_count=wait.call_count
                    self.verify_count=verify.call_count
                    return result
            finally:os.chdir(old)

    def test_preview_has_no_effects(self):
        result=self.invoke()
        self.assertFalse(result['published'])
        self.assertEqual(self.events,[])

    def test_missing_shared_actions_token_stops_before_staging(self):
        with self.assertRaisesRegex(p.build.Hold,'token_missing'):
            self.invoke(mode='publish',execute=True)
        self.assertEqual(self.events,[])

    def test_preview_uses_shared_secret_configuration_without_side_effects(self):
        result=self.invoke(token=True)
        self.assertTrue(result['publicDispatchCredentialConfigured'])
        self.assertFalse(result['published'])
        self.assertEqual(self.events,[])

    def test_prepare_without_extra_token_is_not_publication(self):
        result=self.invoke(execute=True)
        self.assertEqual(result['status'],'prepared')
        self.assertTrue(result['previewStored'])
        self.assertTrue(result['stagedNow'])
        self.assertTrue(result['stagingUrl'].startswith('https://'))
        self.assertFalse(result['published'])
        self.assertEqual(self.events,['staging_reserved','STAGE','prepared'])
        self.assertEqual(self.wait_count,0)
        self.assertEqual(self.verify_count,0)
        self.cleanup_mock.assert_not_called()

    def test_preview_with_formal_token_only_stages_in_runner(self):
        result=self.invoke(mode='prepare',execute=True,token=True)
        self.assertTrue(result['previewStored'])
        self.assertFalse(result['published'])
        self.assertEqual(self.events,['staging_reserved','STAGE','prepared'])
        self.assertEqual(self.wait_count,0)
        self.cleanup_mock.assert_not_called()

    def test_repeated_preview_reuses_only_runner_staging(self):
        result=self.invoke(state=dict(REQ,phase='prepared',publishRequest=PUBLISH),
                           mode='prepare',execute=True,token=True)
        self.assertTrue(result['previewStored'])
        self.assertFalse(result['stagedNow'])
        self.assertFalse(result['published'])
        self.assertEqual(self.events,[])
        self.assertEqual(self.verify_count,0)
        self.cleanup_mock.assert_not_called()

    def test_formal_release_reuses_prepared_preview_without_restaging(self):
        result=self.invoke(state=dict(REQ,phase='prepared',publishRequest=PUBLISH),
                           mode='publish',execute=True,token=True)
        self.assertTrue(result['published'])
        self.assertEqual(self.events,['dispatch_reserved','POST','publishing','published','cleanup_recorded'])
        self.assertEqual(self.wait_count,1)

    def test_preview_cannot_operate_on_publishing_or_published_version(self):
        for phase in ('staging_reserved','dispatch_reserved','publishing','published'):
            with self.subTest(phase=phase),self.assertRaisesRegex(
                    p.build.Hold,'preview_requires_unpublished_prepared_version'):
                self.invoke(state=dict(REQ,phase=phase,publishRequest=PUBLISH),
                            mode='prepare',execute=True,token=True)
            self.assertEqual(self.events,[])

    def test_single_dispatch_exact_run_verified(self):
        result=self.invoke(mode='publish',execute=True,token=True)
        self.assertTrue(result['published'])
        self.assertEqual(result['publicRunId'],789)
        self.assertEqual(self.events,['staging_reserved','STAGE','prepared','dispatch_reserved','POST','publishing','published','cleanup_recorded'])
        self.assertEqual(self.wait_count,1)

    def test_unknown_dispatch_never_succeeds_or_repeats(self):
        with self.assertRaisesRegex(p.build.Hold,'run_id_unknown'):
            self.invoke(mode='publish',execute=True,token=True,bad_response=True)
        self.assertEqual(self.events[-2:],['dispatch_reserved','POST'])
        with self.assertRaisesRegex(p.build.Hold,'dispatch_unknown'):
            self.invoke(state=dict(REQ,phase='dispatch_reserved',publishRequest=PUBLISH),mode='publish',execute=True,token=True)
        self.assertEqual(self.events,[])

    def test_original_publishing_resumes_without_redispatch_or_restage(self):
        result=self.invoke(state=dict(REQ,phase='publishing',publishRequest=PUBLISH,publicRunId=789),mode='publish',execute=True,token=True)
        self.assertTrue(result['published'])
        self.assertEqual(self.events,['published','cleanup_recorded'])
        self.assertEqual(self.wait_count,1)

    def test_prepared_request_reusable_after_manual_backend_release(self):
        result=self.invoke(state=dict(REQ,phase='prepared',publishRequest=PUBLISH),mode='verify',execute=True)
        self.assertTrue(result['published'])
        self.assertEqual(self.events,['published','cleanup_recorded'])

    def test_failed_public_hash_never_cleans_staging(self):
        with self.assertRaisesRegex(p.build.Hold,'bad_public_hash'):
            self.invoke(state=dict(REQ,phase='publishing',publishRequest=PUBLISH,publicRunId=789),
                        mode='publish',execute=True,token=True,fail_verify=True)
        self.cleanup_mock.assert_not_called()

    def test_cleanup_failure_keeps_publication_successful_and_repeat_does_not_dispatch(self):
        state=dict(REQ,phase='published',publishRequest=PUBLISH,publicRunId=789)
        result=self.invoke(state=state,mode='publish',execute=True,token=True,fail_cleanup=True)
        self.assertTrue(result['published'])
        self.assertEqual(result['stagingCleanup']['status'],'pending')
        self.assertEqual(self.events,[])
        result=self.invoke(state=state,mode='publish',execute=True,token=True)
        self.assertEqual(result['stagingCleanup']['status'],'deleted')
        self.assertEqual(self.events,['cleanup_recorded'])

    def test_preview_of_published_version_never_cleans(self):
        self.invoke(state=dict(REQ,phase='published',publishRequest=PUBLISH),execute=False)
        self.cleanup_mock.assert_not_called()

    def test_unknown_stage_cannot_be_repeated(self):
        with self.assertRaises(p.build.Hold):self.invoke(execute=True,fail_stage=True)
        self.assertEqual(self.events,['staging_reserved','STAGE'])
        with self.assertRaisesRegex(p.build.Hold,'staging_outcome_unknown'):
            self.invoke(state=dict(REQ,phase='staging_reserved'),mode='publish',execute=True,token=True)
        self.assertEqual(self.events,[])

    def test_version_identity_conflict_stops_before_effects(self):
        with self.assertRaisesRegex(p.build.Hold,'identity_conflict'):
            self.invoke(state=dict(REQ,sourceCommit='f'*40,phase='prepared'),execute=True)
        self.assertEqual(self.events,[])


if __name__=='__main__':unittest.main()

