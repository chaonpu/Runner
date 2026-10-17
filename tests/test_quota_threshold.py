"""Shared reported-allowance policy and actual production route boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from scheduler.android_route import choose
from scheduler.policies import hosted_capacity, decide
from scheduler.production_session import start
from scheduler.production_registry import RegistryGuard
from test_production_session import MemoryPorts, Clock, environment, DAY

NOW = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)


def reported(minutes, instant=NOW):
    return {"read":"readable", "status":"unknown", "independentlyVerified":False,
            "period":instant.strftime('%Y-%m'), "observedAt":instant.isoformat(),
            "reportingPeriodVerified":True,
            "allowanceEstimate":{"read":"readable", "period":instant.strftime('%Y-%m'),
                "conversionSupported":True,"estimateStatus":"private_standard_linux_only",
                "publicStandardUsageExcluded":True,"remainingMinutesEstimate":minutes,
                "exactRemainingVerified":False}}


class ThresholdTests(unittest.TestCase):
    def test_99_100_101_shared_by_android_and_both_strategy_calculations(self):
        for minutes in (0,99,100,101):
            data=reported(minutes)
            for task in ('stock_a','stock_us','android'):
                spec=MemoryPorts().config['tasks'][task]
                spec.update(estimatedMinutes=9999,reserveMinutes=9999)
                self.assertEqual(hosted_capacity(data,spec,now=NOW),
                                 'available' if minutes>=100 else 'insufficient')
            with patch('scheduler.policies.datetime') as dt:
                dt.now.return_value=NOW
                dt.fromisoformat=datetime.fromisoformat
                self.assertEqual(choose({},data)['route'],'hosted' if minutes>=100 else 'windows')

    def test_supported_report_drives_real_coordinator_route_at_boundary(self):
        for minutes in (99,100,101):
            ports=MemoryPorts()
            ports.audit_value['billing']={'chaonpu':reported(minutes)}
            start(ports,'stock_us',DAY,environment(),clock=Clock())
            self.assertEqual(ports.dispatches[0][2]['inputs']['runner_mode'],
                             'ubuntu' if minutes>=100 else 'windows')

    def test_unknown_never_switches_to_windows_even_when_online(self):
        ports=MemoryPorts()
        ports.audit_value['billing']={}
        with self.assertRaisesRegex(RegistryGuard,'no_verified_route'):
            start(ports,'stock_us',DAY,environment(),clock=Clock())
        self.assertEqual(ports.dispatches,[])
        current=datetime.now(timezone.utc)
        spec=ports.config['tasks']['stock_us']
        spec['windowsWorkflow']='run-us.yml'
        self.assertEqual(decide('stock_us',spec,{},ports.audit_value['runners']).route,'deferred')
        self.assertEqual(choose({}, {})['route'],'hold')

    def test_android_unknown_quota_stops_before_private_dispatch_or_reservation(self):
        from scheduler import android_production as build
        config=MemoryPorts().config
        def document(token,path):
            return (config,'blob') if path==build.CONFIG_PATH else ({'version':1,'builds':{}},'blob')
        with patch.object(build,'json_file',side_effect=document),\
             patch.object(build,'current_source',return_value='a'*40),\
             patch.object(build,'account_usage',return_value={}),\
             patch.object(build,'reserve') as reserve, patch.object(build,'dispatch') as dispatch:
            with self.assertRaisesRegex(build.Hold,'allowance_unverified_dispatch_paused'):
                build.run({'GH_TOKEN':'fake'},execute=True)
            reserve.assert_not_called()
            dispatch.assert_not_called()

    def test_stale_or_unsupported_estimates_remain_unknown(self):
        for change in ('stale','wrong_period','public_not_excluded','unsupported','boolean','raw_usage_only'):
            data=reported(100)
            if change=='stale':data['observedAt']=(NOW-timedelta(seconds=301)).isoformat()
            if change=='wrong_period':data['allowanceEstimate']['period']='2026-09'
            if change=='public_not_excluded':data['allowanceEstimate']['publicStandardUsageExcluded']=False
            if change=='unsupported':data['allowanceEstimate']['conversionSupported']=False
            if change=='boolean':data['allowanceEstimate']['remainingMinutesEstimate']=True
            if change=='raw_usage_only':data.pop('allowanceEstimate')
            self.assertEqual(hosted_capacity(data,{},now=NOW),'unknown')


if __name__=='__main__':unittest.main()
