"""Offline-only preregistration and blinded identity view for the exp2 pilot.

No model adapter or runtime tools are imported. Preparing GT adds explicit
metadata to an independently prepared annotation reference; it never modifies a
model response. Identity review views contain no model numerical quantities or
unrestricted prose. They are not a claim that historical knowledge is erased.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic.contracts import digest, need
from agentic.evidence_io import reserve_directory, write_json_new
from agentic.g2_metric import SEMANTICS

SAMPLE = 'ac452a60e8b34a7080c938c904b23057'
FOCAL_INSTANCE = 'afd097377c71479ea8349eeae83b8143'
DOMAIN = dict(id='pilot_original_day_fixed_common_domain_v1',
              polygon_m=[[-5., -12.], [45., -12.], [45., 12.], [-5., 12.]],
              purpose='current_actor_and_entire_future_body_no_clipping')
# Whitelisting, rather than editing free text, prevents numerical estimates,
# aliases embedding distances and metric prose from reaching identity review.
VISUAL_WORDS = frozenset(('pedestrian person people man woman child adult group crowd '
    'car vehicle sedan suv van truck bus trailer bicycle cyclist motorcycle motorbike '
    'barrier cone debris crossing parked standing walking moving stationary '
    'red blue green yellow orange white black grey gray silver brown '
    'left right front rear back center centre foreground background '
    'shirt jacket pants trousers hat backpack sidewalk curb crosswalk').split())


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_runtime_record(record_path, seal_path):
    """Unlock review only for an exact member of completed runtime evidence."""
    record_path, seal_path = Path(record_path).resolve(), Path(seal_path).resolve()
    need(seal_path.is_file(), 'PILOT_RUNTIME_UNSEALED', 'Seal runtime results before offline review')
    seal = read(seal_path)
    need(isinstance(seal, dict) and seal.get('runtime_sealed') is True
         and isinstance(seal.get('files'), dict),
         'PILOT_RUNTIME_UNSEALED', 'A completed runtime seal must declare runtime_sealed=true and list files')
    need(record_path.is_file() and record_path.is_relative_to(seal_path.parent),
         'PILOT_RECORD_OUTSIDE_SEAL', 'The actual record must be inside the sealed runtime directory')
    relative = record_path.relative_to(seal_path.parent).as_posix()
    entry = seal['files'].get(relative)
    need(isinstance(entry, dict), 'PILOT_RECORD_NOT_SEALED', 'Record path is absent from the runtime seal', path=relative)
    need(entry.get('sha256') == sha(record_path)
         and isinstance(entry.get('bytes'), int) and not isinstance(entry['bytes'], bool)
         and entry['bytes'] == record_path.stat().st_size,
         'PILOT_RECORD_SEAL_MISMATCH', 'Record bytes differ from the sealed runtime evidence', path=relative)
    return dict(seal_path=str(seal_path), seal_sha256=sha(seal_path),
                record_relative_path=relative, record_sha256=entry['sha256'],
                record_bytes=entry['bytes'], runtime_sealed=True)


def alias(index):
    # Alphabetic labels reveal neither original IDs nor positions.
    value = index + 1
    text = ''
    while value:
        value, remainder = divmod(value - 1, 26)
        text = chr(65 + remainder) + text
    return 'actor_' + text


def semantic_view(record):
    """Return review-only information and a separately sealed alias mapping.

    Only fixed vocabulary tokens from actor IDs and bound premise statements
    survive. Lists of actual acquisition IDs are safe because they identify
    evidence, not hypothesized state/geometry/velocity. No confidence_note,
    scenario/unknown descriptions, positions, body dimensions or time offsets
    are exposed. Absent visual identity detail remains absent.
    """
    output = record['output']
    capture = record['capture']
    frames = {x['id']: x for x in capture['observations']}
    hypotheses = output.get('hypotheses', {})
    view = dict(record_id=record['record_id'], sample_id=record['sample_id'],
                source_output_digest=digest(output),
                format='fixed_visual_vocabulary_only_no_model_numeric_values',
                historical_knowledge='NOT_FULLY_BLIND_PRIOR_WINDOW_AND_GT_EXPOSURE_DISCLOSED',
                objects=[])
    mapping = {}
    for index, actor in enumerate(hypotheses.get('objects', [])):
        aid = alias(index)
        mapping[aid] = actor['id']
        prose = [actor['id']]
        prose += [x['statement'] for x in hypotheses.get('premises', [])
                  if actor['id'] in x.get('object_ids', [])]
        words = set(re.findall(r'[a-z]+', ' '.join(prose).lower()))
        evidence = []
        # Preserve even invalid pairs as visible invalid evidence; no IDs added.
        cited_frames = actor.get('frame_ids', [])
        cited_times = actor.get('time_ids', [])
        for frame_index, frame_id in enumerate(cited_frames):
            time_id = cited_times[frame_index] if frame_index < len(cited_times) else None
            captured = frames.get(frame_id)
            evidence.append(dict(frame_id=frame_id if captured else '<INVALID_FRAME_ID>',
                time_id=time_id if captured and captured['time_id'] == time_id else '<INVALID_TIME_ID>',
                camera_id=captured.get('camera_id') if captured else None,
                valid_original_pair=bool(captured and captured['time_id'] == time_id)))
        view['objects'].append(dict(review_alias=aid,
            visual_keyword_hints=sorted(words & VISUAL_WORDS), evidence=evidence))
    view['alias_binding_digest'] = digest(mapping)
    return view, dict(record_id=record['record_id'], source_output_digest=digest(output),
                      review_view_digest=digest(view), aliases=mapping,
                      warning='DO_NOT_SHOW_ALIAS_MAP_OR_MODEL_RECORD_TO_IDENTITY_REVIEWER_BEFORE_ASSOCIATIONS_FREEZE')


def prepare(capture_path, old_dir):
    """Freeze definitions using only old capture, annotation reference and review.

    The old GT is independently prepared from nuScenes annotations. Its numeric
    arrays are copied byte-value-equivalently; the bridge only names already
    documented GT semantics and preserves every actor/missing value. Future
    geometry is explicitly an annotation-linear circumscribed BODY surrogate.
    """
    capture_path, old_dir = Path(capture_path), Path(old_dir)
    capture = read(capture_path)
    need(capture['metadata']['scene_id'] == 'nusc_' + SAMPLE,
         'PILOT_WINDOW', 'The preregistered original day window cannot be replaced')
    meta = capture['metadata']
    need(meta['horizon_s'] == 5. and meta['step_s'] == .2
         and capture['vehicle']['center_to_rear_axle_m'] == 1.2,
         'PILOT_CONFIG', 'Preserve original horizon, grid and vehicle reference offset')
    old_truth = read(old_dir / 'truth.json')['samples'][SAMPLE]
    need(old_truth['reference_time_id'] == meta['reference_time_id']
         and old_truth['reference_time_s'] == meta['reference_time_s']
         and old_truth['configured_center_offset_m'] == 1.2,
         'PILOT_GT_REFERENCE', 'Independent annotation reference must match original capture')
    need(old_truth['evaluation_roi_m'] == [-5., -12., 45., 12.],
         'PILOT_GT_DOMAIN', 'Do not switch the original common evaluation domain')
    images = {x['id']: sha(x['path']) for x in capture['observations']}
    objects = []
    changes = []
    for index, old in enumerate(old_truth['objects']):
        cells = old['occupancy_surrogate']
        occupancy = None if cells is None else [dict(deepcopy(x), kind='whole_interval_body_envelope') for x in cells]
        objects.append(dict(id=old['instance_token'], state_time_id=meta['reference_time_id'],
            position_m=deepcopy(old['position_m']), velocity_mps=deepcopy(old['velocity_mps']),
            occupancy=occupancy, annotation_token=old['annotation_token'],
            category=old['category'], visibility=old['visibility'],
            current_reference_status=old['status']))
        changes.append(dict(source_pointer=f'/samples/{SAMPLE}/objects/{index}',
            source_id_field='instance_token', target_id_field='id',
            values_changed=False,
            metadata_added=['state_time_id', 'occupancy/*/kind'],
            premise='Old GT preparation explicitly interpolated annotation box centers at this acquisition reference; surrogate cells covered whole source intervals. This bridge changes GT formatting only, never model output.'))
    gt = dict(measurement_semantics=deepcopy(SEMANTICS), units='m_s_rad',
        coordinate_frame='ego_at_reference', reference_time_id=meta['reference_time_id'],
        reference_time_s=meta['reference_time_s'], ego_center_to_rear_axle_m=1.2,
        image_sha256=images, evaluation_domain=deepcopy(DOMAIN),
        velocity_time_semantics='bracketing_annotation_displacement_slope',
        current_position_reference_semantics='bracketing_annotation_box_center_linear_interpolation_at_reference_max_gap_0.75s',
        future_body_semantics='annotation_linear_body_surrogate',
        scene_type=old_truth['scene_type'], scene=old_truth['scene'], objects=objects)
    source_paths = [capture_path, old_dir/'truth.json', old_dir/'associations.json',
                    old_dir/'projection_index.json', old_dir/'prepare_provenance.json',
                    ROOT/'implementation/experiments/nuscenes/t1_diagnosis_g2.py',
                    ROOT/'implementation/experiments/nuscenes/prepare.py',
                    ROOT/'implementation/agentic/g2_metric.py', Path(__file__)]
    projection = [x for x in read(old_dir/'projection_index.json') if x['sample_id'] == SAMPLE]
    for row in projection:
        source_paths.append(old_dir/row['review_image'])
        need(images.get(row['frame_id']) == row['source_image_sha256'],
             'PILOT_REVIEW_IMAGE', 'Official projection must refer to an original runtime image')
    plan = dict(version='controlled_single_window_exp2_g2_preregistration_v1',
        sample_id=SAMPLE, original_cohort='original_four', unique_windows=1,
        previous_exposure='This window, earlier model quantities, nuScenes labels and prior nonblind associations were already inspected historically. This is not fully blind or an independent held-out sample.',
        measurement_semantics=deepcopy(SEMANTICS),
        reference_time_id=meta['reference_time_id'], reference_time_s=meta['reference_time_s'],
        ego_center_to_rear_axle_m=1.2, evaluation_horizon_s=5., evaluation_domain=deepcopy(DOMAIN),
        image_sha256=images, fixed_focal_instance=FOCAL_INSTANCE,
        identity_review=dict(
            inputs=['original cited images', 'official SDK annotation projections linked by image SHA',
                    'fixed-vocabulary numerical-value-free semantic view'],
            blocked_inputs=['model position/velocity/BODY/uncertainty/occupancy values',
                            'numerical error tables', 'unrestricted model prose',
                            'nearest-GT or lowest-error selection'],
            method='Freeze aliases-to-instance associations from visible category/appearance/camera evidence before unsealing model numbers. Require unique single actor and at least one valid actually cited image. Preserve ambiguous, group, invalid citation and duplicate-GT rows unmatched. Review every declared actor; never replace the original focal target.',
            prior_association_reuse='Prior labels may guide visual identity, but original model actor IDs are not copied to new responses. New IDs require fresh frozen semantic-view review.',
            historical_front_frame_rule='If no frozen latest-frame projection matches an actually cited frame, either use official SDK projection for that original frame under this same rule before reviewing quantities, or retain unmatched; never add a frame to model evidence.',
            grouping='Initial and revision are repeated observations of one unique window. Missing output/service failure gets its own attempt row; absence of actors is not a zero-error score.'),
        scoring=dict(position='Report Euclidean XY difference only for semantically matching, uniquely associated native values within frozen domain. Reference is annotation-linear interpolation, not exact measured instantaneous truth.',
            velocity='No instantaneous-velocity accuracy score from annotation displacement slopes; preserve provided/missing and proxy status.',
            road='No road-boundary accuracy score: model local patch has no predeclared correspondence to full-map drivable extent.',
            future='Evaluate every declared scenario and all source intervals against complete annotation-linear circumscribed-body surrogate only. Require entire GT body within domain; do not clip. Report coverage, area inflation, missing, gaps, unmatched and no-calibrated-guarantee caveat together.',
            planning='Report sealed real tool/acceptor/execution evidence separately. Overlap never implies planning usability.',
            thresholds_applied=False, g2_pass_claim=False),
        runtime_separation='GT, projection labels, associations and this preregistration are offline only. Model and T1-T4 receive none of them.',
        lifecycle='Freeze this plan before viewing new model metric values. Seal runtime outputs first; freeze identity review next; then evaluate. Do not mutate sealed sources or rerun into existing directories.',
        input_sha256={str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): sha(p) for p in source_paths},
        real_model_calls=0, launched=False)
    return plan, dict(samples={SAMPLE:gt}, associations={}), projection, changes


def bind_associations(view, alias_map, review):
    """Translate a frozen review without inventing matches or seeing numbers."""
    need(view['record_id'] == alias_map['record_id'] == review['record_id'],
         'PILOT_REVIEW_RECORD', 'Review and aliases must refer to the same original record')
    need(alias_map['review_view_digest'] == digest(view)
         and review.get('semantic_view_digest') == digest(view)
         and alias_map['source_output_digest'] == view['source_output_digest']
         and digest(alias_map['aliases']) == view['alias_binding_digest'],
         'PILOT_REVIEW_DIGEST', 'Identity review must bind the exact numerical-value-free view')
    need(review.get('frozen_before_metric_unsealing') is True,
         'PILOT_REVIEW_NOT_FROZEN', 'Do not retrospectively choose matches from metric errors')
    result = {}
    rows = {x['review_alias']:x for x in view['objects']}
    need(set(review['associations']) == set(rows), 'PILOT_REVIEW_INCOMPLETE',
         'Every declared actor needs an explicit matched or unmatched review row')
    for name, row in review['associations'].items():
        need(name in alias_map['aliases'], 'PILOT_REVIEW_ALIAS', 'Unknown reviewer alias')
        need(row.get('reason'), 'PILOT_REVIEW_REASON', 'Keep the reason for every association or refusal')
        if row.get('status') == 'unique_reviewed':
            cited = {x['frame_id'] for x in rows[name]['evidence'] if x['valid_original_pair']}
            need(row.get('gt_id') and row.get('evidence') and set(row.get('review_frame_ids', [])) & cited,
                 'PILOT_REVIEW_EVIDENCE', 'Unique identity requires evidence from an actually cited original image')
            need(row.get('single_actor_not_group') is True, 'PILOT_REVIEW_GROUP', 'Groups cannot be scored as a single body center')
        else:
            need(row.get('gt_id') is None, 'PILOT_REVIEW_UNMATCHED', 'Unmatched rows must not hide a selected GT identity')
        result[alias_map['aliases'][name]] = deepcopy(row)
    return {review['record_id']:result}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command', required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--capture', required=True, type=Path)
    prep.add_argument('--old-g2', required=True, type=Path);prep.add_argument('--out', required=True, type=Path)
    view=sub.add_parser('semantic-view');view.add_argument('--record', required=True, type=Path)
    view.add_argument('--runtime-seal', required=True, type=Path);view.add_argument('--out', required=True, type=Path)
    args=parser.parse_args()
    if args.command=='prepare':
        out=reserve_directory(args.out, readonly_inputs=[args.capture,args.old_g2])
        plan,truth,projection,changes=prepare(args.capture,args.old_g2)
        for name,value in [('preregistration.json',plan),('truth.json',truth),('projection_index.json',projection),('gt_format_bridge.json',changes)]:
            write_json_new(out/name,value)
    else:
        seal_binding=verify_runtime_record(args.record,args.runtime_seal)
        out=reserve_directory(args.out, readonly_inputs=[args.record,args.runtime_seal])
        result,mapping=semantic_view(read(args.record))
        mapping['runtime_seal_binding']=seal_binding
        write_json_new(out/'semantic_view.json',result)
        write_json_new(out/'private_alias_map.json',mapping)
    write_json_new(out/'preparation_provenance.json',dict(
        analysis_source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        analysis_source_sha256=sha(__file__),
        code_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        real_model_calls=0, runtime_truth_writes=0))
    print(out)


if __name__=='__main__':
    main()
