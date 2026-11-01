"""Acceptance-gate SOFTWARE tests, never online/G2 experiment observations.

All images here are generated tiny PNG bytes; provenance is an explicit test
stand-in. No API/CLI call or real dataset sample is used. A structural gate cannot
cryptographically distinguish a fabricated local ledger from a trusted one.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentic.acceptance import assess_round
from agentic.contracts import Failure, digest
from agentic.fixtures import capture, ScriptedAgent, coasting_fault, rect
from agentic import t1, t2, t3, t4
from agentic.runtime import SimulatedExecutor


def receipt(result):
    scene = result['scene']
    return dict(capture_digest=digest(scene['capture']), scene_digest=digest(scene),
                trajectory_digest=digest(result['trajectory']),
                verification_digest=digest(result['verification']),
                execution_digest=digest(result['execution']),
                executed_at_s=scene['metadata']['reference_time_s'], consumed_once=True)


class PhysicalAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.image = Path(cls.temp.name)/'software-test-not-a-real-scene.png'
        Image.new('RGB', (8, 8), (32, 64, 96)).save(cls.image)
        cls.cap, cls.packet = cls.make_packet()
        cls.synthetic_cap, cls.synthetic = cls.make_packet(source='synthetic_fixture')
        cls.hold_cap, cls.hold = cls.make_packet(speed=0.)
        cls.revision_cap, cls.revision = cls.make_packet(revision=1)
        cls.moving_cap, cls.moving_seed = cls.make_packet(policy='moving_seed', past_crossing=True)

    @classmethod
    def make_packet(cls, source='online_model', speed=6., revision=0, policy='initial_anchor', past_crossing=False):
        cap = capture(speed=speed, revision=revision)
        if source == 'online_model':
            cap['observations'][0]['path'] = str(cls.image)
        output, provenance = ScriptedAgent().decide(cap)
        if past_crossing:
            output['decision'].update(behavior='resume', stop=None, desired_speed_mps=6., yield_to=[],
                                      hard_requirements=['target_region','vehicle_limits','collision_free','road'])
            output['hypotheses']['road']['polygon_m'] = rect(-10, 60, -12, 12)
            actor=output['hypotheses']['objects'][0]
            actor.update(position_m=[-1.2,10.], velocity_mps=[0.,-2.5], uncertainty_m=.2)
            for k,cell in enumerate(actor['occupancy']):
                a=k*.2;b=(k+1)*.2
                cell['alternatives_m']=[rect(-1.4,-1.,10-2.5*b-.3,10-2.5*a+.3)]
            provenance['output_digest'] = digest(output)
        if source == 'online_model':
            provenance.update(source='online_model', model_id='unit-test-stand-in-NOT-remote',
                              requested_model='unit-test-stand-in-NOT-remote', tool_calls=0,
                              prompt_version='unit_test_mock_provenance',
                              prompt_sha256=hashlib.sha256(b'not an actual model request').hexdigest(),
                              image_sha256={cap['observations'][0]['id']: hashlib.sha256(cls.image.read_bytes()).hexdigest()})
        scene = t1.construct(cap, output, provenance, allow_synthetic=source == 'synthetic_fixture')
        corridor = t2.build(scene, policy=policy)
        trajectory = t3.solve(scene, corridor)
        verification = t4.verify(scene, trajectory)
        if verification['verdict'] != 'PASS':
            raise AssertionError(verification['violations'][:2])
        executor = SimulatedExecutor(); executor.observe(cap)
        execution = executor.execute(scene, trajectory, verification, cap['metadata']['reference_time_s'])
        result = dict(status='executed_simulated_prefix', revisions=0,
                      agent_output=output, provenance=provenance, scene=scene,
                      corridor=corridor, trajectory=trajectory,
                      verification=verification, execution=execution)
        result['execution_receipt'] = receipt(result)
        return cap, result

    def assert_rejected(self, result, cap=None, code=None):
        verdict = assess_round(result, self.cap if cap is None else cap)
        self.assertFalse(verdict['strict_live_physical_success'])
        self.assertFalse(verdict['software_success'])
        self.assertEqual(verdict['motion_class'], 'NOT_EXECUTED')
        if code:
            self.assertEqual(verdict['reasons'][0]['code'], code)
        json.dumps(verdict, allow_nan=False)
        return verdict

    def test_complete_mock_online_shape_is_single_window_only(self):
        verdict = assess_round(self.packet, self.cap)
        self.assertTrue(verdict['strict_live_physical_success'])
        self.assertTrue(verdict['software_success'])
        self.assertEqual(verdict['motion_class'], 'MOVING_PREFIX')
        self.assertGreater(verdict['prefix_center_displacement_m'], 1.)
        self.assertFalse(verdict['multi_round_closed_loop_success'])
        self.assertEqual(verdict['ground_truth_correctness'], 'NOT_EVALUATED')
        self.assertEqual(verdict['scope'], 'SINGLE_OBSERVATION_SIMULATED_PREFIX')
        json.dumps(verdict, allow_nan=False)

    def test_synthetic_success_never_counts_as_real_model_success(self):
        verdict = assess_round(self.synthetic, self.synthetic_cap)
        self.assertTrue(verdict['software_success'])
        self.assertFalse(verdict['strict_live_physical_success'])
        self.assertEqual(verdict['status'], 'SYNTHETIC_SOFTWARE_SUCCESS')

    def test_stationary_hold_does_not_become_a_moving_success(self):
        verdict = assess_round(self.hold, self.hold_cap)
        self.assertTrue(verdict['strict_live_physical_success'])
        self.assertEqual(verdict['motion_class'], 'STATIONARY_HOLD')
        self.assertLessEqual(verdict['prefix_arc_length_m'], 1e-5)

    def test_failed_round_or_missing_stage_is_not_success(self):
        bad = deepcopy(self.packet);bad['status'] = 'blocked_no_execution'
        self.assert_rejected(bad, code='ROUND_NOT_EXECUTED')
        bad = deepcopy(self.packet);del bad['corridor']
        self.assert_rejected(bad, code='MISSING_ARTIFACTS')

    def test_revisions_preserve_the_original_acquisition(self):
        initial = deepcopy(self.revision_cap);initial['metadata']['revision'] = 0
        result = deepcopy(self.revision);result['revisions'] = 1
        self.assertTrue(assess_round(result, initial)['strict_live_physical_success'])
        result['revisions'] = 0
        self.assert_rejected(result, initial, 'CAPTURE_CHANGED')
        result['revisions'] = True
        self.assert_rejected(result, initial, 'REVISION_PROVENANCE')

    def test_other_capture_content_cannot_change_during_repair(self):
        bad = deepcopy(self.packet);bad['scene']['capture']['ego']['speed_mps'] += 1
        self.assert_rejected(bad, code='CAPTURE_CHANGED')
        bad = deepcopy(self.packet);bad['scene']['metadata']['reference_time_s'] += .2
        self.assert_rejected(bad, code='CAPTURE_CHANGED')

    def test_changed_output_and_auxiliary_tool_use_are_rejected(self):
        bad = deepcopy(self.packet);bad['agent_output']['decision']['desired_speed_mps'] = 3.
        self.assert_rejected(bad, code='OUTPUT_DIGEST_MISMATCH')
        bad = deepcopy(self.packet);bad['provenance']['tool_calls'] = 1
        bad['scene']['provenance'] = deepcopy(bad['provenance'])
        self.assert_rejected(bad, code='TOOL_ACCESS_UNVERIFIED')

    def test_image_hash_must_match_acquired_bytes(self):
        other = Path(self.temp.name)/'other-test.png'
        Image.new('RGB', (8, 8), (255, 1, 3)).save(other)
        cap = deepcopy(self.cap);cap['observations'][0]['path'] = str(other)
        bad = deepcopy(self.packet);bad['scene']['capture'] = deepcopy(cap)
        self.assert_rejected(bad, cap, 'IMAGE_HASH_MISMATCH')
        bad = deepcopy(self.packet);bad['provenance']['image_sha256']['extra-frame'] = '0'*64
        bad['scene']['provenance'] = deepcopy(bad['provenance'])
        self.assert_rejected(bad, code='IMAGE_PROVENANCE')

    def test_t1_and_t2_artifacts_cannot_be_replaced_by_safe_looking_values(self):
        bad = deepcopy(self.packet);bad['scene']['hypotheses']['road']['uncertainty_m'] += .01
        self.assert_rejected(bad, code='T1_RECONSTRUCTION_MISMATCH')
        bad = deepcopy(self.packet);bad['corridor']['body_radius_m'] *= .5
        self.assert_rejected(bad, code='T2_RECONSTRUCTION_MISMATCH')

    def test_injected_candidates_are_not_optimizer_success(self):
        bad = deepcopy(self.packet);bad['trajectory']['solver']['fault_injection'] = 'unit_test'
        self.assert_rejected(bad, code='T3_NOT_CONSTRAINED_SOLUTION')

    def test_independent_t4_rejects_a_forged_pass(self):
        bad = deepcopy(self.packet)
        bad['trajectory'] = coasting_fault(bad['scene'], bad['trajectory'])
        bad['trajectory']['solver'].pop('fault_injection')
        bad['verification']['trajectory_digest'] = digest(bad['trajectory'])
        # A forged PASS/digest cannot hide the between-sample collision and stop violations.
        self.assert_rejected(bad, code='T4_RECHECK_MISMATCH')
        bad['verification'] = t4.verify(bad['scene'], bad['trajectory'])
        self.assertEqual(bad['verification']['verdict'], 'FAIL')
        self.assert_rejected(bad, code='T4_NOT_PASSED')

    def test_independent_t4_rechecks_values_not_just_the_pass_bit(self):
        bad = deepcopy(self.packet);bad['verification']['minimum_clearance_lower_bound_m'] += 1.
        self.assert_rejected(bad, code='T4_RECHECK_MISMATCH')

    def test_local_receipt_must_match_all_artifacts_and_consumption(self):
        for field,value in [('consumed_once',False),('executed_at_s',100.2),('trajectory_digest','not-this-trajectory')]:
            with self.subTest(field=field):
                bad = deepcopy(self.packet);bad['execution_receipt'][field] = value
                self.assert_rejected(bad, code='EXECUTION_RECEIPT_MISMATCH')
        bad = deepcopy(self.packet);del bad['execution_receipt']
        self.assert_rejected(bad, code='MISSING_ARTIFACTS')

    def test_matching_digest_cannot_hide_wrong_prefix_integration(self):
        bad = deepcopy(self.packet);bad['execution']['speed_mps'] += .1
        bad['execution_receipt']['execution_digest'] = digest(bad['execution'])
        self.assert_rejected(bad, code='EXECUTION_INTEGRATION_MISMATCH')
        bad = deepcopy(self.packet);bad['execution']['elapsed_s'] = 1.
        bad['execution_receipt']['execution_digest'] = digest(bad['execution'])
        self.assert_rejected(bad, code='EXECUTION_INTEGRATION_MISMATCH')

    def test_explicit_moving_seed_round_is_reconstructed_under_its_own_policy(self):
        with self.assertRaises(Failure) as caught:
            t2.build(self.moving_seed['scene'], policy='initial_anchor')
        self.assertEqual(caught.exception.record['code'], 'CORRIDOR_ANCHOR_BLOCKED')
        verdict=assess_round(self.moving_seed, self.moving_cap)
        self.assertTrue(verdict['strict_live_physical_success'], verdict['reasons'])
        self.assertEqual(verdict['motion_class'], 'MOVING_PREFIX')
        self.assertGreater(self.moving_seed['trajectory']['points'][-1]['x_m'], 20.)
        bad=deepcopy(self.moving_seed);bad['corridor']['construction']['policy']='auto_overtake'
        self.assert_rejected(bad, self.moving_cap, 'CORRIDOR_POLICY')
        bad=deepcopy(self.moving_seed);bad['corridor']['construction']['seed_points'][2]['rear_x_m'] += .1
        self.assert_rejected(bad, self.moving_cap, 'T2_RECONSTRUCTION_MISMATCH')

    def test_current_runtime_receipt_contract_integrates_with_acceptance(self):
        # This is still a local software double, not an online observation.
        from unittest.mock import Mock
        from agentic.runtime import ClosedLoop, RunLog
        agent=Mock();agent.public_config={'source':'SOFTWARE_TEST_DOUBLE'}
        agent.decide.return_value=(deepcopy(self.moving_seed['agent_output']),deepcopy(self.moving_seed['provenance']))
        with tempfile.TemporaryDirectory() as folder:
            result=ClosedLoop(agent,RunLog(folder),corridor_policy='moving_seed').round(deepcopy(self.moving_cap))
        self.assertEqual(result['status'],'executed_simulated_prefix')
        self.assertTrue(result['acceptance']['strict_live_physical_success'],result['acceptance']['reasons'])
        self.assertEqual(result['acceptance']['motion_class'],'MOVING_PREFIX')
        self.assertEqual(agent.decide.call_count,1)

    def test_nonfinite_or_malformed_results_fail_closed(self):
        bad = deepcopy(self.packet);bad['trajectory']['points'][2]['speed_mps'] = float('nan')
        self.assert_rejected(bad, code='NONFINITE')
        self.assert_rejected([], code='ROUND_STRUCTURE')


if __name__ == '__main__':
    unittest.main()
