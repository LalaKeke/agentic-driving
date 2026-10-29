"""Read-only per-time and combined views of one recorded turning sequence.

No inference, planning, GT, scene conversion or execution is performed here.
Plans from different recorded states are alternatives, never a stitched drive.
"""
import argparse
import csv
import html
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.evidence_io import reserve_directory, write_json_new, write_text_new
from prototype_figure import load_result, occupancy_polygons, body_corners
from bend_result_figure import dense
from summarize_six_scene_scout import case_row, metrics, read

STAGES = ('SERVICE', 'BUSINESS_SCHEMA', 'T1', 'T2', 'T3', 'T4',
          'TURN_TASK', 'ACCEPTOR', 'EXECUTION')
LIMITATION = ('Recorded observations, not observations caused by these plans. '
              'Each 2s plan and consumed 0.2s prefix starts independently; '
              'no stitched execution, real vehicle control or GT evaluation.')


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def rotate(point, angle):
    c, s = math.cos(angle), math.sin(angle)
    return [c * point[0] - s * point[1], s * point[0] + c * point[1]]


def transform_points(points, transform):
    return [[q[0] + transform['translation_m'][0], q[1] + transform['translation_m'][1]]
            for p in points for q in [rotate(p, transform['yaw_rad'])]]


def align_captures(captures):
    """Exact common acquisition records; never interpolate future ego states.

    All coordinates already include the export's explicit CENTER offset.  A
    common historic observation has the same physical ego center and heading
    in both captures, which determines the planar rigid transform exactly.
    """
    transforms = []
    for index, cap in enumerate(captures):
        if index == 0:
            transforms.append(dict(status='ALIGNED', translation_m=[0., 0.], yaw_rad=0.,
                                   parent_frame=None, common_record=None))
            continue
        current = {(h['frame_id'], h['time_id']): h
                   for h in cap.get('acquisition_metadata', {}).get('ego_history', [])}
        chosen = None
        for previous in range(index - 1, -1, -1):
            if transforms[previous]['status'] != 'ALIGNED':
                continue
            history = {(h['frame_id'], h['time_id']): h
                       for h in captures[previous].get('acquisition_metadata', {}).get('ego_history', [])}
            common = set(current) & set(history)
            if not common:
                continue
            # Prefer the previous reference itself, otherwise the latest exact
            # common record. No nearest-time match or pose interpolation.
            reference = captures[previous]['metadata']['reference_time_id']
            key = max(common, key=lambda k: (k[1] == reference, k[1], k[0]))
            a, b = history[key], current[key]
            parent = transforms[previous]
            angle = parent['yaw_rad'] + a['yaw_rad'] - b['yaw_rad']
            world_common = transform_points([a['center_xy_m']], parent)[0]
            offset = rotate(b['center_xy_m'], angle)
            chosen = dict(status='ALIGNED', translation_m=[world_common[k] - offset[k] for k in (0, 1)],
                          yaw_rad=angle, parent_frame=previous,
                          common_record=dict(frame_id=key[0], time_id=key[1]),
                          display_only=True, center_offset_added_again=False)
            break
        transforms.append(chosen or dict(status='UNAVAILABLE_NO_EXACT_COMMON_RECORD',
                                        translation_m=None, yaw_rad=None))
    return transforms


def signed_turn(result):
    points = result.get('trajectory', {}).get('points', [])
    if not points:
        return dict(plan_end_yaw_change_deg=None, plan_end_lateral_m=None,
                    prefix_yaw_change_deg=None, direction_review='NOT_EVALUABLE_NO_PLAN')
    start = result['scene']['capture']['ego']
    end = points[-1]
    execution = result.get('execution', {})
    # Display exact declared ego motion; do not interpolate other actors.
    samples = dense(result)
    prefix_s = result['scene']['capture']['safety']['prefix_s']
    prefix = min(samples, key=lambda p: abs(p[0] - prefix_s))
    return dict(plan_end_yaw_change_deg=(end['yaw_rad'] - start['yaw_rad']) * 180 / math.pi,
                plan_end_lateral_m=end['y_m'] - start['y_m'],
                prefix_yaw_change_deg=(prefix[3] - start['yaw_rad']) * 180 / math.pi,
                prefix_measurement_source='Exact declared control integration; consumption separately checked',
                prefix_was_consumed=bool(execution),
                direction_review='REQUIRES_IMAGE_REVIEW; amplitude gate does not establish correct turn direction')


