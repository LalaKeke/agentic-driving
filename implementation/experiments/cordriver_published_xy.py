"""Map an integrated CorDriver plan to the six positions expected by the release metric."""
import numpy as np
from cordriver_reachable_options import load
from cordriver_astra_packet import NATIVE_R, NATIVE_T

def published_xy(path):
    plan = load(path)
    times = np.asarray(plan['times_s'])
    if not np.allclose(times, np.arange(13)*.25, rtol=0, atol=1e-8):
        raise ValueError(f'Unexpected control times for {path}')
    state = np.asarray(plan['state_xy_yaw_v'])[2::2,:2]
    # Inverse of the upstream QP's fixed ego/lidar extrinsic with z=0 in ego.
    return ((np.c_[state, np.zeros(6)] - NATIVE_T) @ NATIVE_R)[:,:2]
