"""Case-specific zero-call diagnosis of the frozen construction T2 failure.

The analysis loads the delivered planner from Git, so later planner edits do not
change this comparison. No scene values, occupancies or safety settings change.
Run with the project Python; plot the resulting JSON using --plot with a Python
that already has matplotlib installed.
"""
import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[2]
BASELINE = 'd1e44b6d891d92ad731485128549f68afd37cdf0'
CASE = ROOT / 'implementation/results/varied_prototypes_2s_20261028_v1/construction_yield'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analyse(out):
    sys.path.insert(0, str(ROOT / 'implementation'))
    from shapely.geometry import Polygon, box
    from shapely.ops import nearest_points
    from agentic.contracts import grid
    from agentic.geometry import planes, polygon, obstacles, rear_state

    source = subprocess.check_output(['git', 'show', BASELINE + ':implementation/agentic/t2.py'], cwd=ROOT)
    frozen = types.ModuleType('agentic._frozen_construction_t2')
    frozen.__package__ = 'agentic'
    exec(compile(source, 'git:' + BASELINE + ':t2.py', 'exec'), frozen.__dict__)

    def body(q, vehicle):
        d = vehicle['center_to_rear_axle_m']; w = vehicle['half_width_m']
        c, s = math.cos(q[2]), math.sin(q[2])
        return Polygon([(q[0] + c*x-s*y, q[1]+s*x+c*y)
                        for x,y in ((d-vehicle['rear_m'],-w), (d+vehicle['front_m'],-w),
                                    (d+vehicle['front_m'],w), (d-vehicle['rear_m'],w))])

    def intersection(rows):
        domain = box(-1000, -1000, 1000, 1000)
        for row in rows:
            domain = frozen.clip(domain, row['plane'])
        return domain

    def coordinates(shape):
        if shape.is_empty or shape.area <= 1e-12:
            return []
        return [list(p) for p in shape.exterior.coords]

    reports = []
    for attempt in ('02', '03'):
        record_path = CASE / f'runtime/attempt_{attempt}/record.json'
        scene_path = CASE / f'runtime/attempt_{attempt}/T1.json'
        record = json.loads(record_path.read_text())
        scene = json.loads(scene_path.read_text())
        cap = scene['capture']; vehicle = cap['vehicle']; decision = scene['decision']
        road = polygon(scene['hypotheses']['road']['polygon_m'])
        road_u = scene['hypotheses']['road']['uncertainty_m']
        margin = cap['safety']['margin_m']; times = grid(scene['metadata'])
        seed = frozen._moving_seed(scene, times)
        initial_body = body(rear_state(cap['ego'], vehicle), vehicle)
        actors = []
        for obj in record['output']['hypotheses']['objects']:
            g = obj['body_geometry']; x, y = obj['position_m']; yaw = g['yaw_rad']
            c,s = math.cos(yaw), math.sin(yaw)
            points = [[x+c*a-s*b, y+s*a+c*b]
                      for a,b in ((-g['length_m']/2,-g['width_m']/2),
                                  (g['length_m']/2,-g['width_m']/2),
                                  (g['length_m']/2,g['width_m']/2),
                                  (-g['length_m']/2,g['width_m']/2))]
            actors.append(dict(id=obj['id'], native_body_polygon_m=points,
                               position_m=obj['position_m'], body_geometry=g,
                               total_residual_m=obj['uncertainty_m'],
                               alignment_residual_m=obj['alignment']['residual_m'],
                               sources=obj['occupancy']))
        cells = []
        for k, (left, right) in enumerate(zip(times, times[1:])):
            a,b = seed[k:k+2]
            qa = [a['rear_x_m'],a['rear_y_m'],a['yaw_rad'],a['speed_mps']]
            qb = [b['rear_x_m'],b['rear_y_m'],b['yaw_rad'],b['speed_mps']]
            selection = body(qa,vehicle).union(body(qb,vehicle)).convex_hull
            rows = []
            for i,(nx,ny,limit) in enumerate(planes(road)):
                support = frozen.yaw_box_support(vehicle,nx,ny)
                rows.append(dict(label=f'road:{i}', kind='road', normal=[nx,ny],
                                 raw_limit_m=limit, ego_support_m=support,
                                 model_residual_m=road_u, safety_margin_m=0,
                                 plane=[nx,ny,limit-support-road_u]))
            road_allowed = intersection(rows)
            for i,(nx,ny,limit) in enumerate(planes(polygon(decision['target_region']))):
                support = frozen.yaw_point_support((vehicle['center_to_rear_axle_m'],0),nx,ny)
                rows.append(dict(label=f'target:{i}', kind='target_center', normal=[nx,ny],
                                 raw_limit_m=limit, ego_support_m=support,
                                 model_residual_m=0, safety_margin_m=0,
                                 plane=[nx,ny,limit-support]))
            if decision['stop']:
                limit=decision['stop']['center_x_max_m']; support=vehicle['center_to_rear_axle_m']
                rows.append(dict(label='stop',kind='stop',normal=[1,0],raw_limit_m=limit,
                                 ego_support_m=support,model_residual_m=0,safety_margin_m=0,
                                 plane=[1,0,limit-support]))
            road_target = intersection(rows)
            occupied = []
            for j,(oid,shape,u) in enumerate(obstacles(scene,k)):
                origin,near = nearest_points(selection,shape.convex_hull)
                dx,dy = near.x-origin.x,near.y-origin.y
                distance = math.hypot(dx,dy)
                if distance == 0:
                    raise RuntimeError('Unexpected zero seed distance in frozen case')
                nx,ny = dx/distance,dy/distance
                limit = min(nx*x+ny*y for x,y in shape.convex_hull.exterior.coords)
                support = frozen.yaw_box_support(vehicle,nx,ny)
                rows.append(dict(label=f'{oid}:{j}',kind='actor',object_id=oid,
                                 normal=[nx,ny],raw_limit_m=limit,ego_support_m=support,
                                 model_residual_m=u,safety_margin_m=margin,
                                 plane=[nx,ny,limit-support-u-margin]))
                occupied.append(dict(object_id=oid,alternative_index=j,
                                     polygon_m=coordinates(shape),total_residual_m=u,
                                     initial_actual_body_clearance_minus_residual_m=initial_body.distance(shape)-u,
                                     seed_end_actual_body_clearance_minus_residual_m=body(qb,vehicle).distance(shape)-u))
            domain=intersection(rows)
            witness=[]
            if domain.is_empty:
                for n in (2,3):
                    witness=next((list(group) for group in itertools.combinations(rows,n)
                                  if intersection(group).is_empty),[])
                    if witness:break
            # Same separating normals, but only yaw=0: diagnosis only, not an
            # executable new planner or permission to relax a heading contract.
            zero_yaw=[]
            for row in rows:
                row=dict(row); nx,ny=row['normal']
                if row['kind'] in ('road','actor'):
                    support=frozen.yaw_box_support(vehicle,nx,ny,0)
                elif row['kind']=='target_center':
                    support=frozen.yaw_point_support((vehicle['center_to_rear_axle_m'],0),nx,ny,0)
                else:support=row['ego_support_m']
                row['plane']=[nx,ny,row['raw_limit_m']-support-row['model_residual_m']-row['safety_margin_m']]
                zero_yaw.append(row)
            cells.append(dict(interval_s=[left,right],constraints=rows,
                              road_allowed_rear_polygon_m=coordinates(road_allowed),
                              road_target_stop_rear_polygon_m=coordinates(road_target),
                              final_rear_polygon_m=coordinates(domain),final_area_m2=domain.area,
                              minimal_empty_witness=witness,
                              fixed_zero_yaw_intersection_area_m2=intersection(zero_yaw).area,
                              seed_rear_segment_m=[qa[:2],qb[:2]],
                              seed_body_start_m=coordinates(body(qa,vehicle)),
                              seed_body_end_m=coordinates(body(qb,vehicle)),
                              occupancies=occupied))
        first=next(c for c in cells if c['final_area_m2']<=1e-8)
        initial_clearance=min(x['initial_actual_body_clearance_minus_residual_m'] for x in cells[0]['occupancies'])
        reports.append(dict(attempt=attempt,record_path=str(record_path.relative_to(ROOT)),
                            record_sha256=sha(record_path),scene_path=str(scene_path.relative_to(ROOT)),
                            scene_sha256=sha(scene_path),scene_id=scene['metadata']['scene_id'],
                            vehicle=vehicle,ego=cap['ego'],road=scene['hypotheses']['road'],
                            decision=decision,actors=actors,seed_points=seed,
                            margin_m=margin,heading_range_rad=[-.35,.35],
                            first_empty_interval_s=first['interval_s'],
                            initial_actual_body_clearance_minus_residual_m=initial_clearance,
                            initial_actual_body_satisfies_margin=initial_clearance>=margin,
                            minimum_continuous_straight_braking_distance_m=cap['ego']['speed_mps']**2/(2*vehicle['max_brake_mps2']),
                            source_front_image=next(x['path'] for x in reversed(cap['observations']) if x['camera_id']=='CAM_FRONT'),
                            cells=cells))
    result=dict(kind='CASE_SPECIFIC_OFFLINE_DIAGNOSIS_NO_MODEL_CALL',baseline_commit=BASELINE,
                frozen_t2_source_sha256=hashlib.sha256(source).hexdigest(),
                analysis_script_sha256=sha(__file__),new_model_calls=0,
                limitation='Fixed-output conditional geometry; neither a global feasibility proof nor GT accuracy evaluation.',
                attempts=reports)
    out.mkdir(parents=True,exist_ok=False)
    (out/'diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({r['attempt']:dict(first_empty_interval_s=r['first_empty_interval_s'],
                                      initial_body_clearance_m=r['initial_actual_body_clearance_minus_residual_m']) for r in reports}))


