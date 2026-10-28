"""One recorded single-window result figure. No model calls, GT or replanning.

Reads a round_result.json or latest attempt's record/T1/T2/T3/T4/execution files.
Image markers are display-only flat-z=0 projections of model metric positions,
not detector boxes, depth measurements or a source of planning geometry.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic.evidence_io import check_new_destination


def read(path):
    return json.loads(Path(path).read_text())


def load_result(path):
    path = Path(path)
    if path.is_file():
        return read(path), path.parent
    for parent in [path] + sorted(path.glob('attempt_*'), reverse=True):
        if (parent / 'round_result.json').is_file():
            return read(parent / 'round_result.json'), parent
    attempts = [p for p in path.glob('attempt_*') if (p / 'record.json').exists()]
    parent = sorted(attempts)[-1] if attempts else path
    record = read(parent / 'record.json') if (parent / 'record.json').exists() else {}
    value = dict(agent_output=record.get('output', {}), provenance=record.get('provenance', {}),
                 capture=record.get('capture', {}))
    for name, key in [('T1', 'scene'), ('T2', 'corridor'), ('T3', 'trajectory'),
                      ('T4', 'verification'), ('execution', 'execution'),
                      ('execution_receipt', 'execution_receipt')]:
        candidate = parent / (name + '.json')
        if candidate.exists():
            value[key] = read(candidate)
    if not value['capture'] and (parent / 'capture.json').exists():
        value['capture'] = read(parent / 'capture.json')
    return value, parent


def occupancy_polygons(actor):
    for source in actor.get('occupancy') or []:
        for vertices in source.get('alternatives_m', []):
            yield vertices
        for alternative in source.get('alternatives', []):
            yield alternative['polygon_m']


def body_corners(actor):
    state, body = actor.get('position_m'), actor.get('body_geometry')
    if state is None or body is None:
        return None
    x, y = state
    c, s = math.cos(body['yaw_rad']), math.sin(body['yaw_rad'])
    return [[x + c*dx-s*dy, y+s*dx+c*dy] for dx, dy in
            [(-body['length_m']/2, -body['width_m']/2),
             (body['length_m']/2, -body['width_m']/2),
             (body['length_m']/2, body['width_m']/2),
             (-body['length_m']/2, body['width_m']/2)]]


def render(run, destination):
    destination = check_new_destination(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result, chosen = load_result(run)
    scene = result.get('scene', {})
    cap = scene.get('capture', result.get('capture', {}))
    output = result.get('agent_output', {})
    h = scene.get('hypotheses', output.get('hypotheses', {}))
    native_h = output.get('hypotheses', h)
    decision = scene.get('decision', output.get('decision', {}))
    trajectory, corridor = result.get('trajectory', {}), result.get('corridor', {})
    verification, execution = result.get('verification', {}), result.get('execution', {})
    source = result.get('provenance', scene.get('provenance', {}))
    points = trajectory.get('points', [])
    with tempfile.TemporaryDirectory(prefix='driving-prototype-mpl-') as cache:
        os.environ['MPLCONFIGDIR'] = cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as Patch
        import numpy as np
        from PIL import Image
        # The SDK helper performs projection only; no dataset/annotation object.
        try:
            from nuscenes.utils.geometry_utils import view_points
        except ImportError:
            view_points = None
        plt.rcParams.update({'font.size': 10, 'axes.grid': False})
        fig, axes = plt.subplots(2, 2, figsize=(16, 10.4),
                                 gridspec_kw={'height_ratios': [1.05, .85]})
        ax_image, ax_bev, ax_speed, ax_text = axes.flat
        observations = cap.get('observations', [])
        fronts = [f for f in observations if f.get('camera_id') == 'CAM_FRONT']
        frame = max(fronts, key=lambda x: x['capture_time_s']) if fronts else None
        image_size = None
        if frame and Path(frame['path']).is_file():
            original = Image.open(frame['path'])
            image_size = original.size
            ax_image.imshow(original)
            ax_image.set_xlim(0, image_size[0]); ax_image.set_ylim(image_size[1], 0)
        else:
            ax_image.text(.5, .5, 'Original front image unavailable', ha='center',
                          transform=ax_image.transAxes)
        ax_image.axis('off')
        ax_image.set_title('Original camera + model object locations (display projection)')
        calibration = cap.get('acquisition_metadata', {}).get('calibrations', {}).get('CAM_FRONT')
        can_project = (image_size and calibration and frame['time_id'] == cap.get('metadata', {}).get('reference_time_id'))

        def project(xy):
            if not can_project:
                return None
            xyz = np.column_stack((np.asarray(xy, dtype=float), np.zeros(len(xy)))).T
            rotation = np.asarray(calibration['camera_to_center_rotation'])
            camera = rotation.T @ (xyz - np.asarray(calibration['camera_to_center_translation_m'])[:, None])
            intrinsic = np.asarray(calibration['camera_intrinsic'])
            uv = view_points(camera, intrinsic, normalize=True) if view_points else intrinsic @ camera
            if view_points is None:
                uv = uv / uv[2:3]
            mask = (camera[2] > .2) & (uv[0] >= 0) & (uv[0] < image_size[0]) & (uv[1] >= 0) & (uv[1] < image_size[1])
            return uv[:2].T, mask

        road = h.get('road')
        if road:
            ax_bev.add_patch(Patch(road['polygon_m'], fc='#e8edf1', ec='#64748b', lw=1.5, label='Model local road'))
        if decision.get('target_region'):
            ax_bev.add_patch(Patch(decision['target_region'], fc='none', ec='#999', ls='--', lw=1, label='Allowed CENTER region'))
        for index, cell in enumerate(corridor.get('cells', [])):
            ax_bev.add_patch(Patch(cell['polygon_m'], fc='#59c0db', ec='#3395ae', alpha=.07,
                                  lw=.7, label='T2 rear-axle corridor' if index == 0 else None))
        palette = plt.get_cmap('tab10')
        actors = native_h.get('objects', [])
        for index, actor in enumerate(actors):
            color = palette(index % 10)
            polygons = list(occupancy_polygons(actor))
            # Repeated legacy grid cells need not darken the same set 10 times.
            seen = set()
            for polygon in polygons:
                key = json.dumps(polygon)
                if key in seen:
                    continue
                seen.add(key)
                ax_bev.add_patch(Patch(polygon, fc=color, ec=color, alpha=.14, lw=.9,
                                      label=f"{actor['id']} future BODY" if len(seen) == 1 else None))
            corners = body_corners(actor)
            if corners:
                ax_bev.add_patch(Patch(corners, fc=color, ec=color, alpha=.8))
            if actor.get('position_m') is not None:
                x, y = actor['position_m']
                ax_bev.scatter(x, y, c=[color], s=35)
                ax_bev.annotate(actor['id'], (x, y), xytext=(4, 6), textcoords='offset points', fontsize=8, color=color)
                projected = project([[x, y]])
                if projected is not None and projected[1][0]:
                    uv = projected[0][0]
                    ax_image.scatter(*uv, c=[color], s=55, ec='white', lw=.8)
                    ax_image.annotate(actor['id'], uv, xytext=(5, -12), textcoords='offset points',
                                      color='white', fontsize=9,
                                      bbox=dict(fc=color, alpha=.9, boxstyle='round,pad=.2'))
        vehicle = cap.get('vehicle', {})
        if vehicle:
            ax_bev.add_patch(Patch([[-vehicle['rear_m'], -vehicle['half_width_m']],
                                   [vehicle['front_m'], -vehicle['half_width_m']],
                                   [vehicle['front_m'], vehicle['half_width_m']],
                                   [-vehicle['rear_m'], vehicle['half_width_m']]],
                                  fc='#23334a', ec='#23334a', label='Initial ego BODY'))
        prefix = verification.get('released_prefix_s', 0) if verification.get('verdict') == 'PASS' else 0
        if points:
            xy = np.asarray([[p['x_m'], p['y_m']] for p in points])
            ax_bev.plot(xy[:, 0], xy[:, 1], '-o', color='#1755cf', lw=2.5, ms=3, label='T3 trajectory')
            prefix_points = [p for p in points if p['time_s'] <= prefix+1e-8]
            if prefix > 0 and len(prefix_points) >= 2:
                ax_bev.plot([p['x_m'] for p in prefix_points], [p['y_m'] for p in prefix_points],
                            color='#18a66a', lw=6, label=f'Released {prefix:.1f}s prefix')
            projected = project(xy.tolist())
            if projected is not None:
                uv, mask = projected
                for k in range(len(uv)-1):
                    if mask[k] and mask[k+1]:
                        ax_image.plot(uv[k:k+2, 0], uv[k:k+2, 1], c='#00ff9a', lw=3)
            ax_speed.plot([p['time_s'] for p in points], [p['speed_mps'] for p in points],
                          '-o', c='#1755cf', ms=4, label='Planned speed')
        else:
            ax_bev.text(.5, .95, 'NO VERIFIED TRAJECTORY', color='#b33', ha='center', transform=ax_bev.transAxes)
            ax_speed.text(.5, .5, 'Trajectory stage not completed', ha='center', transform=ax_speed.transAxes)
        if prefix:
            ax_speed.axvspan(0, prefix, fc='#18a66a', alpha=.18, label='Released prefix')
        if execution:
            ax_speed.scatter([execution['elapsed_s']], [execution['speed_mps']], c='#18a66a', s=65, zorder=5,
                             label='Simulated consumed state')
        ax_speed.set(xlabel='Relative planning time (s)', ylabel='Speed (m/s)', title='Speed and simulated prefix')
        ax_speed.grid(alpha=.2)
        if ax_speed.get_legend_handles_labels()[0]:
            ax_speed.legend(fontsize=8)
        ax_bev.set(xlabel='Fixed reference X forward (m)', ylabel='Fixed reference Y left (m)',
                   title='Model scene / T2 corridor / T3 trajectory')
        ax_bev.set_aspect('equal', adjustable='datalim'); ax_bev.autoscale_view(); ax_bev.grid(alpha=.2)
        if ax_bev.get_legend_handles_labels()[0]:
            ax_bev.legend(loc='upper right', fontsize=7)
        ax_text.axis('off')
        real = source.get('source') == 'online_model'
        label = 'REAL IMAGES / ONLINE MODEL / SINGLE WINDOW' if real else 'AUTHORED OR OFFLINE SOFTWARE RESULT'
        displacement = execution.get('center_displacement_m')
        lines = [label, f"Scene: {cap.get('metadata', {}).get('scene_id', 'unknown')}",
                 f"Requested/CLI alias: {source.get('model_id', source.get('requested_model', 'unknown'))} | source: {source.get('source', 'unknown')}",
                 f"Behavior: {decision.get('behavior', 'unknown')} | desired speed: {decision.get('desired_speed_mps', 'unknown')} m/s",
                 f"Horizon: {cap.get('metadata', {}).get('horizon_s', '?')}s | grid: {cap.get('metadata', {}).get('step_s', '?')}s",
                 f"T4: {verification.get('verdict', 'NOT_REACHED')} | solver: {trajectory.get('solver', {}).get('status', 'NOT_REACHED')}",
                 f"Simulated prefix: {execution.get('elapsed_s', 'NOT_CONSUMED')} s",
                 f"Displacement XY: ({displacement[0]:.3f}, {displacement[1]:.3f}) m" if displacement is not None else 'Displacement XY: NOT_REACHED',
                 f"Displacement norm: {math.hypot(*displacement):.3f} m" if displacement is not None else '',
                 f"Speed after consumption: {execution['speed_mps']:.3f} m/s" if execution else 'Speed after consumption: NOT_REACHED',
                 '', 'Conditional on declared local model scene and motion premises.',
                 'No real vehicle control; no counterfactual multi-round observations.']
        note = native_h.get('confidence_note', '')
        if note:
            lines += ['', 'Model note:'] + textwrap.wrap(note, width=82)[:5]
        ax_text.text(.01, .98, '\n'.join(lines), va='top', fontsize=9.5, linespacing=1.45, transform=ax_text.transAxes)
        fig.suptitle(f'{label} — T4 {verification.get("verdict", "NOT_REACHED")}', fontsize=17, weight='bold')
        fig.text(.015, .015, 'Image markers/green line: deterministic model-XY projection under display-only z=0 assumption, not detector/GT. '
                 'BEV uses saved metric hypotheses; no future nuScenes ego poses are plotted.', fontsize=8)
        fig.tight_layout(rect=(0, .04, 1, .95))
        fig.savefig(destination, dpi=170)
        plt.close(fig)
    print(json.dumps(dict(figure=str(destination), recorded_attempt=str(chosen),
        real_model_source=real, verification=verification.get('verdict', 'NOT_REACHED'),
        prefix_consumed=bool(execution), no_model_calls=True, no_GT=True)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    render(args.run, args.out)
