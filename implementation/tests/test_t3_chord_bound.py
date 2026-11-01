"""Actual NLP, independent arc integration and collision regressions.
Authored software tests only; no real model or G2 success claims.
"""
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'implementation'))
from agentic import t1,t2,t3,t4
from agentic.contracts import Failure,digest
from agentic.fixtures import capture,ScriptedAgent,rect,coasting_fault
from agentic.geometry import disk_radius
from shapely.geometry import Point,Polygon

EPS=1e-10


def case(speed=6.,target=None,vehicle=None):
    cap=capture(speed=speed)
    if vehicle:cap['vehicle'].update(vehicle)
    out,pro=ScriptedAgent().decide(cap)
    out['decision'].update(behavior='hold_lane',stop=None,desired_speed_mps=speed,
        yield_to=[],target_region=target or rect(-8,45,-1.5,1.5),
        hard_requirements=['target_region','vehicle_limits','collision_free','road'])
    out['hypotheses']['road']['polygon_m']=rect(-10,60,-12,12)
    out['hypotheses']['objects']=[]
    pro['output_digest']=digest(out)
    return cap,out,pro,t1.construct(cap,out,pro,True)


def independent_rear_at(point,t,vehicle):
    """Closed-form circle integration, independent of agentic.geometry.integrate."""
    k=math.tan(point['steer_rad'])/vehicle['wheelbase_m']
    length=point['speed_mps']*t+.5*point['acceleration_mps2']*t*t
    yaw=point['yaw_rad'];x=point['rear_x_m'];y=point['rear_y_m']
    if abs(k)<1e-8:
        return x+length*math.cos(yaw),y+length*math.sin(yaw)
    return x+(math.sin(yaw+k*length)-math.sin(yaw))/k, y+(math.cos(yaw)-math.cos(yaw+k*length))/k


