"""CasADi/IPOPT constrained multiple shooting; no hard constraint relaxation."""
import os
# Bound linear algebra threads before importing CasADi/NumPy.
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import math
import casadi as ca
from .contracts import artifact,identity,grid,need,Failure,validate
from .geometry import rear_state,center_state

def solve(scene,corridor,max_iter=700):
    validate('scene_v2.schema.json',scene);validate('corridor_v2.schema.json',corridor)
    identity(scene,corridor)
    cap=scene['capture'];v=cap['vehicle'];d=scene['decision'];times=grid(scene['metadata']);N=len(times)-1;dt=times[1]
    opt=ca.Opti();X=opt.variable(4,N+1);U=opt.variable(2,N)
    initial=rear_state(cap['ego'],v);opt.subject_to(X[:,0]==initial)
    opt.subject_to(opt.bounded(0,X[3,:],v['max_speed_mps']))
    opt.subject_to(opt.bounded(-v['max_brake_mps2'],U[0,:],v['max_accel_mps2']))
    opt.subject_to(opt.bounded(-v['max_steer_rad'],U[1,:],v['max_steer_rad']))
    weights=d['preferences'];cost=0
    for k in range(N):
        x,y,p,speed=ca.vertsplit(X[:,k]);a,delta=ca.vertsplit(U[:,k]);curv=ca.tan(delta)/v['wheelbase_m']
        opt.subject_to(opt.bounded(-v['max_curvature_per_m'],curv,v['max_curvature_per_m']))
        travel=speed*dt+.5*a*dt*dt;z=curv*travel/2
        # Series at zero, safe denominator in inactive branch. Error < 1e-16 for |z|<1e-4.
        sinc=ca.if_else(ca.fabs(z)<1e-4,1-z*z/6+z**4/120,ca.sin(z)/ca.if_else(ca.fabs(z)<1e-4,1,z))
        nxt=ca.vertcat(x+travel*sinc*ca.cos(p+z),y+travel*sinc*ca.sin(p+z),p+2*z,speed+a*dt)
        opt.subject_to(X[:,k+1]==nxt)
        # Arc-length interpolation: ||d^2 rear_position / ds^2|| = |curv|.
        # The rear arc is within |curv| * travel^2 / 8 of its endpoint chord,
        # for the entire interval, including nonuniform time from acceleration.
        # sqrt(curv^2 + eps^2) >= |curv| is a smooth conservative upper bound;
        # eps=1e-10 (1/m). The closed physical half-space remains unchanged.
        arc_pad=ca.sqrt(curv*curv+1e-20)*travel*travel/8
        for nx,ny,b in corridor['cells'][k]['halfplanes']:
            # Respect the declared closed half-space. An extra shrink rejects valid
            # stationary states on a stop boundary; physical margins are already in T2.
            for j in (k,k+1):opt.subject_to(nx*X[0,j]+ny*X[1,j]+arc_pad<=b)
        if d['stop']:
            deadline=d['stop']['deadline_s']
            if times[k]>=deadline-1e-8:opt.subject_to(X[3,k]==0)
            if times[k]<deadline<=times[k+1]+1e-8:
                opt.subject_to(speed+a*(deadline-times[k])==0)
                opt.subject_to(X[3,k+1]==0)
        cost+=dt*(weights['speed_weight']*(speed-d['desired_speed_mps'])**2+weights['acceleration_weight']*a*a+weights['steering_weight']*delta*delta)
        if k:cost+=weights['smoothness_weight']*ca.sumsqr(U[:,k]-U[:,k-1])
    if d['stop'] and d['stop']['deadline_s']<=times[-1]:opt.subject_to(X[3,N]==0)
    opt.minimize(cost)
    for k,t in enumerate(times):
        brake=min(v['max_brake_mps2'],cap['ego']['speed_mps']/max(d['stop']['deadline_s'],dt)) if d['stop'] else 0
        q=min(t,cap['ego']['speed_mps']/brake) if brake else t
        opt.set_initial(X[:,k],[initial[0]+initial[3]*q-.5*brake*q*q,initial[1],initial[2],max(0,initial[3]-brake*t)])
    opt.set_initial(U,0)
    opt.solver('ipopt',{'print_time':False},{'print_level':0,'sb':'yes','max_iter':max_iter,'tol':1e-8,'acceptable_tol':1e-7})
    try:sol=opt.solve()
    except RuntimeError:
        raise Failure('OPTIMIZATION_FAILED','IPOPT did not produce a feasible solution; no constraints relaxed','tool_infeasible',solver_status=opt.stats().get('return_status','unknown')) from None
    points=[]
    for k,t in enumerate(times):
        q=sol.value(X[:,k]).tolist();a=float(sol.value(U[0,min(k,N-1)]));steer=float(sol.value(U[1,min(k,N-1)]));center=center_state(q,v)
        points.append(dict(time_s=t,rear_x_m=q[0],rear_y_m=q[1],x_m=center[0],y_m=center[1],yaw_rad=q[2],speed_mps=q[3],acceleration_mps2=a,steer_rad=steer,curvature_per_m=math.tan(steer)/v['wheelbase_m']))
    return artifact(scene,points=points,interpolation='exact_constant_acceleration_constant_rear_curvature',requested_prefix_s=cap['safety']['prefix_s'],solver=dict(name='casadi_ipopt_multiple_shooting',version=ca.__version__,status=sol.stats()['return_status'],objective=float(sol.value(cost))))
