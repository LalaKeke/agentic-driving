"""Record both arms' shared inputs at the published CorDriver test.py entry point."""
import argparse
import gzip
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cordriver_paths import TOOLS
from cordriver_validate import sha256

UP = TOOLS / "cordriver"
CONFIG = UP / "projects/configs/VAD/Corridor_stage_2.py"
ASSETS = TOOLS / "cordriver_assets"
INFO = ASSETS / "corridor_nuscenes_infos_temporal_val.pkl"
WEIGHTS = ASSETS / "corridor_epoch_48_learned_weight_12.pth"


def capture(driver, out):
    import cvxpy as cp
    import torch
    from cordriver_nav_frontend import predictions
    from cordriver_edge_gate import intervention_v2

    original = driver.single_gpu_test
    def instrument(model, loader, *args):
        head = model.module.pts_bbox_head
        qp = head.qp
        dataset = loader.dataset
        rows = iter(dataset.data_infos)
        record = {}
        original_solve = cp.Problem.solve
        original_transform = qp.ego2lidar_traj
        def solver(problem, *args, **kwargs):
            try:
                return original_solve(problem, *args, **kwargs)
            finally:
                record["solver_status"] = problem.status

        def transform(rear):
            local = sys._getframe(1).f_locals
            states = (torch.bmm(local['BB'], local['sol_u'])
                      + torch.bmm(local['AA'], local['x0']) + local['gg']).reshape(12, 4)
            if not torch.equal(rear[0], states[1::2, :2]):
                raise RuntimeError("QP state binding failed")
            record["states"] = torch.cat([local['x0'].reshape(1, 4), states]).tolist()
            record["qp_controls_accel_steer"] = local["sol_u"].reshape(12, 2).tolist()
            return original_transform(rear)

        def qp_inputs(module, args):
            bus, features, ref, corridor = args[:4]
            record.update(qp_reference_steps=ref[0].tolist(), qp_corridor=corridor[0].tolist(),
                          can_bus=np.asarray(bus).reshape(-1).tolist(), ego_features=features.reshape(-1).tolist(),
                          previous_fail=head.qp_fail_time)

        def qp_output(module, args, result):
            record["qp_return_steps"] = result[0].tolist()

        def head_output(module, args, result):
            record["token"] = args[1][0]["sample_idx"]
            record["perception"] = predictions(head, result, dataset.CLASSES, dataset.MAPCLASSES)

        names = json.loads((Path("/data/nuscenes_dataset/v1.0-trainval/scene.json")).read_bytes())
        scene_names = {s['token']: s['name'] for s in names}
        def on_frame(module, args, outputs):
            row = next(rows)
            token = record["token"]
            if row["token"] != token or len(outputs) != 1:
                raise ValueError("Frame order mismatch")
            result = outputs[0]
            boxes = result["pts_bbox"]
            cmd = int(boxes["ego_fut_cmd"].argmax())
            ref = boxes["ego_fut_preds"][cmd]
            final = boxes["ego_fut_mpc"][cmd]
            if not np.array_equal(ref.numpy(), record["qp_reference_steps"]):
                raise ValueError("QP reference branch differs from evaluated branch")
            failed = head.qp_fail_time > record.pop("previous_fail")
            verified = not failed and record.get("solver_status") == "optimal" and np.array_equal(
                final.numpy(), record.get("qp_return_steps"))
            if not failed and "states" not in record:
                raise ValueError("Missing same-pass native QP state")
            states = record.pop("states", None)
            scene = scene_names[row["scene_token"]]
            state = dict(token=token, scene=scene, status="BOUND" if verified else "NO_VERIFIED_UPSTREAM_QP_RETURN")
            if verified:
                state.update(state_xy_yaw_speed=states, replay_max_xy_delta_m=0.0,
                             source="SAME_FORWARD_PRE_LIDAR_TRANSFORM")
            visual = record.pop("perception")
            visual.update(token=token)
            record.update(scene=row["scene_token"], scene_name=scene, timestamp_us=int(row["timestamp"]),
                          navigation_command_index=cmd, reference_xy_lidar_m=ref.cumsum(0).tolist(),
                          final_xy_lidar_m=final.cumsum(0).tolist(), fallback=failed, verified_qp_output=verified)
            # All frames go through the same routing rule. Only its inputs are visible here.
            gate = intervention_v2(record, visual, state)
            front = dict(sample=record, state=state, gate=gate, qp_weight=float(qp.weight.item()))
            if gate["consider_guidance"]:
                front["visual"] = visual
            path = out / "frontend" / (token + ".json.gz")
            with gzip.open(path.with_suffix(".partial"), "wt", compresslevel=1) as stream:
                json.dump(front, stream, allow_nan=False, separators=(",", ":"))
            os.replace(path.with_suffix(".partial"), path)
            # The release dataset.evaluate only consumes metric_results. Free dense predictions
            # after the decision inputs have been copied and scored by VAD.simple_test.
            metrics = result["metric_results"]
            evaluation.write(json.dumps(dict(token=token,fut_valid_flag=bool(metrics["fut_valid_flag"]),
                                             metrics={k:float(v) for k,v in metrics.items() if k.startswith("plan_")}),
                                        allow_nan=False) + "\n")
            result.pop("pts_bbox")
            record.clear()

        with (out / "evaluation" / "upstream.jsonl").open("x", buffering=1) as evaluation:
            with qp.register_forward_pre_hook(qp_inputs), qp.register_forward_hook(qp_output), \
                    head.register_forward_hook(head_output), model.register_forward_hook(on_frame), \
                    patch.object(qp, "ego2lidar_traj", transform), patch.object(cp.Problem, "solve", solver):
                return original(model, loader, *args)
    return instrument


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    out = a.out.resolve()
    out.mkdir(exist_ok=True, parents=True)
    (out / "frontend").mkdir(exist_ok=True)
    (out / "evaluation").mkdir(exist_ok=True)
    release = json.loads((Path(__file__).resolve().parents[1] / "environment/assets.json").read_bytes())["cordriver"]
    for path, digest in [(UP / k,v) for k,v in release["source_sha256"].items()] + [
            (p,release["assets"][p.name]["sha256"]) for p in (INFO, WEIGHTS)]:
        if sha256(path) != digest:
            raise ValueError(f"Release source/asset mismatch: {path}")
    sys.path.insert(0, str(UP))
    spec = importlib.util.spec_from_file_location("cordriver_official_test", UP / "tools/test.py")
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    sys.argv = [str(UP / "tools/test.py"), str(CONFIG), str(WEIGHTS), "--launcher", "none", "--eval", "bbox",
                "--seed", "0", "--deterministic", "--cfg-options", f"data.test.ann_file={INFO}",
                "data.workers_per_gpu=2"]
    os.chdir(TOOLS / "cordriver_runtime")
    with patch.object(driver, "single_gpu_test", capture(driver, out)):
        driver.main()
    (out / "official_complete.json").write_text(json.dumps({"exit":0,"entry":str(UP / "tools/test.py")}))


if __name__ == "__main__":
    main()
