"""Independent raw-scene verifier. Exact dynamics + bounded continuous sweeps."""
import math
from shapely.geometry import Point,LineString
from .contracts import artifact,identity,need,grid,finite,validate,digest
from .geometry import polygon,planes,disk_radius,rear_state,center_state,integrate,obstacles

def verify(scene,trajectory):
    validate('scene_v2.schema.json',scene);validate('trajectory_v2.schema.json',trajectory)
    finite(trajectory);identity(scene,trajectory)
    cap=scene['capture'];m=scene['metadata'];v=cap['vehicle'];d=scene['decision'];pts=trajectory['points'];times=grid(m)
    need(trajectory['interpolation']=='exact_constant_acceleration_constant_rear_curvature','INTERPOLATION','Unsupported motion convention')
    need(len(pts)==len(times) and all(abs(p['time_s']-t)<1e-8 for p,t in zip(pts,times)),'TIME_COVERAGE','Trajectory time mismatch')
    need(0<trajectory['requested_prefix_s']<=cap['safety']['prefix_s'],'PREFIX','Prefix exceeds trusted configuration')
    violations=[];minimum=None;r=disk_radius(v);margin=cap['safety']['margin_m']
    def fail(code,t,end,p,value,threshold,unit,oid=None):
        violations.append(dict(code=code,object_id=oid,time_interval_s=[t,end],position_m=p[:2],computed=float(value),threshold=float(threshold),unit=unit,repair_request='Revise decision or evidence-backed scene hypothesis and replan: '+code))
    def state(p):return [p['rear_x_m'],p['rear_y_m'],p['yaw_rad'],p['speed_mps']]
    init=rear_state(cap['ego'],v)
    for j,(value,want) in enumerate(zip(state(pts[0]),init)):
        if abs(value-want)>1e-5:fail('INITIAL_STATE',0,0,state(pts[0]),value,want,['m','m','rad','m/s'][j])
    for e in scene['hypotheses']['traffic_evidence']:
        if e['rule'] not in ('stop_required','yield_required') or e['state'] not in ('active','inactive') or (e['state']=='active' and (d['behavior']!='stop_yield' or (e['rule']=='yield_required' and not d['yield_to']))):
            fail('TRAFFIC_INTENT',0,times[-1],init,0,1,'boolean')
    road=planes(polygon(scene['hypotheses']['road']['polygon_m'],True));ru=scene['hypotheses']['road']['uncertainty_m']
    target=planes(polygon(d['target_region'],True))
    for p in pts:
        q=state(p);c=center_state(q,v)
        if max(abs(p['x_m']-c[0]),abs(p['y_m']-c[1]))>1e-5:fail('REFERENCE_POINT',p['time_s'],p['time_s'],q,math.hypot(p['x_m']-c[0],p['y_m']-c[1]),1e-5,'m')
        if abs(p['curvature_per_m']-math.tan(p['steer_rad'])/v['wheelbase_m'])>1e-6:fail('CURVATURE_CONTROL',p['time_s'],p['time_s'],q,p['curvature_per_m'],math.tan(p['steer_rad'])/v['wheelbase_m'],'1/m')
    for k,(p,b) in enumerate(zip(pts,pts[1:])):
        t=times[k];dt=times[k+1]-t;q=state(p);a=p['acceleration_mps2'];delta=p['steer_rad'];curv=math.tan(delta)/v['wheelbase_m']
        expected=integrate(q,a,delta,dt,v)
        for j,(value,want) in enumerate(zip(state(b),expected)):
            if abs(value-want)>1e-5:fail('DYNAMICS_RESIDUAL',t,t+dt,q,abs(value-want),1e-5,['m','m','rad','m/s'][j])
        motion_ok=True
        for code,value,lo,hi,unit in [('SPEED',q[3],0,v['max_speed_mps'],'m/s'),('SPEED',expected[3],0,v['max_speed_mps'],'m/s'),('ACCELERATION',a,-v['max_brake_mps2'],v['max_accel_mps2'],'m/s^2'),('STEERING',delta,-v['max_steer_rad'],v['max_steer_rad'],'rad'),('CURVATURE',curv,-v['max_curvature_per_m'],v['max_curvature_per_m'],'1/m')]:
            if value<lo-1e-6 or value>hi+1e-6:
                motion_ok=False;fail(code,t,t+dt,q,value,lo if value<lo else hi,unit)
        if not motion_ok:continue
        # At most 0.02 s per subinterval. The extra arc-length/2 bound, not sampling,
        # makes these whole-interval checks conservative and continuous.
        count=max(1,math.ceil(dt/.02))
        cuts=[j*dt/count for j in range(count+1)]
        if d['stop'] and t<d['stop']['deadline_s']<t+dt:cuts.append(d['stop']['deadline_s']-t)
        cuts=sorted(set(cuts))
        obs=list(obstacles(scene,k))
        for left,right in zip(cuts,cuts[1:]):
            qa=integrate(q,a,delta,left,v);qb=integrate(q,a,delta,right,v)
            ca=center_state(qa,v);cb=center_state(qb,v)
            travel=max(0,(qa[3]+qb[3])*.5*(right-left));pad=travel/2+1e-8
            sweep=LineString([qa[:2],qb[:2]]) if travel>1e-10 else Point(qa[:2])
            for nx,ny,limit in road:
                value=max(nx*qa[0]+ny*qa[1],nx*qb[0]+ny*qb[1])+r+pad+ru
                if value>limit+1e-6:fail('ROAD_BOUNDARY',t+left,t+right,ca,value,limit,'m')
            cpad=pad*(1+v['center_to_rear_axle_m']*abs(curv))
            for nx,ny,limit in target:
                value=max(nx*ca[0]+ny*ca[1],nx*cb[0]+ny*cb[1])+cpad
                if value>limit+1e-6:fail('TARGET_INTENT',t+left,t+right,ca,value,limit,'m')
            if d['stop']:
                value=max(ca[0],cb[0])+cpad
                if value>d['stop']['center_x_max_m']+1e-6:fail('STOP_BOUNDARY',t+left,t+right,ca,value,d['stop']['center_x_max_m'],'m')
                if t+right>=d['stop']['deadline_s']-1e-8:
                    elapsed=max(left,d['stop']['deadline_s']-t)
                    value=max(integrate(q,a,delta,elapsed,v)[3],qb[3])
                    if value>1e-5:fail('STOP_DEADLINE',max(t+left,d['stop']['deadline_s']),t+right,ca,value,0,'m/s')
            for oid,shape,u in obs:
                clearance=sweep.distance(shape)-r-pad-u
                minimum=clearance if minimum is None else min(minimum,clearance)
                if clearance<margin-1e-6:fail('BODY_COLLISION',t+left,t+right,ca,clearance,margin,'m',oid)
    return artifact(scene,verdict='FAIL' if violations else 'PASS',violations=violations,minimum_clearance_lower_bound_m=minimum,checked_until_s=times[-1],released_prefix_s=0 if violations else trajectory['requested_prefix_s'],trajectory_digest=digest(trajectory),claim=scene['claim'])
