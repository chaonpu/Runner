"""Real adapter code against an in-memory HTTP model. Never contacts GitHub."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scheduler.production_ports import (
    ReadOnlyPorts, ProductionPorts, document, request, public_report, main, PUBLIC, PRIVATE,
)
from scheduler.production_registry import RegistryGuard, reserve, bind_run
from scheduler.production_session import start, observe
from test_production_session import environment, Clock, DAY, OWNER, TARGET, SOURCE
from test_production_evidence import fixtures, RID as EVIDENCE_RUN

ROOT = Path(__file__).resolve().parents[1]


def record(path, obj):
    raw = (json.dumps(obj, sort_keys=True) + "\n").encode()
    return {"type": "file", "path": path, "encoding": "base64",
        "sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
        "content": base64.b64encode(raw).decode()}


class Backend:
    def __init__(self):
        self.config = json.loads((ROOT / "config/repositories.json").read_text())
        self.config["dispatchEnabled"] = True
        self.config["tasks"]["stock_us"].update(enabledForDispatch=True, windowsWorkflow="run-us.yml")
        self.state = {"schema": 1, "tasks": []}
        self.calls, self.private_paths = [], []
        self.evidence, self.evidence_jobs = fixtures()
        self.target = {"id": TARGET, "repository": {"full_name": "chaonpu/Stock"},
            "path": ".github/workflows/run-us.yml", "event": "workflow_dispatch",
            "head_branch": "main", "head_sha": SOURCE, "run_attempt": 1,
            "status": "queued", "conclusion": None}
        self.post_timeout, self.put_lost, self.source_changed = False, False, False
        self.source_reads = 0

    def request(self, token, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path == PUBLIC + "/branches/main":
            return {"commit": {"sha": "c" * 40}}
        if method == "GET" and "/contents/" in path:
            ref = path.split("?ref=")[1]
            assert ref == "c" * 40, "public documents must share a pinned ref"
            name = path.split("/contents/")[1].split("?")[0]
            return record(name, self.config if name == "config/repositories.json" else self.state)
        if method == "PUT" and path == PUBLIC + "/contents/state/tasks.json":
            assert body["sha"] == record("state/tasks.json", self.state)["sha"]
            self.state = json.loads(base64.b64decode(body["content"]))
            if self.put_lost: raise TimeoutError("accepted PUT but response lost")
            return {"content": {"sha": record("state/tasks.json", self.state)["sha"]}}
        if method == "POST" and path == PRIVATE + "/actions/workflows/run-us.yml/dispatches":
            if self.post_timeout: raise TimeoutError("accepted POST but response lost")
            self.target["inputs"] = deepcopy(body["inputs"])
            return {"workflow_run_id": TARGET}
        raise AssertionError("unexpected API request")

    def reader(self, token):
        backend = self
        class Reader:
            def get(self, path):
                backend.private_paths.append(path)
                if path == PRIVATE + "/actions/workflows/production-readiness-us.yml/runs?event=workflow_dispatch&branch=main&per_page=1":
                    return deepcopy(backend.evidence)
                if path == PRIVATE + f"/actions/runs/{EVIDENCE_RUN}/jobs?filter=latest&per_page=100":
                    return deepcopy(backend.evidence_jobs)
                if path == PRIVATE + f"/actions/runs/{TARGET}": return deepcopy(backend.target)
                raise AssertionError("unexpected private metadata request")
        return Reader()


def env():
    return dict(environment(), GH_TOKEN="synthetic-public-token", CHAONPU_ACTIONS_READ_TOKEN="synthetic-private-token")


class PortsTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.clock = Backend(), Clock()

    def ports(self, cls=ProductionPorts, active=None):
        return cls(env(), requester=self.backend.request, reader_factory=self.backend.reader,
            bill=lambda *args: {"status": "exhausted", "independentlyVerified": True,
                "remainingFreeMinutes": 0, "period": "2026-10", "observedAt": "2026-10-17T06:00:00+00:00"},
            runner=lambda *args: {"name": "ziwuxilv-stock-win", "status": "online",
                                  "busy": False, "labelsVerified": True},
            active=active or (lambda *args: {"status": "no_active_run"}), clock=self.clock)

    def begin(self, ports): return start(ports, "stock_us", DAY, env(), clock=self.clock)

    def effects(self, method): return [c for c in self.backend.calls if c[0] == method]

    def test_real_adapter_reserve_dispatch_bind_observe_against_fake_http(self):
        ports = self.ports()
        session = self.begin(ports)
        self.assertEqual((len(self.effects("PUT")), len(self.effects("POST"))), (2, 1))
        self.backend.target.update(status="completed", conclusion="success")
        result = observe(ports, session, env(), clock=self.clock)
        self.assertEqual(result["status"], "awaiting_effect_verification")
        self.assertFalse(result["businessVerified"])
        self.assertEqual((len(self.effects("PUT")), len(self.effects("POST"))), (3, 1))
        self.assertTrue(all(p.startswith(PRIVATE + "/actions/") and not any(x in p for x in ("/contents/", "/artifacts", "/logs", "/git/"))
                            for p in self.backend.private_paths))

    def test_read_only_adapter_cannot_mutate_even_when_configuration_enabled(self):
        ports = self.ports(ReadOnlyPorts)
        cfg, state, _ = ports.load()
        audit = ports.audit(cfg, state, "stock_us", DAY)
        self.assertTrue(audit["marketDataReady"])
        with self.assertRaisesRegex(RegistryGuard, "read_only_ports_cannot_write"): ports.save()
        with self.assertRaisesRegex(RegistryGuard, "read_only_ports_cannot_dispatch"): ports.dispatch()
        self.assertEqual(self.effects("PUT") + self.effects("POST"), [])

    def test_current_disabled_live_configuration_never_writes(self):
        self.backend.config = json.loads((ROOT / "config/repositories.json").read_text())
        with self.assertRaises(RegistryGuard): self.begin(self.ports())
        self.assertEqual(self.effects("PUT") + self.effects("POST"), [])

    def test_missing_private_readiness_is_hold_despite_online_runner(self):
        self.backend.evidence.update(workflow_runs=[], total_count=0)
        with self.assertRaises(RegistryGuard): self.begin(self.ports())
        self.assertEqual(self.effects("PUT") + self.effects("POST"), [])

    def test_other_market_active_or_unknown_blocks_shared_ledger_start(self):
        for status in ("active", "unknown"):
            self.setUp()
            ports = self.ports(active=lambda repo, wf, spec, env: {
                "status": status if wf == "run-a-share.yml" else "no_active_run"})
            with self.assertRaises(RegistryGuard): self.begin(ports)
            self.assertEqual(self.effects("PUT") + self.effects("POST"), [])

    def test_changed_production_source_is_not_bound_before_private_admission(self):
        self.backend.target["head_sha"] = "d" * 40
        with self.assertRaises(RegistryGuard): self.begin(self.ports())
        self.assertEqual(len(self.effects("POST")), 1)
        self.assertEqual(self.backend.state["tasks"][0]["status"], "reserved")
        self.assertNotIn("runId", self.backend.state["tasks"][0])

    def test_accepted_reservation_put_with_lost_response_never_arms(self):
        self.backend.put_lost = True
        ports = self.ports()
        with self.assertRaises(RegistryGuard): self.begin(ports)
        self.assertEqual(self.backend.state["tasks"][0]["status"], "reserved")
        self.assertEqual(ports._armed, {})
        self.assertEqual(self.effects("POST"), [])
        with self.assertRaises(RegistryGuard): self.begin(self.ports())
        self.assertEqual(self.effects("POST"), [])

    def test_post_timeout_consumes_instance_arm_and_never_resends(self):
        self.backend.post_timeout = True
        ports = self.ports()
        with self.assertRaises(RegistryGuard): self.begin(ports)
        self.assertEqual(self.backend.state["tasks"][0]["status"], "reserved")
        self.assertEqual(ports._armed, {})
        with self.assertRaises(RegistryGuard): self.begin(self.ports())
        self.assertEqual(len(self.effects("POST")), 1)

    def test_new_instance_cannot_dispatch_a_preexisting_reservation(self):
        cfg = self.backend.config
        self.backend.state = reserve(cfg, self.backend.state, "stock_us", route="windows",
            coordinator_run_id=OWNER, at=self.clock().isoformat(), business_date=DAY)
        with self.assertRaisesRegex(RegistryGuard, "not_armed"):
            self.ports().dispatch("chaonpu/Stock", "run-us.yml", {
                "ref": "main", "return_run_details": True, "inputs": {
                    "task_id": "stock_us:"+DAY, "coordinator_run_id": str(OWNER),
                    "force_run": "false", "runner_mode": "windows"}})
        self.assertEqual(self.backend.calls, [])

    def test_direct_binding_without_original_dispatch_receipt_is_rejected(self):
        cfg = self.backend.config
        before = reserve(cfg, {"schema":1,"tasks":[]}, "stock_us", route="windows",
            coordinator_run_id=OWNER, at=self.clock().isoformat(), business_date=DAY)
        self.backend.state = deepcopy(before)
        after = bind_run(cfg, before, "stock_us:"+DAY, run_id=TARGET, at=self.clock().isoformat())
        sha = record("state/tasks.json", before)["sha"]
        with self.assertRaisesRegex(RegistryGuard, "original_dispatch_not_known"):
            self.ports().save(cfg, before, after, sha, env())
        self.assertEqual(self.effects("PUT") + self.effects("POST"), [])

    def test_run_reader_rejects_other_repo_or_unknown_id_before_any_get(self):
        ports = self.ports(ReadOnlyPorts)
        for repo, rid in (("other/repo", TARGET), ("chaonpu/Stock", True), ("chaonpu/Stock", 1)):
            with self.assertRaises(RegistryGuard): ports.run(repo, rid)
        self.assertEqual(self.backend.private_paths, [])

    def test_private_source_error_and_opaque_fields_never_enter_public_report(self):
        ports = self.ports(ReadOnlyPorts)
        cfg, state, _ = ports.load()
        class Forbidden:
            def get(self, path):
                from urllib.error import HTTPError
                raise HTTPError("private-url", 403, "secret-token-private-code", {}, None)
        ports.reader_factory = lambda token: Forbidden()
        audit = ports.audit(cfg, state, "stock_us", DAY)
        audit["private_payload"] = "secret-token-private-code"
        result = public_report(cfg, state, audit)
        self.assertEqual(result["evidenceStatus"], "hold_evidence_permission_denied")
        self.assertFalse(result["dispatchAuthorized"])
        self.assertNotIn("secret-token-private-code", json.dumps(result))

    def test_public_config_and_state_blob_hashes_are_checked(self):
        item = record("config/repositories.json", {"safe": True})
        self.assertEqual(document(item, "config/repositories.json")[0], {"safe": True})
        for changes in ({"sha": "a"*40}, {"path": "private/source.py"}, {"content": "?"}):
            with self.assertRaises(RegistryGuard):
                document(dict(item, **changes), "config/repositories.json")

    def test_low_level_allowlist_rejects_cancel_private_content_and_arbitrary_post(self):
        opener = Mock()
        for method, path in (("DELETE", PUBLIC+"/contents/state/tasks.json"),
                             ("GET", PRIVATE+"/contents/source.py?ref="+SOURCE),
                             ("POST", PRIVATE+f"/actions/runs/{TARGET}/cancel"),
                             ("POST", "/repos/chaonpu/ziwuxilv/actions/workflows/publish.yml/dispatches")):
            with self.assertRaises(RegistryGuard): request("synthetic", method, path, {}, opener=opener)
        opener.assert_not_called()

    def test_http_error_or_accepted_empty_post_response_is_not_retried(self):
        opener = Mock(side_effect=TimeoutError("sensitive"))
        with self.assertRaisesRegex(RegistryGuard, "response_uncertain_no_retry"):
            request("synthetic", "POST", PRIVATE+"/actions/workflows/run-us.yml/dispatches", {}, opener=opener)
        opener.assert_called_once()

    def test_all_retained_private_readiness_routes_reach_the_request_adapter(self):
        for workflow in ("production-readiness-a.yml", "production-readiness-us.yml",
                         "production-readiness-cloud-a.yml", "production-readiness-cloud-us.yml"):
            with self.subTest(workflow=workflow):
                # Reaching the opener proves the route passed the allowlist.
                opener = Mock(side_effect=TimeoutError("synthetic transport stop"))
                with self.assertRaisesRegex(RegistryGuard, "response_uncertain_no_retry"):
                    request("synthetic", "POST", PRIVATE+"/actions/workflows/"+workflow+"/dispatches",
                            {"ref":"main","inputs":{}}, opener=opener)
                opener.assert_called_once()

    def test_writer_cannot_be_constructed_from_push_or_other_workflow(self):
        for change in ({"GITHUB_EVENT_NAME":"push"}, {"GITHUB_WORKFLOW_REF":"other"},
                       {"GITHUB_RUN_ATTEMPT":"2"}):
            with self.assertRaises(RegistryGuard): ProductionPorts(dict(env(), **change))

    def test_command_line_always_uses_read_only_adapter_and_defaults_to_auto_date(self):
        ports = self.ports(ReadOnlyPorts)
        self.backend.config = json.loads((ROOT / "config/repositories.json").read_text())
        ctx = dict(env(), GITHUB_WORKFLOW_REF="chaonpu/Runner/.github/workflows/validate.yml@refs/heads/main",
                   GITHUB_EVENT_NAME="push")
        with patch("scheduler.production_ports.ReadOnlyPorts", return_value=ports) as read_factory, \
             patch("scheduler.production_ports.ProductionPorts") as write_factory, \
             patch.dict("os.environ", ctx, clear=True), \
             patch("sys.argv", ["production_ports", "--task", "stock_us"]), \
             patch("builtins.print") as output:
            self.assertEqual(main(), 0)
            write_factory.assert_not_called()
            read_factory.assert_called_once()
        result = json.loads(output.call_args.args[0])
        self.assertEqual(result["mode"], "live_read_only_production_ports")
        self.assertTrue(result["productionSwitchOff"])
        self.assertFalse(result["dispatchAuthorized"])
        self.assertEqual(self.effects("PUT") + self.effects("POST"), [])


if __name__ == "__main__":
    unittest.main()

