"""Prepare the three preselected hard clips without any model/GT calls.

Uses the existing causal acquisition exporter, configuration, source pins and
input preview helper. Scene labels and complete recorded poses stay in plans
for selection/display only; runtime capture contains no future route or GT.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'implementation'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.contracts import need, validate
from agentic.datasets.nuscenes_capture import Acquisition
from agentic.evidence_io import reserve_directory, write_json_new
from prepare_six_scene_scout import configuration
from prepare_long_clips import previews, source_pins, path_from_root, WIRE

SELECTION = Path('/tmp/hard_clip_selection_20261030.json')
SCENE_IDS = ('scene-0061', 'scene-1094', 'scene-0103')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(out, selection_path=SELECTION):
    selection_path = Path(selection_path).resolve()
    selection = read(selection_path)
    specifications = selection['scenes']
    need(tuple(s['scene_name'] for s in specifications) == SCENE_IDS
         and selection['clip_count'] == 3 and selection['reference_count'] == 21,
         'HARD_CLIP_SELECTION', 'Use the fixed three-scene / 21-reference selection')
    need(all(len(s['planning_references']) == 7 for s in specifications),
         'HARD_CLIP_SELECTION', 'Exactly seven registered references per clip')
    cfg = configuration()
    need(cfg['history_s'] == 2 and cfg['horizon_s'] == 2 and cfg['step_s'] == .2
         and cfg['safety']['prefix_s'] == .2 and cfg['safety']['margin_m'] == 1
         and cfg['vehicle']['max_speed_mps'] == 12,
         'HARD_CLIP_CONFIG', 'Retain original 2s/.2s/.2s, vehicle and safety configuration')
    acq = Acquisition(selection['dataset_root'], selection['dataset_version'])
    root = reserve_directory(out)
    write_json_new(root/'selection.json', selection)
    now = datetime.now(timezone.utc)
    pins = source_pins(extra_paths=(Path(__file__), ROOT/'implementation/experiments/run_hard_clip_scout.py'))
    common = dict(backend='openai_api', execution_authorized=True, launched=False,
        user_authorization='current hard-clip optimization request; independent operation cap42',
        user_call_limit=None, operational_call_ceiling=42, per_window_limit=2,
        old_budget_inherited=0, serial=True, automatic_retries=0,
        stop_on_service_error=True, requested_model='gpt-6.1-sol',
        prompt_version='driving_prototype_v3', experiment_profile='local_conditional_2s_prototype_v1',
        business_contract='3.0.0-exp2', coverage_profile='whole_interval_sources_v3_exp2',
        planner_profile='bounded_seed_candidates_v2', require_turn=False,
        api_preflight_models=True, wire_bundle=WIRE, timeout_s=450,
        network_readiness_get_attempts=3, keepalive_expiry_s=120,
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        source_sha256=pins, preparation_started_at_utc=now.isoformat(),
        deadline_utc=(now+timedelta(seconds=7200)).isoformat(), wall_limit_s=7200,
        configuration=cfg, selection_source_path=str(selection_path), selection_sha256=sha(selection_path),
        selection_snapshot_sha256=sha(root/'selection.json'), gt_used=False,
        recording_boundary='Independent plans from recorded ego states. Later images are not counterfactual observations after these simulated prefixes. No stitched execution.',
        context_policy='Only this reference causal two-second capture and same-reference feedback enter the model. Selection labels, scene descriptions and full display timeline do not enter capture.',
        result_policy='Retain all21 registered references and every failure; input-only frames are not planning passes. Baseline algorithms stay fixed for the complete batch.')
    clips = []
    for specification in specifications:
        folder = reserve_directory(root/specification['clip_id'])
        inputs = reserve_directory(folder/'inputs')
        samples = [s for s in acq.tables['sample'].values()
                   if s['scene_token'] == specification['scene_token']
                   and (s['token'], 'CAM_FRONT') in acq.keyframes]
        samples.sort(key=lambda s: acq.keyframes[(s['token'], 'CAM_FRONT')]['timestamp'])
        need(len(samples) == specification['raw_keyframe_count'], 'HARD_CLIP_IDENTITY',
             'Full recorded clip identity changed', scene_name=specification['scene_name'])
        chosen = {r['raw_index']: r for r in specification['planning_references']}
        need(len(chosen) == 7 and all(0 <= i < len(samples) for i in chosen),
             'HARD_CLIP_SELECTION', 'Registered raw indices are invalid or duplicated')
        first = acq.keyframes[(samples[0]['token'], 'CAM_FRONT')]['timestamp']
        raw = []; cases = []
        for index, sample in enumerate(samples):
            frame = acq.keyframes[(sample['token'], 'CAM_FRONT')]
            history = acq.history(frame, cfg['history_s'])
            ready = len(history)>1 and any(frame['timestamp']-h['timestamp']>=.1e6 for h in history)
            cap = audit = None
            if ready:
                cap, audit = acq.export(sample['token'], cfg)
            counts = Counter(o['camera_id'] for o in cap['observations']) if cap else Counter()
            complete = bool(cap and len(cap['observations']) == 10 and counts['CAM_FRONT'] == 5
                            and len(counts) == 6 and audit['history_span_s'] >= 1.9)
            speed = cap['ego']['speed_mps'] if cap else None
            overspeed = speed is not None and speed > cfg['vehicle']['max_speed_mps']
            eligible = complete and not overspeed
            center, _, yaw = acq.pose_center(frame, cfg['vehicle']['center_to_rear_axle_m'])
            image = acq.root/frame['filename']
            need(image.is_file(), 'NUSCENES_IMAGE', 'Missing real front image', path=str(image))
            role = ('INPUT_OUTSIDE_VEHICLE_LIMIT' if overspeed else
                    'PLANNING_REFERENCE' if index in chosen else
                    'OBSERVATION_ONLY' if complete else 'WARMUP')
            row = dict(raw_index=index, sample_token=sample['token'], frame_id=frame['token'],
                reference_time_id='nusc_us_'+str(frame['timestamp']), reference_time_s=frame['timestamp']*1e-6,
                relative_sequence_time_s=(frame['timestamp']-first)*1e-6,
                image_path=str(image), image_sha256=sha(image), ego_speed_mps=speed,
                speed_source='causal_backward_difference_localization_0.5s; not CAN',
                complete_input_available=eligible, causal_history_ready=complete,
                history_span_s=audit['history_span_s'] if audit else None,
                role=role, planning_case_id=None, model_call_allowed=index in chosen and eligible,
                display_only_ego_pose=dict(center_xy_world_m=center.tolist(), yaw_world_rad=yaw,
                    center_offset_m=cfg['vehicle']['center_to_rear_axle_m']))
            raw.append(row)
            if index not in chosen:
                continue
            fixed = chosen[index]
            need(eligible, 'HARD_CLIP_INPUT', 'A fixed planning reference lacks legal causal input',
                 scene_name=specification['scene_name'], raw_index=index, speed_mps=speed)
            need(sample['token'] == fixed['sample_token'] and frame['token'] == fixed['reference_frame_id']
                 and row['reference_time_id'] == fixed['reference_time_id']
                 and row['image_sha256'] == fixed['front_image_sha256'],
                 'HARD_CLIP_IDENTITY', 'Registered sample/front image changed', raw_index=index)
            need(abs(row['relative_sequence_time_s']-fixed['relative_sequence_time_s']) < 1e-6
                 and abs(speed-fixed['ego_speed_mps']) < 1e-8,
                 'HARD_CLIP_IDENTITY', 'Registered acquisition time or initial speed changed', raw_index=index)
            case_id = fixed['case_id']; row['planning_case_id'] = case_id
            target = reserve_directory(inputs/case_id)
            validate('capture_v2.schema.json', cap)
            write_json_new(target/'capture.json', cap)
            case = dict(case_id=case_id, frame_index=index, raw_index=index,
                planning_reference_index=len(cases), scene_name=specification['scene_name'],
                scene_token=specification['scene_token'], sample_token=sample['token'],
                relative_sequence_time_s=row['relative_sequence_time_s'], reference_time_s=row['reference_time_s'],
                ego_speed_mps=speed, capture=path_from_root(target/'capture.json'),
                capture_sha256=sha(target/'capture.json'), image_sha256=audit['image_sha256'],
                require_turn=False, request_limit=2, selection_role=fixed['reference_role'])
            write_json_new(target/'input_manifest.json', dict(**case, model_requests=0,
                image_count=10, history_span_s=audit['history_span_s'], runtime_tables=audit['accessed_tables'],
                gt_in_runtime=False, images=[dict(**o,sha256=audit['image_sha256'][o['id']]) for o in cap['observations']]))
            cases.append(case)
        need(len(cases) == 7, 'HARD_CLIP_SELECTION', 'Do not drop a registered reference')
        observed_overspeed=[r['raw_index'] for r in raw if r['role']=='INPUT_OUTSIDE_VEHICLE_LIMIT']
        registered_overspeed=[r['raw_index'] for r in specification.get('raw_overspeed_input_only',[])]
        need(observed_overspeed==registered_overspeed,'HARD_CLIP_INPUT_DOMAIN',
             'The frozen overspeed input-only indices changed; never clip speed or replace a reference')
        need(abs(raw[-1]['relative_sequence_time_s']-specification['full_clip_duration_s']) < 1e-6,
             'HARD_CLIP_IDENTITY', 'Full clip duration changed')
        plan = dict(common, experiment_id=root.name+'_'+specification['clip_id'],
            operational_call_ceiling=14, clip_id=specification['clip_id'],
            scene_name=specification['scene_name'], scene_token=specification['scene_token'],
            selection_label=specification['selection_reason'], selection_basis=specification['selection_rule'],
            raw_frames=raw, cases=cases, frame_count=7, raw_frame_count=len(raw),
            full_clip_duration_s=raw[-1]['relative_sequence_time_s'],
            first_planning_time_s=cases[0]['relative_sequence_time_s'], raw_input_gif_final_hold_ms=500,
            input_preview='input_contactsheet.jpg', raw_input_gif='raw_input.gif')
        write_json_new(folder/'raw_frame_index.json', raw)
        write_json_new(folder/'plan.json', plan)
        previews(raw, folder, specification['scene_name']+' | INPUT ONLY')
        clips.append(dict(clip_id=specification['clip_id'], scene_name=specification['scene_name'],
            scene_token=specification['scene_token'], plan=path_from_root(folder/'plan.json'),
            plan_sha256=sha(folder/'plan.json'), reference_count=7, operational_call_ceiling=14,
            raw_frame_count=len(raw), full_clip_duration_s=raw[-1]['relative_sequence_time_s'],
            first_planning_time_s=cases[0]['relative_sequence_time_s']))
    top = dict(common, experiment_id=root.name, clips=clips, unique_scenes=3,
        max_parallel_clips=1, serial_within_clip=True, planning_reference_count=21,
        raw_frame_count=sum(c['raw_frame_count'] for c in clips), accessed_runtime_tables=sorted(acq.accessed),
        model_generation_requests=0, status='PREPARED_NOT_LAUNCHED')
    write_json_new(root/'plan.json', top)
    return dict(out=str(root), clips=3, planning_references=21,
                raw_frames=top['raw_frame_count'], operation_cap=42, model_generation_requests=0, gt_reads=0)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection',type=Path,default=SELECTION)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.out,args.selection),ensure_ascii=False))
