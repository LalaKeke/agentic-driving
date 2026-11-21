"""Build the same locally registered nuScenes baseline-route input for the two models."""

import hashlib
import json
from pathlib import Path

import numpy as np

from cordriver_astra_packet import BODY_OFFSET, NATIVE_R, se3


BRANCHES = ("RIGHT", "LEFT", "STRAIGHT")


def body_frame(info):
    lidar = se3(info["lidar2ego_rotation"], info["lidar2ego_translation"])
    linear = lidar[:2, :2] @ np.linalg.inv(NATIVE_R[:2, :2])
    origin = lidar[:2, 3] + linear @ np.array([BODY_OFFSET, 0.])
    return linear, origin


def navigation(info, route_path):
    """Project the current rear-axle pose to the predefined route, in its published order."""
    route_bytes = Path(route_path).read_bytes()
    points = np.asarray(json.loads(route_bytes), dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError("NAV_INVALID_POLYLINE")
    if len(points) < 3:
        raise ValueError("NAV_INSUFFICIENT_ROUTE")
    points = points[np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1.e-9]]
    if len(points) < 3:
        raise ValueError("NAV_INSUFFICIENT_DISTINCT_POINTS")
    pose = se3(info["ego2global_rotation"], info["ego2global_translation"])
    yaw = np.arctan2(pose[1, 0], pose[0, 0])
    ego_to_global = np.array([[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]])
    local = (points - pose[:2, 3]) @ ego_to_global
    vectors = np.diff(local, axis=0)
    lengths = np.linalg.norm(vectors, axis=1)
    fractions = np.clip(-(local[:-1]*vectors).sum(axis=1)/lengths**2, 0., 1.)
    projections = local[:-1]+fractions[:,None]*vectors
    index = int(np.argmin(np.linalg.norm(projections,axis=1)))
    error = float(np.linalg.norm(projections[index]))
    if error > 5.:
        raise ValueError(f"NAV_START_DISTANCE_{error:.3f}m")
    arc = np.r_[0., np.cumsum(lengths)]
    start = arc[index]+fractions[index]*lengths[index]
    if arc[-1] - start < 21.:
        raise ValueError("NAV_INSUFFICIENT_FORWARD_DISTANCE")

    def at(s):
        return np.stack([np.interp(start+s,arc,local[:,axis]) for axis in range(2)],axis=-1)

    def tangent(s):
        direction = at(s+1.) - at(s-1.)
        if np.linalg.norm(direction) < .1:
            raise ValueError("NAV_DEGENERATE_TANGENT")
        return float(np.degrees(np.arctan2(direction[1],direction[0])))

    start_heading = tangent(0.)
    if abs(start_heading) > 60.:
        raise ValueError(f"NAV_START_HEADING_{start_heading:.2f}deg")
    turn = tangent(20.)
    command = "LEFT" if turn >= 30. else "RIGHT" if turn <= -30. else "STRAIGHT"
    distances = np.arange(0., min(30.,arc[-1]-start)+1.e-8, 2.5)
    physical_xy = at(distances)
    linear, origin = body_frame(info)
    guide = np.linalg.solve(linear, (physical_xy-origin).T).T
    nav = dict(command=command,source="nuScenes CAN baseline recommended route (untimed map path)",
               frame="Fixed initial nominal CorDriver BODY center, x forward and y left",
               along_route_m=distances.tolist(), polyline_guidance_m=guide.tolist(),
               command_rule="Route tangent 20 m ahead, relative to observed ego heading. At least +30 deg LEFT, at most -30 deg RIGHT; otherwise STRAIGHT. Geometric branch proxy.",
               route_tangent_deg_at_20m=turn,
               role="Geometric navigation, independent of speed and obstacle clearance; distance samples have no assigned arrival times.")
    audit = dict(route_file=str(Path(route_path).resolve()),route_sha256=hashlib.sha256(route_bytes).hexdigest(),
                 previous_raw_position=points[index].tolist(),next_raw_position=points[index+1].tolist(),
                 nearest_segment_index_in_deduplicated_route=index,projection_fraction=float(fractions[index]),
                 global_ego_xy=pose[:2,3].tolist(),global_ego_yaw_rad=float(yaw),
                 distance_to_route_m=error,route_start_heading_deg=start_heading,
                 path_distance_available_m=float(arc[-1]-start),polyline_reference_ego_m=physical_xy.tolist(),
                 reference_time_us=int(info["timestamp"]))
    return nav,audit
