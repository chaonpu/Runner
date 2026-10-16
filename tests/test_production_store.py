import base64
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from scheduler.production_registry import reserve, bind_run, RegistryGuard
from scheduler.production_store import load, save, verify_change, CONTENTS_PATH

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-17T07:00:00+08:00"
LATER = "2026-10-17T07:01:00+08:00"
RUN = 38967023305
SHA = "a" * 40


def config(enabled=True):
    obj = json.loads((ROOT / "config/repositories.json").read_text(encoding="utf-8"))
    obj["dispatchEnabled"] = enabled
    obj["tasks"]["stock_a"]["enabledForDispatch"] = enabled
    return obj


def empty():
    return {"schema": 1, "description": "Public-safe state only", "tasks": []}


def context():
    return {
        "GITHUB_REPOSITORY": "chaonpu/Runner",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_ACTOR": "chaonpu",
        "GITHUB_RUN_ID": str(RUN),
        "GH_TOKEN": "fake-for-mock-only",
    }


def reserved():
    return reserve(config(), empty(), "stock_a", route="cloud",
                   coordinator_run_id=RUN, at=NOW, business_date="2026-10-19")


class ProductionStoreTests(unittest.TestCase):
    def test_load_preserves_existing_file_and_sha(self):
        data = empty()
        record = {"type": "file", "encoding": "base64", "sha": SHA,
                  "content": base64.b64encode(json.dumps(data).encode()).decode()}
        caller = Mock(return_value=record)
        result, sha = load(config(False), "mock", caller)
        self.assertEqual(result, data)
        self.assertEqual(sha, SHA)
        self.assertEqual(caller.call_args.args[1:], ("GET", CONTENTS_PATH + "?ref=main"))

    def test_load_rejects_non_base64_or_wrong_blob_sha(self):
        payload = {"type": "file", "encoding": "base64", "sha": "invalid",
                   "content": base64.b64encode(json.dumps(empty()).encode()).decode()}
        with self.assertRaises(RegistryGuard):
            load(config(), "mock", Mock(return_value=payload))

    def test_cas_reservation_uses_original_sha_without_dispatch(self):
        before = empty()
        after = reserved()
        sent = []
        def api(token, method, path, body):
            sent.append((token, method, path, body))
            return {"content": {"sha": "b" * 40}}
        value = save(config(), before, after, SHA, context(), api)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1:3], ("PUT", CONTENTS_PATH))
        self.assertEqual(sent[0][3]["sha"], SHA)
        self.assertEqual(json.loads(base64.b64decode(sent[0][3]["content"])), after)
        self.assertEqual((value["dispatched"], value["verified"]), (False, False))

    def test_config_disabled_blocks_write_even_if_valid_object(self):
        caller = Mock()
        with self.assertRaisesRegex(RegistryGuard, "production_dispatch_disabled"):
            save(config(False), empty(), reserved(), SHA, context(), caller)
        caller.assert_not_called()

    def test_wrong_context_blocks_before_api(self):
        for field, value in [
            ("GITHUB_ACTOR", "somebody"),
            ("GITHUB_REF", "refs/pull/1/merge"),
            ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REPOSITORY", "chaonpu/Stock"),
        ]:
            with self.subTest(field=field):
                ctx = context()
                ctx[field] = value
                api = Mock()
                with self.assertRaisesRegex(RegistryGuard, "untrusted"):
                    save(config(), empty(), reserved(), SHA, ctx, api)
                api.assert_not_called()

    def test_only_original_owner_can_finalize_and_run_binding_is_immutable(self):
        first = reserved()
        second = bind_run(config(), first, "stock_a:2026-10-19",
                          run_id=38967039826, at=LATER)
        self.assertEqual(verify_change(config(), first, second, RUN), "stock_a:2026-10-19")
        with self.assertRaisesRegex(RegistryGuard, "not_original_dispatch_owner"):
            verify_change(config(), first, second, RUN + 2)
        bad = copy.deepcopy(second)
        bad["tasks"][0]["runId"] += 1
        bad["tasks"][0]["updatedAt"] = "2026-10-17T07:02:00+08:00"
        with self.assertRaises(RegistryGuard):
            verify_change(config(), second, bad, RUN)

    def test_no_verified_write_without_private_effect_evidence(self):
        first = reserved()
        second = bind_run(config(), first, "stock_a:2026-10-19",
                          run_id=38967039826, at=LATER)
        next_state = copy.deepcopy(second)
        next_state["tasks"][0]["status"] = "verified"
        next_state["tasks"][0]["updatedAt"] = "2026-10-17T07:02:00+08:00"
        api = Mock()
        with self.assertRaisesRegex(RegistryGuard, "unsafe_registry_transition"):
            save(config(), second, next_state, SHA, context(), api)
        api.assert_not_called()

    def test_unexpected_api_response_does_not_retry(self):
        called = Mock(return_value={"message": "uncertain"})
        with self.assertRaisesRegex(RegistryGuard, "write_result_unconfirmed"):
            save(config(), empty(), reserved(), SHA, context(), called)
        called.assert_called_once()

    def test_cannot_mutate_two_tasks_or_delete_other_task(self):
        original = reserved()
        after = copy.deepcopy(original)
        extra = reserve(config(), original, "stock_a", route="cloud",
                        coordinator_run_id=RUN + 1, at=NOW,
                        business_date="2026-10-20")
        with self.assertRaisesRegex(RegistryGuard, "one_task"):
            verify_change(config(), original, empty(), RUN)
        mixed = copy.deepcopy(extra)
        mixed["tasks"][0]["updatedAt"] = LATER
        with self.assertRaisesRegex(RegistryGuard, "one_task"):
            verify_change(config(), original, mixed, RUN)

    def test_root_metadata_is_immutable_during_single_task_write(self):
        original = empty()
        after = reserved()
        after["description"] = "unexpected changed description"
        with self.assertRaisesRegex(RegistryGuard, "registry_root_metadata_immutable"):
            verify_change(config(), original, after, RUN)

    def test_no_unauthorized_timestamp_or_identity_changes(self):
        original = reserved()
        after = copy.deepcopy(original)
        after["tasks"][0]["status"] = "hold"
        with self.assertRaisesRegex(RegistryGuard, "timestamp_not_updated"):
            verify_change(config(), original, after, RUN)
        after["tasks"][0]["updatedAt"] = LATER
        after["tasks"][0]["workflow"] = "run-us.yml"
        with self.assertRaises(RegistryGuard):
            verify_change(config(), original, after, RUN)


if __name__ == "__main__":
    unittest.main()
