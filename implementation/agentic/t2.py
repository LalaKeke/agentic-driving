"""Conservative convex reference-point corridor, with no behavior prediction."""
import math
from decimal import Decimal
from shapely.geometry import Point,Polygon,LineString
from shapely.ops import nearest_points
from .contracts import artifact,need,grid,validate
from .geometry import planes,polygon,obstacles,obstacles_native_cells,disk_radius,rear_state,integrate

YAW_BOX_PROFILE='yaw_bounded_box_v1'
YAW_BOX_BOUND_RAD=.35
SEARCH_PROFILE='bounded_seed_candidates_v1'
SEARCH_PROFILE_V2='bounded_seed_candidates_v2'
SEARCH_PROFILE_V3='bounded_seed_candidates_v3'
SEARCH_PROFILE_V4='bounded_seed_candidates_v4'
CANDIDATE_IDS=('original','brake_straight','brake_right','brake_left','brake_right_strong')
CANDIDATE_IDS_V4=CANDIDATE_IDS+('early_right_brake','early_right_moderate','early_right_progress')

def yaw_point_support(point,nx,ny,bound=YAW_BOX_BOUND_RAD):
    """Exact support of one rear-relative point rotated over [-bound,bound].

    The maximum sinusoid occurs at an endpoint or an aligned interior angle.
    This is a vehicle geometry calculation, never an actor prediction.
    """
    x,y=point
    values=[nx*(x*math.cos(t)-y*math.sin(t))+ny*(x*math.sin(t)+y*math.cos(t))
            for t in (-bound,bound)]
    angle=math.atan2(ny,nx)-math.atan2(y,x)
    if any(-bound<=angle+2*math.pi*k<=bound for k in (-1,0,1)):
        values.append(math.hypot(nx,ny)*math.hypot(x,y))
    return max(values)

def yaw_box_support(v,nx,ny,bound=YAW_BOX_BOUND_RAD):
    """Full BODY relative to rear axle; includes center offset exactly once."""
    d=v['center_to_rear_axle_m'];w=v['half_width_m']
    return max(yaw_point_support((x,y),nx,ny,bound)
               for x in (d-v['rear_m'],d+v['front_m']) for y in (-w,w))

def band_point_support(point,nx,ny,band):
    """Exact support over an asymmetric closed yaw band, no body shrinkage."""
    lo,hi=band;mid=(lo+hi)/2;c,s=math.cos(mid),math.sin(mid)
    x,y=point
    return yaw_point_support((c*x-s*y,s*x+c*y),nx,ny,(hi-lo)/2)

def band_body_support(v,nx,ny,band):
    d=v['center_to_rear_axle_m'];w=v['half_width_m']
    return max(band_point_support((x,y),nx,ny,band)
               for x in (d-v['rear_m'],d+v['front_m']) for y in (-w,w))

def candidate_seed(scene,candidate_id):
    """Five fixed, bounded ego references; no actor prediction or intent change."""
    need(candidate_id in CANDIDATE_IDS,'CORRIDOR_CANDIDATE','Unknown bounded seed candidate')
    times=grid(scene['metadata'])
    if candidate_id=='original':return _moving_seed(scene,times)
    v=scene['capture']['vehicle'];q=rear_state(scene['capture']['ego'],v);points=[]
    steer={'brake_straight':0.,'brake_right':-.18,'brake_left':.18,'brake_right_strong':-.22}[candidate_id]
    steer=math.copysign(min(abs(steer),v['max_steer_rad'],math.atan(v['wheelbase_m']*v['max_curvature_per_m'])),steer)
    for k,t in enumerate(times):
        dt=times[k+1]-t if k<len(times)-1 else times[1]-times[0]
        a=max(-v['max_brake_mps2'],-q[3]/dt) if k<len(times)-1 else 0.
        delta=steer if q[3]>1e-8 else 0.
        points.append(dict(time_s=t,rear_x_m=q[0],rear_y_m=q[1],yaw_rad=q[2],speed_mps=max(0.,q[3]),acceleration_mps2=a,steer_rad=delta))
        if k<len(times)-1:q=integrate(q,a,delta,dt,v)
    return points


