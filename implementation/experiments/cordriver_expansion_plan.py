"""Frozen open-loop fast-planning comparison on new CorDriver scene records. No model requests."""
import json
from pathlib import Path
from cordriver_paths import TOOLS, CHECK_PYTHON, CONFIG
import subprocess
import time

import numpy as np
from scipy.optimize import minimize
from shapely.geometry import LineString, Point, Polygon, mapping

import cordriver_iter_planner as local
import cordriver_nav_solver as core
from cordriver_iter_planner_width import trim_and_audit
from cordriver_iter_road import build_road, edges_from_features, BODY_CORNERS, check_road
from cordriver_keep_initial_steering_probe import bind_initial_assumption
from cordriver_steering_cohort import derive, sha

KEEP=dict(decision="KEEP",at_1_5s=None,at_3_0s=None)


def optimize(tr,seed,nodes,moving,soft,road):
    nc=30 if moving else 24
    z=np.zeros(nc+4*soft)
    z[:min(nc,len(seed))]=seed[:nc]
    centers,body=core.geometry(tr.states(z[None]),tr.m["origin"],tr.p)
    if soft:
        z[-4:]=np.abs(np.concatenate([r["signed_outside_long_lat_m"] for r in core.node_report(nodes,centers[0],tr.states(z[None])[0])]))+.002
    nearby=local.candidate_obstacles(tr,body[0],limit=8)
    q,p=tr.m["q"],tr.m["p"]
    scale=max(1.,np.linalg.norm(q@z[:24]+p,ord=np.inf))
    bounds=[(-tr.p.a_max,tr.p.a_max),(-tr.p.delta_max,tr.p.delta_max)]*12
    if moving:bounds+=list(zip(-core.SHIFT,core.SHIFT))*2
    if soft:bounds += [(0,None)]*4
    def objective(z):
        tracking=(.5*z[:24]@q@z[:24]+p@z[:24])/scale
        if moving:tracking+=np.sum((z[24:30]/np.tile(core.SHIFT,2))**2)
        return z[-4:]@z[-4:]+.00001*tracking if soft else tracking
    def residual(z):
        base=tr.calc(z,nodes,moving,soft,[])
        _,b=core.geometry(tr.states(z),tr.m['origin'],tr.p)
        return np.column_stack((base,local.signed_gap(b,nearby)-.015,
                                local.route_support_constraint(b,road)))
    perturb=1e-20j*np.eye(len(z))
    res=minimize(objective,z,bounds=bounds,method="SLSQP",
                 jac=lambda x:np.imag([objective(p) for p in x+perturb])/1e-20,
                 constraints=[dict(type="ineq",fun=lambda x:residual(x[None])[0],
                                   jac=lambda x:residual(x[None]+perturb).imag.T/1e-20)],
                 options=dict(maxiter=75,ftol=1e-8))
    return res.x[:nc],dict(iterations=int(res.nit),message=res.message,optimizer_converged=bool(res.success))


def ref_stats(plan, context):
    states=np.asarray(plan["state_xy_yaw_v"])
    xy=np.asarray(plan["body_xy_m"])[2::2]
    ref=[r for r in context["trajectory"] if r["t_s"]>0]
    assert len(ref)==6
    d=xy-np.array([[r['x_forward_m'],r['y_left_m']] for r in ref])
    yaw=states[2::2,2]-[r['nominal_yaw_rad'] for r in ref]
    dpsi=np.rad2deg(np.angle(np.exp(1j*yaw)))
    steer=np.array(plan["controls_accel_steer"])[:,1]
    speed=np.maximum(abs(states[:-1,3]),abs(states[1:,3]))
    route=LineString(context['navigation']['polyline_guidance_m'])
    return dict(command=context['navigation']['command'],center_rms_m=float(np.sqrt((d*d).sum(axis=1).mean())),
                center_errors_m=np.linalg.norm(d,axis=1).tolist(),yaw_errors_deg=dpsi.tolist(),
                terminal_yaw_deg=float(np.rad2deg(states[-1,2])),terminal_ref_yaw_deg=float(np.rad2deg(ref[-1]['nominal_yaw_rad'])),
                terminal_forward_m=float(xy[-1,0]),signed_min_speed_mps=float(states[:,3].min()),
                predicted_peak_kinematic_lateral_accel_mps2=float(np.max(speed**2*abs(np.tan(steer))/2.)),
                route_center_distance_six_knots_m=[float(route.distance(Point(p))) for p in xy])


