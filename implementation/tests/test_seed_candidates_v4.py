"""Time-local ego corridors with original-grid controls; no model or GT calls."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agentic import t1,t2,t3,t4,candidate_search
from agentic.acceptance import assess_round
from agentic.contracts import Failure,digest,validate
from agentic.geometry import rear_state,integrate
from agentic.runtime import SimulatedExecutor
from test_seed_candidates_v3 import scene_for,trajectory_v3

ROOT=Path(__file__).resolve().parents[2]
REAL=ROOT/'implementation/results/hard_clip_baseline_20261030_v1/urban_interaction/runtime/frame_036/attempt_02/T1.json'

class SeedCandidatesV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cap,cls.out,cls.prov,cls.scene=scene_for(speed=3.)
        cls.corridor=t2.build(cls.scene,policy=t2.YAW_BOX_PROFILE,candidate_id='early_right_brake',search_profile=t2.SEARCH_PROFILE_V4)
        cls.trajectory=t3.solve(cls.scene,cls.corridor)
        cls.verification=t4.verify(cls.scene,cls.trajectory)

    def test_old_five_references_and_old_v3_corridor_stay_identical(self):
        for cid in t2.CANDIDATE_IDS:
            self.assertEqual(t2.candidate_seed_v4(self.scene,cid),t2.candidate_seed_v3(self.scene,cid))
        old=t2.build(self.scene,policy=t2.YAW_BOX_PROFILE,candidate_id='original',search_profile=t2.SEARCH_PROFILE_V3)
        new=t2.build(self.scene,policy=t2.YAW_BOX_PROFILE,candidate_id='original',search_profile=t2.SEARCH_PROFILE_V4)
        self.assertEqual(new.pop('corridor_substeps'),1);new['search_profile']=t2.SEARCH_PROFILE_V3
        self.assertEqual(old,new)

    def test_extra_references_have_bounded_original_grid_controls_and_exact_children(self):
        for speed in (0.,3.,8.,12.):
            _,_,_,scene=scene_for(speed=speed);vehicle=scene['capture']['vehicle'];before=deepcopy(scene)
            for cid in t2.CANDIDATE_IDS_V4[5:]:
                seed=t2.candidate_seed_v4(scene,cid)
                self.assertEqual(len(seed),41)
                self.assertEqual([seed[0][n] for n in ('rear_x_m','rear_y_m','yaw_rad','speed_mps')],rear_state(scene['capture']['ego'],vehicle))
                for j,(a,b) in enumerate(zip(seed,seed[1:])):
                    self.assertGreaterEqual(b['speed_mps'],-1e-10)
                    self.assertLessEqual(abs(a['steer_rad']),vehicle['max_steer_rad']+1e-10)
                    self.assertLessEqual(abs(math.tan(a['steer_rad'])/vehicle['wheelbase_m']),vehicle['max_curvature_per_m']+1e-10)
                    self.assertGreaterEqual(a['acceleration_mps2'],-vehicle['max_brake_mps2']-1e-10)
                    if j%4:self.assertEqual([a[n] for n in ('acceleration_mps2','steer_rad')],[seed[j-j%4][n] for n in ('acceleration_mps2','steer_rad')])
                    q=integrate([a[n] for n in ('rear_x_m','rear_y_m','yaw_rad','speed_mps')],a['acceleration_mps2'],a['steer_rad'],b['time_s']-a['time_s'],vehicle)
                    for want,actual in zip(q,[b[n] for n in ('rear_x_m','rear_y_m','yaw_rad','speed_mps')]):self.assertAlmostEqual(want,actual,places=9)
            self.assertEqual(scene,before)

    def test_temporal_children_retain_complete_parent_occupancy_indices(self):
        original=t2.obstacles_native_cells;seen=[]
        def record(scene,k):
            seen.append(k);return original(scene,k)
        with patch.object(t2,'obstacles_native_cells',side_effect=record):
            t2.build(self.scene,policy=t2.YAW_BOX_PROFILE,candidate_id='early_right_brake',search_profile=t2.SEARCH_PROFILE_V4)
        self.assertEqual(seen,[k for k in range(10) for _ in range(4)])

    def test_original_eleven_states_same_controls_meet_all_child_constraints(self):
        tr=self.trajectory;co=self.corridor;v=self.cap['vehicle']
        self.assertEqual(len(tr['points']),11);self.assertEqual(len(co['cells']),40)
        self.assertEqual(tr['corridor_substeps'],4);self.assertEqual(len(tr['heading_bands_rad']),40)
        for k,p in enumerate(tr['points'][:-1]):
            q=[p[n] for n in ('rear_x_m','rear_y_m','yaw_rad','speed_mps')];a=p['acceleration_mps2'];delta=p['steer_rad'];curv=math.tan(delta)/v['wheelbase_m']
            for j in range(4):
                first=integrate(q,a,delta,.05*j,v);last=integrate(q,a,delta,.05*(j+1),v);cell=co['cells'][4*k+j]
                lo,hi=cell['heading_band_rad']
                for state in (first,last):self.assertTrue(lo-1e-7<=state[2]<=hi+1e-7)
                length=(first[3]+last[3])*.025;pad=abs(curv)*length*length/8
                for nx,ny,b in cell['halfplanes']:
                    self.assertLessEqual(max(nx*first[0]+ny*first[1],nx*last[0]+ny*last[1])+pad,b+2e-7)
        self.assertEqual(self.verification['verdict'],'PASS')

    def test_schema_requires_explicit_profile_and_subdivision(self):
        validate('corridor_seed_candidates_v4.schema.json',self.corridor)
        validate('trajectory_seed_candidates_v4.schema.json',self.trajectory)
        with self.assertRaises(Failure):validate('corridor_seed_candidates_v3.schema.json',self.corridor)
        with self.assertRaises(Failure):validate('trajectory_seed_candidates_v3.schema.json',self.trajectory)
        bad=deepcopy(self.trajectory);bad.pop('corridor_substeps')
        with self.assertRaises(Failure):t4.verify(self.scene,bad)

    def test_missing_or_retimed_child_coverage_is_not_accepted(self):
        bad=deepcopy(self.trajectory);bad['heading_bands_rad'].pop()
        with self.assertRaises(Failure) as failure:t4.verify(self.scene,bad)
        self.assertEqual(failure.exception.record['code'],'HEADING_COVERAGE')
        bad=deepcopy(self.corridor);bad['cells'][1]['start_s']+=.001
        with self.assertRaises(Failure) as failure:t3.solve(self.scene,bad)
        self.assertEqual(failure.exception.record['code'],'CORRIDOR_COVERAGE')

    def test_independent_verifier_checks_yaw_between_original_grid_points(self):
        bad=deepcopy(self.trajectory);bad['heading_bands_rad'][2]=[.001,.002]
        report=t4.verify(self.scene,bad)
        self.assertEqual(report['verdict'],'FAIL');self.assertEqual(report['released_prefix_s'],0)
        self.assertTrue(any(e['code']=='CANDIDATE_HEADING_BOUND' and abs(e['time_interval_s'][0]-.1)<1e-9 for e in report['violations']))

    def test_future_intrusion_into_stationary_ego_still_rejects_all_adverse_modes(self):
        _,_,_,scene=scene_for(speed=0.,future=True)
        tr=trajectory_v3(scene);tr.update(search_profile=t2.SEARCH_PROFILE_V4,corridor_substeps=4,
            heading_bands_rad=[[-.001,.001] for _ in range(40)])
        report=t4.verify(scene,tr)
        self.assertEqual(report['verdict'],'FAIL');self.assertEqual(report['released_prefix_s'],0)
        self.assertTrue(any(e['code']=='BODY_COLLISION' for e in report['violations']))

    def test_authored_chain_consumes_and_accepts_with_substep_tamper_rejected(self):
        ex=SimulatedExecutor();ex.observe(self.cap)
        execution=ex.execute(self.scene,self.trajectory,self.verification,self.cap['metadata']['reference_time_s'])
        receipt=dict(capture_digest=digest(self.cap),scene_digest=digest(self.scene),trajectory_digest=digest(self.trajectory),verification_digest=digest(self.verification),execution_digest=digest(execution),executed_at_s=self.cap['metadata']['reference_time_s'],consumed_once=True)
        result=dict(status='executed_simulated_prefix',revisions=0,agent_output=self.out,provenance=self.prov,scene=self.scene,corridor=self.corridor,trajectory=self.trajectory,verification=self.verification,execution=execution,execution_receipt=receipt)
        assessment=assess_round(result,self.cap)
        self.assertTrue(assessment['software_success'],assessment);self.assertFalse(assessment['strict_live_physical_success'])
        bad=deepcopy(result);bad['trajectory']['corridor_substeps']=1
        self.assertFalse(assess_round(bad,self.cap)['software_success'])

    def test_fixed_real_frame036_gains_feasible_optimizer_and_raw_t4_without_scene_changes(self):
        if not REAL.exists():self.skipTest('Optional frozen saved real scene absent')
        scene=json.loads(REAL.read_text());before=deepcopy(scene)
        for cid in ('early_right_brake','early_right_moderate'):
            corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id=cid,search_profile=t2.SEARCH_PROFILE_V4)
            trajectory=t3.solve(scene,corridor);verification=t4.verify(scene,trajectory)
            self.assertEqual(verification['verdict'],'PASS',verification)
            self.assertGreaterEqual(verification['minimum_clearance_lower_bound_m'],1.)
            self.assertEqual(len(trajectory['points']),11)
        self.assertEqual(scene,before)
        # No model, executor or acceptor in this real-output regression.

    def test_progress_reference_acceleration_respects_speed_and_stricter_limits(self):
        for speed,limit,maximum_acceleration in ((0.,12.,2.),(8.698383977895483,12.,2.),(11.95,12.,2.),(12.,12.,2.),(3.9,4.,.2)):
            cap,out,prov,_=scene_for(speed=speed)
            cap['vehicle'].update(max_speed_mps=limit,max_accel_mps2=maximum_acceleration)
            scene=t1.construct(cap,out,prov,True);before=deepcopy(scene)
            points=t2.candidate_seed_v4(scene,'early_right_progress')
            self.assertEqual(len(points),41)
            for j,(a,b) in enumerate(zip(points,points[1:])):
                self.assertTrue(0<=a['acceleration_mps2']<=min(1.,maximum_acceleration)+1e-12)
                self.assertTrue(0<=b['speed_mps']<=limit+1e-12)
                self.assertAlmostEqual(b['speed_mps'],a['speed_mps']+a['acceleration_mps2']*(b['time_s']-a['time_s']),places=10)
                if j%4:self.assertEqual(a['acceleration_mps2'],points[j-j%4]['acceleration_mps2'])
            self.assertEqual(scene,before)
        self.assertEqual(len(t2.CANDIDATE_IDS_V4),8)

    def test_progress_reference_recovers_real_frame006_and_keeps_future_intrusion_rejection(self):
        path=ROOT/'implementation/results/failure_followup_20261030_v1/urban006_temporal_revisit/runtime/frame_006/attempt_02/T1.json'
        if not path.exists():self.skipTest('Optional new saved real scene absent')
        scene=json.loads(path.read_text());before=deepcopy(scene)
        corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='early_right_progress',search_profile=t2.SEARCH_PROFILE_V4)
        trajectory=t3.solve(scene,corridor);verification=t4.verify(scene,trajectory)
        self.assertEqual(verification['verdict'],'PASS',verification)
        self.assertGreaterEqual(verification['minimum_clearance_lower_bound_m'],1.-1e-6)
        self.assertEqual(len(trajectory['points']),11)
        self.assertEqual(scene,before)
        # A separate authored adverse future must still block this new family.
        _,_,_,invaded=scene_for(speed=0.,future=True)
        invaded_corridor=t2.build(invaded,policy=t2.YAW_BOX_PROFILE,candidate_id='early_right_progress',search_profile=t2.SEARCH_PROFILE_V4)
        try:
            invaded_trajectory=t3.solve(invaded,invaded_corridor)
        except Failure as failure:
            self.assertEqual(failure.record['code'],'OPTIMIZATION_FAILED')
        else:
            self.assertEqual(t4.verify(invaded,invaded_trajectory)['verdict'],'FAIL')
        reference=trajectory_v3(invaded);reference.update(search_profile=t2.SEARCH_PROFILE_V4,corridor_substeps=4,
            heading_bands_rad=[[-.001,.001] for _ in range(40)])
        rejected=t4.verify(invaded,reference)
        self.assertEqual(rejected['verdict'],'FAIL');self.assertEqual(rejected['released_prefix_s'],0.)
        self.assertTrue(any(v['code']=='BODY_COLLISION' for v in rejected['violations']))

if __name__=='__main__':unittest.main()
