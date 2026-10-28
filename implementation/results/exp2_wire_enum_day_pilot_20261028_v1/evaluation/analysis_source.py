"""Post-run independent G2 analysis; no model/network/runtime tool invocation.

Identity reviews were frozen separately before numerical unsealing. This file
is post-run analysis, not part of the pre-request frozen runtime source.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
TRIAL = Path(__file__).resolve().parents[1]
RUNTIME, PRE, OUT = TRIAL / 'runtime', TRIAL / 'evaluation_preregister', TRIAL / 'evaluation'
REGISTRATION = TRIAL / 'registration/authorized_config.json'
sys.path.insert(0, str(ROOT / 'implementation'))
sys.path.insert(0, str(ROOT / 'implementation/experiments'))
from agentic.contracts import digest, need
from agentic.evidence_io import write_json_new, write_text_new
from agentic.g2_metric import evaluate
from pilot_g2 import bind_associations, verify_runtime_record


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


seal_path = RUNTIME / 'SHA256_RUNTIME_MANIFEST.json'
seal = read(seal_path)
need(seal.get('runtime_sealed') is True, 'UNSEALED_RUNTIME', 'Require a completed runtime seal')
runtime_checks = []
for name, expected in seal['files'].items():
    path = (RUNTIME / name).resolve()
    need(path.is_relative_to(RUNTIME.resolve()) and path.is_file(), 'RUNTIME_MEMBER', 'Invalid public runtime path')
    actual = dict(sha256=sha(path), bytes=path.stat().st_size)
    need(actual == expected, 'RUNTIME_SEAL_MISMATCH', 'Original runtime evidence changed', path=name)
    runtime_checks.append(dict(path=name, expected=expected, actual=actual, unchanged=True))
actual_runtime = {str(p.relative_to(RUNTIME)) for p in RUNTIME.rglob('*') if p.is_file()}
excluded = sorted(actual_runtime - (set(seal['files']) | {seal_path.name}))
need(all(name.startswith(('transport/.private_state/', 'transport/attempt_01/.private/',
                          'transport/attempt_02/.private/')) for name in excluded),
     'UNSEALED_PUBLIC_RUNTIME', 'A public runtime file was not sealed')
need(set(seal['files']) | {seal_path.name} <= actual_runtime,
     'RUNTIME_MISSING_MEMBER', 'A sealed runtime member is missing')

registration = read(REGISTRATION)
plan = read(PRE / 'preregistration.json')
need(sha(PRE / 'preregistration.json') == registration['g2_preregistration_sha256'],
     'G2_PLAN_CHANGED', 'Do not change the preregistered definitions')
pre_checks = []
for path in sorted(PRE.glob('*.json')):
    blob = subprocess.check_output(['git', 'show', seal['source_commit'] + ':' + relative(path)], cwd=ROOT)
    need(hashlib.sha256(blob).hexdigest() == sha(path), 'G2_PREREG_CHANGED', 'Pre-request G2 input changed', path=relative(path))
    pre_checks.append(dict(path=relative(path), frozen_source_commit=seal['source_commit'],
                           git_blob_sha256=hashlib.sha256(blob).hexdigest(), current_sha256=sha(path), unchanged=True))

identity = OUT / 'identity_review'
identity_seal_path = identity / 'SHA256_IDENTITY_MANIFEST.json'
identity_seal = read(identity_seal_path)
need(identity_seal.get('identity_frozen') is True and identity_seal.get('metric_values_unsealed') is False,
     'IDENTITY_NOT_FROZEN', 'Freeze non-numeric identity review before unsealing values')
for name, row in identity_seal['files'].items():
    need(sha(identity / name) == row['sha256'], 'IDENTITY_SEAL_CHANGED', 'Frozen identity evidence changed')

truth = read(PRE / 'truth.json')
records, attempts, association_files = [], [], []
summary = read(RUNTIME / 'summary.json')
budget = read(RUNTIME / 'budget/final.json')
need(summary['ended'] is True and summary['unique_windows'] == 1 and budget['charged'] == 2,
     'TRIAL_STATE', 'This analysis applies to the completed two-request trial')
for number in (1, 2):
    record_path = RUNTIME / f'attempt_{number:02d}/record.json'
    verify_runtime_record(record_path, seal_path)
    view = read(identity / f'attempt_{number:02d}_semantic_view.json')
    aliases = read(identity / f'attempt_{number:02d}_private_alias_map.json')
    review_path = identity / f'attempt_{number:02d}_review.json'
    review = read(review_path)
    truth['associations'].update(bind_associations(view, aliases, review))
    record = read(record_path)
    need(digest(record['output']) == view['source_output_digest'], 'REVIEW_OUTPUT_MISMATCH', 'Original output differs from reviewed source')
    records.append(record)
    ledger = read(RUNTIME / f'attempt_{number:02d}/stage_ledger.json')
    native = read(RUNTIME / 'transport' / f'attempt_{number:02d}/attempt_summary.json')
    stages = {key: value['status'] for key, value in ledger['stages'].items()}
    need(stages['NATIVE_OUTPUT'] == 'PASS' and stages['T1'] == 'FAIL', 'TRIAL_STAGE', 'Unexpected stage outcome')
    need(all(stages[key] == 'NOT_REACHED' for key in ('T2', 'T3', 'T4', 'ACCEPTOR', 'EXECUTION')),
         'UNREACHED_STAGE', 'Later stages cannot be scored as passed')
    attempts.append(dict(attempt=number, record_id=record['record_id'], stages=stages,
        native_output_present=True, native_output_unchanged=True, schema_valid=True,
        service_notification='COMPLETED_NATIVE_GENERATION',
        model_service_returned=native.get('model_service_returned'),
        planning_usability=dict(status='NOT_USABLE_T1_REJECTED', T1='FAIL',
            T2='NOT_REACHED', T3='NOT_REACHED', T4='NOT_REACHED',
            independent_acceptor='NOT_REACHED', simulated_execution='NOT_REACHED',
            prefix_classification='NOT_REACHED', consumed_prefix=False,
            released_prefix_s=None, no_planning_usability_inference_from_overlap=True)))
    association_files.append(review_path)

inputs = [seal_path, REGISTRATION, *sorted(PRE.glob('*.json')),
          identity_seal_path, *[identity / name for name in identity_seal['files']],
          *[RUNTIME / name for name in seal['files']],
          ROOT / 'implementation/agentic/g2_metric.py', ROOT / 'implementation/experiments/pilot_g2.py']
before = {relative(p): dict(sha256=sha(p), bytes=p.stat().st_size) for p in inputs}
config = dict(evaluation_horizon_s=plan['evaluation_horizon_s'],
              evaluation_domain=deepcopy(plan['evaluation_domain']),
              preregistration_sha256=sha(PRE / 'preregistration.json'),
              identity_freeze_manifest_sha256=sha(identity_seal_path),
              historical_exposure_disclosed=True, fully_blind=False,
              thresholds_applied=False, g2_pass_claim=False)
metrics = evaluate(records, truth, config)
write_json_new(OUT / 'derived_truth_with_frozen_associations.json', truth)
write_json_new(OUT / 'evaluation_config.json', config)
write_json_new(OUT / 'metrics.json', metrics)

availability = metrics['availability']
n = availability['declared_object_rows']
availability = dict(availability,
    occupancy_missing_rate=availability['occupancy_missing'] / n if n else None,
    position_incomparable=n - availability['position_error_m']['n'],
    position_incomparable_rate=(n - availability['position_error_m']['n']) / n if n else None,
    velocity_incomparable=n - availability['velocity_vector_error_mps']['n'],
    velocity_incomparable_rate=(n - availability['velocity_vector_error_mps']['n']) / n if n else None,
    future_incomparable=n - availability['future_minimum_interval_coverage']['n'],
    future_incomparable_rate=(n - availability['future_minimum_interval_coverage']['n']) / n if n else None)
focal = plan['fixed_focal_instance']
focal_rows = []
for record in records:
    rows = [row for row in metrics['rows'] if row['record_id'] == record['record_id'] and row.get('gt_id') == focal]
    focal_rows.append(dict(record_id=record['record_id'], gt_instance=focal,
        reviewed_match_count=len(rows), model_position_present=bool(rows and rows[0]['position_available']),
        evaluable=bool(rows and rows[0]['position_error_m'] is not None),
        position_error_m=rows[0]['position_error_m'] if rows else None,
        reason=rows[0]['position_status'] if rows else 'FOCAL_IDENTITY_NOT_UNIQUELY_MATCHED'))

report = dict(
    evaluation_version='exp2_wire_enum_independent_offline_g2_v1', experiment_id=summary['experiment_id'],
    sample_id=plan['sample_id'], unique_windows=1, original_cohort='original_four',
    attempted_response_slots=2, native_output_slots=2, native_business_schema_valid_slots=2,
    attempts=attempts, budget=budget,
    identity_review=dict(frozen_before_metric_unsealing=True,
        identity_seal_sha256=sha(identity_seal_path), declared_rows=n,
        unique_reviewed=availability['declared_object_rows'] - availability['unmatched'],
        ambiguous_unmatched=availability['unmatched'], group_rows=0, invalid_evidence_rows=0,
        prior_window_and_GT_exposure=True, fully_blind=False,
        metric_error_based_matching=False, fixed_focal_replaced=False,
        basis='Frozen actual-cited-image visual review plus official same-frame SDK annotation projection; ambiguous aliases remain unmatched'),
    declared_actor_availability=availability, fixed_focal_rows=focal_rows,
    position_error_m=availability['position_error_m'], velocity_vector_error_mps=availability['velocity_vector_error_mps'],
    road_geometry_error=dict(value=None, evaluable_count=0,
        reason='No predeclared correspondence between model local patch and actual road boundary; not interchangeable with total drivable map extent'),
    future_coverage=availability['future_minimum_interval_coverage'],
    future_area_inflation=availability['future_mean_envelope_area_ratio'],
    future_status_distribution=dict(Counter(row['future']['status'] for row in metrics['rows'])),
    position_status_distribution=dict(Counter(row['position_status'] for row in metrics['rows'])),
    velocity_status_distribution=dict(Counter(row['velocity_status'] for row in metrics['rows'])),
    truth_reference=dict(measurement_semantics=plan['measurement_semantics'],
        reference_time_id=plan['reference_time_id'], reference_time_s=plan['reference_time_s'],
        ego_center_to_rear_axle_m=plan['ego_center_to_rear_axle_m'],
        evaluation_domain=plan['evaluation_domain'], independently_prepared_gt_actor_rows=len(truth['samples'][plan['sample_id']]['objects']),
        position='Annotation box-center interpolation, not exactly measured instantaneous ground truth',
        velocity='Annotation-displacement-slope proxy; no instantaneous-velocity score',
        future='Annotation-linear circumscribed BODY surrogate, not true continuous occupancy',
        passed_to_runtime=False),
    model_identity=dict(requested_model=registration['model'],
        cli_resolved_aliases=sorted({record['provenance'].get('cli_resolved_alias') for record in records}),
        server_reported_snapshot=None, generation_snapshot_identity='NOT_EXPOSED'),
    unique_window_denominator=1, repeated_revisions_are_independent_windows=False,
    thresholds_applied=False, g2_pass_claim=False, online_success_claim=False,
    real_model_calls_this_evaluation=0, runtime_truth_writes=0,
    strict_numeric_entry_invoked=True, future_six_window_batch_launched=False,
    interpretation='Two repeated native responses from one exposed window; measurement comparisons do not authorize planning or establish calibrated prediction or real-vehicle safety.')
write_json_new(OUT / 'report.json', report)
rows = []
for row in metrics['rows']:
    rows.append(dict(record_id=row['record_id'], object_id=row['object_id'], gt_id=row['gt_id'],
        position_available=row['position_available'], velocity_available=row['velocity_available'], occupancy_available=row['occupancy_available'],
        matched=row['matched'], position_status=row['position_status'], position_error_m=row['position_error_m'],
        velocity_status=row['velocity_status'], velocity_error_mps=row['velocity_vector_error_mps'],
        future_status=row['future']['status'], future_minimum_interval_coverage=row['future_minimum_interval_coverage'],
        future_mean_envelope_area_ratio=row['future_mean_envelope_area_ratio'],
        comparability_reasons='|'.join(row['comparability_reasons'])))
text = io.StringIO()
writer = csv.DictWriter(text, fieldnames=list(rows[0]))
writer.writeheader(); writer.writerows(rows)
write_text_new(OUT / 'object_rows.csv', text.getvalue())
write_json_new(OUT / 'integrity_checks.json', dict(runtime_sealed=True, runtime_seal_sha256=sha(seal_path),
    public_runtime_members_verified=len(runtime_checks), runtime_checks=runtime_checks,
    excluded_private_paths=excluded, excluded_private_contents_read=False,
    whole_runtime_inventory_sealed=False, public_runtime_inventory_exact=True,
    preregistration_git_blob_checks=pre_checks,
    identity_seal_unchanged=True, native_output_transformations=[],
    derived_truth_change='Only frozen aliases-to-GT associations added to a separate derived copy; model output fields never changed'))
after = {relative(p): dict(sha256=sha(p), bytes=p.stat().st_size) for p in inputs}
need(before == after, 'EVALUATION_INPUT_MUTATION', 'Offline evaluation changed input evidence')
write_json_new(OUT / 'provenance.json', dict(
    generated_at_utc=datetime.now(timezone.utc).isoformat(),
    analysis_source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    analysis_source_sha256=sha(Path(__file__)),
    analysis_source_status='Post-run analysis source in new evaluation output; not part of the pre-request frozen source commit',
    worktree_dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()),
    input_sha256=before, input_bytes_unchanged_after_evaluation=True,
    new_model_values_read_only_after_identity_freeze=True,
    real_model_calls=0, network_requests=0, runtime_truth_writes=0))
print(json.dumps(dict(out=str(OUT), unique_windows=1, records=len(records), availability=availability,
                     future_status=report['future_status_distribution'], real_model_calls=0), ensure_ascii=False))
