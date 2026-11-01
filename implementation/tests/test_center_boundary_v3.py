"""Independent CENTER boundary and center-curve bounds; no model calls."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agentic import t1,t2,t3,t4
from agentic.contracts import Failure,digest
from agentic.fixtures import rect
from test_seed_candidates import authored_case,authored_trajectory

ROOT=Path(__file__).resolve().parents[2]

def boundary_scene(speed=2.,xmin=0.):
    cap,out,prov=authored_case(speed=speed,half_road=20.)
    out['decision']['target_region']=rect(xmin,6.,-.3,.3)
    prov['output_digest']=digest(out)
    return t1.construct(cap,out,prov,True)

class CenterBoundaryV3Tests(unittest.TestCase):
    def test_center_chord_bound_encloses_independent_whole_arc_samples(self):
        comparisons=0
        for k in (-.2,-.05,0.,.05,.2):
            for distance in (.02,.5,2.4):
                for offset in (0.,1.2,2.):
                    theta=.71
                    def independent_center(s):
                        x=(math.sin(theta+k*s)-math.sin(theta))/k if k else s*math.cos(theta)
                        y=(math.cos(theta)-math.cos(theta+k*s))/k if k else s*math.sin(theta)
                        return (x+offset*math.cos(theta+k*s),y+offset*math.sin(theta+k*s))
                    first,last=independent_center(0.),independent_center(distance)
                    bound=t4.center_chord_error_bound(k,distance,offset)
                    for j in range(101):
                        u=j/100;point=independent_center(distance*u)
                        chord=[(1-u)*first[i]+u*last[i] for i in (0,1)]
                        self.assertLessEqual(math.hypot(point[0]-chord[0],point[1]-chord[1]),bound+1e-12)
                        comparisons+=1
        self.assertEqual(comparisons,4545)

    def test_body_offset_factor_is_necessary_not_rear_arc_bound(self):
        k=.2;length=1.;offset=1.2
        actual_midpoint_gap=math.hypot(1/k,offset)*(1-math.cos(k*length/2))
        rear_only_bound=abs(k)*length*length/8
        self.assertGreater(actual_midpoint_gap,rear_only_bound)
        self.assertLessEqual(actual_midpoint_gap,t4.center_chord_error_bound(k,length,offset))

    def test_closed_target_legal_initial_state_tightens_only_first_straight_band(self):
        scene=boundary_scene();before=deepcopy(scene)
        for cid in ('original','brake_straight'):
            with self.assertRaises(Failure) as old:
                t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id=cid,search_profile=t2.SEARCH_PROFILE_V2)
            self.assertEqual(old.exception.record['code'],'CORRIDOR_INITIAL_STATE')
            corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id=cid,search_profile=t2.SEARCH_PROFILE_V3)
            self.assertEqual(corridor['cells'][0]['heading_band_rad'],[0.,0.])
            seed=t2.candidate_seed_v3(scene,cid)
            for i,cell in enumerate(corridor['cells'][1:],1):
                self.assertEqual(cell['heading_band_rad'],t2.candidate_band(seed[i],seed[i+1]))
            tr=t3.solve(scene,corridor);report=t4.verify(scene,tr)
            self.assertEqual(tr['solver']['status'],'Solve_Succeeded')
            self.assertAlmostEqual(tr['points'][1]['yaw_rad'],0.,places=10)
            self.assertEqual(report['verdict'],'PASS',report)
        self.assertEqual(scene,before)

    def test_illegal_actual_center_is_not_admitted_and_s_band_is_unchanged(self):
        bad=boundary_scene(xmin=.01)
        for cid in ('original','brake_straight'):
            with self.assertRaises(Failure):t2.build(bad,policy=t2.YAW_BOX_PROFILE,candidate_id=cid,search_profile=t2.SEARCH_PROFILE_V3)
        good=boundary_scene()
        for cid in ('brake_right','brake_left','brake_right_strong'):
            seed=t2.candidate_seed_v3(good,cid);expected=t2.candidate_band(seed[0],seed[1])
            with self.assertRaises(Failure) as failure:t2.build(good,policy=t2.YAW_BOX_PROFILE,candidate_id=cid,search_profile=t2.SEARCH_PROFILE_V3)
            self.assertEqual(failure.exception.record['details']['heading_band_rad'],expected)
            self.assertNotEqual(expected,[0.,0.])

    def test_zero_initial_speed_closed_boundary_remains_feasible(self):
        scene=boundary_scene(speed=0.)
        corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='brake_straight',search_profile=t2.SEARCH_PROFILE_V3)
        tr=t3.solve(scene,corridor);report=t4.verify(scene,tr)
        self.assertEqual(report['verdict'],'PASS',report)
        self.assertAlmostEqual(tr['points'][0]['speed_mps'],0.,places=10)
        self.assertTrue(all(p['speed_mps']>=-1e-8 for p in tr['points']))

    def test_exact_initial_yaw_membership_is_checked_before_optimization(self):
        scene=boundary_scene();corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='original',search_profile=t2.SEARCH_PROFILE_V3)
        bad=deepcopy(corridor);bad['cells'][0]['heading_band_rad']=[.01,.01]
        with self.assertRaises(Failure) as rejected:t3.solve(scene,bad)
        self.assertEqual(rejected.exception.record['code'],'CORRIDOR_HEADING_BOUND')

    def test_real_urban_frame020_fixed_scene_recovers_only_t2_t3_t4_evidence(self):
        path=ROOT/'implementation/results/hard_clip_baseline_20261030_v1/urban_interaction/runtime/frame_020/attempt_02/T1.json'
        if not path.exists():self.skipTest('Optional frozen real scene unavailable')
        scene=json.loads(path.read_text());before=deepcopy(scene)
        for cid in ('original','brake_straight'):
            co=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id=cid,search_profile=t2.SEARCH_PROFILE_V3)
            tr=t3.solve(scene,co);ver=t4.verify(scene,tr)
            self.assertEqual(ver['verdict'],'PASS',ver)
            self.assertGreaterEqual(ver['minimum_clearance_lower_bound_m'],1.)
            self.assertEqual(co['cells'][0]['heading_band_rad'],[0.,0.])
        self.assertEqual(scene,before)
        # This test never invokes executor or acceptor; no full-chain claim.

if __name__=='__main__':unittest.main()
