"""Independent interval BODY checks for a saved expansion candidate, using exact arcs."""
import argparse
import json
from pathlib import Path
import numpy as np
import shapely
from shapely.geometry import shape

from cordriver_iter_swept import certify, exact_path


def check_road(candidate, support, edges):
    if support.is_empty:
        return {"status":"UNKNOWN_NO_CONNECTED_SUPPORT","intervals_certified":0}
    boundary = support.boundary
    lines = shapely.union_all(edges) if edges else None
    for n in (10,50,250):
        bodies, rates, drift = exact_path(candidate,n)
        covered = shapely.covers(support,bodies)
        hits = shapely.intersects(lines,bodies) if lines is not None else np.zeros(len(bodies),bool)
        distances = shapely.distance(bodies,boundary)
        if lines is not None:
            distances = np.minimum(distances,shapely.distance(bodies,lines))
        lower=np.minimum(distances[:-1],distances[1:])-rates*.125/n-1e-8
        dt=.25/n
        missing=np.flatnonzero(~covered | hits)
        stats=dict(intervals=len(rates),dt_s=dt,intervals_certified=int(((lower>0)&covered[:-1]&covered[1:]&~hits[:-1]&~hits[1:]).sum()),
                   observed_boundary_hit_poses=int(hits.sum()),unsupported_poses=int((~covered).sum()),
                   first_unsupported_or_hit_s=None if not len(missing) else float(missing[0]*dt),
                   lower_bound_m=float(lower.min()),min_sampled_boundary_distance_m=float(distances.min()),rk4_drift_m=drift)
        if hits.any():return dict(status="CROSSES_PREDICTED_BOUNDARY",**stats)
        if not covered.all():return dict(status="UNKNOWN_OUTSIDE_MEASURED_SUPPORT",**stats)
        if (lower>0).all():return dict(status="POSITIVE_ROAD_LOWER_BOUND",**stats)
    return dict(status="UNRESOLVED_INTERVAL",**stats)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("candidate","visual","road"):
        p.add_argument("--"+key,type=Path,required=True)
    args=p.parse_args()
    read=lambda path:json.loads(path.read_bytes())
    c=read(args.candidate);v=read(args.visual);r=read(args.road)
    print(json.dumps(dict(objects=certify(c,v),road=check_road(c,shape(r["support"]),[shape(x) for x in r["edges"]]))))


if __name__=="__main__":
    main()
