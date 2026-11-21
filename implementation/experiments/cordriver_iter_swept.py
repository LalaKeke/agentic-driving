"""Bound the continuous distance to translated predicted BODY polygons for saved controls.

Uses the exact constant-control arcs from the initial pose (one continuous
path). Prediction centers interpolate linearly, with the archived footprint
orientation held fixed, as in the other audits. This certificate concerns
model obstacles only, not road observability or unmodelled physical actors.
"""
import json

import numpy as np
import shapely

from cordriver_iter_road import BODY_CORNERS, BODY_ORIGIN_SHIFT, XY


def exact_rings(candidate, subdivisions):
    u=np.asarray(candidate["controls_accel_steer"])
    first=np.asarray(candidate["state_xy_yaw_v"][0])
    initial=first[:2].copy()
    rear=initial.copy()
    yaw,speed=first[2:]
    xy=[];angles=[];rates=[]
    dt=.25/subdivisions
    for acc,steer in u:
        t=np.arange(subdivisions+1)*dt
        dist=t*(speed+.5*acc*t)
        turn=dist*np.tan(steer)/2.
        phi=yaw+turn
        mid=yaw+turn/2
        positions=rear+(dist*np.sinc(turn/(2*np.pi)))[:,None]*np.column_stack((np.cos(mid),np.sin(mid)))
        centers=positions-initial-[.5,0.]+.5*np.column_stack((np.cos(phi),np.sin(phi)))
        xy.extend(centers[:-1]);angles.extend(phi[:-1])
        end_speed=speed+acc*.25
        # Furthest corner from the rear axle bounds rotational motion of every body point.
        radius=np.linalg.norm(BODY_CORNERS+[.5,0.],axis=1).max()
        rates.extend([max(abs(speed),abs(end_speed))*(1.+radius*abs(np.tan(steer))/2.)]*subdivisions)
        rear,yaw,speed=positions[-1].copy(),phi[-1],end_speed
    xy=np.array(xy+[centers[-1]])
    angles=np.array(angles+[phi[-1]])
    rot=np.stack((np.cos(angles),-np.sin(angles),np.sin(angles),np.cos(angles)),axis=1).reshape(-1,2,2)
    footprints=xy[:,None,:]+BODY_CORNERS[None]@rot.transpose(0,2,1)
    discrepancy=float(np.linalg.norm(xy[::subdivisions]-candidate['body_xy_m'],axis=1).max())
    return footprints,np.array(rates),discrepancy


def exact_path(candidate, subdivisions):
    rings,rates,drift=exact_rings(candidate,subdivisions)
    return shapely.polygons(rings),rates,drift


def test(candidate,visual,n):
    ego,body_rate,drift=exact_path(candidate,n)
    t=np.arange(len(ego))*(.25/n)
    intervals=np.arange(len(t)-1)//(2*n)
    closest=(float('inf'),None,None,None)
    lowest=(float('inf'),None,None,None)
    count=0
    for obj in visual['current_objects']:
        if obj['score']<.4:continue
        ring=np.asarray(obj['footprint_lidar_xy_m'])@XY.T+BODY_ORIGIN_SHIFT
        for mode,offsets in enumerate(obj['future_step_xy_lidar_m']):
            count+=1
            steps=np.asarray(offsets)@XY.T
            poses=np.vstack((np.zeros(2),np.cumsum(steps,axis=0)))
            pos=np.column_stack([np.interp(t,np.arange(7)*.5,poses[:,k]) for k in (0,1)])
            distances=shapely.distance(ego,shapely.polygons(ring[None]+pos[:,None,:]))
            i=np.argmin(distances)
            if distances[i]<closest[0]:
                closest=(float(distances[i]),float(t[i]),obj['rank'],mode)
            max_rate=body_rate+np.linalg.norm(steps,axis=1)[intervals]*2.
            # Each time lies within dt/2 of an endpoint. Closed-set distance
            # is 1-Lipschitz under the sum of the two Hausdorff displacements.
            lower=np.minimum(distances[:-1],distances[1:])-max_rate*.125/n-1.e-8
            j=np.argmin(lower)
            if lower[j]<lowest[0]:
                lowest=(float(lower[j]),float(t[j]),obj['rank'],mode)
    return dict(dt_s=.25/n,retained_modes=count,
                status=('EMPTY_PREDICTION_SET' if not count else 'INTERSECTS' if closest[0]<=0.
                        else 'POSITIVE_LOWER_BOUND' if lowest[0]>0. else 'UNRESOLVED'),
                sampled_min_m=closest[0] if count else None,time_of_sampled_min_s=closest[1],
                closest_rank=closest[2],closest_mode=closest[3],continuous_lower_m=lowest[0] if count else None,
                tightest_interval_start_s=lowest[1],bound_rank=lowest[2],bound_mode=lowest[3],
                continuous_vs_native_max_center_delta_m=drift)


def certify(candidate,visual):
    for n in (10,50,250,1250):
        result=test(candidate,visual,n)
        if result['status']!='UNRESOLVED':break
    return result
