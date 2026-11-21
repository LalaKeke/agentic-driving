"""Oriented Astra nodes and bounded, guided corrections of native CorDriver rectangles."""
import importlib
import json
from pathlib import Path
from cordriver_paths import TOOLS, CHECK_PYTHON, CONFIG
import sys
import time
from unittest.mock import patch

import numpy as np
from cvxpy.error import SolverError
from scipy.optimize import minimize
from shapely.geometry import Polygon
import torch

import cordriver_guided_qp as native_api

PROJECT = native_api.PROJECT
sys.path.insert(0, str(TOOLS / "cordriver"))
NATIVE = importlib.import_module(native_api.NATIVE)
TIMES = np.arange(13, dtype=float) * .25
TOL = .001
SHIFT = np.array([2.,3.,np.deg2rad(18)])


def read(path):
    return json.loads(path.read_bytes())


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def rotation(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c,-s],[s,c]])


def parse_nodes(guidance):
    if set(guidance) != {"decision","at_1_5s","at_3_0s"}:
        raise ValueError("INVALID_GUIDANCE_STRUCTURE")
    raw = [guidance["at_1_5s"],guidance["at_3_0s"]]
    if guidance["decision"] == "KEEP" and raw == [None,None]:
        return []
    if guidance["decision"] != "ADJUST":
        raise ValueError("INVALID_GUIDANCE_DECISION")
    nodes = []
    for i, item in zip((6,12),raw):
        if set(item) != {"region_type","center_xy_m","region_yaw_deg","speed_range_mps"}:
            raise ValueError("INVALID_GUIDANCE_NODE")
        center = native_api.numeric_pair(item["center_xy_m"])
        speeds = native_api.numeric_pair(item["speed_range_mps"])
        angle = item["region_yaw_deg"]
        if type(angle) not in (int,float) or not -180 <= angle <= 180 or not 0 <= speeds[0] <= speeds[1]:
            raise ValueError("INVALID_GUIDANCE_BOUND")
        nodes.append((i, center, rotation(np.deg2rad(angle)),
                      np.array(native_api.REGIONS[item["region_type"]]) / 2, speeds))
    return nodes


def compose_native(row, weight, device):
    """Capture native stage-2 objective, initial point, geometry and comparison solve."""
    if native_api.sha(Path(NATIVE.__file__)) != native_api.SOURCE_SHA:
        raise ValueError("NATIVE_SOURCE_CHANGED")
    planner = NATIVE.QPControl().to(device).eval()
    planner.weight.data.fill_(weight)
    ref = torch.tensor([row["qp_reference_steps"]],dtype=torch.float32,device=device)
    rect = torch.tensor([row["qp_corridor"]],dtype=torch.float32,device=device)
    matrices = {}
    native_step = planner.linearization
    def observe(state, u):
        if "start" not in matrices:
            matrices["start"] = state.detach().cpu().numpy().ravel().astype(float)
        return native_step(state,u)
    factory = NATIVE.QPFunction
    def intercept(*settings,**options):
        original = factory(*settings,**options)
        def solve(q,p,g,h,a,b):
            matrices.update(q=q[0].detach().cpu().double().numpy(),p=p[0].detach().cpu().double().numpy(),
                            g=g[0].detach().cpu().double().numpy(),h=h[0].detach().cpu().double().numpy().ravel())
            result = original(q,p,g,h,a,b)
            matrices["seed"] = result[0].detach().cpu().double().numpy()
            return result
        return solve
    try:
        with torch.no_grad(),patch.object(planner,"linearization",observe),patch.object(NATIVE,"QPFunction",intercept):
            result = planner(np.array(row["can_bus"],dtype=np.float32),None,ref,rect)
            matrices["linear_xy_lidar_m"] = result[0].cumsum(dim=0).cpu().numpy()
    except (RuntimeError, ValueError, TypeError, ArithmeticError, SolverError, AssertionError) as exc:
        if "q" not in matrices:
            raise
        matrices["native_replay_error"] = dict(exception=type(exc).__name__,message=str(exc),
                                               recorded_frontend_status=row.get("solver_status"))
    matrices["rect"] = planner.lidar2ego_rect(rect)[0].cpu().numpy().astype(float)
    matrices["reference"] = planner.lidar2ego_traj(ref.cumsum(dim=1))[0].cpu().numpy().astype(float)
    matrices["origin"] = matrices["start"][:2] + [(planner.l_front-planner.l_rear)/2,0.]
    matrices["rect"][:,:2] -= matrices["origin"]
    matrices["reference"] -= matrices["origin"]
    return planner, matrices


