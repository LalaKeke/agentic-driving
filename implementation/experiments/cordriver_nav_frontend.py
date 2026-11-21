"""Run a frozen CorDriver with observed images and the assigned nuScenes baseline navigation path."""
import importlib
import json
import math
from pathlib import Path
import pickle
import subprocess
import sys
import time
from unittest.mock import patch

import cvxpy as cp
import numpy as np
import torch
from nuscenes.utils.data_classes import Box
from pyquaternion import Quaternion

import cordriver_causal_frontend as base
from cordriver_nav_frontend_packet import build, save
from cordriver_nav_route import BRANCHES, navigation


FEATURE_ENCODINGS = ("causal_physical_v1", "released_stage2_v1")


def release_feature_yaw(rotation_wxyz):
    # The released converter reads the raw nuScenes quaternion into x, y, z, w.
    # Reproduce its training feature here only. Calibration, CAN and BEV use physical poses.
    x, y, z, w = rotation_wxyz
    return math.atan2(2 * (w*z + x*y), 1 - 2 * (z*z + y*y))


def network_ego_features(row, previous, message, steering, location, encoding):
    if encoding not in FEATURE_ENCODINGS:
        raise ValueError("Unsupported CorDriver input encoding: " + str(encoding))
    values = base.ego_features(row, previous, message, steering, location)
    if encoding == "released_stage2_v1":
        # The official training converter uses a nominal 0.5 s keyframe difference.
        yaw = release_feature_yaw(row["ego2global_rotation"])
        last_yaw = release_feature_yaw(previous["ego2global_rotation"])
        displacement = np.subtract(row["ego2global_translation"][:2], previous["ego2global_translation"][:2])
        speed = np.linalg.norm(displacement) / 0.5
        values[0] = speed * math.cos(yaw + math.pi/2)
        values[1] = speed * math.sin(yaw + math.pi/2)
        values[4] = (yaw - last_yaw) / 0.5
    return values


def predictions(head,pred,class_names,map_classes):
    maps=head.map_bbox_coder.decode(pred)[0]
    lines=[dict(rank=i,label=map_classes[int(label)],score=float(score),points=pts.tolist())
           for i,(label,score,pts) in enumerate(zip(maps["map_labels"],maps["map_scores"],maps["map_pts"]))]
    boxes=head.bbox_coder.decode(pred)[0]
    logits=pred["all_cls_scores"][-1,0]
    modes=pred["all_traj_cls_scores"][-1,0].reshape(logits.shape[0],head.fut_mode).sigmoid()
    identifiers=torch.arange(len(logits),device=logits.device,dtype=logits.dtype)[:,None]
    associated=head.bbox_coder.decode_single(logits,pred["all_bbox_preds"][-1,0],torch.cat([identifiers,modes],dim=1))
    if any(not torch.equal(boxes[k],associated[k]) for k in ("bboxes","labels","scores")):
        raise ValueError("OBJECT_QUERY_BINDING_MISMATCH")
    objects=[]
    for i,(box,label,score,future,aux) in enumerate(zip(boxes["bboxes"],boxes["labels"],boxes["scores"],
                                                    boxes["trajs"],associated["trajs"])):
        b=box.cpu().numpy()
        # This is the exact horizontal convention in the upstream driver's output_to_nusc_box.
        quat=Quaternion(axis=[0,0,1],radians=float(-b[6]-np.pi/2))
        vertices=Box(b[:3],b[3:6],quat).bottom_corners()[:2].T
        objects.append(dict(rank=i,query_id=int(aux[0]),label=class_names[int(label)],score=float(score),
            bbox9_center_xyz_wlh_yaw_vxvy=b.tolist(),footprint_lidar_xy_m=vertices.tolist(),
            future_step_xy_lidar_m=future.reshape(head.fut_mode,head.fut_ts,2).tolist(),
            future_mode_scores=aux[1:].tolist()))
    return dict(map_features=lines,current_objects=objects,frame="Metric local LiDAR; +y nominally forward, +x nominally right.",
        object_decoder="Pinned VAD head CustomNMSFreeCoder/nuScenes output_to_nusc_box horizontal footprint. 0.5 s steps are per-mode XY displacements (cumulative sum from current center); future yaw and sizes unavailable.",
        query_identity="Unique query_id within observation; a query can appear under several ranked classes.")


def prepare_model():
    sys.path.insert(0,str(base.UPSTREAM))
    importlib.import_module("projects.mmdet3d_plugin")
    ops=importlib.import_module("projects.mmdet3d_plugin.datasets.pipelines.transform_3d")
    config=base.UPSTREAM/"projects/configs/VAD/Corridor_stage_2.py"
    cfg=base.mmcv.Config.fromfile(str(config))
    transforms=[ops.NormalizeMultiviewImage(**cfg.img_norm_cfg),ops.RandomScaleImageMultiViewImage(scales=[.8]),
                ops.PadMultiViewImage(size_divisor=32)]
    base.set_random_seed(0,deterministic=True)
    cfg.model.pretrained=None
    cfg.model.train_cfg=None
    model=base.build_model(cfg.model,test_cfg=cfg.get("test_cfg"))
    checkpoint=torch.load(str(base.WEIGHTS),map_location="cpu")
    model.load_state_dict({(k[7:] if k.startswith("module.") else k):v for k,v in checkpoint["state_dict"].items()},strict=True)
    model.cuda().eval()
    return model,config,cfg,transforms