def variant_geometry(tr,z,plan,nodes,moving,initial):
    st=np.asarray(plan['state_xy_yaw_v'])
    u = np.asarray(plan['controls_accel_steer'])
    ac,d = u.T
    motion={"acceleration":float(tr.p.a_max-abs(ac).max()),"steering":float(tr.p.delta_max-abs(d).max()),
            "steer_slew":None if initial is None else float(tr.p.ddelta_max*tr.p.dt-abs(np.diff(d,prepend=initial)).max()),
            "speed":float(min(st[:,3].min()-tr.p.v_min,tr.p.v_max-st[:,3].max())),
            "rk4_corner":min(plan['raw_min_corner_clearance_m']),
            "exact_corner":min(plan['analytic_min_corner_clearance_m'])}
    if moving:
        knots=z[24:30].reshape(2,3)
        motion["shift_limits"]=float(np.min(core.SHIFT-abs(knots)))
        motion["incremental_shift_limits"]=float(np.min(core.SHIFT-abs(knots[1]-knots[0])))
    exact_state=tr.states(z[None]); centers,_=core.geometry(exact_state,tr.m['origin'],tr.p)
    n2=core.node_report(nodes,centers[0],exact_state[0])
    checks=plan['node_checks']+n2
    motion["hard_speed_windows_mps"]=-max([r["speed_outside_mps"] for r in checks]+[0.])
    max_miss=max([r['outside_m'] for r in checks]+[0.])
    bounds_ok=all(v is not None and v>=-core.TOL for v in motion.values())
    task='KEEP_NO_WINDOW' if not nodes else ('EXACT' if all(r['within'] for r in checks) else 'APPROXIMATE')
    return dict(margins=motion,passes=bool(bounds_ok),window_quality=task,max_window_miss_m=None if not nodes else max_miss,
                analytic_window_checks=n2)


def audit(tr,geometry,z,moving,widened,raw,nodes,road,secondary,road_file,visual,context,method,folder,meta):
    dest=folder/method
    dest.mkdir(exist_ok=False)
    if widened:
        plan=trim_and_audit(tr,raw,z,nodes,moving,road)
    else:
        plan=local.include_road(tr.audit(z,nodes,moving),road)
        plan["expanded_short_dimension_m"]=[0.]*6
    plan.update(meta,method=method,native_geometry_under_zero_steering=plan['geometry_result'])
    adjusted=variant_geometry(tr,z,plan,nodes,moving,geometry)
    plan["causal_variant"]=adjusted
    plan["reference_metrics"]=ref_stats(plan,context)
    file=dest/'candidate.json'
    core.save(file,plan)
    trace=subprocess.run([str(CHECK_PYTHON),'-B',str(Path(__file__).with_name('cordriver_expansion_continuous.py')),
                          "--candidate",str(file),"--road",str(road_file),"--visual",str(visual)],capture_output=True,text=True,check=True)
    evidence=json.loads(trace.stdout)
    swept,continuous_road=evidence['objects'],evidence['road']
    other=None
    if secondary is not None:
        from cordriver_iter_dense import states
        _,centers,yaw=states(plan,.025)
        other=check_road(secondary,centers,yaw)
    accepted=(adjusted['passes'] and continuous_road['status']=='POSITIVE_ROAD_LOWER_BOUND' and
              swept['status'] in ('POSITIVE_LOWER_BOUND','EMPTY_PREDICTION_SET') and
              not tr.objects['missing_objects'] and tr.objects['decoded'] and
              all(r['contains_reintegrated_ego'] for r in plan.get('effective_corridor',[])) and
              (other is None or other['status']!='OUTSIDE_VISUAL_BAND'))
    knot=plan['min_modeled_object_sample_distance_m']
    # Baseline and candidate must be measured at 25 ms even if the certificate
    # adaptively uses finer subdivisions near a collision.
    from cordriver_iter_dense_bridge import check as sample_dense
    dense=sample_dense(plan,core.read(visual))
    accepted=bool(accepted and dense['overlap_times']==0)
    receipt=dict(candidate_path=str(file.resolve()),sha256=sha(file),method=method,
                 eligible_before_fixed_clearance=accepted,steering_motion=adjusted,continuous=evidence,
                 knot_clearance_m=knot,dense_25ms=dense,secondary_road=other['status'] if other else 'UNAVAILABLE_CAUSAL_SECOND_HEAD',
                 reference_metrics=plan['reference_metrics'],width_added_m=max(plan['expanded_short_dimension_m']))
    core.save(dest/'receipt.json',receipt)
    return receipt


