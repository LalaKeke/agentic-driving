"""Offline-only G2 preparation for the authorized string-enum wire pilot.

No runtime transport, model adapter or driving tool is imported or called.
This reuses the original pilot's fixed comparison and identity-review rules.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from pilot_g2 import ROOT, SAMPLE, prepare, sha
from agentic.evidence_io import reserve_directory, write_json_new


def main(out):
    capture = ROOT / 'runs/nuscenes_cases_20261027/runtime_inputs' / (SAMPLE + '.json')
    old_g2 = ROOT / 'implementation/results/t1_unknown_diagnosis/g2'
    old_plan = ROOT / 'implementation/results/exp2_single_day_pilot_20261028_v1/evaluation_preregister/preregistration.json'
    out = reserve_directory(out, readonly_inputs=(capture, old_g2, old_plan))
    plan, truth, projections, bridge = prepare(capture, old_g2)
    plan.update(
        version='controlled_single_window_exp2_wire_enum_g2_preregistration_v1',
        experiment_id='exp2_wire_enum_day_pilot_20261028_v1',
        parent_preregistration_sha256=sha(old_plan),
        parent_preregistration_path=str(old_plan.relative_to(ROOT)),
        measurement_and_identity_rules_changed=False,
        output_contract='3.0.0-exp2', prompt_version='driving_v3_exp2',
        coverage_profile='whole_interval_sources_v3_exp2',
        wire_schema_version='so_const_string_enum_type_v2',
        new_trial_group='Redundant transport type annotations only; preserve original pilot failure and never transfer its unused call.',
        preregistration_timing='Prepared before any new generation request or inspection of new metric values; root freezes these files in Git before invocation.',
        runtime_separation='Offline GT and identity review only. None of these files or labels are model/T1-T4 inputs.')
    for name, value in [('preregistration.json', plan), ('truth.json', truth),
                        ('projection_index.json', projections), ('gt_format_bridge.json', bridge)]:
        write_json_new(out / name, value)
    write_json_new(out / 'preparation_provenance.json', dict(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        analysis_source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        preparation_source_sha256=sha(__file__),
        reused_source_sha256=sha(ROOT / 'implementation/experiments/pilot_g2.py'),
        source_matches_final_prerun_commit='To be verified by root in the actual frozen trial registration',
        truth_and_rules_inherited_without_numerical_changes=True,
        previous_exposure_disclosed=True, claim_of_full_blindness=False,
        real_model_calls=0, runtime_truth_writes=0, new_native_values_read=False))
    print(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    main(parser.parse_args().out)
