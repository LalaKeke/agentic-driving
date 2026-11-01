"""Replay authored counterexamples against the preserved baseline T1, offline.

The original counterexample source and historic log are read-only inputs. Its
classification assertion deliberately describes the baseline, not current T1.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic.contracts import Failure
from agentic.evidence_io import reserve_directory, open_new, write_json_new


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    # Importing archived evidence must not create __pycache__ inside its sealed
    # directory. Compilation is in memory; relative imports retain the spec's
    # package identity and the original __file__ remains inspectable.
    exec(compile(Path(path).read_bytes(), str(path), 'exec'), value.__dict__)
    return value


def main(out):
    out = reserve_directory(out)
    audit = ROOT / 'implementation/results/t1_unknown_diagnosis'
    baseline = audit / 'baseline_sources/t1.py'
    examples = audit / 'relevance/frozen_counterexamples.py'
    frozen = module('agentic._counterexample_baseline_t1', baseline)
    suite_module = module('_authored_t1_counterexamples', examples)
    suite_module.construct = frozen.construct
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(suite_module.IndependentCounterexamples)
    with open_new(out / 'replay.log', 'x') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (Path(__file__), baseline, examples,
                        ROOT / 'implementation/agentic/t2.py', ROOT / 'implementation/agentic/fixtures.py')}
    report = dict(analysis_source_commit=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        scope='AUTHORED_SOFTWARE_COUNTEREXAMPLES_NOT_ONLINE_RESULTS', online_model_calls=0,
        baseline_t1_loaded_explicitly=True, input_sha256=hashes,
        tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
        cases=suite_module.FIXTURES, results=suite_module.RESULTS)
    write_json_new(out / 'replay.json', report)
    print(json.dumps(dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors))))
    return not result.wasSuccessful()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'implementation/results/t1_unknown_diagnosis/relevance/replay')
    try:sys.exit(main(parser.parse_args().out))
    except Failure as exc:
        print(json.dumps(exc.record,ensure_ascii=False));sys.exit(2)
