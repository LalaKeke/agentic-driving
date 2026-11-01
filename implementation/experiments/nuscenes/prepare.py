"""Offline case preparation. This process may read truth; runtime export cannot.

No outputs from annotations/maps are supplied to OnlineAgent or the four tools.
"""
import argparse
from bisect import bisect_right
import hashlib
import json
import math
from pathlib import Path
import sys
import numpy as np
from shapely.geometry import Polygon,box,mapping
from shapely.ops import unary_union
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from agentic.datasets.nuscenes_capture import Acquisition,quaternion_matrix,planar_rotation
from agentic.contracts import need,digest


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


class OfflineTruth:
    def __init__(self,acquisition):
        self.acq=acquisition;self.root=acquisition.root;self.path=self.root/acquisition.version
        self.rows={}
        for name in ['sample_annotation','instance','category','scene','log','visibility']:
            self.rows[name]={r['token']:r for r in json.loads((self.path/(name+'.json')).read_text())}
        self.annotations_by_sample={};self.tracks={};self.maps={}
        for a in self.rows['sample_annotation'].values():
            self.annotations_by_sample.setdefault(a['sample_token'],[]).append(a)
            self.tracks.setdefault(a['instance_token'],[]).append(a)
        for values in self.tracks.values():values.sort(key=lambda x:self.stamp(x))
    def stamp(self,a):return self.acq.tables['sample'][a['sample_token']]['timestamp']*1e-6
    def state(self,track,t):
        times=[self.stamp(a) for a in track]
        if t<times[0] or t>times[-1]:return None
        i=min(max(0,bisect_right(times,t)-1),len(track)-2)
        if i<0:return None
        a,b=track[i:i+2];dt=times[i+1]-times[i]
        if not 0<dt<=.75:return None
        u=(t-times[i])/dt
        pos=(1-u)*np.asarray(a['translation'][:2])+u*np.asarray(b['translation'][:2])
        vel=(np.asarray(b['translation'][:2])-a['translation'][:2])/dt
        radius=max(math.hypot(*a['size'][:2]),math.hypot(*b['size'][:2]))/2
        return pos,vel,radius
    def road(self,location,center,R,roi):
        if location not in self.maps:
            raw=json.loads((self.root/'maps'/'expansion'/(location+'.json')).read_text())
            nodes={n['token']:(n['x'],n['y']) for n in raw['node']};polys={p['token']:p for p in raw['polygon']}
            selected={p for d in raw['drivable_area'] for p in d['polygon_tokens']};shapes=[];invalid=0
            for token in selected:
                p=polys[token];shape=Polygon([nodes[x] for x in p['exterior_node_tokens']],[[nodes[x] for x in h['node_tokens']] for h in p['holes']])
                if shape.is_valid:shapes.append(shape)
                else:invalid+=1
            self.maps[location]=(shapes,invalid)
        shapes,invalid=self.maps[location];x0,y0,x1,y1=roi
        worldcorners=np.asarray([[x0,y0],[x1,y0],[x1,y1],[x0,y1]])@R.T+center
        region=Polygon(worldcorners);parts=[]
        from shapely.ops import transform
        for shape in shapes:
            if shape.intersects(region):
                clipped=shape.intersection(region)
                def local(x,y,z=None):
                    p=(np.column_stack([x,y])-center)@R
                    return p[:,0],p[:,1]
                parts.append(transform(local,clipped))
        result=unary_union(parts)
        return mapping(result),dict(source='nuScenes_map_expansion_drivable_area_OFFLINE_ONLY',location=location,roi_m=roi,invalid_polygons_skipped=invalid,area_m2=result.area)
    def export(self,case,cap,audit,roi):
        sample=self.acq.tables['sample'][case['sample_token']];scene=self.rows['scene'][sample['scene_token']]
        need(scene['name']==case['scene'],'NUSCENES_CASE','Scene identity mismatch')
        ref=self.acq.tables['sample_data'][audit['reference_sample_data_token']]
        offset=cap['vehicle']['center_to_rear_axle_m'];center,R,yaw=self.acq.pose_center(ref,offset)
        t=cap['metadata']['reference_time_s'];h=cap['metadata']['horizon_s'];step=cap['metadata']['step_s']
        objects=[];excluded=[];focal=self.rows['sample_annotation'][case['focal_annotation']]
        for a in self.annotations_by_sample[sample['token']]:
            track=self.tracks[a['instance_token']];state=self.state(track,t)
            if state is None:excluded.append(dict(annotation=a['token'],reason='reference_not_bracketed'));continue
            pos=(state[0]-center)@R
            if not (roi[0]<=pos[0]<=roi[2] and roi[1]<=pos[1]<=roi[3]):continue
            intervals=[];valid=True
            for k in range(round(h/step)):
                left=t+k*step;right=t+(k+1)*step
                knots=[left]+[self.stamp(x) for x in track if left<self.stamp(x)<right]+[right]
                states=[self.state(track,u) for u in knots]
                if any(x is None for x in states):valid=False;break
                local=np.asarray([(x[0]-center)@R for x in states]);radius=max(x[2] for x in states)
                lower=local.min(axis=0)-radius;upper=local.max(axis=0)+radius
                p=[[lower[0],lower[1]],[upper[0],lower[1]],[upper[0],upper[1]],[lower[0],upper[1]]]
                intervals.append(dict(start_s=k*step,end_s=(k+1)*step,polygon_m=p))
            if not valid:
                excluded.append(dict(annotation=a['token'],instance=a['instance_token'],reason='incomplete_future_or_gap_gt_0.75s'));continue
            category=self.rows['category'][self.rows['instance'][a['instance_token']]['category_token']]['name']
            objects.append(dict(id=a['instance_token'],annotation_token=a['token'],category=category,position_m=pos.tolist(),velocity_mps=(state[1]@R).tolist(),occlusion=self.rows['visibility'][a['visibility_token']]['level'],occupancy=intervals))
        need(focal['instance_token'] in {o['id'] for o in objects},'NUSCENES_FOCAL','Focal actor lacks complete evaluation coverage')
        location=self.rows['log'][scene['log_token']]['location'];road,road_info=self.road(location,center,R,roi)
        truth=dict(image_sha256=audit['image_sha256'],reference_time_id=cap['metadata']['reference_time_id'],coordinate_frame='ego_at_reference',
                   road_polygon_m=None,road_geometry_geojson=road,evaluation_roi_m=roi,scene_type=case['case_id'],model_object_ids={},objects=objects,
                   association_status='PENDING_REVIEW_OF_REAL_MODEL_OUTPUT',truth_source='nuScenes annotations/map; offline only',
                   occupancy_interpretation='Annotation-linear-center surrogate with circumscribed body envelopes; NOT measured continuous-time truth',
                   selection_bias='Curated mini cases; only ROI actors with complete 5s annotation tracks are scored; not dataset-level recall',
                   road_info=road_info,excluded_actors=excluded)
        actor=next(o for o in objects if o['id']==focal['instance_token'])
        recovered=np.asarray(actor['position_m'])@R.T+center
        original=self.state(self.tracks[focal['instance_token']],t)[0]
        # Independent homogeneous transform cross-check, including rear axle -> center.
        H=np.eye(3);H[:2,:2]=R;H[:2,2]=center
        inverse=np.linalg.inv(H)@np.r_[original,1.]
        residual=float(max(np.max(np.abs(recovered-original)),np.max(np.abs(inverse[:2]-actor['position_m']))))
        stats=dict(case_id=case['case_id'],scene=scene['name'],sample_id=case['sample_token'],scene_description=scene['description'],
                   focal_position_m=actor['position_m'],focal_velocity_mps=actor['velocity_mps'],focal_category=actor['category'],focal_visibility=actor['occlusion'],
                   focal_instance=actor['id'],scored_actors=len(objects),excluded_actors=len(excluded),coordinate_roundtrip_max_error_m=residual,
                   road_roi_area_m2=road_info['area_m2'],reference_time_s=t,ego_speed_mps=cap['ego']['speed_mps'],
                   future_intervals_per_actor=round(h/step),**{k:audit[k] for k in ['history_count','history_span_s','reference_camera_vs_annotation_sample_offset_s']})
        return truth,stats


