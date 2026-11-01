"""Actual authored optimizer checks for turning vs safe straight motion."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agentic import t1,t2,candidate_search
from agentic.contracts import digest
from agentic.turn_task import evaluate_turn
from test_seed_candidates import authored_case


class TurnTaskTests(unittest.TestCase):
    def test_actual_authored_curve_passes_t4_and_turning_description_without_mutation(self):
        cap,out,prov=authored_case(speed=4.,half_road=20.)
        out['decision']['target_region']=[[-1,-.25],[12,-1.55],[12,-.85],[-1,.45]]
        prov['output_digest']=digest(out);scene=t1.construct(cap,out,prov,True)
        result,_,_=candidate_search.plan(scene,profile=t2.SEARCH_PROFILE_V2)
        self.assertIsNotNone(result);self.assertEqual(result['T4']['verdict'],'PASS')
        before=deepcopy((scene,result['T3']))
        turn=evaluate_turn(scene,result['T3'])
        self.assertEqual(turn['verdict'],'PASS',turn)
        self.assertGreater(turn['measurements']['plan_max_abs_yaw_change_deg'],5.)
        self.assertGreater(turn['measurements']['plan_max_abs_center_lateral_displacement_m'],.3)
        self.assertEqual((scene,result['T3']),before)

    def test_actual_safe_straight_plan_does_not_demonstrate_turning(self):
        cap,out,prov=authored_case(speed=4.,half_road=20.)
        scene=t1.construct(cap,out,prov,True)
        result,_,_=candidate_search.plan(scene,profile=t2.SEARCH_PROFILE_V2)
        self.assertIsNotNone(result);self.assertEqual(result['T4']['verdict'],'PASS')
        turn=evaluate_turn(scene,result['T3'])
        self.assertEqual(turn['verdict'],'FAIL')
        self.assertEqual(turn['failure_code'],'TURN_TASK_NOT_DEMONSTRATED')
        self.assertFalse(turn['checks']['plan_yaw']);self.assertFalse(turn['checks']['plan_lateral'])
        self.assertTrue(turn['checks']['prefix_moving'])


if __name__=='__main__':unittest.main()
