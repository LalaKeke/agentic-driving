"""Small, offline display regressions; no scene/planning/execution is invoked."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

EXPERIMENTS = Path(__file__).resolve().parents[1] / 'experiments'
sys.path.insert(0, str(EXPERIMENTS))
spec = importlib.util.spec_from_file_location('render_continuous_clip', EXPERIMENTS/'render_continuous_clip.py')
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)


class ContinuousClipDisplay(unittest.TestCase):
    def test_round_summary_parent_does_not_hide_attempt_receipts(self):
        with tempfile.TemporaryDirectory(prefix='continuous-layout-test-') as temporary:
            root = Path(temporary)
            attempt = root/'attempt_01'; attempt.mkdir()
            (root/'round_result.json').write_text(json.dumps(dict(verification=dict(verdict='PASS'))))
            (attempt/'T4.json').write_text(json.dumps(dict(verdict='PASS')))
            (attempt/'execution.json').write_text(json.dumps(dict(center_displacement_m=[1.,0.],speed_mps=5.,elapsed_s=.2)))
            (attempt/'execution_receipt.json').write_text(json.dumps(dict(consumed_once=True)))
            (attempt/'acceptor.json').write_text(json.dumps(dict(strict_live_physical_success=True)))
            result, folder = render.load_saved_result(dict(round_result_path=str(root/'round_result.json'),
                runtime_path=str(root), selected_attempt=1))
            self.assertEqual(folder, attempt)
            self.assertTrue(render.metrics(folder)['prefix_consumed'])
            self.assertTrue(render.metrics(folder)['independent_acceptor_pass'])
            (attempt/'T4.json').write_text(json.dumps(dict(verdict='FAIL')))
            with self.assertRaises(ValueError):
                render.load_saved_result(dict(round_result_path=str(root/'round_result.json'),runtime_path=str(root)))
            (attempt/'T4.json').write_text(json.dumps(dict(verdict='PASS')))
            capture = root/'capture.json'
            capture.write_text(json.dumps(dict(metadata=dict(reference_time_id='recorded_1'))))
            source = dict(round_result_path=str(root/'round_result.json'), runtime_path=str(root),
                          capture_path=str(capture), raw_index=4, case_id='frame_004', conditional_chain_pass=True)
            raw = [dict(raw_keyframe_index=4, capture_time_id='recorded_1')]
            with self.assertRaisesRegex(ValueError, 'caller chain status differs'):
                render.prepare_reference(source, raw, root, None, None)
            source.update(conditional_chain_pass=False, stages={s:'PASS' for s in ('T1','T2','T3')})
            with self.assertRaisesRegex(ValueError, 'caller chain status differs'):
                render.prepare_reference(source, raw, root, None, None)

    def test_unattempted_and_no_output_are_not_tool_refusals(self):
        for value, expected in [('NOT_ATTEMPTED', 'NOT_ATTEMPTED'),
                                ('NO_NATIVE_OUTPUT', 'NO_NATIVE_OUTPUT'),
                                ('ATTEMPTED_WITHOUT_SAVED_NATIVE_OUTPUT', 'NO_NATIVE_OUTPUT')]:
            for now in (10., 10.08, 10.5):
                self.assertEqual(render.reference_display_state(now, 10., False, value)[0], expected)
        self.assertEqual(render.reference_display_state(10., 10., False, 'AVAILABLE')[0], 'REFUSED_NO_PREFIX')

    def test_new_image_is_historical_even_inside_consumed_prefix(self):
        self.assertEqual(render.reference_display_state(10., 10., True), ('REF_PASS', 0., True))
        state, age, exact = render.reference_display_state(10.08, 10., True)
        self.assertEqual(state, 'HISTORICAL_PLAN_ONLY')
        self.assertAlmostEqual(age, .08)
        self.assertFalse(exact)
        self.assertEqual(render.reference_display_state(10.5, 10.5, False)[0], 'REFUSED_NO_PREFIX')
        self.assertEqual(render.reference_display_state(9., None, False)[0], 'WARMUP')
        with self.assertRaises(ValueError):
            render.reference_display_state(9., 10., True)

    def test_display_transform_uses_recorded_pose_without_second_rear_offset(self):
        samples = np.array([[0., 0., 0., 0., 6.], [1., 10., 0., 0., 6.]])
        origin = dict(center_xy_world_m=[100., 50.], yaw_world_rad=0., center_offset_m=1.2)
        later = dict(center_xy_world_m=[104., 50.], yaw_world_rad=np.pi/2, center_offset_m=1.2)
        actual = render.transform_plan(samples, origin, later)
        np.testing.assert_allclose(actual[:, 1:3], [[0., 4.], [0., -6.]], atol=1e-12)
        np.testing.assert_array_equal(samples[:, 1:3], [[0., 0.], [10., 0.]])
        np.testing.assert_allclose(actual[:, 3], [-np.pi/2, -np.pi/2])

    def test_gif_quantization_retains_camera_cadence_without_accumulated_drift(self):
        frames = [dict(capture_time_s=100.+i/12) for i in range(229)]
        durations = render.frame_durations_ms(frames)
        self.assertEqual(len(durations), 229)
        self.assertTrue(set(durations).issubset({80, 90}))
        self.assertEqual(sum(durations), round(229/12*100)*10)
        with self.assertRaises(ValueError):
            render.frame_durations_ms([dict(capture_time_s=1.),dict(capture_time_s=1.)])


if __name__ == '__main__':
    unittest.main()
