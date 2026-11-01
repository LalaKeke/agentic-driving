"""Focused v3 software counterexamples. No model calls or online success claims."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agentic import t1,t2,t3,t4,candidate_search
from agentic.acceptance import assess_round
from agentic.contracts import Failure,digest,validate
from agentic.fixtures import rect
from agentic.geometry import rear_state
from agentic.runtime import SimulatedExecutor
from test_seed_candidates import authored_case,authored_trajectory

ROOT=Path(__file__).resolve().parents[2]

def scene_for(speed=3.,future=False,target_min_x=-8.):
    cap,out,prov=authored_case(speed=speed,half_road=20.,future_intrusion=future)
    out['decision']['target_region']=rect(target_min_x,30,-20,20)
    prov['output_digest']=digest(out)
    return cap,out,prov,t1.construct(cap,out,prov,True)

def trajectory_v3(scene,curvature=0.):
    trajectory=authored_trajectory(scene,curvature=curvature)
    trajectory.update(search_profile=t2.SEARCH_PROFILE_V3,
        verification_profile='continuous_oriented_body_adaptive_v1')
    return trajectory

class SeedCandidatesV3Tests(unittest.TestCase):
    def test_original_and_brake_straight_are_exact_v2_seed_values(self):
        _,_,_,scene=scene_for(speed=8.)
        for name in ('original','brake_straight'):
            self.assertEqual(t2.candidate_seed_v3(scene,name),t2.candidate_seed(scene,name))

    def test_five_s_references_obey_independent_dynamics_and_global_bounds(self):
        for speed in (0.,3.,8.,12.):
            cap,out,prov,scene=scene_for(speed=speed);before=deepcopy(scene);v=cap['vehicle']
            for name in t2.CANDIDATE_IDS:
                points=t2.candidate_seed_v3(scene,name)
                self.assertEqual([points[0][k] for k in ('rear_x_m','rear_y_m','yaw_rad','speed_mps')],rear_state(cap['ego'],v))
                for a,b in zip(points,points[1:]):
                    dt=b['time_s']-a['time_s'];acc=a['acceleration_mps2'];delta=a['steer_rad'];k=math.tan(delta)/v['wheelbase_m']
                    self.assertTrue(-v['max_brake_mps2']-1e-10<=acc<=v['max_accel_mps2']+1e-10)
                    self.assertLessEqual(abs(delta),v['max_steer_rad']+1e-10)
                    self.assertLessEqual(abs(k),v['max_curvature_per_m']+1e-10)
                    self.assertGreaterEqual(b['speed_mps'],-1e-10)
                    self.assertLessEqual(abs(b['yaw_rad']),t2.YAW_BOX_BOUND_RAD+1e-10)
                    distance=a['speed_mps']*dt+.5*acc*dt*dt;yaw=a['yaw_rad']
                    ex=a['rear_x_m']+((math.sin(yaw+k*distance)-math.sin(yaw))/k if abs(k)>1e-12 else distance*math.cos(yaw))
                    ey=a['rear_y_m']+((math.cos(yaw)-math.cos(yaw+k*distance))/k if abs(k)>1e-12 else distance*math.sin(yaw))
                    self.assertAlmostEqual(b['rear_x_m'],ex,places=8)
                    self.assertAlmostEqual(b['rear_y_m'],ey,places=8)
                    self.assertAlmostEqual(b['speed_mps'],a['speed_mps']+acc*dt,places=9)
            self.assertEqual(scene,before)
        self.assertEqual(len(t2.CANDIDATE_IDS),5)

    def test_v3_seed_respects_stricter_vehicle_control_limits(self):
        cap,out,prov,_=scene_for(speed=8.)
        cap['vehicle'].update(max_steer_rad=.04,max_curvature_per_m=.01)
        scene=t1.construct(cap,out,prov,True)
        for name in t2.CANDIDATE_IDS:
            for p in t2.candidate_seed_v3(scene,name):
                self.assertLessEqual(abs(p['steer_rad']),.04+1e-12)
                self.assertLessEqual(abs(math.tan(p['steer_rad'])/cap['vehicle']['wheelbase_m']),.01+1e-12)

    def test_v2_saved_corridor_and_verdict_remain_value_identical(self):
        saved=ROOT/'implementation/results/long_clips_20261029_v2/following_deceleration/runtime/frame_004/attempt_02'
        if not saved.is_dir():self.skipTest('Optional original saved real record unavailable')
        scene=json.loads((saved/'T1.json').read_text());expected=json.loads((saved/'T2.json').read_text())
        corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='original',search_profile=t2.SEARCH_PROFILE_V2)
        self.assertEqual(corridor,expected)
        trajectory=json.loads((saved/'T3.json').read_text())
        self.assertEqual(t4.verify(scene,trajectory),json.loads((saved/'T4.json').read_text()))
        self.assertNotIn('continuous_refinement',t4.verify(scene,trajectory))

    def test_explicit_v3_schema_and_verifier_pair_cannot_downgrade(self):
        _,_,_,scene=scene_for()
        corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='original',search_profile=t2.SEARCH_PROFILE_V3)
        validate('corridor_seed_candidates_v3.schema.json',corridor)
        with self.assertRaises(Failure):validate('corridor_seed_candidates_v2.schema.json',corridor)
        trajectory=trajectory_v3(scene)
        validate('trajectory_seed_candidates_v3.schema.json',trajectory)
        bad=deepcopy(trajectory);bad['verification_profile']='continuous_oriented_body_v1'
        with self.assertRaises(Failure):t4.verify(scene,bad)

    def test_center_chord_bound_resolves_safe_near_initial_target_bound(self):
        _,_,_,scene=scene_for(speed=6.,target_min_x=-.05)
        old=trajectory_v3(scene);old.update(search_profile=t2.SEARCH_PROFILE_V2,verification_profile='continuous_oriented_body_v1')
        coarse=t4.verify(scene,old)
        self.assertEqual(coarse['verdict'],'FAIL')
        self.assertIn('TARGET_INTENT',{e['code'] for e in coarse['violations']})
        report=t4.verify(scene,trajectory_v3(scene))
        self.assertEqual(report['verdict'],'PASS',report)
        self.assertEqual(report['continuous_refinement']['refined_nodes'],0)
        self.assertLessEqual(report['continuous_refinement']['max_depth'],3)
        self.assertEqual(report['released_prefix_s'],.2)

    def test_exact_stop_deadline_failure_is_not_a_geometry_refinement(self):
        cap,out,prov,_=scene_for(speed=6.)
        out['decision'].update(behavior='stop_yield',stop=dict(center_x_max_m=30.,deadline_s=.4),
            yield_to=['pedestrian'],desired_speed_mps=0.)
        out['decision']['hard_requirements']+=['stop','yield']
        prov['output_digest']=digest(out);scene=t1.construct(cap,out,prov,True)
        report=t4.verify(scene,trajectory_v3(scene))
        self.assertEqual(report['verdict'],'FAIL');self.assertEqual(report['released_prefix_s'],0.)
        self.assertIn('STOP_DEADLINE',{e['code'] for e in report['violations']})
        self.assertEqual(report['continuous_refinement']['refined_nodes'],0)

    def test_wide_safe_geometry_does_not_refine(self):
        _,_,_,scene=scene_for(speed=1.)
        report=t4.verify(scene,trajectory_v3(scene))
        self.assertEqual(report['verdict'],'PASS')
        self.assertEqual(report['continuous_refinement']['refined_nodes'],0)
        self.assertEqual(report['continuous_refinement']['max_depth'],0)

    def test_future_adverse_intrusion_keeps_all_parent_occupancy_and_rejects(self):
        _,_,_,scene=scene_for(speed=0.,future=True);before=deepcopy(scene)
        report=t4.verify(scene,trajectory_v3(scene))
        self.assertEqual(scene,before)
        self.assertEqual(report['verdict'],'FAIL');self.assertEqual(report['released_prefix_s'],0.)
        self.assertTrue(any(e['code']=='BODY_COLLISION' and e['time_interval_s'][0]>=.6-1e-8 for e in report['violations']))
        self.assertLessEqual(report['continuous_refinement']['max_depth'],3)

    def test_initial_one_meter_margin_violation_is_not_refined_away(self):
        cap,out,prov,_=scene_for(speed=0.)
        out['hypotheses']['unknown_regions']=[dict(polygon_m=rect(3.2,3.5,-.1,.1),uncertainty_m=.4,
            frame_ids=[cap['observations'][0]['id']],time_ids=[cap['metadata']['reference_time_id']])]
        prov['output_digest']=digest(out);scene=t1.construct(cap,out,prov,True)
        report=t4.verify(scene,trajectory_v3(scene))
        self.assertEqual(report['verdict'],'FAIL');self.assertEqual(report['released_prefix_s'],0.)
        self.assertTrue(any(e['code']=='BODY_COLLISION' and e['time_interval_s'][0]==0 for e in report['violations']))

    def test_between_grid_collision_still_rejects_with_one_meter_margin(self):
        cap,out,prov,_=scene_for(speed=12.)
        cap['vehicle'].update(front_m=.05,rear_m=.05,half_width_m=.02,center_to_rear_axle_m=.02,wheelbase_m=.1)
        out['hypotheses']['unknown_regions']=[dict(polygon_m=rect(1.19,1.21,-.01,.01),uncertainty_m=.01,
            frame_ids=[cap['observations'][0]['id']],time_ids=[cap['metadata']['reference_time_id']])]
        prov['output_digest']=digest(out);scene=t1.construct(cap,out,prov,True)
        trajectory=trajectory_v3(scene)
        from shapely.geometry import box
        obstacle=box(1.19,-.01,1.21,.01)
        for point in trajectory['points'][:2]:
            body=t4.oriented_body([point[k] for k in ('rear_x_m','rear_y_m','yaw_rad','speed_mps')],cap['vehicle'])
            self.assertGreater(body.distance(obstacle)-.01,1.)
        report=t4.verify(scene,trajectory)
        self.assertEqual(report['verdict'],'FAIL');self.assertEqual(report['released_prefix_s'],0.)
        self.assertTrue(any(e['code']=='BODY_COLLISION' and 0<e['time_interval_s'][0]<.2 for e in report['violations']))

    def test_full_authored_v3_chain_and_acceptor_are_software_only(self):
        cap,out,prov,scene=scene_for(speed=3.)
        winner,ledger,failure=candidate_search.plan(scene,profile=t2.SEARCH_PROFILE_V3)
        self.assertIsNotNone(winner,ledger);self.assertIsNone(failure)
        self.assertEqual(ledger['candidate_count'],5)
        corridor,trajectory,verification=[winner[k] for k in ('T2','T3','T4')]
        self.assertEqual(trajectory['heading_bands_rad'],[c['heading_band_rad'] for c in corridor['cells']])
        executor=SimulatedExecutor();executor.observe(cap)
        execution=executor.execute(scene,trajectory,verification,cap['metadata']['reference_time_s'])
        receipt=dict(capture_digest=digest(cap),scene_digest=digest(scene),trajectory_digest=digest(trajectory),
            verification_digest=digest(verification),execution_digest=digest(execution),executed_at_s=cap['metadata']['reference_time_s'],consumed_once=True)
        result=dict(status='executed_simulated_prefix',revisions=0,agent_output=out,provenance=prov,
            scene=scene,corridor=corridor,trajectory=trajectory,verification=verification,execution=execution,execution_receipt=receipt)
        accepted=assess_round(result,cap)
        self.assertTrue(accepted['software_success'],accepted);self.assertFalse(accepted['strict_live_physical_success'])
        bad=deepcopy(result);bad['trajectory'].update(search_profile=t2.SEARCH_PROFILE_V2,verification_profile='continuous_oriented_body_v1')
        self.assertFalse(assess_round(bad,cap)['software_success'])

if __name__=='__main__':unittest.main()
