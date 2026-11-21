"""Causal v2 intervention routing on the published CorDriver path and predicted road edges."""
import json
import math

import numpy as np

from cordriver_astra_packet import reference
from cordriver_measurements import body, official_qp
from cordriver_reachable_options import load, sha
from cordriver_iter_road import edges_from_features
from cordriver_native_intervention import intervention


VERSION = "cordriver_native_edge_gate_v2"
BODY_LENGTH_M, BODY_WIDTH_M = 4.084, 1.85
EDGE_MARGIN_M = 0.30
SUBSTEPS = 5  # Five subdivisions of each published 0.5 s interval.
KNOT_DT_S = 0.5
FALLBACK_SOURCE = "NATIVE_RETURNS_LEARNED_REFERENCE_TANGENT_YAW"
EDGE_REASON = "NATIVE_SWEEP_NEAR_PREDICTED_ROAD_EDGE"


def native_body_knots(sample, upstream):
    """Seven timed body centres and headings of the actual selected native return."""
    if upstream is not None and (sample['token'] != upstream['token'] or
                                 sample['scene_name'] != upstream['scene']):
        raise ValueError("Native state identity does not match published return")
    if sample.get('fallback'):
        xy = np.asarray(sample['final_xy_lidar_m'], dtype=float)
        steps = np.asarray(sample['qp_reference_steps'], dtype=float)
        if (xy.shape != (6, 2) or steps.shape != (6, 2) or not np.isfinite(xy).all()
                or not np.allclose(xy, np.cumsum(steps, axis=0), atol=1e-5, rtol=0)
                or not np.allclose(xy, sample['reference_xy_lidar_m'], atol=1e-5, rtol=0)):
            raise ValueError("Cannot bind fallback to the published learned return")
        centers, timed = reference(steps, sample['can_bus'][13])
        return np.column_stack((centers, [r['nominal_yaw_rad'] for r in timed])), FALLBACK_SOURCE
    if upstream is not None and upstream.get('status') == 'BOUND':
        delta = upstream['replay_max_xy_delta_m']
        states = np.asarray(upstream['state_xy_yaw_speed'], dtype=float)
        if (not np.isfinite(delta) or not 0 <= delta <= 0.0005 or
                states.shape != (13, 4) or not np.isfinite(states).all()):
            raise ValueError("Invalid bound native QP states")
    return official_qp(sample, upstream)


def edge_clearance(poses, features):
    """Lower bound on predicted-line distance for a linear-centre, short-yaw swept body."""
    edges = edges_from_features(features)
    result = dict(status='NO_PREDICTED_BOUNDARY', boundary_count=len(edges), triggered=False,
                  initial_clearance_m=None, min_clearance_lower_bound_m=None,
                  closest_boundary_rank=None, closest_interval_s=None, first_risk_interval_s=None)
    if poses is None:
        result['status'] = 'NATIVE_POSES_UNAVAILABLE'
        return result
    poses = np.asarray(poses, dtype=float)
    if poses.shape != (7, 3) or not np.isfinite(poses).all():
        raise ValueError("Expected seven finite reference-frame body poses")
    if not edges:
        return result
    first = body(poses[0, :2], poses[0, 2], BODY_LENGTH_M, BODY_WIDTH_M)
    result['initial_clearance_m'] = min(first.distance(edge.line) for edge in edges.values())
    minimum = math.inf
    radius = math.hypot(BODY_LENGTH_M, BODY_WIDTH_M) / 2
    for i, (start, end) in enumerate(zip(poses[:-1], poses[1:])):
        rotation = math.atan2(math.sin(end[2] - start[2]), math.cos(end[2] - start[2]))
        # A vertex on a rotating rectangle deviates from its endpoint chord by at
        # most max|v''|/8 = radius*(delta_yaw**2)/8. Translation is linear.
        pad = radius * (rotation / SUBSTEPS)**2 / 8
        for step in range(1, SUBSTEPS + 1):
            f = step / SUBSTEPS
            pos = start[:2] + f * (end[:2] - start[:2])
            last = body(pos, start[2] + f * rotation, BODY_LENGTH_M, BODY_WIDTH_M)
            hull = first.union(last).convex_hull
            distance, rank = min((hull.distance(edge.line), edge.rank) for edge in edges.values())
            distance = max(0.0, distance - pad)
            interval = [((i * SUBSTEPS + step - 1) * KNOT_DT_S) / SUBSTEPS,
                        ((i * SUBSTEPS + step) * KNOT_DT_S) / SUBSTEPS]
            if distance < minimum:
                minimum = distance
                result.update(closest_boundary_rank=rank, closest_interval_s=interval)
            if distance <= EDGE_MARGIN_M and result['first_risk_interval_s'] is None:
                result['first_risk_interval_s'] = interval
            first = last
    result.update(status='ASSESSED_PREDICTED_LINES', min_clearance_lower_bound_m=minimum,
                  triggered=minimum <= EDGE_MARGIN_M)
    return result


def intervention_v2(sample, visual, upstream):
    """Extend the v1 vetoes without altering baseline or asserting real-world clearance."""
    legacy = intervention(sample, visual, upstream if upstream is not None else
                          dict(token=sample['token'], status='MISSING'))
    poses, source = native_body_knots(sample, upstream)
    road = edge_clearance(poses, visual['map_features'])
    reasons = list(legacy['reasons'])
    if road['triggered']:
        reasons.append(EDGE_REASON)
    return dict(legacy, version=VERSION, consider_guidance=bool(reasons), reasons=reasons,
                legacy_trigger=legacy['consider_guidance'], trajectory_source=source, road_edge=road)
