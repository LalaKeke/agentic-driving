"""Run the fixed six recorded references through the explicit OpenAI API route.

The new trial has no inherited calls. Twelve requests is this batch's operating
ceiling, not a limit on the user's separate unlimited-call authorization.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic.evidence_io import reserve_directory, write_json_new


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def passed(value):
    return (value.get('status') if isinstance(value, dict) else value) == 'PASS'


def transport_totals(folder):
    """Count actual POST attempts without inventing Codex RPC turns."""
    posts = 0
    usage = {key: 0 for key in ('input_tokens', 'output_tokens', 'total_tokens')}
    models = set()
    usage_present = False
    for path in sorted(folder.glob('transport/attempt_*/attempt_summary.json')):
        record = read(path)
        count = record.get('api_post_requests', 0)
        assert type(count) is int and 0 <= count <= 1, 'No transport retry is permitted'
        posts += count
        model = record.get('server_reported_model') or record.get('response_model')
        if model:
            models.add(model)
        values = record.get('usage') or {}
        for key in usage:
            value = values.get(key)
            if type(value) is int and value >= 0:
                usage[key] += value
                usage_present = True
    return dict(api_post_requests=posts, usage=usage if usage_present else None,
                server_reported_models=sorted(models))


def main(plan_path):
    plan_path = plan_path.resolve()
    plan = read(plan_path)
    root = plan_path.parent
    assert plan['backend'] == 'openai_api'
    assert plan['execution_authorized'] is True
    assert plan['user_call_limit'] is None and plan['user_authorization'] == 'unlimited requests'
    assert plan['operational_call_ceiling'] == 12 and plan['per_window_limit'] == 2
    assert plan['old_budget_inherited'] == 0
    assert plan['prompt_version'] == 'driving_prototype_v3'
    assert plan['planner_profile'] == 'bounded_seed_candidates_v2' and plan['require_turn'] is True
    # A separate recovery group may contain only the unresolved recorded times.
    # It never overwrites or silently retries an ended trial.
    assert 1 <= len(plan['cases']) <= 6
    assert len({case['sample_token'] for case in plan['cases']}) == len(plan['cases'])
    preflight = read(root / 'preflight.json')
    assert passed(preflight['models_access']) and passed(preflight['probes_validation'])
    assert os.environ.get('DRIVING_MODEL') == plan['requested_model'], 'Frozen API model differs from environment'
    runtime = reserve_directory(root / 'runtime')
    deadline = datetime.fromisoformat(plan['deadline_utc'].replace('Z', '+00:00')).timestamp()
    rows = [dict(case_id=case['case_id'], status='NOT_ATTEMPTED', charged_requests=0,
                 api_post_requests=0, native_outputs=0) for case in plan['cases']]
    stop = None
    started = time.monotonic()
    for index, case in enumerate(plan['cases']):
        for name, want in plan['source_sha256'].items():
            assert sha(ROOT / name) == want, 'Frozen source changed: ' + name
        capture = ROOT / case['capture']
        assert sha(capture) == case['capture_sha256'], 'Frozen capture changed'
        cap = read(capture)
        for frame in cap['observations']:
            assert sha(frame['path']) == case['image_sha256'][frame['id']], 'Frozen image changed'
        if time.time() >= deadline - 100:
            stop = 'WALL_LIMIT'
            break
        out = runtime / case['case_id']
        command = [str(ROOT / '.venv/bin/python'), str(ROOT / 'implementation/experiments/run_local_prototype.py'),
                   '--backend', 'openai_api', '--capture', str(capture), '--wire', str(ROOT / plan['wire_bundle']),
                   '--planner-profile', plan['planner_profile'], '--prompt-version', plan['prompt_version'],
                   '--require-turn', '--max-requests', '2', '--timeout-s', str(plan.get('timeout_s', 450)),
                   '--deadline-utc', plan['deadline_utc'], '--out', str(out)]
        if plan.get('api_preflight_models'):
            command.append('--api-preflight-models')
        write_json_new(runtime / (case['case_id'] + '.launch.json'),
                       dict(command=command, started_at_unix=time.time(), backend='openai_api',
                            automatic_transport_retries=0, credential_values_recorded=False))
        timed_out = False
        with (runtime / (case['case_id'] + '.console.txt')).open('x') as stream:
            try:
                process = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                         timeout=max(1, deadline - time.time()))
                exit_code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = None
        summary = read(out / 'summary.json') if (out / 'summary.json').is_file() else None
        count = len(list(out.glob('charge_*.json')))
        totals = transport_totals(out)
        assert totals['api_post_requests'] <= count <= 2
        assert sum(row['charged_requests'] for row in rows) + count <= plan['operational_call_ceiling']
        rows[index].update(totals, charged_requests=count, exit_code=exit_code,
                           status='CONDITIONAL_CHAIN_PASS' if summary and summary['prototype_conditional_success'] else 'NO_COMPLETE_CHAIN',
                           native_outputs=len(list(out.glob('attempt_*/record.json'))),
                           fatal=summary.get('fatal') if summary else 'MISSING_SUMMARY', timed_out=timed_out)
        write_json_new(runtime / (case['case_id'] + '.completed.json'), rows[index])
        print(json.dumps(rows[index], ensure_ascii=False), flush=True)
        if timed_out or not summary or summary.get('fatal') or exit_code not in (0, 3) or count == 0:
            stop = 'WALL_LIMIT' if timed_out else 'SERVICE_INTERFACE_OR_EXECUTION_FAILURE'
            break
    charged = sum(row['charged_requests'] for row in rows)
    write_json_new(root / 'batch_summary.json', dict(
        ended=True, backend='openai_api', charged_requests=charged,
        api_post_requests=sum(row['api_post_requests'] for row in rows),
        user_call_limit=None, user_authorization='unlimited requests',
        operational_call_ceiling=12, unused_operating_slots=12 - charged, old_budget_inherited=0,
        serial=True, automatic_transport_retries=0, cases=rows, stop_reason=stop,
        elapsed_s=time.monotonic() - started, actual_server_snapshot=None,
        recording_boundary=plan['recording_boundary']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    main(parser.parse_args().plan)
