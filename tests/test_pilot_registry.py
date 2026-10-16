import datetime as dt
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from scheduler.pilot_registry import (
    PilotGuard, key_for, perform, reserve, set_dispatch_result,
    validate_registry,
)

EMPTY={"schema":1,"description":"Public-safe pilot records","tasks":[]}
NOW="2026-10-17T00:00:00+00:00"
RUN=123456789

def env():
    return {
        "GITHUB_REPOSITORY":"chaonpu/Runner",
        "GITHUB_REF":"refs/heads/main",
        "GITHUB_EVENT_NAME":"workflow_dispatch",
        "GITHUB_ACTOR":"chaonpu",
        "GITHUB_RUN_ID":str(RUN),
        "GH_TOKEN":"fake-runner-token",
        "CHAONPU_ACTIONS_READ_TOKEN":"fake-private-token",
    }

class PilotRegistryTests(unittest.TestCase):
    def test_date_and_key_validate(self):
        self.assertEqual(key_for("2026-10-17"),"pilot:stock:2026-10-17")
        for value in ("2026-02-30","2026/10/17","2026-1-7","2026-00-00"):
            with self.assertRaises(PilotGuard):
                key_for(value)

    def test_reserved_before_dispatch_and_reject_duplicate(self):
        first=reserve(EMPTY,"2026-10-17",RUN,NOW)
        self.assertEqual(first["tasks"][0]["status"],"reserved")
        self.assertEqual(first["tasks"][0]["canary"],"runner-canary-123456789-stock")
        with self.assertRaisesRegex(PilotGuard,"pilot_already_reserved"):
            reserve(first,"2026-10-17",RUN+1,NOW)

    def test_registry_forbids_private_content_and_unknown_targets(self):
        item=reserve(EMPTY,"2026-10-17",RUN,NOW)
        item["tasks"][0]["holdings"]=["secret"]
        with self.assertRaisesRegex(PilotGuard,"invalid_registry_fields"):
            validate_registry(item)
        item=reserve(EMPTY,"2026-10-17",RUN,NOW)
        item["tasks"][0]["repo"]="chaonpu/ziwuxilv"
        with self.assertRaisesRegex(PilotGuard,"invalid_public_registry_target"):
            validate_registry(item)

    def test_dispatch_result_records_exact_run_id(self):
        state=reserve(EMPTY,"2026-10-17",RUN,NOW)
        next_state=set_dispatch_result(
            state,"2026-10-17",RUN,
            {"status":"accepted","dispatched":True,"runId":87654},NOW)
        self.assertEqual(next_state["tasks"][0]["runId"],87654)
        self.assertEqual(next_state["tasks"][0]["status"],"dispatched")
        with self.assertRaises(PilotGuard):
            set_dispatch_result(next_state,"2026-10-17",RUN,
                                {"dispatched":True,"runId":87655},NOW)

    def test_timeout_never_claims_safe_retry(self):
        state=reserve(EMPTY,"2026-10-17",RUN,NOW)
        status=set_dispatch_result(state,"2026-10-17",RUN,
                       {"status":"network_response_unknown","dispatched":"unknown"},NOW)
        self.assertEqual(status["tasks"][0]["status"],"dispatch_uncertain")
        with self.assertRaises(PilotGuard):
            reserve(status,"2026-10-17",RUN+10,NOW)

    def test_pre2026_accepted_without_run_id_is_not_success_proof(self):
        state=reserve(EMPTY,"2026-10-17",RUN,NOW)
        new=set_dispatch_result(state,"2026-10-17",RUN,
             {"status":"accepted_run_id_unavailable","dispatched":True},NOW)
        self.assertEqual(new["tasks"][0]["status"],"accepted_without_run_id")

    def test_wrong_source_run_cannot_rewrite_record(self):
        state=reserve(EMPTY,"2026-10-17",RUN,NOW)
        with self.assertRaisesRegex(PilotGuard,"pilot_reservation_mismatch"):
            set_dispatch_result(state,"2026-10-17",RUN+1,
                                {"dispatched":True,"runId":123},NOW)

    def test_no_dispatch_before_successful_reservation_write(self):
        def reject_write(*_):
            raise PilotGuard("github_registry_write_or_read_unconfirmed")
        with patch("scheduler.pilot_registry.load_state",return_value=(EMPTY,"a"*40)), \
             patch("scheduler.pilot_registry.save_state",side_effect=reject_write), \
             patch("scheduler.pilot_registry.run_canary") as sent:
            with self.assertRaises(PilotGuard):
                perform("2026-10-17",env())
            sent.assert_not_called()

    def test_full_fake_orchestration_persists_twice_and_no_business_actions(self):
        db=deepcopy(EMPTY)
        sha="a"*40
        saves=[]
        def load(_):
            return deepcopy(db),sha
        def save(_,state,expected_sha,message):
            nonlocal db
            self.assertEqual(expected_sha,sha)
            saves.append([x["status"] for x in state["tasks"]])
            db=deepcopy(state)
        with patch("scheduler.pilot_registry.load_state",side_effect=load), \
             patch("scheduler.pilot_registry.save_state",side_effect=save), \
             patch("scheduler.pilot_registry.run_canary",return_value={
                 "target":"stock","status":"accepted","dispatched":True,"runId":999
             }) as canary:
            result=perform("2026-10-17",env())
            canary.assert_called_once_with("stock",str(RUN),env())
            self.assertEqual(result["runId"],999)
            self.assertFalse(result["strategyExecuted"])
            self.assertEqual(saves,[["reserved"],["dispatched"]])
            with self.assertRaisesRegex(PilotGuard,"pilot_already_reserved"):
                perform("2026-10-17",env())
            canary.assert_called_once()

    def test_untrusted_actor_never_reads_or_dispatches(self):
        bad=env()
        bad["GITHUB_ACTOR"]="pull-request-user"
        with patch("scheduler.pilot_registry.load_state") as read:
            with self.assertRaisesRegex(PilotGuard,"actor_not_allowed"):
                perform("2026-10-17",bad)
            read.assert_not_called()

if __name__=="__main__":
    unittest.main()