def frame_result(case, run_root):
    """Keep missing/failed frames and TASK separate from physical/service gates."""
    cap = read(resolve(case['capture']))
    normalized = dict(case, scene_name=case.get('scene_name', 'scene-0916'),
                      ego_speed_mps=cap['ego']['speed_mps'])
    summary = case_row(normalized, run_root, run_root, {})
    runtime = resolve(summary['runtime_path'])
    runtime_config = read(runtime / 'config.json', {})
    attempt_no = summary['selected_attempt']
    chosen = runtime / f'attempt_{attempt_no:02d}' if attempt_no else runtime
    if attempt_no:
        result, _ = load_result(chosen)
    else:
        result = dict(capture=cap, agent_output={}, provenance={})
    result.setdefault('capture', cap)
    if not result.get('capture'):
        result['capture'] = cap
    stage_info = read(chosen / 'stages.json', {})
    event = next((x for x in summary['attempts'] if x['attempt'] == attempt_no), {})
    stages = {s: event.get('raw_stage_ledger', {}).get(s, 'NOT_REACHED') for s in STAGES}
    stages.update({k: v for k, v in stage_info.get('stages', {}).items() if k in stages})
    if attempt_no:
        stages['SERVICE'] = 'PASS' if event.get('native_json_present') else 'FAIL'
        stages['BUSINESS_SCHEMA'] = event.get('native_business_schema', 'NOT_REACHED')
    task = read(chosen / 'turn_task.json')
    if task:
        stages['TURN_TASK'] = task['verdict']
    failure = stage_info.get('failure') or event.get('raw_last_failure')
    task_failed = stages['TURN_TASK'] == 'FAIL' or (failure or {}).get('code') == 'TURN_TASK_NOT_DEMONSTRATED'
    if task_failed:
        # The generic six-scene helper predates the explicit task gate.
        summary['final_failure_stage'] = 'TURN_TASK'
        summary['final_failure_reasons'] = [failure] if failure else []
        summary['issue_classes_any_attempt'] = sorted(
            (set(summary['issue_classes_any_attempt']) - {'SERVICE_INTERFACE', 'INCOMPLETE_EVIDENCE'}) | {'TURN_TASK_NOT_DEMONSTRATED'})
        if not summary.get('fatal_service_or_interface_failure'):
            summary['status'] = 'TOOL_OR_SEMANTIC_FAILURE'
    summary['selected_native_failure_stage'] = summary['final_failure_stage']
    summary['selected_native_failure_reasons'] = summary['final_failure_reasons']
    if summary.get('fatal_service_or_interface_failure'):
        # The last request may have failed after an earlier valid native JSON.
        # Keep both facts rather than painting the earlier service layer red.
        summary['final_failure_stage'] = 'SERVICE'
        summary['final_failure_reasons'] = [summary['fatal_service_or_interface_failure']]
    # An unselected candidate can be pictured only as an unreleased candidate.
    display_candidate = None
    if not result.get('trajectory'):
        ledger = read(chosen / 'candidate_ledger.json', {})
        candidates = [r for r in ledger.get('candidates', []) if r.get('T3') == 'PASS']
        candidates.sort(key=lambda r: r.get('T4') != 'PASS')
        if candidates:
            candidate = candidates[0]
            candidate_id = candidate.get('candidate_id', candidate.get('id'))
            if candidate_id:
                folder = chosen / 'candidates' / candidate_id
                for name, key in (('T1', 'scene'), ('T2', 'corridor'), ('T3', 'trajectory'), ('T4', 'verification')):
                    value = read(folder / (name + '.json'))
                    if value is not None:
                        result[key] = value
                result.setdefault('scene', read(chosen / 'T1.json', {}))
                display_candidate = candidate_id
                task = read(folder / 'TURN_TASK.json', task)
    summary.update(frame_index=case.get('frame_index'),
                   relative_sequence_time_s=case.get('relative_sequence_time_s'),
                   reference_time_s=cap['metadata']['reference_time_s'],
                   reference_time_id=cap['metadata']['reference_time_id'],
                   input_image_count=len(cap['observations']),
                   capture_path=str(resolve(case['capture'])),
                   stages=stages, selected_attempt=attempt_no,
                   stage_origin_attempt=attempt_no,
                   run_mode=runtime_config.get('mode', 'NOT_ATTEMPTED'),
                   task=task, signed_turn=signed_turn(result),
                   display_only_unreleased_candidate=display_candidate,
                   source=result.get('provenance', {}).get('source', 'NO_NATIVE_OUTPUT'),
                   actual_server_snapshot=result.get('provenance', {}).get('server_reported_snapshot'),
                   direction_review='PENDING_INDEPENDENT_IMAGE_REVIEW',
                   claim=LIMITATION)
    return summary, result, cap


