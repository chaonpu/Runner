import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cleanup', ROOT / 'tools/private_history_cleanup.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
REPO = 'chaonpu/Stock'
OLD = '.github/workflows/retired.yml'
CURRENT = '.github/workflows/run-a-share.yml'


class Tests(unittest.TestCase):
    def run_item(self, path=OLD, status='completed', rid=123):
        return {'id': rid, 'path': path, 'status': status, 'workflow_id': 456,
                'repository': {'full_name': REPO}}

    def simulate(self, runs, files=(), execute=True, live=None, changed=False,
                 missing_registration=False, incomplete_after=False):
        self.calls, self.deleted = [], set()
        def api(token, repo, path, method='GET'):
            self.calls.append((path, method))
            if path.startswith('/actions/workflows?'):
                n = sum(p.startswith('/actions/workflows?') for p, _ in self.calls)
                if incomplete_after and n > 1:
                    return {'workflows': []}
                paths = set(files) | {'.github/workflows/' + p for p in c.REPOSITORIES[REPO]['keep']}
                if changed and n > 1:
                    paths.add(OLD)
                return {'workflows': [{'path': p, 'state': 'active'} for p in paths]}
            if path.startswith('/actions/workflows/'):
                if missing_registration:
                    return None
                return {'id': 456, 'path': OLD, 'state': 'deleted'}
            if '?' in path:
                return {'workflow_runs': runs}
            rid = int(path.rsplit('/', 1)[-1])
            if method == 'DELETE':
                self.deleted.add(rid)
                return {}
            if rid in self.deleted:
                return None
            return copy.deepcopy(live if live is not None else next(r for r in runs if r['id'] == rid))
        with patch.object(c.time, 'sleep'):
            return c.clean_repository(REPO, {'CHAONPU_ACTIONS_READ_TOKEN': 'fake'}, execute, api)

    def test_retired_completed_run_is_deleted_and_absence_verified(self):
        row = self.simulate([self.run_item()])
        self.assertEqual(row['deleted'], 1)
        self.assertEqual(self.calls[-2:], [('/actions/runs/123', 'DELETE'), ('/actions/runs/123', 'GET')])

    def test_current_file_and_fixed_protection_both_prevent_deletion(self):
        future = '.github/workflows/new-service.yml'
        row = self.simulate([self.run_item(CURRENT), self.run_item(future, rid=124)], files=[future])
        self.assertEqual(row['candidates'], 0)
        self.assertFalse(self.deleted)

    def test_running_queued_or_unrecognized_path_are_never_deleted(self):
        row = self.simulate([self.run_item(status='in_progress'), self.run_item(status='queued', rid=124),
                             self.run_item('external.yml', rid=125)])
        self.assertEqual(row['candidates'], 0)

    def test_preview_does_not_delete(self):
        row = self.simulate([self.run_item()], execute=False)
        self.assertEqual((row['status'], row['candidates'], row['deleted']), ('preview', 1, 0))
        self.assertFalse(self.deleted)

    def test_identity_mismatch_stops_before_delete(self):
        live = self.run_item()
        live['repository']['full_name'] = 'foreign/repo'
        row = self.simulate([self.run_item()], live=live)
        self.assertEqual(row['reason'], 'run_identity_mismatch')
        self.assertFalse(self.deleted)

    def test_recreated_workflow_stops_before_delete(self):
        row = self.simulate([self.run_item()], changed=True)
        self.assertEqual(row['skippedAfterRecheck'], 1)
        self.assertFalse(self.deleted)

    def test_status_change_is_skipped_before_delete(self):
        row = self.simulate([self.run_item()], live=self.run_item(status='in_progress'))
        self.assertEqual(row['skippedAfterRecheck'], 1)
        self.assertFalse(self.deleted)

    def test_endpoints_cannot_modify_source_artifacts_or_releases(self):
        for repo, path, method in [
            ('ZIWUXILV/database', '/actions/runs/123', 'DELETE'),
            (REPO, '/contents/file', 'DELETE'), (REPO, '/releases/123', 'DELETE'),
            (REPO, '/actions/artifacts/123', 'DELETE'), (REPO, '/actions/runs/123/cancel', 'POST'),
        ]:
            with self.assertRaisesRegex(c.Stop, 'endpoint_forbidden'):
                c.api('fake', repo, path, method)

    def test_unreadable_workflow_inventory_cannot_authorize_cleanup(self):
        def api(*args):
            return None
        row = c.clean_repository(REPO, {}, True, api)
        self.assertEqual(row['reason'], 'workflow_inventory_unverified')

    def test_incomplete_inventory_does_not_authorize_any_deletion(self):
        with self.assertRaisesRegex(c.Stop, 'protected_workflow_inventory_incomplete'):
            c.workflow_inventory(lambda *a: {'workflows': []}, REPO)

    def test_disabled_and_future_workflows_remain_protected(self):
        entries = [{'path': '.github/workflows/' + p, 'state': 'active'}
                   for p in c.REPOSITORIES[REPO]['keep']]
        entries += [{'path': OLD, 'state': 'disabled_manually'}]
        current = c.workflow_inventory(lambda *a: {'workflows': entries}, REPO)
        self.assertIn(OLD, current)

    def test_missing_registration_still_requires_complete_current_inventory(self):
        row = self.simulate([self.run_item()], missing_registration=True, incomplete_after=True)
        self.assertEqual(row['reason'], 'protected_workflow_inventory_incomplete')
        self.assertFalse(self.deleted)

    def test_push_can_only_preview(self):
        env = {'GITHUB_REPOSITORY': c.ROOT, 'GITHUB_REF': 'refs/heads/main',
               'GITHUB_WORKFLOW_REF': c.WORKFLOW, 'GITHUB_ACTOR': 'chaonpu', 'GITHUB_EVENT_NAME': 'push'}
        c.validate_context(env, False)
        with self.assertRaises(c.Stop):
            c.validate_context(env, True)

    def test_all_pages_are_snapshotted_before_deletion(self):
        calls = []
        def reader(repo, path):
            calls.append(path)
            page = int(path.rsplit('=', 1)[-1])
            return {'workflow_runs': [self.run_item(rid=i + 1) for i in range(100)] if page == 1
                    else [self.run_item(rid=101)]}
        self.assertEqual(len(c.run_snapshot(reader, REPO)), 101)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
