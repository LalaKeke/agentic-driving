"""Read-only continuous CAM_FRONT + last-reference BEV animation.

Every acquired front frame is shown. Saved plans are reprojected with recorded
ego poses solely for display; a historical tail is never a fresh verification
or another execution. No model, planner, verifier, executor, annotation or map
is called. Input poses are a visualization side channel, not runtime facts.
"""
import argparse
from bisect import bisect_right
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.evidence_io import reserve_directory, write_json_new, write_text_new
from bend_result_figure import dense
from prototype_figure import load_result, body_corners, occupancy_polygons
from planning_front_gif import font, project, draw_path, CYAN, GREEN, RED
from append_bev_to_planning_gif import compose, BEV_CROP
from render_hard_clip_comparison import four_panel
from summarize_bend_sequence import signed_turn
from summarize_six_scene_scout import metrics

BACKGROUND = '#101c2b'
AMBER = '#ffd166'
STAGES = ('SERVICE', 'BUSINESS_SCHEMA', 'T1', 'T2', 'T3', 'T4',
          'TURN_TASK', 'ACCEPTOR', 'EXECUTION')


def read(path):
    return json.loads(Path(path).read_text())


def resolve(path):
    value = Path(path)
    return value.resolve() if value.is_absolute() else (ROOT / value).resolve()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def transform_plan(samples, reference_pose, current_pose):
    """Reference CENTER XY -> world -> recorded current CENTER XY, display only."""
    def rotation(pose):
        a = pose['yaw_world_rad']
        return np.asarray([[math.cos(a), -math.sin(a)],
                           [math.sin(a), math.cos(a)]])
    points = np.asarray(samples, dtype=float).copy()
    world = (points[:, 1:3] @ rotation(reference_pose).T
             + np.asarray(reference_pose['center_xy_world_m']))
    points[:, 1:3] = ((world - np.asarray(current_pose['center_xy_world_m']))
                      @ rotation(current_pose))
    points[:, 3] += reference_pose['yaw_world_rad'] - current_pose['yaw_world_rad']
    return points


def frame_durations_ms(frames):
    """Quantize cumulative capture time to GIF's 10 ms without cadence drift."""
    times = [f['capture_time_s'] for f in frames]
    gaps = [b - a for a, b in zip(times, times[1:])]
    if not gaps or min(gaps) <= 0:
        raise ValueError('Original front timestamps must strictly increase')
    last = statistics.median(gaps)
    edges = [round((t - times[0]) * 100) * 10 for t in times]
    edges.append(round((times[-1] - times[0] + last) * 100) * 10)
    durations = [b - a for a, b in zip(edges, edges[1:])]
    if min(durations) < 10:
        raise ValueError('Capture cadence is too dense for one-to-one GIF timing')
    return durations


def reference_availability(value):
    if value == 'NOT_ATTEMPTED':
        return 'NOT_ATTEMPTED'
    if value in ('NO_NATIVE_OUTPUT', 'NO_MODEL_RESULT', 'ATTEMPTED_WITHOUT_SAVED_NATIVE_OUTPUT'):
        return 'NO_NATIVE_OUTPUT'
    return 'AVAILABLE'


def reference_display_state(frame_time, reference_time, passed, availability=None):
    """Only the exact acquired reference can reuse its own saved PASS label."""
    if reference_time is None:
        return 'WARMUP', None, False
    age = frame_time - reference_time
    if age < -1e-7:
        raise ValueError('A future planning reference cannot annotate an earlier image')
    exact = abs(age) < 1e-7
    available = reference_availability(availability)
    state = (available if available != 'AVAILABLE' else 'REF_PASS' if passed and exact
             else 'HISTORICAL_PLAN_ONLY' if passed else 'REFUSED_NO_PREFIX')
    return state, age, exact


