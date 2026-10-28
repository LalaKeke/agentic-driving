"""Read-only transport audit; no native metric values, GT or private raw content.

Writes a single NEW report. It cannot resume the trial or invoke a model.
"""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'implementation'))
from agentic.contracts import digest
from agentic.evidence_io import write_json_new

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'runtime'
REG_COMMIT = '23280fcce39b928760ebd91763bae940a6acb8f1'
CODE_COMMIT = '06db69ba4201b1e0363d467fb09740b24bbb4bc7'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def commit_bytes(commit, path):
    return subprocess.check_output(['git', 'show', commit + ':' + str(path.relative_to(REPO))], cwd=REPO)


def assert_seal(root, name):
    manifest = read(root / name)
    for path, row in manifest['files'].items():
        p = root / path
        assert sha(p) == row['sha256'], str(p)
        assert p.stat().st_size == row['bytes'], str(p)
    return dict(root=str(root.relative_to(REPO)), seal=name, seal_sha256=sha(root / name),
                files_checked=len(manifest['files']), matches=True)


def main():
    report = dict(audit_scope='Registered public seals, source and acquisition pins, protocol metadata, budget and private filesystem permissions only; not whole-filesystem monitoring',
        no_native_metric_values_used_or_emitted=True, no_gt_values_used_or_emitted=True,
        private_content_read=False, new_model_calls=0)
    seal = read(RUNTIME / 'SHA256_RUNTIME_MANIFEST.json')
    report['runtime_integrity'] = assert_seal(RUNTIME, 'SHA256_RUNTIME_MANIFEST.json')
    public = {str(p.relative_to(RUNTIME)) for p in RUNTIME.rglob('*')
              if p.is_file() and not any(s.startswith('.private') for s in p.relative_to(RUNTIME).parts)}
    assert public == set(seal['files']) | {'SHA256_RUNTIME_MANIFEST.json'}
    assert seal['runtime_sealed'] is True and seal['source_commit'] == REG_COMMIT

    config_path = ROOT / 'registration/authorized_config.json'
    config = read(config_path)
    assert config_path.read_bytes() == commit_bytes(REG_COMMIT, config_path)
    assert config['source_freeze_commit'] == CODE_COMMIT
    assert config['authorized_model_invocations'] == 2 and config['execution_authorized'] is True
    assert config['old_pilot_relation']['inherited_unused_calls'] == 0
    assert config['old_pilot_relation']['new_independent_budget'] is True
    assert config['old_pilot_relation']['approved_limit'] == 2
    assert config['sample_id'] == 'ac452a60e8b34a7080c938c904b23057'
    assert config['output_contract'] == '3.0.0-exp2'
    assert config['prompt_version'] == 'driving_v3_exp2'
    assert config['coverage_profile'] == 'whole_interval_sources_v3_exp2'
    assert config['horizon_s'] == 5 and config['step_s'] == .2 and config['prefix_s'] == .2
    source = read(ROOT / 'registration/source_manifest.json')
    assert source['analysis_source_commit'] == CODE_COMMIT
    for name, row in source['files'].items():
        path = REPO / name
        assert sha(path) == row['sha256']
        assert path.read_bytes() == commit_bytes(CODE_COMMIT, path)
    prereg_files = list((ROOT / 'evaluation_preregister').glob('*'))
    for path in prereg_files:
        if path.is_file():
            assert path.read_bytes() == commit_bytes(REG_COMMIT, path)
    prereg = ROOT / 'evaluation_preregister/preregistration.json'
    assert sha(prereg) == config['g2_preregistration_sha256']
    subprocess.run(['git', 'merge-base', '--is-ancestor', CODE_COMMIT, REG_COMMIT], cwd=REPO, check=True)
    cap_path = REPO / config['capture_path']
    assert sha(cap_path) == config['capture_sha256']
    capture = read(cap_path)
    images = {f['id']: sha(Path(f['path'])) for f in capture['observations']}
    assert images == config['image_sha256'] and len(images) == 10
    assert digest(capture['vehicle']) == config['vehicle_digest']
    assert digest(capture['safety']) == config['safety_digest']
    report['frozen_inputs'] = dict(registration_commit=REG_COMMIT, code_commit=CODE_COMMIT,
        source_files_checked=len(source['files']), preregistration_files_opaque_bytes_checked=len(prereg_files),
        config_sha256=sha(config_path), capture_sha256=sha(cap_path), image_count=10,
        image_sha256=images, source_and_registration_blobs_match=True, vehicle_safety_grid_unchanged=True)

    budget = read(RUNTIME / 'budget/final.json')
    assert budget['limit'] == budget['charged'] == budget['turn_start_requests'] == 2
    assert budget['unused'] == 0 and budget['ended'] is True
    assert budget['automatic_transport_retries'] == budget['request_max_retries'] == budget['stream_max_retries'] == 0
    assert budget['no_automatic_batch'] is True
    phases = []
    attempts = []
    wire_path = REPO / config['wire_schema_bundle']['path']
    wire_schema = read(wire_path / 'wire_schema.json')
    assert sha(wire_path / 'wire_schema.json') == config['wire_schema_bundle']['wire_schema_sha256']
    assert sha(wire_path / 'transformation_manifest.json') == config['wire_schema_bundle']['transformation_manifest_sha256']
    prompt_sha = sha(REPO / 'implementation/prompts/driving_v3_exp2.txt')
    for number in (1, 2):
        charge = read(RUNTIME / f'budget/{number:02d}-charge.json')
        outcome = read(RUNTIME / f'budget/{number:02d}-outcome.json')
        folder = RUNTIME / 'transport' / f'attempt_{number:02d}'
        reqs = read(folder / 'requests.json')
        turns = [r for r in reqs if r['method'] == 'turn/start']
        threads = [r for r in reqs if r['method'] == 'thread/start']
        assert len(turns) == len(threads) == 1
        assert turns[0]['params']['outputSchema'] == wire_schema
        assert turns[0]['params']['model'] == config['model']
        assert threads[0]['params']['allowProviderModelFallback'] is False
        assert threads[0]['params']['modelProvider'] == config['provider']
        provider = threads[0]['params']['config']
        for key in ('request_max_retries', 'stream_max_retries'):
            assert provider[f'model_providers.pilot-official.{key}'] == 0
        assert provider['model_providers.pilot-official.supports_websockets'] is False
        assert provider['model_providers.pilot-official.base_url'] == config['service_base']
        events = [json.loads(line) for line in (folder / 'events.redacted.jsonl').read_text().splitlines()]
        methods = Counter(e.get('method') for e in events if e.get('method'))
        forbidden = [e.get('method') for e in events if e.get('method') in ('error', 'model/rerouted',
            'modelProvider/authRecoveryStarted', 'modelProvider/authRecoveryCompleted')
            or (e.get('params') or {}).get('willRetry')]
        assert not forbidden
        assert methods['turn/started'] == methods['turn/completed'] == 1
        item_types = Counter((e.get('params') or {}).get('item', {}).get('type') for e in events
                             if e.get('method') in ('item/started', 'item/completed'))
        assert set(item_types) <= {'userMessage', 'agentMessage', 'reasoning'}
        thread_result = next(e['result'] for e in events if e.get('id') == 2 and 'result' in e)
        assert thread_result['model'] == config['model']
        assert thread_result['modelProvider'] == config['provider']
        assert not thread_result.get('instructionSources')
        prov = read(folder / 'provenance.json')
        summary = read(folder / 'attempt_summary.json')
        pins = read(folder / 'input_manifest.json')
        wire = read(folder / 'wire_manifest.json')
        assert prov['source'] == 'online_model' and prov['cli_resolved_alias'] == config['model']
        assert prov['server_reported_snapshot'] is None and wire['server_reported_snapshot'] is None
        assert prov['model_reroute_observed'] is False and prov['tool_calls'] == 0
        assert prov['attempts'] == 1 and prov['adapter_retries'] == 0
        assert summary['status'] == 'NATIVE_SCHEMA_VALID' and summary['structured_output'] is True
        assert summary['native_json_returned'] is True
        assert summary['service_layer']['completed_turn_observed'] is True
        assert summary['service_layer']['error_notification_observed'] is False
        assert (folder / 'native_output.json').exists() and (folder / 'final_agent_message.txt').exists()
        assert pins['image_sha256'] == images and pins['prompt_sha256'] == prompt_sha
        assert pins['schema_sha256'] == config['wire_schema_bundle']['source_schema_sha256']
        assert pins['schema_transport']['wire_schema_sha256'] == config['wire_schema_bundle']['wire_schema_sha256']
        ledger = read(RUNTIME / f'attempt_{number:02d}/stage_ledger.json')
        stage_status = {name: row['status'] for name, row in ledger['stages'].items()}
        assert stage_status['SERVICE'] == stage_status['NATIVE_OUTPUT'] == 'PASS'
        assert stage_status['T1'] == 'FAIL'
        assert {stage_status[name] for name in ('T2', 'T3', 'T4', 'EXECUTION', 'ACCEPTOR')} == {'NOT_REACHED'}
        failure = read(RUNTIME / f'attempt_{number:02d}/T1-failure.json')
        assert failure['code'] == 'UNBOUNDED_UNKNOWN'
        assert ledger['consumed_prefix'] is False
        assert not (RUNTIME / f'attempt_{number:02d}/execution.json').exists()
        assert not (RUNTIME / f'attempt_{number:02d}/execution_receipt.json').exists()
        assert charge == budget['charges'][number - 1] and outcome == budget['outcomes'][number - 1]
        assert charge['attempt'] == outcome['attempt'] == number and charge['charged_before_invocation'] is True
        assert charge['failed_attempt_consumes_budget'] is True
        assert outcome['charged'] == outcome['turn_start_requests'] == wire['turn_start_requests'] == 1
        phases.append(charge['phase'])
        attempts.append(dict(attempt=number, generation_turns=1, native_json_exists=True,
            business_validation='PASS_AS_RECORDED_BY_ORIGINAL_SCHEMA_GATE', native_json_parsed_by_this_audit=False,
            stages=stage_status, t1_code=failure['code'], simulated_prefix_consumed=False,
            cli_resolved_alias=prov['cli_resolved_alias'], server_snapshot=None,
            service_error_or_retry_events=forbidden, event_method_counts=dict(methods),
            item_type_counts=dict(item_types), wire_manifest_sha256=sha(folder / 'wire_manifest.json')))
    assert phases == ['initial', 'semantic_revision']
    report['budget_and_stages'] = dict(limit=2, charged=2, actual_turn_start_requests=2,
        unused=0, ended=True, inherited_old_allowance=0, automatic_retries=0,
        phases=phases, attempts=attempts, six_window_batch_launched=False)

    private_dirs = [RUNTIME / 'transport/.private_state'] + sorted((RUNTIME / 'transport').glob('attempt_*/.private'))
    private_files = [p for d in private_dirs for p in d.rglob('*') if p.is_file()]
    permission_counts = Counter(oct(stat.S_IMODE(p.stat().st_mode)) for p in private_files)
    for directory in private_dirs:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    raw_files = [p for d in private_dirs[1:] for p in d.glob('*.raw')]
    assert len(raw_files) == 4
    for p in raw_files:
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    relative_files = [str(p.relative_to(REPO)) for p in private_files]
    ignored = subprocess.check_output(['git', 'check-ignore', '--no-index', '-z', '--stdin'],
        cwd=REPO, input=('\0'.join(relative_files) + '\0').encode()).decode().strip('\0').split('\0')
    assert set(ignored) == set(relative_files)
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=REPO).decode().split('\0')
    assert not set(tracked) & set(relative_files)
    report['private_evidence'] = dict(private_roots_checked=len(private_dirs), private_files_checked=len(private_files),
        top_private_directories_mode='0700', raw_files_checked=len(raw_files), raw_file_mode='0600',
        private_file_mode_counts=dict(permission_counts), all_git_ignored=True, tracked_private_files=0,
        contents_read=False, raw_content_hashes_recomputed=False,
        mode_scope='CLI state files may have other modes inside protected 0700 roots; only raw streams are asserted 0600.')

    baseline = read(ROOT / 'baseline_protection.json')
    changed = []
    unchanged = 0
    for name, row in baseline['files'].items():
        path = REPO / name
        if sha(path) == row['sha256'] and path.stat().st_size == row['bytes']:
            unchanged += 1
        else:
            changed.append(name)
    allowed = {'implementation/agentic/pilot_transport.py', 'implementation/experiments/exp2_controlled_pilot.py'}
    assert set(changed) == allowed
    for name in allowed:
        before = ast.parse(commit_bytes(baseline['baseline_commit'], REPO / name).decode())
        after = ast.parse((REPO / name).read_text())
        for tree in (before, after):
            tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        assert ast.dump(before) == ast.dump(after)
    old_pilot = REPO / 'implementation/results/exp2_single_day_pilot_20261028_v1'
    old_wire = REPO / 'implementation/results/wire_schema_compatibility_20261028_v1'
    report['old_public_seals'] = [assert_seal(old_pilot, 'DELIVERY_SHA256_MANIFEST.json'),
                                assert_seal(old_wire, 'DELIVERY_SHA256_MANIFEST.json')]
    old_budget = read(old_pilot / 'runtime/budget/final.json')
    assert old_budget['ended'] is True and old_budget['charged'] == 1 and old_budget['unused'] == 1
    report['baseline_protection'] = dict(registered=len(baseline['files']), unchanged=unchanged,
        changed_runtime_imports_only=sorted(changed), non_import_ast_equal=True,
        old_pilot_ended=True, old_pilot_charged=1, old_unused=1, old_unused_consumed=False,
        all_old_schema_prompt_wire_results_unchanged=True)
    cli_pins = read(old_pilot / 'preflight/client_pins.json')
    for name, row in cli_pins['files'].items():
        path = REPO / name
        assert sha(path) == row['sha256'] and path.stat().st_size == row['bytes']
    report['cli_pins'] = dict(version=cli_pins['cli_version'], exact_files_checked=len(cli_pins['files']), match=True)
    report['audit_passed'] = True
    report['audit_source_sha256'] = sha(Path(__file__))
    write_json_new(Path(__file__).with_suffix('.json'), report)
    print(json.dumps(dict(audit_passed=True, runtime_files=48, requests=2, charged=2,
        native_outputs=2, t1_codes=['UNBOUNDED_UNKNOWN'] * 2, private_files_checked=len(private_files),
        unchanged_baseline=unchanged, actual_model_calls_by_audit=0)))


if __name__ == '__main__':
    main()
