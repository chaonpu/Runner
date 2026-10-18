import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('prune',ROOT/'tools/prune_retired_workflow_history.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class Tests(unittest.TestCase):
    def plan(self):return json.loads((ROOT/p.PLAN).read_text())

    def test_reviewed_plan_has_only_retired_paths_and_exact_count(self):
        self.assertEqual(p.validate(self.plan()),126)

    def test_current_workflow_can_never_be_in_delete_scope(self):
        plan=self.plan();item=plan['repositories'][0]
        item['retiredWorkflows'][0]='.github/workflows/run-a-share.yml'
        with self.assertRaisesRegex(p.Stop,'active_or_invalid_workflow'):p.validate(plan)

    def test_foreign_repository_and_duplicate_id_are_rejected(self):
        plan=self.plan();plan['repositories'][0]['repository']='chaonpu/ziwuxilv'
        with self.assertRaises(p.Stop):p.validate(plan)
        plan=self.plan();plan['repositories'][0]['runs'][1]=copy.deepcopy(plan['repositories'][0]['runs'][0])
        with self.assertRaisesRegex(p.Stop,'invalid_or_duplicate_run'):p.validate(plan)

    def test_api_does_not_allow_release_file_or_unrelated_deletion(self):
        for repo,path in [('chaonpu/ziwuxilv','/actions/runs/123'),
                          ('chaonpu/Stock','/releases/123'),
                          ('chaonpu/Stock','/contents/data.json')]:
            with self.assertRaisesRegex(p.Stop,'endpoint_forbidden'):p.api('fake',repo,path,'DELETE')

    def simulate(self, run):
        expected={'id':123,'path':'.github/workflows/old.yml','workflowId':456}
        plan={'repositories':[{'repository':'chaonpu/Stock','runs':[expected]}]}
        self.effects=[]
        def api(token,repo,path,method='GET',body=None):
            self.effects.append((path,method))
            if '?' in path:return {'workflow_runs':[]}
            if method=='DELETE':return {}
            return run
        with patch.object(p,'validate',return_value=1),patch.object(p,'persist'),\
             patch.object(p,'api',side_effect=api),patch.object(p.time,'sleep'):
            p.cleanup({'GH_TOKEN':'fake','GITHUB_RUN_ID':'999','GITHUB_SHA':'a'*40,
                       'CHAONPU_ACTIONS_READ_TOKEN':'fake'},plan)

    def test_exact_run_deleted_and_inventory_verified(self):
        self.simulate({'id':123,'path':'.github/workflows/old.yml','workflow_id':456,
                       'repository':{'full_name':'chaonpu/Stock'},'status':'completed'})
        self.assertEqual(self.effects,[('/actions/runs/123','GET'),('/actions/runs/123','DELETE'),
                                      ('/actions/runs?per_page=100&page=1','GET')])

    def test_identity_mismatch_stops_before_delete(self):
        with self.assertRaisesRegex(p.Stop,'run_identity_mismatch'):
            self.simulate({'id':123,'path':'.github/workflows/run-a-share.yml','workflow_id':456,
                           'repository':{'full_name':'chaonpu/Stock'},'status':'completed'})
        self.assertNotIn(('/actions/runs/123','DELETE'),self.effects)

    def test_unfinished_run_not_deleted(self):
        self.simulate({'id':123,'path':'.github/workflows/old.yml','workflow_id':456,
                       'repository':{'full_name':'chaonpu/Stock'},'status':'queued'})
        self.assertNotIn(('/actions/runs/123','DELETE'),self.effects)

if __name__=='__main__':unittest.main()
