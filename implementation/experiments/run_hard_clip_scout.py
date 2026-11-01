"""Run three fixed hard clips serially through the existing API clip runner.

No model/physics implementation lives here. Semantic exit3 continues to the
next clip; a service/interface stop marker prevents subsequent new requests.
Use --detach to start the same runner in a separate session and record its PID.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.contracts import need
from agentic.evidence_io import write_json_new


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def observed_without_summary(folder):
    """Retain observed charges if an owned child exits before its summary."""
    charged=len(list((folder/'budget').glob('charge_*.json')))
    posts=sum(read(p).get('api_post_requests',0)
              for p in (folder/'transport').glob('attempt_*/attempt_summary.json'))
    return dict(charged_requests=charged,api_post_requests=posts,
                unknown_dispatch_attempts=max(0,charged-posts),
                accounting_basis='Observed public charge files and finalized transport summaries; unconfirmed dispatch is not a failed model response')


def collect_row(row,result,exit_code):
    cases=result['cases'];fatal=result.get('fatal');reason=result.get('stop_reason')
    row.update(status='STOPPED' if fatal or reason else 'FINISHED',exit_code=exit_code,
        charged_requests=result.get('charged_requests',0),api_post_requests=result.get('api_post_requests',0),
        conditional_chains=result.get('conditional_chain_passes',0),native_outputs=result.get('native_outputs',0),
        attempted_references=sum(c.get('charged_requests',0)>0 for c in cases),
        all_references_processed=result.get('all_references_processed',False),stop_reason=reason,fatal=fatal)


def validate_plan(plan_path):
    plan_path=Path(plan_path).resolve();plan=read(plan_path)
    need(plan['execution_authorized'] is True and plan['old_budget_inherited']==0,
         'HARD_CLIP_AUTHORIZATION','Use this new authorized batch, never an old remaining budget')
    need(plan['max_parallel_clips']==1 and plan['serial_within_clip'] is True
         and plan['serial'] is True and plan['planning_reference_count']==21,
         'HARD_CLIP_CONFIG','Exactly three serial clips and twenty-one fixed references')
    need(len(plan['clips'])==3 and plan['operational_call_ceiling']==42
         and plan['wall_limit_s']==7200 and plan['per_window_limit']==2,
         'HARD_CLIP_CONFIG','Retain the frozen independent42-request /7200s operation cap')
    need([r['scene_name'] for r in plan['clips']]==['scene-0061','scene-1094','scene-0103'],
         'HARD_CLIP_SELECTION','Do not replace the fixed three scenes')
    required={'implementation/experiments/run_hard_clip_scout.py',
              'implementation/experiments/prepare_hard_clip_scout.py',
              'implementation/experiments/run_openai_clip.py',
              'implementation/agentic/openai_adapter.py',
              'implementation/agentic/t1.py','implementation/agentic/t2.py',
              'implementation/agentic/t3.py','implementation/agentic/t4.py'}
    need(required<=set(plan['source_sha256']),'HARD_CLIP_SOURCE','Required frozen source pins are missing')
    for name,expected in plan['source_sha256'].items():
        need(sha(ROOT/name)==expected,'HARD_CLIP_SOURCE','Frozen source changed; no child started',path=name)
    for clip in plan['clips']:
        child=ROOT/clip['plan'];need(sha(child)==clip['plan_sha256'],'HARD_CLIP_PLAN','Child plan bytes changed')
        value=read(child)
        need(len(value['cases'])==7 and value['operational_call_ceiling']==14
             and value['deadline_utc']==plan['deadline_utc'],
             'HARD_CLIP_PLAN','Child count/budget/deadline differs from its fixed parent')
    return plan_path,plan


def detach(plan_path):
    plan_path,plan=validate_plan(plan_path);root=plan_path.parent
    need(os.environ.get('DRIVING_MODEL')==plan['requested_model'],'HARD_CLIP_MODEL',
         'Set the registered DRIVING_MODEL before launching the detached batch')
    need(not(root/'launch.json').exists() and not(root/'detached_job.json').exists(),
         'HARD_CLIP_ALREADY_STARTED','Existing run must not be restarted or have its budget reset')
    command=[str(ROOT/'.venv/bin/python'),str(Path(__file__).resolve()),'--plan',str(plan_path)]
    with (root/'detached_console.txt').open('x') as log:
        process=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,
                                 stderr=subprocess.STDOUT,start_new_session=True)
    record=dict(pid=process.pid,command=command,start_new_session=True,
                started_at_unix=time.time(),credential_values_recorded=False,
                stop_marker=str(root/'STOP_GENERATION.json'),plan_sha256=sha(plan_path))
    write_json_new(root/'detached_job.json',record)
    print(json.dumps(record),flush=True)
    return 0


def main(plan_path):
    plan_path,plan=validate_plan(plan_path);root=plan_path.parent
    need(os.environ.get('DRIVING_MODEL')==plan['requested_model'],'HARD_CLIP_MODEL',
         'Explicit DRIVING_MODEL differs from the registered API model')
    deadline=datetime.fromisoformat(plan['deadline_utc'].replace('Z','+00:00')).timestamp()
    stop_file=root/'STOP_GENERATION.json';started=time.monotonic();active=None
    rows=[dict(clip_id=c['clip_id'],scene_name=c['scene_name'],status='NOT_ATTEMPTED',
               reference_count=7,charged_requests=0,api_post_requests=0,conditional_chains=0,
               attempted_references=0,native_outputs=0) for c in plan['clips']]
    def stop(reason,**extra):
        if not stop_file.exists():
            try:write_json_new(stop_file,dict(reason=reason,at_unix=time.time(),**extra))
            except Exception:
                if not stop_file.exists():raise
    def interrupted(number,_):
        stop('PARENT_SIGNAL_RECEIVED',signal_number=number)
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    for clip in plan['clips']:
        child_root=(ROOT/clip['plan']).parent
        need(not any((child_root/name).exists() for name in ('runtime','budget','transport','config.json','batch_summary.json')),
             'HARD_CLIP_ALREADY_STARTED','A child plan has already started; do not reset its budget',clip_id=clip['clip_id'])
    write_json_new(root/'launch.json',dict(pid=os.getpid(),plan_sha256=sha(plan_path),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        started_at_unix=time.time(),max_parallel_clips=1,serial_within_clip=True,
        automatic_transport_retries=0,credential_values_recorded=False))
    parent_error=None
    try:
        for clip,row in zip(plan['clips'],rows):
            if stop_file.exists():break
            if time.time()>=deadline-100:
                stop('WALL_LIMIT_BEFORE_NEXT_CLIP');break
            validate_plan(plan_path)
            child_plan=ROOT/clip['plan'];folder=child_plan.parent
            command=[str(ROOT/'.venv/bin/python'),str(ROOT/'implementation/experiments/run_openai_clip.py'),
                     '--plan',str(child_plan),'--stop-file',str(stop_file)]
            with (root/(clip['clip_id']+'.console.txt')).open('x') as log:
                active=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
                row['status']='RUNNING'
                write_json_new(root/(clip['clip_id']+'.launch.json'),dict(pid=active.pid,command=command,
                    started_at_unix=time.time()))
                print(json.dumps(dict(event='CLIP_STARTED',clip_id=clip['clip_id'],pid=active.pid)),flush=True)
                while active.poll() is None:
                    if time.time()>=deadline:stop('WALL_LIMIT')
                    time.sleep(1)
                code=active.returncode;active=None
            summary_path=folder/'batch_summary.json';result=read(summary_path) if summary_path.is_file() else None
            if result is None:
                row.update(status='STOPPED',exit_code=code,stop_reason='MISSING_CLIP_SUMMARY')
                row.update(observed_without_summary(folder))
                stop('MISSING_CLIP_SUMMARY',clip_id=clip['clip_id'])
            else:
                fatal=result.get('fatal');reason=result.get('stop_reason')
                collect_row(row,result,code)
                # A normal exit3 means semantic/tool refusals, not service failure.
                if fatal or reason:stop(reason or 'SERVICE_INTERFACE_FAILURE',clip_id=clip['clip_id'])
                elif code not in (0,3):stop('UNEXPECTED_CHILD_EXIT',clip_id=clip['clip_id'],exit_code=code)
            write_json_new(root/(clip['clip_id']+'.completed.json'),row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
            if stop_file.exists():break
    except Exception as error:
        parent_error=dict(code='HARD_CLIP_PARENT_ERROR',exception_type=type(error).__name__)
        stop('LOCAL_PARENT_FAILURE',error=parent_error)
        # Do not start another child. A rare local parent exception after spawn
        # still waits for this owned child to flush its normal stopped summary.
        if active is not None:
            while active.poll() is None:time.sleep(1)
    finally:
        # A local exception after spawning must not erase that child's charges.
        for clip,row in zip(plan['clips'],rows):
            if row['status']!='RUNNING':continue
            folder=(ROOT/clip['plan']).parent;summary_path=folder/'batch_summary.json'
            if summary_path.is_file():collect_row(row,read(summary_path),active.returncode if active else None)
            else:row.update(status='STOPPED',stop_reason='MISSING_CLIP_SUMMARY',**observed_without_summary(folder))
        total=sum(r['charged_requests'] for r in rows)
        need(total<=42,'HARD_CLIP_ACCOUNTING','Independent operation cap exceeded')
        write_json_new(root/'batch_summary.json',dict(ended=True,clips=rows,unique_scenes=3,
            planning_reference_count=21,attempted_reference_count=sum(r['attempted_references'] for r in rows),
            conditional_chain_passes=sum(r['conditional_chains'] for r in rows),charged_requests=total,
            api_post_requests=sum(r['api_post_requests'] for r in rows),operational_call_ceiling=42,
            old_budget_inherited=0,max_parallel_clips=1,serial_within_clip=True,
            automatic_transport_retries=0,elapsed_s=time.monotonic()-started,
            deadline_utc=plan['deadline_utc'],stop=read(stop_file) if stop_file.exists() else None,
            parent_error=parent_error,recording_boundary=plan['recording_boundary']))
    return 0 if not stop_file.exists() and all(r['conditional_chains']==7 for r in rows) else 3


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',required=True,type=Path)
    parser.add_argument('--detach',action='store_true',help='Start this batch in a new session; no requests in the launcher process')
    args=parser.parse_args()
    sys.exit(detach(args.plan) if args.detach else main(args.plan))
