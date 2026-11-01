"""Authored full-chain v2 regressions; never real-model success evidence."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_seed_candidates import authored_case
from agentic import t1,t2,t3,t4
from agentic.acceptance import assess_round
from agentic.contracts import Failure,digest,validate
from agentic.fixtures import rect
from agentic.fixtures_v3 import add_adverse_scenario
from agentic.geometry import obstacles,obstacles_native_cells
from agentic.runtime import SimulatedExecutor


def authored_boundary_case():
    cap,out,prov=authored_case(speed=3.,half_road=10.)
    add_adverse_scenario(out,invade=False)
    actor=out['hypotheses']['objects'][0]
    actor.update(position_m=[-6.,0.],velocity_mps=None,uncertainty_m=.2,
                 body_geometry=dict(length_m=2.,width_m=1.,yaw_rad=0.))
    actor['scenarios'][0]['role']='adopted'
    actor['alignment']['residual_m']=.1
    actor['alignment']['evidence_positions'][0]['position_m']=[-6.,0.]
    for premise in out['hypotheses']['premises']:
        premise['end_s']=2.
        if actor['id'] in premise['object_ids']:
            premise['space_polygon_m']=rect(-8.,2.,-2.,2.)
            premise['statement']='AUTHORED two bounded rear-actor modes; whole BODY sources are manually supplied.'
    out['hypotheses']['metric_scale_support']['anchors'][0]['value_m']=2.
    source=deepcopy(actor['occupancy'][0]);actor['occupancy']=[]
    for index,(left,right,front) in enumerate(((0.,.2,-4.),(.2,2.,-2.95))):
        interval=deepcopy(source)
        interval.update(id=f'authored_boundary_source_{index}',start_s=left,
                        end_s=right,residual_m=.1)
        for alternative in interval['alternatives']:
            alternative['polygon_m']=rect(-7.,front,-.5,.5)
        actor['occupancy'].append(interval)
    prov['output_digest']=digest(out)
    return cap,out,prov


class ToolchainV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cap,cls.out,cls.prov=authored_boundary_case()
        cls.scene=t1.construct(cls.cap,cls.out,cls.prov,True)
        cls.corridor=t2.build(cls.scene,policy=t2.YAW_BOX_PROFILE,
                              candidate_id='original',search_profile=t2.SEARCH_PROFILE_V2)
        cls.trajectory=t3.solve(cls.scene,cls.corridor)
        cls.verification=t4.verify(cls.scene,cls.trajectory)

    def test_native_time_support_full_chain_consumes_actual_simulated_prefix(self):
        before=digest((self.cap,self.out,self.prov,self.scene))
        with self.assertRaises(Failure) as old:
            t2.build(self.scene,policy=t2.YAW_BOX_PROFILE,candidate_id='brake_straight')
        self.assertEqual(old.exception.record['code'],'CORRIDOR_INITIAL_STATE')
        self.assertEqual(len(list(obstacles(self.scene,0))),4)
        self.assertEqual(len(list(obstacles_native_cells(self.scene,0))),2)
        self.assertEqual(self.trajectory['solver']['status'],'Solve_Succeeded')
        self.assertEqual(self.verification['verdict'],'PASS')
        self.assertGreater(self.verification['minimum_clearance_lower_bound_m'],1.)
        executor=SimulatedExecutor();executor.observe(self.cap)
        execution=executor.execute(self.scene,self.trajectory,self.verification,
                                   self.cap['metadata']['reference_time_s'])
        receipt=dict(capture_digest=digest(self.cap),scene_digest=digest(self.scene),
            trajectory_digest=digest(self.trajectory),verification_digest=digest(self.verification),
            execution_digest=digest(execution),executed_at_s=self.cap['metadata']['reference_time_s'],
            consumed_once=True)
        result=dict(status='executed_simulated_prefix',revisions=0,agent_output=self.out,
            provenance=self.prov,scene=self.scene,corridor=self.corridor,
            trajectory=self.trajectory,verification=self.verification,
            execution=execution,execution_receipt=receipt)
        acceptance=assess_round(result,self.cap)
        self.assertTrue(acceptance['software_success'],acceptance)
        self.assertFalse(acceptance['strict_live_physical_success'])
        self.assertEqual(acceptance['motion_class'],'MOVING_PREFIX')
        self.assertAlmostEqual(execution['elapsed_s'],.2)
        self.assertGreater(execution['center_displacement_m'][0],.59)
        self.assertEqual(before,digest((self.cap,self.out,self.prov,self.scene)))
        tampered=deepcopy(result);tampered['trajectory']['points'][1]['x_m']+=.1
        self.assertFalse(assess_round(tampered,self.cap)['software_success'])
        for downgrade_corridor in (False,True):
            downgraded=deepcopy(result)
            downgraded['trajectory']['search_profile']=t2.SEARCH_PROFILE
            if downgrade_corridor:downgraded['corridor']['search_profile']=t2.SEARCH_PROFILE
            self.assertFalse(assess_round(downgraded,self.cap)['software_success'])

    def test_future_adverse_body_at_source_boundary_is_still_checked(self):
        output=deepcopy(self.out);actor=output['hypotheses']['objects'][0]
        adverse=next(a for a in actor['occupancy'][1]['alternatives'] if a['scenario_id']=='adverse')
        adverse['polygon_m']=rect(-7.,2.,-.5,.5)
        provenance=deepcopy(self.prov);provenance['output_digest']=digest(output)
        scene=t1.construct(self.cap,output,provenance,True)
        trajectory=deepcopy(self.trajectory);trajectory['scene_digest']=digest(scene)
        verification=t4.verify(scene,trajectory)
        self.assertEqual(verification['verdict'],'FAIL')
        self.assertEqual(verification['released_prefix_s'],0)
        collisions=[v for v in verification['violations'] if v['code']=='BODY_COLLISION']
        self.assertTrue(collisions)
        self.assertAlmostEqual(min(v['time_interval_s'][0] for v in collisions),.2)

    def test_v1_v2_artifacts_require_explicit_matching_schema_selection(self):
        cap,out,prov=authored_case(speed=3.,half_road=10.)
        scene=t1.construct(cap,out,prov,True)
        old=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='brake_straight')
        old_trajectory=t3.solve(scene,old)
        for artifact,matching,wrong in (
            (old,'corridor_seed_candidates_v1','corridor_seed_candidates_v2'),
            (self.corridor,'corridor_seed_candidates_v2','corridor_seed_candidates_v1'),
            (old_trajectory,'trajectory_seed_candidates_v1','trajectory_seed_candidates_v2'),
            (self.trajectory,'trajectory_seed_candidates_v2','trajectory_seed_candidates_v1')):
            validate(matching+'.schema.json',artifact)
            with self.assertRaises(Failure):validate(wrong+'.schema.json',artifact)
        self.assertEqual(old['search_profile'],t2.SEARCH_PROFILE)
        self.assertEqual(self.corridor['search_profile'],t2.SEARCH_PROFILE_V2)


if __name__=='__main__':unittest.main()
