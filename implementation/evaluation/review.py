"""Review frozen CorDriver open-loop scores or render a front-camera example."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent


def read_table(name):
    with (HERE / name).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def select_fast(frame):
    secondary = frame["fast_mode"] == "secondary"
    native = frame["native_halfsecond_xy_yaw"]
    options = []
    for option in frame["fast_candidates"]:
        if not all(option["checks"].values()) or (secondary and option["primary_edge_triggered"]):
            continue
        clearance, still_triggered = option["edge"]
        if clearance < frame["fast_native_edge"] + 0.10:
            continue
        deviations = [(xy[0] - x, xy[1] - y, yaw)
                      for xy, (x, y, yaw) in zip(option["xy_m"][2::2], native[1:])]
        cost = sum(dx * dx + dy * dy for dx, dy, _ in deviations)
        if len(deviations) != 6 or not math.isclose(cost, option["deviation_m2"], abs_tol=1e-6):
            raise ValueError(f"Fast candidate is bound to another native path: {option['id']}")
        rank = (still_triggered, -clearance if still_triggered else cost, cost, option["method"])
        options.append((rank, option, deviations))
    if not options:
        return None
    _, best, deviations = min(options, key=lambda item: item[0])
    if secondary:
        change = max(abs(dx * math.cos(yaw) + dy * math.sin(yaw)) for dx, dy, yaw in deviations)
        if change > 1.85 / 2:
            return None
    return best


def select_slow(slow):
    decision = slow["guidance"]["decision"]
    if decision == "KEEP":
        return None
    if decision != "ADJUST":
        raise ValueError(f"Unknown decision {decision}")
    for option in slow["candidates"]:
        if not (option["physical_environment"] and option["motion_ok"] and option["smooth_margin"] >= -0.001):
            continue
        if option["quality"] == "APPROXIMATE" and (
            option["method"] not in ("global_smooth_closest", "global_body_refinement") or
            option["window_excess_m"] > 1.001
        ):
            continue
        if all(floor is None or (distance is not None and distance + 0.001 >= floor)
               for distance, floor in zip(option["clearance_m"], slow["clearance_floor_m"])):
            return option
    return None


def replay_dispatch(out):
    inputs = json.loads((HERE / "dispatch_inputs.json").read_text())
    selected, paths = {}, {}
    fired = 0
    for frame in inputs["frames"]:
        token = frame["token"]
        triggered = (frame["qp_fallback"] or frame["predicted_overlap_s"] is not None or
                     frame["normal_accel_mps2"] > 3.0)
        if token in selected or triggered != ("slow" in frame):
            raise ValueError(f"Unexpected routing or duplicate token: {token}")
        fast = select_fast(frame)
        dual = select_slow(frame["slow"]) if triggered else fast
        fired += int(triggered)
        selected[token], paths[token] = {}, {}
        for arm, item in (("fast", fast), ("literal_keep", dual)):
            selected[token][arm] = {k: item[k] if item else None for k in ("id", "receipt")}
            xy = item["xy_m"] if item else [p[:2] for p in frame["native_halfsecond_xy_yaw"]]
            paths[token][arm] = {
                "source": item["method"] if item else "native",
                "candidate_sha256": selected[token][arm]["id"],
                "receipt_sha256": selected[token][arm]["receipt"],
                "time_s": [i * (0.25 if item else 0.5) for i in range(len(xy))], "body_xy_m": xy,
            }
            encoded = json.dumps([paths[token][arm]["time_s"], xy], separators=(",", ":"), allow_nan=False)
            selected[token][arm]["track_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    # Expected selections are evaluation-side and opened only after every return is fixed.
    expected = json.loads((HERE / "dispatch_expected.json").read_text())["frames"]
    if selected != expected:
        mismatches = sorted(k for k in set(selected) | set(expected) if selected.get(k) != expected.get(k))
        raise ValueError(f"Selection or receipt mismatch at {len(mismatches)} tokens: {mismatches}")
    if out:
        with out.open("x", encoding="utf-8") as stream:
            json.dump(paths, stream, allow_nan=False)
            stream.write("\n")
    return {"frames": len(selected), "astra_triggered": fired, "matched_selections": 2 * len(selected),
            "fast_adopted": sum(bool(r["fast"]["id"]) for r in selected.values()),
            "dual_adopted": sum(bool(r["literal_keep"]["id"]) for r in selected.values())}


def method_scores(rows, method):
    results = {"frames": len(rows)}
    for h in (1, 2, 3):
        totals = {kind: 0 for kind in ("ACR", "CCR", "ANY")}
        errors = []
        for row in rows:
            flags = {kind: int(row[f"{method}_{kind}_{h}s"]) for kind in totals}
            if set(flags.values()) - {0, 1} or flags["ANY"] != max(flags["ACR"], flags["CCR"]):
                raise ValueError(f"Inconsistent collision flags: {row['token']}/{method}/{h}s")
            for kind in totals:
                totals[kind] += flags[kind]
            error = float(row[f"{method}_L2_{h}s"])
            if not math.isfinite(error) or error < 0:
                raise ValueError(f"Invalid L2: {row['token']}/{method}/{h}s")
            errors.append(error)
        results[f"{h}s"] = {**totals, "mean_L2_m": mean(errors)}
    results["mean_of_three_L2_m"] = mean(results[f"{h}s"]["mean_L2_m"] for h in (1, 2, 3))
    return results


def score_report():
    rows = read_table("per_frame.csv")
    screen = read_table("screen.csv")
    positives = {(r["scene"], int(i)) for r in screen for i in r["positive_frame_indices"].split()}
    if positives != {(r["scene"], int(r["frame_idx"])) for r in rows}:
        raise ValueError("The frame list differs from the published scene-level screen")
    valid_frames = sum(int(r["valid_frames"]) for r in screen)
    if len({row["token"] for row in rows}) != len(rows):
        raise ValueError("Duplicate sample token")
    paired = [r for r in rows if r["causal_status"] == "AVAILABLE"]
    missing = [r for r in rows if r["causal_status"] == "UNAVAILABLE"]
    if len(paired) + len(missing) != len(rows):
        raise ValueError("Unknown input availability")
    if any(r["official_ANY_3s"] != "1" for r in rows):
        raise ValueError("Unexpected negative in release-positive cohort")
    for r in missing:
        if not r["unavailable_reason"] or any(
            value for key, value in r.items() if key.startswith(("dual_", "fast_", "native_")) and
            any(kind in key for kind in ("_ACR_", "_CCR_", "_L2_", "_ANY_"))
        ):
            raise ValueError(f"Missing-data contract violated for {r['token']}")
    transitions = {}
    for comparator in ("native", "fast"):
        before = f"{comparator}_ANY_3s"
        transitions[comparator] = {
            "cleared": sum(r[before] == "1" and r["dual_ANY_3s"] == "0" for r in paired),
            "introduced": sum(r[before] == "0" and r["dual_ANY_3s"] == "1" for r in paired),
        }
    judgments = read_table("judgments.csv")
    selected_tokens = {r["token"] for r in paired if r["astra_decision"]}
    if len({r["token"] for r in judgments}) != len(judgments) or any(
        r["token"] not in selected_tokens for r in judgments
    ):
        raise ValueError("History-ablation tokens do not match the triggered cohort")
    judgment_counts = {}
    for r in judgments:
        collision = "hit" if max(int(r["shown_ACR"]), int(r["shown_CCR"])) else "clear"
        key = f"{collision}_{r['decision']}"
        judgment_counts[key] = judgment_counts.get(key, 0) + 1
    return {
        "valid_frames_in_published_screen": valid_frames,
        "published_collision_subset": method_scores(rows, "official"),
        "paired_subset": {name: method_scores(paired, name) for name in ("official", "native", "fast", "dual")},
        "missing_causal_inputs": len(missing),
        "paired_real_astra_decisions": len(selected_tokens),
        "paired_dual_3s_ANY_transitions": transitions,
        "separate_history_ablation_decisions_on_shown_path": judgment_counts,
    }


def render_front(case, dataroot, out):
    from PIL import Image, ImageDraw, ImageFont

    examples = json.loads((HERE / "front_examples.json").read_text())
    indexing = {entry["scene_frame"]: entry for entry in examples}
    if case not in indexing:
        raise ValueError(f"Choose from {', '.join(indexing)}")
    entry = indexing[case]
    meta = entry["image"]
    path = dataroot / meta["relative_path"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
        raise ValueError(f"Front RGB digest mismatch for {case}")
    with Image.open(path) as source:
        if source.size != (meta["width"], meta["height"]):
            raise ValueError("Camera image dimensions changed")
        photo = source.convert("RGB")
    draw = ImageDraw.Draw(photo)
    tracks = entry["tracks"]
    styles = (("shown", "Astra input", "#dcfbff"), ("native", "Same-input CorDriver", "#ff714b"),
              ("adjusted", "Astra + optimizer", "#0be4df"))
    amber = "#ffc94e"
    for target in entry["targets"]:
        for a, b in target["visible_segments_px"]:
            draw.line([tuple(a), tuple(b)], fill=amber, width=5)
    for key, _, color in styles:
        for a, b in tracks[key]["visible_segments_px"]:
            draw.line([tuple(a), tuple(b)], fill="#30434a", width=8)
            draw.line([tuple(a), tuple(b)], fill=color, width=4)
    board = Image.new("RGB", (1600, 860), "#f8faf9")
    board.paste(photo.resize((1140, 641), Image.Resampling.LANCZOS), (20, 104))
    draw = ImageDraw.Draw(board)
    def label(x, y, text, size=24, color="#23343e"):
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size)
        except OSError:
            font = ImageFont.load_default()
        draw.text((x, y), text, fill=color, font=font)
    label(20, 13, f"nuScenes {case}   |   CAM_FRONT   |   3 s open-loop", 35)
    label(20, 65, f"{entry['mode']}   /   frame {entry['image']['exposure_us']} us", 22)
    label(1195, 105, "Guidance and paths", 25)
    coords = [xy for track in tracks.values() for xy in track["xy_m"]]
    coords.extend(xy for t in entry["targets"] for xy in t["outline_xy_m"])
    xmin, xmax = min(p[0] for p in coords), max(p[0] for p in coords)
    ymin, ymax = min(p[1] for p in coords), max(p[1] for p in coords)
    scale = min(340 / max(1, ymax - ymin), 360 / max(1, xmax - xmin))
    def bev(xy):
        return 1373 - (xy[1] - (ymin + ymax) / 2) * scale, 523 - (xy[0] - xmin) * scale
    for target in entry["targets"]:
        draw.polygon([bev(xy) for xy in target["outline_xy_m"]], fill="#faeac3", outline="#926e22", width=2)
    for key, _, color in styles:
        points = [bev(xy) for xy in tracks[key]["xy_m"]]
        draw.line(points, fill="#566c73", width=6)
        draw.line(points, fill=color, width=3)
    for idx, target in enumerate(entry["targets"]):
        lo, hi = target["speed_range_mps"]
        label(1180, 548 + idx * 36, f"{target['time_s']:g} s   {target['yaw_deg']:+g} deg   {lo:g}-{hi:g} m/s", 21)
    old, new = (entry["scores"][method] for method in ("native", "adjusted"))
    for idx, key in enumerate(("ACR_3s", "CCR_3s", "L2_3s")):
        val = (f"{old[key]:.3f} -> {new[key]:.3f} m" if key == "L2_3s"
               else f"{int(old[key])} -> {int(new[key])}")
        label(1180, 629 + 32 * idx, f"{key}    {val}", 23)
    for idx, (_, name, color) in enumerate(styles):
        left = 22 + idx * 320
        draw.line([(left, 782), (left + 33, 782)], fill="#526970", width=9)
        draw.line([(left, 782), (left + 33, 782)], fill=color, width=5)
        label(left + 44, 765, name, 21)
    label(20, 815, "Gold: Astra target windows. Ground display plane z=0. Future labels were used only for scoring.", 20)
    out.parent.mkdir(parents=True, exist_ok=True)
    board.save(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("metrics", help="Recompute statistics from saved per-frame scores")
    dispatch = commands.add_parser("dispatch", help="Replay causal routing and select actual paths from frozen receipts")
    dispatch.add_argument("--out", type=Path, help="Write both arms' returned trajectories to a new JSON file")
    front = commands.add_parser("front", help="Render frozen trajectories on the locally licensed CAM_FRONT image")
    front.add_argument("--case", required=True, help="For example 0018/31")
    front.add_argument("--dataroot", type=Path, required=True)
    front.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "metrics":
        print(json.dumps(score_report(), indent=2, allow_nan=False))
    elif args.action == "dispatch":
        print(json.dumps(replay_dispatch(args.out), indent=2, allow_nan=False))
    else:
        render_front(args.case, args.dataroot, args.out)
