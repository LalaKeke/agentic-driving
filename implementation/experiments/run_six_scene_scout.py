"""Serial execution of the frozen six-window scout using the existing runner.

No prompt, perception, planner or physics implementation is changed here.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.evidence_io import reserve_directory,write_json_new

def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def validate_frozen(plan_path):
    plan=read(plan_path);base=plan_path.parent
    registration=base/'prepared_inputs/preparation.json'
    assert sha(registration)==plan['input_registration_sha256'],'Input registration mismatch'
    assert sha(base/plan['selection_path'])==plan['selection_sha256'],'Selection mismatch'
    prep=read(registration)
    for name,want in prep['source_pins'].items():assert sha(ROOT/name)==want,'Source changed: '+name
    assert len(plan['runtime_inputs'])==6
    assert len({c['scene_token'] for c in plan['runtime_inputs']})==6
    for case in plan['runtime_inputs']:
        path=base/plan['runtime_input_root']/case['capture'];cap=read(path)
        assert sha(path)==case['capture_sha256'],'Capture changed'
        manifest=read(path.parent/'input_manifest.json')
        assert len(cap['observations'])==10
        for obs in cap['observations']:assert sha(obs['path'])==manifest['image_sha256'][obs['id']],'Image changed'
    return plan

def main(activation_path):
    active=read(activation_path);root=activation_path.parent
    assert active['execution_authorized'] and active['authorized_calls']==12
    assert active['per_window_limit']==2 and active['old_budget_inherited']==0
    plan_path=ROOT/active['plan_path'];assert sha(plan_path)==active['plan_sha256']
    assert sha(__file__)==active['launcher_sha256'],'Launcher changed after activation'
    plan=validate_frozen(plan_path)
    quota=read(root/'quota_preflight/result.json')
    assert quota['quota_observation']=='WINDOWS_BELOW_LIMIT_NOT_GENERATION_ENTITLEMENT_PROOF','Quota unavailable/reached: no generation'
    runtime=reserve_directory(root/'runtime')
    progress=root/'progress.jsonl'
    with progress.open('x'):pass
    started=time.monotonic();deadline=datetime.fromisoformat(active['deadline_utc']).timestamp()
    rows=[dict(case_id=c['case_id'],scene_token=c['scene_token'],sample_token=c['sample_token'],status='NOT_ATTEMPTED',charged_requests=0) for c in plan['runtime_inputs']]
    stop=None
    for index,case in enumerate(plan['runtime_inputs']):
        validate_frozen(plan_path)
        charged=sum(len(list(runtime.glob(c['case_id']+'/charge_*.json'))) for c in plan['runtime_inputs'])
        if charged>=12 or time.time()>=deadline-100:
            stop='BUDGET_OR_WALL_LIMIT';break
        capture=plan_path.parent/plan['runtime_input_root']/case['capture'];out=runtime/case['case_id']
        cmd=[str(ROOT/'.venv/bin/python'),str(ROOT/'implementation/experiments/run_local_prototype.py'),
             '--capture',str(capture),'--wire',str(ROOT/plan['fixed']['wire_bundle']),
             '--planner-profile',plan['fixed']['planner_profile'],'--max-requests','2',
             '--timeout-s',str(plan['fixed']['timeout_per_request_s']),
             '--deadline-utc',active['deadline_utc'],'--out',str(out)]
        with progress.open('a') as stream:stream.write(json.dumps(dict(event='CASE_START',case_id=case['case_id'],at=datetime.now(timezone.utc).isoformat(),command=cmd))+'\n')
        start=time.monotonic()
        with (runtime/(case['case_id']+'.console.txt')).open('x') as console:
            process=subprocess.run(cmd,cwd=ROOT,stdout=console,stderr=subprocess.STDOUT,timeout=max(1,deadline-time.time()))
        summary=read(out/'summary.json') if (out/'summary.json').exists() else None
        count=len(list(out.glob('charge_*.json')))
        assert count<=2 and charged+count<=12,'Call budget violation'
        rows[index].update(status='CONDITIONAL_CHAIN_PASS' if summary and summary['prototype_conditional_success'] else 'NO_COMPLETE_CHAIN',
            charged_requests=count,exit_code=process.returncode,wall_s=time.monotonic()-start,summary_path=str((out/'summary.json').relative_to(root)),
            native_output_count=len(list(out.glob('attempt_*/record.json'))),fatal=summary.get('fatal') if summary else 'MISSING_SUMMARY')
        with progress.open('a') as stream:stream.write(json.dumps(dict(event='CASE_END',**rows[index]))+'\n')
        print(json.dumps(rows[index],ensure_ascii=False),flush=True)
        if summary is None or summary.get('fatal') or process.returncode not in (0,3):
            stop='SERVICE_INTERFACE_OR_EXECUTION_FAILURE';break
        if count==0:
            stop='NO_REQUEST_WALL_OR_PREFLIGHT_STOP';break
    result=dict(ended=True,launched=True,authorized_calls=12,charged_requests=sum(c['charged_requests'] for c in rows),
                remaining_unused=12-sum(c['charged_requests'] for c in rows),old_budget_inherited=0,
                maximum_parallel_requests=1,automatic_transport_retries=0,
                metadata_quota_requests=1,quota_generation_requests=0,cases=rows,
                all_six_processed=all(c['status']!='NOT_ATTEMPTED' for c in rows),stop_reason=stop,
                elapsed_s=time.monotonic()-started,actual_server_model_id=None,
                statement='Conditional single-window prototypes and simulated prefixes only. Internal HTTP attempts are not observable. No held-out validation calls.')
    write_json_new(root/'batch_summary.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['all_six_processed'] else 3

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--activation',required=True,type=Path)
    args=parser.parse_args();sys.exit(main(args.activation))
