"""Frozen open-loop whole-horizon corridor fitting and bicycle recovery, on causal inputs only."""
import base64
import hashlib
import json
from pathlib import Path
from cordriver_paths import CONFIG
import time
import traceback

import numpy as np
from scipy.optimize import lsq_linear, minimize
from shapely.geometry import Point, Polygon, mapping
from shapely.ops import unary_union
import cordriver_iter_road as visual_road

import cordriver_expansion_plan as legacy
import cordriver_iter_planner as local
import cordriver_nav_solver as core

LAT_MAX = 3.0
JERK_MAX = 5.0
MISS_MAX = 1.0
TIMES = np.arange(0., 3.01, .5)
PROTOCOL = CONFIG / 'protocol.json'


def fit_signal(initial, observations, lower, upper, first_weight, second_weight, absolute_second=None):
    """Fit one complete six-knot scalar sequence with squared, bounded differences."""
    eye = np.eye(7)
    matrices, targets = [], []
    for index, value, weight in observations:
        matrices.append(eye[index, 1:] * np.sqrt(weight))
        targets.append((value - eye[index, 0] * initial) * np.sqrt(weight))
    for order, weight in [(1, first_weight), (2, second_weight)]:
        derivative = np.diff(eye, n=order, axis=0)
        for j, row in enumerate(derivative):
            rhs = -row[0] * initial
            if order == 2 and absolute_second is not None:
                rhs -= derivative[j] @ absolute_second
            matrices.append(np.sqrt(weight) * row[1:])
            targets.append(np.sqrt(weight) * rhs)
    fitted = lsq_linear(matrices, targets, bounds=(lower, upper), tol=1e-10)
    if not fitted.success or not np.isfinite(fitted.x).all():
        raise ValueError("GLOBAL_RECTANGLE_FIT_FAILED")
    return fitted.x


def principal_axis_difference(angle):
    """An unoriented rectangle axis has period pi; tie the right angle to -pi/2."""
    delta = np.angle(np.exp(2j * angle)) / 2.
    return np.where(np.isclose(np.abs(delta), np.pi / 2., rtol=0., atol=1e-10), -np.pi / 2., delta)


def unwrap_rect_axes(angle):
    # Preserve the ordinary unwrap on non-tied data, including archived experiments.
    tied = np.isclose(np.abs(np.angle(np.exp(2j * np.diff(angle)))), np.pi, rtol=0., atol=2e-10)
    if not np.any(tied):
        return np.unwrap(2. * angle) / 2.
    seed = principal_axis_difference(angle[0])
    return np.r_[seed, seed + np.cumsum(principal_axis_difference(np.diff(angle)))]


