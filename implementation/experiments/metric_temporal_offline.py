"""Offline v3-exp1 integration and historical refusal comparison; no model APIs.

Every execution needs a fresh output directory. Historical inputs, including
the 27 native responses, are read-only and are never upgraded to the new schema.
"""
import argparse
import ast
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic import t1, t2, t3, t4
from agentic.contracts import Failure, digest, validate
from agentic.evidence_io import reserve_directory, write_json_new
from agentic.fixtures import coasting_fault, rect
from agentic.fixtures_v3 import metric_case, add_adverse_scenario
from agentic.g2_metric import evaluate
from agentic.metric_contract import VERSION
from agentic.runtime import SimulatedExecutor

OLD = ROOT / 'implementation/results/t1_unknown_diagnosis'
BASELINE = ROOT / 'implementation/results/metric_temporal_contract_v3'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_manifest():
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    names = [Path(__file__).relative_to(ROOT)]
    for folder in ('agentic', 'schemas', 'prompts', 'tests', 'experiments', 'docs'):
        names += [p.relative_to(ROOT) for p in (ROOT/'implementation'/folder).rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts]
    files = {}
    for p in sorted(set(names)):
        raw = (ROOT/p).read_bytes()
        committed = subprocess.check_output(['git', 'show', commit+':'+str(p)], cwd=ROOT)
        if raw != committed:
            raise Failure('UNFROZEN_SOURCE', 'Commit the analysis and runtime sources before running the sealed comparison.', path=str(p))
        files[str(p)] = dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    return dict(analysis_source_commit=commit, files=files, online_model_calls=0,
                source_matches_commit=True,
                worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)))


def history():
    baseline=read(BASELINE/'baseline_protection.json')
    with zipfile.ZipFile(BASELINE/'baseline_source.zip') as z:
        frozen=z.read('implementation/agentic/t1.py').decode()
    current=(ROOT/'implementation/agentic/t1.py').read_text()
    old_fun=next(n for n in ast.parse(frozen).body if isinstance(n,ast.FunctionDef) and n.name=='construct')
    new_fun=next(n for n in ast.parse(current).body if isinstance(n,ast.FunctionDef) and n.name=='_construct_legacy')
    body_same=ast.dump(ast.Module(body=old_fun.body,type_ignores=[]))==ast.dump(ast.Module(body=new_fun.body,type_ignores=[]))
    if not body_same:raise Failure('LEGACY_CHANGED','Historical guard comparison requires the exact legacy body')
    summaries=read(OLD/'fields/responses.json'); rows=[]; inputs={}
    for r in summaries:
        rp=ROOT/r['record']['path'];cp=ROOT/r['capture']['path'];record=read(rp);cap=read(cp)
        for key,p in [('record',rp),('capture',cp)]:
            if sha(p)!=r[key]['sha256']:raise Failure('FROZEN_INPUT_CHANGED','Historical input SHA mismatch',path=str(p))
            inputs[str(p.relative_to(ROOT))]=sha(p)
        try:t1.construct(cap,record['output'],record['provenance'])
        except Failure as exc:actual=exc.record
        else:raise Failure('LEGACY_ACCEPTANCE_CHANGED','A frozen rejected response unexpectedly passed')
        if actual['code']!=r['recorded_first_code']:raise Failure('LEGACY_REJECTION_CHANGED','Historical first error changed')
        try:validate('agent_output_v3_exp2.schema.json',record['output'])
        except Failure as exc:new_schema=exc.record
        else:raise Failure('LEGACY_UPGRADE','Historical output unexpectedly satisfies the new contract')
        rows.append(dict(response_id=r['response_id'],record=r['record'],capture=r['capture'],
                         sample_id=r['sample_id'],group=r['group'],revision=r['revision'],
                         selected_contract=record['provenance']['agent_output_contract'],
                         recorded_first_error=r['first_feedback'],current_first_error=actual,
                         rejected_before=True,rejected_now=True,new_native_schema_rejection=new_schema,
                         transformed=False,online_success=False))
    issue_file=OLD/'fields/issues.jsonl';predicates=[]
    for line in issue_file.read_text().splitlines():
        row=json.loads(line)
        if row['stage']!='T1':continue
        guard=row['guard_file']; current_guard=ROOT/guard
        if guard!='implementation/agentic/t1.py':
            if sha(current_guard)!=row['guard_source_sha256']:
                raise Failure('GUARD_DEPENDENCY_CHANGED','Non-T1 historical guard changed',path=guard)
        predicates.append(dict(response_id=row['response_id'],input=row['record'],
            json_pointer=row['json_pointer'],raw_value=row['raw_value'],object_id=row['object_id'],
            time_interval_s=row['time_interval_s'],evidence=row['evidence'],relevance=row['relevance'],
            old_error_code=row['error_code'],frozen_guard_file=guard,frozen_guard_line=row['guard_line'],
            frozen_guard_sha256=row['guard_source_sha256'],current_guard_file=guard,
            current_guard_line=row['guard_line']+(new_fun.lineno-old_fun.lineno) if guard.endswith('/t1.py') else row['guard_line'],
            current_guard_sha256=sha(current_guard),legacy_predicate_still_present=True,
            basis='Identical legacy function body AST and unchanged dependency bytes; original values unchanged',
            actual_execution=row['actual_execution'],new_profile_tested_on_old_output=False))
    assert len(rows)==27 and len(predicates)==422 and len({x['sample_id'] for x in rows})==6
    inputs[str(issue_file.relative_to(ROOT))]=sha(issue_file)
    return dict(legacy_body_ast_unchanged=True,responses=rows,predicates=predicates,
        valid_responses=27,unique_windows=6,original_four_unique_windows=4,exploratory_unique_windows=2,
        historical_attempts=28,service_nonresponse_attempts=1,first_codes=dict(Counter(x['current_first_error']['code'] for x in rows)),
        failed_predicate_count=422,transformations=[],new_online_successes=0,input_sha256=inputs,
        new_profile_scope='Native new outputs only; no historical response is converted or counted as new success',
        baseline_commit=baseline['baseline_commit'])


