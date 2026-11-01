"""Export one continuous recorded clip and every legal 0.5s planning input.

Only the acquisition five-table exporter is used. Full front timeline poses
are DISPLAY ONLY; each runtime capture contains causal two-second evidence.
No model, annotations, map, future actor state, planner or executor is called.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agentic.contracts import need,validate
from agentic.datasets.nuscenes_capture import Acquisition,quaternion_matrix
from agentic.evidence_io import reserve_directory,write_json_new
from prepare_six_scene_scout import configuration
from prepare_long_clips import source_pins,WIRE,font

SCENE='fcbccedd61424f1b85dcbf8f897f9754'
OLD_PLAN=ROOT/'implementation/results/hard_clip_baseline_20261030_v1/urban_interaction/plan.json'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def preview(rows,path):
    from PIL import Image,ImageDraw,ImageOps
    columns,w,h=6,300,210
    page=Image.new('RGB',(columns*w,65+math.ceil(len(rows)/columns)*h),'white')
    draw=ImageDraw.Draw(page)
    draw.text((12,9),'scene-0103 | complete recorded clip / RAW INPUTS ONLY',font=font(22),fill='black')
    draw.text((12,37),'0-3: causal-history warm-up; 4-39: every keyframe registered for planning; no model or truth overlay',font=font(15),fill='black')
    for i,row in enumerate(rows):
        x=i%columns*w;y=65+i//columns*h
        with Image.open(row['image_path']) as im:tile=ImageOps.contain(im.convert('RGB'),(296,166))
        page.paste(tile,(x+(w-tile.width)//2,y))
        draw.text((x+4,y+169),f"raw{row['raw_index']:03d} | +{row['relative_sequence_time_s']:.3f}s",font=font(14),fill='black')
        v=row['ego_speed_mps'];text='WARMUP' if row['raw_index']<4 else f"P{row['raw_index']:03d} | ego {v:.3f}m/s"
        draw.text((x+4,y+189),text,font=font(14),fill='#666' if row['raw_index']<4 else '#176344')
    page.save(path,quality=93)

def prepare(out,dataset_root):
    root=reserve_directory(out)
    cfg=configuration();old=json.loads(OLD_PLAN.read_text())
    need(old['scene_token']==SCENE and len(old['raw_frames'])==40,'CONTINUOUS_CLIP','Frozen clip identity changed')
    need(cfg['history_s']==cfg['horizon_s']==2 and cfg['step_s']==.2
         and cfg['safety']=={'margin_m':1.,'prefix_s':.2},'CONTINUOUS_CONFIG','Keep original local prototype parameters')
    acq=Acquisition(dataset_root,'v1.0-mini')
    samples=sorted([s for s in acq.tables['sample'].values() if s['scene_token']==SCENE],
                   key=lambda s:acq.keyframes[(s['token'],'CAM_FRONT')]['timestamp'])
    need(len(samples)==40,'CONTINUOUS_CLIP','Expected all40 original keyframes')
    first=acq.keyframes[(samples[0]['token'],'CAM_FRONT')]
    last=acq.keyframes[(samples[-1]['token'],'CAM_FRONT')]
    first_stamp,last_stamp=first['timestamp'],last['timestamp']
    key_index={acq.keyframes[(s['token'],'CAM_FRONT')]['token']:i for i,s in enumerate(samples)}
    sample_ids={s['token'] for s in samples}
    # Follow the native camera linked list, rather than inventing/interpolating images.
    full=[];f=first;seen=set()
    while f and f['timestamp']<=last_stamp:
        need(f['token'] not in seen,'CONTINUOUS_LINK','Cycle in original camera stream');seen.add(f['token'])
        need(acq.channels[f['calibrated_sensor_token']]=='CAM_FRONT'
             and f['sample_token'] in sample_ids,'CONTINUOUS_LINK','Camera link crossed another scene/camera')
        image=acq.root/f['filename'];need(image.is_file(),'NUSCENES_IMAGE','Original continuous image missing',path=str(image))
        center,R,yaw=acq.pose_center(f,cfg['vehicle']['center_to_rear_axle_m'])
        cal=acq.tables['calibrated_sensor'][f['calibrated_sensor_token']]
        hist=acq.history(f,2);older=[q for q in hist if f['timestamp']-q['timestamp']>=100000]
        speed=None
        if older:
            q=min(older,key=lambda q:abs(f['timestamp']-q['timestamp']-500000))
            p,_,_=acq.pose_center(q,cfg['vehicle']['center_to_rear_axle_m'])
            speed=float(((center-p)@R)[0]/((f['timestamp']-q['timestamp'])*1e-6))
        pose=acq.tables['ego_pose'][f['ego_pose_token']]
        full.append(dict(front_frame_index=len(full),frame_id=f['token'],sample_token=f['sample_token'],
            is_key_frame=f['is_key_frame'],raw_keyframe_index=key_index.get(f['token']),
            capture_time_id='nusc_us_'+str(f['timestamp']),capture_time_s=f['timestamp']*1e-6,
            capture_timestamp_us=f['timestamp'],relative_sequence_time_s=(f['timestamp']-first_stamp)*1e-6,
            image_path=str(image),image_sha256=sha(image),ego_speed_mps=speed,
            display_only_ego_pose=dict(center_xy_world_m=center.tolist(),yaw_world_rad=yaw,
                planar_center_to_world_rotation=R.tolist(),center_offset_m=cfg['vehicle']['center_to_rear_axle_m'],
                native_ego_translation_world_m=pose['translation'],native_ego_rotation_wxyz=pose['rotation']),
            display_only_calibration=dict(camera_intrinsic=cal['camera_intrinsic'],
                camera_to_center_rotation=quaternion_matrix(cal['rotation']).tolist(),
                camera_to_center_translation_m=[cal['translation'][0]-cfg['vehicle']['center_to_rear_axle_m'],
                                                cal['translation'][1],cal['translation'][2]])))
        f=acq.tables['sample_data'].get(f['next'])
    need(full[0]['frame_id']==first['token'] and full[-1]['frame_id']==last['token'],
         'CONTINUOUS_LINK','Full native front stream does not reach both endpoints')
    stamps=[x['capture_timestamp_us'] for x in full];gaps=[(b-a)*1e-6 for a,b in zip(stamps,stamps[1:])]
    need(all(g>0 for g in gaps),'CONTINUOUS_TIME','Native front timestamps must strictly increase')
    need({x['raw_keyframe_index'] for x in full if x['is_key_frame']}==set(range(40)),
         'CONTINUOUS_COVERAGE','Some original keyframes are absent from the native front stream')
    rows=[];cases=[];inputs=reserve_directory(root/'inputs')
    for index,sample in enumerate(samples):
        f=acq.keyframes[(sample['token'],'CAM_FRONT')];prior=old['raw_frames'][index]
        need(sample['token']==prior['sample_token'] and f['token']==prior['frame_id'],
             'CONTINUOUS_IDENTITY','Keyframe sample identity changed')
        cap=audit=None
        if index>=4:
            cap,audit=acq.export(sample['token'],cfg);validate('capture_v2.schema.json',cap)
            counts=Counter(o['camera_id'] for o in cap['observations'])
            need(len(cap['observations'])==10 and counts['CAM_FRONT']==5 and len(counts)==6
                 and audit['history_span_s']>=1.9,'CONTINUOUS_HISTORY','Every registered input needs the original complete causal history')
            need(0<=cap['ego']['speed_mps']<=cfg['vehicle']['max_speed_mps'],
                 'CONTINUOUS_SPEED','A planning input is outside unchanged vehicle speed limits',raw_index=index)
            need(all(0<=cap['metadata']['reference_time_s']-o['capture_time_s']<=2.000001 for o in cap['observations']),
                 'CONTINUOUS_CAUSAL','Only actual current/past images belong to runtime')
            case_id=f'frame_{index:03d}';folder=reserve_directory(inputs/case_id)
            write_json_new(folder/'capture.json',cap)
            case=dict(case_id=case_id,frame_index=index,raw_index=index,planning_reference_index=len(cases),
                scene_name='scene-0103',scene_token=SCENE,sample_token=sample['token'],
                reference_time_s=cap['metadata']['reference_time_s'],relative_sequence_time_s=(f['timestamp']-first_stamp)*1e-6,
                ego_speed_mps=cap['ego']['speed_mps'],capture=str((folder/'capture.json').relative_to(ROOT)),
                capture_sha256=sha(folder/'capture.json'),image_sha256=audit['image_sha256'],
                require_turn=False,request_limit=2)
            write_json_new(folder/'input_manifest.json',dict(**case,audit=audit,image_count=10,
                model_calls=0,GT_or_map_read=False,full_display_timeline_not_in_capture=True))
            cases.append(case)
        rows.append(dict(prior,planning_case_id=f'frame_{index:03d}' if index>=4 else None,
            model_call_allowed=index>=4,role='PLANNING_REFERENCE' if index>=4 else 'WARMUP',
            ego_speed_mps=cap['ego']['speed_mps'] if cap else prior['ego_speed_mps']))
    need(len(cases)==36 and [x['raw_index'] for x in cases]==list(range(4,40)),
         'CONTINUOUS_REFERENCES','Do not omit or replace a registered keyframe')
    p_gaps=[b['reference_time_s']-a['reference_time_s'] for a,b in zip(cases,cases[1:])]
    timing=dict(full_camera_frame_count=len(full),raw_keyframe_count=40,planning_references=36,
        full_clip_duration_s=(last_stamp-first_stamp)*1e-6,first_planning_relative_s=cases[0]['relative_sequence_time_s'],
        camera_gap_min_s=min(gaps),camera_gap_median_s=statistics.median(gaps),camera_gap_max_s=max(gaps),
        planning_gap_min_s=min(p_gaps),planning_gap_median_s=statistics.median(p_gaps),planning_gap_max_s=max(p_gaps),
        camera_frames_above_point2s_gap=sum(g>.2 for g in gaps),all_planning_initial_speeds_legal=True,
        speed_min_mps=min(x['ego_speed_mps'] for x in cases),speed_max_mps=max(x['ego_speed_mps'] for x in cases))
    write_json_new(root/'cases.json',dict(scene_name='scene-0103',scene_token=SCENE,cases=cases,timing=timing,
        raw_keyframes=rows,model_calls=0,GT_or_map_read=False))
    write_json_new(root/'full_front_frames.json',dict(scene_name='scene-0103',scene_token=SCENE,timing=timing,
        frames=full,purpose='DISPLAY ONLY: raw native camera frames, actual acquisition poses/calibration; no generated/interpolated camera pixels',
        runtime_boundary='Do not include this full timeline or its later poses in model/tool inputs. Each capture.json contains causal two-second evidence only.',
        camera_interpolation=False,model_calls=0,GT_or_map_read=False))
    preview(rows,root/'input_preview.jpg')
    pins=source_pins(extra_paths=(Path(__file__),))
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    plan=dict(experiment_id='continuous_urban_clip_20261030_v1',clip_id='continuous_urban',
        scene_name='scene-0103',scene_token=SCENE,backend='openai_api',launched=False,
        execution_authorized=False,authorized_generating_requests=0,old_budget_inherited=0,
        requested_model='gpt-6.1-sol',prompt_version='driving_prototype_v3',
        experiment_profile='local_conditional_2s_prototype_v1',business_contract='3.0.0-exp2',
        coverage_profile='whole_interval_sources_v3_exp2',planner_profile='bounded_seed_candidates_v4',
        wire_bundle=WIRE,per_window_limit=2,cases=cases,configuration=cfg,timing=timing,
        source_commit=head,source_sha256=pins,prepared_utc=datetime.now(timezone.utc).isoformat(),
        generation_note='All36 references are registered. Parent may reuse saved real outputs; new-generation cases and independent budget must be separately registered before launch.',
        context_policy='Only this reference causal capture and same-reference feedback enter the model. Full timeline and display poses excluded.',
        recording_boundary='Independent recorded observations and conditional simulated0.2s prefixes. No later raw image is a counterfactual observation after a new trajectory.')
    write_json_new(root/'plan_unlaunched.json',plan)
    write_json_new(root/'source.json',dict(head=head,preparation_script_sha256=sha(__file__),
        frozen_raw_plan_sha256=sha(OLD_PLAN),source_sha256=pins,accessed_tables=sorted(acq.accessed),
        table_allowlist=sorted(Acquisition.ALLOWED_TABLES),timing=timing,new_model_calls=0,new_planning_calls=0,
        prior_evidence_modified=False,worktree_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True))))
    return dict(out=str(root),**timing,new_model_calls=0,GT_reads=0)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--dataset-root',default='/data/nuscenes_dataset');a=p.parse_args()
    print(json.dumps(prepare(a.out.resolve(),a.dataset_root),ensure_ascii=False))
