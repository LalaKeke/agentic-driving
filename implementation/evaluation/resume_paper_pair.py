"""Start a new serial transport batch, preserving the frozen shared forward and every prior attempt."""
import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from cordriver_paper_pair import HERE, atomic_json, index, load, process, sha256
from cordriver_guidance_wire import ENDPOINT, MODEL

CAP = 5119


def joint_ledgers(root):
    return [root / "astra", *sorted((root / "batches").glob("*/astra"))]


def publish(root, batch, token):
    src = batch / "returns" / (token + ".json")
    if not src.exists():
        return
    row = load(src)
    if row["token"] != token:
        raise ValueError("Return token mismatch")
    for key in ("candidate", "plan_result"):
        if key in row:
            local = (batch / row[key]).resolve(strict=True)
            row[key] = str(local.relative_to(root))
    row["source_batch"] = batch.name
    row["source_return_sha256"] = sha256(src)
    dst = root / "returns" / (token + ".json")
    if dst.exists() and load(dst) != row:
        raise ValueError("Another return already exists: " + token)
    atomic_json(dst, row)


def preflight():
    import httpx
    from urllib.parse import urlsplit
    proxy = urlsplit(os.environ["HTTPS_PROXY"])
    if proxy.hostname != "127.0.0.1" or ENDPOINT != "https://api.openai.com/v1/responses":
        raise ValueError("Unexpected local route or API endpoint")
    with httpx.Client(timeout=12, follow_redirects=False) as c:
        resp = c.get("https://api.openai.com/v1/models")
    if resp.status_code != 401 or not resp.headers.get("content-type", "").startswith("application/json"):
        raise RuntimeError("Official API unavailable on the configured route")
    return dict(http=resp.status_code, proxy_port=proxy.port, request_id=resp.headers.get("x-request-id"))

def verify_sources(root, infos):
    reg = load(root / "registration.json")
    sources = reg["sources"].copy()
    if list(infos) != reg["info_valid_tokens"]:
        raise ValueError("Unexpected split")
    for record, key in (("image_binding_addendum", "cordriver_smooth_planner.py"),
                        ("transport_addendum", "cordriver_guidance_wire.py")):
        amendment = load(root / "recovery" / (record + ".json"))
        if (sha256(root / "registration.json") != amendment["registration_sha256"] or
                set(amendment["new"]) != {key} or amendment["old"] != {key:reg["sources"][key]}):
            raise ValueError("Unexpected experiment registration")
        sources.update(amendment["new"])
    if any(sha256(HERE / name) != digest for name, digest in sources.items()):
        raise ValueError("Frozen split or algorithm changed")


def replay_answer(root, prior_batch, token):
    from cordriver_guidance_wire import obtain
    from cordriver_paper_pair import unpack, returned, DATA
    from cordriver_smooth_planner import run_case
    if not prior_batch.isalnum() or token not in index():
        raise ValueError("Invalid batch or token")
    verify_sources(root, index())
    batch = root / "batches" / prior_batch
    case = batch / "cases" / token
    packet = case / "review/packet.json"
    ledger = batch / "astra"
    _, answer = obtain(packet, ledger, None, False)
    frame, digest = unpack(root,token)
    if not frame["gate"]["consider_guidance"] or answer["decision"] != "ADJUST":
        raise ValueError("Expected an eligible archived ADJUST")
    spec, = load(case / "study/selection/registration.json")["cases"]
    dest = case / "solve_image_binding_v2"
    result = run_case((case / "solve/inputs", ledger, dest, MODEL, spec,
                       DATA, frame["qp_weight"], True, False, False), guidance_packet=packet,
                      fast_return=case / "native.json", guided_search="minimal")
    if result["status"] == "COMPUTATION_ERROR":
        raise RuntimeError(str(result))
    path = Path(result["selected_path"]) if result["selected_path"] else None
    returned(batch,token,frame["sample"],"ASTRA_ADJUST" if path else "ASTRA_NO_RELEASE_NATIVE",
             digest, dest / "planning/astra" / token / "result.json",path)
    publish(root, batch, token)


def run(root, batch_id, exception, after):
    root = root.resolve()
    infos = index()
    verify_sources(root, infos)
    if not batch_id.isalnum():
        raise ValueError("Invalid batch ID")
    with (root / "dispatch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX if after else fcntl.LOCK_EX | fcntl.LOCK_NB)
        if after and (not after.isalnum() or not (root / "batches" / after / "slow_complete.json").is_file()):
            raise RuntimeError("Preceding batch stopped or incomplete; no dispatch")
        probe = preflight()
        charged = [s.parent.name for ledger in joint_ledgers(root) for s in ledger.glob("*/precharge.json")]
        # An incomplete on-disk ledger also prevents another dispatch.
        reserved = {d.name for ledger in joint_ledgers(root) if ledger.exists() for d in ledger.iterdir() if d.is_dir()}
        if exception:
            previous = [ledger / exception for ledger in joint_ledgers(root) if (ledger / exception).exists()]
            if (len(previous) != 1 or charged.count(exception) != 1 or
                    load(previous[0] / "receipt.json")["status"] != "STOP" or
                    (root / "returns" / (exception + ".json")).exists()):
                raise ValueError("Exception only valid for a single previously stopped token")
        batch = root / "batches" / batch_id
        batch.mkdir(parents=True, exist_ok=False)
        (batch / "shared01").symlink_to(root / "shared01", target_is_directory=True)
        selected = [t for t in infos if (t not in reserved or t == exception) and
                    not (root / "returns" / (t + ".json")).exists()]
        if exception:
            selected = [exception]
        # Conservatively bound even a trigger on every yet undecided frame.
        if len(charged) + len(selected) > CAP:
            raise ValueError("Batch exceeds global attempt budget")
        atomic_json(batch / "registration.json", dict(split_sha256=sha256(root / "registration.json"),
                    driver_sha256=sha256(Path(__file__)), preflight=probe, model=MODEL,
                    single_authorized_exception=exception, previously_reserved=sorted(reserved),
                    previously_charged=len(charged), global_cap=CAP, eligible_order=selected,
                    stop="service failure stops this batch, with no automatic retry"))
        class Batch:
            def __len__(self):
                return len(selected)

            def items(self):
                for t in selected:
                    if (root / "returns" / (t + ".json")).exists():
                        continue
                    # Only a successful prior packet comparison can authorize identical-wire recovery.
                    if t == exception:
                        from cordriver_paper_pair import unpack, prepare_case
                        from cordriver_guidance_wire import build
                        frame, digest = unpack(batch, t)
                        packet = prepare_case(batch, t, infos[t], frame, digest)
                        if packet is None or build(packet)[1] != (previous[0] / "request.json").read_bytes():
                            raise ValueError("Exception must have the exact original wire bytes")
                    yield t, infos[t]
                    publish(root, batch, t)
        try:
            process(batch, Batch(), "live")
        except BaseException as exc:
            atomic_json(batch / "stop.json", dict(error=type(exc).__name__, detail=str(exc)[:1500],
                        time_unix=time.time()))
            raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--authorized-exception", help="only for a token-specific explicit one-time user authorization")
    ap.add_argument("--after", help="wait for the active dispatch lock, require the named batch to have completed")
    ap.add_argument("--offline-replay", help="replan one archived ADJUST; no network access")
    a = ap.parse_args()
    if a.offline_replay:
        replay_answer(a.root.resolve(), a.batch, a.offline_replay)
    else:
        run(a.root, a.batch, a.authorized_exception, a.after)
