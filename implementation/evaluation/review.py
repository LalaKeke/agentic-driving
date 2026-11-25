"""Review frozen CorDriver open-loop scores or render a front-camera example."""
import argparse
from collections import Counter
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


def full_validation(paths, source):
    """Aggregate archived upstream metrics or paired records from cordriver_pipeline score."""
    horizons = (1, 2, 3)
    names = {"ACR": "plan_obj_box_col", "CCR": "plan_obj_box_map_intersect", "L2": "plan_L2"}
    arms = ("official",) if source == "official" else ("native", "dual")
    totals = {arm: {key: [0, 0, 0] for key in names} for arm in arms}
    seen, valid = set(), set()
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            records = (json.loads(line) for line in stream) if path.suffix == ".jsonl" else [json.load(stream)]
            for row in records:
                token = row["token"]
                if token in seen:
                    raise ValueError(f"Duplicate validation token: {token}")
                seen.add(token)
                if source == "official":
                    if type(row["fut_valid_flag"]) is not bool:
                        raise ValueError(f"Invalid upstream validity flag: {token}")
                    if not row["fut_valid_flag"]:
                        continue
                else:
                    if row["evaluation"] != "PUBLISHED_NUSCENES_OPEN_LOOP":
                        raise ValueError(f"Unexpected scoring protocol: {token}")
                    if row.get("status") == "UNAVAILABLE_EVALUATION_FUTURE":
                        continue
                valid.add(token)
                for arm in arms:
                    scores = row["metrics"] if source == "official" else row[arm]
                    for key, upstream in names.items():
                        prefix = upstream if source == "official" else key
                        for index, horizon in enumerate(horizons):
                            value = scores[f"{prefix}_{horizon}s"]
                            if not math.isfinite(value) or value < 0 or (key != "L2" and value not in (0, 1)):
                                raise ValueError(f"Invalid {arm} {key} at {horizon}s: {token}")
                            totals[arm][key][index] += value

    # Digests of sorted tokens from the release validation infos, one ASCII token per line.
    groups = [(valid, 5119, "53be6dce4b6c2118746d890a0cbd51a22cfb7c5b4d1d39e36749a8522d7c6a76")]
    if seen != valid:
        groups.append((seen - valid, 900, "ba482ae3e9c895e46719dd4afba73090a22274ee75b1bea770916e94ac3251d2"))
    for tokens, count, digest in groups:
        actual = hashlib.sha256(("\n".join(sorted(tokens)) + "\n").encode("ascii")).hexdigest()
        if len(tokens) != count or actual != digest:
            raise ValueError(f"Full validation split mismatch: expected {count} tokens ({digest}), got {len(tokens)} ({actual})")

    result = {"n_scored": len(valid), "n_invalid_in_input": len(seen - valid), "methods": {}}
    for arm, metrics in totals.items():
        result["methods"][arm] = {}
        for key, sums in metrics.items():
            factor = 1 if key == "L2" else 100
            values = [factor * total / len(valid) for total in sums]
            summary = {"unit": "m" if key == "L2" else "%", "values_1_2_3s": values, "avg": mean(values)}
            if key != "L2":
                summary["hits_1_2_3s"] = [int(total) for total in sums]
            result["methods"][arm][key] = summary
    return result


def log_flip_test(rows, before, after):
    """Two-sided exact paired sign-flip test on 3 s ANY counts per recording log."""
    delta = Counter()
    for row in rows:
        delta[row["log_token"]] += int(row[after+"_ANY_3s"]) - int(row[before+"_ANY_3s"])
    observed = sum(delta.values())
    distribution = Counter({0: 1})
    for difference in delta.values():
        updated = Counter()
        for total, count in distribution.items():
            updated[total + difference] += count
            updated[total - difference] += count
        distribution = updated
    p = sum(count for value, count in distribution.items() if abs(value) >= abs(observed)) / sum(distribution.values())
    return dict(logs=len(delta), nonzero_logs=sum(v!=0 for v in delta.values()), delta=observed, p_two_sided=p)


def fast_default_report():
    replay_dispatch(None)
    records = json.loads((HERE/"dispatch_inputs.json").read_text())["frames"]
    outputs = {}
    for frame in records:
        slow = select_slow(frame["slow"]) if "slow" in frame else None
        outputs[frame["token"]] = "dual" if slow else "fast"
    paired = [r for r in read_table("per_frame.csv") if r["causal_status"] == "AVAILABLE"]
    if set(outputs) != {r["token"] for r in paired}:
        raise ValueError("ARCHIVE_SCORE_BINDING_MISMATCH")
    for row in paired:
        method = outputs[row["token"]]
        for h in (1,2,3):
            for metric in ("ACR","CCR","ANY","L2"):
                key = f"{metric}_{h}s"
                row["replay_"+key] = row[method+"_"+key]
    return dict(scope="Retrospective routing of frozen decisions and previously scored paths",
                scores={arm:method_scores(paired,arm) for arm in ("native","fast","dual","replay")},
                transitions={kind:sum((int(row["fast_ANY_3s"]),int(row["replay_ANY_3s"]))==values for row in paired)
                             for kind,values in (("cleared",(1,0)),("introduced",(0,1)))},
                vs_fast=log_flip_test(paired,"fast","replay"), vs_native=log_flip_test(paired,"native","replay"))


