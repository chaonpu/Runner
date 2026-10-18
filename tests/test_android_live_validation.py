"""Live validation must gate signing using private execution metadata."""
import base64
import json
import unittest
from unittest.mock import patch
from scheduler import android_production as a


class LiveValidationTests(unittest.TestCase):
    def jobs(self, conclusion='success'):
        return {'total_count':1, 'jobs':[{'conclusion':'success', 'steps':[
            {'name':'Verify live chart execution evidence', 'conclusion':conclusion}]}]}

    def test_exact_step_and_private_artifact_required(self):
        with patch.object(a, 'api', return_value=self.jobs()), patch.object(a, 'verify_artifact') as artifact:
            a.verify_live_execution('token', 42)
            artifact.assert_called_once_with('token', a.TEST, 42, 'live-chart-validation')
        for outcome in ('skipped','failure',None):
            with patch.object(a, 'api', return_value=self.jobs(outcome)), patch.object(a, 'verify_artifact') as artifact:
                with self.assertRaises(a.Hold):
                    a.verify_live_execution('token', 42)
                artifact.assert_not_called()
        with patch.object(a, 'api', return_value={'total_count':1,'jobs':[{'conclusion':'success','steps':[]}]}):
            with self.assertRaises(a.Hold):
                a.verify_live_execution('token', 42)

    def test_live_option_is_reserved_before_dispatch(self):
        state={'version':1,'builds':{}}
        with patch.object(a, 'json_file', return_value=(state,'blob')), patch.object(a, 'api', return_value={'commit':{'sha':'b'*40}}) as write:
            a.reserve('public','a'*40,live_smoke=True)
            saved=json.loads(base64.b64decode(write.call_args.kwargs['payload']['content']))
            self.assertIs(saved['builds']['a'*40]['liveSmoke'],True)
        self.assertEqual(a.test_inputs(True),{'live_smoke':'true'})
        self.assertEqual(a.test_inputs(False),{'live_smoke':'false'})
        with self.assertRaises(a.Hold):
            a.test_inputs('false')

    def test_recovery_keeps_original_option(self):
        for enabled in (True, False):
            sha='a'*40
            entry={'sourceSha':sha,'route':'windows','phase':'test_started',
                   'testRunId':42,'liveSmoke':enabled}
            state={'version':1,'builds':{sha:entry}}
            original=dict(id=42,repository={'full_name':a.TEST},
                path='.github/workflows/'+a.TEST_WORKFLOW,head_branch='main',
                head_sha=sha,event='workflow_dispatch',run_attempt=1,
                status='completed',conclusion='failure')
            jobs={'total_count':1,'jobs':[{'conclusion':'failure','steps':[
                {'name':'NETWORK_TRANSIENT_SOURCE_FETCH','conclusion':'failure'},
                {'name':'Build unsigned Release APK','conclusion':'skipped'}]}]}
            responses=[original,jobs,{'total_count':0,'artifacts':[]},{'commit':{'sha':'b'*40}}]
            with self.subTest(enabled=enabled), patch.object(a,'api',side_effect=responses), patch.object(a,'classify_failed_run',return_value='network_retry_exhausted_same_run_no_publication'), patch.object(a,'current_source',return_value=sha), patch.object(a,'check_idle'), patch.object(a,'runner_ready',return_value=True), patch.object(a,'json_file',side_effect=[(state,'old'),(state,'new')]), patch.object(a,'dispatch',return_value=43) as dispatch, patch.object(a,'put_state'), patch.object(a,'record_public'):
                self.assertEqual(a.retry_checkout_before_business('p','t',sha,entry,{},
                    {'_test_runner_name':'win'},a.TEST_WORKFLOW),43)
                dispatch.assert_called_once_with('t',a.TEST,a.TEST_WORKFLOW,
                    {'live_smoke':'true' if enabled else 'false'})

    def config(self):
        return {'accounts':{'anwpustudent-lab':{},'chaonpu':{}},'tasks':{'android':{
            'repo':a.TEST,'signingRepo':a.SIGNER,'windowsWorkflow':a.TEST_WORKFLOW,
            'signingWindowsWorkflow':a.SIGN_WORKFLOW,'runnerName':'win','signingRunnerName':'sign'}}}

    def test_new_build_passes_option_to_test(self):
        def document(token,path):
            return (self.config(),'blob') if path==a.CONFIG_PATH else ({'version':1,'builds':{}},'blob')
        for route in ('windows','hosted'):
            with self.subTest(route=route), patch.object(a,'json_file',side_effect=document), patch.object(a,'current_source',return_value='a'*40), patch.object(a,'choose_route',return_value={'route':route,'reason':'verified'}), patch.object(a,'check_idle'), patch.object(a,'choose_signer_route'), patch.object(a,'runner_ready',return_value=True), patch.object(a,'reserve') as reserve, patch.object(a,'dispatch',return_value=42) as dispatch, patch.object(a,'put_state'), patch.object(a,'record_public'), patch.object(a,'wait_recorded',side_effect=a.Hold('stop_after_dispatch')):
                with self.assertRaisesRegex(a.Hold,'stop_after_dispatch'):
                    a.run({},execute=True,live_smoke=True)
                reserve.assert_called_once_with('', 'a'*40,route=route,live_smoke=True)
                dispatch.assert_called_once_with('',a.TEST,a.workflow_for(route),{'live_smoke':'true'})

    def test_resume_rechecks_original_evidence_before_signing(self):
        entry={'sourceSha':'a'*40,'route':'windows','phase':'test_verified','testRunId':42,'liveSmoke':True}
        def document(token,path):
            return (self.config(),'blob') if path==a.CONFIG_PATH else ({'version':1,'builds':{'a'*40:entry}},'blob')
        with patch.object(a,'json_file',side_effect=document), patch.object(a,'wait_recorded'), patch.object(a,'verify_artifact'), patch.object(a,'verify_live_execution',side_effect=a.Hold('missing_live_proof')) as proof, patch.object(a,'dispatch') as dispatch:
            with self.assertRaisesRegex(a.Hold,'missing_live_proof'):
                a.run({},execute=True,resume=True,requested_sha='a'*40,live_smoke=False)
            proof.assert_called_once_with('',42)
            dispatch.assert_not_called()

    def test_old_signed_run_cannot_be_called_live_verified(self):
        entry={'sourceSha':'a'*40,'route':'windows','phase':'signed_verified','testRunId':42,'signingRunId':43}
        def document(token,path):
            return (self.config(),'blob') if path==a.CONFIG_PATH else ({'version':1,'builds':{'a'*40:entry}},'blob')
        with patch.object(a,'json_file',side_effect=document), patch.object(a,'current_source',return_value='a'*40), patch.object(a,'dispatch') as dispatch:
            with self.assertRaisesRegex(a.Hold,'registered_build_without_live_validation'):
                a.run({},execute=True,live_smoke=True)
            dispatch.assert_not_called()

    def test_dedicated_failure_category(self):
        data=self.jobs('failure');data['jobs'][0]['conclusion']='failure'
        with patch.object(a,'api',return_value=data):
            self.assertEqual(a.classify_failed_run('t',a.TEST,42),'android_live_validation_failed_or_skipped')


if __name__=='__main__':
    unittest.main()
