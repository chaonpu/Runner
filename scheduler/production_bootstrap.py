"""One reviewed human push may launch the manual coordinator in this repo."""
import json
import os
from pathlib import Path

from scheduler.production_ports import request, PUBLIC, need
from scheduler.exchange_schedule import evaluate


def main():
    env=os.environ
    need(env.get('GITHUB_REPOSITORY')=='chaonpu/Runner' and env.get('GITHUB_ACTOR')=='chaonpu'
         and env.get('GITHUB_EVENT_NAME')=='push' and env.get('GITHUB_REF')=='refs/heads/main'
         and env.get('GITHUB_RUN_ATTEMPT')=='1'
         and env.get('GITHUB_WORKFLOW_REF')=='chaonpu/Runner/.github/workflows/production-launch.yml@refs/heads/main',
         'launch_request_context_untrusted')
    body=json.loads(Path('config/launch-request.json').read_text())
    need(set(body)=={'enabled','requestId','task','businessDate','publicationMode'},'launch_request_schema_invalid')
    if body['enabled'] is False:
        print('{"launchRequest":"disabled","dispatches":0}')
        return
    need(body['enabled'] is True and body['task'] in ('stock_a','stock_us')
         and body['publicationMode'] in ('formal','theoretical_catchup'),'launch_scope_invalid')
    evaluate(body['task'],body['businessDate'])
    response=request(env['GH_TOKEN'],'POST',PUBLIC+'/actions/workflows/production-dispatch.yml/dispatches',{
        'ref':'main','return_run_details':True,'inputs':{'task':body['task'],
        'business_date':body['businessDate'],'publication_mode':body['publicationMode'],
        'execute':True,'bootstrap_run_id':env['GITHUB_RUN_ID']}})
    need(type(response.get('workflow_run_id')) is int,'coordinator_dispatch_unknown_no_retry')
    print(json.dumps({'coordinatorRunId':response['workflow_run_id'],'requestId':body['requestId'],
                      'automaticRedispatch':False}),flush=True)


if __name__=='__main__': main()