def load_saved_result(source):
    """Read a round summary and resolve the actual saved tool/receipt directory."""
    path = (source.get('round_result_path') or source.get('replay_output_dir')
            or source.get('runtime_path') or source.get('output_dir'))
    if not path:
        return None, None
    result, summary_folder = load_result(resolve(path))
    # A round_result.json can live one level above attempt_01. Its parent is
    # not necessarily where T4, acceptor and the execution receipt were saved.
    artifact_path = (source.get('runtime_path') or source.get('replay_output_dir')
                     or source.get('output_dir') or summary_folder)
    folder = resolve(artifact_path)
    if folder.is_file():
        folder = folder.parent
    selected = source.get('selected_attempt') or 1
    attempt = folder / f'attempt_{selected:02d}'
    if attempt.is_dir():
        folder = attempt
    for name, key in [('T1', 'scene'), ('T2', 'corridor'), ('T3', 'trajectory'),
                      ('T4', 'verification'), ('execution', 'execution'),
                      ('execution_receipt', 'execution_receipt')]:
        artifact = folder / (name + '.json')
        if artifact.is_file() and result.get(key) is not None and result[key] != read(artifact):
            raise ValueError(f'Round result differs from original saved {name} artifact')
    return result, folder


def fixed_bev_limits(sources):
    """Plot-only union of all saved model geometry; never a runtime input."""
    points = []
    def add(polygon):
        for point in polygon or []:
            if len(point) == 2 and all(isinstance(v, (int,float)) and math.isfinite(v) for v in point):
                points.append(point)
    for source in sources:
        result, _ = load_saved_result(source)
        if result is None:
            continue
        native = result.get('agent_output', {}).get('hypotheses') or {}
        hypothesis = result.get('scene', {}).get('hypotheses', native)
        add((hypothesis.get('road') or {}).get('polygon_m'))
        for actor in native.get('objects') or []:
            add(body_corners(actor))
            for polygon in occupancy_polygons(actor):
                add(polygon)
        for unknown in (native.get('unknown_regions') or []) + (native.get('unknowns') or []):
            add(unknown.get('polygon_m') or unknown.get('space_polygon_m'))
        for cell in result.get('corridor', {}).get('cells', []):
            add(cell.get('polygon_m'))
        if result.get('trajectory'):
            add(np.asarray(dense(result))[:,1:3].tolist())
        cap = result.get('scene', {}).get('capture', result.get('capture')) or read(resolve(source['capture_path']))
        vehicle = cap['vehicle']
        add([[-vehicle['rear_m'],-vehicle['half_width_m']], [vehicle['front_m'],vehicle['half_width_m']]])
    if not points:
        return None
    coordinates = np.asarray(points)
    low, high = coordinates.min(axis=0), coordinates.max(axis=0)
    padding = np.maximum(1., (high-low)*.04)
    return [[float(low[0]-padding[0]),float(high[0]+padding[0])],
            [float(low[1]-padding[1]),float(high[1]+padding[1])]]


class FixedBEVPlotter:
    """Apply display bounds at each save, after existing autoscale/layout code."""
    def __init__(self, plt, limits):
        self.plt, self.limits = plt, limits
    def __getattr__(self, name):
        return getattr(self.plt, name)
    def subplots(self, *args, **kwargs):
        figure, axes = self.plt.subplots(*args, **kwargs)
        limits, owner = self.limits, self
        class Figure:
            def __getattr__(self, name):
                return getattr(figure, name)
            def savefig(self, *values, **options):
                if limits:
                    bev = figure.axes[1]
                    bev.set_xlim(limits[0]); bev.set_ylim(limits[1])
                    bev.set_aspect('equal', adjustable='box')
                saved = figure.savefig(*values, **options)
                figure.canvas.draw()
                bounds = figure.axes[1].get_tightbbox(figure.canvas.get_renderer())
                width, height = figure.canvas.get_width_height()
                owner.bev_canvas_size = (width, height)
                owner.bev_crop = (math.floor(bounds.x0)-5, math.floor(height-bounds.y1)-5,
                                  math.ceil(bounds.x1)+5, math.ceil(height-bounds.y0)+5)
                return saved
        proxy = Figure(); proxy.original_figure = figure
        return proxy, axes
    def close(self, figure):
        return self.plt.close(getattr(figure, 'original_figure', figure))