def self_check():
    """Two artificial checks: rigid alignment and explicit missing result."""
    key = dict(frame_id='same_acquisition', time_id='time_0')
    a = dict(metadata=dict(reference_time_id='time_0'), acquisition_metadata=dict(ego_history=[
        dict(key, center_xy_m=[0., 0.], yaw_rad=0.)]))
    # Current origin is (2,3) and current heading is +90 deg in frame zero.
    b = dict(metadata=dict(reference_time_id='time_1'), acquisition_metadata=dict(ego_history=[
        dict(key, center_xy_m=[-3., 2.], yaw_rad=-math.pi / 2)]))
    aligned = align_captures([a, b])
    q = transform_points([[1., 0.]], aligned[1])[0]
    assert max(abs(q[k] - [2., 4.][k]) for k in (0, 1)) < 1e-12
    missing = align_captures([a, dict(metadata={}, acquisition_metadata=dict(ego_history=[]))])
    assert missing[1]['status'] == 'UNAVAILABLE_NO_EXACT_COMMON_RECORD'
    assert signed_turn({})['plan_end_yaw_change_deg'] is None
    # A nonexistent runtime is a deliberate NOT_ATTEMPTED, no fabricated actor.
    with tempfile.TemporaryDirectory(prefix='bend-summary-selfcheck-') as tmp:
        directory = Path(tmp)
        capture = dict(metadata=dict(reference_time_s=0., reference_time_id='t0'),
                       ego=dict(speed_mps=2.), observations=[])
        (directory / 'capture.json').write_text(json.dumps(capture))
        row, value, _ = frame_result(dict(case_id='missing', sample_token='x', scene_token='s',
                                         capture=str(directory / 'capture.json')), directory)
        assert row['status'] == 'NOT_ATTEMPTED' and row['charged_requests'] == 0
        assert not value['agent_output'] and not value.get('trajectory')
        assert all(x == 'NOT_REACHED' for x in row['stages'].values())
    return dict(alignment='PASS', missing_result='PASS', artificial=True,
                real_model_calls=0, GT_used=False)