def artificial_case(speed):
    cap,out,prov=metric_case(speed=speed)
    scene=t1.construct(cap,out,prov,True)
    corridor=t2.build(scene,policy='moving_seed')
    trajectory=t3.solve(scene,corridor);verification=t4.verify(scene,trajectory)
    if verification['verdict']!='PASS':raise Failure('SYNTHETIC_REGRESSION','Authored feasible case did not verify',violations=verification['violations'])
    executor=SimulatedExecutor();executor.observe(cap)
    execution=executor.execute(scene,trajectory,verification,cap['metadata']['reference_time_s'])
    return dict(source='synthetic_fixture',online_success=False,real_model_calls=0,
        capture=cap,output=out,provenance=prov,T1=scene,T2=corridor,T3=trajectory,T4=verification,
        simulation=execution,prefix_kind='MOVING_PREFIX' if execution['center_displacement_m'][0]>1e-6 else 'STATIONARY_HOLD')


def intrusion(base):
    cap,out,prov=metric_case();add_adverse_scenario(out,invade=True)
    actor=out['hypotheses']['objects'][0]
    future=deepcopy(actor['occupancy'][0]);future.update(id='intrusion_after_2s',start_s=2.)
    before=deepcopy(future);before.update(id='safe_before_2s',start_s=0.,end_s=2.)
    before['alternatives'][-1]['polygon_m']=rect(17.6,19.,-3.5,10.)
    actor['occupancy']=[before,future];prov['output_digest']=digest(out)
    scene=t1.construct(cap,out,prov,True)
    try:t2.build(scene,policy='moving_seed')
    except Failure as exc:corridor_failure=exc.record
    else:raise Failure('ADVERSE_BRANCH_DROPPED','Authored future intrusion unexpectedly permitted corridor')
    trajectory=deepcopy(base['T3']);trajectory['metadata']=deepcopy(scene['metadata']);trajectory['scene_digest']=digest(scene)
    trajectory=coasting_fault(scene,trajectory)
    verdict=t4.verify(scene,trajectory)
    assert verdict['verdict']=='FAIL' and verdict['released_prefix_s']==0 and any(v['code']=='BODY_COLLISION' for v in verdict['violations'])
    return dict(source='synthetic_fixture',online_success=False,real_model_calls=0,
                capture=cap,output=out,provenance=prov,T1=scene,T2_failure=corridor_failure,
                authored_stationary_trajectory=trajectory,T4=verdict,execution_released=False)


