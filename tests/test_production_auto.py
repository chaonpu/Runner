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
    def test_both_market_readiness_dispatches_request_auto_with_own_verified_source(self):
        for task, workflow in (("stock_a", "production-readiness-cloud-a.yml"),
                               ("stock_us", "production-readiness-cloud-us.yml")):
            with self.subTest(task=task):
                ports = Mock()
                ports.env = {"CHAONPU_ACTIONS_READ_TOKEN": "synthetic"}
                ports.requester.return_value = {"workflow_run_id": 90000000001}
                visible = {"event": "workflow_dispatch", "head_branch": "main",
                    "run_attempt": 1, "path": ".github/workflows/" + workflow,
                    "status": "completed", "conclusion": "success", "head_sha": "b" * 40}
                ports.run.side_effect = [RegistryGuard("private_actions_read_not_found"), visible]
                cfg = {"tasks": {task: {}}, "accounts": {"chaonpu": {}}}
                state = {"tasks": [
                    {"task": task, "status": "verified", "id": task + ":2026-10-16", "sourceSha": "a" * 40},
                    {"task": task, "status": "hold", "id": task + ":2026-10-18", "sourceSha": "c" * 40},
                ]}
                with patch.object(c, "existing_attempt", return_value={"status": "no_active_run"}), \
                     patch.object(c, "hosted_capacity", return_value="available"), \
                     patch.object(c, "inspect", return_value={"marketDataReady": True, "sourceSha": "b" * 40}), \
                     patch.object(c.time, "sleep") as sleep, \
                     patch("sys.stdout", new_callable=io.StringIO):
                    c.prepare(ports, cfg, task, "2026-10-19", "formal", state)
                self.assertEqual(ports.run.call_count, 2)
                self.assertEqual(sleep.call_count, 1)
                sleep.assert_called_once_with(2)
                ports.requester.assert_called_once()
                inputs = ports.requester.call_args.args[3]["inputs"]
                self.assertEqual(inputs["readiness_profile"], "auto")
                self.assertEqual(inputs["previous_verified_sha"], "a" * 40)

    def test_acknowledged_readiness_id_has_bounded_404_visibility_grace(self):
        for task in ("stock_a", "stock_us"):
            with self.subTest(task=task):
                ports = Mock()
                ports.env = {"CHAONPU_ACTIONS_READ_TOKEN": "synthetic"}
                ports.requester.return_value = {"workflow_run_id": 90000000001}
                ports.run.side_effect = RegistryGuard("private_actions_read_not_found")
                cfg = {"tasks": {task: {}}, "accounts": {"chaonpu": {}}}
                with patch.object(c, "existing_attempt", return_value={"status": "no_active_run"}), \
                     patch.object(c, "hosted_capacity", return_value="available"), \
                     patch.object(c.time, "sleep") as sleep, \
                     patch("sys.stdout", new_callable=io.StringIO):
                    with self.assertRaisesRegex(RegistryGuard, "private_actions_read_not_found"):
                        c.prepare(ports, cfg, task, "2026-10-19", "formal", {"tasks": []})
                self.assertEqual(ports.run.call_count, 15)
                self.assertTrue(all(call.args == ("chaonpu/Stock", 90000000001)
                                    for call in ports.run.call_args_list))
                self.assertEqual(sleep.call_count, 14)
                ports.requester.assert_called_once()

    def test_non_404_readiness_failure_is_never_retried(self):
        ports = Mock()
        ports.env = {"CHAONPU_ACTIONS_READ_TOKEN": "synthetic"}
        ports.requester.return_value = {"workflow_run_id": 90000000001}
        ports.run.side_effect = RegistryGuard("private_actions_read_permission_denied")
        cfg = {"tasks": {"stock_a": {}}, "accounts": {"chaonpu": {}}}
        with patch.object(c, "existing_attempt", return_value={"status": "no_active_run"}), \
             patch.object(c, "hosted_capacity", return_value="available"), \
             patch.object(c.time, "sleep") as sleep, \
             patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaisesRegex(RegistryGuard, "private_actions_read_permission_denied"):
                c.prepare(ports, cfg, "stock_a", "2026-10-19", "formal", {"tasks": []})
        ports.run.assert_called_once_with("chaonpu/Stock", 90000000001)
        sleep.assert_not_called()
        ports.requester.assert_called_once()

    def test_unacknowledged_readiness_dispatch_is_not_queried_or_repeated(self):
        ports = Mock()
        ports.env = {"CHAONPU_ACTIONS_READ_TOKEN": "synthetic"}
        ports.requester.return_value = {}
        cfg = {"tasks": {"stock_a": {}}, "accounts": {"chaonpu": {}}}
        with patch.object(c, "existing_attempt", return_value={"status": "no_active_run"}), \
             patch.object(c, "hosted_capacity", return_value="available"), \
             patch.object(c.time, "sleep") as sleep:
            with self.assertRaisesRegex(RegistryGuard, "readiness_dispatch_unknown_do_not_repeat"):
                c.prepare(ports, cfg, "stock_a", "2026-10-19", "formal", {"tasks": []})
        ports.requester.assert_called_once()
        ports.run.assert_not_called()
        sleep.assert_not_called()

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
            c.resolve_request('stock_us',now=datetime(2029,1,5,22,tzinfo=timezone.utc))

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
             patch.object(c,'pin_latest'),patch.object(c,'resolve_request',return_value=plan) as resolve,\
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

