"""Single-token nuScenes CorDriver/Astra open-loop pipeline. The score command alone reads evaluation truth."""
import argparse
import json
from pathlib import Path
import pickle
import re
import shutil
from types import SimpleNamespace

from cordriver_paths import TOOLS
from cordriver_reachable_options import load, save, sha

MODEL = "gpt-6.2-astra"
PIN = dict(upstream_commit="5a0ef06cb84156f6fa8510f5bf584403bf91c3dc",
           weights_sha256="9d005cefa1400d4a4d795417f3aca99f91bcfa97bcef47b9abace1b1e2598b51",
           infos_sha256="0eca00f787a0a48fd2433e2ade5a36a6f770c6367cf7f7a393fe55419c1c97df",
           config_sha256="54b28f6af7d33ef3e068f56851421b91442eb489bdfed505cdf3ddc6cefcff15",
           feature_encoding="released_stage2_v1")


def register(data, infos, token):
    # Only token, scene and timestamps determine inclusion and BEV warm-up.
    with infos.open("rb") as stream:
        rows = [(r["token"], r["timestamp"], r["scene_token"]) for r in pickle.load(stream)["infos"]]
    row, = [r for r in rows if r[0] == token]
    previous = sorted((r for r in rows if r[2] == row[2] and r[1] < row[1]), key=lambda r:r[1])
    scene, = [s for s in load(data / "v1.0-trainval/scene.json") if s["token"] == row[2]]
    return dict(name=scene["name"], scene_token=row[2], token=token, reference_time_us=row[1],
                history=[r[0] for r in previous[-2:]])


def prepare(a):
    import cordriver_nav_frontend as frontend

    a.study.mkdir(parents=True, exist_ok=False)
    selection = a.study / "selection/registration.json"
    selection.parent.mkdir()
    case = register(a.data, frontend.base.INFOS, a.token)
    save(selection, {"cases":[case]})
    if not case["history"]:
        raise ValueError("INSUFFICIENT_PRE_REFERENCE_HISTORY")
    prepare_registered(a)


def prepare_registered(a):
    """Share one frozen network and one secondary export across pre-registered frames."""
    import cordriver_nav_frontend as frontend
    import cordriver_nav_frontend_exposures as exposures
    import cordriver_expansion_native_states as states

    selection = a.study / "selection/registration.json"
    cases = load(selection)["cases"]
    tokens = [c["token"] for c in cases]
    if not tokens or len(tokens) != len(set(tokens)):
        raise ValueError("EXPECTED_UNIQUE_REGISTERED_CASES")
    fixes = a.study / "exposures.json"
    exposures.main(selection, frontend.base.INFOS, a.data, fixes, allow_partial=len(tokens)>1)
    frontend.main(SimpleNamespace(out=a.study/"frontend", registration=selection, packets=a.study/"packets",
                                  data=a.data, exposures=fixes, feature_encoding=PIN["feature_encoding"]))
    summary = a.study / "frontend/frontend.json"
    identity = load(summary)
    if not identity["samples"]:
        raise ValueError(identity["failures"])
    if any(identity[k] != v for k,v in PIN.items()):
        raise ValueError("UNREGISTERED_FRONTEND")
    states.main(summary, a.study / "native_states.json")
    second = a.study / "secondary_primary"
    if a.sparse:
        from cordriver_expansion_secondary import main as export
        export(selection, a.sparse, second)
    else:
        second.mkdir()
        save(second / "sources.json", {"cases":[dict(token=t, admitted=False,
                                                   reason="SECONDARY_MODEL_NOT_PROVIDED") for t in tokens]})


def prepare_planning_inputs(a, inputs, case, weight):
    """Only this run's newly computed clearance floor is written, even for archived inputs."""
    from cordriver_expansion_plan import entry
    inputs.mkdir()
    for key in ("frontend","selection","packets","secondary_primary"):
        (inputs/key).symlink_to((a.study/key).resolve(strict=True), target_is_directory=True)
    entry(inputs, inputs/"selection/registration.json", case, "diagnostic", a.data, weight)