def fixed_floor_check(rec,floor):
    distances=[rec['knot_clearance_m'],rec['dense_25ms']['min_predicted_m']]
    return all(f is None or (v is not None and v+core.TOL>=f) for v,f in zip(distances,floor))


def entry(study,reg,case,arm,data,weight):
    tok=case['token']
    fname=study/'frontend'/tok/'sample.json'
    vis=study/'frontend'/tok/'visual_map.json'; pkt=study/'packets'/tok/'packet.json'
    guide=study/'astra_live'/tok/'native_output.json'
    row=core.read(fname);visual=core.read(vis);packet=core.read(pkt)
    context=packet['model_context']
    assert row['token']==visual['token']==packet['token']==context['sample_token']==tok
    assert row['scene_name']==case['name'] and row['timestamp_us']==context['reference_time_us']
    task=core.read(guide) if arm=='astra' else KEEP
    nodes=core.parse_nodes(task)
    folder=study/'planning'/arm/tok
    folder.mkdir(exist_ok=False,parents=True)
    start=time.monotonic()
    source={'token':tok,'scene':row['scene_name'],'arm':arm,'guidance':task,
            'files':{str(p.resolve()):sha(p) for p in [fname,vis,pkt,reg]+([guide] if arm=='astra' else [])},
            'model_guidance':arm=='astra','qp_weight':weight,
            'secondary_sources_sha256':sha(study/'secondary_primary/sources.json'),
            'planner_source_sha256':sha(Path(__file__)),
            'interval_checker_sha256':sha(Path(__file__).with_name('cordriver_expansion_continuous.py'))}
    core.save(folder/'inputs.json',source)
    can=data/'can_bus'/(row['scene_name']+'_pose.json')
    try:
        delta,hist=derive(can,row)
        steering={'initial_rad':delta,'samples':len(hist),'first_us':hist[0]['utime'],'last_us':hist[-1]['utime'],
                  'source_sha256':sha(can),'source':str(can)}
    except (FileNotFoundError,ValueError) as exc:
        delta=None;steering={'initial_rad':None,'reason':str(exc)}
    planner,mat=core.compose_native(row,weight,'cpu')
    tr=core.Trial(planner,mat,row,visual)
    raw=tr.m['rect'].copy()
    if delta is not None:bind_initial_assumption(tr,delta)
    road=build_road(context['navigation']['polyline_guidance_m'],edges_from_features(visual['map_features']))
    second=None
    ext=study/'secondary_primary'
    e,=[r for r in core.read(ext/'sources.json')['cases'] if r['token']==tok]
    assert e['admitted']==(ext/(tok+'.json')).exists()
    if e['admitted']:
        m=core.read(ext/(tok+'.json'))
        assert e['admitted'] and e['same_primary_detection_matches'] and e['latest_exposure_minus_decision_us']<=0
        assert m['token']==tok and m['cameras']==e['current_cameras']
        second=build_road(road.route,edges_from_features(m['features']))
    road_file=folder/'road.json'
    core.save(road_file,dict(support=mapping(road.support),edges=[mapping(e.line) for e in road.edges.values()],
                             area_m2=road.support.area,edge_ranks=list(road.edges)))
    native_ok=bool(row.get('verified_qp_output') and row.get('fallback') is False and row.get('qp_controls_accel_steer') is not None)
    entries=[];skips=[];floor=[None,None]; seed=core.secondary_seed(tr,nodes)
    initial_body=Polygon(BODY_CORNERS)
    def eligible(r):return r['eligible_before_fixed_clearance'] and fixed_floor_check(r,floor)
    def good(r):return eligible(r) and r['steering_motion']['window_quality']!='APPROXIMATE'
    def record(z,moving,width,method,meta):
        rec=audit(tr,delta,z,moving,width,raw,nodes,road,second,road_file,vis,context,method,folder,meta)
        entries.append(rec)
        if method in ('upstream_qp','rigid_fixed') and rec['eligible_before_fixed_clearance'] and floor==[None,None]:
            floor[:]=[rec['knot_clearance_m'],rec['dense_25ms']['min_predicted_m']]
        return rec
    if native_ok:
        z=np.asarray(row['qp_controls_accel_steer']).ravel()
        assert z.shape==(24,) and np.isfinite(z).all()
        record(z,False,False,'upstream_qp',dict(source="ARCHIVED_VERIFIED_FRONTEND_GPU_CONTROLS"))
        seed=z
    else:
        skips.append(dict(method='upstream_qp',reason='NO_VERIFIED_FRONTEND_QP_CONTROLS',solver=row.get('solver_status'),fallback=row.get('fallback')))
    for index,method in enumerate(('rigid_fixed','rigid_guided','width_guided','width_second')):
        if entries and good(entries[-1]):
            skips.append(dict(method=method,reason='FIRST_VERIFIED_EXACT_OR_KEEP'));continue
        if delta is None or not road.support.covers(initial_body) or any(e.line.intersects(initial_body) for e in road.edges.values()):
            skips.append(dict(method=method,reason='CAN_UNOBSERVABLE' if delta is None else 'INITIAL_BODY_LACKS_ROAD_SUPPORT'));continue
        moving=bool(nodes) and index>0
        wide=index>=2
        tr.m['rect']=raw.copy()
        if wide:tr.m['rect'][np.arange(6),3+np.argmin(raw[:,3:],axis=1)]+=tr.p.w
        alternate=(method=='rigid_guided' and not moving) or method=='width_second'
        z0=local.shortlist_seed(tr,nodes,moving) if alternate else seed
        z,stats=optimize(tr,z0,nodes,moving,bool(nodes) and method=='width_second',road)
        record(z,moving,wide,method,stats)
        seed=z
    allowed=[r for r in entries if eligible(r)]
    def rank(r):
        quality=r['steering_motion']['window_quality']
        return (quality=='APPROXIMATE',r['steering_motion']['max_window_miss_m'] or 0.,
                r['reference_metrics']['center_rms_m'],r['width_added_m'])
    selected=min(allowed,key=rank) if allowed else None
    results=dict(scene=row['scene_name'],token=tok,arm=arm,decision=task['decision'],status='CONDITIONAL_PASS' if selected else 'NO_RELEASE',
                 native_frontend_qp=native_ok,estimated_steering=steering,initial_full_body_in_support=road.support.covers(initial_body),
                 current_boundary_edges=len(road.edges),fixed_clearance_knot_dense_m=floor,selected_path=selected['candidate_path'] if selected else None,
                 selected_method=selected['method'] if selected else None,
                 selected_task=selected['steering_motion']['window_quality'] if selected else None,
                 candidates=[dict(method=r['method'],candidate_path=r['candidate_path'],sha256=r['sha256'],
                                  eligible=eligible(r),gates_without_clearance_floor=r['eligible_before_fixed_clearance'],
                                  full_receipt_path=str((Path(r['candidate_path']).parent/'receipt.json').resolve())) for r in entries],
                 skips=skips,wall_seconds=time.monotonic()-start)
    core.save(folder/'result.json',results)
    return results