def plot(path):
    os.environ.setdefault('MPLCONFIGDIR',tempfile.mkdtemp(prefix='construction-plot-'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as Patch
    import numpy as np
    from PIL import Image
    data=json.loads(path.read_text()); report=data['attempts'][1]
    cell=next(c for c in report['cells'] if c['interval_s']==report['first_empty_interval_s'])
    fig,axes=plt.subplots(2,2,figsize=(18,12),layout='constrained')
    fig.suptitle('Construction: why the frozen T2 corridor is empty\nReal model output replay | original scene and 1 m margin unchanged',fontsize=17,fontweight='bold')
    def poly(ax,coords,**kw):
        if coords:ax.add_patch(Patch(coords,closed=True,**kw))
    image=axes[0,0];image.imshow(Image.open(report['source_front_image']));image.axis('off')
    image.set_title('Original CAM_FRONT; model identifies van, barrier and pedestrians',fontsize=12)
    ax=axes[0,1]
    poly(ax,report['road']['polygon_m'],facecolor='#dceee9',edgecolor='#3a826b',alpha=.8)
    colors=['#236db4','#ca8d19','#bc5c54','#922889']
    for actor,color in zip(report['actors'],colors):
        occ=[o for o in cell['occupancies'] if o['object_id']==actor['id']]
        for item in occ:poly(ax,item['polygon_m'],facecolor=color,edgecolor=color,alpha=.12)
        poly(ax,actor['native_body_polygon_m'],facecolor=color,edgecolor=color,alpha=.8)
        x,y=actor['position_m'];ax.annotate(actor['id'],(x,y),xytext=(x+1,y+1),fontsize=9,color=color)
    poly(ax,cell['seed_body_start_m'],facecolor='#207249',alpha=.75)
    seed=report['seed_points'];ax.plot([p['rear_x_m'] for p in seed],[p['rear_y_m'] for p in seed],'k.-',label='Max-brake zero-steer seed (rear axle)')
    ax.set(xlim=(-5,28),ylim=(-12,18),aspect='equal',xlabel='Reference X [m]',ylabel='Reference Y [m]',title='Raw model BODY (solid) and full interval occupancies (faint)')
    ax.legend(loc='upper left',fontsize=8);ax.grid(alpha=.2)
    ax=axes[1,0]
    poly(ax,report['road']['polygon_m'],facecolor='#eef4ef',edgecolor='#3a826b',alpha=.8)
    poly(ax,cell['road_allowed_rear_polygon_m'],facecolor='#7caed0',edgecolor='#2871a6',alpha=.35,label='Road after ego yaw support + 0.7 m residual')
    poly(ax,cell['road_target_stop_rear_polygon_m'],facecolor='#73b981',edgecolor='#27883b',alpha=.5,label='Also CENTER target + stop bound')
    for p in (cell['seed_body_start_m'],cell['seed_body_end_m']):poly(ax,p,facecolor='none',edgecolor='#1c5b37',linewidth=1.6)
    ends=np.array(cell['seed_rear_segment_m']);ax.plot(ends[:,0],ends[:,1],'ko-',label='Seed rear points')
    for i,row in enumerate(cell['minimal_empty_witness']):
        nx,ny,limit=row['plane']; x=np.linspace(-5,7,200)
        if abs(ny)>1e-9:
            y=(limit-nx*x)/ny;ax.plot(x,y,'--',lw=2,label=row['label'])
        else:ax.axvline(limit/nx,ls='--',lw=2,label=row['label'])
    ax.set(xlim=(-5,7),ylim=(-3.2,5.4),aspect='equal',xlabel='Rear reference X [m]',ylabel='Rear reference Y [m]',title='Earliest empty cell [0.2, 0.4] s: three incompatible half-planes')
    ax.legend(loc='upper left',fontsize=8);ax.grid(alpha=.2)
    text=axes[1,1];text.axis('off')
    witness=cell['minimal_empty_witness'];ped=next(r for r in witness if r['kind']=='actor')
    summary=(
        'EMPTY-INTERSECTION EVIDENCE (attempt 03)\n\n'
        'Road bottom:  y_rear >= -0.2544 m\n'
        '  raw edge -3 m; ego yaw support 2.0456 m; residual 0.7 m\n'
        f"Pedestrian side: {ped['normal'][0]:.5f} x + {ped['normal'][1]:.5f} y <= {ped['plane'][2]:.4f}\n"
        f"  raw projection {ped['raw_limit_m']:.4f} m\n"
        f"  minus ego support {ped['ego_support_m']:.4f} m\n"
        '  minus total model residual 1.3 m, minus safety margin 1 m\n'
        'Road slanted edge supplies the third incompatible plane.\n\n'
        f"With yaw fixed at zero (diagnosis only), this cell has\n{cell['fixed_zero_yaw_intersection_area_m2']:.3f} m2 of intersection.\n"
        'Thus fixed +/-0.35 rad adds an avoidable early rejection.\n\n'
        'BUT the same straight braking seed has only 0.10 m\n'
        'clearance minus residual from 1.0 s onward (< required 1 m).\n'
        'A narrower band alone does not fix the full plan.\n\n'
        'Attempt 02 is different: initial actual BODY clearance\n'
        'minus residual is 0.5439 m < 1 m. Retain its rejection.\n\n'
        'Stop deadline alone is feasible: best straight braking\n'
        f"distance {report['minimum_continuous_straight_braking_distance_m']:.3f} m < stop-center limit 4.7 m.\n"
        'Collision/road/dynamics joint feasibility is not established.'
    )
    text.text(0,.98,summary,ha='left',va='top',fontsize=10.3,linespacing=1.4,family='monospace')
    target=path.parent/'construction_empty_corridor.png'
    if target.exists():raise SystemExit('Refusing to overwrite diagnostic figure')
    fig.savefig(target,dpi=170);plt.close(fig);print(target)


if __name__=='__main__':
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--out',type=Path);group.add_argument('--plot',type=Path)
    args=parser.parse_args()
    if args.out:analyse(args.out)
    else:plot(args.plot)
