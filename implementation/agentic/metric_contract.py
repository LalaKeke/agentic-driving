"""Experimental metric/time/body contract. No perception or actor prediction.

The v3 output is validated natively, then explicitly lowered to the unchanged
convex-disk physical profile. Full source and lossless source-cell links remain
in provenance; legacy outputs are never upgraded by this module.
"""
from copy import deepcopy
from decimal import Decimal
import math
from shapely.geometry import Point, Polygon
from .contracts import validate, need, digest, grid
from .geometry import polygon

VERSION = '3.0.0-exp2'
COVERAGE_PROFILE = 'whole_interval_sources_v3_exp2'
MEASUREMENT_SEMANTICS = dict(
    coordinate_frame='ego_at_reference', origin='ego_body_center_at_reference',
    axes='x_forward_y_left_fixed_at_reference',
    actor_reference_point='annotation_box_center_ground_xy',
    velocity_semantics='absolute_ground_velocity_in_fixed_reference_axes',
    uncertainty_semantics='noncalibrated_total_residual_radius_once')


def _age(meta, frame):
    # Acquisition JSON decimal values define the clock. Subtract before casting
    # so decimal 100.0 - 99.8 is .2, not .20000000000000284. This is arithmetic,
    # not an age tolerance or an exemption from actor-motion alignment.
    return float(Decimal(str(meta['reference_time_s']))-Decimal(str(frame['capture_time_s'])))


def synthetic_fixture(*args, **kwargs):
    """Authored software fixture only; no real images or online model calls."""
    from .fixtures_v3 import metric_case
    return metric_case(*args, **kwargs)


def _check_capture(cap, provenance, output, allow_synthetic):
    validate('capture_v2.schema.json', cap)
    validate('agent_output_v3_exp2.schema.json', output)
    need(provenance.get('agent_output_contract') == VERSION, 'MODEL_CONTRACT', 'v3 requires explicit provenance contract version')
    need(provenance.get('source') == 'online_model' or (allow_synthetic and provenance.get('source') == 'synthetic_fixture'), 'SOURCE', 'Synthetic input requires explicit test mode')
    need(provenance.get('prompt_version') == 'driving_v3_exp2' or (allow_synthetic and provenance.get('source') == 'synthetic_fixture'), 'PROMPT_CONTRACT', 'v3 prompt and output contract are recorded separately and must be compatible')
    need(provenance.get('output_digest') == digest(output), 'SOURCE', 'Output does not match provenance')
    m=cap['metadata']; frames=cap['observations']; ids={f['id'] for f in frames}; clock={}
    need(len(ids)==len(frames), 'DUPLICATE_ID', 'Duplicate frame id')
    for f in frames:
        need(f['time_id'] not in clock or clock[f['time_id']]==f['capture_time_s'], 'CLOCK_CONFLICT', 'Conflicting capture timestamps')
        clock[f['time_id']]=f['capture_time_s']
        need(0<=_age(m,f)<=m['history_s'], 'OBSERVATION_TIME', 'Frame outside history window')
    need(clock.get(m['reference_time_id'])==m['reference_time_s'], 'REFERENCE_TIME', 'Reference must resolve to acquisition timestamp')
    need(m['valid_until_s']>=m['reference_time_s']+m['horizon_s'], 'TIME_COVERAGE', 'Capture expires before horizon')
    need(cap['safety']['prefix_s']<=min(.5,m['step_s']), 'PREFIX', 'Only a short prefix is supported')
    need(all(abs(cap['ego'][k])<1e-8 for k in ('x_m','y_m','yaw_rad')), 'EGO_FRAME', 'Origin is measured ego body center at reference, with yaw zero')
    need(cap['vehicle']['center_to_rear_axle_m']<=cap['vehicle']['rear_m'], 'VEHICLE', 'Rear axle outside vehicle')
    if 'acquisition_metadata' in cap:
        import numpy as np
        a=cap['acquisition_metadata']
        need(abs(a['center_offset_assumption_m']-cap['vehicle']['center_to_rear_axle_m'])<1e-8, 'CALIBRATION_REFERENCE', 'Camera and vehicle center offsets disagree')
        pairs={(f['id'],f['time_id']) for f in frames}
        need(len(a['ego_history'])==len(frames) and {(x['frame_id'],x['time_id']) for x in a['ego_history']}==pairs, 'EGO_HISTORY', 'Ego history does not match acquisition records')
        need(all(max(abs(x['center_xy_m'][0]),abs(x['center_xy_m'][1]),abs(x['yaw_rad']))<1e-8 for x in a['ego_history'] if x['time_id']==m['reference_time_id']), 'EGO_HISTORY', 'Reference-time ego history must be rebased')
        need(all(f['camera_id'] in a['calibrations'] for f in frames), 'CALIBRATION_MISSING', 'Captured camera calibration missing')
        for cal in a['calibrations'].values():
            R=np.asarray(cal['camera_to_center_rotation']); K=np.asarray(cal['camera_intrinsic'])
            need(np.allclose(R.T@R,np.eye(3),atol=1e-6) and abs(np.linalg.det(R)-1)<1e-6 and abs(np.linalg.det(K))>1e-9, 'CALIBRATION', 'Invalid camera calibration')
    return {f['id']:f for f in frames}


