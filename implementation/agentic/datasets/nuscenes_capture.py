"""nuScenes acquisition-only export: no actor annotations, maps or future poses.

The native ego origin is the rear axle. A configured center offset is explicit;
vehicle dimensions are experiment assumptions, not nuScenes measurements.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from ..contracts import need,validate


def quaternion_matrix(q):
    q=np.asarray(q,dtype=float)
    need(q.shape==(4,) and np.isfinite(q).all() and abs(np.linalg.norm(q)-1)<1e-3,
         'NUSCENES_ROTATION','Invalid wxyz quaternion')
    w,x,y,z=q/np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                     [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                     [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])


def planar_rotation(q):
    R=quaternion_matrix(q);theta=math.atan2(R[1,0],R[0,0])
    return np.array([[math.cos(theta),-math.sin(theta)],
                     [math.sin(theta), math.cos(theta)]]),theta


class Acquisition:
    ALLOWED_TABLES={'sample','sample_data','ego_pose','sensor','calibrated_sensor'}
    def __init__(self,root,version='v1.0-mini'):
        self.root=Path(root).resolve();self.version=version;self.tables={};self.accessed=[]
        need((self.root/version).is_dir(),'NUSCENES_ROOT','Dataset metadata directory missing')
        for name in sorted(self.ALLOWED_TABLES):
            self.accessed.append(name)
            rows=json.loads((self.root/version/(name+'.json')).read_text())
            self.tables[name]={r['token']:r for r in rows}
        self.channels={token:self.tables['sensor'][row['sensor_token']]['channel']
                       for token,row in self.tables['calibrated_sensor'].items()}
        self.keyframes={}
        for row in self.tables['sample_data'].values():
            if row['is_key_frame'] and self.channels[row['calibrated_sensor_token']].startswith('CAM_'):
                self.keyframes[(row['sample_token'],self.channels[row['calibrated_sensor_token']])]=row

    def pose_center(self,frame,offset):
        pose=self.tables['ego_pose'][frame['ego_pose_token']]
        R,yaw=planar_rotation(pose['rotation'])
        return np.asarray(pose['translation'][:2])+R@np.array([offset,0.]),R,yaw

    def history(self,frame,seconds):
        rows=[];current=frame;stamp=frame['timestamp']
        while current and stamp-current['timestamp']<=seconds*1e6:
            rows.append(current)
            current=self.tables['sample_data'].get(current['prev'])
        return sorted(rows,key=lambda r:r['timestamp'])

    def export(self,sample_token,config):
        ref=self.keyframes[(sample_token,'CAM_FRONT')]
        history_s=config['history_s'];horizon=config['horizon_s'];step=config['step_s']
        history=self.history(ref,history_s)
        need(len(history)>1,'NUSCENES_HISTORY','Insufficient acquisition history')
        # Actual recorded frames nearest to requested historical offsets, never future data.
        chosen={ref['token']:ref}
        for delay in (0.,.5,1.,1.5,2.):
            if delay<=history_s:
                row=min(history,key=lambda r:abs(r['timestamp']-(ref['timestamp']-delay*1e6)))
                chosen[row['token']]=row
        for channel in config['current_cameras']:
            row=self.keyframes.get((sample_token,channel))
            while row and row['timestamp']>ref['timestamp']:
                row=self.tables['sample_data'].get(row['prev'])
            if row and 0<=ref['timestamp']-row['timestamp']<=history_s*1e6:
                chosen[row['token']]=row
        frames=sorted(chosen.values(),key=lambda r:(r['timestamp'],r['token']))
        v=config['vehicle'];offset=v['center_to_rear_axle_m'];center,R,yaw=self.pose_center(ref,offset)
        older=min([r for r in history if ref['timestamp']-r['timestamp']>=.1e6],
                  key=lambda r:abs((ref['timestamp']-r['timestamp'])-.5e6))
        before,_,_=self.pose_center(older,offset);dt=(ref['timestamp']-older['timestamp'])*1e-6
        velocity=(center-before)@R/dt
        need(velocity[0]>=-.1,'NUSCENES_REVERSE','Reverse motion unsupported by current planner')
        t=ref['timestamp']*1e-6;observations=[];calibrations={};ego_history=[];hashes={}
        for f in frames:
            path=self.root/f['filename'];need(path.is_file(),'NUSCENES_IMAGE','Missing actual image',filename=f['filename'])
            channel=self.channels[f['calibrated_sensor_token']];cal=self.tables['calibrated_sensor'][f['calibrated_sensor_token']]
            time_id='nusc_us_'+str(f['timestamp']);frame_id=f['token']
            observations.append(dict(id=frame_id,time_id=time_id,capture_time_s=f['timestamp']*1e-6,
                capture_utc=datetime.fromtimestamp(f['timestamp']*1e-6,timezone.utc).isoformat(),camera_id=channel,path=str(path)))
            hashes[frame_id]=hashlib.sha256(path.read_bytes()).hexdigest()
            # Static camera pose relative to the configured vehicle CENTER, not the dataset rear axle.
            trans=np.asarray(cal['translation'])-np.array([offset,0,0])
            calibrations[channel]=dict(camera_intrinsic=cal['camera_intrinsic'],camera_to_center_rotation=quaternion_matrix(cal['rotation']).tolist(),camera_to_center_translation_m=trans.tolist())
            hist_center,_,hist_yaw=self.pose_center(f,offset)
            delta=(hist_center-center)@R
            ego_history.append(dict(frame_id=frame_id,time_id=time_id,center_xy_m=delta.tolist(),yaw_rad=math.atan2(math.sin(hist_yaw-yaw),math.cos(hist_yaw-yaw))))
        cap=dict(metadata=dict(profile='convex_disk_bicycle_v2',schema_version='2.0.0',scene_id='nusc_'+sample_token,
                 revision=0,reference_time_id='nusc_us_'+str(ref['timestamp']),reference_time_s=t,
                 coordinate_frame='ego_at_reference',horizon_s=horizon,step_s=step,history_s=history_s,valid_until_s=t+horizon),
                 observations=observations,ego=dict(x_m=0.,y_m=0.,yaw_rad=0.,speed_mps=max(0.,float(velocity[0]))),
                 vehicle=v,navigation_intent=config['navigation_intent'],safety=config['safety'],
                 acquisition_metadata=dict(dataset='nuScenes',native_ego_reference='rear_axle',
                     center_offset_assumption_m=offset,calibrations=calibrations,ego_history=ego_history,
                     ego_speed_source='causal_backward_difference_localization_0.5s; not direct CAN speed',
                     vehicle_parameters_source=config['vehicle_parameters_source']))
        validate('capture_v2.schema.json',cap)
        return cap,dict(sample_token=sample_token,reference_sample_data_token=ref['token'],image_sha256=hashes,
                       accessed_tables=sorted(self.accessed),history_count=len(frames),
                       history_span_s=(ref['timestamp']-frames[0]['timestamp'])*1e-6,
                       forward_speed_mps=float(velocity[0]),lateral_velocity_diagnostic_mps=float(velocity[1]),
                       reference_camera_vs_annotation_sample_offset_s=(ref['timestamp']-self.tables['sample'][sample_token]['timestamp'])*1e-6,
                       vehicle_parameters_are_assumptions=True)
