"""Seal a fresh offline delivery; verify old evidence and frozen current source.

This is a bookkeeping entry point, never a model or driving-control entry point.
It writes one new seal and refuses an existing destination or sealed directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.contracts import Failure
from agentic.evidence_io import check_new_destination,write_json_new

ALLOWED_SOURCE_CHANGES={
    'implementation/agentic/adapter.py','implementation/agentic/cli.py',
    'implementation/agentic/codex_adapter.py','implementation/agentic/contracts.py',
    'implementation/agentic/diagnostics.py','implementation/agentic/t1.py','implementation/agentic/t4.py',
    'implementation/experiments/nuscenes/t1_diagnosis_g2.py',
    'implementation/experiments/nuscenes/t1_field_diagnosis.py',
    'implementation/experiments/nuscenes/replay_frozen_t1_counterexamples.py',
    'implementation/experiments/nuscenes/finalize_t1_diagnosis.py'}


def main(directory,run,test_log):
    seal=directory/'DELIVERY_SHA256_MANIFEST.json';check_new_destination(seal)
    baseline=json.loads((directory/'baseline_protection.json').read_text())
    zip_path=directory/'baseline_source.zip';sources={}
    with zipfile.ZipFile(zip_path) as z:
        sources={name:hashlib.sha256(z.read(name)).hexdigest() for name in z.namelist()}
    development_protocols={}
    with zipfile.ZipFile(directory/'development_exp1_source.zip') as z:
        for name in ('implementation/schemas/agent_output_v3_exp1.schema.json',
                     'implementation/prompts/driving_v3_exp1.txt',
                     'implementation/docs/计量与时空覆盖契约3_0_exp1.md'):
            expected=hashlib.sha256(z.read(name)).hexdigest()
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
                raise Failure('DEVELOPMENT_PROTOCOL_CHANGED','Frozen exp1 draft must remain unchanged',path=name)
            development_protocols[name]=expected
    changes=[];unchanged=0
    for name,record in baseline['files'].items():
        path=Path(name) if name.startswith('/') else ROOT/name
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        expected=sources.get(name,record['sha256'])
        if actual!=expected:
            if name not in ALLOWED_SOURCE_CHANGES:
                raise Failure('PROTECTED_INPUT_CHANGED','Old protocol, evidence or unrelated source changed',path=name)
            changes.append(dict(path=name,before_sha256=expected,after_sha256=actual,
                                original_source='baseline_source.zip@'+baseline['baseline_commit']))
        else:unchanged+=1
    source=json.loads((run/'source_manifest.json').read_text())
    for name,record in source['files'].items():
        raw=(ROOT/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:
            raise Failure('SOURCE_CHANGED_AFTER_RUN','Source differs from the actual offline run',path=name)
    # The sealing procedure itself is also committed and hashed.
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    self_name=str(Path(__file__).resolve().relative_to(ROOT))
    if Path(__file__).read_bytes()!=subprocess.check_output(['git','show',revision+':'+self_name],cwd=ROOT):
        raise Failure('UNFROZEN_SOURCE','Commit the sealing procedure before execution')
    tests=test_log.read_text()
    if '\nOK\n' not in tests or 'FAILED (' in tests:raise Failure('TEST_FAILURE','Full regression evidence must pass before sealing')
    summary=json.loads((run/'summary.json').read_text())
    if summary['online_model_calls']!=0 or summary['new_online_successes']!=0:
        raise Failure('SCOPE','This delivery must remain offline only')
    provenance=dict(analysis_source_commit=source['analysis_source_commit'],seal_source_commit=revision,
        baseline_commit=baseline['baseline_commit'],protected_files=len(baseline['files']),
        protected_unchanged=unchanged,authorized_code_changes=changes,scope=baseline['scope'],
        reconciliation='baseline_source_discrepancies.json; original initial-read manifest preserved',
        frozen_development_exp1_protocols_unchanged=development_protocols,
        old_protocols_prompts_results_manifests_unchanged=True,
        online_model_calls=0,old_model_output_transformations=0,new_online_successes=0,
        offline_run=str(run.relative_to(ROOT)) if run.is_relative_to(ROOT) else str(run),
        test_log=str(test_log.relative_to(ROOT)) if test_log.is_relative_to(ROOT) else str(test_log),
        test_log_sha256=hashlib.sha256(test_log.read_bytes()).hexdigest(),
        sealing_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    write_json_new(directory/'delivery_protection.json',provenance)
    files={str(p.relative_to(directory)):dict(sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size)
           for p in sorted(directory.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p!=seal}
    write_json_new(seal,dict(analysis_source_commit=source['analysis_source_commit'],files=files,
        scope='This new delivery tree only; no self hash or regenerable bytecode; original-input hashes are separately retained'))
    print(json.dumps(dict(protected_unchanged=unchanged,authorized_source_changes=len(changes),sealed_files=len(files),new_model_calls=0)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True,type=Path);p.add_argument('--run',required=True,type=Path);p.add_argument('--tests',required=True,type=Path);a=p.parse_args()
    try:main(a.directory.resolve(),a.run.resolve(),a.tests.resolve())
    except Failure as exc:
        print(json.dumps(exc.record,ensure_ascii=False));raise SystemExit(2)