def _evidence(item, frames):
    """Paired model citations only, never a fabricated reference-time image."""
    fs=item['frame_ids']; ts=item['time_ids']
    need(len(fs)==len(ts) and len(set(fs))==len(fs), 'MISSING_EVIDENCE', 'v3 evidence lists must be unique index-paired frame/time citations')
    for fid,tid in zip(fs,ts):
        need(fid in frames and frames[fid]['time_id']==tid, 'MISSING_EVIDENCE', 'Evidence does not reference the actual acquisition pair', frame_id=fid, time_id=tid)
    return set(zip(fs,ts))


def _scope(item, objects, scenarios, horizon, history):
    need(-history<=item['start_s']<=item['end_s']<=horizon, 'SCOPE_TIME', 'Premise/unknown time scope lies outside capture history/horizon')
    need(set(item['object_ids'])<=objects, 'SCOPE_OBJECT', 'Scope cites unknown objects')
    need(set(item['scenario_ids'])<=scenarios, 'SCOPE_SCENARIO', 'Scope cites unknown scenarios')
    if item['space_polygon_m'] is not None: polygon(item['space_polygon_m'])


def _premises(ids, registry, oid, sids, left, right):
    need(bool(ids), 'PREMISE_MISSING', 'Supported coverage/alignment must bind explicit conditional premises', 'tool_infeasible', object_id=oid)
    active=[]
    for pid in ids:
        need(pid in registry, 'PREMISE_REFERENCE', 'Unknown conditional premise', premise_id=pid)
        p=registry[pid]
        need((not p['object_ids'] or oid in p['object_ids']) and (not p['scenario_ids'] or bool(set(sids)&set(p['scenario_ids']))) and p['start_s']<=left and p['end_s']>=right,
             'PREMISE_SCOPE', 'Conditional premise does not cover this object, scenario and whole time interval', premise_id=pid, object_id=oid)
        active.append(p)
    for sid in sids:
        need(any(not p['scenario_ids'] or sid in p['scenario_ids'] for p in active),
             'PREMISE_SCOPE', 'Each retained scenario needs its own applicable premise; branch-specific premises are not asserted for other branches', object_id=oid, scenario_id=sid)


def _premise_space(ids, registry, oid, sid, geometry):
    """A declared finite premise domain cannot silently authorize outside it."""
    for pid in ids:
        p=registry[pid]
        if (not p['object_ids'] or oid in p['object_ids']) and (not p['scenario_ids'] or sid in p['scenario_ids']):
            need(p['space_polygon_m'] is None or polygon(p['space_polygon_m']).covers(geometry),
                 'PREMISE_SPACE', 'Declared geometry lies outside its finite premise scope', 'tool_infeasible', object_id=oid, scenario_id=sid, premise_id=pid)


