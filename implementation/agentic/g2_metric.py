"""Strict OFFLINE G2 quantities: no runtime/model imports and no pass threshold.

Unknown actor reference points, relative velocities and unmatched evaluation
domains are not repaired. They produce null metrics with explicit reasons.
Synthetic numerical tests require an explicit flag and are never online results.
"""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess

from shapely.geometry import Point
from shapely.ops import unary_union

from .contracts import Failure, digest, need, validate
from .evidence_io import reserve_directory, write_json_new
from .geometry import polygon

VERSION = '3.0.0-exp2'
SEMANTICS = {
    'coordinate_frame': 'ego_at_reference',
    'origin': 'ego_body_center_at_reference',
    'axes': 'x_forward_y_left_fixed_at_reference',
    'actor_reference_point': 'annotation_box_center_ground_xy',
    'velocity_semantics': 'absolute_ground_velocity_in_fixed_reference_axes',
    'uncertainty_semantics': 'noncalibrated_total_residual_radius_once',
}


def _stats(rows, key):
    values = [x[key] for x in rows if x.get(key) is not None]
    return dict(n=len(values), mean=statistics.mean(values) if values else None,
                min=min(values) if values else None, max=max(values) if values else None)


def _shape(value):
    try:
        return polygon(value)
    except (Failure, TypeError, ValueError, KeyError):
        return None


def _vector(value):
    return (isinstance(value, list) and len(value) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value))


def _covers(spans, start, end):
    """Closed intervals must cover the requested time exactly, without epsilon.

    Geometry tolerances cannot close even a small positive temporal gap.
    Multiple overlapping sources for the same scenario are allowed.
    """
    cursor = start
    for left, right in sorted(spans):
        if right < start or left > end:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= end:
            return True
    return cursor >= end


def _frame_checks(record, gt, config):
    """Conditions for numerical comparability; none authorizes planning."""
    output = record['output']; h = output.get('hypotheses', {})
    reasons = []
    if record['provenance'].get('agent_output_contract') != VERSION or output.get('contract_version') != VERSION:
        reasons.append('LEGACY_OR_UNDECLARED_MEASUREMENT_CONTRACT')
    if output.get('measurement_semantics') != SEMANTICS:
        reasons.append('MODEL_MEASUREMENT_SEMANTICS_MISSING_OR_INCOMPATIBLE')
    if h.get('scale_basis') != 'model_metric_hypothesis':
        reasons.append('MODEL_METRIC_SCALE_UNSUPPORTED')
    if gt is None:
        return reasons + ['GT_SAMPLE_MISSING'], None
    if gt.get('measurement_semantics') != SEMANTICS:
        reasons.append('GT_MEASUREMENT_SEMANTICS_MISSING_OR_INCOMPATIBLE')
    if h.get('coordinate_frame') != gt.get('coordinate_frame') or gt.get('coordinate_frame') != 'ego_at_reference':
        reasons.append('COORDINATE_FRAME_MISMATCH')
    if h.get('units') != gt.get('units') or gt.get('units') != 'm_s_rad':
        reasons.append('GT_UNITS_UNDECLARED_OR_MISMATCH')
    if h.get('reference_time_id') != gt.get('reference_time_id'):
        reasons.append('REFERENCE_TIME_MISMATCH')
    if record['provenance'].get('image_sha256') != gt.get('image_sha256'):
        reasons.append('IMAGE_IDENTITY_MISMATCH')
    capture = record.get('capture')
    if not isinstance(capture, dict):
        reasons.append('CAPTURE_REFERENCE_DEFINITION_MISSING')
    else:
        offset = capture.get('vehicle', {}).get('center_to_rear_axle_m')
        if offset is None or offset != gt.get('ego_center_to_rear_axle_m'):
            reasons.append('EGO_REFERENCE_OFFSET_MISSING_OR_MISMATCH')
        meta = capture.get('metadata', {})
        if meta.get('reference_time_id') != gt.get('reference_time_id') or meta.get('reference_time_s') != gt.get('reference_time_s'):
            reasons.append('ACQUISITION_REFERENCE_MISMATCH')
    domain = config.get('evaluation_domain')
    region = _shape(domain.get('polygon_m')) if isinstance(domain, dict) else None
    if not isinstance(domain, dict) or not domain.get('id') or region is None:
        reasons.append('EVALUATION_DOMAIN_MISSING_OR_INVALID')
    if domain != gt.get('evaluation_domain'):
        reasons.append('GT_EVALUATION_DOMAIN_UNDECLARED_OR_MISMATCH')
    return reasons, region


