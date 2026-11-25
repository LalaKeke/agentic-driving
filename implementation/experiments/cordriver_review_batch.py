"""Run the registered causal baseline, freeze review requests, or score completed paired returns."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace

from cordriver_reachable_options import load, save, sha


def fast_one(args):
    from cordriver_pipeline import plan

    root, data, case = args
    token = case["token"]
    dest = root / "fast" / token
    if not (dest / "return.json").exists():
        plan(SimpleNamespace(study=root / "study", out=dest, token=token, data=data, fast_only=True))
    return token


def inspect(root, case, *, save_wire=True):
    from cordriver_fast_review import fast_path, review_packet, review_edges, approaching_boundary
    from cordriver_guidance_wire import build

    token = case["token"]
    study = root / "study"
    path = root / "fast" / token / "return.json"
    baseline, _, poses, _, _ = fast_path(study, token, path)
    visual = load(study / "frontend" / token / "visual_map.json")
    secfile = study / "secondary_primary" / (token + ".json")
    triggered, edge, sec = review_edges(
        baseline["gate"]["legacy_trigger"],
        poses,
        visual["map_features"],
        load(secfile)["features"] if secfile.exists() else None,
    )
    row = dict(
        token=token,
        scene=case["name"],
        frame=case["frame"],
        panel=case["panel"],
        log=case["log_token"],
        status="REVIEW"
        if triggered and poses is not None
        else ("NO_BOUND_POSES" if poses is None else "NO_TRIGGER"),
        fast_source=baseline["source"],
        fast_sha256=sha(path),
        primary_edge=edge,
        secondary_edge=sec,
    )
    if not save_wire:
        from cordriver_edge_gate import native_body_knots

        (state,) = [r for r in load(study / "native_states.json")["samples"] if r["token"] == token]
        native, _ = native_body_knots(load(study / "frontend" / token / "sample.json"), state)
        row["native_poses"] = None if native is None else native.tolist()
        row["fast_poses"] = None if poses is None else poses.tolist()
        row["extra_review"] = poses is not None and not triggered and approaching_boundary(edge, sec)
    elif row["status"] == "REVIEW":
        folder = root / "offers" / token
        packet = review_packet(study, token, path, folder)
        binding, _ = build(packet)
        row["body_sha256"] = binding["body_sha256"]
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=("fast", "offers", "gates", "pass-through", "live", "score"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--score-output", type=Path, help="New path for this scoring snapshot")
    a = p.parse_args()
    root = a.root.resolve()
    data = a.data.resolve()
    study = root / "study"
    reg = load(study / "selection/registration.json")
    front = load(study / "frontend/frontend.json")
    load(study / "secondary_primary/sources.json")  # The secondary export finishes after the frontend.
    present = {r["token"] for r in front["samples"]}
    cases = [c for c in reg["cases"] if c["token"] in present]
    if a.stage == "fast":
        with ProcessPoolExecutor(max_workers=a.workers) as pool:
            for token in pool.map(fast_one, [(root, data, c) for c in cases]):
                print(token, flush=True)
        return
    if a.stage in ("offers", "gates"):
        failures = {r["token"]: r for r in front["failures"]}
        rows = [
            inspect(root, c, save_wire=a.stage == "offers")
            if c["token"] in present
            else dict(
                scene=c["name"],
                token=c["token"],
                frame=c["frame"],
                log=c["log_token"],
                panel=c["panel"],
                status="NO_FRONTEND",
                reason=failures[c["token"]]["detail"],
            )
            for c in reg["cases"]
        ]
        if a.stage == "gates":
            save(
                root / "gate_decisions.json",
                dict(registration_sha256=sha(study / "selection/registration.json"), rows=rows),
            )
            return
        selected = [r for r in rows if r["status"] == "REVIEW"][: reg["cap_live"]]
        save(
            root / "offers.json",
            dict(
                registration_sha256=sha(study / "selection/registration.json"),
                rows=rows,
                selected=[r["token"] for r in selected],
            ),
        )
        return
    offers = load(root / "offers.json")
    if a.stage == "pass-through":
        from cordriver_pipeline import plan, checked_return

        for row in offers["rows"]:
            if row["status"] not in ("NO_TRIGGER", "NO_BOUND_POSES"):
                continue
            t = row["token"]
            dest = root / "dual" / t
            baseline = root / "fast" / t / "return.json"
            if not (dest / "return.json").exists():
                plan(
                    SimpleNamespace(
                        study=study,
                        data=data,
                        token=t,
                        out=dest,
                        fast_only=False,
                        fast_return=baseline,
                        guided_search="verified",
                        responses=None,
                        live=False,
                        frozen_response=None,
                    )
                )
            result = load(dest / "return.json")
            if (
                result.get("reason") not in ("NO_TRIGGER", "NO_BOUND_FAST_POSES")
                or result["fast_return_sha256"] != sha(baseline)
                or checked_return(study, t, baseline) != checked_return(study, t, dest / "return.json")
            ):
                raise ValueError("FAST_DEFAULT_REPLAY_MISMATCH")
        return
    if a.stage == "live":
        from cordriver_guidance_wire import obtain, build
        from cordriver_pipeline import plan

        expected = [r["token"] for r in offers["rows"] if r["status"] == "REVIEW"][: reg["cap_live"]]
        if (
            offers["registration_sha256"] != sha(study / "selection/registration.json")
            or [r["token"] for r in offers["rows"]] != [c["token"] for c in reg["cases"]]
            or offers["selected"] != expected
            or len(expected) != len(set(expected))
        ):
            raise ValueError("LIVE_BATCH_REGISTRATION_MISMATCH")
        ledger = root / "live_batch"
        ledger.mkdir(exist_ok=False)
        for t in offers["selected"]:
            target = root / "offers" / t / "packet.json"
            binding, _ = build(target)
            (row,) = [r for r in offers["rows"] if r["token"] == t]
            if binding["body_sha256"] != row["body_sha256"]:
                raise ValueError("FROZEN_WIRE_CHANGED")
            try:
                obtain(target, None, ledger, True)
                plan(
                    SimpleNamespace(
                        study=study,
                        data=data,
                        token=t,
                        out=root / "dual" / t,
                        fast_only=False,
                        guided_search="verified",
                        fast_return=root / "fast" / t / "return.json",
                        responses=ledger,
                        live=False,
                        frozen_response=None,
                    )
                )
            except Exception as e:
                save(ledger / "stop.json", dict(token=t, error=type(e).__name__, message=str(e)))
                raise
        return
    from cordriver_published_metrics import Published, rescore
    from cordriver_pipeline import checked_return

    published = Published(data)
    scores = []
    for row in offers["rows"]:
        t = row["token"]
        if row["status"] == "NO_FRONTEND":
            scores.append(dict(token=t, status=row["status"]))
            continue
        truth = published.inputs(t)
        fast = root / "fast" / t / "return.json"
        if not truth:
            scores.append(dict(token=t, status="MISSING_EVALUATION_FUTURE"))
            continue
        native_xy, fast_xy = checked_return(study, t, fast)
        values = dict(
            token=t,
            status=row["status"],
            native=rescore(published, truth, native_xy),
            fast=rescore(published, truth, fast_xy),
        )
        final = root / "dual" / t / "return.json"
        if final.exists():
            _, dual_xy = checked_return(study, t, final)
            values.update(
                dual=rescore(published, truth, dual_xy),
                decision=load(final)["decision"],
                source=load(final)["source"],
            )
        elif row["status"] == "NO_TRIGGER":
            values.update(dual=values["fast"], decision="NOT_TRIGGERED", source="FAST_DEFAULT")
        scores.append(values)
    save(a.score_output or root / "scores.json", dict(offers_sha256=sha(root / "offers.json"), scores=scores))


if __name__ == "__main__":
    main()
