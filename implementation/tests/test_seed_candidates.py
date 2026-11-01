"""Focused bounded-seed regressions; authored fixtures are never online results."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic import t1,t2,t3,t4,candidate_search
from agentic.acceptance import assess_round
from agentic.contracts import Failure,artifact,digest,grid
from agentic.fixtures import rect
from agentic.fixtures_v3 import metric_case,add_adverse_scenario
from agentic.geometry import rear_state,center_state,integrate
from agentic.runtime import SimulatedExecutor


def authored_case(speed=3.,half_road=1.5,future_intrusion=False):
    cap,out,prov=metric_case(asynchronous=False,speed=speed)
    cap['metadata'].update(horizon_s=2.,valid_until_s=102.)
    out['decision'].update(behavior='hold_lane',stop=None,yield_to=[],desired_speed_mps=speed,
        target_region=rect(-8,30,-half_road,half_road),
        hard_requirements=['target_region','vehicle_limits','collision_free','road'])
    out['hypotheses']['road']['polygon_m']=rect(-10,40,-half_road,half_road)
    if future_intrusion:
        add_adverse_scenario(out,invade=False)
        first=out['hypotheses']['objects'][0]['occupancy'][0]
        later=deepcopy(first);first['end_s']=.6
        later.update(id='authored_adverse_future_entry',start_s=.6,end_s=2.)
        later['alternatives'][1]['polygon_m']=rect(-2,19,-4,10)
        out['hypotheses']['objects'][0]['occupancy'].append(later)
    for premise in out['hypotheses']['premises']:premise['end_s']=2.
    for actor in out['hypotheses']['objects']:
        for source in actor['occupancy']:
            if not future_intrusion:source['end_s']=2.
    out['hypotheses']['premises'].append(dict(id='prototype_local_scope',object_ids=[],scenario_ids=[],
        start_s=0.,end_s=2.,space_polygon_m=rect(-100,100,-100,100),
        statement='AUTHORED local world for bounded-seed software tests; no real images or model calls.'))
    prov.update(prompt_version='driving_prototype_v1',experiment_profile='local_conditional_2s_prototype_v1',
                output_digest=digest(out))
    return cap,out,prov


def authored_trajectory(scene,curvature=0.,band=(-.025,.025)):
    v=scene['capture']['vehicle'];q=rear_state(scene['capture']['ego'],v)
    delta=math.atan(curvature*v['wheelbase_m']);points=[]
    for t in grid(scene['metadata']):
        state=integrate(q,0.,delta,t,v);c=center_state(state,v)
        points.append(dict(time_s=t,rear_x_m=state[0],rear_y_m=state[1],x_m=c[0],y_m=c[1],
            yaw_rad=state[2],speed_mps=state[3],acceleration_mps2=0.,steer_rad=delta,
            curvature_per_m=curvature))
    return artifact(scene,points=points,interpolation='exact_constant_acceleration_constant_rear_curvature',
        requested_prefix_s=.2,solver=dict(name='authored_test_trajectory',version='1',status='synthetic',objective=0.),
        planner_profile=t2.YAW_BOX_PROFILE,verification_profile='continuous_oriented_body_v1',
        search_profile=t2.SEARCH_PROFILE,heading_bands_rad=[list(band) for _ in points[:-1]])


class SeedCandidateTests(unittest.TestCase):
    def test_asymmetric_interval_support_matches_dense_independent_rotations(self):
        cap,_,_=authored_case();v=cap['vehicle'];d=v['center_to_rear_axle_m']
        corners=[(x,y) for x in (d-v['rear_m'],d+v['front_m']) for y in (-v['half_width_m'],v['half_width_m'])]
        for band in ((-.29,-.04),(.08,.31),(-.025,.025)):
            for i in range(16):
                nx,ny=math.cos(i*math.pi/8),math.sin(i*math.pi/8)
                support=t2.band_body_support(v,nx,ny,band)
                dense=max(nx*(math.cos(a)*x-math.sin(a)*y)+ny*(math.sin(a)*x+math.cos(a)*y)
                          for a in (band[0]+j*(band[1]-band[0])/400 for j in range(401))
                          for x,y in corners)
                self.assertGreaterEqual(support+1e-12,dense)
                self.assertLess(support-dense,1e-6)
                point=t2.band_point_support((d,0.),nx,ny,band)
                for endpoint in band:
                    self.assertGreaterEqual(point+1e-12,d*(nx*math.cos(endpoint)+ny*math.sin(endpoint)))

    def test_seeds_start_at_actual_state_and_obey_acceleration_steering_curvature_limits(self):
        cap,out,prov=authored_case(speed=6.,half_road=20.)
        cap['vehicle'].update(max_steer_rad=.08,max_curvature_per_m=.025)
        scene=t1.construct(cap,out,prov,True);v=cap['vehicle']
        for name in t2.CANDIDATE_IDS:
            seed=t2.candidate_seed(scene,name)
            self.assertEqual([seed[0][k] for k in ('rear_x_m','rear_y_m','yaw_rad','speed_mps')],rear_state(cap['ego'],v))
            for a,b in zip(seed,seed[1:]):
                dt=b['time_s']-a['time_s'];acc=a['acceleration_mps2'];delta=a['steer_rad'];k=math.tan(delta)/v['wheelbase_m']
                self.assertTrue(-v['max_brake_mps2']-1e-10<=acc<=v['max_accel_mps2']+1e-10)
                self.assertLessEqual(abs(delta),v['max_steer_rad']+1e-10)
                self.assertLessEqual(abs(k),v['max_curvature_per_m']+1e-10)
                travel=a['speed_mps']*dt+.5*acc*dt**2;yaw=a['yaw_rad']
                expected_x=a['rear_x_m']+((math.sin(yaw+k*travel)-math.sin(yaw))/k if k else travel*math.cos(yaw))
                expected_y=a['rear_y_m']+((math.cos(yaw)-math.cos(yaw+k*travel))/k if k else travel*math.sin(yaw))
                self.assertAlmostEqual(b['rear_x_m'],expected_x,places=10)
                self.assertAlmostEqual(b['rear_y_m'],expected_y,places=10)
                self.assertAlmostEqual(b['speed_mps'],a['speed_mps']+acc*dt,places=10)
                self.assertGreaterEqual(b['speed_mps'],-1e-10)
            if name!='original':self.assertAlmostEqual(seed[1]['speed_mps'],5.4)

    def test_narrow_authored_road_full_chain_passes_without_online_credit(self):
        cap,out,prov=authored_case();scene=t1.construct(cap,out,prov,True)
        with self.assertRaises(Failure) as old:t2.build(scene,policy=t2.YAW_BOX_PROFILE)
        self.assertEqual(old.exception.record['code'],'CORRIDOR_EMPTY')
        corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='brake_straight')
        trajectory=t3.solve(scene,corridor);verification=t4.verify(scene,trajectory)
        self.assertEqual(verification['verdict'],'PASS')
        self.assertEqual(trajectory['heading_bands_rad'],[cell['heading_band_rad'] for cell in corridor['cells']])
        executor=SimulatedExecutor();executor.observe(cap)
        execution=executor.execute(scene,trajectory,verification,cap['metadata']['reference_time_s'])
        receipt=dict(capture_digest=digest(cap),scene_digest=digest(scene),trajectory_digest=digest(trajectory),
                     verification_digest=digest(verification),execution_digest=digest(execution),
                     executed_at_s=cap['metadata']['reference_time_s'],consumed_once=True)
        result=dict(status='executed_simulated_prefix',revisions=0,agent_output=out,provenance=prov,
                    scene=scene,corridor=corridor,trajectory=trajectory,verification=verification,
                    execution=execution,execution_receipt=receipt)
        accepted=assess_round(result,cap)
        self.assertTrue(accepted['software_success'],accepted)
        self.assertFalse(accepted['strict_live_physical_success'])
        self.assertEqual(accepted['motion_class'],'MOVING_PREFIX')
        self.assertGreater(execution['center_displacement_m'][0],.5)
        self.assertEqual(accepted['ground_truth_correctness'],'NOT_EVALUATED')
        changed=deepcopy(result);changed['trajectory']['heading_bands_rad'][0]=[-.02,.02]
        rejected=assess_round(changed,cap)
        self.assertFalse(rejected['software_success'])
        self.assertEqual(rejected['reasons'][0]['code'],'CORRIDOR_HEADING_BOUND')

    def test_t4_rejects_yaw_outside_candidate_band_even_inside_legacy_global_cap(self):
        cap,out,prov=authored_case(speed=1.,half_road=20.)
        scene=t1.construct(cap,out,prov,True);trajectory=authored_trajectory(scene,curvature=.1)
        self.assertLess(max(abs(p['yaw_rad']) for p in trajectory['points']),.35)
        result=t4.verify(scene,trajectory);codes={v['code'] for v in result['violations']}
        self.assertIn('CANDIDATE_HEADING_BOUND',codes)
        self.assertNotIn('DYNAMICS_RESIDUAL',codes)
        self.assertEqual(result['released_prefix_s'],0)

    def test_missing_or_reversed_bands_cannot_silently_fall_back(self):
        cap,out,prov=authored_case();scene=t1.construct(cap,out,prov,True)
        corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='brake_straight')
        trajectory=authored_trajectory(scene)
        missing=deepcopy(trajectory);missing.pop('heading_bands_rad')
        with self.assertRaises(Failure):t4.verify(scene,missing)
        missing=deepcopy(corridor);missing['cells'][0].pop('heading_band_rad')
        with self.assertRaises(Failure):t3.solve(scene,missing)
        bad=deepcopy(trajectory);bad['heading_bands_rad'][0]=[.02,-.02]
        with self.assertRaises(Failure) as rejected:t4.verify(scene,bad)
        self.assertEqual(rejected.exception.record['code'],'HEADING_COVERAGE')

    def test_future_adverse_intrusion_rejects_stationary_ego_and_all_candidates(self):
        cap,out,prov=authored_case(speed=0.,half_road=20.,future_intrusion=True)
        scene=t1.construct(cap,out,prov,True);before=digest(scene)
        trajectory=authored_trajectory(scene);verification=t4.verify(scene,trajectory)
        self.assertEqual(verification['verdict'],'FAIL');self.assertEqual(verification['released_prefix_s'],0)
        self.assertTrue(any(v['code']=='BODY_COLLISION' and v['time_interval_s'][0]>=.39 for v in verification['violations']))
        winner,ledger,failure=candidate_search.plan(scene)
        self.assertIsNone(winner);self.assertIsNotNone(failure)
        self.assertEqual(ledger['candidate_count'],5)
        self.assertTrue(all(r['T2']=='FAIL' and r['T3']=='NOT_REACHED' for r in ledger['candidates']))
        self.assertEqual(before,digest(scene))

    def test_blocked_braking_hint_does_not_preclude_safe_forward_optimizer_solution(self):
        cap,out,prov=authored_case(speed=3.,half_road=20.)
        actor=out['hypotheses']['objects'][0]
        actor.update(position_m=[-8.,0.],velocity_mps=None)
        actor['alignment']['evidence_positions'][0]['position_m']=[-8.,0.]
        for premise in out['hypotheses']['premises']:
            if actor['id'] in premise['object_ids']:
                premise['space_polygon_m']=rect(-9,1,-2,2)
                premise['statement']='AUTHORED rear actor approaches inside the supplied interval BODY envelopes; no tool predicts it.'
        original=deepcopy(actor['occupancy'][0]);actor['occupancy']=[]
        for index,(left,right,front) in enumerate(((0.,1.,-6.),(1.,1.4,-3.),(1.4,2.,-1.5))):
            source=deepcopy(original)
            source.update(id=f'authored_rear_source_{index}',start_s=left,end_s=right)
            source['alternatives'][0]['polygon_m']=rect(-8.4,front,-.5,.5)
            actor['occupancy'].append(source)
        prov['output_digest']=digest(out)
        scene=t1.construct(cap,out,prov,True)
        corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='brake_straight')
        seed=corridor['construction']['seed_points']
        self.assertTrue(any(nx*seed[k]['rear_x_m']+ny*seed[k]['rear_y_m']>limit+1e-6
            for k,cell in enumerate(corridor['cells']) for nx,ny,limit in cell['halfplanes']))
        trajectory=t3.solve(scene,corridor);verified=t4.verify(scene,trajectory)
        self.assertEqual(verified['verdict'],'PASS')
        self.assertGreater(trajectory['points'][-1]['x_m'],5.)
        self.assertGreater(verified['minimum_clearance_lower_bound_m'],cap['safety']['margin_m'])
        # The hint is a bounded starting point, not an already safe solution;
        # all original occupancies and the 1 m margin still constrain T3/T4.
        self.assertEqual(scene['capture']['safety']['margin_m'],1.)

    def test_frozen_initially_unsafe_construction_exhausts_without_false_success(self):
        # Attempt 02 actually violates the 1 m initial BODY clearance. Attempt
        # 03 is not used as a "must fail" oracle: finite search exhaustion is
        # not a proof that a future better search cannot solve that scene.
        path=Path(__file__).resolve().parents[1]/'results/varied_prototypes_2s_20261028_v1/construction_yield/runtime/attempt_02/T1.json'
        scene=json.loads(path.read_text());before=digest(scene)
        winner,ledger,_=candidate_search.plan(scene)
        self.assertIsNone(winner)
        self.assertEqual(ledger['candidate_count'],5)
        self.assertEqual([row['candidate_id'] for row in ledger['candidates']],list(t2.CANDIDATE_IDS))
        self.assertTrue(all(row['T4']!='PASS' for row in ledger['candidates']))
        self.assertEqual(digest(scene),before)


if __name__=='__main__':unittest.main()