def fit_corridor(raw, context, nodes, width_ego):
    ref = np.array([[t['x_forward_m'], t['y_left_m']] for t in context['trajectory']])
    if ref.shape != (7, 2) or not np.allclose([t['t_s'] for t in context['trajectory']], TIMES):
        raise ValueError("REFERENCE_TIME_GRID")
    corrections = {}
    for index, center, basis, half, _ in nodes:
        k = index // 2
        local_xy = (ref[k] - center) @ basis
        target = center + np.clip(local_xy, -half, half) @ basis.T
        corrections[k] = target - ref[k]
    corrected = raw.copy()
    target_path = ref.copy()
    for axis, bound in enumerate(core.SHIFT[:2]):
        obs = [(k, 0., .9) for k in range(1, 7)]
        obs += [(k, diff[axis], 4.) for k, diff in corrections.items()]
        displacement = fit_signal(0., obs, -bound, bound, .8, 3.0)
        target_path[1:, axis] += displacement
        # Keep the learned offset between the BODY reference and each road rectangle.
        # Penalise second differences of actual centers as well as movement from the network.
        axis_origin = raw[0, axis] - (raw[1, axis] - raw[0, axis])
        observations = [(k + 1, v, 1.) for k, v in enumerate(displacement)]
        dc = fit_signal(0., observations, -bound, bound, .4, .6, np.r_[axis_origin, raw[:, axis]])
        corrected[:, axis] += dc

    short_axis = np.argmin(raw[:, 3:], axis=1)
    short_index = 3 + short_axis
    # An oriented rectangle is invariant to a pi rotation. Unwrap its long axis,
    # keeping the original side ordering in the downstream native representation.
    long_heading = unwrap_rect_axes(raw[:, 2] + np.where(short_axis == 0, np.pi / 2., 0.))
    initial_heading = long_heading[0]
    segments = np.diff(target_path, axis=0)
    nominal = np.diff(ref, axis=0)
    delta_angle = np.angle(np.exp(1j * (np.arctan2(segments[:, 1], segments[:, 0]) -
                                        np.arctan2(nominal[:, 1], nominal[:, 0]))))
    delta_angle[(np.linalg.norm(nominal, axis=1) < .4) | (np.linalg.norm(segments, axis=1) < .4)] = 0.
    limit = core.SHIFT[2]
    obs = [(k + 1, float(np.clip(value, -limit, limit)), 1.) for k, value in enumerate(delta_angle)]
    corrected[:, 2] += fit_signal(0., obs, -limit, limit, 1., 2., np.r_[initial_heading, long_heading])

    short = raw[np.arange(6), short_index]
    wanted = np.maximum(short, width_ego + .15)
    width_obs = [(k + 1, value - short[k], 1.) for k, value in enumerate(wanted)]
    add = fit_signal(0., width_obs, 0., width_ego, .7, .45, np.r_[short[0], short])
    corrected[np.arange(6), short_index] += add
    return corrected, target_path


def added_margins(states, controls, dt, wheelbase):
    steer = controls[:, 1:24:2]
    curvature = np.tan(steer) / wheelbase
    lat = np.concatenate((states[:, :-1, 3]**2 * curvature, states[:, 1:, 3]**2 * curvature), axis=1)
    jerk = np.diff(controls[:, :24:2], axis=1) / dt
    return np.concatenate((LAT_MAX - lat, LAT_MAX + lat, JERK_MAX - jerk, JERK_MAX + jerk), axis=1)


def solve_controls(tr, seed, nodes, soft, road, target_path, free_seed=False):
    size = 24 + 4 * soft
    start = np.zeros(size)
    start[:24] = seed[:24]
    initial_state = tr.states(start[None])
    xy, body = core.geometry(initial_state, tr.m['origin'], tr.p)
    if soft:
        error = np.concatenate([r['signed_outside_long_lat_m'] for r in core.node_report(nodes, xy[0], initial_state[0])])
        start[-4:] = np.clip(np.abs(error) + .002, 0., MISS_MAX)
    nearby = local.candidate_obstacles(tr, body[0], limit=8)
    perturb = np.eye(size) * 1e-20j
    bounds = [(-tr.p.a_max, tr.p.a_max), (-tr.p.delta_max, tr.p.delta_max)] * 12
    bounds.extend([(0., MISS_MAX)] * (4 * soft))
    q, p = tr.m['q'], tr.m['p']
    qp_scale = max(1., np.max(np.abs(q @ start[:24] + p)))

    def objective(z):
        states = tr.states(z)
        centers, _ = core.geometry(states, tr.m['origin'], tr.p)
        error = centers[:, 2::2] - target_path[None, 1:]
        ac, steer = z[:, :24:2], z[:, 1:24:2]
        jerk = np.diff(ac, axis=1) / tr.p.dt
        delta_rate = np.diff(steer, axis=1) / tr.p.dt
        ay = states[:, :-1, 3]**2 * np.tan(steer) / tr.p.L
        lat_change = np.diff(ay, axis=1) / tr.p.dt
        native = (.5 * np.einsum('bi,ij,bj->b', z[:, :24], q, z[:, :24]) + z[:, :24] @ p) / qp_scale
        cost = (np.mean(np.sum(error**2, axis=2), axis=1) + .05 * native + .035 * np.mean(jerk**2, axis=1)
                + .3 * np.mean(delta_rate**2, axis=1) + .04 * np.mean(ay**2, axis=1)
                + .018 * np.mean(lat_change**2, axis=1))
        if soft:
            cost += 10. * np.sum(z[:, -4:]**2, axis=1)
        return cost

    def residual(z):
        states = tr.states(z)
        _, b = core.geometry(states, tr.m['origin'], tr.p)
        native = tr.calc(z, nodes, False, soft, [])
        if free_seed:
            native = native[:, 16 * len(tr.m["rect"]):]
        return np.column_stack((native, local.signed_gap(b, nearby) - .015,
                                local.route_support_constraint(b, road), added_margins(states, z, tr.p.dt, tr.p.L)))

    res = minimize(lambda z: objective(z[None])[0], start, method='SLSQP', bounds=bounds,
                   jac=lambda z: objective(z[None] + perturb).imag / 1e-20,
                   constraints=[dict(type='ineq', fun=lambda z: residual(z[None])[0],
                                     jac=lambda z: residual(z[None] + perturb).imag.T / 1e-20)],
                   options=dict(maxiter=80, ftol=1e-8))
    return res.x[:24], dict(iterations=int(res.nit), optimizer_status=str(res.message),
                            solver_converged=bool(res.success), objective=float(res.fun))



