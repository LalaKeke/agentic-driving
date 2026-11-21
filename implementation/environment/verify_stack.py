"""Check the actual project environment with stage-2 model construction and two GPU ops."""
import hashlib
import json
from importlib.metadata import version
from pathlib import Path

import torch
from mmcv import Config
from mmcv.ops import nms
from mmdet3d.models import build_model
from mmdet3d.ops.roiaware_pool3d import points_in_boxes_gpu
import mmdet3d
import projects.mmdet3d_plugin  # registers the upstream detector

config_path = Path("projects/configs/VAD/Corridor_stage_2.py")
cfg = Config.fromfile(str(config_path))
model = build_model(cfg.model, test_cfg=cfg.get("test_cfg"))
det = torch.tensor([[0., 0., 1., 1.]], device="cuda")
_, kept = nms(det, torch.ones(1, device="cuda"), 0.5)
points = torch.tensor([[[0., 0., 0.3], [4., 0., 0.3]]], device="cuda")
boxes = torch.tensor([[[0., 0., 0., 1., 1., 1., 0.]]], device="cuda")
hits = points_in_boxes_gpu(points, boxes)
assert kept.tolist() == [0] and hits.tolist() == [[0, -1]]
print(json.dumps({
    "model": type(model).__name__,
    "parameters": sum(p.numel() for p in model.parameters()),
    "stage2_config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
    "GPU": torch.cuda.get_device_name(),
    "torch_cuda": torch.version.cuda,
    "mmcv_gpu_nms_survivors": kept.tolist(),
    "mmdet3d_gpu_points_in_boxes": hits.tolist(),
    "mmdet3d": mmdet3d.__version__,
    "packages": {k: version(k) for k in ("torch", "torchvision", "mmcv-full", "mmdet",
            "mmsegmentation", "numpy", "scipy", "nuscenes-devkit", "cvxpy", "osqp",
            "Shapely", "numba", "llvmlite", "similaritymeasures")},
}, indent=2))