def render_frame(row, result, cap, out, plt, np, Patch, Image):
    fig, axes = plt.subplots(2, 2, figsize=(15.5, 10.))
    image_ax, bev, speed_ax, text_ax = axes.flat
    fronts = [f for f in cap['observations'] if f['camera_id'] == 'CAM_FRONT']
    frame = max(fronts, key=lambda f: f['capture_time_s']) if fronts else None
    original = None
    if frame and Path(frame['path']).is_file():
        original = Image.open(frame['path']); image_ax.imshow(original)
    else:
        image_ax.text(.5, .5, 'Original front image unavailable', ha='center')
    image_ax.axis('off'); image_ax.set_title('Original recorded image / display-only model projection')
    cal = cap.get('acquisition_metadata', {}).get('calibrations', {}).get('CAM_FRONT')
    def project(xy):
        if original is None or not frame or not cal or frame['time_id'] != cap['metadata']['reference_time_id']:
            return None
        xyz = np.column_stack((np.asarray(xy, dtype=float), np.zeros(len(xy)))).T
        camera = np.asarray(cal['camera_to_center_rotation']).T @ (
            xyz - np.asarray(cal['camera_to_center_translation_m'])[:, None])
        uv = np.asarray(cal['camera_intrinsic']) @ camera
        with np.errstate(divide='ignore', invalid='ignore'):
            uv = uv[:2] / uv[2:3]
        return uv.T, camera[2] > .3
    native = result.get('agent_output', {}).get('hypotheses', {})
    hypothesis = result.get('scene', {}).get('hypotheses', native)
    decision = result.get('agent_output', {}).get('decision', {})
    road = hypothesis.get('road')
    if road:
        bev.add_patch(Patch(road['polygon_m'], fc='#e8edf1', ec='#64748b', label='Model local road'))
    actors = native.get('objects', [])
    for i, actor in enumerate(actors):
        color = plt.get_cmap('tab10')(i % 10); seen = set()
        for polygon in occupancy_polygons(actor):
            key = json.dumps(polygon)
            if key in seen:
                continue
            seen.add(key); bev.add_patch(Patch(polygon, fc=color, ec=color, alpha=.13))
        corners = body_corners(actor)
        if corners:
            bev.add_patch(Patch(corners, fc=color, ec=color, alpha=.7))
        if actor.get('position_m') is not None:
            x, y = actor['position_m']; bev.scatter(x, y, c=[color], s=20)
            bev.annotate(str(i + 1) + ' ' + actor['id'], (x, y), fontsize=7, color=color)
            projected = project([[x, y]])
            if projected is not None and projected[1][0]:
                uv = projected[0][0]
                if 0 <= uv[0] < original.width and 0 <= uv[1] < original.height:
                    image_ax.scatter(*uv, c=[color], s=45, edgecolors='white')
                    image_ax.annotate(str(i + 1), uv, color='white', fontsize=9, weight='bold')
    for cell in result.get('corridor', {}).get('cells', []):
        bev.add_patch(Patch(cell['polygon_m'], fc='#60c6d2', ec='#60c6d2', alpha=.07))
    vehicle = cap['vehicle']
    bev.add_patch(Patch([[-vehicle['rear_m'], -vehicle['half_width_m']],
                        [vehicle['front_m'], -vehicle['half_width_m']],
                        [vehicle['front_m'], vehicle['half_width_m']],
                        [-vehicle['rear_m'], vehicle['half_width_m']]], fc='#23334a', label='Initial ego'))
    if result.get('trajectory'):
        samples = np.asarray(dense(result)); color = '#8b5a2b' if row['display_only_unreleased_candidate'] else '#1755cf'
        label = 'Unreleased candidate' if row['display_only_unreleased_candidate'] else '2s plan'
        bev.plot(samples[:, 1], samples[:, 2], c=color, lw=2.5, label=label)
        speed_ax.plot(samples[:, 0], samples[:, 4], c=color, label=label)
        projected = project(samples[:, 1:3])
        if projected is not None:
            uv, visible = projected
            image_ax.plot(np.where(visible, uv[:, 0], np.nan),
                          np.where(visible, uv[:, 1], np.nan), color='#00ef92', lw=2.5)
        if row['physical'].get('prefix_consumed'):
            prefix = samples[samples[:, 0] <= row['physical']['prefix_s'] + 1e-9]
            bev.plot(prefix[:, 1], prefix[:, 2], c='#17a66b', lw=6, label='Consumed 0.2s prefix')
            speed_ax.axvspan(0, row['physical']['prefix_s'], color='#17a66b', alpha=.2)
        speed_ax.legend(fontsize=8)
    else:
        bev.text(.5, .94, 'NO PLAN / NO PREFIX', color='#b33', ha='center', transform=bev.transAxes)
        speed_ax.text(.5, .5, 'NOT_REACHED — no planned speed', ha='center', transform=speed_ax.transAxes)
    if not native:
        bev.text(.5, .5, 'NO NATIVE MODEL OUTPUT\nGeometry unknown, not empty',
                 ha='center', transform=bev.transAxes, color='#b33')
    if frame and Path(frame['path']).is_file():
        image_ax.set_xlim(0, original.width); image_ax.set_ylim(original.height, 0)
    bev.set(xlabel='Local X forward (m)', ylabel='Local Y left (m)', title='Saved native model geometry / independent local plan')
    bev.set_aspect('equal', adjustable='datalim'); bev.autoscale_view(); bev.grid(alpha=.2)
    bev.legend(fontsize=7, loc='best')
    speed_ax.set(xlabel='Time from this observation (s)', ylabel='Speed (m/s)', title='This window only; tails not executed')
    speed_ax.grid(alpha=.2)
    m = row['physical']; turn = row['signed_turn']; text_ax.axis('off')
    lines = [f"{row['case_id']}  t0=+{row.get('relative_sequence_time_s', 0):.3f}s",
             f"Status: {row['status']} / block: {row['final_failure_stage'] or 'NONE'}",
             f"Real requests: {row['charged_requests']} | source: {row['source']}",
             f"Attempt: {row['selected_attempt']} | actual server snapshot: UNKNOWN",
             f"Decision: {decision.get('behavior', 'NOT_REACHED')} | desired v: {decision.get('desired_speed_mps', 'UNKNOWN')}",
             'Stages: ' + ' | '.join(f'{s}={row["stages"][s]}' for s in STAGES),
             f"Objects declared: {len(actors) if native else 'UNKNOWN'}",
             f"T4 stage: {row['stages']['T4']} | selected-plan T4: {m.get('T4_verdict', 'NOT_REACHED')}",
             f"Prefix consumed: {m.get('prefix_consumed', False)} | clearance lower bound: {m.get('minimum_conditional_clearance_lower_bound_m')} m",
             f"Prefix: {m.get('motion_class', 'NOT_EXECUTED')} | distance={m.get('displacement_m')} m",
             f"Prefix end speed: {m.get('end_speed_mps')} m/s",
             f"Signed plan yaw: {turn['plan_end_yaw_change_deg']} deg | lateral={turn['plan_end_lateral_m']} m",
             f"Signed prefix yaw: {turn['prefix_yaw_change_deg']} deg",
             'Turn direction / road agreement: independent review pending.',
             'Amplitude gate: plan >=5 deg and >=0.3m; prefix >=0.1 deg and >0.01m.',
             'These are descriptive gates, not safety or correct-turn proof.',
             'Image dots/line: display-only z=0 projection, not detection/GT.',
             'REJECTED CANDIDATE / NOT CONSUMED' if row['display_only_unreleased_candidate'] else '',
             '', LIMITATION]
    lines = [segment for line in lines for segment in (textwrap.wrap(line, 88) or [''])]
    text_ax.text(0, 1, '\n'.join(lines), va='top', fontsize=8, wrap=False)
    fig.suptitle(f"{row['case_id']} | +{row.get('relative_sequence_time_s', 0):.2f}s | {row['status']}", fontsize=16)
    fig.tight_layout(rect=(0, .02, 1, .95)); fig.savefig(out, dpi=140); plt.close(fig)


