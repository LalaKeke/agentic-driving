"""Close the complete official split, accounting separately for terminal service failures."""
import argparse
import csv
import io
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from paper_pair import METRICS, digest, full_validation, read, write


def aggregate(scores):
    n = len(scores)
    result = {}
    for k in METRICS:
        sums = [sum(float(row[f"{k}_{h}s"]) for row in scores) for h in (1,2,3)]
        v = [x * (1 if k == "L2" else 100) / n for x in sums]
        result[k] = dict(values_1_2_3s=v, avg=mean(v))
        if k != "L2":
            result[k]["hits_1_2_3s"] = [int(x) for x in sums]
    return result


def format_rows(scores):
    rows = [["Method"]+[f"{k}_{h}" for k in METRICS for h in ("1s","2s","3s","Avg")]]
    for arm, name in (("native","CorDriver"),("dual","CorDriver + Astra")):
        m = scores[arm]
        rows.append([name]+[f"{v:.6f}" for k in METRICS for v in m[k]["values_1_2_3s"] + [m[k]["avg"]]])
    return rows


def save_csv(path, rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    write(path, buf.getvalue())


def audit(root, exception):
    tokens = set(read(root / "registration.json")["info_valid_tokens"])
    valid = len(tokens)
    official = root / "shared01/evaluation/upstream.jsonl"
    native = full_validation([official], "official")
    assert native["n_scored"] == valid == 5119 and native["n_invalid_in_input"] == 900
    assert (root / "routing_complete.json").is_file() and (root / "shared01/official_complete.json").is_file()
    dispatch = defaultdict(list)
    for ledger in [root / "astra", *sorted((root / "batches").glob("*/astra"))]:
        for directory in ledger.iterdir():
            pre = read(directory / "precharge.json")
            receipt = read(directory / "receipt.json")
            assert pre["token"] == receipt["token"] == directory.name
            assert pre["body_sha256"] == digest(directory / "request.json")
            assert receipt["client_post_attempts"] == 1
            if receipt["status"] == "COMPLETE":
                assert receipt["http"] == 200 and receipt["requested_model"] == receipt["returned_model"] == "gpt-6.2-astra"
                assert receipt["decision"] == read(directory / "native_output.json")["decision"]
                envelope = read(directory / "native_envelope.json")
                assert envelope["status"] == "completed" and envelope["model"] == receipt["returned_model"]
            offer = read(ledger.parent / "cases" / directory.name / "offer.json")
            assert pre["body_sha256"] == offer["body_sha256"]
            dispatch[directory.name].append((receipt, pre))
    assert dispatch.keys() <= tokens and sum(map(len, dispatch.values())) <= valid
    assert all(len(v) == 1 or (k == exception and len(v) == 2 and
               v[0][1]["body_sha256"] == v[1][1]["body_sha256"]) for k,v in dispatch.items())
    sources, decisions, ledgers, usage = Counter(), Counter(), Counter(), Counter()
    triggers, missing, seen, raw = 0, [], [], {}
    with official.open() as f:
        for line in f:
            row = json.loads(line)
            if row["fut_valid_flag"]:
                raw[row["token"]] = {f"{k}_{h}s":row["metrics"][f"{up}_{h}s"]
                                     for k,up in METRICS.items() for h in (1,2,3)}
    assert set(raw) == tokens
    for t in sorted(tokens):
        route = read(root / "routing" / (t + ".json"))
        path = root / "returns" / (t + ".json")
        scorefile = root / "scored" / (t + ".json")
        attempts = dispatch.get(t, [])
        complete = [r for r, _ in attempts if r["status"] == "COMPLETE"]
        triggers += int(route["trigger"])
        for r, _ in attempts:
            ledgers[r["status"]] += 1
            for k in ("input_tokens", "output_tokens", "total_tokens"):
                if r.get("usage"):
                    usage[k] += r["usage"][k]
        if not path.exists():
            assert attempts and not complete and route["trigger"] and not scorefile.exists(), t
            missing.append(t)
            continue
        returned, scores = read(path), read(scorefile)
        assert returned["token"] == route["token"] == t and route["shared_sha256"] == returned["shared_sha256"]
        assert digest(path) == scores["return_sha256"] and raw[t] == scores["native"]
        sources[returned["source"]] += 1
        if attempts:
            assert route["trigger"] and len(complete) == 1
            decision = complete[0]["decision"]
            assert returned["source"] in (("ASTRA_KEEP_NATIVE",) if decision == "KEEP" else
                                           ("ASTRA_ADJUST","ASTRA_NO_RELEASE_NATIVE"))
            decisions[decision] += 1
        if returned["source"].startswith("ASTRA"):
            assert complete
        if not route["trigger"]:
            assert not attempts and returned["source"] == "NO_TRIGGER_NATIVE"
        seen.append(scores)
    assert len(seen)+len(missing)==5119
    observed={arm:aggregate([row[arm] for row in seen]) for arm in ("native","dual")}
    save_csv(root / "paired_observed.csv", format_rows(observed))
    out = dict(valid_frames=valid, raw_frames=valid+900, triggered=triggers, attempted_frames=len(dispatch),
               measured_pairs=len(seen), service_unresolved=missing, client_attempts=sum(ledgers.values()),
               ledger=dict(ledgers), decisions=dict(decisions), known_usage=dict(usage), returns=dict(sources),
               authorized_exception=exception, paired_observed=observed, official_native=native["methods"]["official"])
    out["three_second_transitions"] = {k:dict(cleared=sum(row["native"][k+"_3s"]>row["dual"][k+"_3s"] for row in seen),
                                        introduced=sum(row["native"][k+"_3s"]<row["dual"][k+"_3s"] for row in seen))
                                        for k in ("ACR","CCR")}
    if missing:
        # Unconditional type-specific full-denominator bounds. An unscored frame contributes 0 or 1 hit.
        out["full_dual_collision_bounds_pct"] = {k:[[100*n/valid,100*(n+len(missing))/valid]
                 for n in observed["dual"][k]["hits_1_2_3s"]] for k in ("ACR","CCR")}
    else:
        assert (root/"paired_complete.json").exists()
    proof_list = []
    for t in missing:
        proof = root/"recovery/invariant"/(t+".json")
        if proof.is_file():
            d=read(proof)
            assert d["token"]==t and d["sum_squared_speed_m2_s2"]<d["minimum"] and d["rows"]>0
            assert all(tr["result"]["reasons"][0]=="CAN_INITIAL_STEERING_UNOBSERVABLE" for tr in d["checks"])
            proof_list.append(t)
    if missing and set(proof_list)==set(missing):
        supplement={"native":aggregate(list(raw.values())),
                    "dual":aggregate([row["dual"] for row in seen]+[raw[t] for t in proof_list])}
        out["conditional_on_valid_guidance_for_missing_frames"] = dict(tokens=proof_list, metrics=supplement)
        save_csv(root/"identified_if_valid.csv", format_rows(supplement))
    rows = format_rows(observed)
    lines = ["# CorDriver 官方验证集配对测评", "", f"完整扫描 6,019 帧，官方有效分母 5,119 帧。"
             f"两行在同一组 {len(seen):,} 帧上实际取得轨迹并计分。服务未决 {len(missing)} 帧。", "",
             "| "+" | ".join(rows[0])+" |","|---|"+"---:|"*12]
    lines += ["| "+" | ".join(row)+" |" for row in rows[1:]]
    lines += ["", f"单位为 m（L2）和 %（ACR、CCR）。冻结条件触发 {triggers} 帧，"
              f"{sum(decisions.values())} 帧取得真实 Astra 回答，客户端累计尝试 {sum(ledgers.values())} 次。",
              "", "[精确配对成绩](paired_observed.csv) · [完整官方分母的主表](table.csv) · [回执与未决分析](closeout.json)"]
    if "conditional_on_valid_guidance_for_missing_frames" in out:
        lines += ["", "对于未获得服务响应的帧，独立检查发现任何可编译的模型决定均会返回原轨迹。"
                  "基于该条件可以另算完整分母的轨迹指标。该分析保留真实请求失败及其缺失的模型决定。[条件分析](identified_if_valid.csv)"]
    write(root/"closeout.json",json.dumps(out,indent=2,allow_nan=False)+"\n")
    write(root/"RESULTS.md","\n".join(lines)+"\n")
    print(json.dumps(out,indent=2))


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--authorized-exception",required=True)
    p.add_argument("--after", help="watch the named running batch and wait for the scoring queue to drain")
    a=p.parse_args()
    if not __debug__:
        p.error("Run with assertions enabled.")
    r=a.root.resolve()
    if a.after:
        b=r/"batches"/a.after
        while not (b/"slow_complete.json").is_file():
            if (b/"stop.json").exists():
                raise SystemExit("Batch stopped: "+str(b/"stop.json"))
            time.sleep(15)
        while {q.stem for q in (r/"returns").glob("*.json")} != {q.stem for q in (r/"scored").glob("*.json")}:
            time.sleep(10)
    audit(r,a.authorized_exception)
