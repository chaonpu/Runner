from datetime import datetime, timezone
from unittest.mock import patch, Mock
import unittest
from scheduler import production_coordinator as c
from scheduler.production_registry import RegistryGuard
from test_production_session import environment, DAY
from test_production_evidence import Reader, fixtures, NOW, RID
from scheduler.production_evidence import inspect, EXISTING_STEP


class NoopTests(unittest.TestCase):
    def env(self):
        return dict(environment(),GITHUB_WORKFLOW_REF='chaonpu/Runner/.github/workflows/stock-us.yml@refs/heads/main')

    def test_verified_original_task_skips_without_new_dispatch_or_lease(self):
        ports=Mock();ports.load.return_value=({}, {'tasks':[{'id':'stock_us:'+DAY,'status':'verified','runId':123}]},'blob')
        with patch.object(c,'ProductionPorts',return_value=ports),patch.object(c,'prepare') as prep,patch.object(c,'Lease') as lease:
            out=c.execute(self.env(),'stock_us',DAY,'theoretical_catchup')
            self.assertEqual(out['status'],'skipped');self.assertEqual(out['dispatches'],0)
            self.assertTrue(out['priorRunVerified']);self.assertFalse(out['businessVerified'])
            prep.assert_not_called();lease.assert_not_called()

    def test_pending_or_uncertain_original_task_remains_blocked(self):
        for status in ('reserved','dispatched','running','hold','awaiting_effect_verification'):
            ports=Mock();ports.load.return_value=({}, {'tasks':[{'id':'stock_us:'+DAY,'status':status}]},'blob')
            with patch.object(c,'ProductionPorts',return_value=ports),patch.object(c,'prepare') as prep:
                with self.assertRaisesRegex(RegistryGuard,'already_registered'):
                    c.execute(self.env(),'stock_us',DAY,'theoretical_catchup')
                prep.assert_not_called()

    def test_authoritative_existing_pair_never_reaches_business_dispatch(self):
        ports=Mock();ports.load.return_value=({'dispatchEnabled':False},{'tasks':[]},'blob')
        proof={'alreadyCurrent':True,'readinessRunId':RID}
        with patch.object(c,'ProductionPorts',return_value=ports),patch.object(c,'prepare',return_value=proof),\
             patch.object(c,'Lease') as lease,patch.object(c,'datetime') as dt:
            dt.now.return_value=NOW
            out=c.execute(self.env(),'stock_us',DAY,'theoretical_catchup')
            self.assertEqual(out['status'],'skipped');self.assertTrue(out['authoritativeStateChecked'])
            self.assertFalse(out['businessVerified']);lease.assert_not_called()

    def test_existing_attestation_is_distinct_from_ready_for_new_calculation(self):
        listing,jobs=fixtures();live=jobs['jobs'][0]['steps'][0]
        current=dict(live,name=EXISTING_STEP);live['conclusion']='skipped'
        jobs['jobs'][0]['steps'].append(current)
        out=inspect(Reader(listing,jobs),'stock_us',DAY,NOW,expected_run_id=RID)
        self.assertTrue(out['alreadyCurrent']);self.assertFalse(out['marketDataReady'])
        self.assertFalse(out['businessVerified'])

    def test_mixed_or_foreign_run_attestations_cannot_authorize_skip(self):
        listing,jobs=fixtures()
        jobs['jobs'][0]['steps'].append(dict(jobs['jobs'][0]['steps'][0],name=EXISTING_STEP))
        out=inspect(Reader(listing,jobs),'stock_us',DAY,NOW,expected_run_id=RID)
        self.assertEqual(out['evidenceStatus'],'hold_evidence_attestation_conflict')
        listing,jobs=fixtures()
        out=inspect(Reader(listing,jobs),'stock_us',DAY,NOW,expected_run_id=RID+1)
        self.assertEqual(out['evidenceStatus'],'hold_evidence_original_run_mismatch')
