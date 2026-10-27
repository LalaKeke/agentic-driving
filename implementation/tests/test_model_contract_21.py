"""Explicit unknown occupancy is a versioned input state, never permission to plan."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.fixtures import capture,ScriptedAgent
from agentic.contracts import Failure,digest,validate,model_contract
from agentic import t1

class ModelContract21Tests(unittest.TestCase):
    def inputs(self):
        cap=capture();out,prov=ScriptedAgent().decide(cap)
        out['hypotheses']['objects'][0]['occupancy']=None;prov.update(agent_output_contract='2.1.0',output_digest=digest(out))
        return cap,out,prov
    def test_unknown_occupancy_validates_only_in_explicit_new_contract(self):
        _,out,_=self.inputs();validate('agent_output_v2_1.schema.json',out)
        with self.assertRaises(Failure):validate('agent_output_v2.schema.json',out)
    def test_unknown_occupancy_returns_specific_infeasibility(self):
        cap,out,prov=self.inputs()
        with self.assertRaises(Failure) as ctx:t1.construct(cap,out,prov,True)
        self.assertEqual(ctx.exception.record['code'],'OCCUPANCY_UNKNOWN');self.assertEqual(ctx.exception.record['category'],'tool_infeasible')
    def test_old_valid_scenes_keep_their_meaning(self):
        cap=capture();out,prov=ScriptedAgent().decide(cap)
        before=t1.construct(cap,out,prov,True);prov['agent_output_contract']='2.1.0';after=t1.construct(cap,out,prov,True)
        for field in ['metadata','hypotheses','decision']:self.assertEqual(before[field],after[field])
    def test_unknown_version_is_rejected(self):
        with self.assertRaises(Failure):model_contract('future-unknown-version')

    def test_calibrated_label_is_not_a_verified_plane_or_projection(self):
        cap=capture();out,prov=ScriptedAgent().decide(cap);out['hypotheses']['scale_basis']='calibrated_ground_plane';prov['output_digest']=digest(out)
        with self.assertRaises(Failure) as ctx:t1.construct(cap,out,prov,True)
        self.assertEqual(ctx.exception.record['code'],'CALIBRATED_PROJECTION_UNVERIFIED')