def prepare_reference(source, raw_frames, out, plt, Patch, bev_limits=None):
    availability = reference_availability(source.get('availability') or source.get('status'))
    capture_path = resolve(source['capture_path'])
    cap = read(capture_path)
    key = source.get('raw_index', source.get('raw_keyframe_index'))
    matches = [f for f in raw_frames
               if (key is not None and f.get('raw_keyframe_index') == key)
               or (key is None and f['capture_time_id'] == cap['metadata']['reference_time_id'])]
    if len(matches) != 1:
        raise ValueError(f'Planning reference has no unique acquired front frame: {source}')
    raw = matches[0]
    if raw['capture_time_id'] != cap['metadata']['reference_time_id']:
        raise ValueError('Planning reference timestamp differs from original capture')
    case = source.get('case_id', f'frame_{key:03d}')
    result = dict(capture=cap, agent_output={})
    loaded, folder = load_saved_result(source)
    if loaded is not None:
        result = loaded
        saved_cap = result.get('scene', {}).get('capture', result.get('capture'))
        if saved_cap and saved_cap != cap:
            raise ValueError(f'{case}: replay capture differs from selected original capture')
    source_record = source.get('source_record_path', source.get('source_record'))
    if source_record:
        source_record = resolve(source_record)
        record = read(source_record)
        expected = source.get('source_record_sha256')
        if expected and expected != sha(source_record):
            raise ValueError(f'{case}: original source record SHA changed')
        if result.get('agent_output') and result['agent_output'] != record['output']:
            raise ValueError(f'{case}: saved replay changed original model output')
        if not result.get('agent_output'):
            result['agent_output'] = record['output']
    physical = metrics(folder) if folder else dict(prefix_consumed=False,
        independent_acceptor_pass=False, motion_class='NOT_EXECUTED')
    stages = {s: source.get('stages', {}).get(s, 'NOT_REACHED') for s in STAGES}
    # Stages are read from the actual saved result when not supplied by caller.
    for name, field in [('T1', 'scene'), ('T2', 'corridor'), ('T3', 'trajectory')]:
        if result.get(field) and stages[name] == 'NOT_REACHED':
            stages[name] = 'PASS'
    if result.get('verification'):
        stages['T4'] = result['verification'].get('verdict', 'UNKNOWN')
    if folder:
        stages['ACCEPTOR'] = 'PASS' if physical.get('independent_acceptor_pass') else stages['ACCEPTOR']
        stages['EXECUTION'] = 'PASS' if physical.get('prefix_consumed') else stages['EXECUTION']
    if availability != 'AVAILABLE':
        if result.get('agent_output') or result.get('trajectory') or physical.get('prefix_consumed'):
            raise ValueError(f'{case}: unavailable reference unexpectedly contains a native result/consumed plan')
        stages = {s: 'NOT_REACHED' for s in STAGES}
    passed = (physical.get('prefix_consumed') is True
              and physical.get('independent_acceptor_pass') is True
              and all(stages[s] == 'PASS' for s in ('T1', 'T2', 'T3', 'T4', 'ACCEPTOR', 'EXECUTION')))
    if (source.get('conditional_chain_pass') is not None
            and source['conditional_chain_pass'] != passed):
        raise ValueError(f'{case}: caller chain status differs from saved original tool/receipt status')
    if passed and not result.get('trajectory'):
        raise ValueError('Accepted prefix lacks its original trajectory')
    failure = source.get('failure')
    reasons = source.get('failure_reasons', source.get('final_failure_reasons', []))
    if failure and not reasons:
        reasons = [failure]
    row = dict(case_id=case, sample_token=source.get('sample_token', raw['sample_token']),
        scene_name=source.get('scene_name', 'scene-0103'), _clip='urban_interaction',
        capture_path=str(capture_path), runtime_path=str(folder or ''), selected_attempt=1,
        initial_speed_mps=cap['ego']['speed_mps'], availability=availability,
        status=availability if availability != 'AVAILABLE' else 'SAVED_CHAIN_PASS' if passed else 'REFUSED',
        final_failure_stage=(source.get('failure_stage', source.get('earliest_failure_stage'))
                             if availability == 'AVAILABLE' else None),
        final_failure_reasons=reasons, physical=physical, stages=stages,
        relative_sequence_time_s=raw['relative_sequence_time_s'], source='SAVED_REAL_MODEL / READ_ONLY_RENDER',
        source_group=source.get('source_group', 'Saved real model output'),
        api_post_requests=0, charged_requests=0, display_only_unreleased_candidate=False,
        signed_turn=signed_turn(result), turn_task_required=False,
        motion_review=dict(unexplained_hold_review_required=False))
    # A refused trajectory is not drawn as released, even if a T3 file exists.
    displayed_result = dict(result)
    if not passed:
        displayed_result.pop('trajectory', None)
    target = out / 'reference_plots' / (case + '.png')
    plotter = FixedBEVPlotter(plt, bev_limits)
    four_panel(row, displayed_result, cap, target, plotter, np, Patch, Image)
    with Image.open(target) as image:
        plot = image.convert('RGB').copy()
    if plot.size != (2170, 1400):
        raise ValueError('Existing BEV layout changed; refuse to guess crop')
    # Fixed metric aspect can move the old subplot box. Extract the actual BEV
    # axes plus all its labels, then fit it into the existing composition crop;
    # never let an adjacent front-image edge enter the right BEV panel.
    if getattr(plotter, 'bev_crop', None):
        sx, sy = plot.width/plotter.bev_canvas_size[0], plot.height/plotter.bev_canvas_size[1]
        bounds = tuple(round(v * (sx if i%2 == 0 else sy)) for i,v in enumerate(plotter.bev_crop))
        bev = plot.crop(bounds)
        bev.thumbnail((BEV_CROP[2]-BEV_CROP[0], BEV_CROP[3]-BEV_CROP[1]), Image.Resampling.LANCZOS)
        ImageDraw.Draw(plot).rectangle(BEV_CROP, fill='white')
        plot.paste(bev, (BEV_CROP[0]+((BEV_CROP[2]-BEV_CROP[0])-bev.width)//2,
                        BEV_CROP[1]+((BEV_CROP[3]-BEV_CROP[1])-bev.height)//2))
    return dict(row=row, result=result, reference=raw, passed=passed, plot=plot,
                samples=np.asarray(dense(result)) if passed else None,
                input=dict(capture=str(capture_path), capture_sha256=sha(capture_path),
                    source_record=str(source_record) if source_record else None,
                    source_record_sha256=sha(source_record) if source_record else None,
                    result_directory=str(folder) if folder else None,
                    four_panel_sha256=sha(target)))


def draw_near_field(image, samples, age, exact, prefix_s):
    """Show actual remaining current-local XY, never extend it into the camera."""
    draw = ImageDraw.Draw(image)
    left, top, width, height = 18, image.height - 223, 335, 205
    draw.rounded_rectangle((left, top, left+width, top+height), radius=8,
                           fill='#10202e', outline='#a8c7d8', width=2)
    draw.text((left+12, top+10), 'NEAR-FIELD PLAN / LOCAL XY', font=font(17, True), fill=CYAN)
    draw.text((left+12, top+36), 'Outside camera view' + ('' if exact else ' / historical'),
              font=font(14), fill='white')
    xmin, xmax = min(-.3, float(samples[:,1].min())-.3), max(3., float(samples[:,1].max())+.3)
    ylim = max(1.5, float(max(abs(samples[:,2])))+.3)
    def point(row):
        return (left+width/2-row[2]/ylim*(width/2-18),
                top+height-33-(row[1]-xmin)/(xmax-xmin)*(height-94))
    draw.line([point(q) for q in samples], fill=CYAN if exact else '#9dd3dc', width=4)
    if exact:
        prefix = samples[samples[:,0] <= prefix_s+1e-9]
        if len(prefix) >= 2:
            draw.line([point(q) for q in prefix], fill=GREEN, width=7)
    x, y = point(samples[0])
    draw.ellipse((x-4,y-4,x+4,y+4), fill=CYAN)
    draw.text((left+12, top+height-25), f't_ref+{age:.2f}..{samples[-1,0]:.2f}s | X {xmin:.1f}..{xmax:.1f}m',
              font=font(13), fill='#b7cbdc')


def render_frame(frame, ref, refs, frame_number, total_frames):
    image_path = resolve(frame['image_path'])
    if sha(image_path) != frame['image_sha256']:
        raise ValueError('Original acquired image SHA changed')
    with Image.open(image_path) as original:
        scale = 1280 / original.width
        image = original.convert('RGB').resize((1280, 720), Image.Resampling.LANCZOS)
    passed = ref is not None and ref['passed']
    state, age, exact = reference_display_state(frame['capture_time_s'],
        ref['reference']['capture_time_s'] if ref else None, passed,
        ref['row'].get('availability') if ref else None)
    unavailable = state in ('NOT_ATTEMPTED', 'NO_NATIVE_OUTPUT')
    color = GREEN if state == 'REF_PASS' else AMBER if passed else '#9eb3c9' if unavailable or not ref else RED
    visible = prefix_visible = 0
    near_field = False
    if passed:
        remaining = ref['samples'][ref['samples'][:, 0] >= age - 1e-7]
        if len(remaining) >= 2:
            shifted = transform_plan(remaining, ref['reference']['display_only_ego_pose'],
                                     frame['display_only_ego_pose'])
            uv, valid = project(shifted, frame['display_only_calibration'], scale)
            visible = draw_path(image, uv, valid, CYAN if exact else '#9dd3dc', 6)
            if exact:
                prefix = remaining[:, 0] <= ref['row']['physical']['prefix_s'] + 1e-9
                prefix_visible = draw_path(image, uv[prefix], valid[prefix], GREEN, 9)
            draw = ImageDraw.Draw(image)
            for seconds in (.5, 1., 1.5, 2.):
                if seconds < age:
                    continue
                k = int(np.argmin(abs(remaining[:, 0] - seconds)))
                x, y = uv[k]
                if valid[k] and 10 < x < 1190 and 25 < y < 700:
                    label = f'+{seconds:g}s(ref)'
                    draw.ellipse((x-4, y-4, x+4, y+4), fill=CYAN, outline=BACKGROUND, width=2)
                    box = draw.textbbox((x+9, y-21), label, font=font(15, True))
                    draw.rectangle((box[0]-3, box[1]-2, box[2]+3, box[3]+2), fill=BACKGROUND)
                    draw.text((x+9, y-21), label, fill=CYAN, font=font(15, True))
            if visible == 0:
                draw_near_field(image, shifted, age, exact, ref['row']['physical']['prefix_s'])
                near_field = True
    left = Image.new('RGB', (1280, 970), BACKGROUND)
    left.paste(image, (0, 100))
    draw = ImageDraw.Draw(left)
    draw.text((22, 10), 'Urban interaction / continuous recorded clip', fill='white', font=font(25, True))
    draw.text((22, 50), f"scene-0103 | +{frame['relative_sequence_time_s']:.3f}s | camera {frame_number+1}/{total_frames}",
              fill='#d4e0ee', font=font(19))
    badge = ('WARMUP / NO PLAN' if ref is None else 'NOT_ATTEMPTED / NO MODEL RESULT' if state == 'NOT_ATTEMPTED'
             else 'NO NATIVE OUTPUT / NO MODEL RESULT' if state == 'NO_NATIVE_OUTPUT'
             else ('REF PASS / ' + ref['row']['physical'].get('motion_class', '')) if exact and passed
             else 'HISTORICAL / NOT REVERIFIED' if passed else 'REFUSED / NO PREFIX')
    # Badge is on the camera area so the long title remains intact.
    draw.rectangle((16, 112, 1264, 158), fill=BACKGROUND)
    draw.text((28, 119), badge, fill=color, font=font(23, True))
    draw.line((0, 96, 1280, 96), fill=color, width=4)
    if ref:
        row = ref['row']; m = row['physical']; decision = ref['result'].get('agent_output', {}).get('decision', {})
        d = m.get('displacement_m'); v = m.get('end_speed_mps')
        t1 = f"Plan ref {row['case_id']} @{row['relative_sequence_time_s']:.3f}s | age {age:.3f}s | {decision.get('behavior','UNKNOWN')} | v0 {row['initial_speed_mps']:.2f} m/s"
        t2 = (f"Saved T1-T4/acceptor PASS; prefix consumed ONCE at ref: {d:.3f} m / v_end {v:.3f} m/s"
              if passed else 'NOT_ATTEMPTED: no real model request at this reference; T1-T4 / acceptor / prefix NOT_REACHED'
              if state == 'NOT_ATTEMPTED' else 'NO NATIVE OUTPUT: T1-T4 / acceptor / prefix NOT_REACHED; see service ledger'
              if state == 'NO_NATIVE_OUTPUT' else f"NO RELEASED TRAJECTORY | stage {row['final_failure_stage'] or 'see saved ledger'} | prefix NOT consumed")
        t3 = ('CYAN: reference 2s CENTER plan | GREEN: original consumed 0.2s prefix'
              if exact and passed else 'HISTORICAL_PLAN_ONLY / NOT_REVERIFIED: reprojected remaining tail; no new execution' if passed
              else 'No previous valid plan is reused at this reference; a missing model result is not a tool refusal.'
              if unavailable else 'No earlier valid plan is carried across this failed planning reference.')
    else:
        t1 = 'WARMUP: causal two-second input history is not yet available. No planning call at this time.'
        t2 = 'The original camera frames are retained; unknown scene is not treated as empty free space.'
        t3 = 'The first registered planning reference will appear after the two-second warmup.'
    for y, text, c in [(829, t1, 'white'), (855, t2, color), (881, t3, '#c4d5e6'),
                       (906, 'Display-only z=0 projection / recorded ego poses. Independent forecasts; no stitched execution or GT.', '#9eb3c9')]:
        draw.text((22, y), text, fill=c, font=font(15, y == 855))
    for index, item in enumerate(refs):
        x = 22 + index * (1236 / max(1, len(refs)))
        draw.rounded_rectangle((x, 941, x + 1236 / max(1, len(refs))-3, 959), radius=2,
            fill='#29a56c' if item['passed'] else '#687d91' if item['row'].get('availability') != 'AVAILABLE' else '#ad404b',
            outline='white' if item is ref else None, width=2)
    if ref:
        combined = compose(left, ref['plot'], ref['row'])
        right = ImageDraw.Draw(combined)
        right.rectangle((1296, 0, 2576, 99), fill=BACKGROUND)
        right.text((1318, 12), 'NO MODEL RESULT / BIRD\'S-EYE VIEW' if unavailable else 'LAST MODEL SCENE / REFERENCE BEV',
                   fill='white', font=font(25, True))
        right.text((1318, 53), f"{ref['row']['case_id']} @{ref['row']['relative_sequence_time_s']:.3f}s | scene age {age:.3f}s", fill='#d4e0ee', font=font(19))
        right.line((1296, 96, 2576, 96), fill=color, width=4)
        right.rectangle((1296, 820, 2576, 970), fill=BACKGROUND)
        notes = ['Original model road, BODY, all future envelopes and saved T2 corridor.',
                 'BEV remains in the last planning reference axes; it is not a fresh scene.',
                 'Blue: saved 2s plan. Green: prefix already consumed ONCE at that reference.',
                 'NOT_REVERIFIED on subsequent images; no hidden target truth or new execution.']
        if unavailable:
            notes[0] = 'No native model scene at this reference; unknown is not empty free space.'
            notes[1] = 'Previous model scene and trajectory are cleared; every tool stage is NOT_REACHED.'
            notes[2] = ('NOT_ATTEMPTED: no model result, no tool chain and no prefix consumption.'
                        if state == 'NOT_ATTEMPTED' else 'NO NATIVE OUTPUT: no tool chain and no prefix consumption.')
        elif not passed:
            notes[2] = 'REFUSED: no released trajectory / no simulated prefix at this reference.'
        for i, text in enumerate(notes):
            right.text((1318, 833+i*29), text, fill='#c4d5e6', font=font(17))
    else:
        combined = Image.new('RGB', (2576, 970), BACKGROUND)
        combined.paste(left, (0, 0)); right = ImageDraw.Draw(combined)
        right.text((1340, 35), 'LOCAL SCENE / BIRD\'S-EYE VIEW', font=font(27, True), fill='white')
        right.text((1550, 350), 'WARMUP / NO MODEL SCENE', font=font(30, True), fill='#b3c3d4')
        right.text((1460, 410), 'No scene or trajectory is invented for the warmup.', font=font(20), fill='#b3c3d4')
    record = dict(front_frame_id=frame['frame_id'], capture_time_s=frame['capture_time_s'],
        relative_sequence_time_s=frame['relative_sequence_time_s'], image_path=str(image_path),
        image_sha256=frame['image_sha256'], planning_reference=ref['row']['case_id'] if ref else None,
        reference_age_s=age, exact_planning_reference=exact, display_state=state,
        reference_availability=ref['row'].get('availability') if ref else None,
        saved_reference_chain_pass=passed, saved_verification_matches_current_image=bool(exact and passed),
        new_verification_by_renderer=False,
        historical_plan_only=bool(passed and not exact), path_visible_segments=visible,
        near_field_inset_drawn=near_field,
        green_prefix_drawn_at_exact_reference_only=bool(prefix_visible),
        new_model_calls=0, new_planner_calls=0, new_verifier_calls=0, new_execution_calls=0)
    return combined, record


def render(manifest_path, full_frames_path, destination, width=1920, mp4=False):
    manifest_path, full_frames_path = resolve(manifest_path), resolve(full_frames_path)
    manifest, front = read(manifest_path), read(full_frames_path)
    raw = front['frames']
    sources = manifest.get('planning_results', manifest.get('frames'))
    if not sources:
        raise ValueError('Manifest must contain planning_results (or frames)')
    out = reserve_directory(destination, readonly_inputs=(manifest_path, full_frames_path))
    (out/'reference_plots').mkdir()
    (out/'frames').mkdir()
    with tempfile.TemporaryDirectory(prefix='continuous-clip-mpl-') as cache:
        os.environ['MPLCONFIGDIR'] = cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as Patch
        bev_limits = fixed_bev_limits(sources)
        refs = [prepare_reference(s, raw, out, plt, Patch, bev_limits) for s in sources]
    refs.sort(key=lambda r: r['reference']['capture_time_s'])
    ref_times = [r['reference']['capture_time_s'] for r in refs]
    if any(b <= a for a, b in zip(ref_times, ref_times[1:])):
        raise ValueError('Planning reference times must strictly increase')
    durations = frame_durations_ms(raw)
    images, records = [], []
    for index, frame in enumerate(raw):
        k = bisect_right(ref_times, frame['capture_time_s'] + 1e-8) - 1
        image, record = render_frame(frame, refs[k] if k >= 0 else None, refs, index, len(raw))
        height = round(image.height * width / image.width)
        height += height % 2
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        image.save(out/'frames'/f'{index:04d}.jpg', quality=91)
        # P images bound memory while preserving every original acquisition.
        images.append(image.quantize(colors=256, method=Image.Quantize.MEDIANCUT))
        record['display_duration_ms'] = durations[index]
        records.append(record)
    gif_path = out/'continuous_front_bev.gif'
    with gif_path.open('xb') as stream:
        images[0].save(stream, format='GIF', save_all=True, append_images=images[1:],
                       duration=durations, loop=0, disposal=2, optimize=False)
    with Image.open(gif_path) as gif:
        if gif.n_frames != len(raw):
            raise ValueError('GIF did not retain every acquired camera frame')
        encoded_durations = []
        for index in range(gif.n_frames):
            gif.seek(index); encoded_durations.append(gif.info['duration'])
        if encoded_durations != durations:
            raise ValueError('Encoded GIF durations differ from capture-time quantization')
    preview = next((i for i, r in enumerate(records) if r['display_state']=='REF_PASS'), len(raw)//2)
    with Image.open(out/'frames'/f'{preview:04d}.jpg') as image:
        image.save(out/'preview.png')
    video_path = None
    if mp4:
        concat = []
        for index, duration in enumerate(durations):
            concat += [f"file 'frames/{index:04d}.jpg'", f'duration {duration/1000:.3f}']
        concat.append(f"file 'frames/{len(raw)-1:04d}.jpg'")
        write_text_new(out/'frames.ffconcat', '\n'.join(concat)+'\n')
        video_path = out/'continuous_front_bev.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'concat', '-safe', '0',
                        '-i', str(out/'frames.ffconcat'), '-vsync', 'vfr',
                        '-c:v', 'libx264', '-crf', '21', '-pix_fmt', 'yuv420p', str(video_path)], check=True)
    summary = dict(clip_id=manifest.get('clip_id','urban_interaction'),
        scene_name=front['scene_name'], scene_token=front['scene_token'],
        new_model_calls=0, new_planner_calls=0, new_verifier_calls=0, new_execution_calls=0,
        display_only=True, all_acquired_front_frames_retained=True, raw_front_frames=len(raw),
        planning_references=len(refs), accepted_reference_prefixes=sum(r['passed'] for r in refs),
        failed_reference_prefixes=sum(not r['passed'] and r['row']['availability']=='AVAILABLE' for r in refs),
        not_attempted_references=sum(r['row']['availability']=='NOT_ATTEMPTED' for r in refs),
        attempted_without_native_output_references=sum(r['row']['availability']=='NO_NATIVE_OUTPUT' for r in refs),
        display_state_counts=dict(Counter(r['display_state'] for r in records)),
        recorded_duration_s=raw[-1]['capture_time_s']-raw[0]['capture_time_s'],
        gif_duration_ms=sum(durations), last_frame_hold_ms=durations[-1],
        gif=gif_path.name, gif_sha256=sha(gif_path),
        video=video_path.name if video_path else None, dimensions_px=[width, images[0].height],
        manifest_path=str(manifest_path), manifest_sha256=sha(manifest_path),
        full_front_frames_path=str(full_frames_path), full_front_frames_sha256=sha(full_frames_path),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
        renderer_sha256=sha(__file__), interpolation='No image interpolation; every acquired image is shown. Exact saved constant-control ego integration for display only.',
        projection='Reference CENTER plan -> recorded world -> current recorded CENTER -> current calibration; display-only z=0. No second rear-axle offset.',
        historical_tail='All non-reference images are NOT_REVERIFIED / HISTORICAL_PLAN_ONLY. Prefix consumption is read once at its original reference, never executed by renderer.',
        bev='Held last-reference original model scene, explicitly aged; fixed union display bounds of all saved reference model geometry; no actor truth/interpolation.',
        bev_fixed_limits_m=bev_limits,
        limitation='Independent recorded observations, not a stitched or counterfactual executed drive. No GT accuracy/safety claim.',
        references=[dict(case_id=r['row']['case_id'], relative_sequence_time_s=r['row']['relative_sequence_time_s'],
                         availability=r['row']['availability'], status=r['row']['status'], stages=r['row']['stages'],
                         accepted_saved_prefix=r['passed'], physical=r['row']['physical'], inputs=r['input']) for r in refs])
    write_json_new(out/'frames.json', dict(summary=summary, frames=records))
    write_json_new(out/'summary.json', summary)
    write_text_new(out/'README.md', '\n'.join([
        '# 完整连续前视 + 参考时刻BEV', '',
        f"保留全部{len(raw)}张原始前视采集图，{len(refs)}个规划时刻。渲染新增模型/规划/核验/执行调用均为0。", '',
        '![连续规划回放](continuous_front_bev.gif)', '',
        '前视原始图按采集时间连续更新；青色为最近参考时刻的剩余计划，仅用录制ego姿态重投影。非规划参考帧一律标为HISTORICAL_PLAN_ONLY / NOT_REVERIFIED，不能沿用为当前核验。绿色仅在原规划参考帧表示当时已消费的一次0.2秒前缀。', '',
        '右侧保留最近模型场景的BEV及其年龄；坐标仍属该参考时刻，不当作当前目标真值。失败或无模型结果参考均清除旧轨迹；未尝试、请求后无原生输出、正常工具拒绝分别列出。片首无2秒历史为WARMUP，不计通过。', '',
        '此为真实录制观测下的独立滚动规划展示，不是反事实多轮执行。z=0投影仅为展示，G2未评价。', '',
        '复现必须使用全新输出目录：', '', '```bash',
        'python implementation/experiments/render_continuous_clip.py \\',
        f'  --manifest {manifest_path} \\',
        f'  --full-front-frames {full_frames_path} \\',
        '  --out /tmp/new_continuous_clip_render --mp4', '```', '']))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('references',)}, ensure_ascii=False), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--full-front-frames', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--mp4', action='store_true')
    arguments = parser.parse_args()
    render(arguments.manifest, arguments.full_front_frames, arguments.out, arguments.width, arguments.mp4)
