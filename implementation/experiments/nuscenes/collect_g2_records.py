"""Collect unchanged, unconditional real responses for offline G2 evaluation.

Feedback revisions are retained in raw run logs but excluded from repeatability
because their prompts differ. No GT is read by this collector.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from agentic.contracts import need,digest


def collect(runs,prepared,out):
    need(not out.exists(),'OUTPUT_EXISTS','Use a fresh evaluation directory')
    out.mkdir(parents=True);records=out/'records';records.mkdir();manifest=[]
    for run in runs:
        cap=None
        for path in sorted(run.glob('*.json')):
            if path.name.endswith('-capture.json'):
                cap=json.loads(path.read_text());continue
            if not path.name.endswith(('-model-record.json','-agent.json')):continue
            need(cap is not None,'G2_CAPTURE','Model response has no preceding acquisition record')
            if cap['metadata']['revision']!=0:continue
            original=json.loads(path.read_text());prov=original['provenance']
            need(prov['source']=='online_model' and prov.get('transport')=='official_codex_cli' and prov['requested_model']=='gpt-6.1-sol','G2_REAL_ONLY','Expected real GPT-6.1 CLI response')
            sample_id=cap['metadata']['scene_id'].removeprefix('nusc_')
            expected=json.loads((prepared/'runtime_inputs'/(sample_id+'.json')).read_text())
            need(digest(cap)==digest(expected),'G2_INPUT_CHANGED','Unconditional repeat capture changed')
            need(digest(original['output'])==prov['output_digest'],'G2_OUTPUT_CHANGED','Original structured output changed')
            record_id=run.name+':'+path.name
            record=dict(sample_id=sample_id,record_id=record_id,output=original['output'],provenance=prov)
            target=records/(run.name+'__'+path.name)
            target.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            manifest.append(dict(record_id=record_id,sample_id=sample_id,source=str(path.resolve()),
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),capture_digest=digest(cap),
                phase='unconditional_repeat' if path.name.endswith('-model-record.json') else 'unconditional_initial_planning_call',
                record=str(target.relative_to(out))))
    (out/'record_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--runs',nargs='+',required=True);p.add_argument('--prepared',required=True);p.add_argument('--out',required=True)
    args=p.parse_args();result=collect([Path(x) for x in args.runs],Path(args.prepared),Path(args.out));print('unconditional_real_responses',len(result))
