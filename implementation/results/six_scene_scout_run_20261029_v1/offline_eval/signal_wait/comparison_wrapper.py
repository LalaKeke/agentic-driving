"""Offline-only wrapper for the saved, sealed signal-wait response."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[5]
HERE=Path(__file__).resolve().parent
RUNTIME=ROOT/'implementation/results/six_scene_scout_run_20261029_v1/runtime/signal_wait'
HELPER=ROOT/'implementation/experiments/critical_actor_comparison.py'
ACTORS=['cross_silver','left_black_sedan']
# The existing SDK environment omits jsonschema. Append the project environment
# for its validation dependency, preserving the SDK's numeric/plotting versions.
sys.path.append(str(ROOT/'.venv/lib/python3.11/site-packages'))

def load_helper():
    # The reused helper eagerly creates a matplotlib temp directory on import.
    # Keep this configuration/cache write within the offline evaluation subtree.
    cache=HERE/'.mpl_config';cache.mkdir(exist_ok=True)
    def local_temp(*args,**kwargs):
        return str(cache)
    os.environ['MPLCONFIGDIR']=str(cache)
    spec=importlib.util.spec_from_file_location('scout_saved_actor_comparison',HELPER)
    helper=importlib.util.module_from_spec(spec)
    with patch('tempfile.mkdtemp',side_effect=local_temp):spec.loader.exec_module(helper)
    helper.BASE=HERE/'record_links'
    return helper

def verify_seal():
    seal=json.loads((RUNTIME/'SHA256_WINDOW_MANIFEST.json').read_text())
    assert seal['ended'] is True
    for entry in seal['public_files']:
        p=RUNTIME/entry['path']
        assert p.stat().st_size==entry['size']
        assert hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--attempt',type=int,choices=(1,2),default=1)
    p.add_argument('--review',type=Path)
    a=p.parse_args();verify_seal();helper=load_helper()
    helper.CASES={'signal_wait':(a.attempt,ACTORS)}
    if a.review:helper.evaluate(a.out,a.review,Path('/data/nuscenes_dataset'))
    else:helper.prepare(a.out,Path('/data/nuscenes_dataset'))