def plan(a):
    import numpy as np
    from cordriver_edge_gate import edge_clearance, native_body_knots, intervention_v2
    from cordriver_guidance_wire import bind_frozen, obtain
    import cordriver_smooth_planner as smooth
    import cordriver_clearance_refit as fast
    import cordriver_reachable_options as menu
    from cordriver_published_xy import published_xy

    a.out.mkdir(parents=True, exist_ok=False)
    native_file = a.study / "frontend" / a.token / "sample.json"
    sample = load(native_file)
    visual_file = native_file.with_name("visual_map.json")
    visual = load(visual_file)
    packet = a.study / "packets" / a.token / "packet.json"
    context = load(packet)
    registry = a.study / "selection/registration.json"
    case, = [r for r in load(registry)["cases"] if r["token"] == a.token]
    summary = a.study / "frontend/frontend.json"
    ident = load(summary)
    native_states = load(a.study / "native_states.json")
    state, = [r for r in native_states["samples"] if r["token"] == a.token]
    if (any(ident[k] != v for k,v in PIN.items()) or ident["registration_sha256"] != sha(registry)
            or native_states["frontend_sha256"] != sha(summary)
            or [r for r in ident["samples"] if r["token"] == a.token] != [sample]
            or not a.token == visual["token"] == context["token"] == context["model_context"]["sample_token"]
            or not sample["timestamp_us"] == case["reference_time_us"] == context["model_context"]["reference_time_us"]):
        raise ValueError("FRAME_OR_FRONTEND_BINDING_MISMATCH")
    gate = intervention_v2(sample, visual, state)
    poses, _ = native_body_knots(sample, state)
    second = a.study / "secondary_primary"
    srow, = [r for r in load(second/"sources.json")["cases"] if r["token"] == a.token]
    aux = second / (a.token + ".json")
    if srow["admitted"] != aux.is_file():
        raise ValueError("SECONDARY_SOURCE_MISMATCH")
    second_gate = None
    if srow["admitted"]:
        observed = load(aux)
        if (not srow["same_primary_detection_matches"] or srow["latest_exposure_minus_decision_us"] > 0
                or observed["token"] != a.token or observed["cameras"] != srow["current_cameras"]):
            raise ValueError("UNBOUND_SECONDARY_INPUT")
        second_gate = edge_clearance(poses, observed["features"])
    record = dict(token=a.token, scene=case["name"], native_sha256=sha(native_file), map_sha256=sha(visual_file),
                  secondary_sources_sha256=sha(second/"sources.json"),
                  secondary_input_sha256=sha(aux) if srow["admitted"] else None,
                  packet_sha256=sha(packet), policy="fast" if a.fast_only else "dual", gate=gate, secondary_gate=second_gate, decision=None,
                  returned_xy_lidar_m=sample["final_xy_lidar_m"], source="NATIVE", candidate=None)
    inputs = a.out / "inputs"
    slow = bool(gate["legacy_trigger"])
    primary = bool(gate["road_edge"]["triggered"])
    secondary = bool(second_gate and second_gate["triggered"])
    save(a.out / "gate.json", dict(native_trigger=slow, primary=primary, secondary=secondary))
    proposed = {}
    reviewed = None
    fast_return = getattr(a, "fast_return", None)
    if fast_return is not None:
        from cordriver_fast_review import fast_path, review_packet, review_edges, approaching_boundary
        baseline, fallback_candidate, fast_poses, _, _ = fast_path(a.study, a.token, fast_return)
        review_trigger,fast_primary,fast_secondary=review_edges(slow,fast_poses,visual["map_features"],
                                                                 observed["features"] if srow["admitted"] else None)
        use_approach=bool(getattr(a,"review_approach",False))
        approach=use_approach and approaching_boundary(fast_primary,fast_secondary)
        review_trigger=review_trigger or approach
        record.update(policy="review_fast_v1", guided_search=getattr(a,"guided_search",None) or "minimal",
                      fast_return_sha256=sha(fast_return), review_trigger=review_trigger,
                      approach_enabled=use_approach, approach_trigger=approach,
                      reviewed_primary=fast_primary, reviewed_secondary=fast_secondary,
                      source="FAST_DEFAULT", returned_xy_lidar_m=baseline["returned_xy_lidar_m"])
        if fallback_candidate is not None:
            copy = a.out / "default" / "candidate.json"
            copy.parent.mkdir()
            for item in (fallback_candidate, fallback_candidate.with_name("receipt.json")):
                shutil.copyfile(item, copy.parent / item.name)
            record.update(candidate=str(copy.relative_to(a.out)), candidate_sha256=sha(copy))
        if not review_trigger or fast_poses is None:
            record["reason"] = "NO_TRIGGER" if not review_trigger else "NO_BOUND_FAST_POSES"
            save(a.out/"return.json",record)
            return record
        reviewed = review_packet(a.study, a.token, fast_return, a.out / "review")
        record["review_packet_sha256"] = sha(reviewed)
        slow = True
    if slow and not a.fast_only:
        if a.frozen_response is not None:
            receipt, answer = bind_frozen(reviewed or packet, a.frozen_response)
            guidance = a.out
            record["response_sha256"] = receipt["native_output_sha256"]
            record["frozen_response_sha256"] = sha(a.frozen_response)
        else:
            guidance, answer = obtain(reviewed or packet, a.responses, a.out/"astra_live", a.live)
            record["response_sha256"] = sha(guidance/a.token/"native_output.json")
        record["decision"] = answer["decision"]
        if answer["decision"] == "ADJUST":
            prepare_planning_inputs(a, inputs, case, ident["qp_weight"])
            proposed = smooth.run_case((inputs, guidance, a.out, MODEL, case, a.data, ident["qp_weight"],
                                        True, False, False), guidance_packet=reviewed, fast_return=fast_return,
                                      frozen_response=a.frozen_response,guided_search=record.get("guided_search","original"))
            if proposed["status"] == "COMPUTATION_ERROR":
                raise RuntimeError(proposed.get("message"))
            record["source"] = "ASTRA_ADJUST" if proposed["selected_path"] else "NATIVE_ADJUST_UNRESOLVED"
        else:
            record["source"] = "NATIVE_STRICT_KEEP"
    elif slow or primary or secondary:
        prepare_planning_inputs(a, inputs, case, ident["qp_weight"])
        menu.build_portfolio(inputs, a.token, a.out/"menus", a.data)
        frame = dict(scene=case["name"], token=a.token, group="single_frame")
        if slow or primary:
            proposed = fast.work((0,frame,a.out/"refits",a.out/"menus"), study=inputs, data=a.data)
        else:
            from cordriver_secondary_refit import refit
            proposed = refit(a.out,dict(**frame,secondary_sha256=sha(aux),clearance=second_gate),inputs,a.data)
            if proposed["selected_path"]:
                xy=np.asarray(load(proposed["selected_path"])["body_xy_m"])[::2]
                direction=np.column_stack([np.cos(poses[:,2]),np.sin(poses[:,2])])
                magnitude=float(np.max(np.abs(np.sum((xy[1:] - poses[1:,:2])*direction[1:],axis=1))))
                record["max_secondary_progress_delta_m"] = magnitude
                if magnitude > .925:
                    proposed = {**proposed, "selected_path":None, "reason":"SECONDARY_PROGRESS_VETO"}
        record["source"] = "FAST_REFIT" if proposed["selected_path"] else "NATIVE_FAST_UNRESOLVED"
    if reviewed is not None and not proposed.get("selected_path"):
        record["source"] = "FAST_DEFAULT_KEEP" if answer["decision"] == "KEEP" else "FAST_DEFAULT_ADJUST_UNRESOLVED"
    if proposed:
        record["planner_status"] = proposed["status"]
        record["reason"] = proposed.get("reason") or proposed.get("reasons")
    if proposed.get("selected_path"):
        p = Path(proposed["selected_path"])
        if sha(p) != proposed["selected_sha256"] or load(p.with_name("receipt.json"))["sha256"] != sha(p):
            raise ValueError("CHANGED_RETURN")
        record.update(returned_xy_lidar_m=published_xy(p).tolist(), candidate=str(p.relative_to(a.out)),
                      candidate_sha256=sha(p))
    save(a.out/"return.json", record)
    print(record["scene"], a.token, record["source"], a.out/"return.json")
    return record


