"""Route actual official CorDriver returns to the two-window slow system; no collision labels in routing."""
import argparse
import csv
import fcntl
import gzip
import hashlib
import json
import os
import pickle
import sys
import time
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from cordriver_paths import TOOLS
from cordriver_validate import data_path, sha256
from cordriver_reachable_options import load, save

HERE = Path(__file__).resolve().parent
INFO = TOOLS / 'cordriver_assets/corridor_nuscenes_infos_temporal_val.pkl'
DATA = Path('/data/nuscenes_dataset')


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.partial')
    with tmp.open('w') as f:
        json.dump(value, f, allow_nan=False, separators=(',', ':'))
    os.replace(tmp, path)


def index():
    with INFO.open('rb') as f:
        entries = pickle.load(f)['infos']
    keys = ('token','timestamp','cams','ego2global_translation','ego2global_rotation',
            'lidar2ego_translation','lidar2ego_rotation')
    return {r['token']:{k:r[k] for k in keys} for r in sorted(entries,key=lambda v:v['timestamp'])
            if r['fut_valid_flag']}


def register(root, infos):
    release = load(HERE.parent / 'environment/assets.json')['cordriver']
    if sha256(INFO) != release['assets'][INFO.name]['sha256'] or len(infos) != 5119:
        raise ValueError('Unexpected release index')
    root.mkdir(parents=True, exist_ok=True)
    contract = dict(stage='paper_pair_1', official_commit=release['commit'], assets=release['assets'],
        info_valid_tokens=list(infos), producer='shared01', model='gpt-6.2-astra', windows_s=[1.5,3],
        native='same official test.py forward, final ego_fut_mpc, including its model fallback',
        routing='intervention_v2.consider_guidance on the actual native output and primary predicted perception',
        geometry='only the official selected six predicted rectangles, primary perception and learned pre-QP reference',
        navigation='benchmark command selected by CorDriver; chart from pre-QP prediction, no additional route or map',
        observations='six official keyframe views and calibration, reported exposure offsets; latest causal CAN',
        fallback='no trigger, unavailable observed state, KEEP, or no eligible guided plan: return exact native trajectory',
        software_or_service_failure='stop affected worker, keep ledger and do not score unresolved frames',
        budget='one request per triggered evaluable token, zero retries, max 5119 for entire official validation',
        sources={p.name:sha256(p) for p in HERE.glob('cordriver_*.py')},
        scorer='release tools/test.py, plan_L2, plan_obj_box_col, plan_obj_box_map_intersect; full valid split')
    save(root/'registration.json',contract)


def wait_for(path, producer, timeout=600):
    start = time.monotonic()
    while not path.exists():
        if (producer/'official_complete.json').exists() or time.monotonic()-start > timeout:
            raise RuntimeError(f'Missing upstream frame: {path}')
        time.sleep(1)


def unpack(root, token):
    path = root/'shared01/frontend'/(token+'.json.gz')
    wait_for(path,root/'shared01')
    with gzip.open(path,'rt') as f:
        frame=json.load(f)
    if frame['sample']['token']!=token or frame['state']['token']!=token:
        raise ValueError('Invalid official frame binding')
    return frame,sha256(path)


def returned(root,t,sample,source,digest,path=None, candidate=None):
    from cordriver_published_xy import published_xy
    xy=sample['final_xy_lidar_m']
    record=dict(token=t,source=source,shared_sha256=digest)
    if path:
        record['plan_result']=str(path.relative_to(root))
        record['plan_result_sha256']=sha256(path)
    if candidate:
        proof=load(candidate.with_name('receipt.json'))
        if not proof['eligible_before_fixed_clearance'] or sha256(candidate)!=proof['sha256']:
            raise ValueError('Unbound guided trajectory')
        xy=published_xy(candidate).tolist()
        record.update(candidate=str(candidate.relative_to(root)),candidate_sha256=proof['sha256'])
    record['returned_xy_lidar_m']=xy
    atomic_json(root/'returns'/(t+'.json'),record)


def screen(root, infos):
    for t in infos:
        if (root/'routing'/(t+'.json')).exists(): continue
        frame,digest=unpack(root,t)
        gate=frame['gate']
        if not gate['consider_guidance']:
            returned(root,t,frame['sample'],'NO_TRIGGER_NATIVE',digest)
        atomic_json(root/'routing'/(t+'.json'),dict(token=t,trigger=gate['consider_guidance'],
                   reasons=gate['reasons'],shared_sha256=digest))
    atomic_json(root/'routing_complete.json',dict(total=len(infos)))


