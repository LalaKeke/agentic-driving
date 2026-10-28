"""Acquisition-only additional temporal camera coverage; no annotation/map reads."""
from pathlib import Path
from datetime import datetime,timezone
import json,math,hashlib,sys,argparse
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.datasets.nuscenes_capture import Acquisition
from agentic.contracts import validate
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--samples',nargs='+',default=['ac452a60e8b34a7080c938c904b23057','ac8b4d49731d43289579f014cb7c97ef']);args=parser.parse_args()
root=ROOT;out=args.out.resolve()
if out.exists():raise SystemExit('Use a new output directory; original evidence is never overwritten')
(out/'runtime_inputs').mkdir(parents=True)
config=json.loads((root/'implementation/experiments/nuscenes/config.json').read_text());a=Acquisition(config['dataset_root']);original=json.loads((root/'implementation/experiments/nuscenes/conditional_estimation_config.json').read_text());selected=[{'sample_id':sid} for sid in args.samples];cases=[];cohort=[];audit=[]
for item in selected:
 cap,base_audit=a.export(item['sample_id'],config);m=cap['metadata'];ref=a.tables['sample_data'][base_audit['reference_sample_data_token']];selected_ids={f['id'] for f in cap['observations']}
 for channel in config['current_cameras']:
  frame=a.keyframes[(item['sample_id'],channel)]
  while frame and frame['timestamp']>ref['timestamp']:frame=a.tables['sample_data'].get(frame['prev'])
  rows=[f for f in a.history(frame,m['history_s']) if 0<=ref['timestamp']-f['timestamp']<=m['history_s']*1e6]
  for offset in [0,1,2]:
   f=min(rows,key=lambda r:abs(r['timestamp']-(ref['timestamp']-offset*1e6)));selected_ids.add(f['token'])
 frames=sorted([a.tables['sample_data'][x] for x in selected_ids],key=lambda f:(f['timestamp'],f['token']));observations=[];history=[];center,R,yaw=a.pose_center(ref,cap['vehicle']['center_to_rear_axle_m'])
 for f in frames:
  t=f['timestamp']*1e-6;time_id='nusc_us_'+str(f['timestamp']);channel=a.channels[f['calibrated_sensor_token']]
  observations.append(dict(id=f['token'],time_id=time_id,capture_time_s=t,capture_utc=datetime.fromtimestamp(t,timezone.utc).isoformat(),camera_id=channel,path=str(a.root/f['filename'])))
  pos,_,angle=a.pose_center(f,cap['vehicle']['center_to_rear_axle_m']);local=(pos-center)@R
  history.append(dict(frame_id=f['token'],time_id=time_id,center_xy_m=local.tolist(),yaw_rad=math.atan2(math.sin(angle-yaw),math.cos(angle-yaw))))
 cap['observations']=observations;cap['acquisition_metadata']['ego_history']=history;validate('capture_v2.schema.json',cap)
 p=out/'runtime_inputs'/(item['sample_id']+'.json');p.write_text(json.dumps(cap,ensure_ascii=False,indent=2)+'\n');hashes={f['id']:hashlib.sha256(Path(f['path']).read_bytes()).hexdigest() for f in observations};cohort.append(dict(sample_id=item['sample_id'],capture_path=str(p.relative_to(root)),capture_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),image_sha256=hashes));cases.append(dict(sample_id=item['sample_id'],capture='runtime_inputs/'+p.name));audit.append(dict(sample_id=item['sample_id'],original_image_count=10,new_image_count=len(observations),history_counts={c:sum(f['camera_id']==c for f in observations) for c in config['current_cameras']},source_table_allowlist=sorted(a.ALLOWED_TABLES),truth_access=False));print(audit[-1])
index=out/'runtime_index.json';index.write_text(json.dumps(dict(cases=cases),indent=2)+'\n');trial=dict(original);trial.update(experiment_id='multicamera_temporal_coverage_exploratory_v1',runtime_index=str(index.relative_to(root)),runtime_index_sha256=hashlib.sha256(index.read_bytes()).hexdigest(),cohort=cohort,input_export_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),scope='Separate input-coverage exploration: original forward history plus causal side/rear histories. Two preselected windows, not the original four-window success denominator. No GT/annotation/map inputs.');(out/'config.json').write_text(json.dumps(trial,ensure_ascii=False,indent=2)+'\n');(out/'acquisition_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