def candidate_seed_v3(scene,candidate_id):
    """Five bounded ego references; v2 original/brake_straight stay identical.

    The remaining references describe finite S-shaped yaw schedules, rather
    than applying a fixed steering angle until they leave the global yaw cap.
    Only ego motion is constructed; actor BODY sources/decisions never change.
    """
    need(candidate_id in CANDIDATE_IDS,'CORRIDOR_CANDIDATE','Unknown bounded seed candidate')
    if candidate_id in ('original','brake_straight'):
        return candidate_seed(scene,candidate_id)
    amplitude={'brake_right':-.12,'brake_left':.12,'brake_right_strong':-.25}[candidate_id]
    v=scene['capture']['vehicle'];q=rear_state(scene['capture']['ego'],v)
    times=grid(scene['metadata']);horizon=times[-1];points=[]
    for k,t in enumerate(times):
        a=delta=0.
        if k<len(times)-1:
            dt=times[k+1]-t
            a=max(-v['max_brake_mps2'],-q[3]/dt)
            travel=q[3]*dt+.5*a*dt*dt
            target_yaw=amplitude*math.sin(math.pi*times[k+1]/horizon)
            curvature=(target_yaw-q[2])/travel if travel>1e-10 else 0.
            curvature=max(-v['max_curvature_per_m'],min(v['max_curvature_per_m'],curvature))
            delta=max(-v['max_steer_rad'],min(v['max_steer_rad'],math.atan(v['wheelbase_m']*curvature)))
        points.append(dict(time_s=t,rear_x_m=q[0],rear_y_m=q[1],yaw_rad=q[2],
            speed_mps=max(0.,q[3]),acceleration_mps2=a,steer_rad=delta))
        if k<len(times)-1:q=integrate(q,a,delta,times[k+1]-t,v)
    return points

def candidate_band(a,b):
    # Narrower geometry is paired with identical T3 and T4 hard yaw bounds.
    # The global +/- .35rad cap remains; these bands are search restrictions.
    return [max(-YAW_BOX_BOUND_RAD,min(a['yaw_rad'],b['yaw_rad'])-.025),
            min(YAW_BOX_BOUND_RAD,max(a['yaw_rad'],b['yaw_rad'])+.025)]


def candidate_seed_v4(scene,candidate_id):
    """Keep v3 references; add three finite early lateral shifts, never predictions.

    New references retain the original control grid. Four intermediate samples
    use the SAME constant control, solely to form narrower time-local BODY/yaw
    corridors; T3 still optimizes one acceleration/steer pair per original cell.
    """
    need(candidate_id in CANDIDATE_IDS_V4,'CORRIDOR_CANDIDATE','Unknown bounded seed candidate')
    if candidate_id in CANDIDATE_IDS:return candidate_seed_v3(scene,candidate_id)
    peak,tail,brake=(-.22,-.15,3.) if candidate_id=='early_right_brake' else (-.26,-.1,1.5)
    v=scene['capture']['vehicle'];times=grid(scene['metadata']);q=rear_state(scene['capture']['ego'],v)
    progress=candidate_id=='early_right_progress'
    if progress:
        need(0<=q[3]<=v['max_speed_mps'],'CORRIDOR_SEED_DYNAMICS',
             'Progress reference starts outside original vehicle speed bounds','tool_infeasible')
    coarse=[];targets=[0.,-.30,-.30,-.25,-.15,-.05,0.] if progress else [0.,peak,tail,tail,0.]
    for k,t in enumerate(times):
        a=delta=0.
        if k<len(times)-1:
            dt=times[k+1]-t
            a=(min(1.,v['max_accel_mps2'],(v['max_speed_mps']-q[3])/dt) if progress
               else max(-min(brake,v['max_brake_mps2']),-q[3]/dt))
            travel=q[3]*dt+.5*a*dt*dt
            target=targets[k+1] if k+1<len(targets) else 0.
            curvature=(target-q[2])/travel if travel>1e-10 else 0.
            curvature=max(-v['max_curvature_per_m'],min(v['max_curvature_per_m'],curvature))
            delta=max(-v['max_steer_rad'],min(v['max_steer_rad'],math.atan(v['wheelbase_m']*curvature)))
        coarse.append(dict(time_s=t,rear_x_m=q[0],rear_y_m=q[1],yaw_rad=q[2],
                           speed_mps=max(0.,q[3]),acceleration_mps2=a,steer_rad=delta))
        if k<len(times)-1:q=integrate(q,a,delta,times[k+1]-t,v)
    points=[]
    for k,(a,b) in enumerate(zip(coarse,coarse[1:])):
        state=[a[n] for n in ('rear_x_m','rear_y_m','yaw_rad','speed_mps')]
        for j in range(4):
            elapsed=(b['time_s']-a['time_s'])*j/4
            q=integrate(state,a['acceleration_mps2'],a['steer_rad'],elapsed,v)
            points.append(dict(time_s=a['time_s']+elapsed,rear_x_m=q[0],rear_y_m=q[1],yaw_rad=q[2],
                speed_mps=max(0.,q[3]),acceleration_mps2=a['acceleration_mps2'],steer_rad=a['steer_rad']))
    points.append(coarse[-1]);return points

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



