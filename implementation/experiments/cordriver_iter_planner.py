"""Development replay of frozen CorDriver controls with dynamic object separators and retained feasible incumbents."""
import time

import numpy as np
from scipy.optimize import minimize

import cordriver_nav_solver as core

TOL = core.TOL


def recover_params(candidate, moving):
    controls = np.asarray(candidate["controls_accel_steer"]).reshape(-1)
    if not moving:
        return controls
    delta = np.asarray(candidate["rectangle_delta_xy_yaw"])
    return np.r_[controls, delta[[2, 5]].ravel()]


def seed_bank(trial, nodes):
    ref = core.secondary_seed(trial, nodes).reshape(12, 2)
    v0 = trial.m["start"][3]
    speeds = [float(np.mean(n[-1])) for n in nodes] if nodes else [v0, v0]
    accelerations = np.r_[np.repeat((speeds[0]-v0)/1.5, 6),
                          np.repeat((speeds[1]-speeds[0])/1.5, 6)]
    bank = []
    for approach in (-1., 0., 1.):
        for steer in (-.48, -.24, -.12, 0., .12, .24, .48):
            for finish in (-1., 0., 1.):
                targets = ref[:, 1] + np.r_[np.repeat(steer, 4), np.repeat(steer*finish, 8)]
                u = np.zeros((12, 2))
                current = v0
                for j in range(12):
                    u[j, 0] = np.clip(accelerations[j] + approach * (1 if j < 6 else -1),
                                      max(-trial.p.a_max, -current/trial.p.dt), trial.p.a_max)
                    current += trial.p.dt*u[j, 0]
                    previous = u[j-1, 1] if j else 0.
                    u[j, 1] = np.clip(targets[j], previous-trial.p.ddelta_max*trial.p.dt,
                                      previous+trial.p.ddelta_max*trial.p.dt)
                bank.append(u.ravel())
    return np.asarray(bank)


def candidate_obstacles(trial, body, limit=12):
    """Unique foreground predictions closest to the seed at each sample, without fixing any separating direction."""
    selected = []
    for t, series in enumerate(trial.series):
        ranked = []
        for rank, mode, ring in series:
            center = np.mean(ring, axis=0)
            bound = np.linalg.norm(np.mean(body[t], axis=0)-center) - np.linalg.norm(ring[0]-center)
            if bound < 12:
                ranked.append((bound, rank, mode, ring))
        seen = set()
        for _, rank, mode, ring in sorted(ranked, key=lambda r:r[:3]):
            key = np.asarray(ring, dtype=np.float64).tobytes()
            if key not in seen:
                selected.append((t, rank, mode, ring))
                seen.add(key)
                if len(seen) == limit:
                    break
    return selected


def signed_gap(body, chosen):
    """Signed separation along the best exact separating axis of two rectangles (positive is separated)."""
    if not chosen:
        return np.empty((len(body), 0), dtype=body.dtype)
    idx = np.array([w[0] for w in chosen])
    obs = np.asarray([w[3] for w in chosen])[None]
    ego = body[:, idx]
    edges = np.concatenate((np.diff(ego[:, :, :3], axis=2),
                            np.broadcast_to(np.diff(obs[:, :, :3], axis=2),
                                            (len(ego),len(idx),2,2))),axis=2)
    axes = edges[..., ::-1] * [-1., 1.]
    axes /= np.sqrt(np.sum(axes*axes, axis=-1, keepdims=True))
    p = np.einsum("bnci,bnai->bnac",ego,axes)
    q = np.einsum("bnci,bnai->bnac",np.broadcast_to(obs,ego.shape),axes)
    a = q.min(axis=3) - p.max(axis=3)
    b = p.min(axis=3) - q.max(axis=3)
    return np.maximum(a,b).max(axis=2)