def _future(o, target, horizon, region):
    result = dict(status='NOT_EVALUABLE', minimum_interval_coverage=None,
                  mean_interval_coverage=None, mean_envelope_area_ratio=None,
                  expected_intervals=0, evaluable_intervals=0, intervals=[])
    sources = o.get('occupancy')
    if sources is None:
        return dict(result, status='MODEL_OCCUPANCY_MISSING')
    if not sources or any(x.get('kind') != 'whole_interval_body_envelope' or x.get('status') != 'supported' for x in sources):
        return dict(result, status='MODEL_INTERVAL_SEMANTICS_OR_SUPPORT_INVALID')
    scenarios = {x['id'] for x in o.get('scenarios', [])}
    if not scenarios or o.get('scenario_inventory_complete') is not True:
        return dict(result, status='MODEL_SCENARIO_INVENTORY_UNCONFIRMED')
    uncertainty = o.get('uncertainty_m')
    if not isinstance(uncertainty, (int, float)) or not math.isfinite(uncertainty) or uncertainty < 0:
        return dict(result, status='MODEL_UNCERTAINTY_MISSING_OR_INVALID')
    scenario_sources = {sid: [] for sid in scenarios}
    for source in sources:
        supplied = {a.get('scenario_id') for a in source.get('alternatives', [])}
        if not supplied or not supplied <= scenarios:
            return dict(result, status='MODEL_UNDECLARED_OR_EMPTY_SOURCE_SCENARIOS')
        left, right = source.get('start_s'), source.get('end_s')
        if (not isinstance(left, (int, float)) or not isinstance(right, (int, float))
                or not math.isfinite(left) or not math.isfinite(right) or left < 0 or right <= left):
            return dict(result, status='MODEL_FUTURE_TIME_INVALID')
        for sid in supplied:
            scenario_sources[sid].append(source)
        alignment_residual = o.get('alignment', {}).get('residual_m')
        if (source.get('residual_m') is None or alignment_residual is None or alignment_residual < 0
                or source['residual_m'] < 0
                or Decimal(str(source['residual_m']))+Decimal(str(alignment_residual)) > Decimal(str(uncertainty))):
            return dict(result, status='MODEL_RESIDUAL_NOT_INCLUDED_IN_TOTAL')
    missing = [sid for sid, subset in scenario_sources.items()
               if not _covers([(x['start_s'], x['end_s']) for x in subset], 0., horizon)]
    if missing:
        return dict(result, status='MODEL_FUTURE_TIME_GAP', missing_scenarios=sorted(missing))
    result['scenario_source_ids'] = {sid: [x['id'] for x in subset] for sid, subset in sorted(scenario_sources.items())}
    truth = target.get('occupancy')
    if not truth:
        return dict(result, status='GT_FUTURE_MISSING')
    cursor = 0.
    eligible = []
    for cell in truth:
        if cell.get('kind') != 'whole_interval_body_envelope':
            return dict(result, status='GT_FUTURE_INTERVAL_SEMANTICS_INVALID')
        a, b = cell.get('start_s'), cell.get('end_s')
        if (not isinstance(a, (int, float)) or not isinstance(b, (int, float))
                or not math.isfinite(a) or not math.isfinite(b) or a != cursor or b <= a):
            return dict(result, status='GT_FUTURE_TIME_GAP_OR_INVALID')
        cursor = b
        if a < horizon:
            eligible.append(cell)
    if cursor < horizon:
        return dict(result, status='GT_FUTURE_INCOMPLETE')
    result['expected_intervals'] = len(eligible)
    for cell in eligible:
        a, b = cell['start_s'], min(cell['end_s'], horizon)
        # Closed source boundaries are retained. No source set is shrunk and no
        # velocity/behavior prediction is manufactured by this evaluator.
        overlap = [x for x in sources if x['start_s'] <= b and x['end_s'] >= a]
        shapes = [_shape(y.get('polygon_m')) for x in overlap for y in x['alternatives']]
        actual = _shape(cell.get('polygon_m'))
        if actual is None or any(x is None for x in shapes) or not shapes:
            return dict(result, status='FUTURE_GEOMETRY_INVALID')
        if region is None or not region.covers(actual):
            return dict(result, status='GT_BODY_OUTSIDE_EVALUATION_DOMAIN')
        predicted = unary_union(shapes).buffer(uncertainty)
        result['intervals'].append(dict(start_s=a, end_s=b,
            source_ids=[x['id'] for x in overlap],
            area_coverage=predicted.intersection(actual).area/actual.area,
            envelope_area_ratio=predicted.area/actual.area,
            residual_added_once_m=uncertainty))
    values = result['intervals']; result['evaluable_intervals'] = len(values)
    result.update(status='CONDITIONAL_BODY_ENVELOPE_COMPARISON',
                  minimum_interval_coverage=min(x['area_coverage'] for x in values),
                  mean_interval_coverage=statistics.mean(x['area_coverage'] for x in values),
                  mean_envelope_area_ratio=statistics.mean(x['envelope_area_ratio'] for x in values))
    return result


