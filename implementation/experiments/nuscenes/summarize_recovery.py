#!/usr/bin/env python3
"""Read-only extraction of process-recovery logs, with no GT or acceptance reruns.

Reports only the strict acceptance decisions already written by the runtime.
Mechanical diagnostics describe interface conformance, never grounding quality.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

NUMBERED = re.compile(r'^\d{4}-(.+)\.json$')
MECHANICAL_CODES = ('DECISION_EXPIRED','TARGET_INITIAL_STATE','TARGET_INSET_EMPTY',
                    'CALIBRATED_PROJECTION_UNVERIFIED','OCCUPANCY_GAP',
                    'SCENE_VERSION_MISMATCH','MISSING_EVIDENCE')


def finite_number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def convex_planes(points):
    if not isinstance(points,list) or len(points)<3 or any(not isinstance(p,list) or len(p)!=2 or not all(finite_number(x) for x in p) for p in points):
        return None
    area=sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(points,points[1:]+points[:1]))/2
    if abs(area)<=1e-8:return None
    points=points if area>0 else list(reversed(points));planes=[]
    for a,b in zip(points,points[1:]+points[:1]):
        dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        if length<=1e-10:continue
        nx,ny=dy/length,-dx/length;planes.append((nx,ny,nx*a[0]+ny*a[1]))
    if len(planes)<3 or any(nx*x+ny*y>limit+1e-7 for nx,ny,limit in planes for x,y in points):return None
    return planes


def inside(point,planes,inset=0.):
    return all(nx*point[0]+ny*point[1]<=b-inset+1e-8 for nx,ny,b in planes)


def nonempty_inset(planes,radius):
    vertices=[]
    for k,(ax,ay,ab) in enumerate(planes):
        for bx,by,bb in planes[k+1:]:
            det=ax*by-ay*bx
            if abs(det)<1e-10:continue
            x=((ab-radius)*by-ay*(bb-radius))/det
            y=(ax*(bb-radius)-(ab-radius)*bx)/det
            if inside((x,y),planes,radius) and all(math.hypot(x-a,y-b)>1e-7 for a,b in vertices):vertices.append((x,y))
    if len(vertices)<3:return False
    cx=sum(x for x,y in vertices)/len(vertices);cy=sum(y for x,y in vertices)/len(vertices)
    vertices.sort(key=lambda p:math.atan2(p[1]-cy,p[0]-cx))
    area=abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(vertices,vertices[1:]+vertices[:1])))/2
    return area>1e-8


def mechanical_audit(capture,record):
    output=record.get('output',{});d=output.get('decision',{});h=output.get('hypotheses',{});m=capture['metadata'];issues=[];status={}
    def check(code,applicable,ok,details=None):
        state='PASS' if applicable and ok else 'FAIL' if applicable else 'NOT_APPLICABLE'
        status[code]=dict(status=state,details=details)
        if state=='FAIL':issues.append(code)
    until=d.get('valid_until_s');expected_until=m['reference_time_s']+m['horizon_s']
    check('DECISION_EXPIRED',True,finite_number(until) and until>=expected_until,
          dict(actual=until,minimum_required=expected_until))
    planes=convex_planes(d.get('target_region'));ego=capture['ego'];v=capture['vehicle'];offset=v['center_to_rear_axle_m']
    rear=(ego['x_m']-offset*math.cos(ego['yaw_rad']),ego['y_m']-offset*math.sin(ego['yaw_rad']))
    center=(ego['x_m'],ego['y_m'])
    check('TARGET_INITIAL_STATE',True,planes is not None and inside(rear,planes,offset),
          dict(center_inside_polygon=inside(center,planes) if planes is not None else None,
               initial_rear_in_conservative_center_configuration=inside(rear,planes,offset) if planes is not None else None,
               polygon_supported_convex=planes is not None))
    check('TARGET_INSET_EMPTY',True,planes is not None and nonempty_inset(planes,offset),
          dict(center_to_rear_axle_m=offset,polygon_supported_convex=planes is not None))
    scale=h.get('scale_basis')
    check('CALIBRATED_PROJECTION_UNVERIFIED',True,scale!='calibrated_ground_plane',
          dict(scale_basis=scale,note='Supported/unknown label is not evidence of metric grounding.'))
    objects=h.get('objects',[]);grid_errors=[];known=0;nulls=0
    n=round(m['horizon_s']/m['step_s'])
    for obj in objects:
        cells=obj.get('occupancy')
        if cells is None:nulls+=1;continue
        known+=1;bad=not isinstance(cells,list) or len(cells)!=n
        if isinstance(cells,list):
            bad=bad or any(not isinstance(c,dict) or not finite_number(c.get('start_s')) or not finite_number(c.get('end_s')) or abs(c['start_s']-k*m['step_s'])>1e-8 or abs(c['end_s']-(k+1)*m['step_s'])>1e-8 for k,c in enumerate(cells))
        if bad:grid_errors.append(obj.get('id'))
    check('OCCUPANCY_GAP',known>0,not grid_errors,
          dict(required_intervals=n,nonnull_actor_occupancies=known,null_actor_occupancies=nulls,
               invalid_actor_ids=grid_errors,note='Null occupancy is unresolved, not a repaired forecast.'))
    check('SCENE_VERSION_MISMATCH',True,d.get('scene_id')==m['scene_id'] and d.get('revision')==m['revision'] and h.get('reference_time_id')==m['reference_time_id'] and h.get('coordinate_frame')==m['coordinate_frame'])
    frames={f['id']:f for f in capture['observations']};times={f['time_id'] for f in capture['observations']};evidence_errors=[]
    items=([h['road']] if h.get('road') is not None else [])+objects+h.get('unknown_regions',[])+h.get('traffic_evidence',[])
    for i,item in enumerate(items):
        fs=item.get('frame_ids',[]);ts=item.get('time_ids',[])
        if not fs or not ts or not set(fs)<=set(frames) or not set(ts)<=times or any(not any(f in frames and frames[f]['time_id']==t for f in fs) for t in ts):evidence_errors.append(i)
    check('MISSING_EVIDENCE',bool(items),not evidence_errors,dict(invalid_item_indices=evidence_errors))
    capability=dict(scale_unknown=scale=='unknown',road_unknown=h.get('road') is None,
                    unbounded_unknown=h.get('unbounded_unknown'),road_uncertainty_unknown=h.get('road') is not None and h['road'].get('uncertainty_m') is None,
                    objects=len(objects),position_unknown=sum(o.get('position_m') is None for o in objects),
                    velocity_unknown=sum(o.get('velocity_mps') is None for o in objects),
                    object_uncertainty_unknown=sum(o.get('uncertainty_m') is None for o in objects),
                    occupancy_unknown=nulls,nonnull_occupancies_with_wrong_grid=len(grid_errors),
                    confidence_is_not_calibrated=True)
    return dict(issues=issues,mechanical=status,capability_gaps=capability,
                warning='Mechanical PASS does not establish supported road/occupancy, grounding, or physical success.')


def log_files(root):
    root=Path(root)
    return sorted([p for p in root.glob('*.json') if NUMBERED.match(p.name)])


def new_case(sid):
    return dict(sample_id=sid,case_result=None,acceptance=None,acceptance_source=None,
                stage_events=[],responses=[],round_statistics=None,feedbacks=[],capture_audits=[])


def extract_run(root):
    root=Path(root).resolve();cases={};active=None;capture=None;manifest=None;configuration=None;summary=None;warnings=[];inputs=[];sequence=[]
    for path in log_files(root):
        raw=path.read_bytes();inputs.append(dict(file=path.name,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
        try:data=json.loads(raw)
        except (ValueError,UnicodeDecodeError):warnings.append(dict(code='PARTIAL_JSON_DURING_SNAPSHOT',file=path.name));continue
        kind=NUMBERED.match(path.name).group(1);sequence.append(int(path.name[:4]))
        if kind=='manifest':manifest=data
        elif kind=='experiment-config':configuration=data.get('configuration',data)
        elif kind=='capture-audit':
            active=data['sample_id'];case=cases.setdefault(active,new_case(active));case['capture_audits'].append(data)
        elif kind=='capture':
            capture=data;active=data['metadata']['scene_id'].removeprefix('nusc_');cases.setdefault(active,new_case(active))
        elif kind in ('agent','model-record'):
            if capture is None:warnings.append(dict(code='RESPONSE_WITHOUT_CAPTURE',file=path.name));continue
            sid=capture['metadata']['scene_id'].removeprefix('nusc_');case=cases.setdefault(sid,new_case(sid));prov=data.get('provenance',{})
            audit=mechanical_audit(capture,data)
            case['responses'].append(dict(file=path.name,phase='planning' if kind=='agent' else 'evaluation_sampling',revision=capture['metadata']['revision'],source=prov.get('source'),
                 model_id=prov.get('model_id'),requested_model=prov.get('requested_model'),prompt_version=prov.get('prompt_version'),prompt_sha256=prov.get('prompt_sha256'),
                 output_contract=prov.get('agent_output_contract','2.0.0'),elapsed_s=prov.get('elapsed_s'),behavior=data.get('output',{}).get('decision',{}).get('behavior'),audit=audit))
        elif kind=='case-result':
            active=data['sample_id'];case=cases.setdefault(active,new_case(active));case['case_result']=data
            if data.get('acceptance'):
                case['acceptance']=data['acceptance'];case['acceptance_source']=path.name+'::acceptance'
        elif kind=='nuscenes-summary':summary=data
        elif active is not None:
            case=cases.setdefault(active,new_case(active))
            if kind=='stage-event':case['stage_events'].append(dict(file=path.name,**data))
            elif kind=='round-acceptance':case['acceptance']=data;case['acceptance_source']=path.name
            elif kind=='round-statistics':case['round_statistics']=data
            elif kind=='feedback':case['feedbacks'].append(dict(file=path.name,**data))
    if configuration:
        for c in configuration.get('cohort',[]):cases.setdefault(c['sample_id'],new_case(c['sample_id']))
    output=[]
    for sid,c in cases.items():
        row=c['case_result'];accept=c['acceptance'];stats=(row or {}).get('planning_statistics') or c['round_statistics']
        events=c['stage_events'];agent_starts=[e for e in events if e.get('stage')=='Agent' and e.get('state')=='started'];agent_finishes=[e for e in events if e.get('stage')=='Agent' and e.get('state')=='completed'];executed=[e for e in events if e.get('stage')=='Execute' and e.get('state')=='completed']
        strict=accept.get('strict_live_physical_success') if isinstance(accept,dict) else None
        success_attempt=executed[-1].get('attempt') if strict is True and executed else None
        if strict is True and success_attempt is None:warnings.append(dict(code='SUCCESS_WITHOUT_EXECUTE_EVENT',sample_id=sid))
        first=(success_attempt==0) if strict is True else False if accept is not None and agent_starts else None
        counts=Counter(e['stage'] for e in events if e.get('state')=='started' and e.get('stage')!='Agent')
        failures={}
        for f in (stats or {}).get('failures',[]):failures[(f.get('stage'),f.get('attempt'),f.get('code'))]=dict(f)
        for e in events:
            if e.get('state')=='failed':
                key=(e.get('stage'),e.get('attempt'),e.get('failure_code'));failures.setdefault(key,dict(stage=key[0],attempt=key[1],code=key[2]))
        if stats:
            if stats.get('model_attempts')!=len(agent_starts) or stats.get('model_responses')!=len(agent_finishes):warnings.append(dict(code='COUNTER_EVENT_MISMATCH',sample_id=sid))
            if any(stats.get('tool_calls',{}).get(k,0)!=counts[k] for k in set(counts)|set(stats.get('tool_calls',{}))):warnings.append(dict(code='TOOL_COUNTER_EVENT_MISMATCH',sample_id=sid))
        output.append(dict(sample_id=sid,status=(row or {}).get('status','IN_PROGRESS' if events else 'NOT_STARTED'),
             case_result_source_present=row is not None,strict_live_physical_success_within_revision_budget=strict,
             first_attempt_strict_success=first,successful_attempt_index=success_attempt,motion_class=(accept or {}).get('motion_class'),
             acceptance_source=c['acceptance_source'],acceptance=accept,planning_model_attempts=(stats or {}).get('model_attempts',len(agent_starts)),
             planning_model_responses=(stats or {}).get('model_responses',len(agent_finishes)),
             evaluation_model_attempts=(row or {}).get('evaluation_model_attempts',0),evaluation_model_responses=(row or {}).get('model_calls',0),
             tool_attempts={name:(stats or {}).get('tool_calls',{}).get(name,counts[name]) for name in ['T1','T2','T3','T4','Execute']},
             failures=list(failures.values()),responses=c['responses'],recorded_feedbacks=c['feedbacks'],capture_audits=c['capture_audits']))
    sequence_gaps=sorted(set(range(min(sequence),max(sequence)+1))-set(sequence)) if sequence else []
    if sequence_gaps:warnings.append(dict(code='NUMBERED_LOG_GAPS',numbers=sequence_gaps))
    return dict(run=str(root),snapshot_status='FINAL_LOG_PRESENT' if summary else 'IN_PROGRESS',manifest=manifest,configuration=configuration,
                recorded_batch_summary=summary,cases=output,warnings=warnings,input_log_files=inputs)


def aggregate_responses(responses):
    metrics={code:dict(applicable_responses=0,passed_responses=0,failed_responses=0,not_applicable_responses=0) for code in MECHANICAL_CODES}
    gaps=Counter();objecttotals=Counter();groups=defaultdict(list)
    for r in responses:
        for code,s in r['audit']['mechanical'].items():
            if s['status']=='NOT_APPLICABLE':metrics[code]['not_applicable_responses']+=1
            else:
                metrics[code]['applicable_responses']+=1;metrics[code]['passed_responses' if s['status']=='PASS' else 'failed_responses']+=1
        for code,value in r['audit']['capability_gaps'].items():
            if isinstance(value,bool) and value:gaps[code]+=1
            elif isinstance(value,int) and not isinstance(value,bool):objecttotals[code]+=value
        key=(r['prompt_version'],r['prompt_sha256'],r['output_contract'],r['model_id']);groups[key].append(r)
    return dict(responses=len(responses),mechanical=metrics,capability_gap_response_counts=dict(gaps),object_entry_counts=dict(objecttotals),
                groups=[dict(prompt_version=k[0],prompt_sha256=k[1],output_contract=k[2],model_id=k[3],responses=len(v)) for k,v in groups.items()],
                note='Every response/revision is included for contract diagnostics. They are not independent scene trials. Unknown occupancy is NOT_APPLICABLE, not a repaired forecast.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path);parser.add_argument('--baseline',nargs='*',type=Path,default=[]);parser.add_argument('--out',type=Path);args=parser.parse_args()
    report=extract_run(args.run);cases=report['cases'];responses=[r for c in cases for r in c['responses']]
    expected=len((report.get('configuration') or {}).get('cohort',[])) or len(cases)
    completed=sum(c['case_result_source_present'] for c in cases);attempted=sum(c['planning_model_attempts']>0 for c in cases)
    report['summary']=dict(cohort_cases=expected,completed_cases=completed,attempted_cases=attempted,strict_successes_within_budget=sum(c['strict_live_physical_success_within_revision_budget'] is True for c in cases),
         first_attempt_strict_successes=sum(c['first_attempt_strict_success'] is True for c in cases),moving_prefix_successes=sum(c['strict_live_physical_success_within_revision_budget'] is True and c['motion_class']=='MOVING_PREFIX' for c in cases),stationary_hold_successes=sum(c['strict_live_physical_success_within_revision_budget'] is True and c['motion_class']=='STATIONARY_HOLD' for c in cases),
         planning_model_attempts=sum(c['planning_model_attempts'] for c in cases),planning_model_responses=sum(c['planning_model_responses'] for c in cases),
         evaluation_model_attempts=sum(c['evaluation_model_attempts'] for c in cases),evaluation_model_responses=sum(c['evaluation_model_responses'] for c in cases),
         tool_attempts={n:sum(c['tool_attempts'][n] for c in cases) for n in ['T1','T2','T3','T4','Execute']},failure_stage_codes=dict(Counter(str(f.get('stage'))+':'+str(f.get('code')) for c in cases for f in c['failures'])))
    report['all_response_contract_audit']=aggregate_responses(responses)
    if args.baseline:
        baselines=[]
        for root in args.baseline:
            roots=[root] if log_files(root) else sorted(p for p in root.iterdir() if p.is_dir() and log_files(p))
            baselines.extend(extract_run(r) for r in roots)
        baseline_responses=[r for b in baselines for c in b['cases'] for r in c['responses']]
        report['baseline_contract_audit']=aggregate_responses(baseline_responses)
        report['baseline_response_records']=[dict(run=b['run'],sample_id=c['sample_id'],**r) for b in baselines for c in b['cases'] for r in c['responses']]
        report['comparison_limits']='Original model responses, contracts, prompts, evaluation pre-calls and revision budgets differ. Mechanical counts are descriptive, not a paired independent success-rate experiment.'
    report['extraction']=dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),source_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         truth_read=False,model_calls=0,acceptance_recomputed=False,physical_tools_called=False,
         success_authority='Only saved runtime round-acceptance/case-result acceptance; no inferred PASS from artifacts, response counts or solver status',
         source_scope='Numbered JSON files in explicitly supplied run directories only',
         incomplete_snapshot_warning='Rerun after nuscenes-summary is written; missing acceptance in an active run is null, not a final failure.')
    text=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if args.out:
        out=args.out.resolve()
        if Path('/tmp') not in out.parents:parser.error('--out must stay under /tmp; copy the finished evidence separately')
        out.parent.mkdir(parents=True,exist_ok=True);out.write_text(text);print(out)
    else:print(text,end='')


if __name__=='__main__':main()