def transfer_report(run, out):
    """Join independently scored outputs. Open recorded future labels only on the scoring side."""
    def load(path):
        return json.loads((run/path).read_text())

    def flatten(scores, arm, row):
        for k,v in scores.items():
            row[arm+"_"+k] = v

    archive = {r["token"]: r for r in load("archived/archive_scores.json")}
    minimal = {r["token"]: r for r in load("archived/minimal_scores.json")}
    decisions = {r["token"]:r["arms"]["minimal"] for r in load("archived/window_minimal/summary.json") if "arms" in r}
    original = {r["token"]:r["arms"]["original"] for r in load("archived/window_comparison/summary.json") if "arms" in r}
    arms = ("native","fast","original","verified_seed","floor_aware","minimal")
    whole, adjusted, missing = [], [], []
    for frozen in read_table("per_frame.csv"):
        if frozen["causal_status"] != "AVAILABLE":
            continue
        token = frozen["token"]
        row = {k:frozen[k] for k in ("scene","frame_idx","log_token","token","astra_decision")}
        if frozen["astra_decision"] == "ADJUST":
            if archive[token]["status"]!="SCORED" or minimal[token]["status"]!="SCORED":
                missing.append(dict(row, reason=archive[token]["status"]))
                continue
            for arm in arms:
                source = minimal[token] if arm=="minimal" else archive[token]
                flatten(source[arm],arm,row)
            final=decisions[token]
            row["case_outcome"]=final["selected_task"] or final["reasons"][0]
            adjusted.append(row)
        else:
            row["case_outcome"]=frozen["astra_decision"] or "NO_TRIGGER"
            for arm in arms:
                baseline = "native" if arm=="native" else "fast"
                for h in (1,2,3):
                    for k in ("ACR","CCR","ANY","L2"):
                        suffix=f"_{k}_{h}s"
                        row[arm+suffix]=frozen[baseline+suffix]
        whole.append(row)
    def summarize(rows):
        return dict(scores={a:method_scores(rows,a) for a in arms},
                    vs_fast={a:log_flip_test(rows,"fast",a) for a in arms if a not in ("fast","native")},
                    minimal_vs_native=log_flip_test(rows,"native","minimal"),
                    transitions={a:dict(cleared=sum(int(r["fast_ANY_3s"])>int(r[a+"_ANY_3s"]) for r in rows),
                                        introduced=sum(int(r["fast_ANY_3s"])<int(r[a+"_ANY_3s"]) for r in rows))
                                 for a in arms if a not in ("fast","native")})
    fresh = load("scores_after_stop.json")["scores"]
    membership = {r["token"]:r for r in load("study/selection/registration.json")["cases"]}
    offers = {r["token"]:r for r in load("offers.json")["rows"]}
    prospective=[]
    for score in fresh:
        case=membership[score["token"]]
        row=dict(token=score["token"],scene=case["name"],frame=case["frame"],log_token=case["log_token"],
                 panel=case["panel"],status=score["status"],actual_return=score.get("source"),
                 reason=offers[score["token"]].get("reason"))
        if row["status"]=="REVIEW":
            receipt=run/"live_batch"/row["token"]/"receipt.json"
            row["model_status"]="NOT_SENT_AFTER_BATCH_STOP"
            if receipt.is_file():
                service=json.loads(receipt.read_text())
                row.update(model_status=service["status"],dispatch=service["dispatch"],http=service["http"],
                           model_returned=service["returned_model"],usage=service["usage"])
        for arm in ("native","fast","dual"):
            if arm in score:
                flatten(score[arm],arm,row)
        prospective.append(row)
    stats = dict(archive_adjust=summarize(adjusted), historical_routing=summarize(whole), missing=missing,
                 residuals={k:dict(frames=sum(r["case_outcome"]==k for r in whole),
                                   any_3s=sum(int(r["minimal_ANY_3s"]) for r in whole if r["case_outcome"]==k))
                            for k in sorted({r["case_outcome"] for r in whole})},
                 new_registered=len(prospective), new_results={a:method_scores(
                     [r for r in prospective if a+"_ANY_3s" in r],a) for a in ("native","fast","dual")})
    out.mkdir(parents=True,exist_ok=False)
    solver=[]
    for row in adjusted:
        t=row["token"]
        trace={k:row[k] for k in ("token","scene","frame_idx","log_token")}
        for arm,entry in (("original",original[t]),("minimal",decisions[t])):
            trace[arm+"_quality"]=entry["selected_task"] or "NO_RELEASE"
            trace[arm+"_fits"]=entry["control_solves"]
            trace[arm+"_method"]=entry["selected_method"]
            trace[arm+"_reason"]="; ".join(entry["reasons"])
        solver.append(trace)
    for name, rows in (("historical_frames",whole),("new_frames",prospective),("solver_by_frame",solver)):
        with (out/(name+".csv")).open("x",newline="") as stream:
            writer=csv.DictWriter(stream,fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            writer.writeheader()
            writer.writerows(rows)
    (out/"metrics.json").write_text(json.dumps(stats,indent=2,allow_nan=False)+"\n")
    return {k:len(v) for k,v in (("adjusted",adjusted),("historical",whole),("prospective",prospective),("missing",missing))}


def routing_report(run):
    decisions=json.loads((run/"gate_decisions.json").read_text())
    frozen={r["token"]: r for r in json.loads((HERE/"dispatch_inputs.json").read_text())["frames"] if "slow" not in r}
    if set(frozen)!={r["token"] for r in decisions["rows"]}:
        raise ValueError("ROUTING_COHORT_CHANGED")
    labels={r["token"]:r for r in read_table("per_frame.csv")}
    rows=[]
    for item in decisions["rows"]:
        token=item["token"]
        row=dict(token=token,scene=item["scene"],frame=item["frame"],status=item["status"])
        rows.append(row)
        if item["status"] in ("NO_FRONTEND","NO_BOUND_POSES") or item["native_poses"] is None:
            continue
        source=frozen[token]
        fast=select_fast(source)
        native=source["native_halfsecond_xy_yaw"]
        original=fast["xy_m"][::2] if fast else [p[:2] for p in native]
        returned=[p[:2] for p in item["fast_poses"]]
        def max_delta(a,b):
            if len(a)!=7 or len(b)!=7 or any(len(x)!=len(y) for x,y in zip(a,b)):
                raise ValueError("INVALID_TRAJECTORY_BINDING")
            return max(abs(u-v) for x,y in zip(a,b) for u,v in zip(x,y))
        row["native_max_delta"]=max_delta(native,item["native_poses"])
        row["fast_max_delta_m"]=max_delta(original,returned)
        if max(row["native_max_delta"],row["fast_max_delta_m"]) > .0005:
            row["status"]="ARCHIVE_TRAJECTORY_MISMATCH"
            continue
        row.update(status="BOUND",review=item["status"]=="REVIEW",extra=item["extra_review"],
                   fast_ANY_3s=int(labels[token]["fast_ANY_3s"]),fast_CCR_3s=int(labels[token]["fast_CCR_3s"]),
                   fast_ACR_3s=int(labels[token]["fast_ACR_3s"]))
    bound=[r for r in rows if r["status"]=="BOUND"]
    selected=[r for r in bound if r["review"]]
    extra=[r for r in bound if r["extra"]]
    def counts(group):
        return {"frames":len(group),**{k:sum(r["fast_"+k+"_3s"] for r in group) for k in ("ACR","CCR","ANY")}}
    return dict(registered=len(rows),bound=counts(bound),review=counts(selected),extra_only=counts(extra),
                withheld=counts([r for r in bound if not r["review"] and not r["extra"]]),rows=rows)


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
    full = commands.add_parser("full-val", help="Official full-validation aggregation with exact token coverage")
    full.add_argument("--source", choices=("official", "paired"), required=True)
    full.add_argument("scores", nargs="+", type=Path, help="Upstream rows.jsonl shards or single-token score JSONs/JSONL")
    commands.add_parser("fast-default", help="Evaluate keeping the actual fast return when no guided replacement passed")
    transfer = commands.add_parser("transfer", help="Summarize the bound solver and prospective cohorts")
    transfer.add_argument("--run",type=Path,required=True)
    transfer.add_argument("--out",type=Path,required=True)
    overlay = commands.add_parser("transfer-front", help="Compare the returned fast path with the archived guided replan")
    overlay.add_argument("--run",type=Path,required=True)
    overlay.add_argument("--token",required=True)
    overlay.add_argument("--out",type=Path,required=True)
    routing = commands.add_parser("routing",help="Bind frozen returns and evaluate the expanded review gate")
    routing.add_argument("--run",type=Path,required=True)
    dispatch = commands.add_parser("dispatch", help="Replay causal routing and select actual paths from frozen receipts")
    dispatch.add_argument("--out", type=Path, help="Write both arms' returned trajectories to a new JSON file")
    front = commands.add_parser("front", help="Render frozen trajectories on the locally licensed CAM_FRONT image")
    front.add_argument("--case", required=True, help="For example 0018/31")
    front.add_argument("--dataroot", type=Path, required=True)
    front.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "metrics":
        print(json.dumps(score_report(), indent=2, allow_nan=False))
    elif args.action == "full-val":
        print(json.dumps(full_validation(args.scores, args.source), indent=2, allow_nan=False))
    elif args.action == "fast-default":
        print(json.dumps(fast_default_report(), indent=2, allow_nan=False))
    elif args.action == "transfer":
        print(json.dumps(transfer_report(args.run,args.out)))
    elif args.action == "transfer-front":
        from atlas_projection import transfer_front
        transfer_front(args.run.resolve(),args.token,args.out)
    elif args.action == "routing":
        print(json.dumps(routing_report(args.run),indent=2,allow_nan=False))
    elif args.action == "dispatch":
        print(json.dumps(replay_dispatch(args.out), indent=2, allow_nan=False))
    else:
        render_front(args.case, args.dataroot, args.out)
