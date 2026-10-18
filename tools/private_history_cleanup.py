"""Remove completed runs only when their workflow file is absent from private main."""
import argparse
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

ROOT = 'chaonpu/Runner'
WORKFLOW = ROOT + '/.github/workflows/private-history-cleanup.yml@refs/heads/main'
LIMIT = 500
REPOSITORIES = {
    'chaonpu/Stock': {
        'actions': 'CHAONPU_ACTIONS_READ_TOKEN', 'source': 'CHAONPU_ACTIONS_READ_TOKEN',
        'keep': {'run-a-share.yml', 'run-us.yml', 'production-readiness-a.yml',
                 'production-readiness-us.yml', 'production-readiness-cloud-a.yml',
                 'production-readiness-cloud-us.yml'},
    },
    'anwpustudent-lab/Test': {
        'actions': 'TEST_ACTIONS_READ_TOKEN', 'source': 'TEST_SOURCE_READ_TOKEN',
        'keep': {'test-self-hosted.yml', 'android-cloud-verified.yml'},
    },
    'chaonpu/ziwuxilv-android': {
        'actions': 'CHAONPU_ACTIONS_READ_TOKEN', 'source': 'CHAONPU_ACTIONS_READ_TOKEN',
        'keep': {'windows-signing-verified-handoff.yml', 'cloud-signing-verified-handoff.yml'},
    },
}
PATH = re.compile(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')


class Stop(Exception):
    pass


def require(value, reason):
    if not value:
        raise Stop(reason)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_context(env, execute):
    event = env.get('GITHUB_EVENT_NAME')
    require(env.get('GITHUB_REPOSITORY') == ROOT
            and env.get('GITHUB_REF') == 'refs/heads/main'
            and env.get('GITHUB_WORKFLOW_REF') == WORKFLOW
            and env.get('GITHUB_ACTOR') == 'chaonpu', 'untrusted_context')
    # A temporary push trigger is used only for deployment's read-only acceptance.
    require(event in ('schedule', 'workflow_dispatch') or (event == 'push' and not execute),
            'untrusted_event')


def permitted(repo, path, method):
    if repo not in REPOSITORIES:
        return False
    if method == 'DELETE':
        return bool(re.fullmatch(r'/actions/runs/[1-9][0-9]*', path))
    if method != 'GET':
        return False
    return (path == '/git/ref/heads/main'
            or bool(re.fullmatch(r'/contents/\.github/workflows\?ref=[0-9a-f]{40}', path))
            or bool(re.fullmatch(r'/actions/runs\?per_page=100&page=[1-9][0-9]*', path))
            or bool(re.fullmatch(r'/actions/runs/[1-9][0-9]*', path)))


def api(token, repo, path, method='GET'):
    require(permitted(repo, path, method), 'endpoint_forbidden')
    require(bool(token), 'credential_missing')
    request = urllib.request.Request('https://api.github.com/repos/' + repo + path,
        method=method, headers={
            'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'runner-private-history-cleanup',
            'Cache-Control': 'no-cache',
        })
    for attempt in range(3):
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
                if method == 'DELETE':
                    require(response.status == 204, 'delete_unconfirmed')
                    return {}
                raw = response.read(8_000_001)
                require(len(raw) <= 8_000_000, 'response_too_large')
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeError):
                    raise Stop('invalid_api_json') from None
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if error.code in (429, 502, 503) and attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            raise Stop('github_http_' + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise Stop('github_transport_unavailable') from None
            time.sleep(5 * (attempt + 1))
    raise Stop('retry_limit')


def main_head(reader, repo):
    ref = reader(repo, '/git/ref/heads/main')
    sha = ref.get('object', {}).get('sha') if isinstance(ref, dict) else None
    require(isinstance(sha, str) and SHA.fullmatch(sha), 'source_head_unverified')
    return sha


def workflow_inventory(reader, repo):
    head = main_head(reader, repo)
    files = reader(repo, '/contents/.github/workflows?ref=' + head)
    # A verified readable main ref precedes this possible missing-directory 404.
    require(files is None or (isinstance(files, list) and len(files) < 1000),
            'workflow_directory_unverified')
    current = set()
    for item in files or []:
        require(isinstance(item, dict) and isinstance(item.get('path'), str),
                'workflow_entry_invalid')
        if PATH.fullmatch(item['path']):
            # Symlinks and ambiguous entries cannot authorize deletion.
            require(item.get('type') == 'file', 'workflow_entry_not_file')
            current.add(item['path'])
    return head, current


def run_snapshot(reader, repo):
    runs, seen = [], set()
    for page in range(1, 101):
        data = reader(repo, '/actions/runs?per_page=100&page=' + str(page))
        require(isinstance(data, dict) and isinstance(data.get('workflow_runs'), list),
                'run_inventory_unverified')
        for run in data['workflow_runs']:
            require(isinstance(run, dict) and type(run.get('id')) is int, 'run_inventory_invalid')
            if run['id'] not in seen:
                runs.append(run)
                seen.add(run['id'])
        if len(data['workflow_runs']) < 100:
            return runs
    raise Stop('run_inventory_page_limit')


def eligible(run, current, keep):
    path = run.get('path', '')
    return (run.get('status') == 'completed' and isinstance(path, str)
            and bool(PATH.fullmatch(path)) and path not in current
            and path.rsplit('/', 1)[-1] not in keep)


def clean_repository(repo, env, execute, call=api):
    spec = REPOSITORIES[repo]
    source = lambda r, p: call(env.get(spec['source'], ''), r, p)
    actions = lambda r, p, method='GET': call(env.get(spec['actions'], ''), r, p, method)
    row = {'repository': repo, 'status': 'running', 'scanned': 0, 'candidates': 0,
           'deleted': 0, 'alreadyAbsent': 0, 'skippedAfterRecheck': 0, 'deferredByLimit': 0}
    try:
        head, current = workflow_inventory(source, repo)
        runs = run_snapshot(actions, repo)  # Snapshot every page before any deletion.
        row['scanned'] = len(runs)
        selected = [r for r in runs if eligible(r, current, spec['keep'])]
        row['candidates'] = len(selected)
        if execute:
            row['deferredByLimit'] = max(0, len(selected) - LIMIT)
            for expected in selected[:LIMIT]:
                path = '/actions/runs/' + str(expected['id'])
                live = actions(repo, path)
                if live is None:
                    row['alreadyAbsent'] += 1
                    continue
                require(live.get('id') == expected['id']
                        and live.get('workflow_id') == expected.get('workflow_id')
                        and live.get('path') == expected.get('path')
                        and live.get('repository', {}).get('full_name') == repo,
                        'run_identity_mismatch')
                if not eligible(live, current, spec['keep']):
                    row['skippedAfterRecheck'] += 1
                    continue
                # Any main update requires a fresh file inventory on the next run.
                require(main_head(source, repo) == head, 'source_changed_cleanup_paused')
                actions(repo, path, 'DELETE')
                require(actions(repo, path) is None, 'deleted_run_still_present')
                row['deleted'] += 1
                time.sleep(0.5)
        row['status'] = 'completed' if execute else 'preview'
    except Stop as error:
        row.update(status='incomplete', reason=str(error))
    except Exception:
        row.update(status='incomplete', reason='cleanup_stopped')
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    env = dict(os.environ)
    validate_context(env, args.execute)
    rows = []
    try:
        for repo in REPOSITORIES:
            row = clean_repository(repo, env, args.execute)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        if env.get('GITHUB_STEP_SUMMARY'):
            with open(env['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as out:
                out.write('### 三个私有仓库历史清理\n\n'
                          '仅清理 main 已移除工作流的已完成运行；保留现用流程及固定保护名单。\n\n'
                          '| 仓库 | 状态 | 扫描 | 待清理 | 已删除 | 延后 |\n'
                          '|---|---|---:|---:|---:|---:|\n')
                for row in rows:
                    out.write('| {repository} | {status} | {scanned} | {candidates} | {deleted} | {deferredByLimit} |\n'.format(**row))
                out.write('\n本次模式：' + ('执行' if args.execute else '只读预览') +
                          '。每仓每次最多删除 500 条，余量留至下次。\n')
        Path('private-history-cleanup-result.json').write_text(
            json.dumps({'execute': args.execute, 'repositories': rows}, indent=2) + '\n', encoding='utf-8')
    return int(any(r['status'] == 'incomplete' for r in rows))


if __name__ == '__main__':
    raise SystemExit(main())
