"""Error taxonomy regression: valid unknowns remain rejected, never imputed."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic import t1
from agentic.contracts import Failure,digest,validate
from agentic.fixtures import capture,ScriptedAgent

class T1UnknownClassificationTests(unittest.TestCase):
    def inputs(self):
        c=capture();o,p=ScriptedAgent().decide(c);p['agent_output_contract']='2.1.0'
        return c,o,p
    def test_schema_valid_unknown_scale_is_tool_infeasible(self):
        c,o,p=self.inputs();o['hypotheses']['scale_basis']='unknown';p['output_digest']=digest(o)
        validate('agent_output_v2_1.schema.json',o);before=deepcopy(o)
        with self.assertRaises(Failure) as caught:t1.construct(c,o,p,True)
        self.assertEqual(caught.exception.record['code'],'MISSING_SCALE')
        self.assertEqual(caught.exception.record['category'],'tool_infeasible')
        self.assertEqual(before,o)
    def test_invalid_scale_label_remains_interface_error(self):
        c,o,p=self.inputs();o['hypotheses']['scale_basis']='invented-scale';p['output_digest']=digest(o)
        with self.assertRaises(Failure) as caught:t1.construct(c,o,p,True)
        self.assertEqual(caught.exception.record['code'],'SCHEMA_ERROR')
        self.assertEqual(caught.exception.record['category'],'interface_error')
    def test_null_occupancy_is_still_rejected(self):
        c,o,p=self.inputs();o['hypotheses']['objects'][0]['occupancy']=None;p['output_digest']=digest(o)
        with self.assertRaises(Failure) as caught:t1.construct(c,o,p,True)
        self.assertEqual(caught.exception.record['code'],'OCCUPANCY_UNKNOWN')
        self.assertEqual(caught.exception.record['category'],'tool_infeasible')
