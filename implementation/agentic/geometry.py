"""Deterministic geometry; no inferred environment facts."""
import math
from decimal import Decimal
from shapely.geometry import Polygon, Point
from shapely.geometry.polygon import orient
from .contracts import need

def polygon(vertices,convex=False):
    p=Polygon(vertices)
    need(p.is_valid and p.area>1e-8,'INVALID_GEOMETRY','Invalid polygon')
    need(not convex or abs(p.convex_hull.area-p.area)<1e-7,'UNSUPPORTED_GEOMETRY','This profile requires convex road/target regions')
    return orient(p,sign=1.)
def planes(p):
    pts=list(orient(p,sign=1.).exterior.coords)
    result=[]
    for a,b in zip(pts,pts[1:]):
        dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        if length>1e-10:result.append([dy/length,-dx/length,(dy*a[0]-dx*a[1])/length])
    return result

def disk_radius(v):
    # Rear axle reference: circumscribed disk encloses every body orientation.
    d=v['center_to_rear_axle_m']
    return math.hypot(max(v['front_m']+d,abs(v['rear_m']-d)),v['half_width_m'])
def rear_state(ego,v):
    d=v['center_to_rear_axle_m'];p=ego['yaw_rad']
    return [ego['x_m']-d*math.cos(p),ego['y_m']-d*math.sin(p),p,ego['speed_mps']]
def center_state(q,v):
    d=v['center_to_rear_axle_m'];return [q[0]+d*math.cos(q[2]),q[1]+d*math.sin(q[2]),q[2],q[3]]
def integrate(q,a,delta,t,v):
    """Exact constant acceleration and rear-axle curvature; no reverse allowed."""
    k=math.tan(delta)/v['wheelbase_m'];s=q[3]*t+.5*a*t*t;z=k*s/2
    sinc=math.sin(z)/z if abs(z)>1e-10 else 1-z*z/6
    return [q[0]+s*sinc*math.cos(q[2]+z),q[1]+s*sinc*math.sin(q[2]+z),q[2]+2*z,q[3]+a*t]
def obstacles(scene,k):
    h=scene['hypotheses']
    for o in h['objects']:
        for poly in o['occupancy'][k]['alternatives_m']:
            yield o['id'],polygon(poly),o['uncertainty_m']
    for r in h['unknown_regions']:
        yield 'unknown_region',polygon(r['polygon_m']),r['uncertainty_m']

def obstacles_native_cells(scene,k):
    """Original whole-interval sources with positive temporal overlap.

    Only for the explicit v2 planner after T1 has checked complete, closed,
    nonzero source coverage for EVERY retained scenario. A source touching
    only a grid boundary need not be extended to the entire neighbouring cell.
    Each cell still checks both endpoints: its own closed sources cover them,
    and the adjacent cell independently checks that same transition state.
    No polygon, mode, residual or motion prediction is changed or interpolated.
    Decimal arithmetic avoids treating a binary grid artefact as real overlap.
    """
    trace=scene.get('provenance',{}).get('metric_contract_trace')
    need(isinstance(trace,dict) and trace.get('version')=='3.0.0-exp2',
         'METRIC_TRACE','Native interval selection requires revalidated exp2 sources')
    raw=trace['source_output']['hypotheses']
    step=Decimal(str(scene['metadata']['step_s']))
    left,right=step*k,step*(k+1)
    for actor in raw['objects']:
        sources=actor['occupancy']
        need(sources is not None,'OCCUPANCY_UNKNOWN','Null cannot be selected as empty occupancy','tool_infeasible',object_id=actor['id'])
        for src in sources:
            if max(Decimal(str(src['start_s'])),left)<min(Decimal(str(src['end_s'])),right):
                for alt in src['alternatives']:
                    yield actor['id'],polygon(alt['polygon_m']),actor['uncertainty_m']
    for region in scene['hypotheses']['unknown_regions']:
        yield 'unknown_region',polygon(region['polygon_m']),region['uncertainty_m']

def project_ground(pixel,intrinsic,rotation_camera_to_ego,origin_ego):
    """Optional calibrated ray-plane intersection, caller asserts static z=0 ground contact.
    Not used to recover dynamic object state or unverified monocular scale.
    """
    import numpy as np
    K=np.asarray(intrinsic,float);R=np.asarray(rotation_camera_to_ego,float);o=np.asarray(origin_ego,float)
    need(K.shape==(3,3) and R.shape==(3,3) and o.shape==(3,) and np.isfinite(K).all() and np.isfinite(R).all() and np.isfinite(o).all(), 'CALIBRATION','Invalid calibration')
    need(abs(np.linalg.det(K))>1e-9 and np.allclose(R.T@R,np.eye(3),atol=1e-6) and abs(np.linalg.det(R)-1)<1e-6,'CALIBRATION','Invalid rigid rotation/intrinsics')
    ray=R@np.linalg.solve(K,[pixel[0],pixel[1],1.])
    need(abs(ray[2])>1e-9,'PROJECTION','Ray parallel to ground')
    t=-o[2]/ray[2];need(t>0,'PROJECTION','Ground intersection behind camera')
    return (o+t*ray)[:2].tolist()