def fit_to_seed(raw, fitted, tr, controls):
    """Project all six rectangles together onto a causal dynamical search seed.

    Fix candidate orientations along the bounded, regularised seed heading. The
    remaining corner constraints are affine in rectangle centers and short widths.
    The convex hinge loss proposes an envelope; only the final re-integrated
    independent audits can make a candidate eligible.
    """
    state = tr.states(controls[None])
    _, bodies = core.geometry(state, tr.m['origin'], tr.p)
    bodies = bodies[0, 2::2]
    side = np.argmin(raw[:, 3:], axis=1)
    long_angle = raw[:, 2] + (side == 0) * np.pi / 2.
    desired = principal_axis_difference(state[0, 2::2, 2] - long_angle)
    start = unwrap_rect_axes(long_angle)
    angle_limit = core.SHIFT[2]
    obs = [(j+1, .7 * desired[j] + .3 * (fitted[j,2] - raw[j,2]), 1.) for j in range(6)]
    heading = raw[:, 2] + fit_signal(0., obs, -angle_limit, angle_limit, .3, .6, np.r_[start[0], start])

    # Each row gives a signed interior-edge margin, A @ displacement + b.
    constraint_rows, b = [], []
    for j, rect in enumerate(raw):
        for ax in range(2):
            direction = core.rotation(heading[j])[:,ax]
            projections = (bodies[j] - rect[:2]) @ direction
            for sign in (-1., 1.):
                row = np.zeros((6,3))
                row[j,:2] = -sign * direction
                row[j,2] = .5 if ax == side[j] else 0.
                constraint_rows.append(row.ravel())
                b.append(.5 * rect[3+ax] + min(sign * projections) - .008)
    A, b = np.array(constraint_rows), np.array(b)
    start = np.column_stack((fitted[:,:2]-raw[:,:2], (fitted[:,3:]-raw[:,3:]).max(axis=1))).ravel()
    # All terms including second differences of true centers/widths use common meters.
    dif = np.diff(np.eye(6), n=2, axis=0)
    D = np.kron(dif, np.eye(3))
    offsets = np.column_stack((raw[:,:2], raw[np.arange(6),3+side])).ravel()
    def cost(z):
        hinge = np.minimum(A @ z + b, 0.)
        curvature = D @ (z+offsets)
        error = z - start
        return error @ error + .7 * curvature @ curvature + 1000. * (hinge @ hinge)
    def grad(z):
        return 2.*(z-start) + 1.4*D.T@(D@(z+offsets)) + 2000.*A.T@np.minimum(A@z+b, 0.)
    opt = minimize(cost,start,jac=grad,bounds=[(-2.,2.),(-3.,3.),(0.,tr.p.w)]*6,
                   method='L-BFGS-B',options=dict(maxiter=220,ftol=1e-12))
    adjusted = raw.copy()
    deformation = opt.x.reshape(6,3)
    adjusted[:,:2] += deformation[:,:2]
    adjusted[:,2] = heading
    adjusted[np.arange(6),3+side] += deformation[:,2]
    return adjusted, dict(fit_status=str(opt.message),seed_corner_min_margin_m=float(np.min(A@opt.x+b)))


