import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
import cordriver_edge_gate as gate
from cordriver_iter_road import XY, BODY_ORIGIN_SHIFT


def predicted_line(xy, rank=1, label='boundary', score=.9):
    points = np.linalg.solve(XY, (np.array(xy) - BODY_ORIGIN_SHIFT).T).T
    return dict(points=points.tolist(), rank=rank, label=label, score=score)


def native_sample(fallback):
    steps = np.tile([0., 5.], (6, 1))
    return dict(token='token', scene_name='test', solver_status='optimal', can_bus=[0.] * 18,
                fallback=fallback, verified_qp_output=not fallback, qp_reference_steps=steps.tolist(),
                reference_xy_lidar_m=steps.cumsum(axis=0).tolist(),
                final_xy_lidar_m=steps.cumsum(axis=0).tolist())


class EdgeTriggerTests(unittest.TestCase):
    def test_native_success_uses_bound_qp_and_fallback_uses_actual_published_return(self):
        sample = native_sample(False)
        states = np.zeros((13, 4))
        states[:, 1] = np.arange(13) * .1
        archive = dict(token='token', scene='test', status='BOUND', state_xy_yaw_speed=states,
                       replay_max_xy_delta_m=0.)
        poses, _ = gate.native_body_knots(sample, archive)
        np.testing.assert_allclose(poses[-1], [0, 1.2, 0])
        archive['replay_max_xy_delta_m'] = .005
        with self.assertRaises(ValueError):
            gate.native_body_knots(sample, archive)
        sample = native_sample(True)
        poses, source = gate.native_body_knots(sample, None)
        self.assertEqual(source, gate.FALLBACK_SOURCE)
        self.assertGreater(poses[-1, 0], 29)
        sample['final_xy_lidar_m'][5][0] += .1
        with self.assertRaises(ValueError):
            gate.native_body_knots(sample, None)

    def test_body_catches_curb_between_safe_centres_and_half_second_knots(self):
        poses = np.zeros((7, 3))
        poses[:, 0] = np.arange(7) * 10.
        # At the two surrounding knots the full car is over 2 m away.
        edge = predicted_line([[5., .9], [5., 2.]])
        r = gate.edge_clearance(poses, [edge])
        self.assertGreater(r['initial_clearance_m'], 2)
        self.assertTrue(r['triggered'])
        self.assertEqual(r['min_clearance_lower_bound_m'], 0.)
        self.assertLess(r['first_risk_interval_s'][1], .5)
        self.assertEqual(r['closest_boundary_rank'], 1)

    def test_margin_measured_from_body_and_missing_or_nonphysical_features(self):
        poses = np.zeros((7, 3))
        width = gate.BODY_WIDTH_M / 2
        def run(clearance, **kwargs):
            return gate.edge_clearance(poses, [predicted_line([[-10, width+clearance],
                                                               [10, width+clearance]], **kwargs)])
        self.assertTrue(run(.25)['triggered'])
        self.assertFalse(run(.35)['triggered'])
        for r in (run(0, score=.399), run(0, label='divider')):
            self.assertEqual(r['status'], 'NO_PREDICTED_BOUNDARY')
            self.assertIsNone(r['min_clearance_lower_bound_m'])
        self.assertIsNone(gate.edge_clearance(None, [])['min_clearance_lower_bound_m'])

    def test_fallback_triggers_without_boundary_and_no_evaluation_inputs(self):
        s = native_sample(True)
        v = dict(token=s['token'], map_features=[], current_objects=[])
        r = gate.intervention_v2(s, v, None)
        self.assertTrue(r['legacy_trigger'] and r['consider_guidance'])
        self.assertEqual(r['reasons'], ['UPSTREAM_FALLBACK'])
        v['map_features'] = [predicted_line([[9., -2.], [9., 2.]])]
        self.assertIn(gate.EDGE_REASON, gate.intervention_v2(s, v, None)['reasons'])


if __name__ == '__main__':
    unittest.main()
