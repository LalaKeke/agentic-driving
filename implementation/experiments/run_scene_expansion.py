"""Four fixed new scenes; serial, at most two requests each, no old budget."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.evidence_io import reserve_directory,write_json_new

def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main(plan_path):
    plan_path=plan_path.resolve();plan=read(plan_path);root=plan_path.parent
    assert plan['authorized_calls']==8 and plan['per_window_limit']==2
    assert plan['old_budget_inherited']==0 and plan['execution_authorized'] is True
    assert len(plan['cases'])==4 and len({c['scene_token'] for c in plan['cases']})==4
    assert read(root/'quota_preflight/result.json')['quota_observation']=='WINDOWS_BELOW_LIMIT_NOT_GENERATION_ENTITLEMENT_PROOF'
    runtime=reserve_directory(root/'runtime')
    deadline=datetime.fromisoformat(plan['deadline_utc']).timestamp()
    rows=[dict(case_id=c['case_id'],status='NOT_ATTEMPTED',charged_requests=0) for c in plan['cases']]
    stop=None;started=time.monotonic()
    for i,case in enumerate(plan['cases']):
        for name,want in plan['source_sha256'].items():assert sha(ROOT/name)==want,'Frozen source changed: '+name
        capture=ROOT/case['capture'];assert sha(capture)==case['capture_sha256']
        cap=read(capture)
        for frame in cap['observations']:assert sha(frame['path'])==case['image_sha256'][frame['id']]
        if time.time()>=deadline-100:
            stop='WALL_LIMIT';break
        out=runtime/case['case_id']
        command=[str(ROOT/'.venv/bin/python'),str(ROOT/'implementation/experiments/run_local_prototype.py'),
            '--capture',str(capture),'--wire',str(ROOT/plan['wire_bundle']),
            '--planner-profile',plan['planner_profile'],'--max-requests','2','--timeout-s','450',
            '--deadline-utc',plan['deadline_utc'],'--out',str(out)]
        write_json_new(runtime/(case['case_id']+'.launch.json'),dict(command=command,started_at_unix=time.time()))
        with (runtime/(case['case_id']+'.console.txt')).open('x') as stream:
            process=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                                   timeout=max(1,deadline-time.time()))
        summary=read(out/'summary.json') if (out/'summary.json').is_file() else None
        count=len(list(out.glob('charge_*.json')))
        assert count<=2 and sum(r['charged_requests'] for r in rows)+count<=8
        rows[i].update(charged_requests=count,exit_code=process.returncode,
            status='CONDITIONAL_CHAIN_PASS' if summary and summary['prototype_conditional_success'] else 'NO_COMPLETE_CHAIN',
            native_outputs=len(list(out.glob('attempt_*/record.json'))),
            fatal=summary.get('fatal') if summary else 'MISSING_SUMMARY')
        write_json_new(runtime/(case['case_id']+'.completed.json'),rows[i])
        print(json.dumps(rows[i],ensure_ascii=False),flush=True)
        if not summary or summary.get('fatal') or process.returncode not in (0,3) or count==0:
            stop='SERVICE_INTERFACE_OR_EXECUTION_FAILURE';break
    charged=sum(r['charged_requests'] for r in rows)
    write_json_new(root/'batch_summary.json',dict(ended=True,charged_requests=charged,
        authorized_calls=8,remaining_unused=8-charged,old_budget_inherited=0,
        serial=True,automatic_transport_retries=0,cases=rows,stop_reason=stop,
        elapsed_s=time.monotonic()-started,actual_server_model_id=None))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',required=True,type=Path)
    main(parser.parse_args().plan)