def measured_chart_components(curve, edges):
    """Rebuild the original accepted finite cells also for fragments outside the ego component."""
    road = visual_road.build_road(curve, edges)
    frame, _ = visual_road.route_basis(curve)
    previous = [None, None]
    previous_frame = frame(road.cuts[0]['s'])[:2]
    polygons = []
    for cut, cell in zip(road.cuts, road.cells):
        center, tangent, normal = frame(cut['s'])
        hits = [p for edge in edges.values() for p in visual_road.crossings(edge, center, tangent, normal)]
        right = min((h for h in hits if h.lateral < 0.), key=lambda h: (-h.lateral,h.rank), default=None)
        left = min((h for h in hits if h.lateral >= 0.), key=lambda h: (h.lateral,h.rank), default=None)
        current = [right, left]
        poly, reason = visual_road.segment_cell(previous,current,edges,(previous_frame,(center,tangent)))
        if reason != cell['reason']:
            raise ValueError('FINITE_CELL_RECONSTRUCTION_MISMATCH')
        if poly is not None:
            polygons.append(poly)
        previous, previous_frame = current, (center,tangent)
    merged = unary_union(polygons)
    return [merged] if merged.geom_type == 'Polygon' else [p for p in merged.geoms if p.geom_type == 'Polygon']


def chart_atlas(curves, edges):
    parts = [p for curve in curves for p in measured_chart_components(curve, edges)]
    reached = {i for i,p in enumerate(parts) if p.covers(Point(0.,0.))}
    while True:
        new = {j for j,p in enumerate(parts) if j not in reached
               and any(p.intersection(parts[i]).area > 1e-6 for i in reached)}
        if not new:
            break
        reached.update(new)
    return unary_union([parts[i] for i in sorted(reached)]), dict(finite_components=len(parts), connected_by_positive_area_overlap=len(reached))


def bind_final_guidance_packet(path, original_path, sample_path, original, sample):
    """Bind the model's final-path packet to the optimizer's original learned-reference packet."""
    from cordriver_edge_gate import native_body_knots

    actual = core.read(path)
    proof_path = path.with_name('provenance.json')
    proof = core.read(proof_path)
    model_ctx, planning_ctx = actual['model_context'], original['model_context']
    for key in ('sample_token','reference_time_us','guidance_frame','navigation','initial_speed_mps',
                'measured_ego_state','cameras','sensor_scope'):
        if model_ctx[key] != planning_ctx[key]:
            raise ValueError('MODEL_AND_PLANNER_OBSERVATION_DIFFER')
    for key in ('display_height_reference_ego_z_m','xy_guidance_to_reference_ego_linear','xy_guidance_origin_in_reference_ego_m'):
        if model_ctx['overlay_geometry'][key] != planning_ctx['overlay_geometry'][key]:
            raise ValueError('MODEL_AND_PLANNER_FRAMES_DIFFER')
    token = sample['token']
    source = original_path.resolve().parents[2]
    state_path = source/'native_states.json'
    front_path = source/'frontend/frontend.json'
    if (actual['token'] != token or proof['token'] != token or proof['packet_sha256'] != legacy.sha(path)
            or proof['original_packet_sha256'] != legacy.sha(original_path)
            or proof['sample_sha256'] != legacy.sha(sample_path) or proof['frontend_sha256'] != legacy.sha(front_path)
            or proof['native_states_sha256'] != legacy.sha(state_path) or
            model_ctx['trajectory_contract'] != 'cor_final_body_sweep_v1'):
        raise ValueError('FINAL_NATIVE_SOURCE_BINDING_MISMATCH')
    state = core.read(state_path)
    if state['frontend_sha256'] != proof['frontend_sha256']:
        raise ValueError('FINAL_STATE_ARCHIVE_MISMATCH')
    native, = [r for r in state['samples'] if r['token']==token]
    poses, status = native_body_knots(sample, native)
    fields = ('x_forward_m','y_left_m','nominal_yaw_rad')
    rows = model_ctx['trajectory']
    if (poses is None or model_ctx['native_return'] != status or proof['actual_return'] != status or
            len(rows) != len(TIMES) or not np.array_equal([r['t_s'] for r in rows], TIMES) or
            not np.allclose([[r[k] for k in fields] for r in rows], poses, atol=1e-8, rtol=0)):
        raise ValueError('MODEL_TRAJECTORY_IS_NOT_PUBLISHED_RETURN')
    orig_rgb = [(im['label'], im['sha256']) for im in original['images'] if im['label'].endswith(' original RGB')]
    actual_rgb = [(im['label'], im['sha256']) for im in actual['images'] if im['label'].endswith(' original RGB')]
    if (len(orig_rgb) != 6 or actual_rgb != orig_rgb or len(actual['images']) != 7 or
            actual['images'][-1]['sha256'] != proof['overlay_sha256']):
        raise ValueError('MODEL_GUIDANCE_CAMERA_MISMATCH')
    return actual, [path, proof_path, state_path, front_path]


