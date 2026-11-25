"""Offline frame eligibility under the published CorDriver full-body collision metrics."""
import argparse
import csv
import json
import sys
from math import isfinite
from pathlib import Path

import numpy as np
import torch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "implementation/experiments"))
from cordriver_published_metrics import Published
from cordriver_reachable_options import sha

HORIZONS = (1, 2, 3)
GT_TYPES = {"ACR": ("ACR",), "CCR": ("CCR",), "ANY": ("ACR", "CCR"), "L2": ()}


def metric_status(gt, metric, horizon):
    """Eligibility is shared across methods, specific to the metric and its prefix."""
    if gt["status"] == "INCOMPLETE_GT":
        return "INCOMPLETE_GT"
    for kind in GT_TYPES[metric]:
        if any(t <= horizon for t in gt[f"gt_{kind}_hit_s"]):
            return "EXCLUDE_GT_HIT"
    return "KEEP"


def summarize(rows, fields, statuses):
    """Aggregate frozen wide-format score columns; missing predictions are counted separately."""
    summary = []
    for horizon in HORIZONS:
        for metric in GT_TYPES:
            suffix = f"_{metric}_{horizon}s"
            for field in fields:
                if not field.endswith(suffix):
                    continue
                scored = [row for row in rows if row[field] not in (None, "")]
                kept = [row for row in scored if metric_status(statuses[row["token"]], metric, horizon) == "KEEP"]
                values = [float(row[field]) for row in kept]
                if not all(isfinite(v) and (v >= 0 if metric == "L2" else v in (0, 1)) for v in values):
                    raise ValueError(f"Invalid score in {field}")
                incomplete = sum(statuses[r["token"]]["status"] == "INCOMPLETE_GT" for r in scored)
                summary.append(dict(method=field[:-len(suffix)], metric=metric, horizon_s=horizon,
                                    n_input=len(rows), n_unscored=len(rows)-len(scored),
                                    n_incomplete_gt_scored=incomplete, n_gt_hit_scored=len(scored)-incomplete-len(kept),
                                    denominator=len(kept), hits=int(sum(values)) if metric != "L2" and values else None,
                                    mean=sum(values)/len(values) if values else None))
    return summary


def recorded_ego_status(evaluator, token):
    """Apply the same six times and two rasters to recorded ego alone, before prediction masking."""
    info = evaluator.dataset.data_infos[evaluator.index[token]]
    valid = np.asarray(info["gt_ego_fut_masks"]).reshape(-1)
    if not info["fut_valid_flag"] or valid.shape != (6,) or not np.all(valid == 1):
        return dict(status="INCOMPLETE_GT", gt_ACR_hit_s=None, gt_CCR_hit_s=None)
    inputs = evaluator.inputs(token)
    gt = inputs["gt_ego_fut_trajs"][0]
    if gt.shape != (6, 2) or not torch.isfinite(gt).all():
        return dict(status="INCOMPLETE_GT", gt_ACR_hit_s=None, gt_CCR_hit_s=None)

    metric = evaluator.metric.planning_metric
    with torch.no_grad():
        vehicles, pedestrians = metric.get_label(inputs["gt_agent_boxes"].clone(), inputs["gt_agent_feats"])
        roads = metric.get_map_label(vehicles, inputs["gt_map_bbox"], inputs["gt_map_label"])
        def hit_times(raster):
            flags = metric.evaluate_single_coll(gt, raster[0], input_gt=True)
            return [(i + 1) / 2 for i, hit in enumerate(flags.tolist()) if hit]
        acr = hit_times(torch.logical_or(vehicles, pedestrians))
        ccr = hit_times(roads)

    return dict(status="EXCLUDE_GT_HIT" if acr or ccr else "KEEP", gt_ACR_hit_s=acr, gt_CCR_hit_s=ccr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", required=True, type=Path, help="Frozen CSV with unique nuScenes sample tokens")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--data", default=Path("/data/nuscenes_dataset"), type=Path)
    parser.add_argument("--retain", choices=tuple(GT_TYPES), help="Also export retained.csv for this metric only")
    parser.add_argument("--horizon", type=int, choices=HORIZONS, default=3, help="Horizon for --retain")
    args = parser.parse_args()
    with args.cohort.open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        rows = list(reader)
    if len(set(row["token"] for row in rows)) != len(rows):
        raise ValueError("Duplicate sample tokens in the input cohort")
    if args.out.exists():
        raise FileExistsError(args.out)
    evaluator = Published(args.data)
    statuses = {row["token"]: recorded_ego_status(evaluator, row["token"]) for row in rows}
    args.out.mkdir(parents=True)
    if args.retain:
        with (args.out / "retained.csv").open("x", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(row for row in rows
                             if metric_status(statuses[row["token"]], args.retain, args.horizon) == "KEEP")
    with (args.out / "frames.csv").open("x", newline="") as stream:
        identifiers = [name for name in ("scene", "frame_idx", "frame", "token") if name in fields]
        labels = [f"{m}_{h}s" for h in HORIZONS for m in GT_TYPES]
        writer = csv.DictWriter(stream, fieldnames=identifiers + ["gt_ACR_hit_s", "gt_CCR_hit_s"] + labels)
        writer.writeheader()
        for row in rows:
            result = statuses[row["token"]]
            writer.writerow({**{key: row[key] for key in identifiers},
                             **{f"{m}_{h}s": metric_status(result, m, h) for h in HORIZONS for m in GT_TYPES},
                             **{k: json.dumps(result[k]) for k in ("gt_ACR_hit_s", "gt_CCR_hit_s")}})
    summary = summarize(rows, fields, statuses)
    if summary:
        with (args.out / "metrics.csv").open("x", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
            writer.writeheader()
            writer.writerows(summary)
    manifest = dict(input_sha256=sha(args.cohort), evaluator=evaluator.scope, filter_sha256=sha(Path(__file__)),
                    sample_times_s=[.5, 1, 1.5, 2, 2.5, 3], exclude_by_metric=GT_TYPES,
                    export_metric=args.retain, export_horizon_s=args.horizon if args.retain else None,
                    counts={f"{m}_{h}s": sum(metric_status(r, m, h) == "KEEP" for r in statuses.values())
                            for h in HORIZONS for m in GT_TYPES})
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(manifest["counts"])


if __name__ == "__main__":
    main()
