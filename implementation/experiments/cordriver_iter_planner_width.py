"""Isolated width-repair experiment; the unmodified planning receipts remain intact."""

import numpy as np
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

import cordriver_iter_planner as it
import cordriver_nav_solver as core
from cordriver_iter_road import check_road, load_road


def project_rect(rect):
    center, yaw = rect[:2], rect[2]
    unit_corners = np.array([[1,1],[1,-1],[-1,-1],[-1,1]])
    return Polygon(unit_corners*(rect[3:]/2)@core.rotation(yaw).T+center)


def trim_and_audit(trial, original, controls, nodes, moving, road):
    expanded = trial.m["rect"]
    analytic = trial.states(controls[None])
    rk = trial.audit(controls,nodes,moving)
    _, c_analytic = core.geometry(analytic,trial.m["origin"],trial.p)
    _, c_rk = core.geometry(np.asarray(rk["state_xy_yaw_v"])[None],trial.m["origin"],trial.p)
    shift = core.corridor_moves(controls[None])[0] if moving else np.zeros((6,3))
    trimmed = original.copy()
    for j, rect in enumerate(original):
        axis = int(np.argmin(rect[3:]))
        final_yaw = rect[2]+shift[j,2]
        origin = rect[:2]+shift[j,:2]
        direction = core.rotation(final_yaw)[:,axis]
        max_projection = max(float(np.max(np.abs((body[0,2*j+2]-origin)@direction))) for body in (c_rk,c_analytic))
        required = 2*(max_projection+.0015)
        if required > trimmed[j,3+axis]:
            trimmed[j,3+axis] = min(required, expanded[j,3+axis])
    try:
        trial.m["rect"] = trimmed
        result = it.include_road(trial.audit(controls,nodes,moving),road)
    finally:
        trial.m["rect"] = expanded
    changes = np.asarray(trimmed[:,3:])-original[:,3:]
    result["expanded_short_dimension_m"] = changes.max(axis=1).tolist()
    result["original_short_dimension_m"] = original[:,3:].min(axis=1).tolist()
    result["effective_corridor"] = []
    for j, corrected in enumerate(np.asarray(result["rectangles_ego_body_xy_yaw_l_w"])):
        enlarged = project_rect(corrected)
        raw_aligned = corrected.copy()
        raw_aligned[3:] = original[j,3:]
        obstacles = unary_union([poly for _, _, poly in trial.obs_polys[2*j+2]])
        supported = enlarged.intersection(road.support).difference(obstacles)
        body = Polygon(c_rk[0,2*j+2])
        in_supported = supported.covers(body)
        extra = supported.difference(project_rect(raw_aligned))
        result["effective_corridor"].append(dict(t_s=float((j+1)*.5),expanded_rectangle_area_m2=enlarged.area,
            effective_area_m2=supported.area,additional_supported_m2=extra.area,contains_reintegrated_ego=in_supported,
            all_object_mode_polygons=len(trial.obs_polys[2*j+2]),effective_geojson=mapping(supported)))
        # Full expanded rectangle is only a numerical envelope; with an empty/outside road do not count a pass.
        if result["environment_result"]=="CONDITIONAL_PASS" and not in_supported:
            result["environment_result"] = "REJECTED"
            result["effective_corridor_rejection"] = "FOOTPRINT_NOT_IN_ROAD_AND_OBJECT_CLIPPED_RECTANGLE"
    return result
