"""Moving ego-only corridor seeds remove a documented false rejection.

Actor occupancies remain authored test inputs; these tests are not real G2 data.
The actual nonlinear solver and independent original-scene verifier are exercised.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from shapely.geometry import Point,Polygon
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.contracts import Failure,digest,validate
from agentic.fixtures import capture,ScriptedAgent,rect
from agentic.geometry import rear_state,integrate
from agentic import t1,t2,t3,t4


class MovingSeedCorridorTests(unittest.TestCase):
    @staticmethod
    def scene(past_crossing=True,speed=6.,stopping=False):
        cap=capture(speed=speed);out,pro=ScriptedAgent().decide(cap)
        if not stopping:
            out['decision'].update(behavior='resume',stop=None,desired_speed_mps=6.,yield_to=[],
                hard_requirements=['target_region','vehicle_limits','collision_free','road'])
        out['hypotheses']['road']['polygon_m']=rect(-10,60,-12,12)
        actor=out['hypotheses']['objects'][0]
        if past_crossing:
            actor.update(position_m=[-1.2,10.],velocity_mps=[0.,-2.5],uncertainty_m=.2)
            for k,cell in enumerate(actor['occupancy']):
                a=k*.2;b=(k+1)*.2
                cell['alternatives_m']=[rect(-1.4,-1.,10-2.5*b-.3,10-2.5*a+.3)]
        pro['output_digest']=digest(out)
        return t1.construct(cap,out,pro,True)

    def test_default_matches_committed_legacy_artifact_exactly(self):
        # This committed fixture predates the new construction mode.
        root=Path(__file__).parents[1]/'results/v2/first_round'
        scene=json.loads((root/'0013-T1.json').read_text())
        expected=json.loads((root/'0014-T2.json').read_text())
        self.assertEqual(t2.build(scene),expected)
        self.assertEqual(t2.build(scene,policy='initial_anchor'),expected)
        self.assertNotIn('construction',expected)

    def test_old_pose_later_occupied_does_not_block_vehicle_that_has_left(self):
        scene=self.scene();before=digest(scene)
        with self.assertRaises(Failure) as caught:t2.build(scene)
        self.assertEqual(caught.exception.record['code'],'CORRIDOR_ANCHOR_BLOCKED')
        corridor=t2.build(scene,policy='moving_seed')
        self.assertEqual(digest(scene),before,'No object prediction, scenario or behavior mutation')
        validate('corridor_v2.schema.json',corridor)
        self.assertEqual(corridor['construction']['seed_source'],
                         'ego_state_vehicle_limits_semantic_template_only')
        self.assertEqual(corridor['construction']['target_prediction_source'],
                         'model_whole_interval_occupancy_only')
        self.assertFalse(corridor['construction']['seed_is_solution'])
        self.assertEqual(corridor['body_radius_m'],t2.disk_radius(scene['capture']['vehicle']))
        self.assertEqual(corridor['margin_m'],scene['capture']['safety']['margin_m'])
        trajectory=t3.solve(scene,corridor)
        verification=t4.verify(scene,trajectory)
        self.assertIn('ipopt',trajectory['solver']['name'])
        self.assertEqual(verification['verdict'],'PASS',verification['violations'][:3])
        self.assertEqual(verification['released_prefix_s'],.2)
        self.assertGreater(trajectory['points'][-1]['x_m'],20.)
        self.assertGreater(verification['minimum_clearance_lower_bound_m'],1.)
        # The future cell really is allowed to exclude the obsolete initial pose.
        old=Point(rear_state(scene['capture']['ego'],scene['capture']['vehicle'])[:2])
        self.assertTrue(Polygon(corridor['cells'][0]['polygon_m']).buffer(1e-7).covers(old))
        self.assertTrue(any(not Polygon(c['polygon_m']).covers(old) for c in corridor['cells'][1:]))

    def test_true_initial_collision_is_still_rejected(self):
        scene=self.scene();actor=scene['hypotheses']['objects'][0]
        actor['position_m']=[-1.2,0.]
        for cell in actor['occupancy']:cell['alternatives_m']=[rect(-1.4,-1.,-.3,.3)]
        with self.assertRaises(Failure) as caught:t2.build(scene,policy='moving_seed')
        self.assertEqual(caught.exception.record['code'],'CORRIDOR_SEED_BLOCKED')
        self.assertIn('not prove all trajectories',caught.exception.record['message'])

    def test_stationary_vehicle_is_not_allowed_to_escape_its_future_collision(self):
        scene=self.scene(speed=0.,stopping=True)
        with self.assertRaises(Failure) as caught:t2.build(scene,policy='moving_seed')
        self.assertEqual(caught.exception.record['code'],'CORRIDOR_SEED_BLOCKED')
        self.assertGreater(caught.exception.record['details']['interval'][0],0.)

    def test_seed_obeys_public_dynamics_and_time_cells_are_connected(self):
        scene=self.scene();corridor=t2.build(scene,policy='moving_seed')
        seed=corridor['construction']['seed_points'];v=scene['capture']['vehicle']
        self.assertEqual([seed[0][x] for x in ['rear_x_m','rear_y_m','yaw_rad','speed_mps']],
                         rear_state(scene['capture']['ego'],v))
        for a,b in zip(seed,seed[1:]):
            q=[a[x] for x in ['rear_x_m','rear_y_m','yaw_rad','speed_mps']]
            expected=integrate(q,a['acceleration_mps2'],a['steer_rad'],b['time_s']-a['time_s'],v)
            for value,want in zip([b[x] for x in ['rear_x_m','rear_y_m','yaw_rad','speed_mps']],expected):
                self.assertAlmostEqual(value,want,places=10)
            self.assertGreaterEqual(a['acceleration_mps2'],-v['max_brake_mps2'])
            self.assertLessEqual(a['acceleration_mps2'],v['max_accel_mps2'])
            self.assertEqual(a['steer_rad'],0.)
        for a,b in zip(corridor['cells'],corridor['cells'][1:]):
            self.assertFalse(Polygon(a['polygon_m']).intersection(Polygon(b['polygon_m'])).is_empty)

    def test_yield_behavior_and_stop_deadline_are_not_changed(self):
        scene=self.scene(past_crossing=False,stopping=True);original=deepcopy(scene['decision'])
        corridor=t2.build(scene,policy='moving_seed');trajectory=t3.solve(scene,corridor)
        self.assertEqual(t4.verify(scene,trajectory)['verdict'],'PASS')
        self.assertEqual(scene['decision'],original)
        seed=corridor['construction']['seed_points'];deadline=scene['decision']['stop']['deadline_s']
        self.assertTrue(all(abs(p['speed_mps'])<1e-8 for p in seed if p['time_s']>=deadline))
        self.assertEqual(corridor['construction']['behavior'],'stop_yield')

    def test_seed_does_not_silently_relax_impossible_stop(self):
        scene=self.scene(past_crossing=False,stopping=True)
        scene['decision']['stop']['deadline_s']=.2
        with self.assertRaises(Failure) as caught:t2.build(scene,policy='moving_seed')
        self.assertEqual(caught.exception.record['code'],'CORRIDOR_SEED_STOP_INFEASIBLE')
        self.assertEqual(scene['decision']['stop']['deadline_s'],.2)

    def test_seed_checks_the_actual_between_grid_stop_deadline(self):
        scene=self.scene(past_crossing=False,speed=2.5,stopping=True)
        scene['decision']['stop']['deadline_s']=.9
        with self.assertRaises(Failure) as caught:t2.build(scene,policy='moving_seed')
        self.assertEqual(caught.exception.record['code'],'CORRIDOR_SEED_STOP_INFEASIBLE')

    def test_unknown_policy_rejected(self):
        with self.assertRaises(Failure) as caught:t2.build(self.scene(),policy='auto_overtake')
        self.assertEqual(caught.exception.record['code'],'CORRIDOR_POLICY')


if __name__=='__main__':unittest.main()
