"""Resolve camera exposures later than a fixed LiDAR reference from the actual sensor chain."""
import json
import pickle

import numpy as np

from cordriver_measurements import stream_array


def transform(rotation, translation):
    result=np.eye(4)
    quaternion = np.asarray(rotation, dtype=float)
    if quaternion.shape != (4,) or not np.isfinite(quaternion).all() or abs(np.linalg.norm(quaternion) - 1) >= .001:
        raise ValueError("Invalid nuScenes wxyz quaternion")
    w,x,y,z = quaternion / np.linalg.norm(quaternion)
    result[:3,:3] = [[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                     [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                     [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]]
    result[:3,3]=translation
    return result


def load_tokens(path,tokens):
    found={}
    for row in stream_array(path):
        if row["token"] in tokens:
            found[row["token"]]=row
            if len(found)==len(tokens): break
    if found.keys()!=tokens:
        raise ValueError(f"Missing records in {path.name}: {tokens-found.keys()}")
    return found


def main(reg_path,infos,data,out, *, allow_partial=False):
    cases=json.loads(reg_path.read_bytes())["cases"]
    tokens={t for c in cases for t in c["history"]+[c["token"]]}
    with infos.open("rb") as f:
        rows={r["token"]:r for r in pickle.load(f)["infos"] if r["token"] in tokens}
    assert rows.keys()==tokens
    fixes=[(r,c,channel) for r in rows.values() for channel,c in r["cams"].items() if c["timestamp"]>r["timestamp"]]
    db=data/"v1.0-trainval"
    sd={}
    unavailable={}
    missing={c['sample_data_token'] for _,c,_ in fixes}
    while missing:
        sd.update(load_tokens(db/"sample_data.json",missing))
        missing=set()
        for r,c,_ in fixes:
            row=sd[c['sample_data_token']]
            while row["timestamp"]>r["timestamp"]:
                if not row["prev"]:
                    if not allow_partial:
                        raise ValueError("NO_PREVIOUS_CAMERA_EXPOSURE")
                    unavailable.setdefault(r["token"],{})[c["type"]]="NO_PREVIOUS_CAMERA_EXPOSURE"
                    break
                if row["prev"] not in sd:
                    missing.add(row["prev"])
                    break
                previous=sd[row["prev"]]
                assert previous["next"]==row["token"] and previous["timestamp"]<row["timestamp"]
                row=previous
    pose=load_tokens(db/"ego_pose.json",{r['ego_pose_token'] for r in sd.values()}) if sd else {}
    cals={r["token"]:r for r in json.loads((db/"calibrated_sensor.json").read_bytes())}
    sensors={r["token"]:r for r in json.loads((db/"sensor.json").read_bytes())}
    patches=[]
    for row,c,channel in fixes:
        if row["token"] in unavailable:
            continue
        orig=sd[c["sample_data_token"]]
        picked=orig
        chain=[orig["token"]]
        while picked["timestamp"]>row["timestamp"]:
            picked=sd[picked["prev"]]
            chain.append(picked["token"])
        calib=cals[picked["calibrated_sensor_token"]]
        assert sensors[calib["sensor_token"]]["channel"]==channel and (data/picked["filename"]).is_file()
        exposure=pose[picked["ego_pose_token"]]
        assert exposure["timestamp"]==picked["timestamp"]
        world_ref=transform(row["ego2global_rotation"],row["ego2global_translation"])
        ref_lidar=transform(row["lidar2ego_rotation"],row["lidar2ego_translation"])
        world_cam=transform(exposure["rotation"],exposure["translation"]) @ transform(calib["rotation"],calib["translation"])
        lidar_cam=np.linalg.solve(world_ref @ ref_lidar,world_cam)
        native=dict(data_path=picked["filename"],type=channel,sample_data_token=picked["token"],
            sensor2ego_translation=calib["translation"],sensor2ego_rotation=calib["rotation"],
            ego2global_translation=exposure["translation"],ego2global_rotation=exposure["rotation"],
            timestamp=picked["timestamp"],sensor2lidar_rotation=lidar_cam[:3,:3].tolist(),
            sensor2lidar_translation=lidar_cam[:3,3].tolist(),cam_intrinsic=calib["camera_intrinsic"])
        patches.append(dict(sample=row["token"],channel=channel,reference_us=row["timestamp"],
             original_token=orig["token"],original_timestamp_us=orig["timestamp"],chain=chain,
             new_delta_us=picked["timestamp"]-row["timestamp"],new_ego_pose_token=picked["ego_pose_token"],
             new_calibration_token=picked["calibrated_sensor_token"],new_native_camera=native))
    with out.open("x") as f:
        json.dump(dict(rule="Follow same physical camera prev pointers to nearest exposure at or before original LiDAR reference. Recalculate camera-to-reference-LiDAR SE3 with its own exposure ego pose and calibration.",input_samples=len(rows),patches=patches,**({"unavailable_samples":unavailable} if unavailable else {})),f,indent=2,allow_nan=False)
        f.write("\n")
    print(len(rows),"sensor samples,",len(patches),"late camera bindings replaced")
