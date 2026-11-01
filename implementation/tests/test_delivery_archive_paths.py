"""Git's Chinese-path escaping must not invalidate frozen source evidence."""
import importlib.util
from pathlib import Path
import sys
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
script=ROOT/'implementation/experiments/seal_metric_temporal_delivery.py'
spec=importlib.util.spec_from_file_location('metric_delivery_sealer',script)
sealer=importlib.util.module_from_spec(spec);spec.loader.exec_module(sealer)


class DeliveryArchivePathsTests(unittest.TestCase):
    def test_git_c_quoted_utf8_and_plain_paths(self):
        self.assertEqual(sealer.archive_path(r'"implementation/docs/\350\256\241.md"'),
                         'implementation/docs/计.md')
        self.assertEqual(sealer.archive_path('implementation/agentic/t1.py'),
                         'implementation/agentic/t1.py')

    def test_actual_development_protocol_bytes_are_recoverable(self):
        folder=ROOT/'implementation/results/metric_temporal_contract_v3'
        for path in ['implementation/schemas/agent_output_v3_exp1.schema.json',
                     'implementation/prompts/driving_v3_exp1.txt',
                     'implementation/docs/计量与时空覆盖契约3_0_exp1.md']:
            raw,archive=sealer.development_protocol_bytes(folder,path)
            self.assertEqual(raw,(ROOT/path).read_bytes())
            if path.startswith('implementation/docs/'):
                self.assertEqual(archive,'development_exp1_docs_supplement.zip')


if __name__=='__main__':unittest.main()
