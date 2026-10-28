"""One-off acquisition-only preparation; no model client, annotations or maps."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / 'implementation'))
from agentic.datasets.nuscenes_capture import Acquisition
from agentic.contracts import validate

HERE = Path(__file__).resolve().parent
OUT = HERE / 'live_preparation'
assert not OUT.exists(), 'Preparation directory exists; do not overwrite.'
OUT.mkdir()
BASE = PROJECT / 'implementation/results/varied_prototypes_2s_20261028_v1'
def load(path):
    return json.loads(path.read_text())
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def write(name, value):
    with (OUT / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')

original_path = BASE / 'construction_yield/capture.json'
original = load(original_path)
capture = deepcopy(original)
acquisition = Acquisition('/data/nuscenes_dataset')
reference_id = next(row['id'] for row in original['observations']
                    if row['time_id'] == original['metadata']['reference_time_id'])
reference = acquisition.tables['sample_data'][reference_id]
existing_ids = {row['id'] for row in original['observations']}
left = next(row for row in original['observations'] if row['camera_id'] == 'CAM_FRONT_LEFT')
rows = [row for row in acquisition.history(acquisition.tables['sample_data'][left['id']], 2)
        if 0 <= reference['timestamp'] - row['timestamp'] <= 2e6]
extra = {min(rows, key=lambda row: abs(row['timestamp'] - (reference['timestamp'] - offset * 1e6)))['token']
         for offset in (1, 2)} - existing_ids
assert len(extra) <= 2
center, rotation, yaw = acquisition.pose_center(reference, capture['vehicle']['center_to_rear_axle_m'])
added = []
for frame_id in sorted(extra):
    frame = acquisition.tables['sample_data'][frame_id]
    stamp = frame['timestamp'] * 1e-6
    observation = dict(id=frame_id, time_id='nusc_us_' + str(frame['timestamp']), capture_time_s=stamp,
                       capture_utc=datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                       camera_id='CAM_FRONT_LEFT', path=str(acquisition.root / frame['filename']))
    capture['observations'].append(observation)
    position, _, angle = acquisition.pose_center(frame, capture['vehicle']['center_to_rear_axle_m'])
    capture['acquisition_metadata']['ego_history'].append(dict(
        frame_id=frame_id, time_id=observation['time_id'], center_xy_m=((position-center) @ rotation).tolist(),
        yaw_rad=math.atan2(math.sin(angle-yaw), math.cos(angle-yaw))))
    added.append(dict(**observation, image_sha256=sha(Path(observation['path'])),
                      age_s=(reference['timestamp']-frame['timestamp'])*1e-6))
capture['observations'].sort(key=lambda row: (row['capture_time_s'], row['id']))
capture['acquisition_metadata']['ego_history'].sort(
    key=lambda row: next(item['capture_time_s'] for item in capture['observations'] if item['id']==row['frame_id']))
validate('capture_v2.schema.json', capture)
for name in ('metadata', 'ego', 'vehicle', 'navigation_intent', 'safety'):
    assert capture[name] == original[name], name
assert capture['acquisition_metadata']['calibrations'] == original['acquisition_metadata']['calibrations']
assert all(row in capture['observations'] for row in original['observations'])
write('construction_capture.json', capture)
redlight_path = BASE / 'redlight_crossflow/capture.json'
with (OUT / 'redlight_capture.json').open('xb') as stream:
    stream.write(redlight_path.read_bytes())
assert sha(OUT / 'redlight_capture.json') == sha(redlight_path)

record_path = BASE / 'construction_yield/runtime/attempt_03/record.json'
record = load(record_path)
objects = {row['id']: row for row in record['output']['hypotheses']['objects']}
targeted = []
for oid in ('left_curb_pedestrian', 'right_worker'):
    actor = objects[oid]
    targeted.append(dict(object_id=oid, actual_cited_frame_ids=actor['frame_ids'],
                         actual_cited_time_ids=actor['time_ids'],
                         supplied_camera_history=('CAM_FRONT_LEFT: now three actual causal frames' if oid=='left_curb_pedestrian'
                                                  else 'CAM_FRONT: original five actual causal frames'),
                         source_intervals=[dict(source_id=row['id'], interval_s=[row['start_s'],row['end_s']],
                                                scenario_ids=[alt['scenario_id'] for alt in row['alternatives']])
                                           for row in actor['occupancy']],
                         conflict='The frozen native scene creates an empty conservative corridor at [0.2,0.4]s with the original yaw-bounded box planner. This alone is NOT proof that every trajectory is physically infeasible.',
                         request='Use the actually visible history to reassess this actor BODY center, dimensions, yaw, state-time alignment, short-time motion conditions and residual evidence. Provide genuinely supported whole-interval BODY bounds for all retained reasonable adverse scenarios; otherwise retain unknown and explain the missing evidence. Keep ID stable. Do not move the actor, shrink residuals, omit an adverse scenario or enlarge the road merely to make a corridor pass.'))
feedback = dict(purpose='Targeted new independent construction initial request after zero-call planner diagnosis; not an old-budget retry.',
                prior_native_record=str(record_path.relative_to(PROJECT)), prior_native_record_sha256=sha(record_path),
                previous_native_output=record['output'], targeted_objects=targeted,
                preserve_evidence='Only attached images and acquisition/ego metadata are runtime evidence. No GT, annotations, future frames, map or other perception model was supplied.',
                output_metadata='This is a new trial; use current capture metadata scene_id/revision/time, not the prior response revision.',
                boundary='Do not repair planner approximations by editing the scene. Revise a metric estimate only when additional actual image evidence warrants it. Explain ID additions/disappearances and retain all declared obstacles and reasonable adverse modes.')
write('construction_initial_feedback.json', feedback)

redlight = load(redlight_path)
write('plan.json', dict(launched=False, execution_owner='root_serial_runner',
    independent_budget=True, inherits_old_remaining_budget=False, max_real_requests=4,
    per_case_maximum={'construction_yield':2,'redlight_crossflow':2},
    execution_order=['construction_yield','redlight_crossflow'], maximum_parallel_requests=1,
    count_failures_and_generation_compatibility_probes=True, automatic_transport_retries=0,
    service_or_schema_auth_quota_error='Immediately stop external requests; continue frozen-output offline work.',
    generation_requests_sent_by_preparation=0, horizon_s=2, step_s=0.2, prefix_s=0.2,
    safety_margin_m=1, vehicle=capture['vehicle'], business_contract='3.0.0-exp2',
    prompt_version='driving_prototype_v1', experiment_profile='local_conditional_2s_prototype_v1',
    wire_bundle='implementation/results/exp2_wire_enum_day_pilot_20261028_v1/registration/wire_bundle',
    quota_preflight='../quota_preflight/result.json',
    cases=[dict(label='construction_yield', sample_id='88449a5cb1644a199c1c11f6ac034867',
                capture='construction_capture.json', capture_sha256=sha(OUT/'construction_capture.json'),
                image_count=len(capture['observations']), max_requests=2,
                initial_feedback='construction_initial_feedback.json',
                requires_root_confirmed_model_information_blocker_before_call=True),
           dict(label='redlight_crossflow', sample_id='c537fef0b4b241df92f44daa976fe5b3',
                capture='redlight_capture.json', capture_sha256=sha(OUT/'redlight_capture.json'),
                image_count=len(redlight['observations']), max_requests=2,
                label_is_not_a_signal_fact=True, stationary_hold_is_valid=True)],
    planner_profile='ROOT_TO_PIN_AFTER_OFFLINE_ITERATIONS_BEFORE_LAUNCH',
    source_freeze='ROOT_TO_COMMIT_ACTUAL_RUNNER_AND_CONFIGURATION_BEFORE_LAUNCH'))
write('input_summary.json', dict(prepared_at=datetime.now(timezone.utc).isoformat(),
    source_sha256=sha(Path(__file__)), acquisition_exporter_sha256=sha(PROJECT/'implementation/agentic/datasets/nuscenes_capture.py'),
    allowed_tables=sorted(acquisition.ALLOWED_TABLES), accessed_tables=acquisition.accessed,
    annotations_maps_future_read=False, model_requests=0,
    original_construction_capture_sha256=sha(original_path),
    construction_original_image_sha256={row['id']:sha(Path(row['path'])) for row in original['observations']},
    construction_extra_images=added,
    construction_all_image_sha256={row['id']:sha(Path(row['path'])) for row in capture['observations']},
    redlight_capture_original_bytes_preserved=True, redlight_capture_sha256=sha(redlight_path),
    redlight_image_sha256={row['id']:sha(Path(row['path'])) for row in redlight['observations']}))
print(json.dumps(dict(out=str(OUT), construction_images=len(capture['observations']),
                      added_images=added, redlight_images=len(redlight['observations']), model_requests=0),ensure_ascii=False))
