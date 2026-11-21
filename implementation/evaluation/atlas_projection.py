"""Front-camera projection and display styles used in the frozen collision atlas."""
import math
import sys
from pathlib import Path

import numpy as np
from PIL import ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from cordriver_astra_packet import clip_segment, sha


def project(layer, ctx):
    meta = ctx["overlay_geometry"]
    cam = ctx["cameras"]["CAM_FRONT"]
    times = np.asarray(layer["time_s"], dtype=float)
    xy = layer["xy"]
    if times.ndim != 1 or len(times) != len(xy) or not np.isfinite(times).all() or (np.diff(times) <= 0).any():
        raise ValueError("Trajectory needs one finite, strictly increasing time per point")
    linear = np.asarray(meta["xy_guidance_to_reference_ego_linear"])
    origin = np.asarray(meta["xy_guidance_origin_in_reference_ego_m"])
    depth_transform = np.asarray(cam["T_camera_from_reference_ego"])
    k = np.asarray(cam["K_original_px"])
    w, h = cam["width_px"], cam["height_px"]
    points, markers, segments = [], [], []
    for time, point in zip(times, xy):
        if point is None:
            points.append(None)
            continue
        coord = np.asarray(point, dtype=float)
        if coord.shape != (2,) or not np.isfinite(coord).all():
            raise ValueError("Invalid BODY-center coordinate")
        x, y = linear @ coord + origin
        xyz = (depth_transform @ [x,y,meta["display_height_reference_ego_z_m"],1.])[:3]
        points.append(xyz)
        if xyz[2] >= .05:
            p = k @ xyz
            uv = p[:2] / p[2]
            if 0 <= uv[0] <= w-1 and 0 <= uv[1] <= h-1:
                markers.append({"t_s":float(time), "uv":uv.tolist()})
    for i, (a,b) in enumerate(zip(points, points[1:])):
        if a is None or b is None:
            continue
        part = clip_segment(a,b,k,w,h)
        if part is None:
            continue
        lo, hi, p, q = part
        start, duration = times[i], times[i+1]-times[i]
        segments.append(dict(segment_index=i,t_start_s=float(start+lo*duration),
                             t_end_s=float(start+hi*duration),start_uv=list(p),end_uv=list(q)))
    return dict(**layer, segments=segments, markers=markers,
                visible_s=math.fsum(s["t_end_s"]-s["t_start_s"] for s in segments))


STYLES = {
    "initial": ("Learned proposal", "#38c9ff", (18, 9)),
    "qp": ("CorDriver QP", "#ffbb46", ()),
    "astra": ("Astra-guided plan", "#58f1a0", ()),
    "fixed": ("Fixed corridor", "#fa859c", (11, 7)),
    "recorded": ("Recorded ego", "#d9a8ff", (5, 8)),
}
INK, PAPER, MUTED, HALO = "#182a34", "#f7f8f5", "#4a5e68", "#17232b"


def _layer(image, key, trace, scale):
    _, color, pattern = STYLES[key]
    pen = ImageDraw.Draw(image)
    width = max(2, round(4 * scale))
    halo_width = width + 2 * max(1, round(scale))
    previous = None
    phase = 0.
    for seg in trace["segments"]:
        p, q = seg["start_uv"], seg["end_uv"]
        if previous is None or math.hypot(p[0]-previous[0], p[1]-previous[1]) > .25:
            phase = 0.
        previous = q
        dx, dy = q[0]-p[0], q[1]-p[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        if pattern:
            dash, gap = (v * scale for v in pattern)
            period = dash + gap
            start = -phase
            while start < length:
                lo, hi = max(0., start), min(length, start + dash)
                if hi > lo:
                    a = (p[0]+dx*lo/length, p[1]+dy*lo/length)
                    b = (p[0]+dx*hi/length, p[1]+dy*hi/length)
                    pen.line((a, b), fill=HALO, width=halo_width)
                    pen.line((a, b), fill=color, width=width)
                start += period
            phase = (phase + length) % period
        else:
            pen.line((tuple(p), tuple(q)), fill=HALO, width=halo_width)
            pen.line((tuple(p), tuple(q)), fill=color, width=width)

    for mark in trace["markers"]:
        t = mark["t_s"]
        if abs(t-1.5) > 1e-7 and abs(t-3.) > 1e-7:
            if not trace["segments"]:
                x, y = mark["uv"]
                radius = max(2, round(3 * scale))
                pen.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color, outline=HALO)
            continue
        x, y = mark["uv"]
        radius = max(3, round(6 * scale))
        if abs(t-1.5) < 1e-7:
            pen.ellipse((x-radius,y-radius,x+radius,y+radius), fill=color, outline=HALO,
                        width=max(1,round(2*scale)))
        else:
            shape = [(x,y-radius-1),(x+radius+1,y),(x,y+radius+1),(x-radius-1,y)]
            pen.polygon(shape, fill=color)
            pen.line(shape+[shape[0]], fill=HALO, width=max(1,round(2*scale)))


def _detail_crop(size, traces):
    w, h = size
    coords = [seg[end] for tr in traces.values() for seg in tr["segments"]
              for end in ("start_uv", "end_uv")]
    if not coords:
        return (0, 0, w, h)
    xs, ys = zip(*coords)
    # A single bounding window shared by all methods preserves their relative alignment.
    frac = min(1., max(.38, (max(xs)-min(xs)+.08*w)/w, (max(ys)-min(ys)+.1*h)/h))
    cw, ch = round(w*frac), round(h*frac)
    left = min(w-cw,max(0,round((min(xs)+max(xs)-cw)/2)))
    top = min(h-ch,max(0,round((min(ys)+max(ys)-ch)/2)))
    return left, top, left+cw, top+ch
