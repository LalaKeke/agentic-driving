"""ARTIFICIAL fixtures. No real images, model calls, or simulator hidden truth."""
from copy import deepcopy
import json
from .contracts import ROOT,digest,artifact
from .geometry import integrate,rear_state,center_state

def rect(x0,x1,y0,y1):return [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
def capture(t=100.,speed=6.,revision=0):
    return dict(metadata=dict(profile='convex_disk_bicycle_v2',schema_version='2.0.0',scene_id='artificial_pedestrian',revision=revision,reference_time_id=f't{t:.3f}',reference_time_s=t,coordinate_frame='ego_at_reference',horizon_s=5.,step_s=.2,history_s=2.,valid_until_s=t+5),observations=[dict(id=f'fixture{t:.3f}',time_id=f't{t:.3f}',capture_time_s=t,capture_utc=None,camera_id='front',path='fixture://not-real-image')],ego=dict(x_m=0.,y_m=0.,yaw_rad=0.,speed_mps=speed),vehicle=dict(front_m=2.3,rear_m=2.3,half_width_m=.9,center_to_rear_axle_m=1.2,wheelbase_m=2.7,max_speed_mps=12.,max_accel_mps2=2.,max_brake_mps2=3.,max_steer_rad=.5,max_curvature_per_m=.2),navigation_intent='Proceed ahead after yielding to crossing pedestrian',safety=dict(margin_m=1.,prefix_s=.2))

class ScriptedAgent:
    source='synthetic_fixture'
    def __init__(self):self.calls=0;self.ego_x=0.
    def decide(self,cap,feedback=None):
        self.calls+=1;m=cap['metadata'];t=m['reference_time_s'];frame=cap['observations'][-1]
        refs=dict(frame_ids=[frame['id']],time_ids=[frame['time_id']]);clear=t>=104.2
        # Authored artificial model hypotheses, rebased using PUBLIC ego prefix only.
        # No actor/world state is read from the executor; no true sensor images exist.
        intervals=[]
        for k in range(round(m['horizon_s']/m['step_s'])):
            a=k*m['step_s'];b=(k+1)*m['step_s']
            y=-3+2.5*(t-100+a)
            intervals.append(dict(start_s=a,end_s=b,kind='whole_interval_envelope',alternatives_m=[rect(17.6-self.ego_x,18.4-self.ego_x,y-.5,y+.5+2.5*(b-a))]))
        position=[18.-self.ego_x,-3+2.5*(t-100)]
        hard=['target_region','vehicle_limits','collision_free','road']+([] if clear else ['stop','yield'])
        output=dict(decision=dict(scene_id=m['scene_id'],revision=m['revision'],behavior='resume' if clear else 'stop_yield',target_region=rect(-8,45,-3,3),desired_speed_mps=4. if clear else 0.,yield_to=[] if clear else ['pedestrian'],stop=None if clear else dict(center_x_max_m=10. if feedback else 12.,deadline_s=max(0.,104.-t)),priority=1,valid_until_s=m['valid_until_s'],hard_requirements=hard,preferences=dict(speed_weight=1.,acceleration_weight=1.,steering_weight=1.,smoothness_weight=.1)),hypotheses=dict(coordinate_frame='ego_at_reference',reference_time_id=m['reference_time_id'],units='m_s_rad',scale_basis='model_metric_hypothesis',road=dict(polygon_m=rect(-10,50,-6,6),uncertainty_m=.1,**refs),objects=[dict(id='pedestrian',position_m=position,velocity_mps=[0.,2.5],uncertainty_m=.2,occupancy=intervals,**refs)],unknown_regions=[],traffic_evidence=[],unbounded_unknown=False,confidence_note='Artificial fixture; uncertainty is not calibrated'))
        return output,dict(source='synthetic_fixture',model_id='scripted-not-a-model-call',prompt_version='fixture_v2',output_digest=digest(output))

def coasting_fault(scene,traj):
    out=deepcopy(traj);out['metadata']=deepcopy(scene['metadata']);out['scene_digest']=digest(scene);q=rear_state(scene['capture']['ego'],scene['capture']['vehicle']);v=scene['capture']['vehicle']
    for p in out['points']:
        state=integrate(q,0,0,p['time_s'],v);c=center_state(state,v)
        p.update(rear_x_m=state[0],rear_y_m=state[1],x_m=c[0],y_m=c[1],yaw_rad=state[2],speed_mps=state[3],acceleration_mps2=0.,steer_rad=0.,curvature_per_m=0.)
    out['solver']['fault_injection']='synthetic_coasting';return out