def _planning(record, output):
    # This entry point never invokes T1/T2 nor infers usability from overlap.
    report = record.get('planning_result')
    if not isinstance(report, dict):
        return dict(status='NOT_EVALUATED', reason='No independently bound tool results supplied')
    if report.get('output_digest') != digest(output):
        return dict(status='UNBOUND_RESULT_REJECTED', reason='Tool record belongs to a different hypothesis output')
    artifacts = report.get('artifacts', {})
    hashes = report.get('artifact_digests', {})
    if set(artifacts) != {'T1', 'T2'} or set(hashes) != set(artifacts) or any(digest(v) != hashes[k] for k,v in artifacts.items()):
        return dict(status='UNBOUND_RESULT_REJECTED', reason='Need original T1/T2 artifacts and their digests')
    scene, corridor = artifacts['T1'], artifacts['T2']
    try:
        validate('scene_v2.schema.json', scene)
        validate('corridor_v2.schema.json', corridor)
    except Failure:
        return dict(status='UNBOUND_RESULT_REJECTED', reason='Bound data do not satisfy the T1/T2 artifact schemas')
    if (corridor.get('scene_digest') != digest(scene) or scene.get('provenance', {}).get('output_digest') != digest(output)
            or scene.get('capture') != record.get('capture')):
        return dict(status='UNBOUND_RESULT_REJECTED', reason='Capture, source output or scene digest does not match')
    return dict(status='REPORTED_T1_T2_SUCCESS_NOT_INDEPENDENTLY_REEXECUTED',
                artifact_digests=hashes, reason='Neither optimization nor independent T4 or execution is established here')