class ChordBoundTests(unittest.TestCase):
    def test_01_narrow_target_real_ipopt_and_independent_t4_pass(self):
        cap,out,pro,scene=case();corridor=t2.build(scene)
        first=corridor['cells'][0]
        self.assertAlmostEqual(max(y for x,y in first['polygon_m']),.3)
        self.assertGreater((6*.2-.5*3*.2**2)/2,.3)
        trajectory=t3.solve(scene,corridor)
        self.assertIn('ipopt',trajectory['solver']['name'])
        verification=t4.verify(scene,trajectory)
        self.assertEqual(verification['verdict'],'PASS',verification['violations'][:3])
        self.assertEqual(verification['released_prefix_s'],.2)
        self.assertGreater(trajectory['points'][-1]['x_m'],29.)
        print('NARROW_TARGET',{'legacy_minimum_endpoint_padding_m':.57,'new':trajectory['solver']['status'],
              'T4':verification['verdict'],'terminal_x_m':trajectory['points'][-1]['x_m'],
              'prefix_s':verification['released_prefix_s']})

    def test_02_actual_curved_ipopt_path_dense_independent_halfplane_check(self):
        target=[[-8,-3],[45,4],[45,8],[-8,1]]
        cap,out,pro,scene=case(speed=2.,target=target)
        out['decision']['desired_speed_mps']=3.
        # The usual 12m-wide road also independently constrains the body disk.
        out['hypotheses']['road']['polygon_m']=rect(-10,50,-6,6)
        pro['output_digest']=digest(out);scene=t1.construct(cap,out,pro,True)
        corridor=t2.build(scene);trajectory=t3.solve(scene,corridor)
        verification=t4.verify(scene,trajectory)
        self.assertEqual(verification['verdict'],'PASS',verification['violations'][:3])
        self.assertGreater(abs(trajectory['points'][-1]['y_m']),.1)
        self.assertGreater(max(abs(p['curvature_per_m']) for p in trajectory['points']),1e-4)
        worst=-float('inf');samples=0;max_arc_error=0.
        for k,(point,nxt) in enumerate(zip(trajectory['points'],trajectory['points'][1:])):
            dt=nxt['time_s']-point['time_s'];curv=math.tan(point['steer_rad'])/cap['vehicle']['wheelbase_m']
            length=point['speed_mps']*dt+.5*point['acceleration_mps2']*dt*dt
            pad=math.sqrt(curv*curv+EPS*EPS)*length*length/8
            first=(point['rear_x_m'],point['rear_y_m']);last=independent_rear_at(point,dt,cap['vehicle'])
            for j in range(201):
                t=dt*j/200.;xy=independent_rear_at(point,t,cap['vehicle'])
                travelled=point['speed_mps']*t+.5*point['acceleration_mps2']*t*t
                ratio=travelled/length if length>1e-12 else 0.
                chord=((1-ratio)*first[0]+ratio*last[0],(1-ratio)*first[1]+ratio*last[1])
                error=math.dist(xy,chord);max_arc_error=max(max_arc_error,error)
                self.assertLessEqual(error,pad+2e-7)
                for nx,ny,b in corridor['cells'][k]['halfplanes']:
                    worst=max(worst,nx*xy[0]+ny*xy[1]-b)
                samples+=1
        self.assertLessEqual(worst,1e-6)
        print('CURVED_DENSE',{'samples':samples,'worst_halfplane_excess_m':worst,
              'max_arc_to_chord_m':max_arc_error,'terminal_y_m':trajectory['points'][-1]['y_m'],
              'T4':verification['verdict']})

    def test_03_arc_length_bound_for_signed_curvature_and_long_arcs(self):
        # Deliberately includes >pi turns to test the stated interpolation theorem,
        # independent of the smaller timestep used in the production profile.
        comparisons=0
        for curv in [-.2,-.01,0.,.01,.2]:
            for length in [0.,.1,1.2,8.,40.]:
                for yaw in [-1.2,0.,.7]:
                    if curv==0:last=(length*math.cos(yaw),length*math.sin(yaw))
                    else:last=((math.sin(yaw+curv*length)-math.sin(yaw))/curv,
                               (math.cos(yaw)-math.cos(yaw+curv*length))/curv)
                    bound=math.sqrt(curv*curv+EPS*EPS)*length*length/8
                    for j in range(201):
                        ratio=j/200.;s=ratio*length
                        if curv==0:xy=(s*math.cos(yaw),s*math.sin(yaw))
                        else:xy=((math.sin(yaw+curv*s)-math.sin(yaw))/curv,
                                 (math.cos(yaw)-math.cos(yaw+curv*s))/curv)
                        chord=(ratio*last[0],ratio*last[1])
                        self.assertLessEqual(math.dist(xy,chord),bound+2e-12)
                        comparisons+=1
        print('ARC_BOUND_COMPARISONS',comparisons)

    def test_04_full_size_vehicle_physical_collision_still_rejected(self):
        cap,out,pro,scene=case()
        trajectory=t3.solve(scene,t2.build(scene))
        self.assertEqual(t4.verify(scene,trajectory)['verdict'],'PASS')
        authored,auth_pro=ScriptedAgent().decide(cap);actor=deepcopy(authored['hypotheses']['objects'][0])
        actor.update(position_m=[18.,0.],velocity_mps=[0.,0.],uncertainty_m=.2)
        for interval in actor['occupancy']:interval['alternatives_m']=[rect(17.8,18.2,-.2,.2)]
        out['hypotheses']['objects']=[actor];pro['output_digest']=digest(out)
        collision_scene=t1.construct(cap,out,pro,True)
        candidate=coasting_fault(collision_scene,trajectory)
        verification=t4.verify(collision_scene,candidate)
        self.assertEqual(verification['verdict'],'FAIL')
        self.assertEqual(verification['released_prefix_s'],0)
        self.assertIn('BODY_COLLISION',{v['code'] for v in verification['violations']})

    def test_05_between_sample_physical_collision_still_rejected_by_unchanged_t4(self):
        v=dict(front_m=.05,rear_m=.05,half_width_m=.02,center_to_rear_axle_m=.02,wheelbase_m=.1)
        cap,out,pro,scene=case(speed=12.,target=rect(-8,95,-1.5,1.5),vehicle=v)
        out['hypotheses']['road']['polygon_m']=rect(-10,100,-12,12)
        pro['output_digest']=digest(out);scene=t1.construct(cap,out,pro,True)
        corridor=t2.build(scene);free_trajectory=t3.solve(scene,corridor)
        self.assertEqual(t4.verify(scene,free_trajectory)['verdict'],'PASS')
        authored,auth_pro=ScriptedAgent().decide(cap);actor=deepcopy(authored['hypotheses']['objects'][0])
        actor.update(position_m=[1.2,0.],velocity_mps=[0.,0.],uncertainty_m=.01)
        for interval in actor['occupancy']:interval['alternatives_m']=[rect(1.19,1.21,-.01,.01)]
        out['hypotheses']['objects']=[actor];pro['output_digest']=digest(out)
        collision_scene=t1.construct(cap,out,pro,True)
        # Authored fault candidate is bound to the new raw scene. No corridor is
        # supplied to T4, so success of a free-scene solve cannot hide the obstacle.
        candidate=coasting_fault(collision_scene,free_trajectory)
        obstacle=Polygon(actor['occupancy'][0]['alternatives_m'][0]);radius=disk_radius(cap['vehicle'])
        self.assertTrue(all(obstacle.distance(Point((p['rear_x_m'],p['rear_y_m'])))-radius-actor['uncertainty_m']>cap['safety']['margin_m']
                            for p in candidate['points'][:2]))
        check=t4.verify(collision_scene,candidate)
        collisions=[v for v in check['violations'] if v['code']=='BODY_COLLISION']
        self.assertEqual(check['verdict'],'FAIL');self.assertEqual(check['released_prefix_s'],0)
        self.assertTrue(collisions)
        self.assertTrue(any(0<v['time_interval_s'][0]<.2 for v in collisions))
        print('COLLISION_T4',{'verdict':check['verdict'],'prefix_s':check['released_prefix_s'],
              'collision_intervals':len(collisions)})

    def test_06_stopped_boundary_stays_closed(self):
        cap,out,pro,scene=case(speed=0.)
        out['decision'].update(behavior='stop_yield',stop={'center_x_max_m':0.,'deadline_s':0.},
            hard_requirements=['target_region','vehicle_limits','collision_free','road','stop','yield'])
        pro['output_digest']=digest(out);scene=t1.construct(cap,out,pro,True)
        corridor=t2.build(scene,policy='moving_seed');trajectory=t3.solve(scene,corridor)
        self.assertEqual(t4.verify(scene,trajectory)['verdict'],'PASS')
        self.assertTrue(all(abs(p['speed_mps'])<1e-5 for p in trajectory['points']))


if __name__=='__main__':unittest.main(verbosity=2)
