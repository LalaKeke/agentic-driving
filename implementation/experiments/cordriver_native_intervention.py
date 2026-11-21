"""Decide whether to request a correction using only the native return and model predictions."""

import numpy as np

from cordriver_measurements import body, forecast_rings, official_qp


def intervention(sample, visual, upstream):
    if not (sample["token"] == visual["token"] == upstream["token"]):
        raise ValueError("Mismatched native scene")
    poses, status = official_qp(sample, upstream)
    if poses is None:
        return dict(consider_guidance=True, reasons=[status], peak_sampled_normal_accel_mps2=None,
                    first_predicted_overlap_s=None)
    rings=forecast_rings(visual)
    overlaps=[]
    for index, (pose, modes) in enumerate(zip(poses[1:],rings[1:]), 1):
        ego=body(pose[:2], pose[2], 4.084, 1.85)
        if any(ego.intersects(polygon) for _,_,polygon in modes):
            overlaps.append(index*.5)
    accel=np.diff(poses[:,:2], n=2, axis=0)/(.5**2)
    normals=np.column_stack([-np.sin(poses[1:-1,2]),np.cos(poses[1:-1,2])])
    peak=float(np.max(np.abs(np.sum(accel * normals,axis=1))))
    reasons=[]
    if overlaps:
        reasons.append("NATIVE_INTERSECTS_PREDICTED_BODY")
    if peak > 3.0:
        reasons.append("NATIVE_SAMPLED_NORMAL_ACCEL_OVER_3")
    return dict(consider_guidance=bool(reasons),reasons=reasons,peak_sampled_normal_accel_mps2=peak,
                first_predicted_overlap_s=min(overlaps) if overlaps else None)