def evaluate(records, truth, config, allow_synthetic=False):
    need(config.get('evaluation_horizon_s', 0)>0, 'G2_CONFIG', 'Specify a positive evaluation_horizon_s')
    need(isinstance(truth.get('samples'), dict), 'G2_TRUTH', 'Truth needs an explicit samples dictionary')
    need(bool(records), 'G2_RECORDS', 'Provide one or more structured model records')
    rows = []; scenes = []; truth_rows = []
    for index, record in enumerate(records):
        output = record['output']; prov = record['provenance']; sid = record['sample_id']; rid = record.get('record_id')
        need(bool(rid), 'G2_RECORD_ID', 'Every response needs its original record_id')
        need(prov.get('source') == 'online_model' or (allow_synthetic and prov.get('source') == 'synthetic_fixture'),
             'G2_REAL_ONLY', 'Synthetic arithmetic requires explicit test mode and never counts as online evidence')
        need(bool(prov.get('image_sha256')), 'G2_IMAGES', 'Image SHA identity is required')
        need(prov.get('output_digest') == digest(output), 'G2_OUTPUT_MISMATCH', 'Output changed after provenance was recorded')
        gt = truth['samples'].get(sid)
        reasons, region = _frame_checks(record, gt, config)
        if prov.get('agent_output_contract') == VERSION:
            try: validate('agent_output_v3_exp2.schema.json', output)
            except Failure: reasons.append('MODEL_OUTPUT_SCHEMA_INVALID')
        h = output.get('hypotheses', {}); objects = h.get('objects', [])
        gt_objects = (gt or {}).get('objects', [])
        need(isinstance(gt_objects,list) and all(isinstance(x,dict) for x in gt_objects),
             'G2_TRUTH', 'GT objects must be an explicit list of entity records')
        ground = {x['id']: x for x in gt_objects if x.get('id')}
        if len(ground) != len(gt_objects):
            reasons.append('GT_ENTITY_IDS_MISSING_DUPLICATED_OR_LEGACY_FORMAT')
        associations = truth.get('associations', {}).get(rid, {})
        assigned = Counter(x.get('gt_id') for x in associations.values() if isinstance(x,dict) and x.get('gt_id'))
        matched_ids = set()
        for o in objects:
            mapping = associations.get(o['id']); target = None; errors = list(reasons)
            if not isinstance(mapping, dict) or mapping.get('status') != 'unique_reviewed' or not mapping.get('evidence'):
                errors.append('NO_UNIQUE_REVIEWED_ASSOCIATION')
            elif assigned[mapping.get('gt_id')] != 1:
                errors.append('DUPLICATE_GT_IDENTITY_NO_SCORE')
            elif mapping.get('gt_id') not in ground:
                errors.append('GT_IDENTITY_MISSING')
            else:
                target = ground[mapping['gt_id']]; matched_ids.add(target['id'])
            row = dict(sample_id=sid, record_id=rid, response_index=index, object_id=o['id'],
                       scene_type=(gt or {}).get('scene_type','UNKNOWN'),
                       output_contract=prov.get('agent_output_contract','UNDECLARED'), prompt_version=prov.get('prompt_version','UNDECLARED'),
                       group=record.get('group','UNDECLARED'), source=prov['source'],
                       position_available=o.get('position_m') is not None, velocity_available=o.get('velocity_mps') is not None,
                       occupancy_available=o.get('occupancy') is not None, matched=target is not None,
                       gt_id=target['id'] if target else None, comparability_reasons=errors,
                       position_error_m=None, velocity_vector_error_mps=None,
                       position_status='NOT_EVALUABLE', velocity_status='NOT_EVALUABLE',
                       future=dict(status='NOT_EVALUABLE'), future_minimum_interval_coverage=None,
                       future_mean_envelope_area_ratio=None, planning_usability=_planning(record, output))
            if not errors and target:
                state_errors=[]
                if o.get('state_time_id') != h.get('reference_time_id'):
                    state_errors.append('MODEL_STATE_EPOCH_NOT_REFERENCE')
                if target.get('state_time_id') != gt.get('reference_time_id'):
                    state_errors.append('GT_STATE_EPOCH_NOT_REFERENCE')
                if state_errors:
                    row['position_status']=row['velocity_status']='STATE_EPOCH_COMPARABILITY_REJECTED'
                    row['state_epoch_reasons']=state_errors
                elif not row['position_available']:
                    row['position_status']='MODEL_POSITION_MISSING'
                elif not _vector(target.get('position_m')):
                    row['position_status']='GT_CURRENT_POSITION_MISSING_OR_INVALID'
                elif region is None or not region.covers(Point(target['position_m'])):
                    row['position_status']='GT_CURRENT_POSITION_OUTSIDE_EVALUATION_DOMAIN'
                else:
                    row.update(position_error_m=math.dist(o['position_m'], target['position_m']), position_status='SEMANTICALLY_MATCHED_NUMERIC_COMPARISON')
                if state_errors:
                    pass
                elif not row['velocity_available']:
                    row['velocity_status']='MODEL_VELOCITY_MISSING'
                elif not _vector(target.get('position_m')):
                    row['velocity_status']='GT_VELOCITY_DOMAIN_LOCATION_UNAVAILABLE'
                elif region is None or not region.covers(Point(target['position_m'])):
                    row['velocity_status']='GT_VELOCITY_OUTSIDE_EVALUATION_DOMAIN'
                elif not _vector(target.get('velocity_mps')):
                    row['velocity_status']='GT_VELOCITY_MISSING_OR_INVALID'
                elif gt.get('velocity_time_semantics') != 'instantaneous_at_state_time':
                    row['velocity_status']='GT_VELOCITY_IS_PROXY_OR_TIME_SEMANTICS_UNDECLARED'
                else:
                    row.update(velocity_vector_error_mps=math.dist(o['velocity_mps'],target['velocity_mps']), velocity_status='SEMANTICALLY_MATCHED_NUMERIC_COMPARISON')
                # Future body comparison is independent of current point or
                # velocity availability. Its own whole-interval/domain checks
                # remain mandatory even if current quantities are unavailable.
                if gt.get('future_body_semantics') not in ('whole_interval_actual_body', 'annotation_linear_body_surrogate', 'synthetic_authored_body'):
                    row['future']=dict(status='GT_FUTURE_BODY_SEMANTICS_UNDECLARED')
                else:
                    row['future']=_future(o,target,config['evaluation_horizon_s'],region)
                    row['future']['gt_reference_kind']=gt['future_body_semantics']
                    row['future']['conditional_not_calibrated']=True
                    row['future_minimum_interval_coverage']=row['future'].get('minimum_interval_coverage')
                    row['future_mean_envelope_area_ratio']=row['future'].get('mean_envelope_area_ratio')
            else:
                row['position_status']=row['velocity_status']='SEMANTIC_OR_IDENTITY_COMPARABILITY_REJECTED'
                row['future']=dict(status='SEMANTIC_OR_IDENTITY_COMPARABILITY_REJECTED')
            rows.append(row)
        for actor in gt_objects:
            truth_rows.append(dict(sample_id=sid,record_id=rid,gt_id=actor.get('id'),
                                   legacy_instance_token=actor.get('instance_token'),
                                   matched=bool(actor.get('id') and actor['id'] in matched_ids),
                                   position_available=actor.get('position_m') is not None,
                                   current_state_time_id=actor.get('state_time_id'),future_available=bool(actor.get('occupancy'))))
        scenes.append(dict(sample_id=sid,record_id=rid,output_contract=prov.get('agent_output_contract','UNDECLARED'),
                           scene_type=(gt or {}).get('scene_type','UNKNOWN'),comparability_reasons=reasons,
                           road_available=h.get('road') is not None,road_accuracy_score=None,
                           road_status='MODEL_ROAD_MISSING' if h.get('road') is None else 'MODEL_ROAD_SCOPE_AND_BOUNDARY_CORRESPONDENCE_UNDECLARED',
                           reason='A local drivable patch is not interchangeable with total map drivable extent; no road boundary score is manufactured'))
    def summarize(selected):
        n=len(selected)
        return dict(declared_object_rows=n,
                    position_available=sum(x['position_available'] for x in selected),
                    position_missing=sum(not x['position_available'] for x in selected),
                    position_missing_rate=sum(not x['position_available'] for x in selected)/n if n else None,
                    velocity_available=sum(x['velocity_available'] for x in selected),
                    velocity_missing=sum(not x['velocity_available'] for x in selected),
                    velocity_missing_rate=sum(not x['velocity_available'] for x in selected)/n if n else None,
                    occupancy_available=sum(x['occupancy_available'] for x in selected),
                    occupancy_missing=sum(not x['occupancy_available'] for x in selected),
                    unmatched=sum(not x['matched'] for x in selected),
                    unmatched_rate=sum(not x['matched'] for x in selected)/n if n else None,
                    semantic_or_identity_rejections=sum(bool(x['comparability_reasons']) for x in selected),
                    position_error_m=_stats(selected,'position_error_m'),
                    velocity_vector_error_mps=_stats(selected,'velocity_vector_error_mps'),
                    future_minimum_interval_coverage=_stats(selected,'future_minimum_interval_coverage'),
                    future_mean_envelope_area_ratio=_stats(selected,'future_mean_envelope_area_ratio'))
    by_protocol={version:summarize([x for x in rows if x['output_contract']==version]) for version in sorted({x['output_contract'] for x in rows})}
    return dict(metric_version='g2_metric_semantics_exp2',status='SYNTHETIC_NUMERIC_TEST_NOT_G2' if allow_synthetic else 'OFFLINE_METRICS_NO_PASS_CLAIM',
                real_model_calls_this_evaluation=0, unique_windows=len({x['sample_id'] for x in records}), valid_record_count=len(records),
                declared_object_rows=len(rows), truth_object_rows=len(truth_rows),
                availability=summarize(rows),by_output_contract=by_protocol,
                by_scene={scene:summarize([x for x in rows if x['scene_type']==scene]) for scene in sorted({x['scene_type'] for x in rows})},
                by_window={sid:summarize([x for x in rows if x['sample_id']==sid]) for sid in sorted({x['sample_id'] for x in records})},
                rows=rows,scene_rows=scenes,truth_rows=truth_rows,experiment_config=config,thresholds_applied=False,
                mixed_protocol_numeric_pooling_claim=False,online_success_claims=0,
                limitations=['Metrics require declared measurement semantics, evaluation domain and reviewed unique identity.',
                            'Repeated windows/revisions are repeated observations, not independent samples.',
                            'Wide-envelope overlap must be read with area inflation and planning usability; it is not calibrated reliability.',
                            'Annotation-derived future occupancy is a surrogate, not measured continuous-time truth.',
                            'No relevance pruning, behavior prediction, model invocation or runtime truth feedback occurs here.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records',nargs='+',required=True,type=Path)
    parser.add_argument('--truth',required=True,type=Path);parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path,help='New output directory; existing/sealed destinations are refused')
    args=parser.parse_args();inputs=[*args.records,args.truth,args.config]
    out=reserve_directory(args.out,readonly_inputs=inputs)
    load=lambda p:json.loads(p.read_text())
    result=evaluate([load(p) for p in args.records],load(args.truth),load(args.config))
    source=Path(__file__)
    write_json_new(out/'result.json',result)
    write_json_new(out/'provenance.json',dict(analysis_source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source.parents[2],text=True).strip(),
        analysis_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        input_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},real_model_calls=0,runtime_truth_writes=0))
    print(out)


if __name__=='__main__':
    try:main()
    except Failure as exc:
        print(json.dumps(exc.record,ensure_ascii=False));raise SystemExit(2)
