"""Run the fixed clip plans with two independent serial API sessions at most."""
import argparse
import hashlib
import json
import signal
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic.evidence_io import write_json_new


def read(path):
    return json.loads(Path(path).read_text())


def main(plan_path):
    plan_path = plan_path.resolve()
    plan = read(plan_path)
    root = plan_path.parent
    assert plan['execution_authorized'] and plan['old_budget_inherited'] == 0
    assert plan['user_call_limit'] is None and plan['user_authorization'] == 'unlimited requests'
    parallel = plan['max_parallel_clips']
    assert type(parallel) is int and parallel in (1, 2) and plan['serial_within_clip'] is True
    assert plan['operational_call_ceiling'] == sum(row.get('operational_call_ceiling', 2 * row['reference_count']) for row in plan['clips'])
    for name, expected in plan['source_sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    stop_file = root / 'STOP_GENERATION.json'
    def interrupted(signum, frame):
        try:
            with stop_file.open('x') as stream:
                json.dump(dict(reason='PARENT_SIGNAL_RECEIVED', signal_number=signum,
                               at_unix=time.time()), stream)
        except FileExistsError:
            pass
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    write_json_new(root / 'launch.json', dict(
        plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        max_parallel_clips=parallel, serial_within_clip=True, automatic_transport_retries=0,
        credential_values_recorded=False, started_at_unix=time.time()))
    pending = list(plan['clips'])
    rows = {row['clip_id']: dict(clip_id=row['clip_id'], status='NOT_ATTEMPTED',
                               charged_requests=0, api_post_requests=0) for row in pending}
    active = {}
    started = time.monotonic()
    while pending or active:
        while pending and len(active) < parallel and not stop_file.exists():
            clip = pending.pop(0)
            child_plan = ROOT / clip['plan']
            assert hashlib.sha256(child_plan.read_bytes()).hexdigest() == clip['plan_sha256']
            command = [str(ROOT / '.venv/bin/python'),
                       str(ROOT / 'implementation/experiments/run_openai_clip.py'),
                       '--plan', str(child_plan), '--stop-file', str(stop_file)]
            log = (root / (clip['clip_id'] + '.console.txt')).open('x')
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            active[clip['clip_id']] = (process, log, child_plan.parent)
            rows[clip['clip_id']]['status'] = 'RUNNING'
            write_json_new(root / (clip['clip_id'] + '.launch.json'), dict(command=command))
            print(json.dumps(dict(event='CLIP_STARTED', clip_id=clip['clip_id'])), flush=True)
        for clip_id, (process, log, folder) in list(active.items()):
            if process.poll() is None:
                continue
            log.close()
            path = folder / 'batch_summary.json'
            result = read(path) if path.is_file() else None
            if not result or result.get('fatal') or result.get('stop_reason'):
                if not stop_file.exists():
                    write_json_new(stop_file, dict(clip_id=clip_id,
                        reason=(result or {}).get('stop_reason') or 'MISSING_CLIP_SUMMARY'))
            cases = (result or {}).get('cases', [])
            rows[clip_id].update(
                status='FINISHED' if result and result.get('stop_reason') is None else 'STOPPED',
                exit_code=process.returncode,
                charged_requests=(result or {}).get('charged_requests', 0),
                api_post_requests=(result or {}).get('api_post_requests', 0),
                conditional_chains=sum(case['status'] == 'CONDITIONAL_CHAIN_PASS' for case in cases),
                reference_count=len(read(folder / 'plan.json')['cases']),
                stop_reason=(result or {}).get('stop_reason'), fatal=(result or {}).get('fatal'))
            del active[clip_id]
            write_json_new(root / (clip_id + '.completed.json'), rows[clip_id])
            print(json.dumps(rows[clip_id], ensure_ascii=False), flush=True)
        if stop_file.exists() and not active:
            break
        if active:
            time.sleep(1)
    total = sum(row['charged_requests'] for row in rows.values())
    assert total <= plan['operational_call_ceiling']
    write_json_new(root / 'batch_summary.json', dict(
        ended=True, clips=list(rows.values()), charged_requests=total,
        api_post_requests=sum(row['api_post_requests'] for row in rows.values()),
        operational_call_ceiling=plan['operational_call_ceiling'], old_budget_inherited=0,
        max_parallel_clips=parallel, serial_within_clip=True, automatic_transport_retries=0,
        elapsed_s=time.monotonic() - started,
        stop=read(stop_file) if stop_file.exists() else None))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    main(parser.parse_args().plan)