def boundary_band(features, transform, initial_body):
    """Image-derived strip; open lines must pass independent geometric preconditions."""
    sides = [[],[]]
    for feat in features:
        if feat["label"] != "boundary" or feat["score"] < .4:
            continue
        xy = transform(feat["points"])
        if np.all(np.diff(xy[:,0]) < -1e-4):
            xy = xy[::-1]
        if not np.all(np.diff(xy[:,0]) > 1e-4) or not xy[0,0] < 0 < xy[-1,0]:
            continue
        y = float(np.interp(0.,xy[:,0],xy[:,1]))
        sides[int(y>0)].append((abs(y),feat["rank"],xy))
    if not all(sides):
        return None
    _,ri,right = min(sides[0],key=lambda x:x[:2])
    _,li,left = min(sides[1],key=lambda x:x[:2])
    lo = max(right[0,0],left[0,0])
    hi = min(right[-1,0],left[-1,0])
    xs = np.unique(np.r_[lo, right[:,0],left[:,0],hi])
    xs = xs[(xs>=lo) & (xs<=hi)]
    lower = np.interp(xs,right[:,0],right[:,1])
    upper = np.interp(xs,left[:,0],left[:,1])
    if not np.all(upper>lower):
        return None
    polygon = Polygon(np.vstack((np.c_[xs,lower],np.c_[xs[::-1],upper[::-1]])))
    if not polygon.is_valid or not polygon.covers(initial_body):
        return None
    return dict(ranks=[ri,li],xs=xs,lower=lower,upper=upper,polygon=polygon)


def predicted_objects(visual, basis, transform):
    series = [[] for _ in TIMES]
    retained = modes = missing = 0
    for obj in visual.get("current_objects",[]):
        if obj["score"] < .4:
            continue
        retained += 1
        ring = obj.get("footprint_lidar_xy_m")
        steps = obj.get("future_step_xy_lidar_m")
        if ring is None or steps is None:
            missing += 1
            continue
        ring = transform(ring)
        steps = np.asarray(steps,dtype=float)
        if ring.shape != (4,2) or steps.ndim != 3 or steps.shape[1:] != (6,2) or not steps.shape[0]:
            raise ValueError("INVALID_PREDICTED_OBJECT_CONVENTION")
        if not np.all(np.isfinite(ring)) or not np.all(np.isfinite(steps)) or not Polygon(ring).is_valid:
            raise ValueError("INVALID_PREDICTED_GEOMETRY")
        for mode, increments in enumerate(steps):
            endpoint = np.vstack((np.zeros(2),np.cumsum(increments@basis.T,axis=0)))
            shifts = np.empty((13,2))
            shifts[::2] = endpoint
            shifts[1::2] = (endpoint[:-1]+endpoint[1:]) / 2
            for n,delta in enumerate(shifts):
                series[n].append((obj["rank"], mode, ring+delta))
            modes += 1
    return series,dict(retained_objects=retained,forecast_modes=modes,missing_objects=missing,
                       decoded="current_objects" in visual)


def control_states(z, initial, dt, wheelbase):
    """Exact constant-control bicycle arcs for a batch of real/complex parameter vectors."""
    acc, steer = z[:,0:24:2], z[:,1:24:2]
    speeds = initial[3] + np.column_stack((np.zeros(len(z)),np.cumsum(acc*dt,axis=1)))
    distance = dt*(speeds[:,1:]+speeds[:,:-1])/2
    angle = distance*np.tan(steer)/wheelbase
    yaw = initial[2]+np.column_stack((np.zeros(len(z)),np.cumsum(angle,axis=1)))
    arc = distance*np.sinc(angle/(2*np.pi))
    dx = arc*np.cos(yaw[:,:-1]+angle/2)
    dy = arc*np.sin(yaw[:,:-1]+angle/2)
    x = initial[0]+np.column_stack((np.zeros(len(z)),np.cumsum(dx,axis=1)))
    y = initial[1]+np.column_stack((np.zeros(len(z)),np.cumsum(dy,axis=1)))
    return np.stack((x,y,yaw,speeds),axis=2)


