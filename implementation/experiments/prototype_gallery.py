"""Plot saved single-window cases; no model calls, truth, or replanning."""
import argparse
import json
import math
import os
from pathlib import Path
import tempfile

from prototype_figure import (body_corners, check_new_destination, load_result,
                              occupancy_polygons, read, short_actor_name)


def render(cases_root, destination):
    cases_root = Path(cases_root)
    cases = [p for p in sorted(cases_root.iterdir())
             if p.is_dir() and ((p / 'runtime' / 'summary.json').is_file()
                                or (p / 'not_attempted.json').is_file())]
    if not cases:
        raise ValueError('No case/runtime/summary.json found; no figure written.')
    destination = check_new_destination(destination)
    with tempfile.TemporaryDirectory(prefix='driving-gallery-mpl-', dir='/tmp') as cache:
        os.environ['MPLCONFIGDIR'] = cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon
        from PIL import Image
        plt.rcParams.update({'font.size': 9})
        fig, axes = plt.subplots(len(cases), 2, figsize=(15, 5.2 * len(cases)),
                                 squeeze=False, gridspec_kw={'width_ratios': [1.1, 1]})
        rendered = []
        for row, case in enumerate(cases):
            attempted = (case / 'runtime' / 'summary.json').is_file()
            online_summary = read(case / 'runtime' / 'summary.json') if attempted else {}
            replay = (case / 'yaw_box_replay' / 'summary.json').is_file()
            chosen_run = case / ('yaw_box_replay' if replay else 'runtime')
            summary = read(chosen_run / 'summary.json') if replay else online_summary
            result, chosen = load_result(chosen_run)
            scene, output = result.get('scene', {}), result.get('agent_output', {})
            cap = scene.get('capture') or result.get('capture')
            if not cap and (case / 'capture.json').is_file():
                cap = read(case / 'capture.json')
            cap = cap or {}
            h = scene.get('hypotheses', output.get('hypotheses', {}))
            actors = output.get('hypotheses', h).get('objects', [])
            decision = scene.get('decision', output.get('decision', {}))
            points = result.get('trajectory', {}).get('points', [])
            verification, execution = result.get('verification', {}), result.get('execution', {})
            accepted = bool(summary.get('prototype_conditional_success'))
            consumed = bool(summary.get('prefix_consumed') and execution)
            passed = accepted and consumed and verification.get('verdict') == 'PASS'
            status, color = ('CONDITIONAL PASS', '#087d4a') if passed else ('INCOMPLETE / FAIL', '#a73535')
            if not attempted:
                status, color = 'NOT_ATTEMPTED / NO MODEL OUTPUT', '#64748b'
            ax_image, ax_bev = axes[row]
            fronts = [f for f in cap.get('observations', []) if f.get('camera_id') == 'CAM_FRONT']
            frame = max(fronts, key=lambda f: f['capture_time_s']) if fronts else None
            if frame and Path(frame['path']).is_file():
                with Image.open(frame['path']) as im:
                    ax_image.imshow(im)
            else:
                ax_image.text(.5, .5, 'Original front image unavailable', ha='center',
                              transform=ax_image.transAxes)
            ax_image.axis('off')
            sample = cap.get('metadata', {}).get('scene_id', 'unknown')
            ax_image.set_title(f"{case.name.replace('_', ' ')} | original front view\n{sample}", fontsize=11)
            road = h.get('road')
            if road:
                ax_bev.add_patch(Polygon(road['polygon_m'], fc='#e7edf3', ec='#64748b',
                                         lw=1.3, label='Model local road'))
            for index, cell in enumerate(result.get('corridor', {}).get('cells', [])):
                ax_bev.add_patch(Polygon(cell['polygon_m'], fc='#38abc8', ec='#38abc8',
                                         alpha=.08, lw=.7, label='T2 corridor' if index == 0 else None))
            for index, actor in enumerate(actors):
                c, seen = plt.get_cmap('tab10')(index % 10), set()
                for polygon in occupancy_polygons(actor):
                    key = json.dumps(polygon)
                    if key in seen:
                        continue
                    seen.add(key)
                    ax_bev.add_patch(Polygon(polygon, fc=c, ec=c, alpha=.15, lw=.8))
                corners = body_corners(actor)
                if corners:
                    ax_bev.add_patch(Polygon(corners, fc=c, ec=c, alpha=.8))
                if actor.get('position_m') is not None:
                    x, y = actor['position_m']
                    ax_bev.scatter(x, y, color=c, s=20,
                                   label=f"{index + 1}: {short_actor_name(actor['id'])}")
                    ax_bev.annotate(str(index + 1), (x, y), xytext=(4, 4),
                                    textcoords='offset points', color=c, weight='bold')
            vehicle = cap.get('vehicle', {})
            if vehicle:
                ax_bev.add_patch(Polygon([[-vehicle['rear_m'], -vehicle['half_width_m']],
                    [vehicle['front_m'], -vehicle['half_width_m']],
                    [vehicle['front_m'], vehicle['half_width_m']],
                    [-vehicle['rear_m'], vehicle['half_width_m']]], fc='#24344a', label='Initial ego BODY'))
            if points:
                ax_bev.plot([p['x_m'] for p in points], [p['y_m'] for p in points],
                            '-o' if passed else '--', color='#1755cf', lw=2, ms=3,
                            label='Verified T3 plan' if passed else 'UNRELEASED candidate')
                prefix = verification.get('released_prefix_s', 0) if consumed else 0
                pp = [p for p in points if p['time_s'] <= prefix + 1e-8]
                if passed and len(pp) >= 2:
                    ax_bev.plot([p['x_m'] for p in pp], [p['y_m'] for p in pp],
                                color='#13a66a', lw=5, label=f'Simulated {prefix:g}s prefix')
            else:
                ax_bev.text(.5, .83, 'NO TRAJECTORY', color='#a73535', ha='center',
                            transform=ax_bev.transAxes, bbox=dict(fc='white', ec='none', alpha=.85))
            d = execution.get('center_displacement_m')
            motion = (f"consumed {execution['elapsed_s']:g}s; displacement {math.hypot(*d):.3f}m; "
                      f"speed {execution['speed_mps']:.3f}m/s") if consumed and d else 'No simulated prefix consumed'
            provenance = 'OFFLINE REPLAN OF REAL MODEL; original online FAIL\n' if replay else ''
            ax_bev.set_title(provenance + f"{status} | T4 {verification.get('verdict', 'NOT_REACHED')} | "
                             f"{online_summary.get('charged_requests', 0)} real requests\n"
                             f"{decision.get('behavior', 'NOT_REACHED')} | {motion}", color=color, fontsize=10)
            ax_bev.set(xlabel='Fixed X forward (m)', ylabel='Fixed Y left (m)')
            ax_bev.set_aspect('equal', adjustable='datalim')
            ax_bev.autoscale_view(); ax_bev.grid(alpha=.2)
            if ax_bev.get_legend_handles_labels()[0]:
                ax_bev.legend(loc='best', fontsize=6.5, ncol=3, framealpha=.9)
            rendered.append(dict(case=case.name, attempt=str(chosen), conditional_pass=passed,
                                 prefix_consumed=consumed, offline_replan=replay,
                                 online_conditional_success=bool(online_summary.get('prototype_conditional_success')),
                                 real_requests=online_summary.get('charged_requests', 0), attempted=attempted))
        fig.suptitle('LOCAL CONDITIONAL 2S REAL MODEL SINGLE WINDOWS', fontsize=17, weight='bold')
        fig.text(.02, .012, 'Saved model assumptions, BODY occupancy, and actual simulated prefixes only. '
                 'No real vehicle control, G2 claim, or counterfactual future observations.', fontsize=8)
        fig.tight_layout(rect=(0, .025, 1, .965))
        destination.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(destination, dpi=160)
        plt.close(fig)
    print(json.dumps(dict(figure=str(destination), cases=rendered, new_model_calls=0)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases-root', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    render(args.cases_root, args.out)