@lru_cache(maxsize=2)
def can_stream(scene):
    from cordriver_causal_frontend import read_messages
    path=DATA/'can_bus'/(scene+'_pose.json')
    return read_messages(path) if path.exists() else ([],[])


def prepare_case(root,t,info,frame,digest):
    from cordriver_astra_packet import CHANNELS, se3, camera_transform, reference
    from cordriver_nav_route import BRANCHES, body_frame
    from cordriver_causal_frontend import latest
    from cordriver_fast_review import review_packet
    from cordriver_edge_gate import native_body_knots
    case_root=root/'cases'/t
    study=case_root/'study'
    sample=frame['sample'].copy()
    scene=sample['scene_name']
    ts=info['timestamp']
    if ts != sample['timestamp_us']: raise ValueError('Timestamp mismatch')
    msg=latest(can_stream(scene),ts)
    poses,_=native_body_knots(sample,frame['state'])
    centers,_=reference(sample['qp_reference_steps'],sample['can_bus'][13])
    distinct=np.r_[True,np.linalg.norm(np.diff(centers,axis=0),axis=1)>1e-8]
    chart=centers[distinct]
    if msg is None or poses is None or len(chart)<2:
        reason='NO_CAUSAL_CAN' if msg is None else ('NO_BOUND_BODY' if poses is None else 'DEGENERATE_REFERENCE_CHART')
        returned(root,t,sample,reason+'_NATIVE',digest)
        return
    # Preserve the exact native QP vehicle input; the wire uses the actual time-stamped CAN separately.
    sample['can_pose_timestamp_us']=int(msg['utime'])
    registration={'name':scene,'token':t,'reference_time_us':int(ts)}
    atomic_json(study/'selection/registration.json',{'cases':[registration]})
    src=study/'frontend'/t/'sample.json'
    atomic_json(src,sample)
    atomic_json(src.with_name('visual_map.json'),frame['visual'])
    front=study/'frontend/frontend.json'
    atomic_json(front,dict(qp_weight=frame['qp_weight'],samples=[sample],shared_sha256=digest))
    atomic_json(study/'native_states.json',dict(frontend_sha256=sha256(front),samples=[frame['state']]))
    second=study/'secondary_primary/sources.json'
    atomic_json(second,dict(cases=[dict(token=t,admitted=False,reason='SAME_PUBLISHED_FRONTEND_ONLY')]))
    images,cameras=[],{}
    linear,origin=body_frame(info)
    for ch in CHANNELS:
        c=info['cams'][ch]
        img=Path(data_path(c['data_path'],DATA))
        actual=se3(info['lidar2ego_rotation'],info['lidar2ego_translation'])
        mat,extrinsic,_=camera_transform(info,c,actual)
        with Image.open(img) as im: w,h=im.size
        cameras[ch]=dict(width_px=w,height_px=h,exposure_us=int(c['timestamp']),
                         delta_from_reference_us=int(c['timestamp']-ts),sample_data_token=c['sample_data_token'],
                         K_original_px=np.asarray(c['cam_intrinsic']).tolist(),T_exposure_ego_from_camera=extrinsic.tolist(),
                         T_camera_from_reference_ego=mat.tolist())
        images.append(dict(label=ch+' original RGB',path=str(img),sha256=sha256(img)))
    nav=dict(command=BRANCHES[sample['navigation_command_index']],source='Official benchmark high-level branch only.',
             polyline_source='official_pre_qp',polyline_guidance_m=chart.tolist(),
             role='Magenta geometry is the native learned pre-QP body-center prediction. No map navigation polyline supplied.')
    ctx=dict(sample_token=t,reference_time_us=int(ts),guidance_frame='Fixed initial nominal CorDriver body center: x forward, y left, metres; positive yaw left.',
             navigation=nav,initial_speed_mps=msg['vel'][0],measured_ego_state=dict(timestamp_us=msg['utime'],
             delta_from_reference_us=int(msg['utime']-ts),velocity_xyz_mps=msg['vel'],accel_xyz_raw_mps2=msg['accel'],
             rotation_rate_xyz_radps=msg['rotation_rate'],source='Last available CAN pose at or before reference.'),
             cameras=cameras,sensor_scope='Official six camera keyframes, asynchronous exposures with individual signed offsets.',
             overlay_geometry=dict(display_height_reference_ego_z_m=0.,xy_guidance_to_reference_ego_linear=linear.tolist(),
                                   xy_guidance_origin_in_reference_ego_m=origin.tolist()))
    _,ctx['trajectory']=reference(sample['qp_reference_steps'],sample['can_bus'][13])
    packet=study/'packets'/t/'packet.json'
    atomic_json(packet,dict(token=t,model_context=ctx,images=images))
    baseline=case_root/'native.json'
    atomic_json(baseline,dict(policy='fast',token=t,native_sha256=sha256(src),packet_sha256=sha256(packet),
                map_sha256=sha256(src.with_name('visual_map.json')),secondary_sources_sha256=sha256(second),
                secondary_input_sha256=None,candidate=None,returned_xy_lidar_m=sample['final_xy_lidar_m']))
    return review_packet(study,t,baseline,case_root/'review')