def shortlist_seed(trial, nodes, moving):
    candidates = seed_bank(trial, nodes)
    nvars = 30 if moving else 24
    z = np.zeros((len(candidates), nvars))
    z[:, :24] = candidates
    states = trial.states(z)
    centers, bodies = core.geometry(states, trial.m["origin"], trial.p)
    reference_body = bodies[len(bodies)//2]
    near = candidate_obstacles(trial, reference_body)
    gaps = signed_gap(bodies, near)
    physical = trial.calc(z, [], moving, False, [], buffered=False)
    penalty = np.sum(np.minimum(physical, 0)**2, axis=1)
    if gaps.size:
        penalty += np.sum(np.minimum(gaps-.015,0)**2, axis=1)
    task = np.zeros(len(z))
    for index, center, basis, half, speed in nodes:
        diff = np.maximum(np.abs((centers[:,index]-center)@basis)-half, 0.)
        task += np.sum(diff**2,axis=1)
        penalty += np.maximum(np.maximum(speed[0]-states[:,index,3],states[:,index,3]-speed[1]),0.)**2
    score = 10*penalty + task
    return z[np.argmin(score)]


def route_support_constraint(footprint, road):
    """Signed point-to-polygon margins and local derivative, with vectorized winding for support holes."""
    if road is None or road.support.is_empty:
        return np.empty((len(footprint),0))
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    points = footprint[0].reshape(-1,2).real
    margin = []
    gradient = []
    for coord in points:
        pt = Point(coord)
        closest = nearest_points(pt, road.support.boundary)[1]
        vec = coord-np.array(closest.coords[0])
        dist = np.linalg.norm(vec)
        sign = 1 if road.support.covers(pt) else -1
        margin.append(sign*dist)
        gradient.append(sign*vec/max(dist,1e-8))
    flat = footprint.reshape(len(footprint),-1,2)
    return np.asarray(margin) + np.sum((flat-points)*np.asarray(gradient),axis=2)-.0015


def include_road(audit, road):
    if road is None:
        return audit
    from cordriver_iter_road import check_road
    updated = check_road(road, np.asarray(audit["body_xy_m"]), np.asarray(audit["state_xy_yaw_v"])[:,2])
    audit["original_road_status"] = audit["road_status"]
    audit["road_detail"] = updated
    audit["road_status"] = updated["status"]
    obs = audit["object_status"]
    if obs == "PREDICTED_OVERLAP" or audit["road_status"] == "OUTSIDE_VISUAL_BAND":
        audit["environment_result"] = "REJECTED"
    elif obs == "CONDITIONAL_PASS" and audit["road_status"] == "CONDITIONAL_PASS":
        audit["environment_result"] = "CONDITIONAL_PASS"
    else:
        audit["environment_result"] = "UNKNOWN"
    return audit


def local_solve(trial, seed, nodes, moving, soft, road):
    size = 24 + 6*int(moving)
    z0 = np.zeros(size+4*int(soft))
    z0[:size] = seed[:size]
    centers, body = core.geometry(trial.states(z0[None]), trial.m["origin"], trial.p)
    subset = candidate_obstacles(trial, body[0])
    if soft:
        reports = core.node_report(nodes, centers[0], trial.states(z0[None])[0])
        z0[-4:] = np.abs(np.concatenate([r["signed_outside_long_lat_m"] for r in reports]))+.001
    q, p = trial.m["q"], trial.m["p"]
    scale = max(1, np.linalg.norm(q@z0[:24]+p,ord=np.inf))
    def objective(z):
        if soft:
            return z[-4:]@z[-4:]
        tracking = (.5*z[:24]@q@z[:24]+p@z[:24])/scale
        if moving:
            tracking += np.sum((z[24:30]/np.tile(core.SHIFT,2))**2)
        return tracking
    def cost_jac(z):
        g = np.zeros(len(z))
        if soft:
            g[-4:] = 2*z[-4:]
        else:
            g[:24] = (q@z[:24]+p)/scale
            if moving:
                g[24:30] = 2*z[24:30]/np.tile(core.SHIFT,2)**2
        return g
    def constraints(z):
        base = trial.calc(z,nodes,moving,soft,[])
        _, body = core.geometry(trial.states(z), trial.m["origin"], trial.p)
        return np.column_stack((base,signed_gap(body,subset)-.015,route_support_constraint(body,road)))
    bounds = [(-trial.p.a_max,trial.p.a_max),(-trial.p.delta_max,trial.p.delta_max)]*12
    if moving:
        bounds.extend(list(zip(-core.SHIFT,core.SHIFT))*2)
    if soft:
        bounds.extend([(0,None)]*4)
    delta = 1.e-20j*np.eye(len(z0))
    start = time.monotonic()
    opt = minimize(objective,z0,method="SLSQP",jac=cost_jac,bounds=bounds,
                   constraints=[dict(type="ineq",fun=lambda z:constraints(z[None])[0],
                                     jac=lambda z:constraints(z[None]+delta).imag.T/1.e-20)],
                   options=dict(maxiter=100,ftol=1e-8))
    audit = include_road(trial.audit(opt.x,nodes,moving),road)
    audit.update(optimization_status=str(opt.message),optimizer_succeeded=bool(opt.success),iterations=int(opt.nit),
                 elapsed_s=time.monotonic()-start,active_mode_times=len(subset),optimized_for="closest_window" if soft else "exact_window")
    return audit,opt.x


def candidate_rank(c):
    return core.sorted_outcomes(c)


def accepted(c):
    return c["geometry_result"] in ("EXACT","APPROXIMATE") and c["environment_result"]=="CONDITIONAL_PASS"


def width_certificate(trial):
    width = min(trial.p.w,trial.p.l_front+trial.p.l_rear)
    return [dict(t_s=float(core.TIMES[2*j+2]),short_side_m=min(r[3:]),minimum_body_projection_m=width,
                 deficit_after_two_face_tolerances_m=width-min(r[3:])-2*TOL)
            for j,r in enumerate(trial.m["rect"]) if min(r[3:])+2*TOL < width]
