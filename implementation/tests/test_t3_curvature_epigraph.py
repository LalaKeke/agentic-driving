"""Fixed-output solver comparisons; no model calls or changes to model geometry."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic import t1,t2,t3,t4
from agentic.contracts import Failure,digest
from test_seed_candidates import authored_case

ROOT=Path(__file__).resolve().parents[2]
REPLAYS=ROOT/'implementation/results/construction_recovery_20261029_v1/final_replays'


def fixed_case(name):
    if name=='following':
        root=ROOT/'implementation/results/six_scene_scout_run_20261029_v1/runtime/following_deceleration/attempt_02'
    else:root=REPLAYS/name/'attempt_01'
    return (json.loads((root/'T1.json').read_text()),
            json.loads((root/'candidates/brake_straight/T2.json').read_text()))


def epigraph_corridor(corridor):
    # Explicit offline planner-artifact selection, never a conversion of a
    # model's JSON or a relabeling of its historical execution result.
    result=deepcopy(corridor)
    result['search_profile']='bounded_seed_candidates_v2'
    return result


class CurvatureEpigraphTests(unittest.TestCase):
    def assert_magnitude_bounds(self,scene,trajectory):
        bounds=trajectory['solver']['curvature_magnitude_bounds_per_m']
        self.assertEqual(len(bounds),len(trajectory['points'])-1)
        for bound,point in zip(bounds,trajectory['points'][:-1]):
            # Compare against the emitted physical control, independently of
            # Opti's internal constraint status or success flag.
            curvature=math.tan(point['steer_rad'])/scene['capture']['vehicle']['wheelbase_m']
            self.assertGreaterEqual(bound+1e-7,abs(curvature))
            self.assertLessEqual(bound,scene['capture']['vehicle']['max_curvature_per_m']+1e-7)

    def test_feasible_parking_seed_recovers_without_changing_model_or_constraints(self):
        scene,old=fixed_case('parking');before=digest(scene)
        seed=old['construction']['seed_points']
        # The stored warm start genuinely satisfies the geometric inequalities;
        # its numerical rejection is not evidence that this corridor is empty.
        for k,cell in enumerate(old['cells']):
            for point in seed[k:k+2]:
                for nx,ny,limit in cell['halfplanes']:
                    self.assertLess(nx*point['rear_x_m']+ny*point['rear_y_m']-limit,-1.)
        with self.assertRaises(Failure) as failure:t3.solve(scene,old)
        self.assertEqual(failure.exception.record['details']['solver_status'],'Maximum_Iterations_Exceeded')
        new=epigraph_corridor(old)
        trajectory=t3.solve(scene,new)
        self.assertEqual(trajectory['solver']['status'],'Solve_Succeeded')
        self.assertEqual(trajectory['solver']['arc_bound'],'curvature_epigraph_v1')
        self.assert_magnitude_bounds(scene,trajectory)
        self.assertEqual(t4.verify(scene,trajectory)['verdict'],'PASS')
        self.assertEqual(before,digest(scene))
        restored=deepcopy(new);restored['search_profile']=old['search_profile']
        self.assertEqual(restored,old)

    def test_existing_bus_and_following_trajectories_remain_independently_verified(self):
        for name in ('bus','following'):
            with self.subTest(case=name):
                scene,old=fixed_case(name);before=digest(scene)
                trajectory=t3.solve(scene,epigraph_corridor(old))
                self.assert_magnitude_bounds(scene,trajectory)
                verified=t4.verify(scene,trajectory)
                self.assertEqual(verified['verdict'],'PASS')
                self.assertGreaterEqual(verified['minimum_clearance_lower_bound_m'],1.)
                self.assertEqual(before,digest(scene))

    def test_curved_candidate_keeps_absolute_curvature_upper_bound_and_t4(self):
        capture,output,provenance=authored_case(speed=3.,half_road=20.)
        scene=t1.construct(capture,output,provenance,True)
        corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE,candidate_id='brake_right')
        trajectory=t3.solve(scene,epigraph_corridor(corridor))
        self.assert_magnitude_bounds(scene,trajectory)
        self.assertTrue(any(abs(point['curvature_per_m'])>.01 for point in trajectory['points']))
        self.assertEqual(t4.verify(scene,trajectory)['verdict'],'PASS')
        corrupted=deepcopy(trajectory)
        corrupted['points'][1]['rear_y_m']+=.1
        rejected=t4.verify(scene,corrupted)
        self.assertEqual(rejected['verdict'],'FAIL')
        self.assertEqual(rejected['released_prefix_s'],0.)
        self.assertTrue(any(v['code']=='DYNAMICS_RESIDUAL' for v in rejected['violations']))

    def test_legacy_search_keeps_its_original_solver_formulation(self):
        scene,corridor=fixed_case('bus')
        trajectory=t3.solve(scene,corridor)
        self.assertNotIn('arc_bound',trajectory['solver'])
        self.assertNotIn('curvature_magnitude_bounds_per_m',trajectory['solver'])
        self.assertEqual(trajectory['search_profile'],'bounded_seed_candidates_v1')
        self.assertEqual(t4.verify(scene,trajectory)['verdict'],'PASS')


if __name__=='__main__':unittest.main()
