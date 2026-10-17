from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from scheduler.production_ports import ProductionPorts, PUBLIC, PRIVATE, CONFIG
from scheduler.production_lease import Lease
from scheduler.production_session import start, observe
from scheduler.production_effect import verify
from scheduler.production_registry import RegistryGuard
from test_production_ports import Backend, record, env
from test_production_session import Clock, DAY, OWNER, TARGET, SOURCE

ROOT=Path(__file__).resolve().parents[1]


class LifecycleBackend(Backend):
    def __init__(self):
        super().__init__()
        self.config=json.loads((ROOT/'config/repositories.json').read_text())
        self.lose_config_ack=False
        self.fail_attest=False

    def request(self, token, method, path, body=None):
        if method=='PUT' and path==PUBLIC+'/contents/'+CONFIG:
            import base64
            self.calls.append((method,path))
            assert body['sha']==record(CONFIG,self.config)['sha']
            self.config=json.loads(base64.b64decode(body['content']))
            if self.lose_config_ack:
                self.lose_config_ack=False
                raise TimeoutError('accepted permit ACK lost')
            return {'content':{'sha':record(CONFIG,self.config)['sha']}}
        return super().request(token,method,path,body)

    def reader(self,token):
        original=super().reader(token)
        backend=self
        class Reader:
            def get(self,path):
                if path==PRIVATE+f'/actions/runs/{TARGET}/jobs?filter=latest&per_page=100':
                    row=backend.state['tasks'][0]
                    return {'total_count':1,'jobs':[{'run_id':TARGET,'status':'completed','conclusion':'success',
                        'name':f"effect-v1:{row['id']}:{row['coordinatorRunId']}:{row.get('publicationMode','formal')}",
                        'completed_at':'2026-10-17T06:00:06Z',
                        'steps':[{'name':'Verify and attest published ledger receipt','status':'completed',
                                  'conclusion':'failure' if backend.fail_attest else 'success',
                                  'completed_at':'2026-10-17T06:00:05Z'}]}]}
                return original.get(path)
        return Reader()


class CompletionTests(unittest.TestCase):
    def setup_ports(self):
        backend=LifecycleBackend(); clock=Clock()
        ports=ProductionPorts(env(),requester=backend.request,reader_factory=backend.reader,
            bill=lambda *args:{'status':'exhausted','independentlyVerified':True,
                'remainingFreeMinutes':0,'period':'2026-10','observedAt':'2026-10-17T06:00:00+00:00'},
            runner=lambda *args:{'name':'ziwuxilv-stock-win','status':'online','busy':False,'labelsVerified':True},
            active=lambda *args:{'status':'no_active_run'},clock=clock)
        return backend,ports,clock

    def test_exact_task_lease_full_fake_http_effect_verification_and_off_restore(self):
        backend,ports,clock=self.setup_ports(); original=deepcopy(backend.config)
        lease=Lease(ports); lease.arm('stock_us',DAY,'formal')
        self.assertEqual(backend.config['activeLease']['taskId'],'stock_us:'+DAY)
        self.assertTrue(backend.config['tasks']['stock_us']['enabledForDispatch'])
        self.assertFalse(backend.config['tasks']['stock_a']['enabledForDispatch'])
        session=start(ports,'stock_us',DAY,env(),clock=clock)
        backend.target.update(status='completed',conclusion='success',run_started_at='2026-10-17T05:59:00Z',updated_at='2026-10-17T06:00:07Z')
        observe(ports,session,env(),clock=clock)
        result=ports.finish(session)
        self.assertTrue(result['businessVerified'])
        self.assertTrue(result['formalReportVerified'])
        self.assertEqual(backend.state['tasks'][0]['status'],'verified')
        lease.release()
        self.assertEqual(backend.config,original)
        self.assertEqual(ports.load()[1]['tasks'][0]['status'],'verified')
        self.assertEqual(len([x for x in backend.calls if x[0]=='POST']),1)

    def test_catchup_verified_as_data_effect_never_as_formal_report(self):
        backend,ports,clock=self.setup_ports(); lease=Lease(ports)
        backend.evidence_jobs['jobs'][0]['name']+=':theoretical_catchup'
        lease.arm('stock_us',DAY,'theoretical_catchup')
        session=start(ports,'stock_us',DAY,env(),clock=clock)
        backend.target.update(status='completed',conclusion='success',run_started_at='2026-10-17T05:59:00Z',updated_at='2026-10-17T06:00:07Z')
        observe(ports,session,env(),clock=clock)
        out=ports.finish(session)
        self.assertEqual(out['publicationMode'],'theoretical_catchup')
        self.assertFalse(out['formalReportVerified'])
        self.assertTrue(out['businessVerified'])
        lease.release()

    def test_green_run_without_private_attestation_never_becomes_verified(self):
        backend,ports,clock=self.setup_ports(); lease=Lease(ports); lease.arm('stock_us',DAY,'formal')
        session=start(ports,'stock_us',DAY,env(),clock=clock)
        backend.target.update(status='completed',conclusion='success',run_started_at='2026-10-17T05:59:00Z',updated_at='2026-10-17T06:00:07Z')
        observe(ports,session,env(),clock=clock); backend.fail_attest=True
        with self.assertRaises(RegistryGuard): ports.finish(session)
        self.assertEqual(backend.state['tasks'][0]['status'],'awaiting_effect_verification')
        lease.release()

    def test_accepted_permit_with_lost_ack_can_only_restore_own_exact_config(self):
        backend,ports,_=self.setup_ports(); old=deepcopy(backend.config); lease=Lease(ports)
        backend.lose_config_ack=True
        with self.assertRaises(TimeoutError): lease.arm('stock_us',DAY,'formal')
        self.assertTrue(backend.config['dispatchEnabled'])
        lease.release()
        self.assertEqual(backend.config,old)

    def test_existing_lease_cannot_be_overwritten(self):
        backend,ports,_=self.setup_ports(); first=Lease(ports); first.arm('stock_us',DAY,'formal')
        with self.assertRaises(RegistryGuard): Lease(ports).arm('stock_a',DAY,'formal')
        first.release()

    def test_bot_launch_requires_an_actual_trusted_parent(self):
        fake=dict(env(),GITHUB_ACTOR='github-actions[bot]',BOOTSTRAP_RUN_ID='90000000444',
                  REQUEST_TASK='stock_us',REQUEST_PUBLICATION_MODE='formal',REQUEST_BUSINESS_DATE='')
        class Wrong:
            def get(self,path): return {'id':90000000444,'actor':{'login':'other'}}
        with self.assertRaises(RegistryGuard):
            ProductionPorts(fake,reader_factory=lambda token:Wrong(),requester=lambda *args:None)


if __name__=='__main__': unittest.main()
