"""Offline wire export, three-state review and NON-EXECUTABLE trial preparation.

No transport is instantiated, no authentication is read and no model is called.
Outputs must be fresh and outside sealed evidence. This does not authorize a run.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic.contracts import Failure, need
from agentic.evidence_io import reserve_directory, write_json_new
from agentic.fixtures_v3 import metric_case
from agentic.wire_schema import export_wire_bundle, load_wire_bundle, WireSchemaConflict
from agentic.wire_schema_review import build_compatibility_review, CONFLICT
from metric_temporal_offline import source_manifest

OLD = ROOT / 'implementation/results/exp2_single_day_pilot_20261028_v1'
BUSINESS = ROOT / 'implementation/schemas/agent_output_v3_exp2.schema.json'
OLD_CONFIG = ROOT / 'implementation/experiments/exp2_single_day_pilot_config.json'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_pilot():
    """Read public sealed evidence, never resume or inspect private credentials."""
    seal = OLD / 'DELIVERY_SHA256_MANIFEST.json'
    manifest = read(seal)
    for name, row in manifest['files'].items():
        path = OLD / name
        need(sha(path) == row['sha256'] and path.stat().st_size == row['bytes'],
             'OLD_PILOT_CHANGED', 'Original sealed public pilot differs', path=str(path))
    summary = read(OLD / 'runtime/summary.json')
    budget = read(OLD / 'runtime/budget/final.json')
    need(summary['ended'] is True and budget['limit'] == 2 and budget['charged'] == 1
         and budget['unused'] == 1, 'OLD_PILOT_STATE', 'Original pilot must stay ended at 1/2')
    need(not list((OLD / 'runtime/transport').glob('attempt_*/native_output.json')),
         'OLD_PILOT_STATE', 'Original pilot native output count unexpectedly changed')
    return dict(experiment_id=summary['experiment_id'], ended=True, charged=1, limit=2,
        unused=1, native_outputs=0, sealed_public_files_checked=len(manifest['files']),
        seal_sha256=sha(seal), unchanged=True,
        private_files_scope='No private credential/raw file content is published or reinterpreted.')


def field_differential(original, wire, manifest):
    """Actual twelve string sites: complete authored instance and wrong values."""
    _, fixture, _ = metric_case()
    old, new = Draft7Validator(original), Draft7Validator(wire)
    need(old.is_valid(fixture) and new.is_valid(fixture), 'WIRE_DIFFERENTIAL',
         'The authored positive control must validate in both schemas')
    rows = []
    restored = deepcopy(wire)
    for change in manifest['transformations']:
        parts = [part.replace('~1', '/').replace('~0', '~')
                 for part in change['schema_pointer'].split('/')[1:]]
        node = restored
        for part in parts:
            node = node[int(part)] if isinstance(node, list) else node[part]
        del node['type']
        # Translate this fixed business schema's properties/items/anyOf path to
        # the exercised authored instance. Never used on a real model output.
        instance_parts = []
        index = 0
        while index < len(parts):
            keyword = parts[index]
            if keyword == 'properties':
                instance_parts.append(parts[index + 1]); index += 2
            elif keyword == 'items':
                instance_parts.append(0); index += 1
            elif keyword == 'anyOf':
                index += 2
            else:
                raise Failure('WIRE_DIFFERENTIAL_PATH', 'Unexpected fixed-fixture schema path', path=parts)
        tests = []
        for value in ('__wrong_const__', None, True, 0, [], {}):
            negative = deepcopy(fixture)
            location = negative
            for part in instance_parts[:-1]:
                location = location[part]
            location[instance_parts[-1]] = value
            old_valid, wire_valid = old.is_valid(negative), new.is_valid(negative)
            need(not old_valid and not wire_valid, 'WIRE_DIFFERENTIAL',
                 'A const mutation changed business/wire acceptance', pointer=change['schema_pointer'])
            tests.append(dict(value=value, business_valid=old_valid, wire_valid=wire_valid))
        rows.append(dict(schema_pointer=change['schema_pointer'], instance_path=instance_parts,
                         const_value=change['const_value'], added_type=change['new_value'],
                         positive_business_valid=True, positive_wire_valid=True, negative_instances=tests))
    need(restored == original, 'WIRE_NON_MINIMAL', 'Removing listed redundant types must restore the business schema')
    return dict(source='synthetic_fixture', native_model_output=False, real_model_calls=0,
        rows=rows, transformation_sites=len(rows), comparisons=len(rows) * 7,
        transformation_removal_restores_original_json=True,
        interpretation='Repeated authored controls, not independent windows or a general equivalence proof.')


def prepare(out):
    source = source_manifest()  # Require committed analysis/runtime sources.
    old_state = verify_pilot()
    original_bytes = BUSINESS.read_bytes()
    out = reserve_directory(out, readonly_inputs=(OLD, BUSINESS, OLD_CONFIG))
    manifest = export_wire_bundle(BUSINESS, out / 'wire_bundle')
    wire, _ = load_wire_bundle(out / 'wire_bundle', BUSINESS)
    review = {
        'business': build_compatibility_review(json.loads(original_bytes)),
        'wire': build_compatibility_review(wire),
        'source_commit': source['analysis_source_commit'],
        'business_schema_sha256': manifest['source_schema_sha256'],
        'wire_schema_sha256': manifest['wire_schema_sha256'],
        'actual_generation_requests': 0,
    }
    write_json_new(out / 'compatibility_review.json', review)
    write_json_new(out / 'actual_field_differential.json', field_differential(json.loads(original_bytes), wire, manifest))
    write_json_new(out / 'source_manifest.json', source)

    # Pin the same existing CLI files without invoking an app-server or login.
    clients = read(OLD / 'preflight/client_pins.json')
    for name, row in clients['files'].items():
        need(sha(ROOT / name) == row['sha256'], 'CLIENT_CHANGED', 'Pinned CLI bytes changed', path=name)
    clients.update(verification='Exact local file SHA only; no inference/auth/metadata call',
                   copied_from=str((OLD / 'preflight/client_pins.json').relative_to(ROOT)))
    write_json_new(out / 'client_pins.json', clients)

    config = deepcopy(read(OLD_CONFIG))
    config.update(
        experiment_id='exp2_wire_day_trial_20261028_v2',
        baseline_commit=source['analysis_source_commit'],
        registration_type='OFFLINE_PREPARED_TRIAL_REQUIRES_NEW_AUTHORIZATION',
        launched=False, execution_authorized=False, authorized_model_invocations=0,
        actual_model_invocations=0,
        runtime_path='implementation/results/exp2_wire_day_trial_20261028_v2/runtime',
        wire_schema_bundle=dict(path=str((out / 'wire_bundle').relative_to(ROOT))
            if out.is_relative_to(ROOT) else str(out / 'wire_bundle'),
            **{key: manifest[key] for key in ('source_schema_sha256', 'wire_schema_sha256',
                                             'wire_schema_version', 'generator_version')},
            transformation_manifest_sha256=sha(out / 'wire_bundle/transformation_manifest.json')),
        source_sha256={p: row['sha256'] for p, row in source['files'].items()
                       if not p.startswith(('implementation/tests/', 'implementation/docs/'))},
        old_pilot_relation=dict(original_id=old_state['experiment_id'], original_ended=True,
            original_charged=1, original_unused=1, inherited_unused_calls=0,
            new_independent_budget=True, proposed_limit=2, approved_limit=0,
            rule='This proposal cannot resume or reset the old trial. New authorization and a new frozen configuration are required.'),
        experimental_variable='Transport schema only: redundant const types; business/prompt/physics remain unchanged',
        compatibility_probe_budget_rule='Any future generation request, including an interface probe or failed request, consumes the NEW budget; no hidden retry budget.',
        stop_conditions=['Any interface/schema-service error stops immediately; do not spend the revision on a transport repair.',
            'Authentication/quota/model reroute or evidence/pin failure stops immediately.',
            'At most one semantic revision only after a completed genuine native generation; no automatic request/stream retry.',
            'After any consumed prefix stop this single-window trial; do not claim new counterfactual observations.',
            'Never launch six-window batch automatically.'],
        preparation_review=dict(explicit_conflicts=sum(row['status'] == CONFLICT for row in review['wire']['checks']),
            unresolved_checks=review['wire']['status_counts']['UNCONFIRMED'],
            backend_acceptance=False,
            remaining_risk='Documented API subset is not the deployed Codex backend guarantee; enum-only/minLength/const form remain unconfirmed.'),
    )
    config['subsequent_six_window_relationship'] = dict(
        parent_proposal=config['subsequent_six_window_relationship']['parent_proposal'],
        parent_batch_launched=False, unique_windows=1, original_cohort='original_four',
        original_trial_preserved=True, separate_transport_group=True,
        rule='A future authorized trial is counted once at most. Never replace the original interface failure or merge revisions into independent windows.',
        group_boundary='Wire schema, provider/client wrapping, budget and business/prompt differences must remain visible in any later comparison.')
    config['g2_preparation'] = dict(
        reference='implementation/results/exp2_single_day_pilot_20261028_v1/evaluation_preregister/preregistration.json',
        sha256=sha(OLD / 'evaluation_preregister/preregistration.json'),
        historical_exposure=True, fully_blind=False, new_associations_frozen_before_numeric_inspection=True,
        truth_is_runtime_input=False, no_new_g2_scores=True,
        rule='Freeze new trial identity/association review before inspecting new values; preserve body point, absolute velocity, epoch and evaluation-domain definitions.')
    write_json_new(out / 'trial_registration.json', config)
    need(BUSINESS.read_bytes() == original_bytes, 'BUSINESS_SCHEMA_CHANGED', 'Business bytes must not change')
    need(verify_pilot() == old_state, 'OLD_PILOT_CHANGED', 'Original pilot changed during preparation')
    write_json_new(out / 'old_pilot_integrity.json', old_state)
    summary = dict(source_commit=source['analysis_source_commit'], business_schema_sha256=sha(BUSINESS),
        wire_schema_sha256=manifest['wire_schema_sha256'], transformations=manifest['transformation_count'],
        original_business_schema_unchanged=True, original_pilot_unchanged=True,
        wire_review=review['wire']['status_counts'], offline_review_completed=True,
        backend_acceptance_established=False, new_trial_launched=False,
        new_trial_authorized_calls=0, new_trial_charged_calls=0, actual_model_invocations=0,
        old_unused_calls_transferred=0, scientific_online_success_claim=False)
    write_json_new(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    try:
        raise SystemExit(prepare(parser.parse_args().out))
    except Failure as exc:
        print(json.dumps(exc.record, ensure_ascii=False))
        raise SystemExit(2)
    except WireSchemaConflict as exc:
        print(json.dumps(dict(code='WIRE_SCHEMA_CONFLICT', conflicts=exc.conflicts), ensure_ascii=False))
        raise SystemExit(2)
