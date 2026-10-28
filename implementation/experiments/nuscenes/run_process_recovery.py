"""Frozen same-window process-recovery trial. Reads acquisition inputs, never GT."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(Path(__file__).parent))
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.contracts import need,Failure
from run_cases import run


def launch(config_path,runs):
    config_path=Path(config_path);config=json.loads(config_path.read_text())
    dirty=subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)
    need(not dirty,'EXPERIMENT_SOURCE_DIRTY','Commit the concrete source/config before a new live trial')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    index=ROOT/config['runtime_index']
    need(hashlib.sha256(index.read_bytes()).hexdigest()==config['runtime_index_sha256'],'FROZEN_COHORT','Observation index changed')
    for case in config['cohort']:
        path=ROOT/case['capture_path'];need(hashlib.sha256(path.read_bytes()).hexdigest()==case['capture_sha256'],'FROZEN_COHORT','Capture changed')
        cap=json.loads(path.read_text())
        need(cap['metadata']['horizon_s']==5 and cap['metadata']['step_s']==.2,'FROZEN_COHORT','Planning grid changed')
        hashes={f['id']:hashlib.sha256(Path(f['path']).read_bytes()).hexdigest() for f in cap['observations']}
        need(hashes==case['image_sha256'],'FROZEN_COHORT','Actual model image bytes changed')
    os.environ.update(DRIVING_OUTPUT_CONTRACT=config['output_contract'],DRIVING_PROMPT_VERSION=config['prompt_version'],DRIVING_CORRIDOR_POLICY=config['corridor_policy'],DRIVING_CODEX_TIMEOUT_S=str(config['timeout_s']))
    manifest=dict(configuration=config,source_revision=revision,configuration_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),launcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    path,summary=run(index,runs,config['evaluation_repeats'],config['backend'],config['model'],config['max_revisions'],manifest)
    subprocess.run(['git','archive','--format=zip','--output='+str((path/'source_snapshot.zip').resolve()),revision,'implementation/agentic','implementation/schemas','implementation/prompts','implementation/experiments/nuscenes'],cwd=ROOT,check=True)
    print(json.dumps(dict(run=str(path),**summary),ensure_ascii=False,indent=2))
    return 2 if summary['status']=='BLOCKED' else 0 if summary['strict_live_physical_successes']==summary['physical_success_denominator'] else 3

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',default=str(Path(__file__).with_name('process_recovery_config.json')));p.add_argument('--runs',default=str(ROOT/'runs/process_recovery'))
    a=p.parse_args()
    try:raise SystemExit(launch(a.config,a.runs))
    except Failure as exc:print(json.dumps(exc.record,ensure_ascii=False));raise SystemExit(2)
