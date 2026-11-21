"""Prepare paired, witnessed CorDriver guidance nodes before requesting a driving decision."""
import hashlib
import json
import math
from pathlib import Path
from cordriver_paths import TOOLS, CHECK_PYTHON, CONFIG
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
PROTOCOL = CONFIG / 'portfolio_protocol.json'
TEMPLATES = [('track',0.,0.),('ease',.70,0.),('left',0.,.8),('right',0.,-.8),
             ('ease_left',.70,.8),('ease_right',.70,-.8)]


def load(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def node(xy, yaw, speed, kind, half_speed):
    return dict(region_type=kind, center_xy_m=list(map(float, xy)),
                region_yaw_deg=math.degrees(math.atan2(math.sin(yaw), math.cos(yaw))),
                speed_range_mps=[max(0., float(speed)-half_speed), max(0., float(speed)+half_speed)])


def guide(nodes):
    return dict(decision='ADJUST', at_1_5s=nodes[0], at_3_0s=nodes[1])


def target_curve(poses, brake, lateral):
    import numpy as np
    t = np.arange(7)*.5
    xy = poses[:,:2]
    station = np.r_[0., np.cumsum(np.linalg.norm(np.diff(xy,axis=0),axis=1))]
    wanted = np.maximum.accumulate(np.maximum(0., station - .5*brake*t*t))
    distinct = np.r_[True, np.diff(station)>1e-10]
    progress = np.minimum(wanted, station[-1])
    points = np.column_stack([np.interp(progress,station[distinct],xy[distinct,i]) for i in (0,1)])
    heading = np.interp(progress, station[distinct], np.unwrap(poses[:,2])[distinct])
    points += (lateral*(t/3)**2)[:,None] * np.column_stack([-np.sin(heading), np.cos(heading)])
    velocity = np.linalg.norm(np.diff(points,axis=0),axis=1)/.5
    nodes = [node(points[i], heading[i], velocity[i-1], 'lateral' if lateral else 'cruise', 1.) for i in (3,6)]
    return points, guide(nodes)


def select_option(portfolio_path, option_id, *, expected_sha256=None):
    """Resolve the exact two-node primitive; optionally bind the caller's pre-request frozen digest."""
    path = Path(portfolio_path).resolve(strict=True)
    if expected_sha256 is not None and sha(path)!=expected_sha256:
        raise ValueError('PORTFOLIO_DIFFERS_FROM_OFFER')
    portfolio = load(path)
    if portfolio.get('version')!='witnessed_body_menu_v1' or option_id not in portfolio['options']:
        raise ValueError('OPTION_NOT_OFFERED')
    item = portfolio['options'][option_id]
    public, = [r for r in portfolio['public_options'] if r['option_id']==option_id]
    for key in ('candidate','certificate'):
        artifact = Path(item[key+'_path']).resolve(strict=True)
        if path.parent not in artifact.parents or sha(artifact)!=item[key+'_sha256']:
            raise ValueError('OPTION_ARTIFACT_DIGEST_MISMATCH')
    proof = load(item['certificate_path'])
    if (proof['token']!=portfolio['token'] or proof['option_id']!=option_id or
            not all(proof['checks'].values()) or proof['candidate_sha256']!=item['candidate_sha256'] or
            proof['public_sha256']!=digest(public) or sha(proof['audit_receipt'])!=proof['audit_sha256']):
        raise ValueError('OPTION_CERTIFICATE_MISMATCH')
    return dict(item)


def road_for(context, features, legacy):
    return legacy.build_road(context['navigation']['polyline_guidance_m'],legacy.edges_from_features(features))


def certify(tr,controls,method,context,folder,raw,rect,road,secondary,steering,floor,visual_path,meta):
    """Run the old two dynamics checks, continuous certificate, clearance floor and smooth limits."""
    import numpy as np
    import cordriver_smooth_planner as smooth
    core, legacy = smooth.core, smooth.legacy
    tr.m['rect'] = rect.copy()
    analytic = tr.states(controls[None])
    centers, _ = core.geometry(analytic,tr.m['origin'],tr.p)
    nodes = [node(centers[0,i], analytic[0,i,2], analytic[0,i,3],
                  'near_stop' if analytic[0,i,3]<.5 else 'cruise', .25) for i in (6,12)]
    delta = rect[:, :3]-raw[:, :3]
    widths = np.max(rect[:,3:]-raw[:,3:],axis=1)
    meta.update(original_rectangles_ego=raw.tolist(), rectangle_delta_xy_yaw=delta.tolist(),
                expanded_short_dimension_m=widths.tolist(),max_correction_m=float(np.linalg.norm(delta[:,:2],axis=1).max()),
                max_correction_deg=float(np.rad2deg(abs(delta[:,2]).max())))
    rec=legacy.audit(tr,steering,controls,False,True,rect,core.parse_nodes(guide(nodes)),road,secondary,
                     folder/'road.json',visual_path,context,method,folder,meta)
    plan = load(rec['candidate_path'])
    rk4 = np.asarray(plan['state_xy_yaw_v'],dtype=float)
    smooth_margin = min(float(smooth.added_margins(s,controls[None],tr.p.dt,tr.p.L).min())
                        for s in (analytic,rk4[None]))
    checks = dict(physical_and_environment=bool(rec['eligible_before_fixed_clearance']),
                  clearance_floor=bool(legacy.fixed_floor_check(rec,floor)), smooth=smooth_margin>=-core.TOL,
                  exact_both_nodes=rec['steering_motion']['window_quality']=='EXACT')
    return plan, rec, checks, nodes, smooth_margin


def _build(study, token, folder, data):
    import numpy as np
    from shapely.geometry import mapping, Polygon
    from cordriver_edge_gate import native_body_knots
    import cordriver_smooth_planner as smooth
    core, legacy = smooth.core, smooth.legacy
    front = study/'frontend'
    sf = front/token/'sample.json'
    vf = sf.with_name('visual_map.json')
    pkt = study/'packets'/token/'packet.json'
    floorpath=study/'planning/diagnostic'/token/'result.json'
    archive = front.resolve().parent/'native_states.json'
    regpath = study/'selection/registration.json'
    fp=front/'frontend.json'
    sources=study/'secondary_primary/sources.json'
    row,visual,packet,floorrec,native_archive,frontend,index,second = map(load,(sf,vf,pkt,floorpath,archive,fp,regpath,sources))
    context=packet['model_context']
    if not (token==row['token']==visual['token']==packet['token']==context['sample_token']==floorrec['token']
            and row['timestamp_us']==context['reference_time_us'] and floorrec['arm']=='diagnostic'
            and native_archive['frontend_sha256']==sha(fp) and frontend['registration_sha256']==sha(regpath)
            and [v for v in frontend['samples'] if v['token']==token]==[row]):
        raise ValueError('PLANNING_INPUT_BINDING_MISMATCH')
    case, = [c for c in index['cases'] if c['token']==token]
    if row['scene_name']!=case['name'] or case['name']!=floorrec['scene']:
        raise ValueError('PLANNING_SCENE_MISMATCH')
    traced, = [r for r in native_archive['samples'] if r['token']==token]
    poses, native_source = native_body_knots(row,traced)
    floor=floorrec['fixed_clearance_knot_dense_m']
    can=(data / 'can_bus')/(case['name']+'_pose.json')
    files=[sf,vf,pkt,floorpath,archive,regpath,fp,sources,can,PROTOCOL,Path(__file__),
           Path(smooth.__file__),Path(legacy.__file__),Path(core.__file__)]
    table=dict(version='witnessed_body_menu_v1', scene=case['name'], token=token, native_source=native_source,
               options={}, public_options=[], checks=[], attempts=0, control_solves=0, initial_seed_calls=0, reasons=[],
               source_sha256={str(p.resolve()):sha(p) for p in files if p.is_file()})
    try:
        steering,hist=legacy.derive(can,row)
    except (ValueError,FileNotFoundError) as e:
        table['reasons']=['CAN_INITIAL_STEERING_UNOBSERVABLE',str(e)]
        return table
    if poses is None:
        table['reasons']=['NATIVE_REPRESENTATION_UNAVAILABLE']
        return table
    road=road_for(context,visual['map_features'],legacy)
    body=Polygon(legacy.BODY_CORNERS)
    if not road.support.covers(body) or any(edge.line.intersects(body) for edge in road.edges.values()):
        table['reasons']=['INITIAL_BODY_LACKS_ROAD_SUPPORT']
        return table
    other, = [r for r in second['cases'] if r['token']==token]
    aux=sources.with_name(token+'.json')
    if other['admitted']!=aux.is_file():
        raise ValueError('SECONDARY_ROAD_BINDING_MISMATCH')
    secondary=None
    if other['admitted']:
        payload=load(aux)
        if (not other['same_primary_detection_matches'] or other['latest_exposure_minus_decision_us']>0 or
                payload['token']!=token or payload['cameras']!=other['current_cameras']):
            raise ValueError('UNBOUND_SECONDARY_ROAD')
        secondary=road_for(context,payload['features'],legacy)
        table['source_sha256'][str(aux.resolve())]=sha(aux)
    save(folder/'road.json', dict(support=mapping(road.support),edges=[mapping(v.line) for v in road.edges.values()],
                                  area_m2=road.support.area,edge_ranks=list(road.edges)))
    planner, matrices=core.compose_native(row,frontend['qp_weight'],'cpu')
    tr=core.Trial(planner,matrices,row,visual)
    legacy.bind_initial_assumption(tr,steering)
    raw=tr.m['rect'].copy()
    native_ok=bool(row['verified_qp_output'] and not row['fallback'] and row.get('qp_controls_accel_steer') is not None)
    native_controls=np.asarray(row['qp_controls_accel_steer']).reshape(24) if native_ok else None
    witnesses=[]

    def check(controls,method,rect,meta):
        plan,rec,checks,nodes,smooth_margin=certify(tr,controls,method,context,folder,raw,rect,road,
                                                   secondary,steering,floor,vf,meta)
        xy=np.asarray(plan['body_xy_m'])
        speed=np.asarray(plan['state_xy_yaw_v'])[:,3]
        duplicate=any(np.linalg.norm(xy-previous,axis=1).max()<=.15 and
                      np.max(np.abs(speed-v))<=.2 for previous,v in witnesses)
        entry=dict(method=method,checks=checks, smooth_margin=smooth_margin, redundant=bool(duplicate),
                   candidate_path=rec['candidate_path'], candidate_sha256=rec['sha256'])
        table['checks'].append(entry)
        table['attempts']+=1
        if not all(checks.values()) or duplicate:
            return False
        option_id='P%02d'%(len(witnesses)+1)
        witnesses.append((xy,speed))
        public=dict(option_id=option_id, at_1_5s=nodes[0],at_3_0s=nodes[1],body_xy_m_0p5s=xy[::2].tolist(),
                    speed_mps_0p5s=speed[::2].tolist())
        receipt=folder/method/'receipt.json'
        cert=folder/method/'option_certificate.json'
        save(cert,dict(token=token,option_id=option_id,checks=checks,public_sha256=digest(public),
                       candidate_sha256=rec['sha256'],audit_receipt=str(receipt.resolve()),audit_sha256=sha(receipt),
                       smooth_margin=smooth_margin,fixed_clearance_floor=floor,clearance_floor_sha256=sha(floorpath)))
        table['options'][option_id]=dict(candidate_path=rec['candidate_path'],candidate_sha256=rec['sha256'],
                                        certificate_path=str(cert.resolve()),certificate_sha256=sha(cert),method=method)
        table['public_options'].append(public)
        entry['offered_id']=option_id
        return True

    if native_ok:
        check(native_controls,'native_controls',raw,dict(source='NATIVE_CONTROLS_REINTEGRATED'))
    for style,brake,lateral in TEMPLATES:
        target,template=target_curve(poses,brake,lateral)
        shape=core.parse_nodes(template)
        tr.m['rect']=raw.copy()
        seed=native_controls if native_ok else core.secondary_seed(tr,shape)
        table['initial_seed_calls']+=int(not native_ok)
        z,stats=smooth.solve_controls(tr,seed,shape,False,road,target)
        table['control_solves']+=1
        if check(z,style+'_fixed',raw,dict(solver=stats, template=template)):
            continue
        fitted,_=smooth.fit_corridor(raw,context,shape,tr.p.w)
        tr.m['rect']=fitted.copy()
        seed=core.secondary_seed(tr,shape)
        table['initial_seed_calls']+=1
        warm,warmstats=smooth.solve_controls(tr,seed,shape,True,road,target,free_seed=True)
        table['control_solves']+=1
        rect,placement=smooth.fit_to_seed(raw,fitted,tr,warm)
        tr.m['rect']=rect.copy()
        tuned,stats=smooth.solve_controls(tr,warm,shape,True,road,target)
        table['control_solves']+=1
        check(tuned,style+'_refit',rect,dict(solver=stats, template=template, placement=placement,
                                             warm_start=dict(controls=warm.reshape(12,2).tolist(),**warmstats)))
    if not witnesses:
        table['reasons']=['NO_VERIFIED_OPTION_IN_FINITE_MENU']
    return table


def build_portfolio(study, token, dest, data):
    folder=Path(dest).resolve()/token
    folder.mkdir(parents=True,exist_ok=False)
    start=time.monotonic()
    table=_build(Path(study).resolve(),token,folder,data)
    table['wall_seconds']=time.monotonic()-start
    portfolio=folder/'portfolio.json'
    save(portfolio,table)
    for option in table['public_options']:
        select_option(portfolio,option['option_id'])
    return portfolio
