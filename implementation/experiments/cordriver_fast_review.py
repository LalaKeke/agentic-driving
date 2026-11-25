"""Bind the displayed and default trajectory to the actual causal fast-only return."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from cordriver_astra_packet import clip_segment
from cordriver_edge_gate import BODY_LENGTH_M, BODY_WIDTH_M, native_body_knots, edge_clearance
from cordriver_published_xy import published_xy
from cordriver_reachable_options import load, save, sha

CONTRACT = "fast_return_body_sweep_v1"


def review_edges(legacy_trigger, poses, primary, secondary=None):
    first = edge_clearance(poses, primary)
    second = edge_clearance(poses, secondary) if secondary is not None else None
    triggered = bool(legacy_trigger or first["triggered"] or (second and second["triggered"]))
    return triggered, first, second


def approaching_boundary(first, second):
    """Both measured networks must predict a shrinking clearance within half a vehicle width."""
    for head in (first, second):
        if not head or head.get("min_clearance_lower_bound_m") is None or head.get("initial_clearance_m") is None:
            return False
        lower = head["min_clearance_lower_bound_m"]
        if lower > BODY_WIDTH_M / 2 or head["initial_clearance_m"] - lower < 0.30:
            return False
    return True


def fast_path(study, token, returned):
    sample_file = study / "frontend" / token / "sample.json"
    packet = study / "packets" / token / "packet.json"
    record = load(returned)
    if (
        record["policy"] != "fast"
        or record["token"] != token
        or record["native_sha256"] != sha(sample_file)
        or record["packet_sha256"] != sha(packet)
        or record["map_sha256"] != sha(sample_file.with_name("visual_map.json"))
        or record["secondary_sources_sha256"] != sha(study / "secondary_primary/sources.json")
        or record["secondary_input_sha256"]
        != (
            sha(study / "secondary_primary" / (token + ".json"))
            if (study / "secondary_primary" / (token + ".json")).is_file()
            else None
        )
    ):
        raise ValueError("FAST_RETURN_BINDING_MISMATCH")
    sample = load(sample_file)
    candidate = None
    if record["candidate"]:
        candidate = (returned.parent / record["candidate"]).resolve(strict=True)
        receipt = load(candidate.with_name("receipt.json"))
        if (
            returned.parent.resolve() not in candidate.parents
            or record["candidate_sha256"] != sha(candidate)
            or receipt["sha256"] != sha(candidate)
            or not receipt["eligible_before_fixed_clearance"]
            or published_xy(candidate).tolist() != record["returned_xy_lidar_m"]
        ):
            raise ValueError("UNBOUND_FAST_CANDIDATE")
        plan = load(candidate)
        poses = np.column_stack((plan["body_xy_m"], np.asarray(plan["state_xy_yaw_v"])[:, 2]))[::2]
        speeds = np.asarray(plan["state_xy_yaw_v"])[::2, 3]
        speed_source = "integrated longitudinal speed"
    else:
        if record["returned_xy_lidar_m"] != sample["final_xy_lidar_m"]:
            raise ValueError("FAST_NATIVE_COORDINATE_MISMATCH")
        states = load(study / "native_states.json")
        if states["frontend_sha256"] != sha(study / "frontend/frontend.json"):
            raise ValueError("FAST_NATIVE_STATE_BINDING_MISMATCH")
        (state,) = [r for r in states["samples"] if r["token"] == token]
        poses, status = native_body_knots(sample, state)
        if poses is None:
            return record, None, None, None, status
        if sample["fallback"]:
            speeds = np.r_[sample["can_bus"][13], np.linalg.norm(np.diff(poses[:, :2], axis=0), axis=1) / 0.5]
            speed_source = "measured initial and interval-average displacement speeds on fallback"
        else:
            speeds = np.asarray(state["state_xy_yaw_speed"])[::2, 3]
            speed_source = "published linearized QP speed"
    if (
        poses.shape != (7, 3)
        or speeds.shape != (7,)
        or not np.isfinite(poses).all()
        or not np.isfinite(speeds).all()
    ):
        raise ValueError("INVALID_FAST_STATES")
    return record, candidate, poses, speeds, speed_source


def review_packet(study, token, returned, folder):
    original = study / "packets" / token / "packet.json"
    packet = load(original)
    ctx = packet["model_context"]
    _, _, poses, speeds, source = fast_path(study, token, returned)
    if poses is None:
        raise ValueError("NO_BOUND_FAST_POSES")
    ctx.update(
        trajectory_contract=CONTRACT,
        trajectory_kind="Actually returned fast body-center trajectory. " + source,
        trajectory=[
            dict(
                t_s=i * 0.5,
                x_forward_m=float(x),
                y_left_m=float(y),
                nominal_yaw_rad=float(yaw),
                speed_mps=float(speed),
            )
            for i, ((x, y, yaw), speed) in enumerate(zip(poses, speeds))
        ],
        nominal_body_length_width_m=[BODY_LENGTH_M, BODY_WIDTH_M],
    )
    originals = [im for im in packet["images"] if im["label"].endswith(" original RGB")]
    if len(originals) != 6:
        raise ValueError("EXPECTED_SIX_ORIGINALS")
    (front,) = [im for im in originals if im["label"] == "CAM_FRONT original RGB"]
    f = (original.parent / front["path"]).resolve(strict=True)
    if sha(f) != front["sha256"]:
        raise ValueError("FRONT_DIGEST_MISMATCH")
    folder.mkdir(parents=True, exist_ok=False)
    with Image.open(f) as im:
        image = im.convert("RGB")
    draw = ImageDraw.Draw(image)
    w, h = image.size
    cam = ctx["cameras"]["CAM_FRONT"]
    k = np.array(cam["K_original_px"])
    matrix = np.array(cam["T_camera_from_reference_ego"])
    geom = ctx["overlay_geometry"]
    linear = np.array(geom["xy_guidance_to_reference_ego_linear"])
    origin = np.array(geom["xy_guidance_origin_in_reference_ego_m"])

    def draw_polyline(points, color, width):
        xy = np.asarray(points) @ linear.T + origin
        hom = np.column_stack(
            [xy, np.full(len(xy), geom["display_height_reference_ego_z_m"]), np.ones(len(xy))]
        )
        view = (hom @ matrix.T)[:, :3]
        count = 0
        for a, b in zip(view[:-1], view[1:]):
            clipped = clip_segment(a, b, k, w, h)
            if clipped is not None:
                draw.line((clipped[2], clipped[3]), fill=color, width=width)
                count += 1
        return count

    nav = draw_polyline(ctx["navigation"]["polyline_guidance_m"], "magenta", 3)
    corners = np.array([[1, 1], [1, -1], [-1, -1], [-1, 1]]) * [BODY_LENGTH_M / 2, BODY_WIDTH_M / 2]
    boxes = []
    for x, y, yaw in poses:
        c, s = np.cos(yaw), np.sin(yaw)
        boxes.append(corners @ np.array([[c, s], [-s, c]]) + [x, y])
    boxes = np.asarray(boxes)
    marks = 0
    for box in boxes:
        marks += draw_polyline(np.vstack([box, box[0]]), "orange", 2)
    for rail in boxes.transpose(1, 0, 2):
        marks += draw_polyline(rail, "orange", 2)
    center = draw_polyline(poses[:, :2], "cyan", 4)
    legend = "Cyan: fast body center | orange: nominal body / corner paths | magenta: navigation"
    if ctx["navigation"].get("polyline_source") == "official_pre_qp":
        legend = "Cyan: returned body center | orange: nominal body sweep | magenta: pre-QP prediction"
    draw.text((10, 8), legend, fill="white", stroke_width=2, stroke_fill="black")
    overlay = folder / "CAM_FRONT_fast.png"
    image.save(overlay)
    geom["visible_front_segments"] = dict(navigation=nav, fast_center=center, body_lines=marks)
    ctx["overlay_geometry"]["legend"] = legend
    packet["images"] = [
        dict(im, path=str((original.parent / im["path"]).resolve(strict=True))) for im in originals
    ]
    packet["images"].append(
        dict(label="CAM_FRONT same RGB | " + legend, path=str(overlay.resolve()), sha256=sha(overlay))
    )
    path = folder / "packet.json"
    save(path, packet)
    save(
        folder / "provenance.json",
        dict(
            token=token,
            original_packet_sha256=sha(original),
            fast_return_sha256=sha(returned),
            packet_sha256=sha(path),
            overlay_sha256=sha(overlay),
        ),
    )
    return path


def ranked_menu_controls(portfolio_path, sample_path, nodes, exclude_sha=None, trial=None):
    """Rank causal, certified controls by the requested windows in both integrations."""
    from cordriver_nav_solver import TOL
    from cordriver_reachable_options import select_option

    if not portfolio_path.exists():
        return []
    portfolio = load(portfolio_path)
    if portfolio["token"] != load(sample_path)["token"] or portfolio["source_sha256"].get(
        str(sample_path.resolve())
    ) != sha(sample_path):
        raise ValueError("MENU_WORLD_MISMATCH")
    ranked = []
    for public in portfolio["public_options"]:
        key = public["option_id"]
        option = select_option(portfolio_path, key)
        if option["candidate_sha256"] == exclude_sha:
            continue
        candidate = load(option["candidate_path"])
        receipt = load(Path(option["candidate_path"]).with_name("receipt.json"))
        analytic = receipt["steering_motion"]["analytic_window_checks"]
        rk4 = candidate["node_checks"]
        if len(analytic) != 2 or len(rk4) != 2 or len(nodes) != 2:
            raise ValueError("INVALID_WITNESS_NODE_COUNT")
        if [r["t_s"] for r in rk4] != [0.25 * n[0] for n in nodes]:
            # Cached certificates bind the controls, but their task checks use the old clock.
            from cordriver_nav_solver import geometry, node_report
            if trial is None:
                raise ValueError("RETIMED_WITNESS_REQUIRES_BOUND_WORLD")
            times = np.asarray(candidate["times_s"])
            if not np.array_equal(times, np.arange(13)*trial.p.dt):
                raise ValueError("INVALID_WITNESS_TRAJECTORY_GRID")
            z = np.asarray(candidate["controls_accel_steer"]).reshape(1,24)
            st = trial.states(z)
            centers, _ = geometry(st, trial.m["origin"], trial.p)
            analytic = node_report(nodes, centers[0], st[0])
            rk4 = node_report(nodes, np.asarray(candidate["body_xy_m"]), np.asarray(candidate["state_xy_yaw_v"]))
        distance = 0.0
        exact = True
        for checks in (analytic, rk4):
            for report, (i, center, basis, half, speeds) in zip(checks, nodes):
                if report["t_s"] != i * 0.25:
                    raise ValueError("INVALID_WITNESS_TIME")
                local = (np.asarray(report["center_xy_m"]) - center) @ basis
                excess = np.maximum(np.abs(local) - half, 0.0)
                speed = report["speed_mps"]
                dv = max(speeds[0] - speed, speed - speeds[1], 0.0)
                exact = exact and bool(np.max(excess) <= TOL and dv <= TOL)
                # Use window half-widths and 1 m/s as distance scales for warm-start ordering only.
                distance += float(np.sum((excess / half) ** 2) + dv**2)
        ranked.append((exact, distance, key, option))
    return sorted(ranked, key=lambda row: (not row[0], row[1], row[2]))
