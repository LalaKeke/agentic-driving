"""Run the offline suite into a new directory, with network connections denied."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.contracts import Failure
from agentic.evidence_io import reserve_directory,open_new,write_json_new
from metric_temporal_offline import source_manifest


def main(out):
    source=source_manifest()
    out=reserve_directory(out)
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',
             NUSCENES_CASES_DIR=str(ROOT/'runs/nuscenes_cases_20261027'))
    child="""import socket,unittest,sys
def denied(*args,**kwargs):
    raise RuntimeError('Offline regression forbids network connections')
socket.socket.connect=denied
socket.socket.connect_ex=denied
socket.create_connection=denied
suite=unittest.defaultTestLoader.discover('implementation/tests')
result=unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
"""
    with open_new(out/'unittest.txt') as stream:
        result=subprocess.run([sys.executable,'-B','-c',child],cwd=ROOT,env=env,
                              stdout=stream,stderr=subprocess.STDOUT)
    write_json_new(out/'provenance.json',dict(analysis_source_commit=source['analysis_source_commit'],
        source_files=source['files'],source_matches_commit=True,network_connections_denied=True,
        actual_model_invocations=0,exit_code=result.returncode,
        command=[sys.executable,'-B','-c','see committed run_metric_offline_tests.py child string']))
    print(json.dumps(dict(out=str(out),exit_code=result.returncode,network_connections_denied=True,real_model_calls=0)))
    return result.returncode


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path)
    try:sys.exit(main(p.parse_args().out))
    except Failure as exc:
        print(json.dumps(exc.record,ensure_ascii=False));raise SystemExit(2)
