from copy import deepcopy
from .contracts import validate,need,grid,digest,model_contract
from .geometry import polygon

def _construct_legacy(capture,output,provenance,allow_synthetic=False):
    validate('capture_v2.schema.json',capture);validate(model_contract(provenance.get('agent_output_contract','2.0.0'))[0],output)
    m=capture['metadata'];h=output['hypotheses'];d=output['decision'];times=grid(m)
    need(provenance['source']=='online_model' or (allow_synthetic and provenance['source']=='synthetic_fixture'),'SOURCE','Synthetic input requires explicit test mode')
    need(provenance.get('output_digest')==digest(output),'SOURCE','Output does not match provenance')
    need(m['valid_until_s']>=m['reference_time_s']+m['horizon_s'],'TIME_COVERAGE','Capture expires before horizon')
    need(capture['safety']['prefix_s']<=min(.5,m['step_s']),'PREFIX','Only a short prefix up to one step / 0.5 s is supported')
    frames=capture['observations'];ids={f['id'] for f in frames};tids={f['time_id'] for f in frames}
    need(len(ids)==len(frames),'DUPLICATE_ID','Duplicate frame id')
    clock={}
    for f in frames:
        need(f['time_id'] not in clock or clock[f['time_id']]==f['capture_time_s'],'CLOCK_CONFLICT','Conflicting capture timestamps')
        clock[f['time_id']]=f['capture_time_s']
        need(0<=m['reference_time_s']-f['capture_time_s']<=m['history_s']+1e-8,'OBSERVATION_TIME','Frame outside history window')
    need(clock.get(m['reference_time_id'])==m['reference_time_s'],'REFERENCE_TIME','Reference must resolve to acquisition timestamp')
    need(h['reference_time_id']==m['reference_time_id'],'DYNAMIC_ALIGNMENT','Dynamic states must be model-aligned to current acquisition record; ego compensation alone is insufficient')
    need(h['coordinate_frame']==m['coordinate_frame'],'COORDINATES','Unsupported frame')
    need(h['scale_basis']!='unknown','MISSING_SCALE','Metric scale unsupported; supply grounded hypothesis/calibration','tool_infeasible')
    need(h['scale_basis']!='calibrated_ground_plane','CALIBRATED_PROJECTION_UNVERIFIED',
         'This native-metric profile has no verified ground-plane/pixel-projection contract; calibration parameters alone do not establish metric scale','tool_infeasible')
    need(h['road'] is not None and not h['unbounded_unknown'],'UNBOUNDED_UNKNOWN','Unknown road/extent blocks planning','tool_infeasible')
    need(d['scene_id']==m['scene_id'] and d['revision']==m['revision'],'SCENE_VERSION_MISMATCH','Decision belongs to another round')
    need(d['valid_until_s']>=m['reference_time_s']+m['horizon_s'],'DECISION_EXPIRED','Decision does not cover horizon')
    need(set(['target_region','vehicle_limits','collision_free','road'])<=set(d['hard_requirements']),'SEMANTIC_CONTRACT','Missing mandatory hard requirements')
    need((d['behavior']=='stop_yield')==(d['stop'] is not None),'STOP_INTENT','Stop requirements inconsistent with behavior')
    if d['stop']:
        need({'stop','yield'}<=set(d['hard_requirements']) and 0<=d['stop']['deadline_s']<=times[-1],'STOP_INTENT','Stop deadline/hard requirements invalid')
    polygon(d['target_region'],True);polygon(h['road']['polygon_m'],True)
    def evidence(item):
        need(set(item['frame_ids'])<=ids and set(item['time_ids'])<=tids,'MISSING_EVIDENCE','Unknown evidence reference')
        need(all(any(f['id'] in item['frame_ids'] and f['time_id']==t for f in frames) for t in item['time_ids']), 'MISSING_EVIDENCE','Evidence times do not reference cited frames')
    for r in [h['road']]+h['unknown_regions']:
        evidence(r);polygon(r['polygon_m']);need(r['uncertainty_m'] is not None,'UNKNOWN_UNCERTAINTY','No usable geometric uncertainty extent','tool_infeasible')
    object_ids=[o['id'] for o in h['objects']]
    need(len(object_ids)==len(set(object_ids)),'DUPLICATE_ID','Duplicate object')
    need(set(d['yield_to'])<=set(object_ids),'YIELD_TARGET','Unknown yield target')
    for e in h['traffic_evidence']:
        evidence(e)
        need(e['rule'] in ('stop_required','yield_required'),'UNSUPPORTED_TRAFFIC_RULE','Translate supported traffic evidence into a supported behavior or request more information','tool_infeasible')
        need(e['state'] in ('active','inactive'),'UNKNOWN_TRAFFIC_CONDITION','Unknown traffic requirement blocks planning','tool_infeasible')
        if e['state']=='active':
            need(d['behavior']=='stop_yield' and (e['rule']!='yield_required' or bool(d['yield_to'])),'TRAFFIC_INTENT_CONFLICT','Decision contradicts model traffic evidence','tool_infeasible')
    for o in h['objects']:
        evidence(o)
        need(m['reference_time_id'] in o['time_ids'],'DYNAMIC_ALIGNMENT','Dynamic state lacks reference-time evidence')
        need(o['uncertainty_m'] is not None,'UNKNOWN_UNCERTAINTY','Unknown uncertainty extent blocks planning','tool_infeasible')
        need(o['occupancy'] is not None,'OCCUPANCY_UNKNOWN','Model explicitly lacks whole-interval occupancy; supply additional evidence','tool_infeasible')
        need(len(o['occupancy'])==len(times)-1,'OCCUPANCY_GAP','Missing whole-interval occupancy')
        for k,c in enumerate(o['occupancy']):
            need(abs(c['start_s']-times[k])<1e-8 and abs(c['end_s']-times[k+1])<1e-8,'OCCUPANCY_GAP','Occupancy times do not cover grid')
            for p in c['alternatives_m']:polygon(p)
        if o['position_m'] is not None:
            from shapely.geometry import Point
            need(any(polygon(p).distance(Point(o['position_m']))<=o['uncertainty_m']+1e-8 for p in o['occupancy'][0]['alternatives_m']),'HYPOTHESIS_CONFLICT','Position conflicts with initial occupancy')
    need(all(abs(capture['ego'][k])<1e-8 for k in ('x_m','y_m','yaw_rad')),'EGO_FRAME','ego_at_reference requires center position and yaw zero; rebase from measured ego state')
    if 'acquisition_metadata' in capture:
        import numpy as np
        a=capture['acquisition_metadata']
        need(abs(a['center_offset_assumption_m']-capture['vehicle']['center_to_rear_axle_m'])<1e-8,'CALIBRATION_REFERENCE','Camera and vehicle center offsets disagree')
        pairs={(f['id'],f['time_id']) for f in frames}
        need(len(a['ego_history'])==len(frames) and {(x['frame_id'],x['time_id']) for x in a['ego_history']}==pairs,'EGO_HISTORY','Ego history does not match acquisition records')
        need(all(max(abs(x['center_xy_m'][0]),abs(x['center_xy_m'][1]),abs(x['yaw_rad']))<1e-8 for x in a['ego_history'] if x['time_id']==m['reference_time_id']),'EGO_HISTORY','Reference-time ego history must be rebased to center zero')
        need(all(f['camera_id'] in a['calibrations'] for f in frames),'CALIBRATION_MISSING','A captured camera has no calibration')
        for cal in a['calibrations'].values():
            R=np.asarray(cal['camera_to_center_rotation']);K=np.asarray(cal['camera_intrinsic'])
            need(np.allclose(R.T@R,np.eye(3),atol=1e-6) and abs(np.linalg.det(R)-1)<1e-6 and abs(np.linalg.det(K))>1e-9,'CALIBRATION','Invalid rotation or camera intrinsics')
    need(capture['vehicle']['center_to_rear_axle_m']<=capture['vehicle']['rear_m'],'VEHICLE','Rear axle outside vehicle')
    return dict(metadata=deepcopy(m),capture=deepcopy(capture),decision=deepcopy(d),hypotheses=deepcopy(h),provenance=deepcopy(provenance),
                claim='conditional_on_model_hypotheses_and_whole_interval_coverage; uncertainty is not calibrated probability')


def construct(capture,output,provenance,allow_synthetic=False):
    """Explicit experimental selection; legacy defaults and guard body retained."""
    need(provenance.get('agent_output_contract')!='3.0.0-exp1','MODEL_CONTRACT','The exp1 engineering draft is frozen evidence, not an active runtime contract; select exp2 explicitly')
    if provenance.get('agent_output_contract')=='3.0.0-exp2':
        from .metric_contract import construct as construct_metric
        return construct_metric(capture,output,provenance,allow_synthetic)
    return _construct_legacy(capture,output,provenance,allow_synthetic)
