"""Record regression tests and synthetic demo in an isolated job snapshot."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--local',action='store_true',help='Explicit local developer validation only')
    args=parser.parse_args()
    if not args.local and not os.environ.get('SLURM_JOB_ID'):
        parser.error('Submit with Slurm; login-node execution is disabled.')
    source=args.source_root.resolve(); output=args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    report={
        'started_at_utc':datetime.now(timezone.utc).isoformat(),
        'commit':args.commit,'job_id':os.environ.get('SLURM_JOB_ID'),
        'hostname':socket.gethostname(),'python_version':platform.python_version(),
        'jsonschema_version':importlib.metadata.version('jsonschema'),
        'data_origin':'synthetic_fixture','real_model_connected':False,'steps':[]}
    commands=[('unit_tests',[sys.executable,'-m','unittest','discover','-s','tests','-v']),
              ('pedestrian_demo',[sys.executable,'run_demo.py'])]
    for name,cmd in commands:
        with (output/(name+'.log')).open('w',encoding='utf-8') as stream:
            try:
                completed=subprocess.run(cmd,cwd=source,stdout=stream,stderr=subprocess.STDOUT,
                                         text=True,encoding='utf-8',timeout=120,check=False)
                returncode=completed.returncode
            except subprocess.TimeoutExpired:
                stream.write('\nStep exceeded 120 second limit.\n')
                returncode=124
        report['steps'].append({'name':name,'returncode':returncode})
        if returncode!=0:break
    report['success']=len(report['steps'])==2 and all(s['returncode']==0 for s in report['steps'])
    demo_summary=source/'results/summary.json'
    if report['success']:
        report['demo_summary']=json.loads(demo_summary.read_text(encoding='utf-8'))
    report['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    (output/'hpc_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['success'] else 1


if __name__=='__main__':raise SystemExit(main())
