"""Independent, upstream CorDriver ACR, CCR and prefix L2 evaluation."""
import sys
from pathlib import Path
from types import SimpleNamespace
import mmcv
import numpy as np
import torch
from mmdet3d.datasets import build_dataset
from cordriver_paths import TOOLS, load_asset_manifest
from cordriver_astra_packet import NATIVE_R, NATIVE_T
from cordriver_reachable_options import sha
UPSTREAM=TOOLS/"cordriver"
sys.path.insert(0,str(UPSTREAM))
from projects.mmdet3d_plugin.VAD.VAD import VAD
from projects.mmdet3d_plugin.VAD.planner.metric_bp import PlanningMetric
from projects.mmdet3d_plugin.datasets.pipelines.transform_3d import CustomObjectRangeFilter, CustomObjectNameFilter
KEYS={"ACR":"plan_obj_box_col","CCR":"plan_obj_box_map_intersect"}
HORIZONS=(1,2,3)

class Published:
    def __init__(self, data):
        self.config = UPSTREAM/'projects/configs/VAD/Corridor_stage_2.py'
        self.pkl = TOOLS/'cordriver_assets/corridor_nuscenes_infos_temporal_val.pkl'
        release = load_asset_manifest()["cordriver"]
        files = {"upstream_head_sha": UPSTREAM / "projects/mmdet3d_plugin/VAD/VAD.py",
                 "metric_sha": UPSTREAM / "projects/mmdet3d_plugin/VAD/planner/metric_bp.py",
                 "upstream_dataset_sha": UPSTREAM / "projects/mmdet3d_plugin/datasets/nuscenes_vad_dataset.py",
                 "object_filter_sha": UPSTREAM / "projects/mmdet3d_plugin/datasets/pipelines/transform_3d.py",
                 "config_sha": self.config}
        self.scope = {}
        for key, path in files.items():
            self.scope[key] = sha(path)
            if self.scope[key] != release["source_sha256"][str(path.relative_to(UPSTREAM))]:
                raise ValueError(f"CorDriver release evaluator mismatch: {path}")
        self.scope["validation_infos_sha"] = sha(self.pkl)
        if self.scope["validation_infos_sha"] != release["assets"][self.pkl.name]["sha256"]:
            raise ValueError(f"CorDriver release validation index mismatch: {self.pkl}")
        cfg=mmcv.Config.fromfile(str(self.config))
        spec=cfg.data.test.copy()
        spec.pop("samples_per_gpu",None)
        spec.update(ann_file=str(self.pkl),data_root=str(data),test_mode=True)
        self.dataset=build_dataset(spec)
        self.index={r["token"]:i for i,r in enumerate(self.dataset.data_infos)}
        self.metric=SimpleNamespace(planning_metric=PlanningMetric())
        self.rf=CustomObjectRangeFilter(cfg.point_cloud_range)
        self.lf=CustomObjectNameFilter(cfg.class_names)

    def inputs(self,token):
        i=self.index[token]
        d=self.dataset.data_infos[i]
        if not bool(d["fut_valid_flag"]):
            return None
        labels=self.lf(self.rf(self.dataset.get_ann_info(i)))
        maps=self.dataset.vectormap_pipeline({},d)
        gt=torch.tensor(d["gt_ego_fut_trajs"],dtype=torch.float32).cumsum(0)[None]
        return dict(gt_ego_fut_trajs=gt,gt_agent_boxes=labels["gt_bboxes_3d"],
                    gt_agent_feats=torch.from_numpy(labels["gt_attr_labels"])[None],
                    gt_map_bbox=maps["map_gt_bboxes_3d"].data,gt_map_label=maps["map_gt_labels_3d"].data,
                    fut_valid_flag=True)

    def evaluate(self,xy,inputs):
        if inputs is None:
            return {}
        if np.asarray(xy).shape!=(6,2) or not np.isfinite(xy).all():
            raise ValueError("Bad trajectory coordinates")
        pred=torch.tensor(xy,dtype=torch.float32)[None]
        # The upstream CPU rasterizer converts box yaw in-place through a NumPy view.
        inputs=dict(inputs,gt_agent_boxes=inputs["gt_agent_boxes"].clone())
        with torch.no_grad():
            metric=VAD.compute_planner_metric_stp3(self.metric,pred_ego_fut_trajs=pred,
                    ref_ego_trajs=pred,**inputs)
        return {f"{m}_{t}s":int(metric[f"{field}_{t}s"]) for m,field in KEYS.items() for t in HORIZONS}

def add_union(scores):
    for h in HORIZONS:
        scores[f'ANY_{h}s'] = max(scores[f'ACR_{h}s'], scores[f'CCR_{h}s'])
    return scores

def rescore(published, inputs, xy):
    values = published.evaluate(xy, inputs)
    pred = torch.tensor(xy, dtype=torch.float32)
    gt = inputs['gt_ego_fut_trajs'][0]
    measure = published.metric.planning_metric.compute_L2
    for h in HORIZONS:
        values[f'L2_{h}s'] = measure(pred[:2*h], gt[:2*h])
    return add_union(values)