def inference(model,transforms,timeline,registration_case,location,data,folder,command,cfg,
              feature_encoding="causal_physical_v1"):
    qp=model.pts_bbox_head.qp
    head=model.pts_bbox_head
    case=registration_case
    name=case["name"]
    busdir=data/"can_bus"
    pose=base.read_messages(busdir/(name+"_pose.json"))
    steer=base.read_messages(busdir/(name+"_steeranglefeedback.json"))
    prev_bev=prev_row=None
    history=[]
    if len(timeline)<2 or any(a["timestamp"]>=b["timestamp"] or a["scene_token"]!=b["scene_token"]
                              for a,b in zip(timeline,timeline[1:])):
        raise ValueError("INVALID_SENSOR_HISTORY")
    for idx,row in enumerate(timeline):
        ts=row["timestamp"]
        measurement,steering=base.latest(pose,ts),base.latest(steer,ts)
        if measurement is None or steering is None:
            if idx==len(timeline)-1:
                raise ValueError("MISSING_CURRENT_CAUSAL_CAN")
            history.append(dict(token=row["token"],status="OMITTED_NO_PAST_CAN"))
            continue
        imgs,meta,cameras=base.images(row,data,transforms)
        can=base.bus_vector(row,measurement,prev_row)
        meta.update(can_bus=can,scene_token=row["scene_token"],sample_idx=row["token"])
        with torch.no_grad():
            feat=model.extract_feat(img=imgs,img_metas=[meta])
            if idx<len(timeline)-1:
                prev_bev=head(feat,[meta],prev_bev=prev_bev,only_bev=True)
                prev_row=row
                history.append(dict(token=row["token"],status="BEV_WARMUP",cameras=cameras,timestamp_us=ts,
                                    can_pose_timestamp_us=measurement["utime"]))
                continue
            nav_index=BRANCHES.index(command["command"])
            nav_onehot=np.eye(3,dtype=np.float32)[nav_index]
            rec=dict(token=row["token"],scene=row["scene_token"],scene_name=name,timestamp_us=ts,
                cameras=cameras,history=history,navigation_command_index=nav_index,navigation=command,
                can_pose_timestamp_us=measurement["utime"],can_steer_timestamp_us=steering["utime"],
                can_bus=can.tolist(),feature_encoding=feature_encoding,
                ego_features=network_ego_features(row,timeline[idx-1],measurement,steering,location,feature_encoding))
            head_module=importlib.import_module("projects.mmdet3d_plugin.VAD.VAD_head")
            native_qp=qp.forward
            solver=cp.Problem.solve
            def observe_solver(problem,*args,**options):
                try:
                    return solver(problem,*args,**options)
                finally:
                    rec.update(solver_status=problem.status,solver=getattr(problem.solver_stats,"solver_name",None))
            def observe_qp(bus,feature,reference,corridor,**opts):
                rec.update(qp_called=True,qp_reference_steps=reference[0].tolist(),qp_corridor=corridor[0].tolist(),
                           qp_device=str(reference.device))
                native_module=sys.modules[qp.__class__.__module__]
                factory=native_module.QPFunction
                def observe_factory(*args,**options):
                    solve=factory(*args,**options)
                    def observe_controls(*problem):
                        u=solve(*problem)
                        rec["qp_controls_accel_steer"]=u.detach().reshape(12,2).cpu().tolist()
                        return u
                    return observe_controls
                with patch.object(cp.Problem,"solve",observe_solver),patch.object(native_module,"QPFunction",observe_factory):
                    try:
                        output=native_qp(bus,feature,reference,corridor,**opts)
                    except Exception as ex:
                        rec["qp_error"]=type(ex).__name__+": "+str(ex)[:400]
                        raise
                rec["qp_return_steps"]=output[0].tolist()
                return output
            failed=head.qp_fail_time
            start=time.monotonic()
            with patch.object(qp,"forward",observe_qp):
                pred=head(feat,[meta],prev_bev=prev_bev,ego_lcf_feat=imgs.new_tensor(rec["ego_features"]).reshape(1,1,1,9),
                    ego_fut_cmd=imgs.new_tensor(nav_onehot).reshape(1,1,1,3),gt_corridor=[imgs.new_empty(0)],
                    ego_his_trajs=None,ego_fut_trajs=None)
            metric=head_module.to_real_corridor(pred["ego_cor_preds"],head.pc_range).detach().cpu().numpy()[0]
            proposals=pred["ego_fut_preds"][0].detach().cpu().numpy()
            if metric.shape!=(3,6,5) or not np.array_equal(metric[nav_index],rec["qp_corridor"]):
                raise ValueError("NATIVE_HEAD_CORRIDOR_BINDING")
            if proposals.shape!=(3,6,2) or not np.array_equal(proposals[nav_index],rec["qp_reference_steps"]):
                raise ValueError("NATIVE_HEAD_REFERENCE_BINDING")
            final=pred["mpc_trajs"][0,nav_index].cpu().numpy()
            rec["head_seconds"]=time.monotonic()-start
            rec["fallback"]=head.qp_fail_time>failed
            rec["verified_qp_output"]=(not rec["fallback"] and rec.get("solver_status")=="optimal" and
                np.array_equal(final,rec.get("qp_return_steps")) and bool(np.isfinite(final).all()))
            rec["reference_xy_lidar_m"]=proposals[nav_index].cumsum(0).tolist()
            rec["final_xy_lidar_m"]=final.cumsum(0).tolist()
            save(folder/"head_corridors.json",dict(token=row["token"],selected_index=nav_index,
                 corridors={b.lower():metric[i].tolist() for i,b in enumerate(BRANCHES)},
                 pre_qp_steps_lidar={b.lower():proposals[i].tolist() for i,b in enumerate(BRANCHES)}))
            visual=predictions(head,pred,cfg.class_names,cfg.map_classes)
            save(folder/"visual_map.json",dict(token=rec["token"],selected_index=nav_index,**visual))
            return rec