def _failure_source_evidence(scene,k,entry):
    """Attach original citations to a failed constraint, never revise geometry."""
    result=dict(entry)
    if entry['kind']!='occupancy':return result
    step=Decimal(str(scene['metadata']['step_s']));left,right=step*k,step*(k+1)
    shape=polygon(entry['occupancy_polygon_m']);sources=[]
    raw=scene['provenance']['metric_contract_trace']['source_output']['hypotheses']
    for actor in raw['objects']:
        if actor['id']!=entry['object_id']:continue
        for source in actor['occupancy']:
            if max(left,Decimal(str(source['start_s'])))>=min(right,Decimal(str(source['end_s']))):continue
            for alt in source['alternatives']:
                if shape.equals_exact(polygon(alt['polygon_m']),0):
                    sources.append(dict(source_id=source['id'],scenario_id=alt['scenario_id'],
                        source_interval_s=[source['start_s'],source['end_s']],polygon_m=alt['polygon_m'],
                        frame_ids=source['frame_ids'],time_ids=source['time_ids'],
                        premise_ids=source['premise_ids'],source_residual_m=source['residual_m']))
    if entry['object_id']=='unknown_region':
        sources=[dict(frame_ids=r['frame_ids'],time_ids=r['time_ids'],polygon_m=r['polygon_m'])
                 for r in raw['unknown_regions'] if shape.equals_exact(polygon(r['polygon_m']),0)]
    result['native_sources']=sources
    return result