def _body(o):
    b=o['body_geometry']; x,y=o['position_m'];c=math.cos(b['yaw_rad']);s=math.sin(b['yaw_rad'])
    return Polygon([(x+c*dx-s*dy,y+s*dx+c*dy) for dx,dy in [(-b['length_m']/2,-b['width_m']/2),(b['length_m']/2,-b['width_m']/2),(b['length_m']/2,b['width_m']/2),(-b['length_m']/2,b['width_m']/2)]])


def _scale_support(support, h, frames, premises):
    """Validate a reviewable model-authored conditional scale argument.

    This does not recover scale from pixels or certify the chosen prior. Every
    positive dimension prior must refer to an actually cited actor and its
    explicit native body hypothesis, rather than merely relabelling a number.
    """
    need(support is not None,'MISSING_SCALE_SUPPORT','A scale_basis label is insufficient: provide explicit model-native reasoning with cited conditional dimension anchors','tool_infeasible')
    need(bool(support['metric_reasoning'].strip()),'MISSING_SCALE_SUPPORT','Metric-scale reasoning must state its conditional basis, not whitespace','tool_infeasible')
    pairs=_evidence(support,frames);objects={o['id']:o for o in h['objects']};covered=set();seen=set()
    need(set(support['premise_ids'])<=set(premises),'PREMISE_REFERENCE','Scale support cites an unknown premise')
    for anchor in support['anchors']:
        oid=anchor['object_id'];key=(oid,anchor['dimension'])
        need(key not in seen,'SCALE_ANCHOR','Duplicate scale anchor cannot supply additional support',object_id=oid);seen.add(key)
        need(oid in objects and objects[oid]['body_geometry'] is not None and objects[oid]['position_m'] is not None,'SCALE_ANCHOR','Scale anchor needs an existing native body-center hypothesis',object_id=oid)
        o=objects[oid]
        need(Decimal(str(anchor['value_m']))==Decimal(str(o['body_geometry'][anchor['dimension']])), 'SCALE_ANCHOR','Conditional dimension prior conflicts with the native body dimension',object_id=oid,dimension=anchor['dimension'])
        need(bool(anchor['rationale'].strip()),'MISSING_SCALE_SUPPORT','Each adopted scale prior needs a reviewable conditional rationale','tool_infeasible',object_id=oid)
        overlap=_evidence(o,frames)&pairs
        observed={(x['frame_id'],x['time_id']) for x in o['alignment']['evidence_positions'] if x['observed_actor']}
        need(bool(overlap) and overlap<=observed,'SCALE_ANCHOR_EVIDENCE','Scale anchor must be seen in its cited original acquisition evidence, not an unrelated frame','tool_infeasible',object_id=oid)
        covered|=overlap
        sids=[s['id'] for s in o['scenarios']]
        applicable=[pid for pid in support['premise_ids'] if (not premises[pid]['object_ids'] or oid in premises[pid]['object_ids']) and (not premises[pid]['scenario_ids'] or bool(set(sids)&set(premises[pid]['scenario_ids'])))]
        _premises(applicable,premises,oid,sids,0,0)
        for sid in sids:_premise_space(applicable,premises,oid,sid,_body(o))
    need(covered==pairs,'SCALE_ANCHOR_EVIDENCE','All scale-support image/time citations must belong to a stated observed dimension anchor','tool_infeasible')


