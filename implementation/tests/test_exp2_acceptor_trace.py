"""A native T1 trace must not masquerade as a provider field or evade recheck."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.acceptance import assess_round
from agentic.fixtures_v3 import metric_case
from agentic.runtime import ClosedLoop,RunLog


class Exp2AcceptorTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture,out,provenance=metric_case()
        class AuthoredAgent:
            def decide(self,capture,feedback=None):
                return deepcopy(out),deepcopy(provenance)
        with tempfile.TemporaryDirectory() as folder:
            cls.result=ClosedLoop(AuthoredAgent(),RunLog(folder),True,max_revisions=0,
                                  corridor_policy='moving_seed').round(cls.capture)

    def test_native_trace_is_reconstructed_and_synthetic_remains_synthetic(self):
        result=self.result
        self.assertEqual(result['verification']['verdict'],'PASS')
        self.assertTrue(result['acceptance']['software_success'])
        self.assertFalse(result['acceptance']['strict_live_physical_success'])
        self.assertEqual(result['acceptance']['status'],'SYNTHETIC_SOFTWARE_SUCCESS')
        self.assertNotIn('metric_contract_trace',result['provenance'])
        self.assertIn('metric_contract_trace',result['scene']['provenance'])

    def test_changed_provider_field_is_still_rejected(self):
        result=deepcopy(self.result);result['scene']['provenance']['model_id']='altered'
        verdict=assess_round(result,self.capture)
        self.assertFalse(verdict['software_success'])
        self.assertEqual(verdict['reasons'][0]['code'],'PROVENANCE_MISMATCH')

    def test_changed_trace_cannot_pass_original_scene_reconstruction(self):
        result=deepcopy(self.result)
        result['scene']['provenance']['metric_contract_trace']['source_output']['hypotheses']['confidence_note']='tampered'
        verdict=assess_round(result,self.capture)
        self.assertFalse(verdict['software_success'])
        self.assertEqual(verdict['reasons'][0]['code'],'T1_RECONSTRUCTION_MISMATCH')


if __name__=='__main__':unittest.main()
