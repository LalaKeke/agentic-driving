"""Two focused feedback checks; authored fixture and saved real JSON, no calls/GT."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'));sys.path.insert(0,str(ROOT/'implementation/tests'))
from agentic import t1,t2
from agentic.contracts import Failure,digest
from agentic.fixtures import rect
from test_toolchain_v2 import authored_boundary_case

class T2FeedbackDetailsTests(unittest.TestCase):
    def test_failed_constraints_include_native_sources_and_initial_state_evidence(self):
        base=ROOT/'implementation/results/scene_expansion_20261029_v1/runtime/pedestrian_interaction/attempt_01'
        scene=json.loads((base/'T1.json').read_text());before=digest(scene)
        with self.assertRaises(Failure) as caught:
            t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='original',search_profile=t2.SEARCH_PROFILE_V2)
        failure=caught.exception.record;d=failure['details'];entry=d['first_conflicting_constraint']
        self.assertEqual(failure['code'],'CORRIDOR_EMPTY')
        self.assertEqual(d['object_id'],'following_car');self.assertEqual(d['interval'],[0.,.2])
        self.assertEqual(entry['native_sources'][0]['source_id'],'following_body_0_2')
        self.assertEqual(entry['native_sources'][0]['scenario_id'],'following_continue')
        self.assertEqual(entry['native_sources'][0]['frame_ids'],['c9436084f6ad4894b3a4726c07a84c97'])
        self.assertEqual(entry['model_total_residual_m'],1.1);self.assertEqual(entry['safety_margin_m'],1.)
        self.assertAlmostEqual(entry['halfplane'][2],entry['obstacle_support_m']-entry['ego_body_support_m']-1.1-1.)
        self.assertGreater(d['allowed_area_before_m2'],0.);self.assertEqual(d['area_after_m2'],0.)
        stop=next(x for x in d['base_constraints'] if x['kind']=='stop')['halfplane']
        self.assertGreater(-entry['halfplane'][2],stop[2])
        self.assertEqual(before,digest(scene))
        cap,out,prov=authored_boundary_case()
        for alt in out['hypotheses']['objects'][0]['occupancy'][0]['alternatives']:
            alt['polygon_m']=rect(-7.,-2.95,-.5,.5)
        prov['output_digest']=digest(out);authored=t1.construct(cap,out,prov,True)
        with self.assertRaises(Failure) as caught:
            t2.build(authored,policy=t2.YAW_BOX_PROFILE,candidate_id='original',search_profile=t2.SEARCH_PROFILE_V2)
        self.assertEqual(caught.exception.record['code'],'CORRIDOR_INITIAL_STATE')
        violations=caught.exception.record['details']['initial_state_violations']
        self.assertTrue(violations);self.assertTrue(any(x['kind']=='occupancy' and x['native_sources'] for x in violations))
        self.assertTrue(all(x['computed_m']>x['threshold_m'] for x in violations))

    def test_successful_artifact_and_legacy_error_stay_value_identical(self):
        base=ROOT/'implementation/results/toolchain_optimization_20261029_v1'
        for case in ('parking','bus'):
            folder=base/'improved_final'/case
            scene=json.loads((folder/'T1.json').read_text());expected=json.loads((folder/'T2.json').read_text())
            actual=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id=expected['construction']['candidate_id'],search_profile=t2.SEARCH_PROFILE_V2)
            self.assertEqual(actual,expected)
        folder=base/'baseline_final/straight_revision'
        scene=json.loads((folder/'T1.json').read_text())
        expected=json.loads((folder/'candidates/brake_straight/T2-failure.json').read_text())
        with self.assertRaises(Failure) as caught:
            t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='brake_straight')
        self.assertEqual(caught.exception.record,expected)

if __name__=='__main__':unittest.main()
