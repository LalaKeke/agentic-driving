"""Describe an actual turning task; these are not safety or grounding limits."""
import math

from .contracts import finite,identity,need
from .geometry import rear_state,center_state,integrate


def evaluate_turn(scene,trajectory):
    """Measure the unchanged plan and its exactly integrated first prefix.

    The caller must independently pass T4 before releasing anything. This task
    check cannot authorize a prefix or turn a rejected trajectory into a safe
    one. It does not inspect, predict or interpolate any other road actor.
    """
    finite(trajectory);identity(scene,trajectory)
    cap=scene['capture'];vehicle=cap['vehicle'];ego=cap['ego']
    points=trajectory['points'];prefix=cap['safety']['prefix_s']
    need(scene['metadata']['horizon_s']==2. and prefix==.2,
         'TURN_TASK_CONFIGURATION','Turning description is registered for a two-second plan and 0.2-second prefix')
    need(len(points)>=2 and abs(points[0]['time_s'])<1e-8
         and abs(points[-1]['time_s']-2.)<1e-8
         and points[1]['time_s']>=prefix-1e-8,
         'TURN_TASK_TIME','A complete two-second plan and first prefix control are required')
    initial=rear_state(ego,vehicle);first=points[0]
    end=integrate(initial,first['acceleration_mps2'],first['steer_rad'],prefix,vehicle)
    center=center_state(end,vehicle)
    measures=dict(
        plan_max_abs_yaw_change_deg=max(abs(p['yaw_rad']-ego['yaw_rad']) for p in points)*180/math.pi,
        plan_max_abs_center_lateral_displacement_m=max(abs(p['y_m']-ego['y_m']) for p in points),
        prefix_abs_yaw_change_deg=abs(end[2]-initial[2])*180/math.pi,
        prefix_center_displacement_m=math.hypot(center[0]-ego['x_m'],center[1]-ego['y_m']),
        prefix_center_displacement_xy_m=[center[0]-ego['x_m'],center[1]-ego['y_m']],
        prefix_duration_s=prefix)
    thresholds=dict(plan_min_max_abs_yaw_change_deg=5.,
                    plan_min_max_abs_center_lateral_displacement_m=.3,
                    prefix_min_abs_yaw_change_deg=.1,
                    prefix_min_center_displacement_m_exclusive=.01)
    checks=dict(
        plan_yaw=measures['plan_max_abs_yaw_change_deg']>=5.,
        plan_lateral=measures['plan_max_abs_center_lateral_displacement_m']>=.3,
        prefix_yaw=measures['prefix_abs_yaw_change_deg']>=.1,
        prefix_moving=measures['prefix_center_displacement_m']>.01)
    return dict(task_profile='visible_turn_description_v1',verdict='PASS' if all(checks.values()) else 'FAIL',
                failure_code=None if all(checks.values()) else 'TURN_TASK_NOT_DEMONSTRATED',
                measurements=measures,thresholds=thresholds,checks=checks,
                claim='Descriptive engineering turning task only; not collision safety, G2 accuracy, or completed real-world corner traversal. Independent T4 and execution acceptance are separately required.')
