"""Refit CorDriver control sequences with predicted curb and object clearance in the search."""
from pathlib import Path
from cordriver_paths import TOOLS, CHECK_PYTHON, CONFIG
import time

import numpy as np
from scipy.optimize import minimize
from shapely.geometry import LineString, Polygon, mapping
from shapely.ops import nearest_points

import cordriver_reachable_options as options
import cordriver_smooth_planner as smooth
from cordriver_edge_gate import edge_clearance, native_body_knots

core, legacy = smooth.core, smooth.legacy
MARGINS = (.40, .65, .90)


def half_states(trial, u):
    """Sample each exact constant-control arc halfway too, using its own starting state."""
    knots = trial.states(u)
    x, y, yaw, speed = knots[:, :-1].transpose(2, 0, 1)
    t = trial.p.dt / 2
    acceleration, steer = u[:, :24:2], u[:, 1:24:2]
    dist = t * (speed + .5 * t * acceleration)
    turn = dist * np.tan(steer) / trial.p.L
    arc = dist * np.sinc(turn / (2 * np.pi))
    half = np.stack((x + arc*np.cos(yaw + turn/2), y + arc*np.sin(yaw + turn/2),
                     yaw + turn, speed + acceleration*t), axis=-1)
    sampled = np.empty((len(u), 25, 4), dtype=knots.dtype)
    sampled[:, ::2] = knots
    sampled[:, 1::2] = half
    return knots, sampled


def separator(body, geometry):
    """Supporting plane perpendicular to closest-point vector in the seed geometry."""
    polygon = Polygon(body)
    a, b = nearest_points(geometry, polygon)
    normal = np.array(b.coords[0]) - np.array(a.coords[0])
    length = np.linalg.norm(normal)
    if length < 1e-8:
        # Choose the side determined by the current seed centre for an intersecting shape.
        a, b = nearest_points(geometry, polygon.centroid)
        normal = np.array(b.coords[0]) - np.array(a.coords[0])
        length = np.linalg.norm(normal)
        if length < 1e-8:
            return None
    normal /= length
    return normal


