"""Mock transport tests; never counted as real model or G2 experiments."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from agentic.contracts import Failure
from agentic.fixtures import capture,ScriptedAgent

path=Path(__file__).resolve().parents[1]/'experiments/nuscenes/run_cases.py'
spec=importlib.util.spec_from_file_location('nuscenes_batch_under_test',path)
batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)

class NuScenesBatchFailureTests(unittest.TestCase):
    def run_two(self,code):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);inputs=root/'runtime_inputs';inputs.mkdir();img=root/'unit.png';Image.new('RGB',(8,8)).save(img)
            cap=capture();cap['observations'][0]['path']=str(img)
            for name in ['a','b']:(inputs/(name+'.json')).write_text(json.dumps(cap))
            index=root/'runtime_index.json';index.write_text(json.dumps({'cases':[{'sample_id':name,'capture':'runtime_inputs/'+name+'.json'} for name in ['a','b']]}))
            output,prov=ScriptedAgent().decide(cap)
            agent=Mock();agent.public_config={'unit_test':True};agent.decide.side_effect=[Failure(code,'unit-test failure'),(output,prov)]
            fake_loop=Mock();fake_loop.round.return_value={'status':'blocked_no_execution'}
            with patch.object(batch,'make_agent',return_value=agent),patch.object(batch,'ClosedLoop',return_value=fake_loop):
                _,summary=batch.run(index,root/'logs',repeats=1,max_revisions=1)
            return summary,agent.decide.call_count
    def test_one_slow_case_does_not_skip_other_observations(self):
        summary,calls=self.run_two('CODEX_TIMEOUT')
        self.assertEqual(calls,2);self.assertEqual(summary['cases'][0]['status'],'MODEL_OR_TOOL_FAILURE')
        self.assertEqual(summary['cases'][1]['status'],'blocked_no_execution')
    def test_global_model_denial_blocks_remaining_requests(self):
        summary,calls=self.run_two('CODEX_MODEL_UNAVAILABLE')
        self.assertEqual(calls,1);self.assertEqual(summary['cases'][1]['status'],'BLOCKED_MODEL_ACCESS')


    def test_zero_evaluation_calls_does_not_gate_the_planning_round(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);inputs=root/'runtime_inputs';inputs.mkdir();img=root/'unit.png';Image.new('RGB',(8,8)).save(img)
            cap=capture();cap['observations'][0]['path']=str(img);(inputs/'a.json').write_text(json.dumps(cap))
            index=root/'runtime_index.json';index.write_text(json.dumps({'cases':[{'sample_id':'a','capture':'runtime_inputs/a.json'}]}))
            agent=Mock();agent.public_config={'unit_test':True};fake_loop=Mock()
            fake_loop.round.return_value={'status':'blocked_no_execution','statistics':{'model_attempts':1,'model_responses':1}}
            with patch.object(batch,'make_agent',return_value=agent),patch.object(batch,'ClosedLoop',return_value=fake_loop):
                _,summary=batch.run(index,root/'logs',repeats=0,max_revisions=1)
            fake_loop.round.assert_called_once();agent.decide.assert_not_called()
            self.assertEqual(summary['all_model_attempts'],1);self.assertEqual(summary['experiment_verdict'],'NO_PHYSICAL_SUCCESS')
            self.assertEqual(summary['strict_live_physical_successes'],0)
