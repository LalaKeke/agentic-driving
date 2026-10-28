"""Finalize an offline T1 diagnosis: immutable inputs, source provenance, taxonomy parity.

No online adapter is imported. Local T1 replays check only that the classification
fix preserves rejection; no original acceptance is overwritten or recomputed.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic import t1
from agentic.contracts import Failure


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def finalize(out):
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    names=['implementation/experiments/nuscenes/t1_field_diagnosis.py',
           'implementation/experiments/nuscenes/t1_diagnosis_g2.py',
           'implementation/experiments/nuscenes/finalize_t1_diagnosis.py',
           'implementation/experiments/nuscenes/replay_frozen_t1_counterexamples.py',
           'implementation/experiments/nuscenes/prepare.py',
           'implementation/agentic/datasets/nuscenes_capture.py',
           'implementation/agentic/t1.py', 'implementation/agentic/contracts.py',
           'implementation/agentic/geometry.py']
    sources={}
    for name in names:
        current=(ROOT/name).read_bytes()
        committed=subprocess.check_output(['git','show',revision+':'+name],cwd=ROOT)
        assert current==committed,'Commit analysis source before finalizing: '+name
        sources[name]={'sha256':hashlib.sha256(current).hexdigest(),'bytes':len(current)}
    protection=read(out/'baseline_protection.json');changes=[]
    original=(out/'baseline_sources/t1.py').read_text()
    old="need(h['scale_basis']!='unknown','MISSING_SCALE','Metric scale unsupported; supply grounded hypothesis/calibration')"
    new="need(h['scale_basis']!='unknown','MISSING_SCALE','Metric scale unsupported; supply grounded hypothesis/calibration','tool_infeasible')"
    assert (ROOT/'implementation/agentic/t1.py').read_text()==original.replace(old,new)
    assert sha(out/'baseline_sources/t1.py')==protection['protected_files']['implementation/agentic/t1.py']['sha256']
    unchanged=0
    for name,record in protection['protected_files'].items():
        path=Path(name) if name.startswith('/') else ROOT/name;actual=sha(path)
        if actual!=record['sha256']:
            assert name=='implementation/agentic/t1.py','Protected original changed: '+name
            changes.append(dict(path=name,before_sha256=record['sha256'],after_sha256=actual,
                                kind='AUTHORIZED_CODE_TAXONOMY_FIX_ONLY',frozen_input_preserved='baseline_sources/t1.py'))
        else:unchanged+=1
    spec=importlib.util.spec_from_file_location('agentic._frozen_t1_unknown_audit',out/'baseline_sources/t1.py')
    frozen=importlib.util.module_from_spec(spec);spec.loader.exec_module(frozen)
    rows=[]
    for path in sorted((ROOT/'implementation/results/process_recovery').rglob('*agent.json')):
        record=read(path)
        if record.get('provenance',{}).get('source')!='online_model':continue
        cap_path=max(p for p in path.parent.glob('*capture.json') if p.name<path.name)
        cap=read(cap_path);errors=[]
        for module in [frozen,t1]:
            try:module.construct(cap,record['output'],record['provenance'])
            except Failure as exc:errors.append(exc.record)
            else:raise AssertionError('This round must not newly accept a frozen rejected response')
        before,after=errors
        assert before['code']==after['code']
        compare=dict(after);compare['category']=before['category'];assert compare==before
        if before['category']!=after['category']:
            assert before['code']=='MISSING_SCALE' and before['category']=='interface_error' and after['category']=='tool_infeasible'
        rows.append(dict(response=str(path.relative_to(ROOT)),response_sha256=sha(path),capture=str(cap_path.relative_to(ROOT)),capture_sha256=sha(cap_path),
                         frozen_code=before['code'],current_code=after['code'],frozen_category=before['category'],current_category=after['category'],
                         rejected_before=True,rejected_after=True))
    assert len(rows)==27
    write(out/'source_fix_parity.json',dict(analysis_source_commit=revision,baseline_t1_sha256=sha(out/'baseline_sources/t1.py'),current_t1_sha256=sha(ROOT/'implementation/agentic/t1.py'),
        offline_t1_construct_attempts=54,online_model_calls=0,original_acceptance_recomputed=False,
        same_rejection_codes=27,classification_changes=sum(x['frozen_category']!=x['current_category'] for x in rows),rows=rows))
    fields=read(out/'fields/summary.json');g2=read(out/'g2/summary.json')
    assert fields['valid_responses']==g2['valid_structured_responses']==27
    assert fields['unique_windows']==g2['unique_windows']==6
    assert fields['source_commit']==revision,'Field audit must run after the analysis source commit'
    g2_provenance=read(out/'g2/evaluation_provenance.json')
    assert g2_provenance['analysis_source_commit']==revision,'G2 evaluation must run after the analysis source commit'
    write(out/'analysis_provenance.json',dict(analysis_source_commit=revision,analysis_sources=sources,
        baseline_commit=protection['baseline_commit'],input_manifest='baseline_protection.json',
        protected_unchanged_files=unchanged,explicit_code_changes=changes,scope=protection['scope'],
        online_model_calls=0,new_runtime_trials=0,original_acceptance_changed=False,original_four_denominators_changed=False,
        t3_causal_attribution=False,ground_truth_scope='Offline g2 products only; no runtime/model input writes'))
    print(json.dumps(dict(source_commit=revision,valid_responses=27,windows=6,protected_unchanged=unchanged,
                          allowed_code_changes=len(changes),classification_changes=sum(x['frozen_category']!=x['current_category'] for x in rows),online_model_calls=0)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,default=ROOT/'implementation/results/t1_unknown_diagnosis');a=p.parse_args();finalize(a.out)
