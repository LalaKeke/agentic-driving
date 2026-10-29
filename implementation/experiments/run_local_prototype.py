"""One local conditional real-image prototype; max four charged requests.

Existing physics and original business Schema are retained. --replay reuses an
explicit saved real response without a fresh model call; it is labelled replay.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic import t1,t2,t3,t4
from agentic.contracts import Failure,digest,need
from agentic.acceptance import assess_round
from agentic.evidence_io import reserve_directory,write_json_new
from agentic.pilot_transport import PilotSubscriptionAgent
from agentic.runtime import SimulatedExecutor

PROFILE='local_conditional_2s_prototype_v1'
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def body_feedback(output):
    """Only diagnose model geometry; never edit it or generate a prediction."""
    from shapely.geometry import Polygon
    from agentic.metric_contract import _body
    conflicts=[]
    for actor in output.get('hypotheses',{}).get('objects',[]):
        if actor.get('position_m') is None or actor.get('body_geometry') is None:continue
        body=_body(actor)
        for src in actor.get('occupancy') or []:
            if src['start_s']!=0:continue
            for alt in src['alternatives']:
                if not Polygon(alt['polygon_m']).covers(body):
                    conflicts.append(dict(object_id=actor['id'],source_id=src['id'],scenario_id=alt['scenario_id'],
                        computed_initial_body_corners_m=[list(x) for x in body.exterior.coords][:-1],
                        instruction='Model must enclose its complete oriented BODY. Round justified envelope coordinates outward with numerical room; uncertainty is not a substitute. No tool has changed this polygon.'))
    return conflicts


def physical(cap,out,prov,initial,revision,folder,policy='moving_seed',require_turn=False):
    data={};stages={k:'NOT_REACHED' for k in ('T1','T2','T3','T4','EXECUTION','ACCEPTOR')}
    failure=None
    tools=[('T1',lambda:t1.construct(cap,out,prov)),
        ('T2',lambda:t2.build(data['T1'],policy=policy)),
        ('T3',lambda:t3.solve(data['T1'],data['T2'])),
        ('T4',lambda:t4.verify(data['T1'],data['T3']))]
    if policy in (t2.SEARCH_PROFILE,t2.SEARCH_PROFILE_V2):tools=tools[:1]
    for name,fn in tools:
        try:
            data[name]=fn();write_json_new(folder/(name+'.json'),data[name]);stages[name]='PASS'
            if name=='T4' and data[name]['verdict']!='PASS':
                stages[name]='FAIL';failure=dict(code='VERIFICATION_REJECTED',violations=data[name]['violations'])
        except Failure as exc:
            failure=exc.record;stages[name]='FAIL';write_json_new(folder/(name+'-failure.json'),failure)
        if failure:
            write_json_new(folder/'stages.json',dict(stages=stages,failure=failure))
            return None,failure,stages
    if policy in (t2.SEARCH_PROFILE,t2.SEARCH_PROFILE_V2):
        from agentic.candidate_search import plan
        candidate_root=reserve_directory(folder/'candidates');candidate_dirs={}
        def save(candidate,name,value):
            if candidate not in candidate_dirs:candidate_dirs[candidate]=reserve_directory(candidate_root/candidate)
            write_json_new(candidate_dirs[candidate]/(name+'.json'),value)
        winner,ledger,failure=plan(data['T1'],save,profile=policy)
        write_json_new(folder/'candidate_ledger.json',ledger)
        if winner:
            data.update(winner)
            for name in ('T2','T3','T4'):write_json_new(folder/(name+'.json'),data[name]);stages[name]='PASS'
        else:
            for name in ('T2','T3','T4'):
                values=[r[name] for r in ledger['candidates']]
                stages[name]='PASS' if 'PASS' in values else ('FAIL' if 'FAIL' in values else 'NOT_REACHED')
            write_json_new(folder/'stages.json',dict(stages=stages,failure=failure))
            return None,failure,stages
    scene,corridor,traj,verdict=(data[n] for n in ('T1','T2','T3','T4'))
    turn_task=None
    if require_turn:
        from agentic.turn_task import evaluate_turn
        turn_task=evaluate_turn(scene,traj)
        write_json_new(folder/'turn_task.json',turn_task)
        stages['TURN_TASK']=turn_task['verdict']
        if turn_task['verdict']!='PASS':
            failure=dict(code='TURN_TASK_NOT_DEMONSTRATED',category='task_not_completed',details=turn_task,
                repair_request='This verified plan does not demonstrate the registered turning task. Use only actual image-supported local road and target geometry; never distort geometry or remove a threat to meet a numeric threshold. A necessary stop remains a valid safety choice but does not complete this turning demonstration.')
            write_json_new(folder/'stages.json',dict(stages=stages,failure=failure))
            return None,failure,stages
    executor=SimulatedExecutor();executor.observe(cap)
    execution=executor.execute(scene,traj,verdict,cap['metadata']['reference_time_s'])
    receipt=dict(capture_digest=digest(scene['capture']),scene_digest=digest(scene),trajectory_digest=digest(traj),
        verification_digest=digest(verdict),execution_digest=digest(execution),
        executed_at_s=cap['metadata']['reference_time_s'],consumed_once=digest(verdict) in executor.consumed)
    write_json_new(folder/'execution.json',execution);write_json_new(folder/'execution_receipt.json',receipt)
    result=dict(status='executed_simulated_prefix',revisions=revision,agent_output=out,provenance=prov,
        scene=scene,corridor=corridor,trajectory=traj,verification=verdict,execution=execution,execution_receipt=receipt,
        experiment_profile=PROFILE,claim='Local conditional prototype only; not old strict success, real vehicle safety, G2, or counterfactual multi-round observations.')
    if turn_task is not None:result['turn_task']=turn_task
    acceptance=assess_round(result,initial);result['acceptance']=acceptance
    result['prototype_conditional_success']=acceptance['strict_live_physical_success']
    stages['EXECUTION']='PASS';stages['ACCEPTOR']='PASS' if result['prototype_conditional_success'] else 'FAIL'
    write_json_new(folder/'acceptor.json',acceptance);write_json_new(folder/'stages.json',dict(stages=stages))
    return result,None,stages


def main(args):
    limit=getattr(args,'max_requests',4)
    planner=getattr(args,'planner_profile','legacy_disk_v2')
    require_turn=getattr(args,'require_turn',False)
    prompt=getattr(args,'prompt_version','driving_prototype_v1')
    if args.replay:prompt=read(args.replay)['provenance']['prompt_version']
    need(prompt in ('driving_prototype_v1','driving_prototype_v2'),'PROMPT_CONTRACT','Explicit prototype prompt version required')
    need(args.replay or prompt!='driving_prototype_v2' or planner==t2.SEARCH_PROFILE_V2,
         'PROTOTYPE_PLANNER','The corrected v2 prompt requires the actual v2 planner for online execution')
    need(planner in ('legacy_disk_v2','yaw_bounded_box_v1',t2.SEARCH_PROFILE,t2.SEARCH_PROFILE_V2),'PROTOTYPE_PLANNER','Explicit planner profile required')
    policy='moving_seed' if planner=='legacy_disk_v2' else planner
    need(type(limit) is int and 1<=limit<=4,'PROTOTYPE_BUDGET','Per-case request limit must be 1..4')
    root=reserve_directory(args.out);initial=read(args.capture)
    need(initial['metadata']['horizon_s']==2 and initial['metadata']['step_s']==.2 and initial['safety']['prefix_s']==.2,
         'PROTOTYPE_CONFIG','Separate 2s/.2s prototype only')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    source_files=[Path(__file__),ROOT/'implementation/agentic/pilot_transport.py',
        ROOT/'implementation/agentic/metric_contract.py',ROOT/('implementation/prompts/'+prompt+'.txt')]
    source_files += [ROOT/('implementation/agentic/'+name+'.py') for name in ('t1','t2','t3','t4','candidate_search','acceptance','geometry','runtime')]
    source_files += [ROOT/('implementation/schemas/'+name+'.schema.json') for name in ('corridor_seed_candidates_v1','trajectory_seed_candidates_v1','corridor_seed_candidates_v2','trajectory_seed_candidates_v2')]
    if require_turn:source_files.append(ROOT/'implementation/agentic/turn_task.py')
    write_json_new(root/'config.json',dict(profile=PROFILE,capture_path=str(args.capture),capture_sha256=sha(args.capture),
        wire_bundle=str(args.wire),source_commit=commit,max_requests=limit,deadline_utc=args.deadline_utc,planner_profile=planner,
        require_turn=require_turn,prompt_version=prompt,horizon_s=2,step_s=.2,prefix_s=.2,mode='FROZEN_REAL_OUTPUT_OFFLINE_REPLAY' if args.replay else 'FRESH_ONLINE_SINGLE_WINDOW',
        gt_used=False,source_worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
        actual_source_sha256={str(p.relative_to(ROOT)):sha(p) for p in source_files}))
    budget=[];feedback=read(args.initial_feedback) if getattr(args,'initial_feedback',None) else None
    if feedback is not None:write_json_new(root/'initial_feedback.json',feedback)
    result=None;rows=[];fatal=None
    if args.replay:
        record=read(args.replay);cap=record['capture'];out=record['output'];prov=record['provenance']
        folder=reserve_directory(root/'attempt_01');write_json_new(folder/'record.json',record)
        result,failure,stages=physical(cap,out,prov,cap,0,folder,policy=policy,**({'require_turn':True} if require_turn else {}))
        if result:
            result['fresh_online_model_call']=False;result['replay_of_real_response']=str(args.replay)
        rows.append(dict(attempt=1,source='OFFLINE_REPLAY_OF_REAL_MODEL',stages=stages,failure=failure))
    else:
        deadline=datetime.fromisoformat(args.deadline_utc.replace('Z','+00:00')).timestamp()
        for key in ('HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','https_proxy','http_proxy','all_proxy'):
            os.environ[key]='http://127.0.0.1:17891'
        agent=PilotSubscriptionAgent(evidence_dir=root/'transport',timeout_s=args.timeout_s,
            wire_schema_bundle=args.wire,prompt_version=prompt,max_calls=4,experiment_profile=PROFILE)
        try:
            agent.preflight()
            for revision in range(limit):
                remaining=deadline-time.time()
                if remaining<100:break
                agent.timeout=min(args.timeout_s,remaining-90)
                cap=deepcopy(initial);cap['metadata']['revision']+=revision
                folder=reserve_directory(root/f'attempt_{revision+1:02d}')
                charge=dict(attempt=revision+1,phase='initial' if not revision else 'targeted_revision',
                    charged_before_request=True,timeout_s=agent.timeout,failed_requests_consume_budget=True)
                budget.append(charge);write_json_new(root/f'charge_{revision+1:02d}.json',charge)
                stages={k:'NOT_REACHED' for k in ('SERVICE','BUSINESS_SCHEMA','T1','T2','T3','T4','EXECUTION','ACCEPTOR')}
                try:
                    out,prov=agent.decide(cap,feedback);stages['SERVICE']='PASS';stages['BUSINESS_SCHEMA']='PASS'
                    write_json_new(folder/'record.json',dict(capture=cap,output=out,provenance=prov))
                    result,failure,tools=physical(cap,out,prov,initial,revision,folder,policy=policy,**({'require_turn':True} if require_turn else {}));stages.update(tools)
                    rows.append(dict(attempt=revision+1,stages=stages,failure=failure))
                    print(json.dumps(dict(attempt=revision+1,stages=stages,failure=failure),ensure_ascii=False),flush=True)
                    if result:break # Any consumed prefix ends this observation.
                    feedback=dict(failure=failure,previous_native_output=out,
                        all_initial_body_envelope_conflicts=body_feedback(out),
                        concrete_repair_request='Supply the specific missing native road polygon/scale support or whole BODY two-second occupancy, under justified local motion conditions. Repair actual evidence/premise/residual/geometry errors. Never fabricate numbers, remove obstacles or relax physical checks. If a conservative corridor fails, revise a justified decision/target region or report the conflict; do not merely explain refusal.')
                except Failure as exc:
                    failure=exc.record;write_json_new(folder/'failure.json',failure)
                    rows.append(dict(attempt=revision+1,stages=stages,failure=failure));fatal=failure
                    break # Service/interface failure is terminal; no retry.
        except Failure as exc:fatal=exc.record
    if result:write_json_new(root/'round_result.json',result)
    summary=dict(profile=PROFILE,planner_profile=planner,turn_task_required=require_turn,charged_requests=len(budget),budget_limit=0 if args.replay else limit,remaining_unused=0 if args.replay else limit-len(budget),
        ended=True,attempts=rows,fatal=fatal,prototype_conditional_success=bool(result and result['prototype_conditional_success']),
        prefix_consumed=bool(result),source_commit=commit,mode='replay' if args.replay else 'online',
        old_strict_experiment_success=False,g2_evaluated=False,real_vehicle_control=False)
    write_json_new(root/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if summary['prototype_conditional_success'] else 3


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--capture',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path);p.add_argument('--wire',required=True,type=Path)
    p.add_argument('--replay',type=Path);p.add_argument('--timeout-s',type=float,default=300)
    p.add_argument('--initial-feedback',type=Path,help='Explicit evidence-based feedback from a saved response; recorded verbatim')
    p.add_argument('--prompt-version',choices=['driving_prototype_v1','driving_prototype_v2'],default='driving_prototype_v1')
    p.add_argument('--require-turn',action='store_true',help='Require registered nonzero turning evidence before simulated prefix consumption')
    p.add_argument('--max-requests',type=int,default=4)
    p.add_argument('--planner-profile',choices=['legacy_disk_v2','yaw_bounded_box_v1',t2.SEARCH_PROFILE,t2.SEARCH_PROFILE_V2],default='legacy_disk_v2')
    p.add_argument('--deadline-utc',default='2026-10-28T11:58:34Z')
    sys.exit(main(p.parse_args()))
