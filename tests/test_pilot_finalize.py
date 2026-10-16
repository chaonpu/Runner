import copy
import unittest
from unittest.mock import Mock

from scheduler.pilot_registry import reserve, PilotGuard
from scheduler.pilot_finalize import finalizable, plan_completion, finalize

def registry(status="dispatched"):
    data=reserve({"schema":1,"tasks":[]},"2026-10-17",38961235915,
                 "2026-10-16T20:18:09+00:00")
    data["tasks"][0].update({"runId":38961249562,"status":status})
    return data

def true_run(status="completed",conclusion="success"):
    return {"id":38961249562,"repository":{"full_name":"chaonpu/Stock"},
            "path":".github/workflows/runner-dispatch-canary.yml@main",
            "event":"workflow_dispatch","head_branch":"main",
            "status":status,"conclusion":conclusion}

def real_env():
    return {"GITHUB_REPOSITORY":"chaonpu/Runner",
            "GITHUB_REF":"refs/heads/main",
            "GITHUB_EVENT_NAME":"workflow_dispatch",
            "GH_TOKEN":"public-noop-token",
            "CHAONPU_ACTIONS_READ_TOKEN":"private-noop-token"}

class VerifyNoopFinalizeTests(unittest.TestCase):
    def test_exact_noop_run_is_finalizable(self):
        self.assertTrue(finalizable(registry()["tasks"][0],true_run()))

    def test_wrong_repo_workflow_event_branch_and_id_never_complete(self):
        row=registry()["tasks"][0]
        for name,value in (("id",999),("repository",{"full_name":"chaonpu/ziwuxilv"}),
                           ("path",".github/workflows/run-us.yml"),
                           ("event","push"),("head_branch","feature")):
            run=true_run()
            run[name]=value
            self.assertFalse(finalizable(row,run),msg=name)

    def test_queued_and_in_progress_not_finalized(self):
        for state,result in (("queued",None),("in_progress",None)):
            output,done=plan_completion(registry(),lambda _:true_run(state,result),"now")
            self.assertFalse(done)
            self.assertEqual(output["tasks"][0]["status"],"dispatched")

    def test_failed_noop_run_is_recorded_as_failure_never_success(self):
        output, done = plan_completion(registry(), lambda _: true_run("completed", "failure"), "now")
        self.assertEqual(done, ["pilot:stock:2026-10-17"])
        self.assertEqual(output["tasks"][0]["status"], "completed_failure")

    def test_disconnected_record_can_recover_once_run_is_finished(self):
        entry = registry()
        entry["tasks"][0]["status"] = "reconciliation_required"
        output, done = plan_completion(entry, lambda _: true_run(), "now")
        self.assertEqual(done, ["pilot:stock:2026-10-17"])
        self.assertEqual(output["tasks"][0]["status"], "completed_success")

    def test_already_finalized_not_changed(self):
        output,done=plan_completion(registry("completed_success"),
                                    lambda _:self.fail("Already done must not refetch"),"now")
        self.assertEqual(done,[])
        self.assertEqual(output["tasks"][0]["status"],"completed_success")

    def test_success_changes_only_noop_registry_status(self):
        output,done=plan_completion(registry(),lambda _:true_run(),"now")
        self.assertEqual(done,["pilot:stock:2026-10-17"])
        row=output["tasks"][0]
        self.assertEqual(row["status"],"completed_success")
        self.assertEqual(row["runId"],38961249562)
        self.assertEqual(row["coordinatorRunId"],38961235915)
        self.assertEqual(row["updatedAt"],"now")

    def test_api_failure_aborts_no_registry_write(self):
        save=Mock()
        class ErrorClient:
            def __init__(self,token): pass
            def get(self,path): raise OSError("Network temporarily unavailable")
        with self.assertRaises(OSError):
            finalize(real_env(),loader=lambda token:(registry(),"a"*40),
                     saver=save,client_factory=ErrorClient)
        save.assert_not_called()

    def test_only_successful_canary_causes_cas_write(self):
        saves=[]
        class Client:
            def __init__(self,token): self.token=token
            def get(self,path):
                self_path="/repos/chaonpu/Stock/actions/runs/38961249562"
                if path!=self_path: raise AssertionError("Forbidden endpoint")
                return true_run()
        def save(token,state,sha,msg):
            saves.append((sha,msg,copy.deepcopy(state)))
        result=finalize(real_env(),loader=lambda token:(registry(),"a"*40),
                        saver=save,client_factory=Client)
        self.assertEqual(result["updated"],1)
        self.assertEqual(result["dispatches"],0)
        self.assertEqual(len(saves),1)
        self.assertEqual(saves[0][0],"a"*40)
        self.assertEqual(saves[0][2]["tasks"][0]["status"],"completed_success")

    def test_date_scoped_finalization_does_not_touch_other_task(self):
        from scheduler.pilot_registry import reserve
        state = registry()
        state = reserve(state, '2026-10-18', 38961235916, 'now')
        state['tasks'][1]['runId'] = 38961249563
        state['tasks'][1]['status'] = 'dispatched'
        paths = []
        def getter(path):
            paths.append(path)
            return true_run()
        result, done = plan_completion(state, getter, 'now', only_date='2026-10-17')
        self.assertEqual(done, ['pilot:stock:2026-10-17'])
        self.assertEqual(len(paths), 1)
        self.assertEqual(result['tasks'][1]['status'], 'dispatched')

    def test_invalid_date_rejected_before_status_check(self):
        with self.assertRaises(PilotGuard):
            plan_completion(registry(), lambda _: self.fail('queried'),
                            'now', only_date='2026-02-30')
    def test_untrusted_context_rejected_before_read(self):
        for attr,value in (("GITHUB_REPOSITORY","chaonpu/ziwuxilv"),
                           ("GITHUB_REF","refs/pull/1/merge"),
                           ("GITHUB_EVENT_NAME","pull_request")):
            env=real_env()
            env[attr]=value
            loader=Mock()
            with self.assertRaisesRegex(PilotGuard,"untrusted_finalize_context"):
                finalize(env,loader=loader)
            loader.assert_not_called()

    def test_missing_token_does_not_read_registry(self):
        env=real_env()
        env.pop("CHAONPU_ACTIONS_READ_TOKEN")
        loader=Mock()
        with self.assertRaisesRegex(PilotGuard,"finalize_credentials_missing"):
            finalize(env,loader=loader)
        loader.assert_not_called()

if __name__=="__main__":
    unittest.main()
