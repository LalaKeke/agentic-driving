"""Synthetic oriented-BODY verifier regressions; no online observations."""
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest
from shapely.geometry import Point

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic import t1,t4
from agentic.contracts import artifact,digest,Failure,grid
from agentic.fixtures import capture,ScriptedAgent,rect
from agentic.geometry import rear_state,center_state,integrate


def fixture(speed=0.,vehicle=None,curvature=0.,actor=None,road=None):
    cap=capture(speed=speed)
    cap['metadata'].update(horizon_s=2.,valid_until_s=102.)
    if vehicle:cap['vehicle'].update(vehicle)
    out,prov=ScriptedAgent().decide(cap)
    out['decision'].update(behavior='hold_lane',stop=None,yield_to=[],desired_speed_mps=speed,
        target_region=rect(-20,60,-30,30),hard_requirements=['target_region','vehicle_limits','collision_free','road'])
    out['hypotheses']['road']['polygon_m']=road or rect(-20,60,-30,30)
    out['hypotheses']['objects']=[]
    if actor is not None:
        refs=dict(frame_ids=[cap['observations'][0]['id']],time_ids=[cap['metadata']['reference_time_id']])
        out['hypotheses']['objects']=[dict(id='adverse_actor',position_m=None,velocity_mps=None,uncertainty_m=.01,
            occupancy=[dict(start_s=k*.2,end_s=(k+1)*.2,kind='whole_interval_envelope',alternatives_m=actor(k)) for k in range(10)],**refs)]
    prov.update(experiment_profile='local_conditional_2s_prototype_v1',output_digest=digest(out))
    scene=t1.construct(cap,out,prov,True);v=cap['vehicle'];q=rear_state(cap['ego'],v)
    steer=math.atan(curvature*v['wheelbase_m']);points=[]
    for t in grid(cap['metadata']):
        state=integrate(q,0.,steer,t,v);center=center_state(state,v)
        points.append(dict(time_s=t,rear_x_m=state[0],rear_y_m=state[1],x_m=center[0],y_m=center[1],
            yaw_rad=state[2],speed_mps=state[3],acceleration_mps2=0.,steer_rad=steer,curvature_per_m=curvature))
    trajectory=artifact(scene,points=points,interpolation='exact_constant_acceleration_constant_rear_curvature',
        requested_prefix_s=.2,solver=dict(name='authored_test_trajectory',version='1',status='synthetic',objective=0.),
        planner_profile='yaw_bounded_box_v1',verification_profile='continuous_oriented_body_v1')
    return scene,trajectory


