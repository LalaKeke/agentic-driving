"""A recorded clip through the existing prototype, sharing one real API client.

Each reference remains an independent observation, decision, plan and simulated
prefix. Only the HTTP connection is shared; no previous observation, answer or
simulated ego state is carried into the next reference. No automatic retries.
"""
import argparse
from copy import deepcopy
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.contracts import Failure, need, validate
from agentic.evidence_io import reserve_directory, write_json_new
from agentic.openai_adapter import OpenAIResponsesAgent
from run_local_prototype import PROFILE, body_feedback, physical
from run_openai_bend_sequence import transport_totals


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_check(plan):
    for name, expected in plan['source_sha256'].items():
        need(sha(ROOT / name) == expected, 'CLIP_SOURCE_CHANGED',
             'Frozen clip source changed; no new request sent', path=name)


def capture_check(case):
    path = ROOT / case['capture']
    need(sha(path) == case['capture_sha256'], 'CLIP_CAPTURE_CHANGED',
         'Frozen capture changed; no new request sent', case_id=case['case_id'])
    capture = read(path)
    validate('capture_v2.schema.json', capture)
    need(capture['metadata']['scene_id'] == 'nusc_' + case['sample_token'],
         'CLIP_WINDOW_MISMATCH', 'Capture differs from the fixed sample')
    need(capture['metadata']['horizon_s'] == 2 and capture['metadata']['step_s'] == .2
         and capture['safety']['prefix_s'] == .2,
         'CLIP_CONFIG', 'This recorded prototype retains 2s/.2s/.2s')
    for image in capture['observations']:
        need(sha(image['path']) == case['image_sha256'][image['id']],
             'CLIP_IMAGE_CHANGED', 'Frozen image changed; no new request sent', image_id=image['id'])
    return capture


def request_stop(stop_file, case_id, failure):
    """Signal other clip processes before they send another generating request."""
    if stop_file is None:
        return
    record = dict(case_id=case_id, failure_code=failure.get('code'),
                  source='clip_runner', model_retries=0, at_unix=time.time())
    try:
        with stop_file.open('x') as stream:
            json.dump(record, stream)
            stream.write('\n')
    except FileExistsError:
        pass


def alias_transport(case_root, global_root, local_attempt, global_attempt):
    """A read-only pathname alias; the original response remains global evidence."""
    actual = global_root / f'attempt_{global_attempt:02d}'
    alias = case_root / 'transport' / f'attempt_{local_attempt:02d}'
    exists = actual.is_dir()
    if exists:
        alias.symlink_to(os.path.relpath(actual, alias.parent), target_is_directory=True)
    return dict(local_attempt=local_attempt, global_attempt=global_attempt,
                local_alias=str(alias.relative_to(case_root)),
                global_evidence=str(actual.relative_to(global_root.parent)),
                relative_symlink=os.path.relpath(actual, alias.parent) if exists else None,
                alias_created=exists, originals_rewritten=False)


