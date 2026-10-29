"""Small, fixed real-output regression set. Offline only; never calls an agent."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
sys.path.insert(0, str(Path(__file__).parent))
from agentic.contracts import digest
from agentic.evidence_io import reserve_directory, write_json_new
from run_local_prototype import physical

R = ROOT / 'implementation/results'
SCOUT = R / 'six_scene_scout_run_20261029_v1/runtime'
VARIED = R / 'varied_prototypes_2s_20261028_v1'
CASES = {
    'bus': R / 'moving_prototype_2s_20261028_v1/runtime/attempt_02/record.json',
    'parking': VARIED / 'parking_bend/runtime/attempt_02/record.json',
    'construction_saved': VARIED / 'construction_yield/runtime/attempt_03/record.json',
    'construction_new': R / 'construction_recovery_20261029_v1/live_construction/attempt_01/record.json',
    'straight_initial': SCOUT / 'ordinary_straight/attempt_01/record.json',
    'straight_revision': SCOUT / 'ordinary_straight/attempt_02/record.json',
    'deceleration_initial': SCOUT / 'following_deceleration/attempt_01/record.json',
    'deceleration_revision': SCOUT / 'following_deceleration/attempt_02/record.json',
    'signal_initial': SCOUT / 'signal_wait/attempt_01/record.json',
}


def offline_only(*args, **kwargs):
    raise RuntimeError('OFFLINE_REPLAY: network connections prohibited')


def main(args):
    socket.socket.connect = offline_only
    socket.socket.connect_ex = offline_only
    socket.create_connection = offline_only
    root = reserve_directory(args.out)
    source_files = [ROOT/'implementation/agentic'/f'{n}.py' for n in
                    ('t1','t2','t3','t4','metric_contract','geometry','candidate_search','acceptance','runtime')]
    source_files.append(Path(__file__))
    source_files += list((ROOT/'implementation/schemas').glob('*seed_candidates_v*.schema.json'))
    source = dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
                  sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files})
    write_json_new(root/'source.json',source)
    rows = []
    for name in args.cases or CASES:
        path = CASES[name]
        record = json.loads(path.read_text())
        cap, out, prov = (record[k] for k in ('capture', 'output', 'provenance'))
        folder = reserve_directory(root / name)
        result, failure, stages = physical(cap, out, prov, cap, 0, folder, policy=args.profile)
        row = dict(case=name, sample_id=cap['metadata']['scene_id'], record=str(path.relative_to(ROOT)),
                   record_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   output_digest=digest(out), capture_digest=digest(cap), stages=stages,
                   failure=failure, mode='SAVED_REAL_OUTPUT_OFFLINE_REPLANNING', new_model_calls=0,
                   prototype_conditional_success=bool(result and result['prototype_conditional_success']))
        ledger_path = folder / 'candidate_ledger.json'
        if ledger_path.exists():
            ledger = json.loads(ledger_path.read_text())
            row['candidate_counts'] = {n: sum(c[n]=='PASS' for c in ledger['candidates']) for n in ('T2','T3','T4')}
            row['selected_candidate'] = ledger['selected_candidate']
        if result:
            result['fresh_online_model_call'] = False
            result['replay_of_real_response'] = str(path)
            write_json_new(folder / 'round_result.json', result)
            row.update(execution=result['execution'], motion_class=result['acceptance']['motion_class'],
                       min_clearance_m=result['verification']['minimum_clearance_lower_bound_m'],
                       solver=result['trajectory']['solver'],
                       max_abs_acceleration_mps2=max(abs(p['acceleration_mps2']) for p in result['trajectory']['points']))
        rows.append(row)
        print(json.dumps({k: row.get(k) for k in ('case','stages','candidate_counts','prototype_conditional_success')}, ensure_ascii=False), flush=True)
    write_json_new(root / 'summary.json', dict(
        profile=args.profile, source_commit=source['commit'], code_dirty=source['dirty'],
        new_model_calls=0, network_connections_prohibited=True, unique_windows=len({r['sample_id'] for r in rows}),
        responses=len(rows), rows=rows,
        claim='Fixed saved real outputs, offline comparison only; no online success-rate or GT accuracy claim.'))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--profile', choices=['bounded_seed_candidates_v1','bounded_seed_candidates_v2'], required=True)
    p.add_argument('--cases', nargs='+', choices=list(CASES))
    main(p.parse_args())
