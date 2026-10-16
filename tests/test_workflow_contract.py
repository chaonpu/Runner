"""Enforce the no-periodic-polling, manual-only coordinator workflow contract."""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
MANUAL_ONLY = {
    "dispatch-canary.yml",
    "dispatch-extra-canary.yml",
    "reconcile.yml",
    "scheduler.yml",
    "stage-android-apk.yml",
    "stock-noop-pilot.yml",
}
VALIDATE = "validate.yml"


def event_keys(yaml_text):
    """Inspect root on: mapping keys without depending on a YAML library."""
    match = re.search(r"(?m)^on:\s*$", yaml_text)
    if not match:
        raise ValueError("missing root on mapping")
    body = yaml_text[match.end():]
    body = re.split(r"(?m)^[A-Za-z][A-Za-z0-9_-]*:\s*", body, maxsplit=1)[0]
    keys = re.findall(r"(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):(?:\s|$)", body)
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("missing or duplicate workflow event keys")
    return set(keys)


class WorkflowContractTests(unittest.TestCase):
    def test_only_validation_has_push_all_others_manual_only(self):
        files = {p.name: p for p in WORKFLOWS.glob("*.yml")}
        self.assertEqual(set(files), MANUAL_ONLY | {VALIDATE})
        for name, path in files.items():
            with self.subTest(workflow=name):
                actual = event_keys(path.read_text(encoding="utf-8"))
                wanted = {"workflow_dispatch", "push"} if name == VALIDATE else {"workflow_dispatch"}
                self.assertEqual(actual, wanted, "No scheduled, PR, or other automatic task trigger")

    def test_pilot_dispatch_watch_finalize_remain_same_job(self):
        src = (WORKFLOWS / "stock-noop-pilot.yml").read_text(encoding="utf-8")
        self.assertIn("github.actor == 'chaonpu'", src)
        self.assertIn("github.ref == 'refs/heads/main'", src)
        self.assertIn("cancel-in-progress: false", src)
        self.assertIn("scheduler.pilot_registry --date", src)
        self.assertIn("scheduler.foreground_watch --date", src)
        self.assertIn("scheduler.pilot_finalize --date", src)
        self.assertIn("PILOT_MODE: $" + "{{ inputs.mode }}", src)
        self.assertNotRegex(src, r"(?m)^\s*(schedule|cron):")

    def test_production_dispatch_stays_closed(self):
        config = json.loads((ROOT / "config/repositories.json").read_text(encoding="utf-8"))
        self.assertIs(config["dispatchEnabled"], False)
        self.assertTrue(config["tasks"])
        self.assertTrue(all(t["enabledForDispatch"] is False for t in config["tasks"].values()))
        self.assertNotIn("apk_publish", config["tasks"])
        self.assertNotIn("database_sync", config["tasks"])
        self.assertTrue(all(t["repo"] != "chaonpu/ziwuxilv" for t in config["tasks"].values()))

    def test_staging_stays_independently_approved(self):
        src = (WORKFLOWS / "stage-android-apk.yml").read_text(encoding="utf-8")
        self.assertIn("github.actor == 'chaonpu'", src)
        self.assertIn("github.ref == 'refs/heads/main'", src)
        self.assertIn("public_staging_approval:", src)
        self.assertNotRegex(src, r"(?m)^\s*(schedule|cron):")


if __name__ == "__main__":
    unittest.main()
