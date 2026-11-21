"""Evaluate an image-only CorDriver head with past CAN and assigned navigation on registered samples."""
from bisect import bisect_right
import json
import math
from pathlib import Path
from cordriver_paths import TOOLS, CHECK_PYTHON, CONFIG

import mmcv
import numpy as np
from pyquaternion import Quaternion
import torch
from mmdet.apis import set_random_seed
from mmdet3d.models import build_model
from cordriver_validate import data_path, sha256

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = TOOLS / "cordriver"
ASSETS = TOOLS / "cordriver_assets"
INFOS = ASSETS / "corridor_nuscenes_infos_temporal_val.pkl"
WEIGHTS = ASSETS / "corridor_epoch_48_learned_weight_12.pth"


def yaw(row):
    rot = Quaternion(row["ego2global_rotation"]).rotation_matrix
    return math.atan2(rot[1, 0], rot[0, 0])


def read_messages(path):
    records = json.loads(path.read_text())
    times = [r["utime"] for r in records]
    if times != sorted(times):
        raise ValueError("Out-of-order CAN stream")
    return times, records


def latest(stream, timestamp):
    i = bisect_right(stream[0], timestamp) - 1
    return stream[1][i] if i >= 0 else None


def bus_vector(row, message, previous_bev_row):
    angle = math.degrees(yaw(row)) % 360.
    position_delta = np.zeros(3)
    angle_delta = 0.
    if previous_bev_row is not None:
        position_delta = np.subtract(row['ego2global_translation'], previous_bev_row['ego2global_translation'])
        angle_delta = angle - math.degrees(yaw(previous_bev_row)) % 360.
    return np.r_[position_delta, row['ego2global_rotation'], message['accel'],
                 message['rotation_rate'], message['vel'], math.radians(angle), angle_delta]


def ego_features(row, previous, message, steer, location):
    dt = (row["timestamp"] - previous["timestamp"]) * 1e-6
    direction = yaw(row)
    speed = np.linalg.norm(np.subtract(row["ego2global_translation"][:2], previous["ego2global_translation"][:2])) / dt
    angular = math.atan2(math.sin(direction-yaw(previous)), math.cos(direction-yaw(previous))) / dt
    steer_value = -steer["value"] if location.startswith("singapore") else steer["value"]
    return [speed*math.cos(direction+math.pi/2), speed*math.sin(direction+math.pi/2),
            *message["accel"][:2], angular, 4.084, 1.85, message["vel"][0], 2*steer_value/2.588]


def images(row, dataroot, transforms):
    batch = {"img": [], "lidar2img": []}
    sources = {}
    for channel, camera in row["cams"].items():
        if camera["timestamp"] > row["timestamp"]:
            raise ValueError("Exposure after reference")
        path = Path(data_path(camera["data_path"], dataroot))
        sources[channel] = {"file": str(path), "sha256": sha256(path),
                            "timestamp_us": int(camera["timestamp"])}
        batch["img"].append(mmcv.imread(str(path)).astype(np.float32))
        basis = np.linalg.inv(camera["sensor2lidar_rotation"])
        transform = np.eye(4)
        transform[:3, :3] = basis
        transform[:3, 3] = -basis @ camera["sensor2lidar_translation"]
        intrinsic = np.eye(4)
        intrinsic[:3, :3] = camera["cam_intrinsic"]
        batch["lidar2img"].append(intrinsic @ transform)
    for transform in transforms:
        batch = transform(batch)
    imgs = torch.from_numpy(np.stack(batch.pop("img")).transpose(0,3,1,2).copy()).cuda().unsqueeze(0)
    return imgs, batch, sources
