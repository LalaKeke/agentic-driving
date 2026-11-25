"""Replay saved target boxes against the original staged planner and the verified-control variant."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from cordriver_reachable_options import load, save, sha
from cordriver_smooth_planner import search_world


def run_pair(args):
    root, data, case, mode, floor_only = args
    t = case["token"]
    study = root / "study"
    sample = load(study / "frontend" / t / "sample.json")
    weight = load(study / "frontend/frontend.json")["qp_weight"]
    archive = mode in ("archive", "atlas", "minimal")
    work = root / ("fast" if archive else "replica_menus") / t
    output = root / ("window_floor" if floor_only else "window_comparison") / t
    if mode == "witness-extra":
        output = root / "window_remaining" / t / case["key"]
    elif mode == "atlas":
        output = root / "window_atlas" / t
    elif mode == "minimal":
        output = root / "window_minimal" / t
    output.mkdir(parents=True, exist_ok=False)
    if archive:
        global_archive = Path(__file__).resolve().parents[1] / "evaluation/dispatch_inputs.json"
        (frame,) = [f for f in load(global_archive)["frames"] if f["token"] == t]
        raw = frame["slow"]["guidance"]
        # The original file's ordering can differ after archival canonicalization.
        canonical_hash = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        from cordriver_edge_gate import native_body_knots

        (st,) = [i for i in load(study / "native_states.json")["samples"] if i["token"] == t]
        poses, _ = native_body_knots(sample, st)
        matched = poses is not None and np.allclose(
            poses, frame["native_halfsecond_xy_yaw"], atol=0.0005, rtol=0
        )
        provenance = dict(
            guidance=raw,
            archive_sha256=sha(global_archive),
            archived_native_output_sha256=frame["slow"]["source_sha256"],
            canonical_guidance_sha256=canonical_hash,
            bound_native_poses=bool(matched),
            native_pose_max_abs_delta=float(np.max(np.abs(poses - frame["native_halfsecond_xy_yaw"])))
            if poses is not None
            else None,
        )
        save(output / "task.json", provenance)
        if not matched:
            return dict(token=t, scene=case["name"], error="ARCHIVED_NATIVE_POSES_DIFFER")
        exclude = None
    else:
        tasks = load(root / "witness_replication/tasks.json")["tasks"]
        task = next(r for r in tasks if r["token"] == t and ("key" not in case or r["key"] == case["key"]))
        raw = task["guidance"]
        exclude = task["donor_sha256"]
        save(output / "task.json", dict(guidance=raw, exclude=exclude, key=task["key"]))
    rows = {}
    if not (work / "inputs" / "frontend" / t / "sample.json").exists():
        return dict(token=t, scene=case["name"], error="BASELINE_SEARCH_INPUTS_UNAVAILABLE")
    arms = (
        ("original", "floor_aware")
        if mode == "witness-extra"
        else (("floor_aware",) if floor_only else ("original", "verified_seed"))
    )
    if mode == "atlas":
        arms = ("atlas",)
    elif mode == "minimal":
        arms = ("minimal",)
    for arm in arms:
        folder = output / arm
        folder.mkdir()
        rec = dict(
            scene=case["name"],
            token=t,
            selected_path=None,
            selected_method=None,
            selected_task=None,
            candidates=[],
            eligible=False,
            reasons=[],
            control_solves=0,
            fitted_seed_solves=0,
        )
        start = time.monotonic()
        menu = work / "menus" / t / "portfolio.json" if arm != "original" else None
        search_world(
            work / "inputs",
            folder,
            rec,
            data,
            raw,
            weight,
            portfolio=menu,
            exclude_sha=exclude,
            floor_aware=arm in ("floor_aware", "atlas", "minimal"),
            dual_chart=arm == "atlas",
            finite_atlas=arm == "atlas",
            minimum_change_base=work / "return.json" if arm == "minimal" else None,
        )
        rec["seconds"] = time.monotonic() - start
        save(folder / "result.json", rec)
        rows[arm] = {
            k: rec.get(k)
            for k in (
                "status",
                "eligible",
                "selected_task",
                "selected_method",
                "selected_sha256",
                "selected_path",
                "control_solves",
                "reasons",
                "seconds",
            )
        }
    print(case["name"], t, [(k, v["selected_task"]) for k, v in rows.items()], flush=True)
    return dict(token=t, scene=case["name"], log=case["log_token"], key=case.get("key"), arms=rows)


def score_archive(root, data, include_minimal=False):
    """Open dataset labels after binding both search experiments and their fallback returns."""
    from cordriver_published_metrics import Published, rescore
    from cordriver_published_xy import published_xy
    from cordriver_pipeline import checked_return

    published = Published(data)

    def indexed(folder):
        return {r["token"]: r for r in load(root / folder / "summary.json")}

    if include_minimal:
        studies = {"minimal": indexed("window_minimal")}
    else:
        legacy = indexed("window_comparison")
        floor = indexed("window_floor")
        atlas = {**floor, **indexed("window_atlas")}
        studies = dict(original=legacy, verified_seed=legacy, floor_aware=floor, atlas=atlas)
    cases = load(root / "study/selection/registration.json")["cases"]
    rows = []
    for case in cases:
        t = case["token"]
        row = dict(token=t, scene=case["name"], log=case["log_token"])
        rows.append(row)
        missing = [table.get(t, {}) for table in studies.values() if not table.get(t, {}).get("arms")]
        if missing:
            row["status"] = missing[0].get("error", "NO_BOUND_REPLAY")
            continue
        world = published.inputs(t)
        if world is None:
            row["status"] = "MISSING_EVALUATION_FUTURE"
            continue
        native, fast = checked_return(root / "study", t, root / "fast" / t / "return.json")
        row.update(
            status="SCORED", native=rescore(published, world, native), fast=rescore(published, world, fast)
        )
        for arm, table in studies.items():
            records = table[t]["arms"]
            record = records["floor_aware"] if arm == "atlas" and arm not in records else records[arm]
            xy = fast
            if record["eligible"]:
                path = Path(record["selected_path"])
                receipt = load(path.with_name("receipt.json"))
                if (
                    path.parent.parent.parent.name != t
                    or sha(path) != record["selected_sha256"]
                    or receipt["sha256"] != sha(path)
                    or not receipt["eligible_before_fixed_clearance"]
                ):
                    raise ValueError("UNBOUND_ARCHIVE_CANDIDATE")
                xy = published_xy(path).tolist()
            row[arm] = rescore(published, world, xy)
        print(case["name"], t, row["status"], flush=True)
    save(root / ("minimal_scores.json" if include_minimal else "archive_scores.json"), rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mode",
        choices=("witness", "witness-extra", "archive", "atlas", "minimal", "score-archive", "score-minimal"),
        required=True,
    )
    p.add_argument("--floor-only", action="store_true")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--workers", type=int, default=3)
    a = p.parse_args()
    root = a.root.resolve()
    if a.mode in ("score-archive", "score-minimal"):
        score_archive(root, a.data.resolve(), a.mode == "score-minimal")
        return
    cases = load(root / "study/selection/registration.json")["cases"]
    present = {r["token"] for r in load(root / "study/frontend/frontend.json")["samples"]}
    if a.mode == "witness":
        present = {r["token"] for r in load(root / "witness_replication/tasks.json")["tasks"]}
    elif a.mode == "atlas":
        registration = load(root / "road_support_registration.json")
        if registration["parent_summary_sha256"] != sha(root / "window_floor/summary.json"):
            raise ValueError("ATLAS_REGISTRATION_SOURCE_CHANGED")
        present = set(registration["tokens"])
    cases = [c for c in cases if c["token"] in present]
    if a.mode == "witness-extra":
        tasks = load(root / "witness_replication/tasks.json")["tasks"]
        cases = [
            dict(c, key=k) for c in cases for k in [t["key"] for t in tasks if t["token"] == c["token"]][1:]
        ]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        results = list(pool.map(run_pair, [(root, a.data.resolve(), c, a.mode, a.floor_only) for c in cases]))
    folder = {"witness-extra": "window_remaining", "atlas": "window_atlas", "minimal": "window_minimal"}.get(
        a.mode, "window_floor" if a.floor_only else "window_comparison"
    )
    save(root / folder / "summary.json", results)


if __name__ == "__main__":
    main()
