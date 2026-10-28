"""Freeze this user's authorized two-request pilot; preparation sends no turn.

Same acquisition and physics, new independent budget and string-enum wire v2.
The generated config must be committed before the separate runtime is invoked.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic.contracts import digest, need
from agentic.evidence_io import reserve_directory, write_json_new
from agentic.wire_schema_v2 import export_wire_bundle, load_wire_bundle
from agentic.wire_schema_review import build_compatibility_review
from metric_temporal_offline import source_manifest
from prepare_exp2_wire_trial import verify_pilot

TRIAL = 'exp2_wire_enum_day_pilot_20261028_v1'
RESULT = ROOT / 'implementation/results' / TRIAL
BUSINESS = ROOT / 'implementation/schemas/agent_output_v3_exp2.schema.json'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(out):
    sources = source_manifest()
    old_state = verify_pilot()
    config = deepcopy(read(ROOT / 'implementation/experiments/exp2_single_day_pilot_config.json'))
    capture_path = ROOT / config['capture_path']
    need(sha(capture_path) == config['capture_sha256'], 'PILOT_CAPTURE', 'Original acquisition bytes changed')
    capture = read(capture_path)
    need({f['id']: sha(f['path']) for f in capture['observations']} == config['image_sha256'],
         'PILOT_IMAGES', 'The same ten images must remain byte-identical')
    need(digest(capture['vehicle']) == config['vehicle_digest'] and digest(capture['safety']) == config['safety_digest'],
         'PILOT_PHYSICS', 'Original vehicle and safety settings changed')
    out = reserve_directory(out)
    manifest = export_wire_bundle(BUSINESS, out / 'wire_bundle')
    wire, _ = load_wire_bundle(out / 'wire_bundle', BUSINESS)
    need(manifest['string_enum_type_annotations'] == 8 and manifest['const_type_annotations'] == 12,
         'WIRE_SCOPE', 'This trial permits exactly the observed 8 string enums and 12 const annotations')
    review = build_compatibility_review(wire)
    write_json_new(out / 'compatibility_review.json', review)
    need(review['status_counts']['EXPLICIT_CONFLICT'] == 0, 'WIRE_EXPLICIT_CONFLICT',
         'Do not send a model request when a definite unresolved conflict is known')
    plan = RESULT / 'evaluation_preregister/preregistration.json'
    need(read(plan)['experiment_id'] == TRIAL, 'PILOT_G2', 'Freeze this new trial G2 plan before generation')
    config.update(experiment_id=TRIAL, baseline_commit='f201f952fb1ed86b750548416567185aa3731f9c',
        source_freeze_commit=sources['analysis_source_commit'], launched=False, execution_authorized=True,
        authorized_model_invocations=2, authorization_basis='User explicitly authorized a NEW independent maximum of two real requests: initial once and, if needed, one semantic revision; all failures consume budget; no automatic transport retries.',
        runtime_path=str((RESULT / 'runtime').relative_to(ROOT)),
        wire_schema_bundle=dict(path=str((out / 'wire_bundle').relative_to(ROOT)),
            **{key: manifest[key] for key in ('source_schema_sha256', 'wire_schema_sha256', 'wire_schema_version', 'generator_version')},
            transformation_manifest_sha256=sha(out / 'wire_bundle/transformation_manifest.json')),
        old_pilot_relation=dict(original_id=old_state['experiment_id'], original_ended=True,
            original_charged=1, original_unused=1, inherited_unused_calls=0, new_independent_budget=True,
            proposed_limit=2, approved_limit=2),
        source_sha256={name: row['sha256'] for name, row in sources['files'].items()
                       if not name.startswith(('implementation/tests/', 'implementation/docs/'))},
        g2_preregistration_sha256=sha(plan),
        g2_not_runtime_input=True,
        scientific_change='Transport-only redundant type annotations. Business schema, prompt, occupancy profile and physical standards remain unchanged.',
        service_support_unconfirmed=review['status_counts']['UNCONFIRMED'],
        generation_probes='No separate compatibility generation probe. Any generation request must be charged to this two-request ledger.',
        stop_policy='Any schema-interface/auth/quota/service/protocol error ends this trial immediately. Only a completed native generation with normal semantic refusal may receive one registered revision. Never modify schema and continue this ended budget.',
        subsequent_six_window_relationship=dict(parent_batch_launched=False, original_cohort='original_four',
            unique_windows=1, distinct_wire_group=True,
            rule='Count this trial at most once in a later explicitly authorized comparison. Preserve both old pilot failure and this trial; no window replacement or hidden inherited allowance.'),
        interpretation='Any eventual full-chain pass is conditional on model hypotheses and only consumes a simulated short prefix; no real vehicle, multi-round counterfactual or G2 pass claim.')
    write_json_new(out / 'authorized_config.json', config)
    write_json_new(out / 'source_manifest.json', sources)
    write_json_new(out / 'old_pilot_integrity.json', old_state)
    print(json.dumps(dict(trial=TRIAL, generated_config=str(out / 'authorized_config.json'),
        source_commit=sources['analysis_source_commit'], wire_schema_sha256=manifest['wire_schema_sha256'],
        authorized_new_requests=2, requests_sent=0, inherited_old_allowance=0,
        still_requires_config_git_commit=True), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    prepare(parser.parse_args().out)
