"""Extra 25 ms sampled check of model footprints between the planning audit knots."""
import json

import numpy as np
import shapely
from shapely.geometry import Polygon

from cordriver_iter_road import BODY_CORNERS, XY, BODY_ORIGIN_SHIFT


def states(plan, dt):
    knot = np.asarray(plan["state_xy_yaw_v"])
    control = np.asarray(plan["controls_accel_steer"])
    ts = np.arange(121)*dt
    cell = np.minimum(np.arange(len(ts))//10,11)
    t = ts - .25*cell
    acceleration, angle = control[cell].T
    x,y,heading,v = knot[cell].T
    dist = t*(v + .5*acceleration*t)
    turn = dist*np.tan(angle)/2.
    direction = heading + turn/2
    arc = dist*np.sinc(turn/(2*np.pi))
    yaw = heading + turn
    center = np.column_stack((x+arc*np.cos(direction),y+arc*np.sin(direction)))
    center -= knot[0,:2]+[.5,0.]
    center += .5*np.column_stack((np.cos(yaw),np.sin(yaw)))
    return ts, center, yaw


def check(plan, visual):
    ts, center, yaw = states(plan,.025)
    rotation = np.stack((np.cos(yaw),-np.sin(yaw),np.sin(yaw),np.cos(yaw)),axis=1).reshape(-1,2,2)
    rings = center[:,None,:] + BODY_CORNERS[None] @ rotation.transpose(0,2,1)
    vectorized = hasattr(shapely, 'polygons')
    ego = shapely.polygons(rings) if vectorized else [Polygon(r) for r in rings]
    near = None
    event_times = set()
    for obj in visual["current_objects"]:
        if obj["score"] < .4:
            continue
        ring = np.asarray(obj["footprint_lidar_xy_m"]) @ XY.T + BODY_ORIGIN_SHIFT
        for k, offsets in enumerate(obj["future_step_xy_lidar_m"]):
            track = np.vstack((np.zeros(2),np.cumsum(np.asarray(offsets) @ XY.T,axis=0)))
            ticks = np.arange(7)*.5
            pos = np.column_stack([np.interp(ts,ticks,track[:,i]) for i in range(2)])
            rings = ring[None]+pos[:,None,:]
            if vectorized:
                polygons = shapely.polygons(rings)
                distances = shapely.distance(ego,polygons)
                touches = shapely.intersects(ego,polygons)
            else:
                polygons = [Polygon(r) for r in rings]
                distances = np.array([a.distance(b) for a,b in zip(ego,polygons)])
                touches = np.array([a.intersects(b) for a,b in zip(ego,polygons)])
            n = int(np.argmin(distances))
            point = (float(distances[n]),float(ts[n]),obj["rank"],k)
            if near is None or point < near:
                near = point
            event_times.update(float(s) for s in ts[touches])
    return dict(samples=121, min_predicted_m=near[0] if near else None,
                closest_s=near[1] if near else None, closest_rank=near[2] if near else None,
                closest_mode=near[3] if near else None, overlap_times=len(event_times),
                first_overlap_s=min(event_times) if event_times else None,
                empty_observed_set=near is None)
