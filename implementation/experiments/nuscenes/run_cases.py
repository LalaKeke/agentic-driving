"""Runtime-only nuScenes batch. This entrypoint has NO truth/map arguments/imports."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from agentic.contracts import validate,need,Failure
from agentic.adapter import make_agent
from agentic.runtime import RunLog,ClosedLoop


def inspect_capture(cap):
    validate('capture_v2.schema.json',cap)
    m=cap['metadata'];ids=set();hashes={};total=0
    for f in cap['observations']:
        need(f['id'] not in ids,'DUPLICATE_ID','Duplicate frame');ids.add(f['id'])
        need(0<=m['reference_time_s']-f['capture_time_s']<=m['history_s']+1e-6,'CAUSALITY','Future or stale image')
        data=Path(f['path']).read_bytes();total+=len(data)
        need(data.startswith(b'\xff\xd8') or data.startswith(b'\x89PNG\r\n\x1a\n'),'IMAGE_SIGNATURE','Missing image signature')
        hashes[f['id']]=hashlib.sha256(data).hexdigest()
    need(any(f['time_id']==m['reference_time_id'] and f['capture_time_s']==m['reference_time_s'] for f in cap['observations']),'REFERENCE_TIME','Reference not acquired')
    return dict(status='ACQUISITION_CHECKS_PASS',frames=len(ids),image_bytes=total,image_sha256=hashes)


def run(index_path,runs,repeats=0,backend=None,model=None,max_revisions=2,experiment_config=None):
    need(0<=max_revisions<=2,'REVISIONS','Use 0..2 revisions per observation')
    need(0<=repeats<=10,'REPEATS','Use 0..10 separate evaluation calls per case')
    index_path=Path(index_path).resolve();root=index_path.parent;index=json.loads(index_path.read_text());log=RunLog(runs)
    log.write('entrypoint-provenance',dict(launcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),runtime_index_sha256=hashlib.sha256(index_path.read_bytes()).hexdigest(),sampling_order='planning_first_optional_evaluation_after',repeats=repeats,max_revisions=max_revisions))
    if experiment_config is not None:log.write('experiment-config',experiment_config)
    try:agent=make_agent(backend,model);blocked=None
    except Failure as exc:agent=None;blocked=exc.record
    results=[];global_errors=('CODEX_TRANSPORT','CODEX_MODEL_UNAVAILABLE','CODEX_PROCESS','CODEX_CLI_STATE','CODEX_CHATGPT_LOGIN_REQUIRED')
    for item in index['cases']:
        path=(root/item['capture']).resolve()
        need(root/'runtime_inputs' in path.parents,'INPUT_ISOLATION','Only runtime_inputs capture paths accepted')
        cap=json.loads(path.read_text());audit=inspect_capture(cap);log.write('capture-audit',dict(sample_id=item['sample_id'],capture_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),**audit))
        row=dict(sample_id=item['sample_id'],acquisition_status=audit['status'],model_calls=0,evaluation_model_attempts=0,planning_status='NOT_RUN',physical_success=False)
        if agent is None:row.update(status='BLOCKED_MODEL_ACCESS',reason=blocked)
        else:
            log.write('adapter-config',agent.public_config)
            before=log.count
            result=ClosedLoop(agent,log,max_revisions=max_revisions).round(cap)
            row.update(status=result['status'],planning_status=result['status'],planning_log_start=before+1,acceptance=result.get('acceptance',{}),planning_statistics=result.get('statistics',{}))
            row['physical_success']=bool(row['acceptance'].get('strict_live_physical_success',False))
            if result.get('feedback',{}).get('code') in global_errors:
                blocked=result['feedback'];agent=None
            # Evaluation sampling is optional and never a prerequisite for planning.
            if agent is not None:
                try:
                    for attempt in range(repeats):
                        row['evaluation_model_attempts']+=1;log.write('sampling-attempt',dict(sample_id=item['sample_id'],attempt=attempt,state='started'))
                        log.write('capture',cap);output,provenance=agent.decide(cap);row['model_calls']+=1
                        log.write('model-record',dict(sample_id=item['sample_id'],output=output,provenance=provenance))
                except Failure as exc:
                    row.update(status='physical_success_sampling_failure' if row['physical_success'] else 'MODEL_OR_TOOL_FAILURE',reason=exc.record)
                    if exc.record['code'] in global_errors:blocked=exc.record;agent=None
        row['all_model_attempts']=row['evaluation_model_attempts']+row.get('planning_statistics',{}).get('model_attempts',0)
        row['all_model_responses']=row['model_calls']+row.get('planning_statistics',{}).get('model_responses',0)
        log.write('case-result',row);results.append(row)
    successes=sum(r['physical_success'] for r in results)
    summary=dict(status='BLOCKED' if blocked else 'COMPLETED_WITH_PER_CASE_RESULTS',cases=results,
                 experiment_verdict='ALL_PHYSICAL_SUCCESS' if successes==len(results) and results else 'PARTIAL_PHYSICAL_SUCCESS' if successes else 'NO_PHYSICAL_SUCCESS',
                 strict_live_physical_successes=successes,physical_success_denominator=len(results),
                 repeated_calls_requested=repeats,max_revisions_per_observation=max_revisions,actual_repeated_model_calls=sum(r['model_calls'] for r in results),
                 all_model_attempts=sum(r['all_model_attempts'] for r in results),all_model_responses=sum(r['all_model_responses'] for r in results),
                 truth_access=False,execution='SIMULATED_PREFIX_ONLY',
                 claim='Single-window success is conditional on model hypotheses; G2/GT correctness and multi-round counterfactual closed loop are separate')
    log.write('nuscenes-summary',summary)
    return log.path,summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--index',required=True);p.add_argument('--runs',default='runs');p.add_argument('--repeats',type=int,default=0)
    p.add_argument('--backend',choices=['http','codex_cli']);p.add_argument('--model');p.add_argument('--max-revisions',type=int,default=2)
    a=p.parse_args()
    try:
        path,summary=run(a.index,a.runs,a.repeats,a.backend,a.model,a.max_revisions);print(json.dumps(dict(run=str(path),**summary),ensure_ascii=False,indent=2))
        raise SystemExit(2 if summary['status']=='BLOCKED' else 0 if summary['strict_live_physical_successes']==summary['physical_success_denominator'] else 3)
    except Failure as e:print(json.dumps(e.record,ensure_ascii=False));raise SystemExit(2)
