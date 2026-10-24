"""Four manual business entries plus bounded maintenance and read-only health checks."""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github/workflows'
MANUAL_ONLY = {'android-production.yml','android-publish.yml','production-dispatch.yml','stock-us.yml'}
MAINTENANCE_ONLY = {'private-history-cleanup.yml'}
HEALTH_ONLY = {'calendar-health.yml'}


def event_keys(text):
    match = re.search(r'(?m)^on:\s*$', text)
    if not match:
        raise ValueError('missing on mapping')
    body = re.split(r'(?m)^[A-Za-z][A-Za-z0-9_-]*:', text[match.end():], maxsplit=1)[0]
    return set(re.findall(r'(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):(?:\s|$)', body))


class WorkflowContractTests(unittest.TestCase):
    def test_exactly_four_manual_workflows(self):
        files = {p.name:p for p in WORKFLOWS.glob('*.yml')}
        # Maintenance and read-only calendar health are independent of the
        # four owner-initiated business workflows; reject unknown entrypoints.
        self.assertEqual(set(files), MANUAL_ONLY | MAINTENANCE_ONLY | HEALTH_ONLY)
        for name in sorted(MANUAL_ONLY):
            path = files[name]
            text = path.read_text()
            self.assertEqual(event_keys(text), {'workflow_dispatch'})
            self.assertIn("github.actor == 'chaonpu'", text)
            self.assertIn("github.ref == 'refs/heads/main'", text)
            self.assertIn('cancel-in-progress: false', text)
            self.assertIn('ref: ${{ github.sha }}', text)
            if path.name == 'android-publish.yml':
                self.assertIn('release_mode:', text)
                self.assertIn('default: "预览"', text)
                self.assertNotIn('      execute:', text)
            else:
                self.assertIn('execute:', text)
                self.assertIn('default: false', text)
            self.assertNotIn('pull_request_target', text)
            self.assertNotIn('actions: write', text)

    def test_maintenance_schedule_and_permissions(self):
        text = (WORKFLOWS/'private-history-cleanup.yml').read_text()
        self.assertEqual(event_keys(text), {'schedule', 'workflow_dispatch'})
        self.assertIn("cron: '30 20 * * *'", text)
        self.assertIn("github.actor == 'chaonpu'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn('contents: read', text)
        self.assertNotIn('contents: write', text)
        self.assertNotIn('actions: write', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertIn('tools/private_history_cleanup.py', text)
        self.assertIn('test_private_history_cleanup.py', text)
        self.assertNotIn('scheduler.android_publish', text)
        self.assertNotIn('scheduler.production_coordinator', text)
        self.assertNotIn('scheduler.android_production', text)

    def test_calendar_health_is_read_only_and_cannot_dispatch_business(self):
        text = (WORKFLOWS/'calendar-health.yml').read_text()
        self.assertEqual(event_keys(text), {'schedule', 'workflow_dispatch', 'push'})
        self.assertIn("cron: '17 1 * * *'", text)
        self.assertIn('branches: [main]', text)
        self.assertIn("github.repository == 'chaonpu/Runner'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn('contents: read', text)
        self.assertIn('actions: read', text)
        self.assertNotRegex(text, r'(?m)^\s+[\w-]+:\s*write\s*$')
        self.assertNotIn('secrets.', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertIn('timeout-minutes: 5', text)
        self.assertIn('python3 -m scheduler.calendar_watchdog', text)
        self.assertIn('test_calendar_watchdog.py', text)
        for module in ('android_publish', 'production_coordinator', 'android_production'):
            self.assertNotIn('scheduler.' + module, text)
        self.assertNotIn('/dispatches', text)
        self.assertNotIn('gh workflow run', text)

    def test_only_maintenance_and_health_have_automatic_triggers(self):
        automatic = {
            path.name for path in WORKFLOWS.glob('*.yml')
            if event_keys(path.read_text()) - {'workflow_dispatch'}
        }
        self.assertEqual(automatic, MAINTENANCE_ONLY | HEALTH_ONLY)

    def test_strategy_market_and_cleanup(self):
        for name, task in (('production-dispatch.yml','stock_a'),('stock-us.yml','stock_us')):
            text = (WORKFLOWS/name).read_text()
            self.assertIn('TASK: '+task, text)
            self.assertNotIn('inputs.task', text)
            self.assertNotIn('bootstrap', text)
            self.assertIn('scheduler.production_coordinator', text)
            self.assertIn('scheduler.production_lease', text)
            self.assertIn('if: always() && inputs.execute == true', text)
            self.assertIn('group: runner-production-single-task', text)

    def test_publication_is_separate_and_no_source_checkout(self):
        text = (WORKFLOWS/'android-publish.yml').read_text()
        self.assertIn('scheduler.android_publish', text)
        inputs = text.split('    inputs:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(set(re.findall(r'(?m)^      (\w+):', inputs)), {'update_log', 'release_mode'})
        self.assertIn('type: choice', inputs)
        self.assertIn('          - "预览"', inputs)
        self.assertIn('          - "正式"', inputs)
        self.assertIn('RELEASE_MODE: ${{ inputs.release_mode }}', text)
        self.assertIn('--release-mode "$mode"', text)
        self.assertNotIn('inputs.mode', text)
        self.assertNotIn('inputs.source_sha', text)
        self.assertIn('CHAONPU_ACTIONS_READ_TOKEN: ${{ secrets.CHAONPU_ACTIONS_READ_TOKEN }}', text)
        self.assertNotIn('ZIWUXILV_ACTIONS_WRITE_TOKEN', text)
        self.assertNotIn('ANDROID_SIGNING_KEYSTORE', text)
        self.assertNotRegex(text, r'(?m)^\s+repository:')

    def test_production_dispatch_stays_closed_between_runs(self):
        config = json.loads((ROOT/'config/repositories.json').read_text())
        self.assertIs(config['dispatchEnabled'], False)
        self.assertTrue(all(t['enabledForDispatch'] is False for t in config['tasks'].values()))


if __name__ == '__main__':
    unittest.main()