def checked_return(study, token, plan_path):
    r = load(plan_path)
    sf = study / "frontend" / token / "sample.json"
    if r["token"] != token or load(sf)["token"] != token or r["native_sha256"] != sha(sf):
        raise ValueError("SCORE_BINDING_MISMATCH")
    from cordriver_published_xy import published_xy
    if r.get("candidate"):
        candidate = plan_path.parent / r["candidate"]
        if plan_path.parent.resolve() not in candidate.resolve().parents:
            raise ValueError("CANDIDATE_OUTSIDE_RUN")
        expected = sha(candidate)
        receipt = load(candidate.with_name("receipt.json"))
        if expected != r["candidate_sha256"] or expected != receipt["sha256"] or not receipt["eligible_before_fixed_clearance"]:
            raise ValueError("UNBOUND_OR_UNRELEASED_CANDIDATE")
        points = published_xy(candidate).tolist()
    else:
        points = load(sf)["final_xy_lidar_m"]
    if points != r["returned_xy_lidar_m"]:
        raise ValueError("RETURN_COORDINATES_CHANGED")
    return load(sf)["final_xy_lidar_m"], points


def score(a):
    from cordriver_published_metrics import Published, rescore
    native, returned = checked_return(a.study, a.token, a.plan)
    published = Published(a.data)
    truth = published.inputs(a.token)
    result = dict(token=a.token, return_sha256=sha(a.plan), evaluation="PUBLISHED_NUSCENES_OPEN_LOOP")
    if truth is None:
        result["status"] = "UNAVAILABLE_EVALUATION_FUTURE"
    else:
        result["native"] = rescore(published,truth,native)
        result["dual"] = rescore(published,truth,returned)
    save(a.out,result)
    print(json.dumps(result))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    subs = ap.add_subparsers(dest="stage", required=True)
    for name in ("prepare","prepare-registered","plan","score"):
        sub = subs.add_parser(name)
        sub.add_argument("--study", required=True, type=Path)
        sub.add_argument("--token", required=name != "prepare-registered")
        sub.add_argument("--data", required=True, type=Path)
        if name in ("prepare", "prepare-registered"):
            sub.add_argument("--sparse",type=Path,help="Same-token SparseDrive stage-2 prediction export")
        else:
            sub.add_argument("--out",type=Path,required=True)
        if name == "plan":
            sub.add_argument("--fast-return",type=Path,help="Review the actual same-study fast-only return and retain it on KEEP or unresolved adjustment")
            sub.add_argument("--guided-search",choices=("original","verified","floor","minimal"),
                             help="Search with original stages, certified seeds, explicit floor, or extra minimal-deviation fit")
            sub.add_argument("--review-approach",action="store_true",help="Also review shrinking curb clearance supported by both map heads")
            group = sub.add_mutually_exclusive_group()
            group.add_argument("--fast-only",action="store_true",help="Run the paired deterministic fast planner without Astra")
            group.add_argument("--responses",type=Path,help="Archived per-token Responses records")
            group.add_argument("--frozen-response",type=Path,help="Public compact receipt, bound to the complete rebuilt request SHA")
            group.add_argument("--live",action="store_true",help="Allow at most one paid request on this token")
        if name == "score":
            sub.add_argument("--plan",type=Path,required=True,help="Frozen return.json from plan")
    a = ap.parse_args()
    if a.token is not None and not re.fullmatch(r"[a-f0-9]{32}",a.token):
        ap.error("expected one 32-character lowercase hexadecimal nuScenes sample token")
    for k,v in list(vars(a).items()):
        if isinstance(v,Path):
            setattr(a,k,v.expanduser().resolve())
    if getattr(a,"fast_only",False) and getattr(a,"fast_return",None):
        ap.error("--fast-return requires a dual-system run")
    if getattr(a,"guided_search",None) and not getattr(a,"fast_return",None):
        ap.error("--guided-search requires --fast-return")
    if getattr(a,"review_approach",False) and not getattr(a,"fast_return",None):
        ap.error("--review-approach requires --fast-return")
    if not __debug__:
        ap.error("Run without -O to retain upstream source-binding checks")
    {"prepare":prepare,"prepare-registered":prepare_registered,"plan":plan,"score":score}[a.stage](a)


if __name__ == "__main__":
    main()
