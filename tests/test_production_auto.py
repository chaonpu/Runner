"""Automatic date/mode boundaries and preview/execute parity."""
from datetime import datetime, timezone
import io
import json
from unittest.mock import Mock, patch
import unittest

from scheduler import production_coordinator as c
from scheduler.production_registry import RegistryGuard
from test_production_session import environment


def utc(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


class AutomaticStrategyTests(unittest.TestCase):
    def test_a_close_boundary(self):
        before=c.resolve_request('stock_a',now=utc(10,19,6,59))
        after=c.resolve_request('stock_a',now=utc(10,19,7))
        self.assertEqual((before['businessDate'],before['publicationMode']),('2026-10-16','theoretical_catchup'))
        self.assertEqual((after['businessDate'],after['publicationMode']),('2026-10-19','formal'))

    def test_us_china_next_morning_is_still_formal(self):
        plan=c.resolve_request('stock_us',now=utc(10,16,22))
        self.assertEqual((plan['businessDate'],plan['publicationMode']),('2026-10-16','formal'))
        self.assertEqual(plan['marketTimezone'],'America/New_York')

    def test_us_native_midnight_switches_to_catchup(self):
        before=c.resolve_request('stock_us',now=utc(10,17,3,59))
        after=c.resolve_request('stock_us',now=utc(10,17,4))
        self.assertEqual(before['publicationMode'],'formal')
        self.assertEqual(after['publicationMode'],'theoretical_catchup')
        self.assertEqual(after['businessDate'],'2026-10-16')

    def test_holiday_and_early_close(self):
        china=c.resolve_request('stock_a',now=utc(10,7,8))
        us=c.resolve_request('stock_us',now=utc(11,27,18))
        self.assertEqual((china['businessDate'],china['publicationMode']),('2026-09-30','theoretical_catchup'))
        self.assertEqual((us['businessDate'],us['publicationMode']),('2026-11-27','formal'))

    def test_calendar_exhaustion_cannot_dispatch(self):
        with self.assertRaisesRegex(RegistryGuard,'business_date_unavailable'):
            c.resolve_request('stock_us',now=datetime(2027,1,5,22,tzinfo=timezone.utc))

    def test_internal_override_cannot_replay_historical_universe(self):
        with self.assertRaisesRegex(RegistryGuard,'historical_decision_replay_unsupported'):
            c.resolve_request('stock_us','2026-10-15',now=utc(10,17,17))
        with self.assertRaisesRegex(RegistryGuard,'publication_window'):
            c.resolve_request('stock_us',mode='formal',now=utc(10,17,17))

    def test_workflows_only_expose_execute_and_remain_manual(self):
        from pathlib import Path
        import re
        root=Path(__file__).resolve().parents[1]
        for name in ('production-dispatch.yml','stock-us.yml'):
            text=(root/'.github/workflows'/name).read_text()
            trigger=text.split('on:\n',1)[1].split('\npermissions:',1)[0]
            self.assertEqual(re.findall(r'^  (\w+):',trigger,re.M),['workflow_dispatch'])
            self.assertEqual(re.findall(r'^      (\w+):',trigger,re.M),['execute'])
            self.assertIn('args=(--task "$TASK")',text)
            self.assertNotIn('inputs.business_date',text)
            self.assertNotIn('inputs.publication_mode',text)

    def run_cli(self, execute):
        plan={'businessDate':'2026-10-16','publicationMode':'theoretical_catchup',
              'dateMode':'auto_official_schedule_candidate','marketTimezone':'America/New_York'}
        env=dict(environment(),GITHUB_WORKFLOW_REF='chaonpu/Runner/.github/workflows/stock-us.yml@refs/heads/main')
        ports=Mock()
        ports.load.return_value=({'tasks':{'stock_us':{'publicationMode':'formal'}}},{'tasks':[]},'blob')
        argv=['coordinator','--task','stock_us']+(['--execute'] if execute else [])
        with patch.dict(c.os.environ,env,clear=True),patch('sys.argv',argv),\
             patch.object(c,'resolve_request',return_value=plan) as resolve,\
             patch.object(c,'execute') as run,patch.object(c,'ReadOnlyPorts',return_value=ports),\
             patch('scheduler.production_ports.public_report',return_value={'dispatchAuthorized':False}),\
             patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(c.main(),0)
            resolve.assert_called_once_with('stock_us','','auto')
            if execute:
                run.assert_called_once()
                self.assertEqual(run.call_args.args[1:],('stock_us','2026-10-16','theoretical_catchup'))
                ports.load.assert_not_called()
            else:
                run.assert_not_called()
                cfg,_,task,day=ports.audit.call_args.args
                self.assertEqual(cfg['tasks'][task]['publicationMode'],'theoretical_catchup')
                self.assertEqual(day,'2026-10-16')
                report=json.loads(output.getvalue().splitlines()[-1])
                self.assertEqual(report['publicationMode'],'theoretical_catchup')
                self.assertFalse(report['dispatchAuthorized'])

    def test_preview_uses_automatic_mode_without_execution(self):
        self.run_cli(False)

    def test_execution_uses_same_resolved_plan(self):
        self.run_cli(True)
