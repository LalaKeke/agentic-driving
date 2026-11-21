"""Geometry and physical gates shared by the nuScenes whole-horizon planner."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments'))
import cordriver_smooth_planner as planner


class SmoothPlannerTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(trajectory=[dict(t_s=t, x_forward_m=2*t, y_left_m=0.) for t in planner.TIMES])
        self.raw = np.column_stack((np.arange(1., 7.), np.zeros(6), np.zeros(6),
                                    np.full(6, 8.), np.full(6, 3.)))
        self.vehicle = SimpleNamespace(w=1.85, l_front=2.542, l_rear=1.542, dt=.25, L=2.)
        self.trial = SimpleNamespace(p=self.vehicle, m={'origin': np.array([.5, 0.])}, states=lambda u:
                        planner.core.control_states(u, np.array([0., 0., 0., 2.]), self.vehicle.dt, self.vehicle.L))

    def assert_same_rectangles(self, first, second):
        np.testing.assert_allclose(first[:, [0, 1, 3, 4]], second[:, [0, 1, 3, 4]], rtol=0, atol=1e-6)
        # Projective directions avoid interpreting e.g. 179 degrees and -1 degree as different.
        np.testing.assert_allclose(np.exp(2j*first[:, 2]), np.exp(2j*second[:, 2]), rtol=0, atol=1e-6)

    def test_equivalent_yaw_at_perpendicular_neighbor_is_same_geometric_plan(self):
        self.raw[:, 2] = [0., np.pi/2, np.pi/2, 0., 0., 0.]
        equivalent = self.raw.copy()
        equivalent[0, 2] += np.pi
        a, _ = planner.fit_corridor(self.raw, self.context, [], self.vehicle.w)
        b, _ = planner.fit_corridor(equivalent, self.context, [], self.vehicle.w)
        self.assert_same_rectangles(a, b)

    def test_refined_heading_parallel_or_perpendicular_to_same_unoriented_axis(self):
        self.raw[:, 2] = [0., np.pi/2, np.pi/2, 0., 0., 0.]
        equivalent = self.raw.copy()
        equivalent[1, 2] -= np.pi
        zero_controls = np.zeros(24)
        a, _ = planner.fit_to_seed(self.raw, self.raw.copy(), self.trial, zero_controls)
        b, _ = planner.fit_to_seed(equivalent, equivalent.copy(), self.trial, zero_controls)
        self.assert_same_rectangles(a, b)

    def test_swapping_side_labels_and_rotating_quarter_turn_preserves_geometry(self):
        self.raw[:, 2] = np.array([0., np.pi/2, np.pi/2, 0., .09, -.12])
        equivalent = self.raw.copy()
        exchanged = np.array([0, 2, 3])
        equivalent[exchanged, 2] += np.pi/2
        equivalent[exchanged, 3:5] = equivalent[exchanged, 3:5][:, ::-1]
        primary, target = planner.fit_corridor(self.raw, self.context, [], self.vehicle.w)
        recoded, same_target = planner.fit_corridor(equivalent, self.context, [], self.vehicle.w)
        np.testing.assert_allclose(target, same_target, atol=1e-12)
        refined, _ = planner.fit_to_seed(self.raw, primary, self.trial, np.zeros(24))
        second, _ = planner.fit_to_seed(equivalent, recoded, self.trial, np.zeros(24))
        for a, b in [(primary, recoded), (refined, second)]:
            b = b.copy()
            b[exchanged, 2] -= np.pi/2
            b[exchanged, 3:5] = b[exchanged, 3:5][:, ::-1]
            self.assert_same_rectangles(a, b)

    def test_only_short_side_can_grow_and_both_stages_respect_total_cap(self):
        self.raw[:, 4] = [.1, 4.5, .1, 4.5, .1, 4.5]
        self.raw[1::2, 3:] = self.raw[1::2, 3:][:, ::-1]
        primary, _ = planner.fit_corridor(self.raw, self.context, [], self.vehicle.w)
        refined, _ = planner.fit_to_seed(self.raw, primary, self.trial, np.zeros(24))
        short_col = np.argmin(self.raw[:, 3:], axis=1) + 3
        long_col = 7 - short_col
        for rect in (primary, refined):
            with self.subTest(added=rect[:, 3:].tolist()):
                difference = rect - self.raw
                np.testing.assert_allclose(difference[np.arange(6), long_col], 0., atol=1e-10)
                added = difference[np.arange(6), short_col]
                self.assertTrue(np.all(added >= -1e-10))
                self.assertTrue(np.all(added <= self.vehicle.w + 1e-10))
                self.assertGreater(float(added.max()), 1.)

    def test_signed_jerk_gate_and_lateral_acceleration_at_interval_end(self):
        states = np.zeros((1, 13, 4))
        controls = np.zeros((1, 24))
        for sign in (-1, 1):
            controls[0, 2::2] = sign * planner.JERK_MAX * .25
            self.assertAlmostEqual(planner.added_margins(states, controls, .25, 2.).min(), 0.)
            controls[0, 2] += sign * .01
            self.assertLess(planner.added_margins(states, controls, .25, 2.).min(), -.03)
        controls.fill(0.)
        for sign in (-1, 1):
            # Interval zero is the only one with steering. Its start is at rest.
            controls[0, 1] = sign * np.arctan(1./6)
            states[0, 1, 3] = 6.
            self.assertAlmostEqual(planner.added_margins(states, controls, .25, 2.).min(), 0.)
            states[0, 1, 3] = 6.1
            self.assertLess(planner.added_margins(states, controls, .25, 2.).min(), -.09)

    def test_stationary_past_remains_unknown_even_with_immediate_future_motion(self):
        def message(t, speed):
            return dict(utime=t, vel=[speed, 0., 0.], rotation_rate=[0., 0., .02])
        sample = dict(timestamp_us=500000, can_pose_timestamp_us=500000)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'can.json'
            path.write_text(json.dumps([message(249999, 1.), message(450000, 0.), message(500000, 0.),
                                        message(500001, 1.)]))
            with self.assertRaisesRegex(ValueError, 'Insufficient motion'):
                planner.legacy.derive(path, sample)
            path.write_text(json.dumps([message(500000, 1.), message(500001, 50.)]))
            estimate, history = planner.legacy.derive(path, sample)
            self.assertEqual(len(history), 1)
            self.assertAlmostEqual(estimate, np.arctan(.04))


if __name__ == '__main__':
    unittest.main()