def geometry(states, origin, planner):
    c,s = np.cos(states[:,:,2]),np.sin(states[:,:,2])
    offset = (planner.l_front-planner.l_rear)/2
    ref = states[:,:,:2]-origin
    centers = ref+offset*np.stack((c,s),axis=-1)
    local = np.array([[planner.l_front,planner.w/2],[planner.l_front,-planner.w/2],
                      [-planner.l_rear,-planner.w/2],[-planner.l_rear,planner.w/2]])
    xx = ref[:,:,0,None]+c[:,:,None]*local[:,0]-s[:,:,None]*local[:,1]
    yy = ref[:,:,1,None]+s[:,:,None]*local[:,0]+c[:,:,None]*local[:,1]
    return centers,np.stack((xx,yy),axis=-1)


def object_separators(seed_body, series):
    """Bounded separator set for local optimization; final check covers every predicted polygon."""
    fixed = []
    for n,objects in enumerate(series):
        ego = seed_body[n]
        candidates = []
        for rank,mode,ring in objects:
            vec = ring.mean(axis=0)-ego.mean(axis=0)
            if np.linalg.norm(vec) > 12 + max(np.linalg.norm(ring-ring.mean(axis=0),axis=1)):
                continue
            edges = np.vstack((np.diff(ego[:3],axis=0),np.diff(ring[:3],axis=0)))
            axes = edges[:,::-1]*[-1.,1.]
            axes /= np.maximum(1e-9,np.linalg.norm(axes,axis=1))[:,None]
            axes *= np.where(axes@vec>=0,1,-1)[:,None]
            edge = np.min(ring@axes.T,axis=0)
            clearance = edge-np.max(ego@axes.T,axis=0)
            i = int(np.argmax(clearance))
            if clearance[i] < 2:
                candidates.append((float(clearance[i]),rank,mode,axes[i],edge[i]))
        used = set()
        for _,_,_,axis,edge in sorted(candidates,key=lambda e:e[:3]):
            key = tuple(np.round(np.r_[axis,edge],3))
            if key not in used and len(used)<3:
                fixed.append((n,axis,edge))
                used.add(key)
    return fixed


def corridor_moves(z):
    knots = np.concatenate((np.zeros((len(z),1,3)), z[:,24:30].reshape(-1,2,3)),axis=1)
    coeff = np.array([7/27,20/27,1,7/27,20/27,1])
    i = np.array([0,0,0,1,1,1])
    return knots[:,i]+coeff[None,:,None]*(knots[:,i+1]-knots[:,i])


def node_report(nodes, centers, states):
    details = []
    for i,center,basis,half,speeds in nodes:
        local = (centers[i]-center)@basis
        signed = np.sign(local)*np.maximum(np.abs(local)-half,0.)
        error_v = max(speeds[0]-states[i,3],states[i,3]-speeds[1],0.)
        details.append(dict(t_s=float(TIMES[i]),center_xy_m=centers[i].tolist(),speed_mps=float(states[i,3]),
             signed_outside_long_lat_m=signed.tolist(),outside_m=float(np.linalg.norm(signed)),
             speed_outside_mps=float(error_v),within=bool(np.max(np.abs(signed))<=TOL and error_v<=TOL)))
    return details