def render_overview(rows, results, transforms, out, plt, np):
    fig, axes = plt.subplots(2, 2, figsize=(15, 10.5)); timeline, bev, speed, yaw = axes.flat
    colors = plt.get_cmap('tab10')
    palette = {'PASS': '#2b9b65', 'FAIL': '#ca4b4b', 'NOT_REACHED': '#dddddd'}
    for index, row in enumerate(rows):
        for s, stage in enumerate(STAGES):
            timeline.scatter(index, s, s=190, marker='s', c=palette.get(row['stages'][stage], '#c8a26a'))
    timeline.set(xticks=range(len(rows)), xticklabels=[r['case_id'] for r in rows],
                 yticks=range(len(STAGES)), yticklabels=STAGES,
                 title='Selected native attempt stages (final status in frame sheets)')
    timeline.tick_params(axis='x', rotation=30); timeline.invert_yaxis()
    recorded = []
    for i, (row, result, transform) in enumerate(zip(rows, results, transforms)):
        color = colors(i % 10); label = f"{row['case_id']} t0=+{row['relative_sequence_time_s']:.2f}s"
        if transform['status'] == 'ALIGNED':
            recorded.append(transform['translation_m']); xy = transform['translation_m']
            bev.scatter(*xy, c=[color], s=38); bev.annotate(row['case_id'], xy, fontsize=7)
        else:
            bev.text(.02, .9 - i * .05, f"{row['case_id']}: alignment unavailable", transform=bev.transAxes)
        if not result.get('trajectory'):
            continue
        a = np.asarray(dense(result))
        if transform['status'] == 'ALIGNED':
            xy = np.asarray(transform_points(a[:, 1:3], transform))
            bev.plot(xy[:, 0], xy[:, 1], color=color, ls='--' if row['display_only_unreleased_candidate'] else '-', label=label)
            if row['physical'].get('prefix_consumed'):
                prefix = a[:, 0] <= row['physical']['prefix_s'] + 1e-9
                bev.plot(xy[prefix, 0], xy[prefix, 1], c=color, lw=5)
        speed.plot(a[:, 0] + row['relative_sequence_time_s'], a[:, 4], color=color, label=label)
        yaw.plot(a[:, 0], (a[:, 3] - a[0, 3]) * 180 / math.pi, color=color, label=label)
    if recorded:
        path = np.asarray(recorded); bev.plot(path[:, 0], path[:, 1], '--', color='#777', label='Recorded localization reference; NOT executed')
    bev.set(xlabel='First-frame X (m)', ylabel='First-frame Y (m)', title='Display-only alignment; independent overlapping 2s plans')
    bev.set_aspect('equal', adjustable='datalim'); bev.grid(alpha=.2)
    if bev.get_legend_handles_labels()[0]: bev.legend(fontsize=7)
    speed.set(xlabel='Recorded sequence time + local plan time (s)', ylabel='Planned speed (m/s)',
              title='Overlapping forecasts, not a continuous executed speed trace')
    yaw.set(xlabel='Time from each observation (s)', ylabel='Signed plan yaw change (deg)',
            title='Signed turning; amplitude does not establish correct direction')
    yaw.axhline(5, color='#999', ls=':'); yaw.axhline(-5, color='#999', ls=':')
    speed.grid(alpha=.2); yaw.grid(alpha=.2)
    if speed.get_legend_handles_labels()[0]: speed.legend(fontsize=7)
    fig.suptitle('One original recording / six independent online decisions', fontsize=17)
    fig.text(.02, .014, LIMITATION, fontsize=8)
    fig.tight_layout(rect=(0, .04, 1, .95)); fig.savefig(out, dpi=150); plt.close(fig)


