"""Re-run the 0018/31 secondary-road experiment from frozen model predictions."""
import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
from shapely.geometry import mapping

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "experiments"))
import cordriver_smooth_planner as smooth
from cordriver_iter_dense import states
from cordriver_iter_road import build_road, check_road, edges_from_features

core, legacy = smooth.core, smooth.legacy


def secondary_planes(road, corners, ranks):
    """At each seed corner take the closest edge segment and the initial ego side."""
    planes = []
    for rank in sorted(ranks):
        points = road.edges[rank].points
        direction = np.diff(points, axis=0)
        length_sq = np.einsum("ij,ij->i", direction, direction)
        if np.any(length_sq <= 0):
            raise ValueError("Degenerate secondary edge")
        anchors, normals = np.empty_like(corners), np.empty_like(corners)
        for ti, body in enumerate(corners):
            for ci, corner in enumerate(body):
                frac = np.clip(np.einsum("ij,ij->i", corner - points[:-1], direction) / length_sq, 0, 1)
                near = points[:-1] + frac[:, None] * direction
                idx = int(np.argmin(np.linalg.norm(near - corner, axis=1)))
                tangent = direction[idx] / np.sqrt(length_sq[idx])
                normal = np.array([-tangent[1], tangent[0]])
                anchors[ti, ci] = near[idx]
                normals[ti, ci] = normal if np.dot(-near[idx], normal) >= 0 else -normal
        planes.append((anchors, normals))
    return planes


def replay(out):
    data = core.read(HERE / "0018_31.json")
    ctx, visual = data["context"], data["primary"]
    route = ctx["navigation"]["polyline_guidance_m"]
    road = build_road(route, edges_from_features(visual["map_features"]))
    secondary = build_road(route, edges_from_features(data["secondary_features"]))
    out.mkdir(parents=True, exist_ok=False)
    road_file, visual_file = out / "road.json", out / "visual.json"
    core.save(road_file, dict(support=mapping(road.support), edges=[mapping(e.line) for e in road.edges.values()]))
    core.save(visual_file, visual)

    vehicle, mat = core.compose_native(data["row"], data["qp_weight"], "cpu")
    tr = core.Trial(vehicle, mat, data["row"], visual)
    legacy.bind_initial_assumption(tr, data["steering"])
    raw = tr.m["rect"].copy()
    seed = np.asarray(data["warm_start"]).ravel()
    seed_states = tr.states(seed[None])
    _, centers, headings = states(dict(state_xy_yaw_v=seed_states[0], controls_accel_steer=seed.reshape(-1, 2)), .025)
    check = check_road(secondary, centers, headings)
    ranks = {rank for pose in check["poses"] for rank in pose["intersected_boundary_ranks"]}
    if not ranks:
        raise ValueError("Seed no longer intersects the expected secondary edge")
    _, corners = core.geometry(seed_states, tr.m["origin"], vehicle)
    planes = secondary_planes(secondary, corners[0], ranks)
    base_calc = tr.calc

    def with_secondary(z, *args):
        _, body = core.geometry(tr.states(z), tr.m["origin"], vehicle)
        margins = [((body - a) * n).sum(axis=-1).reshape(len(z), -1) - .015 for a, n in planes]
        return np.column_stack([base_calc(z, *args)] + margins)

    nodes = core.parse_nodes(data["guidance"])
    target = np.asarray(data["fitted"]["soft_reference_xy_m"])
    fitted = np.asarray(data["fitted"]["global_fit"])
    trials = []
    control_solves = 0
    for method in ("secondary_exact", "secondary_closest", "secondary_refit", "margin_refit"):
        if method != "margin_refit":
            tr.m["rect"] = fitted.copy()
            start = seed
        soft = method != "secondary_exact"
        pad = 0.
        if method == "margin_refit":
            prev = receipt["continuous"]["road"]
            if prev["status"] != "UNRESOLVED_INTERVAL" or prev["dt_s"] != .001:
                break
            pad = 2 * (prev["min_sampled_boundary_distance_m"] - prev["lower_bound_m"])
            start = controls

        base_support = smooth.local.route_support_constraint
        def padded_support(body, region):
            return base_support(body, region) - pad

        with patch.object(tr, "calc", with_secondary), patch.object(smooth.local, "route_support_constraint", padded_support):
            if method == "secondary_refit":
                free, _ = smooth.solve_controls(tr, seed, nodes, True, road, target, free_seed=True)
                tr.m["rect"], _ = smooth.fit_to_seed(raw, fitted, tr, free)
                start = free
                control_solves += 1
            controls, info = smooth.solve_controls(tr, start, nodes, soft, road, target)
            control_solves += 1
        receipt = legacy.audit(tr, data["steering"], controls, False, True, tr.m["rect"], nodes, road,
                               secondary, road_file, visual_file, ctx, method, out, dict(**info))
        candidate = core.read(Path(receipt["candidate_path"]))
        margin = min(float(smooth.added_margins(st, controls[None], vehicle.dt, vehicle.L).min())
                     for st in (tr.states(controls[None]), np.asarray(candidate["state_xy_yaw_v"])[None]))
        goal = receipt["steering_motion"]
        accepted = bool(receipt["eligible_before_fixed_clearance"] and legacy.fixed_floor_check(receipt, data["floor"])
                        and margin >= -core.TOL and (goal["window_quality"] == "EXACT" or
                        (soft and goal["max_window_miss_m"] <= smooth.MISS_MAX + core.TOL)))
        trials.append(dict(method=method, accepted=accepted, position_excess_m=goal["max_window_miss_m"],
                           primary=receipt["continuous"]["road"]["status"], secondary=receipt["secondary_road"],
                           objects=receipt["continuous"]["objects"]["status"], candidate=method + "/candidate.json"))
        if accepted:
            break
    summary = dict(token=data["token"], control_solves=control_solves, trials=trials, accepted=trials[-1]["accepted"])
    core.save(out / "summary.json", summary)
    print(summary)
    return summary["accepted"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    raise SystemExit(0 if replay(parser.parse_args().out) else 1)