def build(scene,policy='initial_anchor',candidate_id=None,search_profile=SEARCH_PROFILE):
    validate('scene_v2.schema.json',scene)
    need(policy in ('initial_anchor','moving_seed',YAW_BOX_PROFILE),'CORRIDOR_POLICY','Unknown explicit corridor construction policy')
    cap=scene['capture'];v=cap['vehicle'];m=scene['metadata'];d=scene['decision']
    r=disk_radius(v);margin=cap['safety']['margin_m'];anchor=Point(rear_state(cap['ego'],v)[:2])
    base=polygon(scene['hypotheses']['road']['polygon_m'],True)
    box=policy==YAW_BOX_PROFILE
    search=candidate_id is not None
    temporal=search_profile==SEARCH_PROFILE_V4
    adaptive=search_profile in (SEARCH_PROFILE_V3,SEARCH_PROFILE_V4)
    native=search_profile in (SEARCH_PROFILE_V2,SEARCH_PROFILE_V3,SEARCH_PROFILE_V4)
    candidate_ids=CANDIDATE_IDS_V4 if temporal else CANDIDATE_IDS
    need(search_profile in (SEARCH_PROFILE,SEARCH_PROFILE_V2,SEARCH_PROFILE_V3,SEARCH_PROFILE_V4) and (not native or search),
         'CORRIDOR_CANDIDATE','Explicit candidate profile required')
    if native:
        from .metric_contract import validate_scene
        validate_scene(scene)
    need(not search or (box and candidate_id in candidate_ids and (candidate_id!='original' or native)),
         'CORRIDOR_CANDIDATE','Candidate search requires explicit bounded-box profile')
    if box:
        need(abs(cap['ego']['yaw_rad'])<=YAW_BOX_BOUND_RAD,'CORRIDOR_HEADING_BOUND',
             'Initial yaw outside explicit bounded-box planner profile','tool_infeasible')
    support=lambda nx,ny: yaw_box_support(v,nx,ny) if box else r
    hp=[[nx,ny,b-support(nx,ny)-scene['hypotheses']['road']['uncertainty_m']] for nx,ny,b in planes(base)]
    # Target describes CENTER region. A rear-axle disk of radius d conservatively contains center.
    hp += [[nx,ny,b-(yaw_point_support((v['center_to_rear_axle_m'],0),nx,ny) if box
                     else v['center_to_rear_axle_m'])] for nx,ny,b in planes(polygon(d['target_region'],True))]
    if d['stop']:hp.append([1.,0.,d['stop']['center_x_max_m']-v['center_to_rear_axle_m']])
    times=grid(m);cells=[]
    substeps=4 if temporal and candidate_id not in CANDIDATE_IDS else 1
    seed=((candidate_seed_v4(scene,candidate_id) if temporal else candidate_seed_v3(scene,candidate_id)) if adaptive else candidate_seed(scene,candidate_id)) if search else (_moving_seed(scene,times) if policy in ('moving_seed',YAW_BOX_PROFILE) else None)
    if substeps>1:times=[p['time_s'] for p in seed]
    previous=None
    for k in range(len(times)-1):
        constraints=[p[:] for p in hp]
        band=None
        if seed is None:seed_geometry=anchor
        else:
            a,b=seed[k:k+2]
            ends=[(a['rear_x_m'],a['rear_y_m']),(b['rear_x_m'],b['rear_y_m'])]
            seed_geometry=Point(ends[0]) if ends[0]==ends[1] else LineString(ends)
        if search:
            band=candidate_band(a,b)
            if substeps>1:
                # Search restriction, not a safety reduction: these SAME narrow
                # bands constrain T3 exact sub-states and independent T4.
                band=[max(-YAW_BOX_BOUND_RAD,min(a['yaw_rad'],b['yaw_rad'])-.001),
                      min(YAW_BOX_BOUND_RAD,max(a['yaw_rad'],b['yaw_rad'])+.001)]
            if (adaptive and k==0 and candidate_id in ('original','brake_straight')
                    and a['yaw_rad']==0. and b['yaw_rad']==0.
                    and polygon(d['target_region'],True).covers(Point(cap['ego']['x_m'],cap['ego']['y_m']))):
                # A single conservative offset over a nonzero yaw band can
                # exclude the exact initial rear axle when its CENTER is on
                # a valid closed target boundary. Restrict only this zero-
                # steering reference's first interval to its exact yaw; T3/T4
                # enforce that same tighter band. No target boundary moves.
                target_planes=planes(polygon(d['target_region'],True))
                if any(nx*anchor.x+ny*anchor.y>z-band_point_support((v['center_to_rear_axle_m'],0),nx,ny,band)+1e-7
                       for nx,ny,z in target_planes):
                    band=[0.,0.]
            need(band[0]<=a['yaw_rad']<=band[1] and band[0]<=b['yaw_rad']<=band[1],
                 'CORRIDOR_HEADING_BOUND','Candidate leaves the unchanged global yaw cap','tool_infeasible',interval=[times[k],times[k+1]])
            constraints=[[nx,ny,z-band_body_support(v,nx,ny,band)-scene['hypotheses']['road']['uncertainty_m']] for nx,ny,z in planes(base)]
            constraints += [[nx,ny,z-band_point_support((v['center_to_rear_axle_m'],0),nx,ny,band)] for nx,ny,z in planes(polygon(d['target_region'],True))]
            if d['stop']:constraints.append([1.,0.,d['stop']['center_x_max_m']-band_point_support((v['center_to_rear_axle_m'],0),1,0,band)])
        constraint_evidence=[]
        if native:
            constraint_evidence=[dict(kind='road',halfplane=constraints[i],
                input_boundary_halfplane=[nx,ny,z],ego_body_support_m=band_body_support(v,nx,ny,band),
                road_uncertainty_m=scene['hypotheses']['road']['uncertainty_m'])
                for i,(nx,ny,z) in enumerate(planes(base))]
            offset=len(constraint_evidence)
            constraint_evidence += [dict(kind='target_center',halfplane=constraints[offset+i],
                input_boundary_halfplane=[nx,ny,z],center_offset_support_m=band_point_support((v['center_to_rear_axle_m'],0),nx,ny,band))
                for i,(nx,ny,z) in enumerate(planes(polygon(d['target_region'],True)))]
            if d['stop']:constraint_evidence.append(dict(kind='stop',halfplane=constraints[-1],
                center_x_max_m=d['stop']['center_x_max_m'],center_offset_support_m=band_point_support((v['center_to_rear_axle_m'],0),1,0,band)))
        selection_geometry=seed_geometry
        if box:
            # Use the zero-steer seed BODY only to choose a separating normal.
            # Safety still uses the analytic support for every allowed yaw below.
            d0=v['center_to_rear_axle_m'];w=v['half_width_m']
            selection_geometry=Polygon([(p['rear_x_m']+math.cos(p['yaw_rad'])*x-math.sin(p['yaw_rad'])*y,
                                         p['rear_y_m']+math.sin(p['yaw_rad'])*x+math.cos(p['yaw_rad'])*y)
                for p in seed[k:k+2] for x,y in ((d0-v['rear_m'],-w),(d0+v['front_m'],-w),
                                             (d0+v['front_m'],w),(d0-v['rear_m'],w))]).convex_hull
        # Every temporal child uses the COMPLETE original parent occupancy,
        # including all adverse modes. Never interpolate or trim actor sources.
        for oid,shape,u in (obstacles_native_cells(scene,k//substeps) if native else obstacles(scene,k)):
            hull=shape.convex_hull
            origin,near=nearest_points(selection_geometry,hull)
            dx,dy=near.x-origin.x,near.y-origin.y;dist=math.hypot(dx,dy)
            need(native or (dist>0 and (box or dist>r+u+margin)),
                 'CORRIDOR_ANCHOR_BLOCKED' if seed is None else 'CORRIDOR_SEED_BLOCKED',
                 'Conservative corridor anchor blocked' if seed is None else
                 'Ego-only seed blocked; this does not prove all trajectories infeasible',
                 'tool_infeasible',object_id=oid,interval=[times[k],times[k+1]])
            if native:
                # Finite separating directions, all evaluated against the FULL
                # model BODY set and unchanged residual/margin. References only
                # rank safe half-spaces; they are not accepted trajectories.
                normals=([(dx/dist,dy/dist)] if dist>0 else [])+[(1.,0.),(-1.,0.),(0.,1.),(0.,-1.)]
                def slack(n):
                    nx,ny=n
                    limit=min(nx*x+ny*y for x,y in hull.exterior.coords)-band_body_support(v,nx,ny,band)-u-margin
                    return limit-max(nx*p['rear_x_m']+ny*p['rear_y_m'] for p in seed[k:k+2])
                nx,ny=max(normals,key=slack)
            else:
                nx,ny=dx/dist,dy/dist
            support=min(nx*x+ny*y for x,y in hull.exterior.coords)
            ego_support=band_body_support(v,nx,ny,band) if search else (yaw_box_support(v,nx,ny) if box else r)
            constraints.append([nx,ny,support-ego_support-u-margin])
            if native:constraint_evidence.append(dict(kind='occupancy',object_id=oid,
                halfplane=constraints[-1],obstacle_support_m=support,ego_body_support_m=ego_support,
                model_total_residual_m=u,safety_margin_m=margin,
                occupancy_polygon_m=[list(p) for p in shape.exterior.coords][:-1]))
        domain=base;empty_context={}
        for index,p in enumerate(constraints):
            before=domain;domain=clip(domain,p)
            if native and not empty_context and (domain.is_empty or domain.area<=1e-8):
                entry=_failure_source_evidence(scene,k//substeps,constraint_evidence[index])
                empty_context=dict(candidate_id=candidate_id,object_id=entry.get('object_id'),
                    heading_band_rad=band,reference_seed_segment=seed[k:k+2],
                    first_conflicting_constraint=entry,
                    allowed_domain_before_polygon_m=[list(x) for x in before.exterior.coords][:-1],
                    allowed_area_before_m2=before.area,area_after_m2=domain.area,
                    base_constraints=constraint_evidence[:len(planes(base))+len(planes(polygon(d['target_region'],True)))+bool(d['stop'])],
                    ordering_note='First constraint in construction order to remove positive area; not a minimal unsatisfiable set or global infeasibility proof.')
        need(not domain.is_empty and domain.area>1e-8,'CORRIDOR_EMPTY','Empty convex cell','tool_infeasible',interval=[times[k],times[k+1]],**empty_context)
        if seed is None:
            need(domain.buffer(1e-7).covers(anchor),'CORRIDOR_DISCONNECTED','Anchor not in convex cell','tool_infeasible')
        else:
            if not search:
                need(domain.buffer(1e-7).covers(seed_geometry),'CORRIDOR_SEED_OUTSIDE',
                     'Ego-only seed leaves a corridor cell; not a global infeasibility proof',
                     'tool_infeasible',interval=[times[k],times[k+1]],policy=policy)
            # Candidate references only choose a convex homotopy and initialize
            # IPOPT. They need not themselves solve every physical constraint.
            # The true initial state, cell connectivity, every T3 half-space and
            # independent continuous T4 remain mandatory before execution.
            if k==0:
                initial_context={}
                if native and not domain.buffer(1e-7).covers(anchor):
                    violated=[dict(_failure_source_evidence(scene,k//substeps,entry),
                        computed_m=p[0]*anchor.x+p[1]*anchor.y,threshold_m=p[2],
                        excess_m=p[0]*anchor.x+p[1]*anchor.y-p[2])
                        for p,entry in zip(constraints,constraint_evidence)
                        if p[0]*anchor.x+p[1]*anchor.y>p[2]+1e-7]
                    initial_context=dict(interval=[times[k],times[k+1]],candidate_id=candidate_id,
                        initial_rear_point_m=[anchor.x,anchor.y],heading_band_rad=band,
                        initial_state_violations=violated,object_id=next((x.get('object_id') for x in violated if x.get('object_id')),None),
                        allowed_domain_polygon_m=[list(x) for x in domain.exterior.coords][:-1])
                need(domain.buffer(1e-7).covers(anchor),'CORRIDOR_INITIAL_STATE',
                     'First corridor cell must contain the true initial rear axle','tool_infeasible',**initial_context)
            if previous is not None:
                need(not previous.intersection(domain).is_empty,'CORRIDOR_DISCONNECTED',
                     'Adjacent time cells do not share a transition region','tool_infeasible',
                     interval=[times[k-1],times[k+1]])
        previous=domain
        cells.append(dict(start_s=times[k],end_s=times[k+1],halfplanes=constraints,polygon_m=[list(p) for p in domain.exterior.coords][:-1],**({'heading_band_rad':band} if search else {})))
    # Default output stays byte-compatible. Moving seeds change construction only;
    # body envelopes, obstacle occupancies, margins and the T3/T4 checks are unchanged.
    extra={}
    if seed is not None:
        extra['construction']=dict(policy=policy,
            seed_source='ego_state_vehicle_limits_semantic_template_only',
            seed_dynamics='exact_constant_acceleration_zero_steer',
            target_prediction_source='model_whole_interval_occupancy_only',
            behavior=d['behavior'],seed_points=seed,seed_is_solution=False,
            limitation='A blocked seed is not a proof of global infeasibility')
        if search:
            extra['construction']['seed_dynamics']='exact_constant_acceleration_constant_rear_curvature'
            extra['construction']['candidate_id']=candidate_id
            extra['search_profile']=search_profile
            if temporal:extra['corridor_substeps']=substeps
    if box:
        return artifact(scene,cells=cells,reference_point='rear_axle',
            body_envelope='yaw_bounded_oriented_box',planner_profile=YAW_BOX_PROFILE,
            heading_bound_rad=YAW_BOX_BOUND_RAD,margin_m=margin,conservative=True,**extra)
    return artifact(scene,cells=cells,reference_point='rear_axle',body_envelope='orientation_independent_circumscribed_disk',body_radius_m=r,margin_m=margin,conservative=True,**extra)