def run(plan_path, stop_file=None):
    plan_path = Path(plan_path).resolve()
    plan = read(plan_path)
    root = plan_path.parent
    if stop_file is not None:
        stop_file = Path(stop_file).resolve()
        need(stop_file.parent.is_dir(), 'CLIP_STOP_PATH', 'Shared stop marker parent must already exist')
    cases = plan['cases']
    need(plan['execution_authorized'] is True and plan['old_budget_inherited'] == 0,
         'CLIP_AUTHORIZATION', 'A new explicitly authorized clip plan is required')
    need(plan['backend'] == 'openai_api' and plan['prompt_version'] == 'driving_prototype_v3'
         and plan['planner_profile'] == 'bounded_seed_candidates_v2',
         'CLIP_CONFIG', 'Use the existing explicit API/v3/prototype planner')
    need(isinstance(cases, list) and len(cases) > 0
         and len({case['case_id'] for case in cases}) == len(cases)
         and len({case['sample_token'] for case in cases}) == len(cases),
         'CLIP_CASES', 'Fixed nonempty unique case and sample identities required')
    need(plan['per_window_limit'] == 2, 'CLIP_BUDGET', 'At most one targeted revision per reference')
    limit = 2 * len(cases)
    need(plan['operational_call_ceiling'] == limit, 'CLIP_BUDGET',
         'Clip operation ceiling must equal two requests per fixed reference')
    need(os.environ.get('DRIVING_MODEL') == plan['requested_model'],
         'CLIP_MODEL', 'The environment differs from the registered API model')
    source_check(plan)
    # Reserve the roots before reading credentials or constructing any client.
    runtime = reserve_directory(root / 'runtime')
    budget_root = reserve_directory(root / 'budget')
    deadline = datetime.fromisoformat(plan['deadline_utc'].replace('Z', '+00:00')).timestamp()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    write_json_new(root / 'config.json', dict(
        backend='openai_api', source_commit=commit, registered_plan_sha256=sha(plan_path),
        one_api_client_for_clip=True, per_window_conversation_state=False,
        automatic_transport_retries=0, operational_call_ceiling=limit,
        per_window_limit=2, source_sha256=plan['source_sha256']))
    rows = [dict(case_id=case['case_id'], status='NOT_ATTEMPTED', charged_requests=0,
                 api_post_requests=0, native_outputs=0) for case in cases]
    charges = []
    stop = None
    fatal = None
    agent = None
    started = time.monotonic()
    current_case = None
    try:
        if stop_file is not None and stop_file.exists():
            stop = 'EXTERNAL_STOP_BEFORE_PREFLIGHT'
        elif time.time() >= deadline - 100:
            stop = 'WALL_LIMIT'
        else:
            agent = OpenAIResponsesAgent(
                evidence_dir=root / 'transport', model=plan['requested_model'],
                timeout_s=plan.get('timeout_s', 450), wire_schema_bundle=ROOT / plan['wire_bundle'],
                prompt_version=plan['prompt_version'], experiment_profile=PROFILE,
                max_calls=limit, network_preflight=True)
            write_json_new(root / 'transport_config.json', agent.public_config)
            agent.preflight()  # One non-generating authenticated GET on the same real client.
            for index, case in enumerate(cases):
                current_case = case['case_id']
                if stop_file is not None and stop_file.exists():
                    stop = 'EXTERNAL_STOP'
                    break
                if time.time() >= deadline - 100:
                    stop = 'WALL_LIMIT'
                    break
                source_check(plan)
                initial = capture_check(case)
                require_turn = case.get('require_turn', plan.get('require_turn', False))
                need(isinstance(require_turn, bool), 'CLIP_CONFIG', 'require_turn must be an explicit boolean')
                case_root = reserve_directory(runtime / current_case)
                reserve_directory(case_root / 'transport')
                write_json_new(case_root / 'config.json', dict(
                    profile=PROFILE, backend='openai_api', source_commit=commit,
                    capture_path=str(ROOT / case['capture']), capture_sha256=case['capture_sha256'],
                    planner_profile=plan['planner_profile'], require_turn=require_turn,
                    prompt_version=plan['prompt_version'], max_requests=2,
                    horizon_s=2, step_s=.2, prefix_s=.2, gt_used=False,
                    mode='FRESH_ONLINE_SINGLE_WINDOW', shared_clip_transport='../../transport',
                    source_sha256=plan['source_sha256']))
                local_rows, mappings, local_charges = [], [], []
                result = None
                feedback = None  # Never carry another reference's answer or feedback.
                case_fatal = None
                for revision in range(2):
                    if stop_file is not None and stop_file.exists():
                        stop = 'EXTERNAL_STOP'
                        break
                    remaining = deadline - time.time()
                    if remaining < 100:
                        stop = 'WALL_LIMIT'
                        break
                    source_check(plan)
                    agent.timeout = min(plan.get('timeout_s', 450), remaining - 90)
                    capture = deepcopy(initial)
                    capture['metadata']['revision'] += revision
                    local_attempt = revision + 1
                    global_attempt = agent.call_count + 1
                    folder = reserve_directory(case_root / f'attempt_{local_attempt:02d}')
                    charge = dict(case_id=current_case, local_attempt=local_attempt,
                                  global_attempt=global_attempt,
                                  phase='initial' if revision == 0 else 'targeted_revision',
                                  charged_before_request=True, timeout_s=agent.timeout,
                                  failed_requests_consume_budget=True, automatic_retries=0)
                    need(len(charges) < limit, 'CLIP_BUDGET', 'Clip operation ceiling exhausted')
                    charges.append(charge)
                    local_charges.append(charge)
                    write_json_new(case_root / f'charge_{local_attempt:02d}.json', charge)
                    write_json_new(budget_root / f'charge_{len(charges):03d}.json', charge)
                    stages = {name: 'NOT_REACHED' for name in
                              ('SERVICE', 'BUSINESS_SCHEMA', 'T1', 'T2', 'T3', 'T4', 'EXECUTION', 'ACCEPTOR')}
                    try:
                        output, provenance = agent.decide(capture, feedback)
                        stages.update(SERVICE='PASS', BUSINESS_SCHEMA='PASS')
                        write_json_new(folder / 'record.json', dict(
                            capture=capture, output=output, provenance=provenance))
                        result, failure, tools = physical(
                            capture, output, provenance, initial, revision, folder,
                            policy=plan['planner_profile'], require_turn=require_turn)
                        stages.update(tools)
                        local_rows.append(dict(attempt=local_attempt, global_attempt=global_attempt,
                                               stages=stages, failure=failure))
                        if result:
                            break  # Any consumed prefix ends this reference, including a failed post-check.
                        feedback = dict(
                            failure=failure, previous_native_output=output,
                            all_initial_body_envelope_conflicts=body_feedback(output),
                            concrete_repair_request='Supply the specific missing native road polygon/scale support or whole BODY two-second occupancy, under justified local motion conditions. Repair actual evidence/premise/residual/geometry errors. Never fabricate numbers, remove obstacles or relax physical checks. If a conservative corridor fails, revise a justified decision/target region or report the conflict; do not merely explain refusal.')
                    except Failure as error:
                        case_fatal = error.record
                        observed = agent.last_attempt_summary or {}
                        if observed.get('model_service_returned') is True:
                            stages['SERVICE'] = 'PASS'
                        if observed.get('native_json_returned') and case_fatal['code'] == 'SCHEMA_ERROR':
                            stages['BUSINESS_SCHEMA'] = 'FAIL'
                        write_json_new(folder / 'failure.json', case_fatal)
                        local_rows.append(dict(attempt=local_attempt, global_attempt=global_attempt,
                                               stages=stages, failure=case_fatal))
                        stop = 'SERVICE_INTERFACE_OR_EXECUTION_FAILURE'
                        fatal = case_fatal
                        request_stop(stop_file, current_case, case_fatal)
                        break
                    finally:
                        mappings.append(alias_transport(case_root, root / 'transport',
                                                        local_attempt, global_attempt))
                write_json_new(case_root / 'transport' / 'mapping.json', dict(
                    mappings=mappings, interpretation='Relative aliases to the original shared-client evidence; no output conversion'))
                if result:
                    write_json_new(case_root / 'round_result.json', result)
                case_summary = dict(
                    profile=PROFILE, planner_profile=plan['planner_profile'], turn_task_required=require_turn,
                    backend='openai_api', charged_requests=len(local_charges), budget_limit=2,
                    remaining_unused=2 - len(local_charges), ended=True, attempts=local_rows,
                    fatal=case_fatal, prototype_conditional_success=bool(result and result['prototype_conditional_success']),
                    prefix_consumed=bool(result), source_commit=commit, mode='online',
                    old_strict_experiment_success=False, g2_evaluated=False, real_vehicle_control=False)
                write_json_new(case_root / 'summary.json', case_summary)
                totals = transport_totals(case_root)
                need(totals['api_post_requests'] <= len(local_charges) <= 2,
                     'CLIP_ACCOUNTING', 'Transport accounting exceeds the case charge ledger')
                rows[index].update(totals, charged_requests=len(local_charges),
                    status='CONDITIONAL_CHAIN_PASS' if case_summary['prototype_conditional_success'] else
                           ('NOT_ATTEMPTED' if not local_charges else 'NO_COMPLETE_CHAIN'),
                    native_outputs=len(list(case_root.glob('transport/attempt_*/native_output.json'))),
                    business_valid_records=len(list(case_root.glob('attempt_*/record.json'))),
                    fatal=case_fatal, prefix_consumed=bool(result), require_turn=require_turn)
                write_json_new(runtime / (current_case + '.completed.json'), rows[index])
                print(json.dumps(rows[index], ensure_ascii=False), flush=True)
                if stop is not None:
                    break
    except Failure as error:
        fatal = error.record
        stop = 'SERVICE_INTERFACE_OR_INPUT_FAILURE'
        request_stop(stop_file, current_case, fatal)
    except (OSError, ValueError, KeyError) as error:
        fatal = dict(code='CLIP_RUNNER_ERROR', category='interface_error',
                     message='Local clip runner failed; raw exception suppressed',
                     details=dict(exception_type=type(error).__name__))
        stop = 'LOCAL_RUNNER_FAILURE'
        request_stop(stop_file, current_case, fatal)
    finally:
        totals = transport_totals(root)
        all_chains = all(row['status'] == 'CONDITIONAL_CHAIN_PASS' for row in rows)
        write_json_new(root / 'batch_summary.json', dict(
            ended=True, backend='openai_api', charged_requests=len(charges),
            api_post_requests=totals['api_post_requests'], usage=totals['usage'],
            server_reported_models=totals['server_reported_models'],
            operational_call_ceiling=limit, unused_operating_slots=limit - len(charges),
            per_window_limit=2, old_budget_inherited=0, cases=rows, stop_reason=stop, fatal=fatal,
            native_outputs=len(list((root / 'transport').glob('attempt_*/native_output.json'))),
            conditional_chain_passes=sum(row['status'] == 'CONDITIONAL_CHAIN_PASS' for row in rows),
            all_references_processed=all(row['status'] != 'NOT_ATTEMPTED' for row in rows),
            all_conditional_chains_passed=all_chains,
            serial=True, one_api_client_for_clip=True, independent_observations=True,
            automatic_transport_retries=0, elapsed_s=time.monotonic() - started,
            actual_server_snapshot=None, recording_boundary=plan['recording_boundary']))
        if agent is not None and hasattr(agent, 'client'):
            agent.client.close()
    return 0 if stop is None and all_chains else 3


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--stop-file', type=Path,
                        help='Explicit shared stop marker; no new GET/POST after it is observed')
    arguments = parser.parse_args()
    sys.exit(run(arguments.plan, arguments.stop_file))
