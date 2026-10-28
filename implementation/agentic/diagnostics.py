"""Aggregate deterministic contract faults for feedback, without repairing model facts.

Diagnostics never authorize planning. T1-T4 remain the hard acceptance gates.
"""
from .contracts import grid,Failure
from .geometry import polygon,planes,rear_state


def diagnose(capture,output):
    issues=[]
    def add(code,path,expected,actual,repair):
        issues.append(dict(code=code,path=path,expected=expected,actual=actual,repair_request=repair))
    m=capture['metadata'];d=output.get('decision',{});h=output.get('hypotheses',{})
    deadline=m['reference_time_s']+m['horizon_s'];valid=d.get('valid_until_s')
    if not isinstance(valid,(int,float)) or valid<deadline:
        add('DECISION_EXPIRED','decision.valid_until_s',deadline,valid,'Use the acquired planning validity; execution prefix is a separate shorter duration.')
    try:
        region=polygon(d['target_region'],True);q=rear_state(capture['ego'],capture['vehicle']);distance=capture['vehicle']['center_to_rear_axle_m']
        if any(nx*q[0]+ny*q[1]>b-distance+1e-8 for nx,ny,b in planes(region)):
            add('TARGET_INITIAL_STATE','decision.target_region','All-horizon CENTER region whose rear-reference configuration contains the initial state',d['target_region'],'Provide the image-supported allowed center region for the entire path. Put a stopping point in stop, not in a tiny target box. Do not expand without evidence.')
    except (Failure,KeyError,TypeError,ValueError,AttributeError):pass  # T1 still reports the authoritative geometry error.
    if h.get('scale_basis')=='calibrated_ground_plane':
        add('CALIBRATED_PROJECTION_UNVERIFIED','hypotheses.scale_basis','Supported metric hypothesis with evidence, or unknown',h['scale_basis'],'This profile has no verified projection contract. Retain unknown if scale is unsupported; changing the label alone does not add evidence.')
    times=grid(m)
    for i,obj in enumerate(h.get('objects',[])):
        cells=obj.get('occupancy');path=f'hypotheses.objects[{i}].occupancy'
        if cells is None:
            add('OCCUPANCY_UNKNOWN',path,'Evidence-supported whole-interval occupancy or explicit null',None,'Unknown stays unknown. Request additional evidence; never insert placeholder envelopes.')
        elif len(cells)!=len(times)-1 or any(abs(c.get('start_s',-1)-a)>1e-8 or abs(c.get('end_s',-1)-b)>1e-8 for c,a,b in zip(cells,times,times[1:])):
            add('OCCUPANCY_GAP',path,dict(interval_count=len(times)-1,start_s=0,end_s=times[-1],step_s=m['step_s']),dict(interval_count=len(cells)),'Use the supplied relative interval grid. A genuine supported whole-horizon envelope may be repeated unchanged across its covered cells; unsupported placeholders remain invalid.')
    return issues
