import sys
import unittest
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
import cordriver_nav_solver as solver


class NavigationSolverTests(unittest.TestCase):
    def test_oriented_windows_use_local_axes(self):
        guide = dict(region_type="cruise",center_xy_m=[2.,0.],region_yaw_deg=90.,
                     speed_range_mps=[0.,2.])
        stages=solver.parse_nodes(dict(decision="ADJUST",at_1_5s=guide,at_3_0s=guide))
        centers=np.zeros((13,2))
        states=np.zeros((13,4))
        centers[6]=[2.,.9]
        centers[12]=[2.8,0.]
        first,last=solver.node_report(stages,centers,states)
        self.assertTrue(first["within"])
        np.testing.assert_allclose(last["signed_outside_long_lat_m"],[0.,-.3],atol=1e-9)
        self.assertFalse(last["within"])

    def test_move_preserves_knots_and_interpolation(self):
        z=np.zeros((1,30))
        z[0,24:]=[.5,.8,.2,.7,-.4,-.1]
        shifts=solver.corridor_moves(z)[0]
        np.testing.assert_allclose(shifts[[2,5]].ravel(),z[0,24:])
        np.testing.assert_allclose(shifts[0],(7/27.)*shifts[2])

    def test_same_control_rollout_in_upstream_implementation(self):
        u=np.tile([-.5,.07],12)
        x0=np.array([.943713,0.,0.,8.])
        p=solver.NATIVE.QPControl()
        exact=solver.control_states(u[None],x0,p.dt,p.L)[0]
        rk=[torch.tensor(x0,dtype=torch.float64)]
        for ctrl in u.reshape(12,2):
            rk.append(p.kinematics_forward(rk[-1],torch.tensor(ctrl)))
        np.testing.assert_allclose(exact,torch.stack(rk).numpy(),rtol=0,atol=1e-6)

    def test_all_motion_modes_preserved_with_halfstep_interpolation(self):
        ring=[[-1,-.5],[1,-.5],[1,.5],[-1,.5]]
        seq=np.zeros((2,6,2))
        seq[1,:,0]=.4
        forecasts,status=solver.predicted_objects(dict(current_objects=[dict(
            rank=2,score=.9,footprint_lidar_xy_m=ring,future_step_xy_lidar_m=seq.tolist())]),
            np.eye(2),np.asarray)
        self.assertEqual(status["forecast_modes"],2)
        np.testing.assert_allclose(forecasts[1][1][2],np.array(ring)+[.2,0])
        np.testing.assert_allclose(forecasts[12][1][2],np.array(ring)+[2.4,0])


if __name__=="__main__":
    unittest.main()
