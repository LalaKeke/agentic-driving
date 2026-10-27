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


def run(index_path,runs,repeats=3,backend=None,model=None):
    need(1<=repeats<=10,'REPEATS','Use 1..10 calls per case')
    index_path=Path(index_path).resolve();root=index_path.parent;index=json.loads(index_path.read_text());log=RunLog(runs)
    # Capability/config failure is recorded for all cases, with no substituted response.
    try:agent=make_agent(backend,model);blocked=None
    except Failure as exc:agent=None;blocked=exc.record
    results=[]
    for item in index['cases']:
        path=(root/item['capture']).resolve()
        need(root/'runtime_inputs' in path.parents,'INPUT_ISOLATION','Only runtime_inputs capture paths accepted')
        cap=json.loads(path.read_text());audit=inspect_capture(cap);log.write('capture-audit',dict(sample_id=item['sample_id'],**audit))
        row=dict(sample_id=item['sample_id'],acquisition_status=audit['status'],model_calls=0,planning_status='NOT_RUN')
        if agent is None:
            row.update(status='BLOCKED_MODEL_ACCESS',reason=blocked)
        else:
            log.write('adapter-config',agent.public_config);log.write('capture',cap)
            try:
                for _ in range(repeats):
                    output,provenance=agent.decide(cap);row['model_calls']+=1
                    log.write('model-record',dict(sample_id=item['sample_id'],output=output,provenance=provenance))
                # A fresh mandatory agent call begins the planning round. Failures cause
                # fresh image-conditioned revisions, not replay of a cached model result.
                before=log.count
                result=ClosedLoop(agent,log).round(cap)
                row.update(status=result['status'],planning_status=result['status'],planning_log_start=before+1)
            except Failure as exc:
                row.update(status='MODEL_OR_TOOL_FAILURE',reason=exc.record)
                if exc.record['code'] in ('CODEX_TIMEOUT','CODEX_TRANSPORT','CODEX_MODEL_UNAVAILABLE','CODEX_PROCESS','CODEX_CLI_STATE'):
                    blocked=exc.record;agent=None
        log.write('case-result',row);results.append(row)
    summary=dict(status='BLOCKED' if blocked else 'COMPLETED_WITH_PER_CASE_RESULTS',cases=results,
                 repeated_calls_requested=repeats,actual_repeated_model_calls=sum(r['model_calls'] for r in results),
                 planning_agent_calls='See per-round agent logs; not included in repeated-call count',
                 truth_access=False,execution='SIMULATED_PREFIX_ONLY',
                 claim='No method feasibility or G2 pass claim from acquisition checks')
    log.write('nuscenes-summary',summary)
    return log.path,summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--index',required=True);p.add_argument('--runs',default='runs');p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--backend',choices=['http','codex_cli']);p.add_argument('--model')
    a=p.parse_args()
    try:
        path,summary=run(a.index,a.runs,a.repeats,a.backend,a.model);print(json.dumps(dict(run=str(path),**summary),ensure_ascii=False,indent=2))
        raise SystemExit(2 if summary['status']=='BLOCKED' else 0)
    except Failure as e:print(json.dumps(e.record,ensure_ascii=False));raise SystemExit(2)
