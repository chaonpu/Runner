"""Offline timing regression tests; does not call Actions APIs."""
import unittest
from scheduler.production_timing import summarize, seconds, collect


def step(name, begin=0, duration=8, *, conclusion="success"):
    def at(offset):
        return "2026-10-19T07:%02d:%02dZ" % (offset // 60, offset % 60)
    return {"name": name, "status": "completed", "conclusion": conclusion,
            "started_at": at(begin), "completed_at": at(begin + duration)}


class TimingTests(unittest.TestCase):
    def test_us_selection_and_git_route_delay_are_visible(self):
        result = summarize({"jobs": [{"steps": [
            step("Run US strategy with retry", duration=142),
            step("Prepare verified job-scoped GitHub route before Windows checkout", duration=236),
            step("Send US mail only after verified ledger publication", duration=9),
        ]}]})
        self.assertEqual(result["data_and_selection_combined"], 142)
        self.assertEqual(result["git_route_verification"], 236)
        self.assertEqual(result["mail_delivery"], 9)

    def test_known_stages_measured_without_inventing_internal_times(self):
        jobs = {"jobs": [{"steps": [
            step("Set up job", duration=15),
            step("Install dependencies", duration=45),
            step("Run A-share strategy with retry", duration=70),
            step("Publish verified snapshot and ledger to private database", duration=12),
            step("Send A-share mail only after verified ledger publication", duration=5),
            step("Verify and attest published ledger receipt", duration=2),
        ]}]}
        result = summarize(jobs)
        self.assertEqual(result["components_setup"], 15)
        self.assertEqual(result["dependency_install"], 45)
        self.assertEqual(result["data_and_selection_combined"], 70)
        self.assertEqual(result["ledger_validation_and_publish"], 12)
        self.assertEqual(result["mail_delivery"], 5)
        self.assertIsNone(result["market_quotes_seconds"])
        self.assertIsNone(result["industry_lookup_seconds"])
        self.assertIsNone(result["pure_selection_compute_seconds"])

    def test_failed_or_unavailable_steps_not_misreported(self):
        jobs = {"jobs": [{"steps": [
            step("Install dependencies", duration=10, conclusion="failure"),
            {"name": "Run A-share strategy with retry", "started_at": None},
        ]}]}
        self.assertEqual(summarize(jobs)["dependency_install"], 0)
        self.assertIsNone(seconds(jobs["jobs"][0]["steps"][1]))
        with self.assertRaises(ValueError):
            summarize({})

    def test_collection_reads_only_actions_job_metadata(self):
        endpoints = []
        class Reader:
            def get(self, path):
                endpoints.append(path)
                return {"jobs": [{"steps": [step("Install dependencies", duration=7)]}]}
        class Ports:
            def private_reader(self):
                return Reader()
        out = collect(Ports(), 90000000000, 90000000001)
        self.assertEqual(out["dependency_install"], 14)
        self.assertEqual(len(endpoints), 2)
        self.assertTrue(all("/actions/runs/" in x and "/jobs?" in x for x in endpoints))


if __name__ == "__main__":
    unittest.main()

