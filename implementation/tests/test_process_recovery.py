"""Engineering regressions, not online model feasibility evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic import t1,t2,t3,t4
from agentic.contracts import Failure,digest,resolve_prompt,planning_contract
from agentic.diagnostics import diagnose
from agentic.fixtures import capture,ScriptedAgent,rect
from agentic.runtime import ClosedLoop,RunLog

class ProcessRecoveryTests(unittest.TestCase):
    def test_multiple_masked_contract_faults_are_reported_without_repair(self):
        cap=capture();out,_=ScriptedAgent().decide(cap)
        out['decision']['valid_until_s']=100.2;out['decision']['target_region']=rect(-.1,.1,-.1,.1)
        out['hypotheses']['scale_basis']='calibrated_ground_plane'
        out['hypotheses']['objects'][0]['occupancy']=out['hypotheses']['objects'][0]['occupancy'][:1]
        before=deepcopy(out);codes={x['code'] for x in diagnose(cap,out)}
        self.assertTrue({'DECISION_EXPIRED','TARGET_INITIAL_STATE','CALIBRATED_PROJECTION_UNVERIFIED','OCCUPANCY_GAP'}<=codes)
        self.assertEqual(before,out)
    def test_prompt_revisions_are_explicit_not_silent_contract_changes(self):
        self.assertEqual(resolve_prompt('2.1.0'),('agent_output_v2_1.schema.json','driving_v2_1'))
        self.assertEqual(resolve_prompt('2.1.0','driving_v2_2')[0],'agent_output_v2_1.schema.json')
        with self.assertRaises(Failure):resolve_prompt('2.0.0','driving_v2_2')
        grid=planning_contract(capture())['occupancy_grid'];self.assertEqual(len(grid),25);self.assertEqual(grid[-1]['end_s'],5)
    def test_valid_zero_speed_stop_boundary_does_not_become_false_infeasible(self):
        cap=capture(speed=0);out,prov=ScriptedAgent().decide(cap)
        out['decision']['stop']=dict(center_x_max_m=0.,deadline_s=0.)
        out['hypotheses']['objects']=[];out['decision']['yield_to']=[];prov['output_digest']=digest(out)
        scene=t1.construct(cap,out,prov,True);corr=t2.build(scene)
        traj=t3.solve(scene,corr,max_iter=150);ver=t4.verify(scene,traj)
        self.assertEqual(ver['verdict'],'PASS',ver['violations'][:2]);self.assertEqual(ver['released_prefix_s'],.2)
        self.assertLess(max(abs(p['speed_mps']) for p in traj['points']),1e-5)
    def test_transport_timeout_is_not_spent_as_three_semantic_revisions(self):
        agent=Mock();agent.public_config={};agent.decide.side_effect=Failure('CODEX_TIMEOUT','mock timeout')
        with tempfile.TemporaryDirectory() as root:
            result=ClosedLoop(agent,RunLog(root),max_revisions=2).round(capture())
        self.assertEqual(agent.decide.call_count,1);self.assertFalse(result['acceptance']['strict_live_physical_success'])
        self.assertEqual(result['statistics']['model_attempts'],1);self.assertEqual(result['feedback']['failed_stage'],'Agent')
