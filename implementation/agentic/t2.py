"""Conservative convex reference-point corridor, with no behavior prediction."""
import math
from shapely.geometry import Point,Polygon,LineString
from shapely.ops import nearest_points
from .contracts import artifact,need,grid,validate
from .geometry import planes,polygon,obstacles,disk_radius,rear_state,integrate

def clip(domain,plane):
    if domain.is_empty:return domain
    nx,ny,b=plane
    # Clip via a very large half-plane relative to current finite domain bounds.
    bound=max(abs(x) for x in domain.bounds)+abs(b)+100
    tx,ty=-ny,nx;anchor=(nx*b,ny*b)
    pts=[(anchor[0]+tx*u-nx*v,anchor[1]+ty*u-ny*v) for u,v in [(-4*bound,0),(4*bound,0),(4*bound,8*bound),(-4*bound,8*bound)]]
    return domain.intersection(Polygon(pts))

def _moving_seed(scene,times):
    """An ego-only zero-steer reference, never an actor forecast or a solution.

    The semantic stop template brakes maximally; hold/resume approaches the
    preferred speed within the same vehicle bounds. If this seed is blocked,
    another feasible trajectory may still exist; callers must not infer global
    physical infeasibility from a seed-construction failure.
    """
    cap=scene['capture'];v=cap['vehicle'];d=scene['decision']
    q=rear_state(cap['ego'],v);points=[]
    need(0<=q[3]<=v['max_speed_mps'],'CORRIDOR_SEED_DYNAMICS',
         'Ego-only seed starts outside vehicle speed bounds','tool_infeasible')
    if d['stop']:
        need(q[3]<=v['max_brake_mps2']*d['stop']['deadline_s']+1e-8,
             'CORRIDOR_SEED_STOP_INFEASIBLE',
             'Max-braking ego seed cannot meet the semantic stop deadline',
             'tool_infeasible',policy='moving_seed')
    for k,t in enumerate(times):
        a=0.
        if k<len(times)-1:
            dt=times[k+1]-t
            target=0. if d['stop'] else min(v['max_speed_mps'],d['desired_speed_mps'])
            a=max(-v['max_brake_mps2'],min(v['max_accel_mps2'],(target-q[3])/dt))
        points.append(dict(time_s=t,rear_x_m=q[0],rear_y_m=q[1],yaw_rad=q[2],
                           speed_mps=q[3],acceleration_mps2=a,steer_rad=0.))
        if k<len(times)-1:
            q=integrate(q,a,0.,times[k+1]-t,v)
            # Round-off at a target speed is not a reverse-motion seed.
            need(q[3]>=-1e-8,'CORRIDOR_SEED_DYNAMICS','Negative seed speed','tool_infeasible')
            q[3]=max(0.,q[3])
    if d['stop']:
        deadline=d['stop']['deadline_s']
        k=min(len(points)-2,max(i for i,t in enumerate(times) if t<=deadline))
        p=points[k];elapsed=deadline-p['time_s']
        speed_at_deadline=p['speed_mps']+p['acceleration_mps2']*elapsed
        need(speed_at_deadline<=1e-8 and all(p['speed_mps']<=1e-8 for p in points if p['time_s']>=deadline),
             'CORRIDOR_SEED_STOP_INFEASIBLE',
             'Bounded ego seed does not stop by the semantic deadline on this time grid',
             'tool_infeasible',policy='moving_seed')
    return points


