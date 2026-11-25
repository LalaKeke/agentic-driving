"""Front-camera projection and display styles used in the frozen collision atlas."""
import math
import sys
from pathlib import Path

import numpy as np
from PIL import ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from cordriver_astra_packet import clip_segment, sha


def transfer_front(root, token, out):
    """Draw the actual fast and minimum-change trajectories in the same recorded view."""
    import json
    from PIL import Image, ImageFont
    from cordriver_fast_review import fast_path
    from cordriver_nav_solver import parse_nodes

    def load(path):
        return json.loads(path.read_text())
    archive = root/"archived"
    study = archive/"study"
    packet_file = study/"packets"/token/"packet.json"
    packet = load(packet_file)
    ctx = packet["model_context"]
    front, = [im for im in packet["images"] if im["label"]=="CAM_FRONT original RGB"]
    path = packet_file.parent/front["path"]
    if sha(path)!=front["sha256"]:
        raise ValueError("CAM_FRONT_DIGEST_MISMATCH")
    original = Image.open(path).convert("RGB")
    _, _, fast, _, _ = fast_path(study,token,archive/"fast"/token/"return.json")
    chosen = load(archive/"window_minimal"/token/"minimal/result.json")
    candidate = Path(chosen["selected_path"])
    if sha(candidate)!=chosen["selected_sha256"]:
        raise ValueError("RETURNED_TRACK_CHANGED")
    plan = load(candidate)
    guidance=load(archive/"window_minimal"/token/"task.json")["guidance"]
    scored, = [r for r in load(archive/"minimal_scores.json") if r["token"]==token]
    head=ImageFont.truetype("DejaVuSans.ttf",22)
    small=ImageFont.truetype("DejaVuSans.ttf",17)
    board=Image.new("RGB",(1600,548),PAPER)
    pen=ImageDraw.Draw(board)
    for j,(arm,xy,style,label) in enumerate((("fast",fast[:,:2],"qp","Fast system"),
                ("minimal",plan["body_xy_m"],"astra","Astra targets + refined trajectory"))):
        canvas=original.copy()
        trace=project(dict(time_s=np.linspace(0.,3.,len(xy)),xy=xy),ctx)
        _layer(canvas,style,trace,1.5)
        if arm=="minimal":
            for _,center,basis,half,_ in parse_nodes(guidance):
                corners=np.array([[1,1],[1,-1],[-1,-1],[-1,1],[1,1]])*half @ basis.T + center
                for seg in project(dict(time_s=range(5),xy=corners),ctx)["segments"]:
                    ImageDraw.Draw(canvas).line((tuple(seg["start_uv"]),tuple(seg["end_uv"])),fill="#ffdd44",width=4)
        x=800*j
        pen.text((x+12,8),label,font=head,fill=INK)
        board.paste(canvas.resize((800,450),Image.Resampling.LANCZOS if hasattr(Image,"Resampling") else Image.LANCZOS),(x,42))
        numbers=scored[arm]
        pen.text((x+10,498),f"3 s    ACR {numbers['ACR_3s']}    CCR {numbers['CCR_3s']}    L2 {numbers['L2_3s']:.3f} m",
                 font=small,fill=INK)
    pen.text((10,524),f"{scored['scene']}   {token[:8]}    Same CAM_FRONT exposure   |   Yellow outlines: target regions at 1.5 s and 3 s   |   Display-plane projection",font=small,fill=MUTED)
    out.parent.mkdir(parents=True,exist_ok=True)
    board.save(out)


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