def strict_history_g2(hist):
    truth=read(OLD/'g2/truth.json');association=read(OLD/'g2/associations.json');records=[]
    # Explicit GT/identity FORMAT aliases only. Never assign new measurement
    # semantics, state epochs or numbers to these old model responses or GT.
    bridge=[]
    for sid,gt in truth['samples'].items():
        for i,actor in enumerate(gt['objects']):
            actor['id']=actor['instance_token']
            bridge.append(dict(source_file='g2/truth.json',source_path=f'/samples/{sid}/objects/{i}/instance_token',
                raw_value=actor['instance_token'],target_path=f'/samples/{sid}/objects/{i}/id',
                operation='GT_IDENTITY_ALIAS_ONLY',premise='Same frozen nuScenes instance token; no measurement definition supplied'))
    truth['associations']={}
    for rid,items in association['record_associations'].items():
        target=truth['associations'].setdefault(rid,{})
        for oid,item in items.items():
            if item.get('status')!='UNIQUE_VISUAL_SEMANTIC_MATCH':continue
            target[oid]=dict(gt_id=item['instance_token'],status='unique_reviewed',evidence=[deepcopy(item)],
                             reviewer_limitation=association['reviewer'])
            bridge.append(dict(source_file='g2/associations.json',record_id=rid,object_id=oid,
                raw_value=deepcopy(item),target_value=deepcopy(target[oid]),operation='REVIEW_FORMAT_ALIAS_ONLY',
                premise='Preserve original non-blind appearance/camera matching; not confirmation of metric comparability'))
    for row in hist['responses']:
        rec=read(ROOT/row['record']['path']);cap=read(ROOT/row['capture']['path'])
        records.append(dict(sample_id=row['sample_id'],record_id=row['record']['path'],
                            capture=cap,output=rec['output'],provenance=rec['provenance'],group=row['group']))
    result=evaluate(records,truth,dict(evaluation_horizon_s=5.,evaluation_domain=None))
    result['legacy_association_sha256']=sha(OLD/'g2/associations.json')
    result['explicit_gt_identity_format_bridge']='legacy_gt_identity_bridge.json'
    result['no_measurement_semantics_added']=True
    result['source_truth_sha256']=sha(OLD/'g2/truth.json')
    result['historical_service_nonresponses_separately_reported']=1
    assert result['valid_record_count']==27 and result['unique_windows']==6
    assert result['availability']['declared_object_rows']==170
    assert result['availability']['unmatched']==119
    assert result['availability']['position_error_m']['n']==0 and result['availability']['velocity_vector_error_mps']['n']==0
    return result,dict(kind='EXPLICIT_OFFLINE_GT_AND_REVIEW_FORMAT_BRIDGE_NOT_MODEL_MIGRATION',
        source_sha256={'g2/truth.json':sha(OLD/'g2/truth.json'),'g2/associations.json':sha(OLD/'g2/associations.json')},
        model_output_field_transformations=[],measurement_definitions_added=[],rows=bridge,
        online_success_claims=0,original_files_changed=False)


def run(out):
    source=source_manifest()  # Read-only version check precedes any output.
    out=reserve_directory(out,readonly_inputs=[OLD,BASELINE/'baseline_protection.json',BASELINE/'baseline_source.zip'])
    write_json_new(out/'source_manifest.json',source)
    hist=history();write_json_new(out/'historical_predicate_comparison.json',hist)
    held=artificial_case(0.);write_json_new(out/'authored_stationary.json',held)
    moving=artificial_case(6.);write_json_new(out/'authored_moving.json',moving)
    blocked=intrusion(held);write_json_new(out/'authored_future_intrusion.json',blocked)
    g2,bridge=strict_history_g2(hist)
    write_json_new(out/'legacy_gt_identity_bridge.json',bridge)
    write_json_new(out/'historical_g2_strict_refusals.json',g2)
    summary=dict(analysis_source_commit=source['analysis_source_commit'],output_contract=VERSION,
        prompt_version='driving_v3_exp2',online_model_calls=0,new_online_successes=0,historical_model_output_transformations=0,
        historical_valid_responses=27,unique_windows=6,legacy_failed_predicates_retained=422,
        historic_first_codes=hist['first_codes'],legacy_body_ast_unchanged=True,
        authored_cases=dict(stationary=held['T4']['verdict'],moving=moving['T4']['verdict'],
                            future_intrusion=blocked['T4']['verdict'],future_intrusion_prefix_s=blocked['T4']['released_prefix_s']),
        strict_g2=dict(position_scores=0,velocity_scores=0,declared_objects=170,
                       position_available=g2['availability']['position_available'],velocity_available=g2['availability']['velocity_available']),
        interpretation='Software/representation evidence only; no new online/G2 pass claim or acceptance-rule migration')
    write_json_new(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True,type=Path)
    try:run(parser.parse_args().out)
    except Failure as exc:
        print(json.dumps(exc.record,ensure_ascii=False));raise SystemExit(2)
