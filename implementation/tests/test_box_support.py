"""Small authored geometry tests; no model calls or online success claims."""
import json
import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic import t2,t3
from agentic.contracts import validate


class BoxSupportTests(unittest.TestCase):
    vehicle=dict(front_m=2.3,rear_m=2.3,half_width_m=.9,center_to_rear_axle_m=1.2)

    def test_exact_support_contains_dense_rotations_for_all_corners(self):
        v=self.vehicle;d=v['center_to_rear_axle_m']
        for i in range(24):
            nx,ny=math.cos(i*math.pi/12),math.sin(i*math.pi/12)
            bound=t2.yaw_box_support(v,nx,ny)
            dense=[]
            for k in range(701):
                a=-.35+k*.001;c,s=math.cos(a),math.sin(a)
                for x in (d-v['rear_m'],d+v['front_m']):
                    for y in (-v['half_width_m'],v['half_width_m']):
                        dense.append(nx*(c*x-s*y)+ny*(s*x+c*y))
            self.assertLessEqual(max(dense),bound+1e-12)
            self.assertLess(bound-max(dense),1e-6)

    def test_asymmetry_and_center_offset_are_not_counted_twice(self):
        v=self.vehicle
        self.assertAlmostEqual(t2.yaw_point_support((1.2,0),1,0),1.2)
        self.assertAlmostEqual(t2.yaw_point_support((1.2,0),-1,0),-1.2*math.cos(.35))
        self.assertLess(t2.yaw_box_support(v,0,1),t2.disk_radius(v))
        self.assertGreater(t2.yaw_box_support(v,1,0),t2.yaw_box_support(v,-1,0))

    def test_explicit_profile_and_old_default_artifact(self):
        root=Path(__file__).parents[1]/'results/v2/first_round'
        scene=json.loads((root/'0013-T1.json').read_text())
        self.assertEqual(t2.build(scene),json.loads((root/'0014-T2.json').read_text()))
        corridor=t2.build(scene,policy=t2.YAW_BOX_PROFILE)
        validate('corridor_yaw_box_v1.schema.json',corridor)
        self.assertEqual(corridor['planner_profile'],t2.YAW_BOX_PROFILE)
        self.assertEqual(corridor['heading_bound_rad'],.35)
        trajectory=t3.solve(scene,corridor)
        validate('trajectory_yaw_box_v1.schema.json',trajectory)
        self.assertTrue(all(abs(p['yaw_rad'])<=.35+1e-8 for p in trajectory['points']))


if __name__=='__main__':unittest.main()
