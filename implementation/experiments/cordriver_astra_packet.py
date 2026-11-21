"""Build an image/calibration packet for the 3-second, pre-QP CorDriver reference.

This reader never consumes annotations, logged futures, benchmark commands, QP solutions
or predicted object states. Input inference must itself use the causal preparation protocol.
"""
import hashlib
import math

import numpy as np
from scipy.spatial.transform import Rotation

from cordriver_validate import data_path

CHANNELS = ("CAM_FRONT", "CAM_FRONT_LEFT", "CAM_FRONT_RIGHT",
            "CAM_BACK_LEFT", "CAM_BACK", "CAM_BACK_RIGHT")
# Frozen upstream QPControl_HARD_corner_12N_learned_weights.lidar2ego_traj
NATIVE_R = np.array([[.00203327, .99970406, .02424172],
                     [-.99998053, .00217566, -.00584864],
                     [-.00589965, -.02422936, .99968902]])
NATIVE_T = np.array([.943713, 0., 1.84023])
BODY_OFFSET = .5  # (l_front - l_rear) / 2 for the upstream footprint
STEP_S = .5


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def se3(quat_wxyz, position):
    m = np.eye(4)
    m[:3, :3] = Rotation.from_quat(np.roll(quat_wxyz, -1)).as_matrix()
    m[:3, 3] = position
    return m


def reference(steps, initial_speed):
    steps = np.array(steps, dtype=float)
    if steps.shape != (6, 2) or not np.isfinite(steps).all() or not np.isfinite(initial_speed):
        raise ValueError('Expected six finite half-second neural displacement pairs and initial speed')
    native = steps @ NATIVE_R[:2, :2].T
    points = np.vstack([np.zeros(2), native.cumsum(axis=0)])
    headings = [0.]
    for dx, dy in native:
        headings.append(math.atan2(dy, dx) if math.hypot(dx, dy) > 1e-5 else headings[-1])
    offsets = BODY_OFFSET * np.column_stack([np.cos(headings), np.sin(headings)])
    centers = points + offsets - offsets[0]
    speeds = np.r_[initial_speed, np.linalg.norm(np.diff(centers, axis=0), axis=1) / STEP_S]
    rows = [dict(t_s=i * STEP_S, x_forward_m=float(p[0]), y_left_m=float(p[1]),
                 speed_mps=float(v), nominal_yaw_rad=yaw)
            for i, (p, v, yaw) in enumerate(zip(centers, speeds, headings))]
    return centers, rows


def camera_transform(info, camera, actual_lidar):
    ref = se3(info['ego2global_rotation'], info['ego2global_translation'])
    at_image = se3(camera['ego2global_rotation'], camera['ego2global_translation'])
    extrinsic = se3(camera['sensor2ego_rotation'], camera['sensor2ego_translation'])
    camera_from_ref = np.linalg.inv(at_image @ extrinsic) @ ref
    lidar_from_camera = np.eye(4)
    lidar_from_camera[:3, :3] = camera['sensor2lidar_rotation']
    lidar_from_camera[:3, 3] = camera['sensor2lidar_translation']
    diff = np.max(np.abs(camera_from_ref - np.linalg.inv(actual_lidar @ lidar_from_camera)))
    if diff > 1e-6:
        raise ValueError(f'Exposure alignment disagrees with stored camera-to-LiDAR transform: {diff}')
    return camera_from_ref, extrinsic, float(diff)


def clip_segment(a, b, k, w, h):
    """Clip to z >= 5 cm and the four full-resolution rectified image planes."""
    planes = [([0., 0., 1.], -.05), (k[0], 0.), ((w-1)*k[2]-k[0], 0.),
              (k[1], 0.), ((h-1)*k[2]-k[1], 0.)]
    lo, hi = 0., 1.
    for n, offset in planes:
        fa, fb = n @ a + offset, n @ b + offset
        if fa < 0 and fb < 0:
            return None
        if fa < 0 or fb < 0:
            crossing = fa / (fa - fb)
            if fa < 0:
                lo = max(lo, crossing)
            else:
                hi = min(hi, crossing)
    if lo >= hi:
        return None
    def project(frac):
        p = k @ (a + frac * (b-a))
        return tuple(np.clip(p[:2] / p[2], [0, 0], [w-1, h-1]))
    return lo, hi, project(lo), project(hi)