def prepare(config,out):
    need(not out.exists(),'OUTPUT_EXISTS','Use a new output directory; existing evidence will not be overwritten')
    out.mkdir(parents=True);acq=Acquisition(config['dataset_root'],config['version']);offline=OfflineTruth(acq)
    index=[];truth={'samples':{}};stats=[]
    for case in config['cases']:
        cap,audit=acq.export(case['sample_token'],config)
        relative='runtime_inputs/'+case['sample_token']+'.json';write(out/relative,cap)
        gt,summary=offline.export(case,cap,audit,config['evaluation_roi_m'])
        truth['samples'][case['sample_token']]=gt;stats.append(summary)
        index.append(dict(sample_id=case['sample_token'],capture=relative))
        write(out/'offline_evaluation'/(case['case_id']+'_audit.json'),dict(acquisition_audit=audit,summary=summary))
    write(out/'runtime_index.json',dict(cases=index))
    write(out/'offline_evaluation'/'truth.json',truth)
    write(out/'offline_evaluation'/'case_selection.json',config)
    manifest=dict(status='REAL_DATA_PREPARED_NOT_MODEL_FEASIBILITY_VERIFIED',config_digest=digest(config),
                  dataset_root=str(acq.root),dataset_version=acq.version,cases=stats,online_calls=0,g2_experiment_status='NOT_RUN',
                  runtime_table_allowlist=sorted(acq.ALLOWED_TABLES),
                  source_hashes={str(p.relative_to(Path(__file__).parents[2])):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(sys.modules[Acquisition.__module__].__file__)]})
    write(out/'preparation_report.json',manifest);return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',default=str(Path(__file__).with_name('config.json')));parser.add_argument('--out',required=True)
    args=parser.parse_args();result=prepare(json.loads(Path(args.config).read_text()),Path(args.out))
    print(json.dumps(result,ensure_ascii=False,indent=2))