class Trial:
    def __init__(self, planner, matrices, row, visual):
        self.p,self.m,self.row = planner,matrices,row
        self.basis = planner.lidar2ego_r[:2,:2].cpu().numpy().astype(float)
        translation = planner.lidar2ego_t[:2].cpu().numpy().astype(float)-matrices["origin"]
        transform = lambda points: np.array(points)@self.basis.T+translation
        _,initial = geometry(matrices["start"][None,None,:],matrices["origin"],planner)
        self.band = boundary_band(visual.get("map_features",[]),transform,Polygon(initial[0,0]))
        self.series,self.objects = predicted_objects(visual,self.basis,transform)
        self.obs_polys = [[(i,j,Polygon(p)) for i,j,p in series] for series in self.series]

    def states(self,z):
        return control_states(z,self.m["start"],self.p.dt,self.p.L)

    def calc(self,z,nodes,rev,soft,separators,buffered=True):
        st = self.states(z)
        centers,body = geometry(st,self.m["origin"],self.p)
        if rev:
            shift = corridor_moves(z)
        else:
            shift = np.zeros((len(z),6,3))
        rect = self.m["rect"][None]
        yaw = rect[:,:,2]+shift[:,:,2]
        c,s = np.cos(yaw)[:,:,None],np.sin(yaw)[:,:,None]
        vec = body[:,2::2,:,:]-(rect[:,:,:2]+shift[:,:,:2])[:,:,None,:]
        cx = vec[:,:,:,0]*c+vec[:,:,:,1]*s
        cy = -vec[:,:,:,0]*s+vec[:,:,:,1]*c
        edge = .0015 if buffered else 0.
        def flatten(x):
            return x.reshape(len(z),-1)
        parts = []
        for xy,half in [(cx,rect[:,:,3,None]/2),(cy,rect[:,:,4,None]/2)]:
            parts += [flatten(half-edge-xy),flatten(half-edge+xy)]
        parts += [st[:,1:,3]-self.p.v_min,self.p.v_max-st[:,1:,3]]
        steer_change = np.diff(z[:,1:24:2],axis=1,prepend=0.)
        parts += [self.p.ddelta_max*self.p.dt-steer_change,self.p.ddelta_max*self.p.dt+steer_change]
        if rev:
            kd = z[:,27:30]-z[:,24:27]
            parts += [SHIFT-kd,SHIFT+kd]
        for j,(i,center,basis,half,speeds) in enumerate(nodes):
            local = (centers[:,i]-center)@basis
            slack = z[:,-4+j*2:z.shape[1]-4+j*2+2] if soft else 0.
            parts += [half+slack-local,half+slack+local,st[:,i,3:4]-speeds[0],speeds[1]-st[:,i,3:4]]
        if self.band is not None:
            b = self.band
            x,y = body[:,:,:,0],body[:,:,:,1]
            xs = b["xs"]
            idx = np.clip(np.searchsorted(xs,x.real)-1,0,len(xs)-2)
            parts += [flatten(x-xs[0]),flatten(xs[-1]-x)]
            for sign,side in [(1,"lower"),(-1,"upper")]:
                vals = b[side]
                line = vals[idx] + (x-xs[idx]) * ((vals[idx+1]-vals[idx])/(xs[idx+1]-xs[idx]))
                parts.append(flatten(sign*(y-line)-edge))
        if separators:
            idx, axes, offsets = zip(*separators)
            projected = np.einsum("bsci,si->bsc",body[:,idx],axes)
            parts.append(flatten(np.array(offsets)[None,:,None]-projected-edge))
        return np.concatenate(parts,axis=1)

    def audit(self,z,nodes,rev):
        p,m=self.p,self.m
        states=[torch.tensor(m["start"],dtype=torch.float64)]
        for u in z[:24].reshape(-1,2):
            states.append(p.kinematics_forward(states[-1],torch.tensor(u,dtype=torch.float64)))
        rk = torch.stack(states).numpy()
        closed = self.states(z[None])[0]
        centers,body = geometry(rk[None],m["origin"],p)
        body,centers = body[0],centers[0]
        closed_centers,closed_body = geometry(closed[None],m["origin"],p)
        rect = m["rect"].copy()
        shift = corridor_moves(z[None])[0] if rev else np.zeros((6,3))
        rect[:,:3] += shift
        corner_gaps,analytic_gaps = [],[]
        for j,r in enumerate(rect):
            for arr,gap in ((body,corner_gaps),(closed_body[0],analytic_gaps)):
                xy = (arr[2*j+2]-r[:2])@rotation(r[2])
                gap.append(float(np.min(r[3:]/2-np.abs(xy))))
        nodes_record = node_report(nodes,centers,rk)
        other_nodes = node_report(nodes,closed_centers[0],closed)
        ac,d = z[:24].reshape(-1,2).T
        margins = [p.a_max-np.max(np.abs(ac)),p.delta_max-np.max(np.abs(d)),
                   p.ddelta_max*p.dt-np.max(np.abs(np.diff(d,prepend=0))),
                   min(rk[:,3])-p.v_min,p.v_max-max(rk[:,3]),*corner_gaps,*analytic_gaps]
        if rev:
            knots = z[24:30].reshape(2,3)
            margins.extend((float(np.min(SHIFT-np.abs(knots))),float(np.min(SHIFT-np.abs(knots[1]-knots[0])))))
        valid = min(margins)>=-TOL and all(n["speed_outside_mps"]<=TOL for n in nodes_record+other_nodes)
        exact = all(n["within"] for n in nodes_record+other_nodes)
        outside,collisions,distances=[],[],[]
        for i,points in enumerate(body):
            poly=Polygon(points)
            if self.band is not None and not self.band["polygon"].covers(poly):
                outside.append(i)
            for rank,mode,ob in self.obs_polys[i]:
                dist = poly.distance(ob)
                distances.append(dist)
                if poly.intersects(ob):
                    collisions.append([float(TIMES[i]),rank,mode])
        domain = self.band is not None and all(b[:,0].min()>=self.band["xs"][0] and
                                               b[:,0].max()<=self.band["xs"][-1] for b in body)
        road = "UNKNOWN" if not domain else ("OUTSIDE_VISUAL_BAND" if outside else "CONDITIONAL_PASS")
        if collisions:
            actor = "PREDICTED_OVERLAP"
        elif not self.objects["decoded"] or self.objects["missing_objects"]:
            actor = "UNKNOWN"
        else:
            actor = "CONDITIONAL_PASS"
        env = "REJECTED" if road=="OUTSIDE_VISUAL_BAND" or collisions else (
            "UNKNOWN" if road=="UNKNOWN" or actor=="UNKNOWN" else "CONDITIONAL_PASS")
        geo = ("EXACT" if exact else "APPROXIMATE") if valid else "UNVERIFIED"
        return dict(geometry_result=geo,environment_result=env,
             body_xy_m=centers.tolist(),state_xy_yaw_v=rk.tolist(),times_s=TIMES.tolist(),
             controls_accel_steer=z[:24].reshape(-1,2).tolist(),node_checks=nodes_record,
             raw_min_corner_clearance_m=corner_gaps, analytic_min_corner_clearance_m=analytic_gaps,
             max_RK4_vs_closed_form_body_xy_m=float(np.max(np.linalg.norm(centers-closed_centers[0],axis=1))),
             min_native_physical_margin=float(min(margins)),rectangles_ego_body_xy_yaw_l_w=rect.tolist(),
             rectangle_delta_xy_yaw=shift.tolist(),max_correction_m=float(np.max(np.linalg.norm(shift[:,:2],axis=1))),
             max_correction_deg=float(np.rad2deg(np.max(np.abs(shift[:,2])))),
             road_status=road,visual_band_ranks=None if self.band is None else self.band["ranks"],
             road_uncovered_samples=outside,object_status=actor,object_overlap_time_rank_mode=collisions,
             min_modeled_object_sample_distance_m=float(min(distances)) if distances else None,objects=self.objects,
             position_loss_m2=float(sum(w["outside_m"]**2 for w in nodes_record)))

    def solve(self,nodes,rev,soft,initial):
        num=24+int(rev)*6+int(soft)*4
        z0 = np.zeros(num)
        z0[:min(len(initial),num)] = initial[:num]
        if soft:
            st=self.states(z0[None])
            xy,_=geometry(st,self.m["origin"],self.p)
            gaps=node_report(nodes,xy[0],st[0])
            z0[-4:]=np.abs(np.concatenate([n["signed_outside_long_lat_m"] for n in gaps]))+.01
        _,seed_polys=geometry(self.states(z0[None]),self.m["origin"],self.p)
        separators=object_separators(seed_polys[0],self.series)
        q,p=self.m["q"],self.m["p"]
        scale=max(1.,float(np.linalg.norm(q@z0[:24]+p,ord=np.inf)))
        def objective(z):
            if soft:
                return z[-4:]@z[-4:]
            shift_cost=np.dot(z[24:30]/np.tile(SHIFT,2),z[24:30]/np.tile(SHIFT,2)) if rev else 0.
            return (.5*z[:24]@q@z[:24]+p@z[:24])/scale+shift_cost
        def derivative(z):
            grad=np.zeros_like(z)
            if soft: grad[-4:]=2*z[-4:]
            else:
                grad[:24]=(q@z[:24]+p)/scale
                if rev: grad[24:30]=2*z[24:30]/np.tile(SHIFT,2)**2
            return grad
        params=(nodes,rev,soft,separators)
        eps=1e-20
        eye=1j*eps*np.eye(num)
        limits=[(-self.p.a_max,self.p.a_max),(-self.p.delta_max,self.p.delta_max)]*12
        if rev: limits+=list(zip(-SHIFT,SHIFT))*2
        if soft: limits += [(0,None)]*4
        start=time.monotonic()
        opt=minimize(objective,z0,jac=derivative,method="SLSQP",bounds=limits,
             constraints=[dict(type="ineq",fun=lambda z:self.calc(z[None],*params)[0],
                         jac=lambda z:self.calc(z[None]+eye,*params).imag.T/eps)],
             options=dict(maxiter=100,ftol=1e-8))
        aud=self.audit(opt.x,nodes,rev)
        aud.update(optimization_status=str(opt.message),optimizer_succeeded=bool(opt.success),
              elapsed_s=time.monotonic()-start,iterations=int(opt.nit),objective=float(opt.fun),
              optimized_for="closest_window" if soft else "exact_window",local_separators=len(separators),
              solved_native_tracking_cost=float(.5*opt.x[:24]@q@opt.x[:24]+p@opt.x[:24]))
        return aud,opt.x


