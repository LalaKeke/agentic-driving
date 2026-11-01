"""Small post-hoc annotation comparison, completely separate from runtime.

Prepare official SDK image projections, review identities, then evaluate a fixed
review file. No model adapter or planning tool is imported. Annotation surrogate
coverage is not measured continuous-time truth or a safety validation.
"""
import argparse
from bisect import bisect_right
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

os.environ.setdefault('MPLCONFIGDIR', tempfile.mkdtemp(prefix='critical-actor-mpl-'))
import numpy as np
from pyquaternion import Quaternion
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'implementation'))
from agentic.datasets.nuscenes_capture import Acquisition

BASE = ROOT/'implementation/results/varied_prototypes_2s_20261028_v1'
CASES = {'parking_bend': (2, ['right_gray_mercedes', 'left_gray_nissan']),
         'construction_yield': (3, ['lead_minivan', 'construction_barrier'])}


def read(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p, value):
    with Path(p).open('x') as f:
        f.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def prepare(out, dataset):
    from PIL import Image, ImageDraw
    from nuscenes.nuscenes import NuScenes
    from nuscenes.utils.geometry_utils import view_points
    out.mkdir(parents=True, exist_ok=False)
    nusc = NuScenes(version='v1.0-mini', dataroot=str(dataset), verbose=False)
    rows = []
    for case, (attempt, ids) in CASES.items():
        rp = BASE/case/f'runtime/attempt_{attempt:02d}/record.json'
        record = read(rp); cap = record['capture']
        objects = {o['id']: o for o in record['output']['hypotheses']['objects']}
        frames = {f['id']: f for f in cap['observations']}
        seen = set()
        for oid in ids:
            for fid in objects[oid]['frame_ids']:
                if fid in seen: continue
                seen.add(fid); f = frames[fid]
                path, boxes, intr = nusc.get_sample_data(fid)
                im = Image.open(path).convert('RGB'); draw = ImageDraw.Draw(im)
                visible = []
                for i, box in enumerate(sorted(boxes, key=lambda b: b.token)):
                    xyz = box.corners()
                    if not np.all(xyz[2] > .1): continue
                    uv = view_points(xyz, np.asarray(intr), normalize=True)[:2]
                    if uv[0].max()<0 or uv[0].min()>im.width or uv[1].max()<0 or uv[1].min()>im.height: continue
                    ann=nusc.get('sample_annotation',box.token); label=i+1
                    for a,b in [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]:
                        draw.line([tuple(uv[:,a]),tuple(uv[:,b])],fill='yellow',width=3)
                    x=max(0,min(im.width-90,uv[0].mean()));y=max(0,min(im.height-24,uv[1].min()-24))
                    draw.rectangle([x,y,x+80,y+22],fill='black');draw.text((x+2,y+2),str(label),fill='yellow')
                    visible.append(dict(label=label,instance_token=ann['instance_token'],annotation_token=ann['token'],
                        category=box.name,pixel_bounds=[float(uv[0].min()),float(uv[1].min()),float(uv[0].max()),float(uv[1].max())]))
                dest=out/f'{case}_{f["camera_id"]}.jpg'
                im.save(dest,quality=92)
                rows.append(dict(case=case,camera=f['camera_id'],frame_id=fid,image_sha256=sha(path),
                    projection_path=str(dest.relative_to(out)),visible=visible))
    write(out/'projection_index.json',rows)


