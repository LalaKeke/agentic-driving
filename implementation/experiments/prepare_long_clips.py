"""Prepare three full recorded scene timelines using acquisition tables only.

No model client, annotation, map or future actor truth is imported. The full
timeline is for selection/display; a model receives only its selected capture.
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
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic.contracts import Failure, need, validate
from agentic.datasets.nuscenes_capture import Acquisition
from agentic.evidence_io import reserve_directory, write_json_new
from prepare_six_scene_scout import configuration

WIRE = 'implementation/results/exp2_wire_enum_day_pilot_20261028_v1/registration/wire_bundle'
TEMPLATE = 'implementation/results/moving_prototype_2s_20261028_v1/capture.json'
SCENES = (
    dict(clip_id='following_deceleration', scene_name='scene-0103',
         scene_token='fcbccedd61424f1b85dcbf8f897f9754',
         label='Lead vehicle / deceleration and continued travel', expected_frames=40, expected_first_index=4),
    dict(clip_id='bus_road', scene_name='scene-0655',
         scene_token='bebf5f5b2a674631ab5c88fd1aa9e87a',
         label='Daylight road / bus and roadside context', expected_frames=41, expected_first_index=4),
    dict(clip_id='parking_bend', scene_name='scene-0916',
         scene_token='325cef682f064c55a255f2625c533b75',
         label='Parking-area road / compound bends', expected_frames=41, expected_first_index=5),
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_pins(extra_paths=()):
    """Required runtime pins plus any already-present new runner/report files."""
    names = [str(Path(__file__).relative_to(ROOT)),
             'implementation/experiments/prepare_six_scene_scout.py', TEMPLATE,
             'implementation/experiments/run_local_prototype.py',
             'implementation/experiments/run_openai_bend_sequence.py',
             'implementation/prompts/driving_prototype_v3.txt',
             'implementation/agentic/datasets/nuscenes_capture.py']
    names += ['implementation/agentic/' + name + '.py' for name in
              ('openai_adapter', 't1', 't2', 't3', 't4', 'metric_contract', 'geometry',
               'candidate_search', 'acceptance', 'runtime', 'contracts', 'wire_schema', 'wire_schema_v2',
               'evidence_io', 'turn_task', 'contract_feedback')]
    names += ['implementation/schemas/' + name + '.schema.json' for name in
              ('capture_v2', 'scene_v2', 'agent_output_v3_exp2', 'corridor_seed_candidates_v2',
               'trajectory_seed_candidates_v2')]
    for version in (3,4):
        names += [f'implementation/schemas/{kind}_seed_candidates_v{version}.schema.json'
                  for kind in ('corridor','trajectory')
                  if (ROOT/f'implementation/schemas/{kind}_seed_candidates_v{version}.schema.json').is_file()]
    names += [WIRE + '/wire_schema.json', WIRE + '/transformation_manifest.json']
    for optional in ('implementation/experiments/run_long_clips.py',
                     'implementation/experiments/run_openai_clip.py',
                     'implementation/experiments/summarize_long_clip.py'):
        if (ROOT / optional).is_file():
            names.append(optional)
    for value in extra_paths:
        path = Path(value)
        path = path if path.is_absolute() else ROOT / path
        need(path.is_file(), 'SOURCE_PIN', 'Additional source pin does not exist', path=str(path))
        names.append(str(path.relative_to(ROOT)))
    return {name: sha(ROOT / name) for name in dict.fromkeys(names)}


def path_from_root(path):
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def font(size):
    from PIL import ImageFont
    source = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    return ImageFont.truetype(str(source), size) if source.is_file() else ImageFont.load_default()


def previews(rows, folder, title):
    """All original front frames, labelled input-only, including the warm-up."""
    from PIL import Image, ImageDraw, ImageOps
    columns, tile_w, tile_h = 6, 270, 190
    sheet = Image.new('RGB', (columns * tile_w, 65 + ((len(rows) + columns - 1) // columns) * tile_h), 'white')
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 8), title, font=font(21), fill='black')
    draw.text((10, 36), 'Complete recorded CAM_FRONT timeline; INPUT ONLY, no model or GT result', font=font(15), fill='black')
    animation = []
    for row in rows:
        index = row['raw_index']; x = (index % columns) * tile_w; y = 65 + (index // columns) * tile_h
        with Image.open(row['image_path']) as source:
            image = source.convert('RGB')
            thumb = ImageOps.contain(image, (tile_w - 6, 149))
            sheet.paste(thumb, (x + 3, y))
            page = Image.new('RGB', (640, 410), 'white')
            page.paste(ImageOps.contain(image, (640, 360)), (0, 0))
        speed = 'UNKNOWN' if row['ego_speed_mps'] is None else f"{row['ego_speed_mps']:.3f}m/s"
        caption = f"#{index:02d} +{row['relative_sequence_time_s']:.3f}s  v={speed}"
        draw.text((x + 3, y + 151), caption, font=font(12), fill='black')
        draw.text((x + 3, y + 169), row['role'], font=font(12), fill='black')
        page_draw = ImageDraw.Draw(page)
        page_draw.text((8, 364), title, font=font(14), fill='black')
        page_draw.text((8, 386), caption + ' | ' + row['role'] + ' | INPUT ONLY', font=font(12), fill='black')
        animation.append(page)
    sheet.save(folder / 'input_contactsheet.jpg', quality=90)
    durations = [max(50, round((b['reference_time_s'] - a['reference_time_s']) * 1000))
                 for a, b in zip(rows, rows[1:])]
    durations.append(500)
    animation[0].save(folder / 'raw_input.gif', save_all=True, append_images=animation[1:],
                      duration=durations, loop=0)


def prepare(out, dataset_root):
    cfg = configuration()
    # configuration() already supplies a generic task intention, no scene label.
    need(cfg['history_s'] == 2 and cfg['horizon_s'] == 2 and cfg['step_s'] == .2
         and cfg['safety']['prefix_s'] == .2 and cfg['safety']['margin_m'] == 1,
         'LONG_CLIP_CONFIG', 'Keep the current two-second prototype parameters')
    acq = Acquisition(dataset_root)
    root = reserve_directory(out)
    now = datetime.now(timezone.utc)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    pins = source_pins()
    common = dict(backend='openai_api', execution_authorized=True, launched=False,
                  user_authorization='unlimited requests', user_call_limit=None,
                  operational_call_ceiling=114, per_window_limit=2, old_budget_inherited=0,
                  serial=True, automatic_retries=0, stop_on_service_error=True,
                  requested_model='gpt-6.1-sol', prompt_version='driving_prototype_v3',
                  experiment_profile='local_conditional_2s_prototype_v1',
                  business_contract='3.0.0-exp2', coverage_profile='whole_interval_sources_v3_exp2',
                  planner_profile='bounded_seed_candidates_v2', require_turn=False,
                  api_preflight_models=True, wire_bundle=WIRE, timeout_s=450,
                  source_commit=commit, source_sha256=pins,
                  preparation_started_at_utc=now.isoformat(),
                  deadline_utc=(now + timedelta(hours=4)).isoformat(), wall_limit_s=14400,
                  configuration=cfg, gt_used=False,
                  recording_boundary='Independent plans from recorded ego states. Later original images are not counterfactual observations following the simulated prefixes. No stitched plan is executed.',
                  context_policy='Each model gets only its own causal two-second capture and optional within-window tool feedback. Full timeline and selection/display poses do not enter capture.',
                  result_policy='Keep every chosen reference and every failure. First two seconds are warm-up, remaining unsampled front frames are observation-only; never call these online planning successes.')
    clips = []
    for specification in SCENES:
        folder = reserve_directory(root / specification['clip_id'])
        inputs = reserve_directory(folder / 'inputs')
        samples = [row for row in acq.tables['sample'].values()
                   if row['scene_token'] == specification['scene_token']
                   and (row['token'], 'CAM_FRONT') in acq.keyframes]
        samples.sort(key=lambda sample: acq.keyframes[(sample['token'], 'CAM_FRONT')]['timestamp'])
        need(len(samples) == specification['expected_frames'], 'CLIP_IDENTITY', 'Fixed scene timeline changed')
        first = acq.keyframes[(samples[0]['token'], 'CAM_FRONT')]['timestamp']
        raw, captures, audits = [], {}, {}
        for index, sample in enumerate(samples):
            frame = acq.keyframes[(sample['token'], 'CAM_FRONT')]
            history = acq.history(frame, cfg['history_s'])
            cap = audit = None
            history_ready = len(history) > 1 and any(frame['timestamp'] - h['timestamp'] >= .1e6 for h in history)
            if history_ready:
                cap, audit = acq.export(sample['token'], cfg)
            counts = Counter(o['camera_id'] for o in cap['observations']) if cap else Counter()
            eligible = bool(cap and len(cap['observations']) == 10 and counts['CAM_FRONT'] == 5
                            and len(counts) == 6 and audit['history_span_s'] >= 1.9)
            if cap:
                need(0 <= cap['ego']['speed_mps'] <= cfg['vehicle']['max_speed_mps'],
                     'CLIP_INITIAL_SPEED', 'Recorded speed exceeds fixed vehicle limits; do not clip it',
                     sample_token=sample['token'], speed_mps=cap['ego']['speed_mps'])
                captures[index], audits[index] = cap, audit
            center, _, yaw = acq.pose_center(frame, cfg['vehicle']['center_to_rear_axle_m'])
            image_path = acq.root / frame['filename']
            need(image_path.is_file(), 'NUSCENES_IMAGE', 'Raw front image is missing', path=str(image_path))
            raw.append(dict(raw_index=index, sample_token=sample['token'], frame_id=frame['token'],
                            reference_time_id='nusc_us_' + str(frame['timestamp']),
                            reference_time_s=frame['timestamp'] * 1e-6,
                            relative_sequence_time_s=(frame['timestamp'] - first) * 1e-6,
                            image_path=str(image_path), image_sha256=sha(image_path),
                            ego_speed_mps=cap['ego']['speed_mps'] if cap else None,
                            speed_source='causal_backward_difference_localization_0.5s; not CAN',
                            complete_input_available=eligible,
                            history_span_s=audit['history_span_s'] if audit else None,
                            role='WARMUP', planning_case_id=None,
                            display_only_ego_pose=dict(center_xy_world_m=center.tolist(), yaw_world_rad=yaw,
                                                       center_offset_m=cfg['vehicle']['center_to_rear_axle_m'])))
        first_index = next((r['raw_index'] for r in raw if r['complete_input_available']), None)
        need(first_index == specification['expected_first_index'], 'CLIP_HISTORY', 'Fixed causal-history start changed')
        need(all(r['complete_input_available'] for r in raw[first_index:]),
             'CLIP_COVERAGE', 'An interior reference lacks original full inputs; cannot silently skip it')
        chosen = list(range(first_index, len(raw), 2))
        if chosen[-1] != len(raw) - 1:
            chosen.append(len(raw) - 1)
        need(len(chosen) == 19, 'CLIP_SELECTION', 'The frozen stride must yield nineteen references')
        cases = []
        for row in raw[first_index:]:
            row['role'] = 'PLANNING_REFERENCE' if row['raw_index'] in chosen else 'OBSERVATION_ONLY'
        for ordinal, index in enumerate(chosen):
            row, cap, audit = raw[index], captures[index], audits[index]
            case_id = f'frame_{index:03d}'; row['planning_case_id'] = case_id
            target = reserve_directory(inputs / case_id)
            validate('capture_v2.schema.json', cap)
            write_json_new(target / 'capture.json', cap)
            case = dict(case_id=case_id, frame_index=index, raw_index=index,
                        planning_reference_index=ordinal, scene_name=specification['scene_name'],
                        scene_token=specification['scene_token'], sample_token=row['sample_token'],
                        relative_sequence_time_s=row['relative_sequence_time_s'],
                        reference_time_s=row['reference_time_s'], ego_speed_mps=cap['ego']['speed_mps'],
                        capture=path_from_root(target / 'capture.json'), capture_sha256=sha(target / 'capture.json'),
                        image_sha256=audit['image_sha256'], require_turn=False)
            write_json_new(target / 'input_manifest.json', dict(**case, model_requests=0,
                           image_count=10, history_span_s=audit['history_span_s'],
                           runtime_tables=audit['accessed_tables'], gt_in_runtime=False,
                           images=[dict(**o, sha256=audit['image_sha256'][o['id']]) for o in cap['observations']]))
            cases.append(case)
        plan = dict(common, experiment_id=root.name + '_' + specification['clip_id'],
                    operational_call_ceiling=2 * len(cases),
                    clip_id=specification['clip_id'], scene_name=specification['scene_name'],
                    scene_token=specification['scene_token'], selection_label=specification['label'],
                    selection_basis='Previously exposed acquisition/image-based scene choice; whole ego timeline is selection/display only. No annotation or map read.',
                    raw_frames=raw, cases=cases, frame_count=len(cases), raw_frame_count=len(raw),
                    full_clip_duration_s=raw[-1]['relative_sequence_time_s'],
                    first_planning_time_s=raw[first_index]['relative_sequence_time_s'],
                    raw_input_gif_final_hold_ms=500, input_preview='input_contactsheet.jpg',
                    raw_input_gif='raw_input.gif')
        write_json_new(folder / 'raw_frame_index.json', raw)
        write_json_new(folder / 'plan.json', plan)
        previews(raw, folder, specification['scene_name'] + ' | ' + specification['label'])
        clips.append(dict(clip_id=specification['clip_id'], scene_name=specification['scene_name'],
                          scene_token=specification['scene_token'], plan=path_from_root(folder / 'plan.json'),
                          plan_sha256=sha(folder / 'plan.json'), reference_count=len(cases),
                          raw_frame_count=len(raw), full_clip_duration_s=raw[-1]['relative_sequence_time_s'],
                          first_planning_time_s=raw[first_index]['relative_sequence_time_s']))
    top = dict(common, experiment_id=root.name, clips=clips, unique_scenes=3,
               serial=False, max_parallel_clips=2, serial_within_clip=True,
               planning_reference_count=sum(c['reference_count'] for c in clips),
               raw_frame_count=sum(c['raw_frame_count'] for c in clips),
               accessed_runtime_tables=sorted(acq.accessed), model_generation_requests=0,
               status='PREPARED_NOT_LAUNCHED')
    write_json_new(root / 'plan.json', top)
    return dict(out=str(root), clips=3, planning_references=57, raw_frames=122,
                model_generation_requests=0, gt_reads=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--dataset-root', default='/data/nuscenes_dataset')
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.out, arguments.dataset_root), ensure_ascii=False))