def sorted_outcomes(aud):
    geo,env=aud["geometry_result"],aud["environment_result"]
    tier = {"CONDITIONAL_PASS":0,"UNKNOWN":1,"REJECTED":2}[env] if geo!="UNVERIFIED" else 3
    return (tier, {"EXACT":0,"APPROXIMATE":1,"UNVERIFIED":2}[geo],round(aud["position_loss_m2"],6),
            aud["max_correction_m"],aud.get("solved_native_tracking_cost",0.))


def variant(trial,nodes,seed,rev):
    exact,z=trial.solve(nodes,rev,False,seed)
    results=[exact]
    if nodes and not (exact["geometry_result"]=="EXACT" and exact["environment_result"]=="CONDITIONAL_PASS"):
        approx,_=trial.solve(nodes,rev,True,z)
        results.append(approx)
    return results


def secondary_seed(trial,nodes):
    v0=trial.m["start"][3]
    v1=float(np.mean(nodes[0][-1])) if nodes else v0
    v2=float(np.mean(nodes[1][-1])) if nodes else v0
    targets=[w[1] for w in nodes] if nodes else [trial.m["reference"][2],trial.m["reference"][5]]
    route=np.asarray(trial.row.get("navigation",{}).get("polyline_guidance_m",[]),dtype=float)
    seed=[]
    for j,(target,a) in enumerate(zip(targets,[(v1-v0)/1.5,(v2-v1)/1.5])):
        delta = np.arctan2(2*trial.p.L*target[1],max(1.,np.dot(target,target)))
        if route.ndim==2 and route.shape[1]==2:
            # Use the navigation tangent at the initial speed's lookahead station for this knot.
            ix=int(np.argmin(np.linalg.norm(route-target,axis=1)))
            tang=route[min(ix+1,len(route)-1)]-route[max(0,ix-1)]
            if np.linalg.norm(tang)>1e-3:
                delta=.5*(delta+np.arctan2(trial.p.L*np.arctan2(tang[1],tang[0]),max(1.,v0*(j+1)*1.5)))
        seed.extend([np.clip(a,-trial.p.a_max,trial.p.a_max),np.clip(delta,-.25,.25)]*6)
    return np.array(seed)


def classify(aud):
    if aud["geometry_result"]=="UNVERIFIED":
        return "NO_VERIFIED_GEOMETRY"
    if aud["environment_result"]!="CONDITIONAL_PASS":
        return "ENVIRONMENT_"+aud["environment_result"]
    return aud["geometry_result"]+"_CONDITIONAL"
