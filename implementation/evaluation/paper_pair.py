"""Score frozen dual-system returns with CorDriver's released nuScenes planning metric."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

from review import full_validation

METRICS = {"L2": "plan_L2", "ACR": "plan_obj_box_col", "CCR": "plan_obj_box_map_intersect"}
HORIZONS = (1, 2, 3)
METHODS = ("CorDriver", "CorDriver + Astra")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, content):
    # status can run concurrently with the scorer
    tmp = path.with_name(path.name + "." + str(os.getpid()) + ".partial")
    tmp.write_text(content)
    tmp.replace(path)


def metrics(row):
    return {f"{key}_{h}s": row["metrics"][f"{upstream}_{h}s"]
            for key, upstream in METRICS.items() for h in HORIZONS}


def report(root):
    producer = root / "shared01"
    official = producer / "evaluation/upstream.jsonl"
    baseline = dual = None
    if (producer / "official_complete.json").exists():
        baseline = full_validation([official], "official")["methods"]["official"]
    if (root / "paired_complete.json").exists():
        result = read(root / "paired_complete.json")
        baseline, dual = result["methods"]["native"], result["methods"]["dual"]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Method"] + [f"{m}_{h}" for m in METRICS for h in ("1s", "2s", "3s", "Avg")])
    for name, scores in zip(METHODS, (baseline, dual)):
        cells = []
        for key in METRICS:
            cells.extend([""] * 4 if scores is None else
                         [f"{x:.6f}" for x in scores[key]["values_1_2_3s"] + [scores[key]["avg"]]])
        writer.writerow([name] + cells)
    write(root / "table.csv", buf.getvalue())
    routing = list((root / "routing").glob("*.json"))
    ledgers = [root / "astra", *sorted((root / "batches").glob("*/astra"))]
    receipts = []
    for ledger in ledgers:
        for path in ledger.glob("*/receipt.json"):
            try:
                receipts.append(read(path))
            except json.JSONDecodeError:
                # The original transport writes a receipt directly. Read the completed file next poll.
                continue
    status = dict(official_total=6019, valid_total=5119,
                  official_forward=len(list((producer / "frontend").glob("*.json.gz"))),
                  screened=len(routing), triggered=sum(read(p)["trigger"] for p in routing),
                  locked_returns=len(list((root / "returns").glob("*.json"))),
                  scored=len(list((root / "scored").glob("*.json"))),
                  requests=sum(1 for ledger in ledgers for p in ledger.glob("*/precharge.json")),
                  receipts=dict(Counter(r["status"] for r in receipts)),
                  known_tokens=sum((r.get("usage") or {}).get("total_tokens", 0) for r in receipts),
                  final_baseline_available=baseline is not None, final_dual_available=dual is not None)
    write(root / "status.json", json.dumps(status, indent=2) + "\n")
    return status


def learn_new_rows(file, records):
    # A producer may still be writing the last line.
    while True:
        offset = file.tell()
        line = file.readline()
        if not line.endswith("\n"):
            file.seek(offset)
            return
        row = json.loads(line)
        if row["token"] in records:
            raise ValueError("Duplicate token in upstream run: " + row["token"])
        records[row["token"]] = row


def score(root, watch):
    from cordriver_published_metrics import Published, rescore
    evaluator = Published(Path("/data/nuscenes_dataset"))
    out = root / "scored"
    out.mkdir(exist_ok=True)
    expected = set(read(root / "registration.json")["info_valid_tokens"])
    done = {p.stem for p in out.glob("*.json")}
    producer = root / "shared01"
    records = {}
    with (producer / "evaluation/upstream.jsonl").open() as stream:
        while True:
            learn_new_rows(stream, records)
            returns = {p.stem for p in (root / "returns").glob("*.json")}
            if not returns <= expected or not done <= returns:
                raise ValueError("Invalid return/score token set")
            for t in sorted((returns - done) & records.keys()):
                row = records[t]
                retpath = root / "returns" / (t + ".json")
                ret = read(retpath)
                src = producer / "frontend" / (t + ".json.gz")
                if not row["fut_valid_flag"] or ret["token"] != t or digest(src) != ret["shared_sha256"]:
                    raise ValueError("Invalid same-frame binding: " + t)
                with gzip.open(src, "rt") as f:
                    native = json.load(f)["sample"]["final_xy_lidar_m"]
                xy = ret["returned_xy_lidar_m"]
                identical = xy == native
                if not identical and (ret["source"] != "ASTRA_ADJUST" or
                                      digest(root / ret["candidate"]) != ret["candidate_sha256"]):
                    raise ValueError("Unverified change: " + t)
                original = metrics(row)
                dual = original
                # Validate a control frame and every changed frame against upstream test.py.
                replay = not done or not identical
                if replay:
                    labels = evaluator.inputs(t)
                    check = rescore(evaluator, labels, native)
                    if not all(math.isclose(original[k], float(check[k]), rel_tol=1e-6, abs_tol=1e-6) for k in original):
                        raise ValueError(f"Upstream metric parity failed for {t}: {original} vs {check}")
                    if not identical:
                        scored = rescore(evaluator, labels, xy)
                        dual = {k:float(scored[k]) for k in original}
                result = dict(evaluation="PUBLISHED_NUSCENES_OPEN_LOOP", token=t,
                              return_sha256=digest(retpath), exact_native=identical, replay_checked=replay,
                              native=original, dual=dual)
                write(out / (t + ".json"), json.dumps(result, allow_nan=False) + "\n")
                done.add(t)
            if done == expected and (producer / "official_complete.json").exists():
                paired = full_validation(sorted(out.glob("*.json")), "paired")
                upstream = full_validation([producer / "evaluation/upstream.jsonl"], "official")
                for k in METRICS:
                    if any(not math.isclose(a,b,abs_tol=1e-10) for a,b in zip(
                            paired["methods"]["native"][k]["values_1_2_3s"],
                            upstream["methods"]["official"][k]["values_1_2_3s"])):
                        raise ValueError("Complete native result differs from official")
                write(root / "paired_complete.json", json.dumps(paired, indent=2) + "\n")
                print(report(root), flush=True)
                return
            status = report(root)
            print(status, flush=True)
            if ((producer / "official_complete.json").exists() and
                    (root / "routing_complete.json").exists() and done == returns and
                    status["receipts"].get("STOP", 0) and not any(
                        not (p.parent / "stop.json").exists() and not (p.parent / "slow_complete.json").exists()
                        for p in (root / "batches").glob("*/registration.json"))):
                write(root / "local_complete.json", json.dumps(status, indent=2) + "\n")
                return
            if not watch:
                return
            time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "score"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    if args.action == "status":
        print(json.dumps(report(args.root.resolve()), indent=2))
    else:
        score(args.root.resolve(), args.watch)
