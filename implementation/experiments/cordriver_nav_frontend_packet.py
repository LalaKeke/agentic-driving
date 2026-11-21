"""Construct six unmodified camera inputs and one front-view navigation/trajectory overlay."""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import cordriver_astra_packet as camera
from cordriver_nav_route import BRANCHES, body_frame


def save(path, data):
    with Path(path).open("x", encoding="utf8") as f:
        json.dump(data,f,indent=2,allow_nan=False)
        f.write("\n")


def render(original, out, intrinsic, camera_from_ref, path, route, time_marks):
    def project(points):
        hom = np.column_stack([points,np.zeros(len(points)),np.ones(len(points))])
        return (hom @ camera_from_ref.T)[:, :3]

    with Image.open(original) as im:
        view = im.convert("RGB")
    w,h = view.size
    draw = ImageDraw.Draw(view)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf",19)
    except OSError:
        font = ImageFont.load_default()
    segments = {}
    for name,line in (("navigation",route),("network_reference",path)):
        pts = project(line)
        visible = []
        for i,(a,b) in enumerate(zip(pts,pts[1:])):
            part = camera.clip_segment(a,b,intrinsic,w,h)
            if part is None:
                continue
            lower,upper,p,q = part
            start,end = np.array(p),np.array(q)
            visible.append(dict(segment_index=i,from_fraction=lower,to_fraction=upper,
                                start_uv=start.tolist(),end_uv=end.tolist()))
            if name == "navigation":
                length = np.linalg.norm(end-start)
                if length == 0:
                    continue
                for s in np.arange(0.,length,22.):
                    u=tuple(start+(end-start)*s/length)
                    v=tuple(start+(end-start)*min(s+13.,length)/length)
                    draw.line((u,v),fill=(30,23,30),width=10)
                    draw.line((u,v),fill=(248,63,201),width=6)
            else:
                draw.line((p,q),fill=(20,20,20),width=6)
                draw.line((p,q),fill=(24,235,245),width=3)
        segments[name]=visible
    for p,t in zip(project(path),time_marks):
        if p[2] <= .05:
            continue
        uv = intrinsic@p
        x,y = uv[:2]/uv[2]
        if 0<=x<w and 0<=y<h:
            draw.ellipse((x-3,y-3,x+3,y+3),fill=(255,220,65))
            draw.text((min(w-48,x+4),min(h-22,max(0.,y-10))),f"{t:g}s",font=font,fill=(255,220,65),
                      stroke_width=2,stroke_fill=(20,20,20))
    for i,(name,text,color) in enumerate([("navigation","Navigation (distance only)",(248,63,201)),
                                          ("network_reference","Initial trajectory (timed)",(24,235,245))]):
        if not segments[name]:
            text += " - out of view"
        draw.text((12,8+25*i),text,font=font,fill=color,stroke_width=2,stroke_fill=(10,10,10))
    view.save(out,format="PNG")
    return segments


def build(info, rec, nav, out, data):
    if rec["token"] != info["token"] or rec["timestamp_us"] != info["timestamp"]:
        raise ValueError("PACKET_FRAME_MISMATCH")
    if BRANCHES[rec["navigation_command_index"]] != nav["command"]:
        raise ValueError("PACKET_BRANCH_MISMATCH")
    if any(rec[k] > info["timestamp"] for k in ("can_pose_timestamp_us","can_steer_timestamp_us")):
        raise ValueError("FUTURE_CAN")
    out.mkdir(parents=True,exist_ok=False)
    linear,origin = body_frame(info)
    can = np.asarray(rec["can_bus"])
    centers,timed = camera.reference(rec["qp_reference_steps"],can[13])
    lidar = camera.se3(info["lidar2ego_rotation"],info["lidar2ego_translation"])
    images,cameras,audit = [],{},{}
    for ch in camera.CHANNELS:
        c=info["cams"][ch]
        source=rec["cameras"][ch]
        original=Path(camera.data_path(c["data_path"],data)).resolve(strict=True)
        original_hash=camera.sha(original)
        if (source["sha256"]!=original_hash or source["timestamp_us"]!=c["timestamp"]
                or c["type"]!=ch or c["timestamp"]>info["timestamp"]):
            raise ValueError(f"INCONSISTENT_CAMERA_{ch}")
        mat,extrinsic,diff=camera.camera_transform(info,c,lidar)
        intrinsic=np.asarray(c["cam_intrinsic"])
        with Image.open(original) as im:
            w,h=im.size
        cameras[ch]=dict(width_px=w,height_px=h,exposure_us=int(c["timestamp"]),
            delta_from_reference_us=int(c["timestamp"]-info["timestamp"]),sample_data_token=c["sample_data_token"],
            K_original_px=intrinsic.tolist(),T_exposure_ego_from_camera=extrinsic.tolist(),
            T_camera_from_reference_ego=mat.tolist())
        images.append(dict(path=str(original),sha256=original_hash,label=ch+" original RGB"))
        audit[ch]=dict(original_sha256=original_hash,
                       T_camera_from_reference_ego_independent_max_abs_difference=diff)
        if ch=="CAM_FRONT":
            marked=out/"CAM_FRONT_navigation_reference.png"
            projection=render(original,marked,intrinsic,mat,centers@linear.T+origin,
                np.asarray(nav["polyline_guidance_m"])@linear.T+origin,[r["t_s"] for r in timed])
            audit[ch]["projection"]=projection
    images.append(dict(path=str(marked.resolve()),sha256=camera.sha(marked),
        label="CAM_FRONT same RGB | magenta dashed: untimed navigation; cyan: initial network trajectory; yellow: times"))
    assert len(images)==7
    context=dict(sample_token=rec["token"],reference_time_us=int(info["timestamp"]),
        guidance_frame="Fixed initial nominal CorDriver footprint center; x forward, y left, metres. Relative times in seconds, positive heading turns toward +y.",
        navigation=nav,initial_speed_mps=float(can[13]),measured_ego_state=dict(
            source="Latest at-or-before-reference nuScenes CAN pose; axes in the vehicle frame.",
            timestamp_us=rec["can_pose_timestamp_us"],delta_from_reference_us=rec["can_pose_timestamp_us"]-info["timestamp"],
            velocity_xyz_mps=can[13:16].tolist(),accel_xyz_raw_mps2=can[7:10].tolist(),rotation_rate_xyz_radps=can[10:13].tolist()),
        trajectory_kind="CorDriver pre-QP learned proposal from the matching navigation branch; footprint center from native tangent heading plus 0.5 m. At t>0, speed is preceding interval average.",
        trajectory=timed,cameras=cameras,overlay_geometry=dict(display_height_reference_ego_z_m=0.,
            xy_guidance_to_reference_ego_linear=linear.tolist(),xy_guidance_origin_in_reference_ego_m=origin.tolist(),
            visible_front_segments={k:len(v) for k,v in projection.items()},
            explanation="Original pixels. Horizontal display plane z=0 at reference ego. Drawn segments are in-camera geometrically; terrain height and occlusions are unknown."),
        sensor_scope="Six original camera exposures bound to this decision and a marked CAM_FRONT copy. The registered keyframe camera is used when available by the reference time, otherwise its nearest earlier same-camera sweep is used. Each camera has its own exposure time and pose.")
    packet=out/"packet.json"
    save(packet,dict(token=rec["token"],model_context=context,images=images))
    save(out/"provenance.json",dict(packet_sha256=camera.sha(packet),camera_checks=audit))
    return packet