def evaluate(out, review, dataset):
    out.mkdir(parents=True, exist_ok=False)
    acq=Acquisition(dataset,'v1.0-mini'); meta=Path(dataset)/'v1.0-mini'
    anns=read(meta/'sample_annotation.json'); tracks={}
    for a in anns: tracks.setdefault(a['instance_token'],[]).append(a)
    stamp=lambda a:acq.tables['sample'][a['sample_token']]['timestamp']*1e-6
    for t in tracks.values(): t.sort(key=stamp)
    associations=read(review); rows=[]

    def state(track,t):
        times=[stamp(a) for a in track]
        if len(times)<2 or t<times[0] or t>times[-1]:return None
        i=min(max(0,bisect_right(times,t)-1),len(times)-2)
        a,b=track[i:i+2];dt=times[i+1]-times[i]
        if not 0<dt<=.75:return None
        u=(t-times[i])/dt
        ya=Quaternion(a['rotation']).yaw_pitch_roll[0]
        yb=Quaternion(b['rotation']).yaw_pitch_roll[0]
        delta=math.atan2(math.sin(yb-ya),math.cos(yb-ya))
        return dict(position=(1-u)*np.asarray(a['translation'])+u*np.asarray(b['translation']),
                    size=(1-u)*np.asarray(a['size'])+u*np.asarray(b['size']),
                    planar_yaw=ya+u*delta,
                    rotation=Quaternion.slerp(Quaternion(a['rotation']),Quaternion(b['rotation']),amount=u))

    def oriented_surrogate(track,knots,center,R,ego_yaw):
        """Conservative sweep for the explicit annotation interpolation proxy."""
        segments=[]
        for left,right in zip(knots,knots[1:]):
            cuts=np.linspace(left,right,max(1,math.ceil((right-left)/.02))+1)
            for a,b in zip(cuts,cuts[1:]):
                sa,sb=state(track,float(a)),state(track,float(b))
                pa,pb=(sa['position'][:2]-center)@R,(sb['position'][:2]-center)@R
                wa,la=sa['size'][:2];wb,lb=sb['size'][:2]
                w,l=max(wa,wb),max(la,lb);radius=math.hypot(w,l)/2
                corners=[]
                for s,p in [(sa,pa),(sb,pb)]:
                    yaw=s['planar_yaw']-ego_yaw;c,z=math.cos(yaw),math.sin(yaw)
                    corners += [(p[0]+c*x-z*y,p[1]+z*x+c*y) for x,y in [(-l/2,-w/2),(l/2,-w/2),(l/2,w/2),(-l/2,w/2)]]
                yaw_step=abs(math.atan2(math.sin(sb['planar_yaw']-sa['planar_yaw']),math.cos(sb['planar_yaw']-sa['planar_yaw'])))
                pad=(float(np.linalg.norm(pb-pa))+radius*yaw_step)/2+1e-10
                # Circumscribe the buffer polygon, so Shapely's finite circle
                # tessellation cannot silently reduce this surrogate margin.
                segments.append(MultiPoint(corners).convex_hull.buffer(pad/math.cos(math.pi/256),resolution=64))
        return unary_union(segments)

    inputs=[Path(__file__),Path(review),meta/'sample_annotation.json',meta/'sample.json']
    for case,(attempt,ids) in CASES.items():
        path=BASE/case/f'runtime/attempt_{attempt:02d}/record.json';inputs.append(path)
        r=read(path);cap=r['capture'];t=cap['metadata']['reference_time_s']
        ref=next(
            acq.tables['sample_data'][f['id']] for f in cap['observations'] if f['time_id']==cap['metadata']['reference_time_id'])
        center,R,ego_yaw=acq.pose_center(ref,cap['vehicle']['center_to_rear_axle_m'])
        objects={o['id']:o for o in r['output']['hypotheses']['objects']}
        for oid in ids:
            o=objects[oid];a=associations[case][oid];instance=a.get('instance_token');track=tracks.get(instance)
            row=dict(case=case,object_id=oid,instance_token=instance,match_status=a['status'],match_reason=a['reason'],
                model_position_m=o['position_m'],model_body=o['body_geometry'],position_missing=o['position_m'] is None,
                occupancy_missing=o['occupancy'] is None,center_error_m=None,length_error_m=None,width_error_m=None,
                yaw_error_rad=None,future_surrogate_coverage=None,future_area_inflation=None,
                planning_use='Read separately recorded T1–T4/prefix results; GT comparison never authorizes planning',
                annotation_comparison_status='UNMATCHED_NO_SCORE',future_intervals=[])
            if track is None:rows.append(row);continue
            s=state(track,t)
            if s is None:row['annotation_comparison_status']='REFERENCE_NOT_BRACKETED';rows.append(row);continue
            gt=(s['position'][:2]-center)@R;mat=s['rotation'].rotation_matrix
            yaw=math.atan2(mat[1,0],mat[0,0])-ego_yaw
            dyaw=math.atan2(math.sin(o['body_geometry']['yaw_rad']-yaw),math.cos(o['body_geometry']['yaw_rad']-yaw))
            row.update(annotation_comparison_status='BRACKETED_ANNOTATION_PROXY',gt_position_m=gt.tolist(),
                gt_length_m=float(s['size'][1]),gt_width_m=float(s['size'][0]),gt_yaw_rad=math.atan2(math.sin(yaw),math.cos(yaw)),
                center_error_m=float(np.linalg.norm(np.asarray(o['position_m'])-gt)),
                length_error_m=abs(o['body_geometry']['length_m']-float(s['size'][1])),
                width_error_m=abs(o['body_geometry']['width_m']-float(s['size'][0])),yaw_error_rad=abs(dyaw))
            # Each 0.2s GT surrogate conservatively contains linearly interpolated
            # annotation centers plus the maximum box circumscribed radius.
            for k in range(10):
                left,right=k*.2,(k+1)*.2
                knots=[t+left]+[stamp(x) for x in track if t+left<stamp(x)<t+right]+[t+right]
                ss=[state(track,x) for x in knots]
                if any(x is None for x in ss):
                    row['future_intervals'].append(dict(start_s=left,end_s=right,status='GT_NOT_BRACKETED'));continue
                xy=np.asarray([(x['position'][:2]-center)@R for x in ss]);radius=max(np.linalg.norm(x['size'][:2])/2 for x in ss)
                surrogate=MultiPoint(xy).convex_hull.buffer(float(radius),resolution=64)
                oriented=oriented_surrogate(track,knots,center,R,ego_yaw)
                sources=[x for x in o['occupancy'] if x['start_s']<=right and x['end_s']>=left]
                declared=unary_union([Polygon(z['polygon_m']) for x in sources for z in x['alternatives']])
                # Contract 3.0-exp2 declares uncertainty_m as the TOTAL radius;
                # alignment and source residuals are components, never re-added.
                residual=o['uncertainty_m']
                expanded=declared.buffer(residual,resolution=64)
                row['future_intervals'].append(dict(start_s=left,end_s=right,status='ANNOTATION_SURROGATE_ONLY',
                    raw_coverage=declared.intersection(surrogate).area/surrogate.area,
                    raw_area_inflation=declared.area/surrogate.area,
                    expanded_coverage=expanded.intersection(surrogate).area/surrogate.area,
                    expanded_area_inflation=expanded.area/surrogate.area,total_model_residual_m=residual,
                    oriented_raw_coverage=declared.intersection(oriented).area/oriented.area,
                    oriented_raw_area_inflation=declared.area/oriented.area,
                    oriented_expanded_coverage=expanded.intersection(oriented).area/oriented.area,
                    oriented_expanded_area_inflation=expanded.area/oriented.area))
            valid=[x for x in row['future_intervals'] if x['status']=='ANNOTATION_SURROGATE_ONLY']
            if len(valid)==10:
                row['future_surrogate_coverage']=sum(x['expanded_coverage'] for x in valid)/10
                row['future_area_inflation']=sum(x['expanded_area_inflation'] for x in valid)/10
                row['oriented_surrogate_coverage']=sum(x['oriented_expanded_coverage'] for x in valid)/10
                row['oriented_surrogate_area_inflation']=sum(x['oriented_expanded_area_inflation'] for x in valid)/10
            rows.append(row)
    summary=dict(unique_windows=len(CASES),selected_objects=len(rows),matched=sum(r['instance_token'] is not None for r in rows),
        unmatched=sum(r['instance_token'] is None for r in rows),position_missing=sum(r['position_missing'] for r in rows),
        occupancy_missing=sum(r['occupancy_missing'] for r in rows),scored_current=sum(r['center_error_m'] is not None for r in rows),
        online_model_calls=0,truth_runtime_feedback=False,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256=sha(__file__),inputs_sha256={str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p):sha(p) for p in inputs},
        assumptions=['Non-blind post-hoc assistant visual association; model numerical values and historical truth were already exposed.',
            'Body geometric center ground XY, absolute reference axes, configured ego rear-to-center offset 1.2m held fixed.',
            'Linear annotation-center/size interpolation with quaternion slerp; <=0.75s bracketing gap; annotation proxy, not exact instantaneous truth.',
            'Future coverage uses annotation-linear-center circumscribed-body surrogate; model scenarios unioned without dropping adverse modes.',
            'Additional oriented-body surrogate uses annotation-linear center/dimensions and shortest planar yaw interpolation; <=0.02s conservative swept subsegments. This is an interpolation assumption, not actual measured continuous motion.',
            'Expanded scores apply the original total object uncertainty_m once; source/alignment components are not re-added. No 1m ego safety margin in prediction evaluation.',
            'High coverage of a large envelope is not accurate prediction, planning success, calibrated uncertainty or safety.'],rows=rows)
    write(out/'report.json',summary)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--dataset',type=Path,default=Path('/data/nuscenes_dataset'))
    p.add_argument('--review',type=Path);a=p.parse_args()
    if a.review:evaluate(a.out,a.review,a.dataset)
    else:prepare(a.out,a.dataset)