def process(root, infos, mode):
    from cordriver_guidance_wire import build, obtain, MODEL
    for t,info in infos.items():
        if (root/'returns'/(t+'.json')).exists(): continue
        frame,digest=unpack(root,t)
        if not frame['gate']['consider_guidance']: continue
        case=root/'cases'/t
        packet=case/'review/packet.json'
        if not packet.exists():
            packet=prepare_case(root,t,info,frame,digest)
            if packet is None: continue
        manifest=case/'offer.json'
        if not manifest.exists():
            binding,wire=build(packet)
            if 'gt_ego_fut_trajs' in json.loads(wire)['input'][0]['content'][0]['text']:
                raise ValueError('Unexpected evaluation field')
            atomic_json(manifest,dict(token=t,model=MODEL,body_sha256=binding['body_sha256'],
                        shared_sha256=digest,images=binding['images']))
        if mode=='offer':
            print(t,manifest,flush=True)
            return
        ledger=root/'astra'
        receipt=ledger/t/'receipt.json'
        if (ledger/t).exists():
            if not receipt.exists() or load(receipt)['status']!='COMPLETE':
                raise RuntimeError('Prior unresolved dispatch; batch stopped: '+t)
            _,answer=obtain(packet,ledger,None,False)
        else:
            _,answer=obtain(packet,None,ledger,True)
        if answer['decision']=='KEEP':
            returned(root,t,frame['sample'],'ASTRA_KEEP_NATIVE',digest)
        else:
            from cordriver_pipeline import prepare_planning_inputs
            from cordriver_smooth_planner import run_case
            study=case/'study'
            spec=load(study/'selection/registration.json')['cases'][0]
            work=case/'solve'
            work.mkdir(exist_ok=True)
            inputs=work/'inputs'
            if not inputs.exists():
                prepare_planning_inputs(SimpleNamespace(study=study,data=DATA),inputs,spec,frame['qp_weight'])
            ans=run_case((inputs,ledger,work,MODEL,spec,DATA,frame['qp_weight'],True,False,False),
                         guidance_packet=packet,fast_return=case/'native.json',guided_search='minimal')
            if ans['status']=='COMPUTATION_ERROR': raise RuntimeError(str(ans))
            p=Path(ans['selected_path']) if ans['selected_path'] else None
            returned(root,t,frame['sample'],'ASTRA_ADJUST' if p else 'ASTRA_NO_RELEASE_NATIVE',digest,
                     work/'planning/astra'/t/'result.json',p)
        print(t,answer['decision'],flush=True)
    atomic_json(root/'slow_complete.json',dict(processed_split=len(infos)))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=['register','screen','offer','live'])
    p.add_argument('--root',type=Path,required=True)
    a=p.parse_args()
    root=a.root.resolve()
    infos=index()
    if a.stage=='register':
        register(root,infos)
        return
    reg=load(root/'registration.json')
    if list(infos)!=reg['info_valid_tokens'] or any(sha256(HERE/name)!=digest for name,digest in reg['sources'].items()):
        raise ValueError('Changed frozen source/split')
    with (root/(('dispatch' if a.stage in ('offer','live') else a.stage)+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.stage=='screen': screen(root,infos)
        else: process(root,infos,a.stage)


if __name__=='__main__': main()
