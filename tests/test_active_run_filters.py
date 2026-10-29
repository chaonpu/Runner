import unittest

from scheduler.probes import ACTIVE_RUN_STATES, existing_attempt


REPO = "chaonpu/Stock"
WORKFLOW = "run-a-share.yml"
SPEC = {"actionsSecret": "TOKEN"}
ENV = {"TOKEN": "test-read-only-token"}


def reader_factory(responses, seen):
    class Reader:
        def __init__(self, token):
            if token != ENV["TOKEN"]:
                raise AssertionError("unexpected synthetic token")
        def get(self, path):
            seen.append(path)
            status = path.split("?status=", 1)[1].split("&", 1)[0]
            value = responses.get(status, (0, []))
            if isinstance(value, Exception):
                raise value
            count, rows = value
            return {"total_count": count, "workflow_runs": rows}
    return Reader


class ExactStatusRunProbeTests(unittest.TestCase):
    def query(self, responses):
        paths = []
        outcome = existing_attempt(REPO, WORKFLOW, SPEC, ENV,
            reader_factory=reader_factory(responses, paths))
        return outcome, paths

    def test_all_five_filters_required_for_absence(self):
        r, paths = self.query({})
        self.assertEqual(r["status"], "no_active_run")
        self.assertEqual(r["read"], "readable")
        self.assertEqual(len(paths), 5)
        self.assertEqual({p.split("?status=")[1].split("&")[0] for p in paths},
                         set(ACTIVE_RUN_STATES))
        self.assertTrue(all(p.endswith("&per_page=1") for p in paths))

    def test_any_existing_active_status_blocks_dispatch(self):
        for status in ACTIVE_RUN_STATES:
            with self.subTest(status=status):
                r, paths = self.query({status: (1, [{"status": status}])})
                self.assertEqual(r["status"], "active")
                self.assertFalse(r["matchingTaskVerified"])
                self.assertLessEqual(len(paths), 5)

    def test_older_than_thirty_active_run_is_not_missed(self):
        r, _ = self.query({"waiting": (1, [{"status": "waiting", "id": 1001}])})
        self.assertEqual(r["status"], "active")

    def test_any_malformed_or_partial_status_probe_returns_unknown(self):
        cases = (
            (None, []), (-1, []), (True, []),
            (0, [{"status": "queued"}]),
            (1, []), (1, [{"status": "completed"}]),
        )
        for bad in cases:
            with self.subTest(bad=bad):
                r, _ = self.query({"pending": bad})
                self.assertEqual(r["status"], "unknown")
                self.assertNotEqual(r["status"], "no_active_run")

    def test_event_filter_excludes_push_only_regressions(self):
        paths = []
        reader = reader_factory({}, paths)
        result = existing_attempt(REPO, WORKFLOW, SPEC, ENV,
                                  event="workflow_dispatch",
                                  reader_factory=reader)
        self.assertEqual(result["status"], "no_active_run")
        self.assertEqual(len(paths), len(ACTIVE_RUN_STATES))
        self.assertTrue(all("&event=workflow_dispatch&per_page=1" in path
                            for path in paths))

    def test_event_filtered_manual_build_blocks_dispatch(self):
        paths = []
        responses = {"queued": (1, [{"status": "queued",
                                    "event": "workflow_dispatch"}])}
        result = existing_attempt(REPO, WORKFLOW, SPEC, ENV,
                                  event="workflow_dispatch",
                                  reader_factory=reader_factory(responses, paths))
        self.assertEqual(result["status"], "active")

    def test_event_filter_fails_closed_if_server_ignores_filter(self):
        paths = []
        responses = {"queued": (1, [{"status": "queued", "event": "push"}])}
        result = existing_attempt(REPO, WORKFLOW, SPEC, ENV,
                                  event="workflow_dispatch",
                                  reader_factory=reader_factory(responses, paths))
        self.assertEqual(result["status"], "unknown")
        self.assertNotEqual(result["status"], "no_active_run")

    def test_unsupported_event_filter_cannot_claim_no_active_runs(self):
        result = existing_attempt(REPO, WORKFLOW, SPEC, ENV,
                                  event="push",
                                  reader_factory=lambda _: self.fail("unexpected query"))
        self.assertEqual(result["status"], "unknown")

    def test_network_failure_must_hold_without_inference(self):
        r, _ = self.query({"requested": TimeoutError("synthetic unavailable")})
        self.assertEqual(r["status"], "unknown")
        self.assertEqual(r["read"], "network_unavailable")

    def test_missing_actions_secret_holds(self):
        result = existing_attempt(REPO, WORKFLOW, SPEC, {},
            reader_factory=lambda token: self.fail("must not query"))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["read"], "secret_missing")


if __name__ == "__main__":
    unittest.main()