def normalize(capture, output, provenance, allow_synthetic=False):
    """Return explicit legacy physical output + full trace, without predictions.

    Inclusion uses closed source intervals: a cell touching a source boundary
    retains that source's *entire* polygon. This can be conservative, never a
    smaller body set. Source and alignment residuals must fit the one total
    object residual; the physical tools apply that total exactly once.
    """
    frames=_check_capture(capture,provenance,output,allow_synthetic)
    m=capture['metadata'];h=output['hypotheses'];d=output['decision'];times=grid(m);H=times[-1]
    need(h['reference_time_id']==m['reference_time_id'], 'DYNAMIC_ALIGNMENT', 'State epoch must resolve to the trusted acquisition reference')
    need(h['coordinate_frame']==m['coordinate_frame'], 'COORDINATES', 'Unsupported coordinate frame')
    need(h['scale_basis']=='model_metric_hypothesis', 'MISSING_SCALE', 'Native metric hypothesis with evidence is required; no unverified projection or scale relabelling', 'tool_infeasible')
    need(h['metric_scale_support'] is not None,'MISSING_SCALE_SUPPORT','Metric labels do not establish scale; explicit conditional scale support is required','tool_infeasible')
    need(h['road'] is not None and not h['unbounded_unknown'], 'UNBOUNDED_UNKNOWN', 'Unknown road/extent blocks planning', 'tool_infeasible')
    need(d['scene_id']==m['scene_id'] and d['revision']==m['revision'], 'SCENE_VERSION_MISMATCH', 'Decision belongs to another round')
    need(d['valid_until_s']>=m['reference_time_s']+H, 'DECISION_EXPIRED', 'Decision does not cover horizon')
    need({'target_region','vehicle_limits','collision_free','road'}<=set(d['hard_requirements']), 'SEMANTIC_CONTRACT', 'Missing mandatory hard requirements')
    need((d['behavior']=='stop_yield')==(d['stop'] is not None), 'STOP_INTENT', 'Stop requirements inconsistent with behavior')
    if d['stop']:
        need({'stop','yield'}<=set(d['hard_requirements']) and 0<=d['stop']['deadline_s']<=H, 'STOP_INTENT', 'Stop deadline/hard requirements invalid')
    polygon(d['target_region'], True)
    for r in [h['road']]+h['unknown_regions']:
        _evidence(r,frames);polygon(r['polygon_m'])
        need(r['uncertainty_m'] is not None, 'UNKNOWN_UNCERTAINTY', 'No usable geometric uncertainty extent', 'tool_infeasible')
    polygon(h['road']['polygon_m'],True)
    object_ids=[o['id'] for o in h['objects']]
    need(len(object_ids)==len(set(object_ids)), 'DUPLICATE_ID', 'Duplicate object')
    need(set(d['yield_to'])<=set(object_ids), 'YIELD_TARGET', 'Unknown yield target')
    for e in h['traffic_evidence']:
        _evidence(e,frames)
        need(e['rule'] in ('stop_required','yield_required'), 'UNSUPPORTED_TRAFFIC_RULE', 'Unsupported traffic rule', 'tool_infeasible')
        need(e['state'] in ('active','inactive'), 'UNKNOWN_TRAFFIC_CONDITION', 'Unknown traffic requirement blocks planning', 'tool_infeasible')
        if e['state']=='active':need(d['behavior']=='stop_yield' and (e['rule']!='yield_required' or bool(d['yield_to'])), 'TRAFFIC_INTENT_CONFLICT', 'Decision contradicts traffic evidence', 'tool_infeasible')
    scenario_ids=[s['id'] for o in h['objects'] for s in o['scenarios']]
    need(len(scenario_ids)==len(set(scenario_ids)), 'DUPLICATE_SCENARIO', 'Scenario IDs are globally unique in v3')
    pids=[p['id'] for p in h['premises']]
    need(len(pids)==len(set(pids)), 'DUPLICATE_PREMISE', 'Duplicate conditional premise')
    premises={p['id']:p for p in h['premises']}
    for p in premises.values():_scope(p,set(object_ids),set(scenario_ids),H,m['history_s'])
    _scale_support(h['metric_scale_support'],h,frames,premises)
    uids=[u['id'] for u in h['unknowns']]
    need(len(uids)==len(set(uids)), 'DUPLICATE_UNKNOWN', 'Duplicate unknown record')
    for u in h['unknowns']:
        _scope(u,set(object_ids),set(scenario_ids),H,m['history_s'])
        need(set(u['premise_ids'])<=set(pids), 'PREMISE_REFERENCE', 'Unknown record cites missing premise')
        need(u['kind']=='multiple_conditionals', 'TYPED_UNKNOWN_BLOCKING', 'No relevance pruning: absent, unexpressible or unbounded coverage still blocks planning', 'tool_infeasible', unknown_id=u['id'], unknown_kind=u['kind'], object_ids=u['object_ids'],time_interval_s=[u['start_s'],u['end_s']])
        need(bool(u['object_ids']) and bool(u['scenario_ids']), 'CONDITIONAL_SCOPE', 'Multiple-conditionals record must identify objects and scenarios')
    lower=deepcopy(output);lower.pop('contract_version');lower.pop('coverage_profile');lower.pop('measurement_semantics')
    lh=lower['hypotheses'];lh.pop('premises');lh.pop('unknowns');lh.pop('metric_scale_support')
    trace=dict(version=VERSION,coverage_profile=COVERAGE_PROFILE,measurement_semantics=deepcopy(MEASUREMENT_SEMANTICS),
               source_output=deepcopy(output),source_output_digest=digest(output),lowered_output_digest=None,objects=[],metric_scale_support=deepcopy(h['metric_scale_support']),
               source_intervals_closed=True, refinement='include_all_closed_intersecting_source_sets_without_shrinking',
               residual_application='one_total_object_radius >= alignment_residual + each_source_geometry_residual; never added again',
               assumptions='Model-proposed premises and adverse-scenario inventory are conditional, uncalibrated and not independently certified')
    for index,o in enumerate(h['objects']):
        oid=o['id']; pairs=_evidence(o,frames);a=o['alignment']
        need(o['state_time_id']==m['reference_time_id'], 'STATE_TIME', 'Native model state must be at the trusted reference; tools do not extrapolate stale state', 'tool_infeasible', object_id=oid)
        need(o['position_m'] is not None and o['body_geometry'] is not None, 'BODY_STATE_UNKNOWN', 'This explicit body-center profile requires native reference position and body box', 'tool_infeasible', object_id=oid)
        need(o['uncertainty_m'] is not None, 'UNKNOWN_UNCERTAINTY', 'Unknown total residual blocks planning', 'tool_infeasible', object_id=oid)
        need(a['residual_m']<=o['uncertainty_m'], 'RESIDUAL_BUDGET', 'Total object residual must include alignment residual', object_id=oid)
        evidence={(x['frame_id'],x['time_id']):x for x in a['evidence_positions']}
        need(len(evidence)==len(a['evidence_positions']) and set(evidence)==pairs, 'ALIGNMENT_EVIDENCE', 'Every cited actor observation requires one original paired model position', object_id=oid)
        for pair,x in evidence.items():
            need(x['observed_actor'], 'ACTOR_NOT_OBSERVED', 'Do not cite images in which the model did not observe this actor', 'tool_infeasible', object_id=oid)
        ages=[_age(m,frames[f]) for f,t in pairs]
        asynchronous=any(age!=0 for age in ages)
        sids=[s['id'] for s in o['scenarios']]
        need(bool(sids) and o['scenario_inventory_complete'] and any(s['role']=='reasonable_adverse' for s in o['scenarios']), 'SCENARIO_INCOMPLETE', 'Model must explicitly retain a reasonable adverse scenario inventory', 'tool_infeasible', object_id=oid)
        for s in o['scenarios']:_premises(s['premise_ids'],premises,oid,[s['id']],0,H)
        if a['method']=='direct_reference_observation':
            need(not asynchronous and a['motion_bound'] is None, 'ALIGNMENT_REQUIRED', 'Asynchronous evidence needs model motion bounds, not a small-time exemption', 'tool_infeasible', object_id=oid)
            _premises(a['premise_ids'],premises,oid,sids,0,0)
            for x in evidence.values():need(math.dist(x['position_m'],o['position_m'])<=a['residual_m']+1e-8,'ALIGNMENT_CONFLICT','Native state conflicts with direct reference observation',object_id=oid)
        else:
            b=a['motion_bound'];need(b is not None,'MOTION_BOUND_MISSING','Model must provide motion bounds; ego compensation is insufficient','tool_infeasible',object_id=oid)
            need(b['covers_all_declared_scenarios'], 'ALIGNMENT_SCENARIO_GAP', 'Motion bound must cover all declared scenarios','tool_infeasible',object_id=oid)
            if o['velocity_mps'] is not None:
                need(math.hypot(*o['velocity_mps'])<=b['max_speed_mps'], 'VELOCITY_BOUND_CONFLICT', 'Native absolute ground velocity contradicts the alignment speed bound at reference', 'tool_infeasible', object_id=oid)
            _premises(a['premise_ids'],premises,oid,sids,-max(ages),0)
            for (f,t),x in evidence.items():
                age=_age(m,frames[f])
                bound=min(b['displacement_bound_m'],b['max_speed_mps']*age)+a['residual_m']
                need(math.dist(x['position_m'],o['position_m'])<=bound+1e-8,'ALIGNMENT_CONFLICT','Model observed/reference state displacement exceeds its declared motion and residual bounds','tool_infeasible',object_id=oid,frame_id=f,computed_m=math.dist(x['position_m'],o['position_m']),threshold_m=bound)
        for sid in sids:
            for x in evidence.values():_premise_space(a['premise_ids'],premises,oid,sid,Point(x['position_m']))
            _premise_space(a['premise_ids'],premises,oid,sid,Point(o['position_m']))
        sources=o['occupancy'];need(sources is not None,'OCCUPANCY_UNKNOWN','Null is not an empty future body occupancy','tool_infeasible',object_id=oid)
        source_ids=[s['id'] for s in sources];need(len(source_ids)==len(set(source_ids)),'DUPLICATE_SOURCE','Duplicate envelope source',object_id=oid)
        per_scenario={sid:[] for sid in sids}
        body=_body(o)
        for src in sources:
            need(src['status']=='supported','PLACEHOLDER_OCCUPANCY','A placeholder rectangle is not supported coverage','tool_infeasible',object_id=oid,source_id=src['id'])
            need(0<=src['start_s']<src['end_s']<=H,'OCCUPANCY_SOURCE_TIME','Source must cover a nonzero whole interval within the horizon',object_id=oid,source_id=src['id'])
            need(_evidence(src,frames)<=pairs,'OCCUPANCY_EVIDENCE','Source must cite actual actor observations, not unrelated reference frames',object_id=oid)
            residual_sum=Decimal(str(a['residual_m']))+Decimal(str(src['residual_m']))
            need(residual_sum<=Decimal(str(o['uncertainty_m'])),'RESIDUAL_BUDGET','Total object residual must include the sum of alignment and source-geometry residuals',object_id=oid)
            present=[x['scenario_id'] for x in src['alternatives']]
            need(len(present)==len(set(present)) and set(present)<=set(sids),'SCENARIO_REFERENCE','Unknown/duplicate scenario alternative',object_id=oid)
            _premises(src['premise_ids'],premises,oid,present,src['start_s'],src['end_s'])
            for alt in src['alternatives']:
                poly=polygon(alt['polygon_m']);per_scenario[alt['scenario_id']].append(src)
                _premise_space(src['premise_ids'],premises,oid,alt['scenario_id'],poly)
                if src['start_s']==0:
                    need(poly.covers(body),'BODY_ENVELOPE_CONFLICT','Nominal initial envelope must enclose the oriented actor body before residual inflation, not only its center','tool_infeasible',object_id=oid,scenario_id=alt['scenario_id'])
        for sid,seq in per_scenario.items():
            end=0.
            for src in sorted(seq,key=lambda x:(x['start_s'],x['end_s'])):
                need(src['start_s']<=end,'OCCUPANCY_GAP','Declared scenario has a future coverage gap','tool_infeasible',object_id=oid,scenario_id=sid,gap_start_s=end,gap_end_s=src['start_s'])
                end=max(end,src['end_s'])
            need(end>=H,'OCCUPANCY_GAP','Every retained scenario must cover the complete horizon','tool_infeasible',object_id=oid,scenario_id=sid,covered_until_s=end)
        cells=[];mapping=[]
        for left,right in zip(times,times[1:]):
            chosen=[s for s in sources if s['start_s']<=right+1e-8 and s['end_s']>=left-1e-8]
            alternatives=[deepcopy(alt['polygon_m']) for s in chosen for alt in s['alternatives']]
            cells.append(dict(start_s=left,end_s=right,kind='whole_interval_envelope',alternatives_m=alternatives))
            mapping.append(dict(start_s=left,end_s=right,source_ids=[s['id'] for s in chosen],alternatives=[dict(source_id=s['id'],scenario_id=alt['scenario_id'],source_interval_s=[s['start_s'],s['end_s']],premise_ids=deepcopy(s['premise_ids']),residual_m=s['residual_m'],frame_ids=deepcopy(s['frame_ids']),time_ids=deepcopy(s['time_ids'])) for s in chosen for alt in s['alternatives']]))
        lo=lh['objects'][index]
        for key in ('state_time_id','body_geometry','alignment','scenarios','scenario_inventory_complete'):lo.pop(key)
        lo['occupancy']=cells
        trace['objects'].append(dict(object_id=oid,alignment=deepcopy(a),cell_sources=mapping,total_residual_m=o['uncertainty_m']))
    # Conditional records are informational only when all bound scenario IDs
    # belong to the cited objects and every such mode survived full coverage.
    for u in h['unknowns']:
        permitted={s['id'] for o in h['objects'] if o['id'] in u['object_ids'] for s in o['scenarios']}
        need(set(u['scenario_ids'])<=permitted,'CONDITIONAL_SCOPE','Conditional record cites scenario of another object')
        for oid in u['object_ids']:
            local={s['id'] for o in h['objects'] if o['id']==oid for s in o['scenarios']}&set(u['scenario_ids'])
            need(bool(local),'CONDITIONAL_SCOPE','Each object in a conditional record needs its own referenced scenario', object_id=oid)
            relevant=[pid for pid in u['premise_ids'] if (not premises[pid]['object_ids'] or oid in premises[pid]['object_ids']) and (not premises[pid]['scenario_ids'] or bool(local&set(premises[pid]['scenario_ids'])))]
            _premises(relevant,premises,oid,local,u['start_s'],u['end_s'])
    validate('agent_output_v2_1.schema.json',lower)
    trace['lowered_output_digest']=digest(lower)
    return lower,trace


def construct(capture, output, provenance, allow_synthetic=False):
    lower,trace=normalize(capture,output,provenance,allow_synthetic)
    p=deepcopy(provenance);p['metric_contract_trace']=trace
    scene=dict(metadata=deepcopy(capture['metadata']),capture=deepcopy(capture),decision=lower['decision'],hypotheses=lower['hypotheses'],provenance=p,
               claim='conditional_on_model_hypotheses_and_whole_interval_coverage; explicit_metric_temporal_contract_3.0.0-exp2; uncertainty is not calibrated probability')
    validate('scene_v2.schema.json',scene)
    return scene


def validate_scene(scene):
    """Independent source-to-physical consistency check for T4 and acceptance."""
    p=deepcopy(scene['provenance']);trace=p.pop('metric_contract_trace',None)
    need(p.get('agent_output_contract')==VERSION and isinstance(trace,dict),'METRIC_TRACE','v3 physical scene must preserve its source trace')
    expected=construct(scene['capture'],trace.get('source_output'),p,allow_synthetic=p.get('source')=='synthetic_fixture')
    need(expected==scene,'METRIC_TRACE_MISMATCH','Physical scene differs from revalidated native v3 output and source intervals')
    return True
