"""Prompt/transport/audit regressions only; mocks never count as real model or G2 data."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from agentic.adapter import OnlineAgent
from agentic.contracts import ROOT,Failure,resolve_prompt,planning_contract
from agentic.fixtures import capture,ScriptedAgent

REPO=ROOT.parent
PROMPT=ROOT/'prompts/driving_v2_2.txt'
spec=importlib.util.spec_from_file_location('audit_contract_failures',ROOT/'experiments/nuscenes/audit_contract_failures.py')
audit_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit_module)


class Prompt22Tests(unittest.TestCase):
    def request(self,cap,output):
        """Return the request body and MOCK provenance; never make a network call."""
        seen=[]
        def handler(req):
            seen.append(json.loads(req.content))
            return httpx.Response(200,json={'model':'unit-test-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]})
        env={'DRIVING_API_BASE':'https://example.invalid/v1','DRIVING_MODEL':'unit-test-model','DRIVING_API_KEY':'UNIT_ONLY_NOT_A_REAL_SECRET','DRIVING_OUTPUT_MODE':'json_schema','DRIVING_OUTPUT_CONTRACT':'2.1.0','DRIVING_PROMPT_VERSION':'driving_v2_2','DRIVING_RETRIES':'0'}
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,env):
            path=Path(folder)/'protocol-only.png';path.write_bytes(b'\x89PNG\r\n\x1a\nUNIT_PROTOCOL_BYTES_NOT_A_REAL_SCENE')
            supplied=deepcopy(cap);supplied['observations'][0]['path']=str(path);before=deepcopy(supplied)
            agent=OnlineAgent(httpx.Client(transport=httpx.MockTransport(handler)))
            returned,prov=agent.decide(supplied)
            self.assertEqual(supplied,before,'Transport must not mutate trusted acquisition/ego inputs')
        self.assertEqual(len(seen),1);return seen[0],returned,prov

    def test_explicit_prompt_revision_keeps_schema_and_old_defaults(self):
        self.assertEqual(resolve_prompt('2.1.0','driving_v2_2'),('agent_output_v2_1.schema.json','driving_v2_2'))
        self.assertEqual(resolve_prompt('2.1.0'),('agent_output_v2_1.schema.json','driving_v2_1'))
        self.assertEqual(resolve_prompt('2.0.0'),('agent_output_v2.schema.json','driving_v2'))
        with self.assertRaises(Failure):resolve_prompt('2.0.0','driving_v2_2')

    def test_mock_transport_sends_complete_derived_requirements_and_hashes_prompt(self):
        cap=capture();output,_=ScriptedAgent().decide(cap)
        request,returned,prov=self.request(cap,output)
        self.assertEqual(request['messages'][0]['content'],PROMPT.read_text())
        sent=json.loads(request['messages'][1]['content'][0]['text']);contract=sent['tool_contract']
        self.assertEqual(contract,planning_contract(cap));self.assertEqual(len(contract['occupancy_grid']),25)
        self.assertEqual(contract['occupancy_grid'][0],{'start_s':0.,'end_s':.2,'kind':'whole_interval_envelope'})
        self.assertEqual(contract['occupancy_grid'][-1]['end_s'],5.)
        self.assertEqual(contract['decision_valid_until_min_s'],105.)
        self.assertEqual(contract['execution_prefix_s'],.2)
        self.assertEqual(contract['supported_native_metric_scale_labels'],['model_metric_hypothesis','unknown'])
        self.assertEqual(returned,output);self.assertEqual(prov['source'],'mock_transport')
        self.assertEqual(prov['agent_output_contract'],'2.1.0');self.assertEqual(prov['prompt_version'],'driving_v2_2')
        self.assertEqual(prov['prompt_sha256'],hashlib.sha256(PROMPT.read_bytes()).hexdigest())
        self.assertNotIn('UNIT_ONLY_NOT_A_REAL_SECRET',json.dumps(prov))

    def test_grid_and_validity_follow_configuration_not_the_default_25_literal(self):
        cap=capture();cap['metadata'].update(horizon_s=2.,step_s=.5,valid_until_s=102.)
        output,_=ScriptedAgent().decide(cap)
        request,_,_=self.request(cap,output);sent=json.loads(request['messages'][1]['content'][0]['text'])
        grid=sent['tool_contract']['occupancy_grid']
        self.assertEqual([(x['start_s'],x['end_s']) for x in grid],[(0.,.5),(.5,1.),(1.,1.5),(1.5,2.)])
        self.assertEqual(sent['tool_contract']['decision_valid_until_min_s'],102.)
        self.assertEqual(sent['tool_contract']['supplied_valid_until_s'],102.)

    def test_unknown_response_is_preserved_not_filled_to_create_success(self):
        cap=capture();output,_=ScriptedAgent().decide(cap);h=output['hypotheses']
        h['scale_basis']='unknown';h['road']=None;h['unbounded_unknown']=True
        for obj in h['objects']:obj.update(position_m=None,velocity_mps=None,uncertainty_m=None,occupancy=None)
        _,returned,prov=self.request(cap,output)
        self.assertEqual(returned,output)
        self.assertIsNone(returned['hypotheses']['objects'][0]['occupancy'])
        self.assertEqual(prov['source'],'mock_transport')

    def test_original_prompt_and_schema_bytes_are_preserved(self):
        ledger=json.loads((ROOT/'results/gpt61_live/raw_runs/20261027T161613Z-a58e5a49/0001-source-hashes.json').read_text())
        for relative in ['prompts/driving_v2_1.txt','schemas/agent_output_v2_1.schema.json']:
            self.assertEqual(hashlib.sha256((ROOT/relative).read_bytes()).hexdigest(),ledger[relative])


class ContractAuditTests(unittest.TestCase):
    def test_interval_audit_distinguishes_unknown_from_incomplete_and_complete_coverage(self):
        self.assertEqual(audit_module.grid_status(None,5.,.2),'unknown')
        whole=[{'start_s':0.,'end_s':5.}]
        self.assertEqual(audit_module.grid_status(whole,5.,.2),'wrong_interval_count')
        grid=[{'start_s':k*.2,'end_s':(k+1)*.2} for k in range(25)]
        self.assertEqual(audit_module.grid_status(grid,5.,.2),'complete_grid')
        grid[10]['end_s']+=.01
        self.assertEqual(audit_module.grid_status(grid,5.,.2),'wrong_interval_boundaries')

    def test_target_erosion_diagnostic_reproduces_convex_half_plane_offsets(self):
        target=[[-3.,-4.],[3.,-4.],[3.,4.],[-3.,4.]]
        inward=audit_module.eroded_polygon(target,1.2)
        self.assertAlmostEqual(audit_module.polygon_area(inward),3.6*5.6)
        self.assertEqual(audit_module.polygon_area(audit_module.eroded_polygon([[-.1,-.1],[.1,-.1],[.1,.1],[-.1,.1]],1.2)),0.)

    def test_archived_audit_keeps_all_protocols_and_does_not_modify_source(self):
        results=ROOT/'results/gpt61_live';ledger=json.loads((results/'SHA256_MANIFEST.json').read_text())['files']
        def hashes():return {name:hashlib.sha256((results/name).read_bytes()).hexdigest() for name in ledger}
        before=hashes()
        with tempfile.TemporaryDirectory() as folder:
            report=audit_module.audit(results,Path(folder))
            self.assertEqual(report['counts']['responses'],10)
            self.assertEqual(report['counts']['unique_observation_windows'],4)
            self.assertEqual(report['counts']['original_contract_20_responses'],9)
            self.assertEqual(report['counts']['separate_contract_21_responses'],1)
            self.assertEqual(report['counts']['short_planning_validity_responses'],6)
            self.assertEqual(report['counts']['target_region_empty_after_current_t2_erosion_responses'],10)
            self.assertEqual(report['counts']['nonnull_occupancy_entries'],54)
            self.assertEqual(report['counts']['wrong_grid_nonnull_occupancy_entries'],54)
            self.assertEqual(report['counts']['unknown_occupancy_entries'],5)
            self.assertTrue((Path(folder)/'contract_audit.json').is_file())
            self.assertTrue((Path(folder)/'contract_audit.md').is_file())
        self.assertEqual(hashes(),before)