def run_case(args, *, guidance_packet=None, frozen_response=None):
    study, guidance, out, model, case, data, weight, refine, dual_chart, finite_atlas = args
    fixed_keep = model == "DETERMINISTIC_KEEP"
    arm = "diagnostic" if fixed_keep else "astra"
    token = case['token']
    folder = out / 'planning' / arm / token
    if (folder / 'result.json').exists():
        if guidance_packet is not None:
            old_inputs = core.read(folder/'inputs.json')['files']
            for item in (guidance_packet, guidance/token/'native_output.json', guidance/token/'request.json',
                         Path(__file__)):
                if old_inputs.get(str(item.resolve())) != legacy.sha(item):
                    raise ValueError('CACHED_GUIDANCE_BINDING_MISMATCH')
        return core.read(folder / 'result.json')
    started = time.monotonic()
    folder.mkdir(parents=True, exist_ok=False)
    root = dict(scene=case['name'], token=token, arm=arm, selected_path=None, selected_method=None,
                selected_task=None, candidates=[], eligible=False, reasons=[], control_solves=0, fitted_seed_solves=0)
    guide_file = guidance / token / 'native_output.json'
    try:
        reg = study / 'selection/registration.json'
        frontend = study / 'frontend' / token / 'sample.json'
        vis_file = frontend.with_name('visual_map.json')
        pkt = study / 'packets' / token / 'packet.json'
        receipt_file = guide_file.with_name('receipt.json')
        if frozen_response is not None:
            from cordriver_guidance_wire import bind_frozen
            response, guide = bind_frozen(guidance_packet or pkt, frozen_response)
        else:
            guide = legacy.KEEP if fixed_keep else core.read(guide_file)
            response = {} if fixed_keep else core.read(receipt_file)
        if not fixed_keep and (response['token'] != token or response['requested_model'] != model or
                response['returned_model'] != model or response['http'] != 200):
            raise ValueError('GUIDANCE_ID_BINDING')
        row, visual, packet = core.read(frontend), core.read(vis_file), core.read(pkt)
        context = packet['model_context']
        assert token == row['token'] == visual['token'] == packet['token'] == context['sample_token']
        assert row['scene_name'] == case['name'] and row['timestamp_us'] == context['reference_time_us']
        sources = [reg, frontend, vis_file, pkt, PROTOCOL, Path(__file__)]
        if finite_atlas:
            sources.append(PROTOCOL.with_name('finite_atlas_protocol.json'))
        if dual_chart:
            sources.append(PROTOCOL.with_name('dual_chart_protocol.json'))
        if refine or fixed_keep:
            sources.append(PROTOCOL.with_name('secondary_protocol.json'))
        model_packet = packet
        if guidance_packet is not None:
            if fixed_keep:
                raise ValueError('FIXED_KEEP_HAS_NO_MODEL_PACKET')
            model_packet, bound_files = bind_final_guidance_packet(guidance_packet, pkt, frontend, packet, row)
            sources += bound_files
        if frozen_response is not None:
            sources.append(frozen_response)
        elif not fixed_keep:
            envelope_file = guide_file.with_name('native_envelope.json')
            request_file = guide_file.with_name('request.json')
            envelope, request = core.read(envelope_file), core.read(request_file)
            messages = [part['text'] for item in envelope['output'] for part in item.get('content',[])
                        if part.get('type') == 'output_text']
            content = request['input'][0]['content']
            if (envelope['model'] != model or request['model'] != model or json.loads(''.join(messages)) != guide
                    or json.loads(content[0]['text']) != model_packet['model_context']):
                raise ValueError('NATIVE_REQUEST_CONTEXT_OR_RESPONSE_MISMATCH')
            urls = [item['image_url'] for item in content if item.get('type') == 'input_image']
            image_hashes = [hashlib.sha256(base64.b64decode(url.split(',',1)[1], validate=True)).hexdigest() for url in urls]
            if image_hashes != [img['sha256'] for img in model_packet['images']]:
                raise ValueError('NATIVE_REQUEST_FRAME_DIGEST_MISMATCH')
            sources += [receipt_file, guide_file, envelope_file, request_file]
        nodes = core.parse_nodes(guide)
        core.save(folder / 'inputs.json', dict(token=token, scene=case['name'], arm=arm, model=model,
                                              guidance=guide, qp_weight=weight,
                                              files={str(p.resolve()): legacy.sha(p) for p in sources}))
        root['decision'] = guide['decision']
        can = data / 'can_bus' / (case['name'] + '_pose.json')
        try:
            steering, history = legacy.derive(can, row)
        except (FileNotFoundError, ValueError) as exc:
            root.update(status='NO_RELEASE', reasons=['CAN_INITIAL_STEERING_UNOBSERVABLE', str(exc)])
            return finish(folder, root, started)
        root['initial_steering'] = dict(value_rad=steering, count=len(history), latest_us=history[-1]['utime'],
                                        pose_file=str(can), sha256=legacy.sha(can))
        planner, native = core.compose_native(row, weight, 'cpu')
        tr = core.Trial(planner, native, row, visual)
        legacy.bind_initial_assumption(tr, steering)
        raw = tr.m['rect'].copy()
        road = legacy.build_road(context['navigation']['polyline_guidance_m'], legacy.edges_from_features(visual['map_features']))
        initial = Polygon(legacy.BODY_CORNERS)
        needs_chart = not road.support.covers(initial) or any(e.line.intersects(initial) for e in road.edges.values())
        if needs_chart and not dual_chart:
            root.update(status='NO_RELEASE', reasons=['INITIAL_BODY_LACKS_ROAD_SUPPORT'])
            return finish(folder, root, started)
        secondary = None
        ext = study / 'secondary_primary'
        segment, = [r for r in core.read(ext / 'sources.json')['cases'] if r['token'] == token]
        assert segment['admitted'] == (ext / (token + '.json')).exists()
        if segment['admitted']:
            secondary_payload = core.read(ext / (token + '.json'))
            assert (segment['same_primary_detection_matches'] and segment['latest_exposure_minus_decision_us'] <= 0
                    and secondary_payload['token'] == token and secondary_payload['cameras'] == segment['current_cameras'])
            secondary = legacy.build_road(road.route, legacy.edges_from_features(secondary_payload['features']))
        if dual_chart and needs_chart:
            if secondary is None:
                root.update(status='NO_RELEASE', reasons=['MISSING_SECOND_VISUAL_HEAD_FOR_EGO_CHART'])
                return finish(folder, root, started)
            if any(edge.line.intersects(initial) for head in (road,secondary) for edge in head.edges.values()):
                root.update(status='NO_RELEASE', reasons=['MEASURED_BOUNDARY_INTERSECTS_INITIAL_FOOTPRINT'])
                return finish(folder, root, started)
            chart = [[0.,0.],[15.,0.]]
            primary_chart = legacy.build_road(chart,road.edges)
            secondary_chart = legacy.build_road(chart,secondary.edges)
            if finite_atlas:
                learned_ref = [[r['x_forward_m'], r['y_left_m']] for r in context['trajectory']]
                curves = [chart, context['navigation']['polyline_guidance_m'], learned_ref]
                primary_chart.support, primary_meta = chart_atlas(curves, road.edges)
                secondary_chart.support, secondary_meta = chart_atlas(curves, secondary.edges)
                root['finite_atlas'] = dict(primary=primary_meta, secondary=secondary_meta)
            joint = primary_chart.support.intersection(secondary_chart.support)
            root['dual_chart'] = dict(primary_source_sha256=legacy.sha(vis_file),
                    secondary_source_sha256=legacy.sha(ext / (token + '.json')),
                    primary_area_m2=primary_chart.support.area,secondary_area_m2=secondary_chart.support.area,
                    joint_area_m2=joint.area,joint_covers_initial=joint.covers(initial),chart_xy_m=chart)
            if not joint.covers(initial):
                root.update(status='NO_RELEASE', reasons=['BOTH_MEASURED_ROAD_CHARTS_LACK_JOINT_SUPPORT'])
                return finish(folder, root, started)
            # Use every boundary from both heads in the interval certificate. IDs can coincide.
            primary_chart.support = joint
            primary_chart.edges = {**{'primary_'+str(i):e for i,e in road.edges.items()},
                                   **{'secondary_'+str(i):e for i,e in secondary.edges.items()}}
            road = primary_chart
        road_file = folder / 'road.json'
        core.save(road_file, dict(support=mapping(road.support), edges=[mapping(e.line) for e in road.edges.values()],
                                  area_m2=road.support.area, edge_ranks=list(road.edges)))
        corrected, target = fit_corridor(raw, context, nodes, tr.p.w)
        native_ref = np.array([[r['x_forward_m'], r['y_left_m']] for r in context['trajectory']])
        core.save(folder / 'fitted_corridor.json', dict(original=raw.tolist(), global_fit=corrected.tolist(),
                  learned_reference_xy_m=native_ref.tolist(), soft_reference_xy_m=target.tolist(),
                  units='BODY center ego x forward/y left, radians, side lengths meters; time 0.5:0.5:3'))
        # The stored floor depends only on the verified fixed-corridor reference-rule controls
        # and front-end predicted obstacles. It is never a recorded-world clearance.
        diagnostic = study / 'planning/diagnostic' / token / 'result.json'
        original = core.read(diagnostic)
        assert original['token'] == token and original['arm'] == 'diagnostic'
        floor = original['fixed_clearance_knot_dense_m']
        root.update(fixed_clearance_knot_dense_m=floor, clearance_floor_sha256=legacy.sha(diagnostic))
        native_ok = bool(row['verified_qp_output'] and row['fallback'] is False and row.get('qp_controls_accel_steer') is not None)
        seed = np.asarray(row['qp_controls_accel_steer']).ravel() if native_ok else core.secondary_seed(tr, nodes)
        assert seed.shape == (24,) and np.isfinite(seed).all()
        methods = (['native_eligible'] if native_ok else []) + ['fixed_smooth_controls', 'global_smooth_corridor']
        if nodes:
            methods.append('global_smooth_closest')
        elif fixed_keep:
            methods.append('global_smooth_alternate')
        if refine:
            methods.append('global_body_refinement')
        for method in methods:
            fitted = method.startswith('global_')
            rect = corrected if fitted else raw
            extra = {}
            if method == 'global_body_refinement':
                tr.m['rect'] = corrected.copy()
                seed = core.secondary_seed(tr, nodes)
                root['control_solves'] += 1
                root['fitted_seed_solves'] += 1
                seed, warm = solve_controls(tr, seed, nodes, bool(nodes), road, target, free_seed=True)
                rect, placement = fit_to_seed(raw, corrected, tr, seed)
                extra = dict(warm_start=dict(source='synthetic seed; no-release search ignoring learned rectangles only',
                                             controls=seed.reshape(12,2).tolist(), **warm), placement=placement)
            if method == 'global_smooth_alternate':
                seed = core.secondary_seed(tr, nodes)
            tr.m['rect'] = rect.copy()
            if method == 'native_eligible':
                z, info = seed, dict(source='FROZEN_NATIVE_GPU_CONTROLS')
            else:
                soft = method in ('global_smooth_closest','global_body_refinement') and bool(nodes)
                root['control_solves'] += 1
                z, info = solve_controls(tr, seed, nodes, soft, road, target if fitted else native_ref)
                seed = z
            shifts = rect[:, :3] - raw[:, :3]
            width_add = np.max(rect[:, 3:] - raw[:, 3:], axis=1)
            meta = dict(**info, **extra, original_rectangles_ego=raw.tolist(), rectangle_delta_xy_yaw=shifts.tolist(),
                        expanded_short_dimension_m=width_add.tolist(), max_correction_m=float(np.max(np.linalg.norm(shifts[:, :2], axis=1))),
                        max_correction_deg=float(np.rad2deg(np.max(np.abs(shifts[:, 2])))),
                        original_short_dimension_m=np.min(raw[:, 3:], axis=1).tolist(),
                        corridor_basis='global smooth fit' if fitted else 'unmodified network prediction')
            # Supply the final envelope to the legacy clipping routine as both its geometry and cap.
            # This keeps the fitted width fixed; the metadata above records the change vs original.
            r = legacy.audit(tr, steering, z, False, True, rect, nodes, road, secondary,
                             road_file, vis_file, context, method, folder, meta)
            plan = core.read(Path(r['candidate_path']))
            # Audit both the analytic constant controls and the native RK4 sampler independently.
            smooth_margin = min(float(added_margins(states, z[None], tr.p.dt, tr.p.L).min())
                                for states in (tr.states(z[None]), np.asarray(plan['state_xy_yaw_v'])[None]))
            miss = r['steering_motion']['max_window_miss_m']
            quality = r['steering_motion']['window_quality']
            fit_ok = quality != 'APPROXIMATE' or (method in ('global_smooth_closest','global_body_refinement') and miss <= MISS_MAX + core.TOL)
            floor_ok = legacy.fixed_floor_check(r, floor)
            passed = bool(r['eligible_before_fixed_clearance'] and smooth_margin >= -core.TOL and fit_ok and floor_ok)
            item = dict(method=method, candidate_path=r['candidate_path'], sha256=r['sha256'], eligible=passed,
                        smooth_margin=smooth_margin, clearance_floor_pass=floor_ok, window_policy_pass=fit_ok,
                        gates_without_clearance_floor=r['eligible_before_fixed_clearance'],
                        full_receipt_path=str((Path(r['candidate_path']).parent / 'receipt.json').resolve()))
            root['candidates'].append(item)
            if passed:
                root.update(selected_path=r['candidate_path'], selected_method=method, selected_task=quality,
                            selected_sha256=r['sha256'], eligible=True, selected_window_miss_m=miss)
                break
        root['status'] = 'CONDITIONAL_PASS' if root['eligible'] else 'NO_RELEASE'
        if not root['eligible']:
            root['reasons'].append('NO_VERIFIED_CANDIDATE_WITHIN_SEARCH_BUDGET')
    except Exception as exc:
        root.update(status='COMPUTATION_ERROR', exception=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
    return finish(folder, root, started)


def finish(folder, result, started):
    result['wall_seconds'] = time.monotonic() - started
    core.save(folder / 'result.json', result)
    print(result['scene'], result['status'], result.get('selected_method'), result.get('message', ''), flush=True)
    return result
