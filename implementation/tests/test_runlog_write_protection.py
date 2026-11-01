"""RunLog write-once regressions. No model invocation or archived writes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.contracts import Failure
from agentic.runtime import RunLog


class RunLogWriteProtectionTests(unittest.TestCase):
    def test_sealed_parent_rejects_before_unique_child_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);marker=root/'SHA256_MANIFEST.json';marker.write_bytes(b'{"sealed":true}\n')
            before={p.name:p.read_bytes() for p in root.iterdir()}
            with self.assertRaises(Failure) as exc:RunLog(root)
            self.assertEqual(exc.exception.record['code'],'SEALED_EVIDENCE')
            self.assertEqual({p.name:p.read_bytes() for p in root.iterdir()},before)

    def test_existing_stage_file_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=RunLog(tmp)
            self.assertEqual(log.count,2)
            self.assertEqual(json.loads((log.path/'0002-manifest.json').read_text())['execution'],'SIMULATION_ONLY')
            path=log.path/'0003-probe.json';path.write_bytes(b'ORIGINAL_STAGE_BYTES\n')
            with self.assertRaises(Failure) as exc:log.write('probe',{'replaced':True})
            self.assertEqual(exc.exception.record['code'],'OUTPUT_EXISTS')
            self.assertEqual(path.read_bytes(),b'ORIGINAL_STAGE_BYTES\n')
            self.assertEqual(sorted(p.name for p in log.path.iterdir()),['0001-source-hashes.json','0002-manifest.json','0003-probe.json'])


if __name__=='__main__':unittest.main()
