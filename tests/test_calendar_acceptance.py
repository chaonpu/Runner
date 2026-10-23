"""Reuse exact original validation evidence without private side effects."""
from copy import deepcopy
import json
import os
import sys
import unittest
from unittest.mock import patch
from tools import calendar_acceptance as acceptance


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.config={'stockSourceSha':'a'*40,'calendarRevision':'b'*64,'stockValidationRunId':12345678901}
        self.run={'id':12345678901,'head_sha':'a'*40,'event':'workflow_dispatch','head_branch':'main',
                  'run_attempt':1,'path':'.github/workflows/production-readiness-a.yml',
                  'status':'completed','conclusion':'success','html_url':'https://github.com/chaonpu/Stock/actions/runs/12345678901'}
        self.steps=[{'name':'Validate both markets against shared pinned calendar','status':'completed','conclusion':'success'},
                    {'name':'Attest calendar snapshot '+'b'*64,'status':'completed','conclusion':'success'},
                    {'name':'Check authoritative inputs, complete native selection and temporary replay','status':'completed','conclusion':'skipped'}]
        self.calls=[]

    def request(self,token,method,url,body=None):
        self.calls.append((method,url))
        if method!='GET':raise AssertionError('reuse must never dispatch')
        if '/jobs?' in url:return {'total_count':1,'jobs':[{'steps':deepcopy(self.steps)}]}
        return deepcopy(self.run)

    def execute(self):
        with patch.object(sys,'argv',['calendar_acceptance.py','stock']),patch.dict(os.environ,{'CHAONPU_ACTIONS_READ_TOKEN':'synthetic'},clear=True),patch.object(acceptance,'request',side_effect=self.request),patch.object(acceptance.Path,'read_text',return_value=json.dumps(self.config)):
            acceptance.main()

    def test_exact_original_stock_validation_can_be_reused_without_dispatch(self):
        self.execute()
        self.assertEqual(len(self.calls),2)
        self.assertTrue(all(method=='GET' for method,_ in self.calls))

    def test_changed_source_or_rerun_attempt_cannot_replace_original_evidence(self):
        for change in ({'head_sha':'c'*40},{'run_attempt':2},{'path':'.github/workflows/run-a-share.yml'}):
            with self.subTest(change=change):
                previous=deepcopy(self.run);self.run.update(change)
                with self.assertRaisesRegex(ValueError,'source_or_workflow_mismatch'):self.execute()
                self.run=previous

    def test_calendar_revision_is_checked_even_when_whole_run_succeeded(self):
        self.steps[1]['name']='Attest calendar snapshot '+'c'*64
        with self.assertRaisesRegex(ValueError,'calendar_revision_or_comparisons'):self.execute()

    def test_reused_stock_run_must_prove_business_selection_was_skipped(self):
        self.steps[2]['conclusion']='success'
        with self.assertRaisesRegex(ValueError,'calendar_only_validation_boundary'):self.execute()


if __name__=='__main__':unittest.main()