def main(args):
    args.out.mkdir(parents=True,exist_ok=True)
    feature_encoding=getattr(args,"feature_encoding","causal_physical_v1")
    if feature_encoding not in FEATURE_ENCODINGS:
        raise ValueError("Unsupported CorDriver input encoding: " + str(feature_encoding))
    registration=json.loads(args.registration.read_bytes())
    fields=("token","scene_token","timestamp","cams","lidar2ego_translation","lidar2ego_rotation",
            "ego2global_translation","ego2global_rotation")
    with base.INFOS.open("rb") as f:
        rows={s["token"]:{k:s[k] for k in fields} for s in pickle.load(f)["infos"]}
    if args.exposures:
        fixes=json.loads(args.exposures.read_bytes())
        for fix in fixes["patches"]:
            row=rows[fix["sample"]]
            channel=fix["channel"]
            assert row["cams"][channel]["sample_data_token"]==fix["original_token"]
            assert row["cams"][channel]["timestamp"]>row["timestamp"]>=fix["new_native_camera"]["timestamp"]
            camera=dict(fix["new_native_camera"])
            camera["sensor2lidar_rotation"]=np.asarray(camera["sensor2lidar_rotation"])
            camera["sensor2lidar_translation"]=np.asarray(camera["sensor2lidar_translation"])
            row["cams"][channel]=camera
    tables=args.data/"v1.0-trainval"
    scenes={r["token"]:r for r in json.loads((tables/"scene.json").read_bytes())}
    logs={r["token"]:r for r in json.loads((tables/"log.json").read_bytes())}
    model,config,cfg,transforms=prepare_model()
    report=dict(upstream_commit=subprocess.check_output(["git","-C",str(base.UPSTREAM),"rev-parse","HEAD"],text=True).strip(),
        qp_weight=float(model.pts_bbox_head.qp.weight.detach().cpu()),gpu=torch.cuda.get_device_name(0),
        weights_sha256=base.sha256(base.WEIGHTS),infos_sha256=base.sha256(base.INFOS),config_sha256=base.sha256(config),
        registration_sha256=base.sha256(args.registration),source_sha256=base.sha256(Path(__file__)),
        causal_camera_bindings_sha256=base.sha256(args.exposures) if args.exposures else None,
        feature_encoding=feature_encoding,feature_dependencies_sha256=base.sha256(Path(base.__file__)),samples=[],failures=[])
    for case in registration["cases"]:
        token=case["token"]
        folder=args.out/token
        folder.mkdir(exist_ok=False)
        row=rows[token]
        assert scenes[row["scene_token"]]["name"]==case["name"]
        timeline=[rows[t] for t in case["history"]]+[row]
        try:
            nav,audit=navigation(row,args.data/"can_bus"/(case["name"]+"_route.json"))
            save(folder/"navigation.json",dict(navigation=nav,alignment=audit))
            loc=logs[scenes[row["scene_token"]]["log_token"]]["location"]
            rec=inference(model,transforms,timeline,case,loc,args.data,folder,nav,cfg,feature_encoding)
            save(folder/"sample.json",rec)
            build(row,rec,nav,args.packets/token,args.data)
            report["samples"].append(rec)
            print(case["name"],nav["command"],rec.get("solver_status"),"packet ready",flush=True)
        except Exception as exc:
            import traceback
            failure=dict(token=token,scene=case["name"],reason=type(exc).__name__,detail=str(exc))
            save(folder/"error.json",dict(**failure,traceback=traceback.format_exc()))
            report["failures"].append(failure)
            print(failure,flush=True)
    save(args.out/"frontend.json",report)