class BoxVerifierTests(unittest.TestCase):
    def test_00_heading_limit_is_independently_verified(self):
        scene,traj=fixture(speed=3.,curvature=.1)
        result=t4.verify(scene,traj)
        self.assertEqual(result['verdict'],'FAIL')
        self.assertIn('HEADING_BOUND',{v['code'] for v in result['violations']})

    def test_01_narrow_road_actual_body_passes_legacy_disk_remains_rejected(self):
        scene,traj=fixture(speed=3.,road=rect(-10,30,-2.,2.))
        self.assertEqual(t4.verify(scene,traj)['verdict'],'PASS')
        legacy=deepcopy(traj);legacy.pop('planner_profile');legacy.pop('verification_profile')
        result=t4.verify(scene,legacy)
        self.assertEqual(result['verdict'],'FAIL')
        self.assertIn('ROAD_BOUNDARY',{v['code'] for v in result['violations']})

    def test_02_explicit_pair_and_prototype_scene_required(self):
        scene,traj=fixture()
        for which in ('planner_profile','verification_profile'):
            bad=deepcopy(traj);bad.pop(which)
            with self.assertRaises(Failure) as cm:t4.verify(scene,bad)
            self.assertEqual(cm.exception.record['code'],'VERIFICATION_PROFILE')
        scene['provenance'].pop('experiment_profile');traj['scene_digest']=digest(scene)
        with self.assertRaises(Failure):t4.verify(scene,traj)

    def test_03_future_actor_intrusion_hits_stationary_body(self):
        scene,traj=fixture(actor=lambda k:[rect(20,21,-.2,.2)] if k<3 else [rect(-.2,.2,-.2,.2)])
        result=t4.verify(scene,traj)
        self.assertEqual(result['verdict'],'FAIL');self.assertEqual(result['released_prefix_s'],0.)
        self.assertTrue(any(v['code']=='BODY_COLLISION' and v['time_interval_s'][0]>=.59 for v in result['violations']))

    def test_04_between_grid_collision_uses_continuous_body_sweep(self):
        tiny=dict(front_m=.05,rear_m=.05,half_width_m=.02,center_to_rear_axle_m=.02,wheelbase_m=.1)
        scene,traj=fixture(speed=12.,vehicle=tiny,actor=lambda k:[rect(1.19,1.21,-.01,.01)])
        shape=Point(1.2,0.)
        for p in traj['points'][:2]:
            body=t4.oriented_body([p['rear_x_m'],p['rear_y_m'],p['yaw_rad'],p['speed_mps']],scene['capture']['vehicle'])
            self.assertGreater(body.distance(shape),scene['capture']['safety']['margin_m'])
        result=t4.verify(scene,traj)
        self.assertTrue(any(v['code']=='BODY_COLLISION' and 0<v['time_interval_s'][0]<.2 for v in result['violations']))

    def test_05_turned_corner_collision_is_not_rear_point_clearance(self):
        scene,traj=fixture(speed=6.,curvature=.15)
        v=scene['capture']['vehicle'];q=rear_state(scene['capture']['ego'],v)
        at=integrate(q,0.,math.atan(.15*v['wheelbase_m']),.5,v)
        corner=list(t4.oriented_body(at,v).exterior.coords)[2]
        scene,traj=fixture(speed=6.,curvature=.15,actor=lambda k:[rect(corner[0]-.03,corner[0]+.03,corner[1]-.03,corner[1]+.03)])
        result=t4.verify(scene,traj)
        self.assertIn('BODY_COLLISION',{x['code'] for x in result['violations']})

    def test_06_dense_independent_arc_body_is_enclosed_by_proved_padding(self):
        vehicle=capture()['vehicle'];comparisons=0
        for curvature in (-.2,-.05,0.,.05,.2):
            for speed in (0.,2.,12.):
                for acceleration in (-1.,0.,2.):
                    if speed==0. and acceleration<0:continue
                    dt=.02;start=[1.,-2.,.7,speed]
                    def independent(t):
                        s=speed*t+.5*acceleration*t*t;yaw=start[2]
                        if curvature:
                            return [start[0]+(math.sin(yaw+curvature*s)-math.sin(yaw))/curvature,
                                start[1]+(math.cos(yaw)-math.cos(yaw+curvature*s))/curvature,yaw+curvature*s,speed+acceleration*t]
                        return [start[0]+s*math.cos(yaw),start[1]+s*math.sin(yaw),yaw,speed+acceleration*t]
                    _,_,hull,pad=t4.continuous_body_sweep(start,independent(dt),curvature,dt,vehicle)
                    for j in range(101):
                        for xy in list(t4.oriented_body(independent(dt*j/100.),vehicle).exterior.coords)[:-1]:
                            self.assertLessEqual(hull.distance(Point(xy)),pad+1e-10);comparisons+=1
        self.assertGreater(comparisons,10000)

    def test_07_dynamics_reference_and_scene_identity_checks_remain(self):
        scene,traj=fixture(speed=3.)
        for change,code in (('rear_x_m','DYNAMICS_RESIDUAL'),('x_m','REFERENCE_POINT')):
            bad=deepcopy(traj);bad['points'][2][change]+=.1
            result=t4.verify(scene,bad)
            self.assertIn(code,{x['code'] for x in result['violations']})
        bad=deepcopy(traj);bad['scene_digest']='not-the-scene'
        with self.assertRaises(Failure):t4.verify(scene,bad)


if __name__=='__main__':unittest.main()
