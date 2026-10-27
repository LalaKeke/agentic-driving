"""Conservative convex reference-point corridor, with no behavior prediction."""
import math
from shapely.geometry import Point,Polygon
from shapely.ops import nearest_points
from .contracts import artifact,need,grid,validate
from .geometry import planes,polygon,obstacles,disk_radius,rear_state

def clip(domain,plane):
    if domain.is_empty:return domain
    nx,ny,b=plane
    # Clip via a very large half-plane relative to current finite domain bounds.
    bound=max(abs(x) for x in domain.bounds)+abs(b)+100
    tx,ty=-ny,nx;anchor=(nx*b,ny*b)
    pts=[(anchor[0]+tx*u-nx*v,anchor[1]+ty*u-ny*v) for u,v in [(-4*bound,0),(4*bound,0),(4*bound,8*bound),(-4*bound,8*bound)]]
    return domain.intersection(Polygon(pts))

def build(scene):
    validate('scene_v2.schema.json',scene)
    cap=scene['capture'];v=cap['vehicle'];m=scene['metadata'];d=scene['decision']
    r=disk_radius(v);margin=cap['safety']['margin_m'];anchor=Point(rear_state(cap['ego'],v)[:2])
    base=polygon(scene['hypotheses']['road']['polygon_m'],True)
    hp=[[nx,ny,b-r-scene['hypotheses']['road']['uncertainty_m']] for nx,ny,b in planes(base)]
    # Target describes CENTER region. A rear-axle disk of radius d conservatively contains center.
    hp += [[nx,ny,b-v['center_to_rear_axle_m']] for nx,ny,b in planes(polygon(d['target_region'],True))]
    if d['stop']:hp.append([1.,0.,d['stop']['center_x_max_m']-v['center_to_rear_axle_m']])
    times=grid(m);cells=[]
    for k in range(len(times)-1):
        constraints=[p[:] for p in hp]
        for oid,shape,u in obstacles(scene,k):
            hull=shape.convex_hull
            near=nearest_points(anchor,hull)[1]
            dx,dy=near.x-anchor.x,near.y-anchor.y;dist=math.hypot(dx,dy)
            need(dist>r+u+margin,'CORRIDOR_ANCHOR_BLOCKED','Conservative corridor anchor blocked','tool_infeasible',object_id=oid,interval=[times[k],times[k+1]])
            nx,ny=dx/dist,dy/dist
            support=min(nx*x+ny*y for x,y in hull.exterior.coords)
            constraints.append([nx,ny,support-r-u-margin])
        domain=base
        for p in constraints:domain=clip(domain,p)
        need(not domain.is_empty and domain.area>1e-8,'CORRIDOR_EMPTY','Empty convex cell','tool_infeasible',interval=[times[k],times[k+1]])
        need(domain.buffer(1e-7).covers(anchor),'CORRIDOR_DISCONNECTED','Anchor not in convex cell','tool_infeasible')
        cells.append(dict(start_s=times[k],end_s=times[k+1],halfplanes=constraints,polygon_m=[list(p) for p in domain.exterior.coords][:-1]))
    # Every cell contains the fixed anchor: space-time connected, not a dynamics guarantee.
    return artifact(scene,cells=cells,reference_point='rear_axle',body_envelope='orientation_independent_circumscribed_disk',body_radius_m=r,margin_m=margin,conservative=True)
