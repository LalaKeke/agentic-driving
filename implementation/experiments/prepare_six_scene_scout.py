"""Prepare the fixed six-scene scout without importing or calling any model client.

Selection labels are human-facing only. Runtime inputs use the unchanged
acquisition exporter, navigation intent, vehicle and safety configuration.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.datasets.nuscenes_capture import Acquisition
from agentic.contracts import validate
from agentic.evidence_io import reserve_directory,write_json_new

BASELINE='4336dca2572f6afa3d139c2387621ac70f72597e'
TEMPLATE=ROOT/'implementation/results/moving_prototype_2s_20261028_v1/capture.json'
WIRE=ROOT/'implementation/results/exp2_wire_enum_day_pilot_20261028_v1/registration/wire_bundle'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def configuration():
    cap=json.loads(TEMPLATE.read_text())
    return dict(history_s=2.,horizon_s=2.,step_s=.2,
        current_cameras=['CAM_FRONT','CAM_FRONT_LEFT','CAM_FRONT_RIGHT','CAM_BACK','CAM_BACK_LEFT','CAM_BACK_RIGHT'],
        vehicle=cap['vehicle'],safety=cap['safety'],navigation_intent=cap['navigation_intent'],
        vehicle_parameters_source=cap['acquisition_metadata']['vehicle_parameters_source'])

def source_pins():
    paths=[ROOT/'implementation/prompts/driving_prototype_v1.txt',
           ROOT/'implementation/schemas/agent_output_v3_exp2.schema.json',
           WIRE/'wire_schema.json',WIRE/'transformation_manifest.json',
           ROOT/'implementation/experiments/run_local_prototype.py']
    paths += [ROOT/('implementation/agentic/'+name+'.py') for name in
              ('t1','t2','t3','t4','candidate_search','acceptance','geometry','runtime','metric_contract','pilot_transport','contracts')]
    paths += [ROOT/'implementation/agentic/datasets/nuscenes_capture.py']
    paths += [ROOT/('implementation/schemas/'+name+'.schema.json') for name in
              ('capture_v2','scene_v2','corridor_yaw_box_v1','trajectory_yaw_box_v1','corridor_seed_candidates_v1','trajectory_seed_candidates_v1')]
    pins={str(p.relative_to(ROOT)):sha(p) for p in paths}
    for name,expected in pins.items():
        original=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT)
        if hashlib.sha256(original).hexdigest()!=expected:
            raise SystemExit('Frozen baseline source differs: '+name)
    return pins

def preview(capture,path,title):
    from PIL import Image,ImageDraw,ImageFont
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',15)
    front=[o for o in capture['observations'] if o['camera_id']=='CAM_FRONT']
    other=[o for o in capture['observations'] if o['camera_id']!='CAM_FRONT']
    canvas=Image.new('RGB',(1500,454),'white');draw=ImageDraw.Draw(canvas)
    draw.text((12,8),title,font=font,fill='black')
    draw.text((12,31),'Actual input images; no model output / GT / projected geometry',font=font,fill='black')
    ref=capture['metadata']['reference_time_s']
    for index,item in enumerate(front+other):
        x=(index%5)*300;y=62+(index//5)*194
        with Image.open(item['path']) as original:
            tile=original.convert('RGB');tile.thumbnail((296,165))
            canvas.paste(tile,(x+(296-tile.width)//2,y))
        draw.text((x+4,y+167),item['camera_id']+f" {item['capture_time_s']-ref:+.3f}s",font=font,fill='black')
    canvas.save(path)

def prepare(selection_path,out,dataset_root):
    selection=json.loads(selection_path.read_text());main=selection['main_cases'];held=selection['held_out_cases']
    if len(main)!=6 or len(held)!=2:raise SystemExit('Exactly six main and two held-out windows are required')
    rows=main+held
    if len({r['scene_token'] for r in rows})!=8:raise SystemExit('Use eight distinct dataset scene tokens, not window IDs')
    cfg=configuration();pins=source_pins();acq=Acquisition(dataset_root)
    root=reserve_directory(out);exported=[]
    for group,cases in [('main',main),('held_out',held)]:
        for case in cases:
            sample=acq.tables['sample'][case['sample_token']]
            if sample['scene_token']!=case['scene_token']:raise SystemExit('Scene/sample mismatch: '+case['case_id'])
            cap,audit=acq.export(case['sample_token'],cfg)
            validate('capture_v2.schema.json',cap)
            ref=cap['metadata']['reference_time_s'];counts=Counter(o['camera_id'] for o in cap['observations'])
            if len(cap['observations'])!=10 or counts['CAM_FRONT']!=5 or len(counts)!=6:
                raise SystemExit('Input organization differs: '+case['case_id'])
            if any(counts[c]!=1 for c in counts if c!='CAM_FRONT'):raise SystemExit('Non-front camera count differs')
            if not all(0<=ref-o['capture_time_s']<=2.000001 for o in cap['observations']):
                raise SystemExit('Non-causal or out-of-history input')
            if not 0<=cap['ego']['speed_mps']<=cfg['vehicle']['max_speed_mps']:
                raise SystemExit('Initial speed outside fixed vehicle bounds')
            if min(o['capture_time_s'] for o in cap['observations'] if o['camera_id']=='CAM_FRONT')>ref-1.9:
                raise SystemExit('Insufficient actual front history span')
            folder=reserve_directory(root/group/case['case_id'])
            write_json_new(folder/'capture.json',cap)
            manifest=dict(case_id=case['case_id'],group=group,scene_token=case['scene_token'],scene_name=case['scene_name'],
                sample_token=case['sample_token'],runtime_window_id=cap['metadata']['scene_id'],
                capture_sha256=sha(folder/'capture.json'),image_count=len(cap['observations']),
                history_span_s=audit['history_span_s'],reference_time_id=cap['metadata']['reference_time_id'],
                ego_speed_mps=cap['ego']['speed_mps'],image_sha256=audit['image_sha256'],
                images=[dict(**o,sha256=audit['image_sha256'][o['id']]) for o in cap['observations']],
                runtime_tables=audit['accessed_tables'],model_calls=0,gt_in_runtime=False,
                note='Scene descriptions and selection labels are excluded from capture and model instructions.')
            write_json_new(folder/'input_manifest.json',manifest)
            preview(cap,folder/'input_preview.jpg',case['case_id']+' | '+case['scene_name']+' | '+case.get('preview_label',case['label']))
            exported.append(dict(case_id=case['case_id'],group=group,scene_token=case['scene_token'],scene_name=case['scene_name'],
                sample_token=case['sample_token'],capture=str((folder/'capture.json').relative_to(root)),
                capture_sha256=manifest['capture_sha256'],image_count=10,ego_speed_mps=cap['ego']['speed_mps'],
                preview=str((folder/'input_preview.jpg').relative_to(root)),
                status='NOT_ATTEMPTED' if group=='main' else 'HELD_OUT_NOT_LAUNCHED'))
    from PIL import Image,ImageDraw,ImageFont
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',24)
    canvas=Image.new('RGB',(1600,1590),'white');draw=ImageDraw.Draw(canvas)
    for index,case in enumerate(main):
        row=next(x for x in exported if x['case_id']==case['case_id'])
        cap=json.loads((root/row['capture']).read_text());obs=next(o for o in cap['observations'] if o['time_id']==cap['metadata']['reference_time_id'])
        x=(index%2)*800;y=(index//2)*530
        draw.text((x+8,y+8),case['case_id']+' '+case.get('preview_label',case['label']),font=font,fill='black')
        draw.text((x+8,y+40),case['scene_name']+f" | v={cap['ego']['speed_mps']:.3f}m/s | NOT RUN",font=font,fill='black')
        with Image.open(obs['path']) as source:
            tile=source.convert('RGB');tile.thumbnail((790,445));canvas.paste(tile,(x+4,y+80))
    canvas.save(root/'six_scene_input_preview.jpg')
    write_json_new(root/'preparation.json',dict(status='PREPARED_NOT_LAUNCHED',baseline_commit=BASELINE,
        preparation_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        preparation_script_sha256=sha(__file__),selection_sha256=sha(selection_path),
        generation_requests=0,quota_probes=0,source_pins=pins,configuration=cfg,
        main_unique_scenes=6,held_out_unique_scenes=2,main_images=60,held_out_images=20,
        accessed_runtime_tables=acq.accessed,rows=exported,
        statement='Only acquisition tables and raw past/current images are exported. No model client is imported; GT is reserved for separate later offline evaluation.'))
    return dict(out=str(root),main_windows=6,reserved_windows=2,generation_requests=0)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--selection',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path);parser.add_argument('--dataset-root',default='/data/nuscenes_dataset')
    args=parser.parse_args();print(json.dumps(prepare(args.selection,args.out,args.dataset_root),ensure_ascii=False))