def main(plan_path, out):
    plan_path = plan_path.resolve(); run_root = plan_path.parent; plan = read(plan_path)
    batch = read(run_root / 'batch_summary.json')
    if not batch or batch.get('ended') is not True:
        raise ValueError('Sequence batch must have ended=true before producing the final report')
    out = reserve_directory(out)
    checks = self_check(); rows = []; results = []; captures = []
    for case in plan['cases']:
        row, value, cap = frame_result(case, run_root)
        rows.append(row); results.append(value); captures.append(cap)
    for index, row in enumerate(rows):
        if row['frame_index'] is None:
            row['frame_index'] = index
        if row['relative_sequence_time_s'] is None:
            row['relative_sequence_time_s'] = row['reference_time_s'] - rows[0]['reference_time_s']
    transforms = align_captures(captures)
    for row, transform in zip(rows, transforms):
        row['display_transform_to_first_frame'] = transform
    with tempfile.TemporaryDirectory(prefix='bend-sequence-mpl-') as cache:
        os.environ['MPLCONFIGDIR'] = cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        from matplotlib.patches import Polygon as Patch
        from PIL import Image, ImageOps
        for row, value, cap in zip(rows, results, captures):
            folder = reserve_directory(out / row['case_id'])
            write_json_new(folder / 'result.json', row)
            render_frame(row, value, cap, folder / 'result.png', plt, np, Patch, Image)
        render_overview(rows, results, transforms, out / 'sequence_overview.png', plt, np)
        thumbs = []
        for row in rows:
            with Image.open(out / row['case_id'] / 'result.png') as picture:
                thumbs.append(ImageOps.contain(picture.convert('RGB'), (1050, 680)))
        width = max(i.width for i in thumbs); height = max(i.height for i in thumbs)
        sheet = Image.new('RGB', (width * 2, height * math.ceil(len(thumbs) / 2)), 'white')
        for i, thumb in enumerate(thumbs): sheet.paste(thumb, ((i % 2) * width, (i // 2) * height))
        sheet.save(out / 'contactsheet.png')
        pages = [ImageOps.pad(i, (width, height), color='white') for i in thumbs]
        pages[0].save(out / 'sequence.gif', save_all=True, append_images=pages[1:], duration=1300, loop=0)
        pages[0].save(out / 'casebook.pdf', save_all=True, append_images=pages[1:], resolution=110.)
    summary = dict(experiment_id=plan.get('experiment_id'), ended=True,
                   unique_scenes=len({c['scene_token'] for c in plan['cases']}),
                   unique_observation_times=len(rows),
                   charged_requests=sum(r['charged_requests'] for r in rows),
                   conditional_chain_passes=sum(r['status'] in ('FIRST_ATTEMPT_PASS', 'REVISION_PASS') for r in rows),
                   not_attempted=sum(r['status'] == 'NOT_ATTEMPTED' for r in rows),
                   no_extra_model_calls=True, GT_used=False, claim=LIMITATION,
                   frames=rows, self_checks=checks)
    write_json_new(out / 'sequence_summary.json', summary)
    columns = ('case_id', 'sample_token', 'relative_sequence_time_s', 'status', 'charged_requests',
               'first_attempt_pass', 'revision_pass', 'final_failure_stage', 'source', 'selected_attempt')
    with (out / 'sequence_summary.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns + ('T4', 'TURN_TASK', 'prefix_consumed', 'displacement_m', 'end_speed_mps', 'plan_yaw_deg', 'prefix_yaw_deg'))
        writer.writeheader()
        for row in rows:
            item = {k: row.get(k) for k in columns}
            item.update(T4=row['stages']['T4'], TURN_TASK=row['stages']['TURN_TASK'],
                        **{k: row['physical'].get(k) for k in ('prefix_consumed', 'displacement_m', 'end_speed_mps')},
                        plan_yaw_deg=row['signed_turn']['plan_end_yaw_change_deg'],
                        prefix_yaw_deg=row['signed_turn']['prefix_yaw_change_deg'])
            writer.writerow(item)
    cards = ''.join(f'<section><h2>{html.escape(r["case_id"])} · {html.escape(r["status"])}</h2>'
                    f'<a href="{r["case_id"]}/result.json">Per-frame result JSON</a>'
                    f'<img src="{r["case_id"]}/result.png"></section>' for r in rows)
    document = ('<!doctype html><meta charset="utf-8"><title>Recorded turning sequence</title>'
                '<style>body{font:16px sans-serif;max-width:1400px;margin:auto;padding:20px}img{max-width:100%}section{margin:30px 0}</style>'
                '<h1>连续转弯场景：逐时刻结果</h1><p>' + html.escape(LIMITATION) + '</p>'
                '<p>GT未使用；方向与道路一致性需要独立阅图。灰色NOT_REACHED不计通过。</p>'
                '<p><a href="casebook.pdf">PDF</a> · <a href="sequence_summary.csv">CSV</a> · '
                '<a href="sequence_summary.json">完整JSON</a> · <a href="sequence.gif">GIF</a></p>'
                '<img src="sequence_overview.png">' + cards)
    write_text_new(out / 'index.html', document)
    print(json.dumps(dict(output=str(out), frame_count=len(rows), calls_added=0, self_checks=checks)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--self-check', action='store_true')
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check()))
    else:
        if args.plan is None or args.out is None:
            parser.error('--plan and --out required')
        main(args.plan, args.out)
