import unittest

from scheduler.pilot_monitor import classify,inspect
from scheduler.pilot_registry import reserve

def record():
    d=reserve({"schema":1,"tasks":[]},"2026-10-17",123456789,"2026-10-17T00:00:00+00:00")
    d["tasks"][0]["runId"]=777
    d["tasks"][0]["status"]="dispatched"
    return d

def run(status="completed",conclusion="success"):
    return {"id":777,"repository":{"full_name":"chaonpu/Stock"},
            "path":".github/workflows/runner-dispatch-canary.yml@main",
            "event":"workflow_dispatch","status":status,"conclusion":conclusion}

class FakeAPI:
    def __init__(self,token):
        self.token=token
    def get(self,path):
        if path!="/repos/chaonpu/Stock/actions/runs/777":
            raise AssertionError("Unsafe API target")
        return run()

class MonitorNoopTests(unittest.TestCase):
    def test_success_is_not_business_verification(self):
        state,reason=classify(record()["tasks"][0],run())
        self.assertEqual(state,"pilot_completed")
        self.assertEqual(reason,"no_business_side_effects")

    def test_failed_or_queued_not_retried(self):
        row=record()["tasks"][0]
        self.assertEqual(classify(row,run("queued",None))[0],"queued")
        self.assertEqual(classify(row,run("completed","failure"))[0],"hold")

    def test_wrong_workflow_fails_closed(self):
        row=record()["tasks"][0]
        bad=run()
        bad["path"]=".github/workflows/run-us.yml@main"
        self.assertEqual(classify(row,bad)[0],"hold")

    def test_missing_run_id_stays_hold(self):
        s=record()
        del s["tasks"][0]["runId"]
        s["tasks"][0]["status"]="accepted_without_run_id"
        r=inspect(s,"")
        self.assertEqual(r["results"][0]["state"],"hold")
        self.assertEqual(r["dispatched"],0)

    def test_read_only_monitor_can_trace_run_id(self):
        r=inspect(record(),"fake",api_factory=FakeAPI)
        self.assertEqual(r["results"][0]["state"],"pilot_completed")
        self.assertEqual(r["results"][0]["runId"],777)
        self.assertEqual(r["retried"],0)

if __name__=="__main__":
    unittest.main()
