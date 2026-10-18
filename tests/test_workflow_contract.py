"""Only four owner-operated manual workflows; never automatic business triggers."""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github/workflows'
MANUAL_ONLY = {'android-production.yml','android-publish.yml','production-dispatch.yml','stock-us.yml'}


def event_keys(text):
    match = re.search(r'(?m)^on:\s*$', text)
    if not match:
        raise ValueError('missing on mapping')
    body = re.split(r'(?m)^[A-Za-z][A-Za-z0-9_-]*:', text[match.end():], maxsplit=1)[0]
    return set(re.findall(r'(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):(?:\s|$)', body))


class WorkflowContractTests(unittest.TestCase):
    def test_exactly_four_manual_workflows(self):
        files = {p.name:p for p in WORKFLOWS.glob('*.yml')}
        self.assertEqual(set(files), MANUAL_ONLY)
        for path in files.values():
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