def build_planes(trial, road, body, floor, *, objects_only=False):
    edges, objects = [], []
    road_edges = () if objects_only else tuple(road.edges.values())
    initial = min((Polygon(body[0]).distance(e.line) for e in road_edges), default=0.)
    for i, b in enumerate(body):
        polygon = Polygon(b)
        for edge in road_edges:
            if polygon.distance(edge.line) > 3.0:
                continue
            # A nonconvex line is a collection of finite segments, not a single half-plane.
            for a, end in zip(edge.points[:-1], edge.points[1:]):
                geom = LineString((a, end))
                if polygon.distance(geom) > 1.75:
                    continue
                direction = separator(b, geom)
                if direction is not None:
                    edges.append((i, direction, max(a @ direction, end @ direction)))
        pairs = trial.series[i//2]
        following = trial.series[min(12, i//2+1)]
        seen = set()
        for (rank, mode, ring), (r2, m2, second) in zip(pairs, following):
            if (rank, mode) != (r2, m2):
                raise ValueError('object modes not aligned')
            contour = ring if i % 2 == 0 else (ring + second) / 2
            signature = contour.tobytes()
            if signature in seen:
                continue
            seen.add(signature)
            geom = Polygon(contour)
            if polygon.distance(geom) > floor + 3:
                continue
            direction = separator(b, geom)
            if direction is not None:
                objects.append((i, direction, max(contour @ direction)))

    def arrays(rows):
        if not rows:
            return None
        i, n, offset = zip(*rows)
        return np.asarray(i), np.asarray(n), np.asarray(offset)
    return arrays(edges), arrays(objects), float(initial)


def plane_margins(footprint, planes, requested):
    if planes is None:
        return np.empty((len(footprint), 0), dtype=footprint.dtype)
    idx, norm, offset = planes
    project = np.einsum('bnci,ni->bnc', footprint[:, idx], norm)
    return (project.min(axis=2) - offset) - requested[idx]


def refit(tr, controls, rect, road, native, floor, target):
    tr.m['rect'] = rect.copy()
    _, dense = half_states(tr, controls[None])
    _, body = core.geometry(dense, tr.m['origin'], tr.p)
    curb, objects, initial = build_planes(tr, road, body[0], floor)
    ts = np.arange(25) * .125
    ramp = np.minimum(1., ts / .75)
    road_threshold = min(target, initial-.002)*(1-ramp) + target*ramp
    object_threshold = floor - .0005 + .012*ramp
    perturb = np.eye(24)*1e-20j

    def objective(z):
        states = tr.states(z)
        centers, _ = core.geometry(states, tr.m['origin'], tr.p)
        err = centers[:, 2::2] - native[None, 1:, :2]
        accel, steer = z[:, ::2], z[:, 1::2]
        return (np.mean(np.sum(err**2, axis=2), axis=1)
                + .012*np.mean((np.diff(accel,axis=1)/tr.p.dt)**2, axis=1)
                + .15*np.mean((np.diff(steer,axis=1)/tr.p.dt)**2, axis=1)
                + .003*np.mean((accel-controls[None,::2])**2,axis=1))

    def inequalities(z):
        states, sample = half_states(tr, z)
        _, b = core.geometry(sample, tr.m['origin'], tr.p)
        return np.column_stack((tr.calc(z, [], False, False, []),
            smooth.added_margins(states, z, tr.p.dt, tr.p.L),
            smooth.local.route_support_constraint(b[:, ::2], road),
            plane_margins(b, curb, road_threshold), plane_margins(b, objects, object_threshold)))

    optimized = minimize(lambda z: objective(z[None])[0], controls, method='SLSQP',
        jac=lambda z: objective(z[None]+perturb).imag/1e-20,
        constraints=[dict(type='ineq',fun=lambda z: inequalities(z[None])[0],
                          jac=lambda z: inequalities(z[None]+perturb).imag.T/1e-20)],
        bounds=[(-tr.p.a_max,tr.p.a_max),(-tr.p.delta_max,tr.p.delta_max)]*12,
        options=dict(maxiter=75, ftol=1e-8))
    return optimized.x, dict(success=bool(optimized.success), status=str(optimized.message),
        iterations=int(optimized.nit), search_requested_curb_m=target, search_object_floor_m=floor,
        residual_min=float(inequalities(optimized.x[None]).min()), initial_edge_m=initial,
        curb_halfplanes=0 if curb is None else len(curb[0]), object_halfplanes=0 if objects is None else len(objects[0]))


def deviation(plan, poses):
    return float(np.sum((np.asarray(plan['body_xy_m'])[2::2] - poses[1:, :2])**2))


def receipt_entry(chk, native):
    path = Path(chk['candidate_path'])
    if options.sha(path) != chk['candidate_sha256']:
        raise ValueError('candidate not bound to old checks')
    plan = options.load(path)
    receipt = options.load(path.with_name('receipt.json'))
    if receipt['sha256'] != chk['candidate_sha256']:
        raise ValueError('old receipt mismatch')
    old_margin = receipt['continuous']['road'].get('min_sampled_boundary_distance_m')
    return dict(method=chk['method'], eligible=all(chk['checks'].values()), dev=deviation(plan,native),
                source_path=str(path), sha256=chk['candidate_sha256'], clearance=old_margin or 0.,
                positive_road=receipt['continuous']['road']['status']=='POSITIVE_ROAD_LOWER_BOUND')


def work(args, *, study, data):
    pos, case, root, extra_portfolios = args
    token, scene = case['token'], case['scene']
    started = time.monotonic()
    folder = root / token
    folder.mkdir(parents=True, exist_ok=False)
    source = extra_portfolios / token / 'portfolio.json'
    prior = options.load(source)
    assert (prior['token'],prior['scene']) == (token,scene)
    sf = study/'frontend'/token/'sample.json'
    vf = sf.with_name('visual_map.json')
    packet = study/'packets'/token/'packet.json'
    archive = (study/'frontend').resolve().parent/'native_states.json'
    floorpath = study/'planning/diagnostic'/token/'result.json'
    floor_rec = options.load(floorpath)
    front = study/'frontend/frontend.json'
    can = (data / 'can_bus')/(scene+'_pose.json')
    second_src = study/'secondary_primary/sources.json'
    for path in (sf,vf,packet,archive,floorpath,front,can,second_src):
        if options.sha(path) != prior['source_sha256'][str(path.resolve())]:
            raise ValueError('causal archive changed '+str(path))
    sample, visual, ctx = options.load(sf),options.load(vf),options.load(packet)['model_context']
    assert (sample['token']==visual['token']==token==ctx['sample_token'] and floor_rec['token']==token
            and sample['timestamp_us']==ctx['reference_time_us'])
    binding, = [a for a in options.load(archive)['samples'] if a['token']==token]
    poses, status = native_body_knots(sample,binding)
    out = dict(token=token,scene=scene,group=case['group'],status='RETAIN_NATIVE',
               native_source=status, selected_path=None,selected_sha256=None,selected=None,attempts=[],
               prior=str(source),prior_sha256=options.sha(source), protocol_sha256=options.sha(CONFIG/'hard_refit_protocol.json'),
               implementation_sha256=options.sha(Path(__file__)),native_poses=poses.tolist() if poses is not None else None)
    def finish(reason):
        out['reason']=reason
        out['wall_s']=time.monotonic()-started
        options.save(folder/'result.json',out)
        return {k:out[k] for k in ('token','scene','group','status','selected_path','selected_sha256','reason','wall_s')}
    if poses is None:
        out['status']='UNAVAILABLE_NATIVE'
        return finish('NO_BOUND_NATIVE_TRAJECTORY')
    prior_edge=edge_clearance(poses,visual['map_features'])
    out['native_edge']=prior_edge
    old = [receipt_entry(chk,poses) for chk in prior['checks']]
    if not old:
        return finish('NO_NUMERIC_SEED: '+','.join(prior['reasons']))
    if not prior_edge['triggered']:
        return finish('PREDICTED_CURB_DID_NOT_TRIGGER')
    first=min(old,key=lambda r:(not r['eligible'],r['dev'],r['method']))
    second=min(old,key=lambda r:(not r['positive_road'],-r['clearance'],r['dev'],r['method']))
    seeds=[first] if first['method']==second['method'] else [first,second]
    out['seeds']=seeds
    geometry,_=legacy.derive(can,sample)
    road=options.road_for(ctx,visual['map_features'],legacy)
    secondrow,=[r for r in options.load(second_src)['cases'] if r['token']==token]
    secondary=None
    if secondrow['admitted']:
        aux=second_src.with_name(token+'.json')
        if (options.sha(aux)!=prior['source_sha256'][str(aux.resolve())] or not secondrow['same_primary_detection_matches']
                or secondrow['latest_exposure_minus_decision_us']>0):
            raise ValueError('Unbound secondary')
        secondary=options.road_for(ctx,options.load(aux)['features'],legacy)
    floor=floor_rec['fixed_clearance_knot_dense_m']
    numeric_floor=max([v for v in floor if v is not None] or [0.])
    planner,mat = core.compose_native(sample,options.load(front)['qp_weight'],'cpu')
    tr=core.Trial(planner,mat,sample,visual)
    legacy.bind_initial_assumption(tr,geometry)
    raw=tr.m['rect'].copy()
    options.save(folder/'road.json', dict(support=mapping(road.support),edges=[mapping(v.line) for v in road.edges.values()],
                                         area_m2=road.support.area,edge_ranks=list(road.edges)))
    old_road=source.with_name('road.json')
    if options.load(folder/'road.json')!=options.load(old_road):
        raise ValueError('Finite road differs from existing portfolio')
    for i,seed in enumerate(seeds):
        prepared=options.load(seed['source_path'])
        rect=np.asarray(prepared['rectangles_ego_body_xy_yaw_l_w'])
        u0=np.asarray(prepared['controls_accel_steer']).ravel()
        for target in MARGINS:
            name='seed%d_curb%02d'%(i+1,round(target*100))
            u,stats=refit(tr,u0,rect,road,poses,numeric_floor,target)
            plan,rec,checks,nodes,margin=options.certify(tr,u,name,ctx,folder,raw,rect,road,secondary,geometry,floor,vf,
                dict(search=stats,seed_sha256=seed['sha256'],seed_source=seed['source_path']))
            body=np.asarray(plan['body_xy_m'])[::2]
            head=np.asarray(plan['state_xy_yaw_v'])[::2,2]
            actual=edge_clearance(np.column_stack((body,head)),visual['map_features'])
            receip=folder/name/'receipt.json'
            out['attempts'].append(dict(method=name,eligible=all(checks.values()),checks=checks,search=stats,
                smooth_margin=margin,edge=actual,dev=deviation(plan,poses),candidate_path=rec['candidate_path'],
                candidate_sha256=rec['sha256'], receipt_path=str(receip),receipt_sha256=options.sha(receip)))
    qualified=[a for a in out['attempts'] if a['eligible'] and
               a['edge']['min_clearance_lower_bound_m']>=prior_edge['min_clearance_lower_bound_m']+.10]
    if not qualified:
        return finish('NO_VERIFIED_EDGE_IMPROVEMENT')
    first=min(qualified,key=lambda x:(x['edge']['triggered'],x['dev'] if not x['edge']['triggered']
                                      else -x['edge']['min_clearance_lower_bound_m'],x['dev'],x['method']))
    out.update(status='RELEASED',selected_path=first['candidate_path'],selected_sha256=first['candidate_sha256'],
               selected=first)
    return finish('VERIFIED_PREDICTED_EDGE_IMPROVEMENT')