def build(scene,policy='initial_anchor'):
    validate('scene_v2.schema.json',scene)
    need(policy in ('initial_anchor','moving_seed'),'CORRIDOR_POLICY','Unknown explicit corridor construction policy')
    cap=scene['capture'];v=cap['vehicle'];m=scene['metadata'];d=scene['decision']
    r=disk_radius(v);margin=cap['safety']['margin_m'];anchor=Point(rear_state(cap['ego'],v)[:2])
    base=polygon(scene['hypotheses']['road']['polygon_m'],True)
    hp=[[nx,ny,b-r-scene['hypotheses']['road']['uncertainty_m']] for nx,ny,b in planes(base)]
    # Target describes CENTER region. A rear-axle disk of radius d conservatively contains center.
    hp += [[nx,ny,b-v['center_to_rear_axle_m']] for nx,ny,b in planes(polygon(d['target_region'],True))]
    if d['stop']:hp.append([1.,0.,d['stop']['center_x_max_m']-v['center_to_rear_axle_m']])
    times=grid(m);cells=[]
    seed=_moving_seed(scene,times) if policy=='moving_seed' else None
    previous=None
    for k in range(len(times)-1):
        constraints=[p[:] for p in hp]
        if seed is None:seed_geometry=anchor
        else:
            a,b=seed[k:k+2]
            ends=[(a['rear_x_m'],a['rear_y_m']),(b['rear_x_m'],b['rear_y_m'])]
            seed_geometry=Point(ends[0]) if ends[0]==ends[1] else LineString(ends)
        for oid,shape,u in obstacles(scene,k):
            hull=shape.convex_hull
            origin,near=nearest_points(seed_geometry,hull)
            dx,dy=near.x-origin.x,near.y-origin.y;dist=math.hypot(dx,dy)
            need(dist>r+u+margin,
                 'CORRIDOR_ANCHOR_BLOCKED' if seed is None else 'CORRIDOR_SEED_BLOCKED',
                 'Conservative corridor anchor blocked' if seed is None else
                 'Ego-only seed blocked; this does not prove all trajectories infeasible',
                 'tool_infeasible',object_id=oid,interval=[times[k],times[k+1]])
            nx,ny=dx/dist,dy/dist
            support=min(nx*x+ny*y for x,y in hull.exterior.coords)
            constraints.append([nx,ny,support-r-u-margin])
        domain=base
        for p in constraints:domain=clip(domain,p)
        need(not domain.is_empty and domain.area>1e-8,'CORRIDOR_EMPTY','Empty convex cell','tool_infeasible',interval=[times[k],times[k+1]])
        if seed is None:
            need(domain.buffer(1e-7).covers(anchor),'CORRIDOR_DISCONNECTED','Anchor not in convex cell','tool_infeasible')
        else:
            need(domain.buffer(1e-7).covers(seed_geometry),'CORRIDOR_SEED_OUTSIDE',
                 'Ego-only seed leaves a corridor cell; not a global infeasibility proof',
                 'tool_infeasible',interval=[times[k],times[k+1]],policy=policy)
            if k==0:
                need(domain.buffer(1e-7).covers(anchor),'CORRIDOR_INITIAL_STATE',
                     'First corridor cell must contain the true initial rear axle','tool_infeasible')
            if previous is not None:
                need(not previous.intersection(domain).is_empty,'CORRIDOR_DISCONNECTED',
                     'Adjacent time cells do not share a transition region','tool_infeasible',
                     interval=[times[k-1],times[k+1]])
        previous=domain
        cells.append(dict(start_s=times[k],end_s=times[k+1],halfplanes=constraints,polygon_m=[list(p) for p in domain.exterior.coords][:-1]))
    # Default output stays byte-compatible. Moving seeds change construction only;
    # body envelopes, obstacle occupancies, margins and the T3/T4 checks are unchanged.
    extra={}
    if seed is not None:
        extra['construction']=dict(policy='moving_seed',
            seed_source='ego_state_vehicle_limits_semantic_template_only',
            seed_dynamics='exact_constant_acceleration_zero_steer',
            target_prediction_source='model_whole_interval_occupancy_only',
            behavior=d['behavior'],seed_points=seed,seed_is_solution=False,
            limitation='A blocked seed is not a proof of global infeasibility')
    return artifact(scene,cells=cells,reference_point='rear_axle',body_envelope='orientation_independent_circumscribed_disk',body_radius_m=r,margin_m=margin,conservative=True,**extra)
