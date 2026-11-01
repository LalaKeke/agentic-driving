"""Authored physical candidates: task selection never replaces T4 safety."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agentic import t1,t2,candidate_search
from agentic.contracts import digest
from agentic.fixtures import rect
from agentic.turn_task import evaluate_turn
from test_seed_candidates import authored_case


class CandidateTaskFilterTests(unittest.TestCase):
    def test_existing_verified_curved_candidate_can_win_without_changing_default(self):
        cap,out,prov=authored_case(speed=4.,half_road=20.)
        scene=t1.construct(cap,out,prov,True);before=digest(scene)
        original,old_ledger,_=candidate_search.plan(scene,profile=t2.SEARCH_PROFILE_V2)
        self.assertIsNotNone(original);self.assertEqual(original['T4']['verdict'],'PASS')
        self.assertEqual(evaluate_turn(scene,original['T3'])['verdict'],'FAIL')
        self.assertEqual(old_ledger['selected_candidate'],'original')
        self.assertNotIn('task_filter_active',old_ledger)
        self.assertTrue(all('TURN_TASK' not in row for row in old_ledger['candidates']))
        saved={}
        selected,ledger,failure=candidate_search.plan(scene,
            save=lambda candidate,name,value:saved.setdefault(candidate,{ }).update({name:value}),
            profile=t2.SEARCH_PROFILE_V2,task_filter=lambda trajectory:evaluate_turn(scene,trajectory))
        self.assertIsNone(failure);self.assertIsNotNone(selected)
        self.assertEqual(selected['T4']['verdict'],'PASS')
        self.assertEqual(evaluate_turn(scene,selected['T3'])['verdict'],'PASS')
        eligible=[row for row in ledger['candidates'] if row['TURN_TASK']=='PASS']
        self.assertEqual(selected['T3']['solver']['objective'],min(row['objective'] for row in eligible))
        straight=next(row for row in ledger['candidates'] if row['candidate_id']=='original')
        self.assertEqual((straight['T4'],straight['TURN_TASK']),('PASS','FAIL'))
        self.assertEqual(saved['original']['TURN_TASK']['verdict'],'FAIL')
        self.assertEqual(before,digest(scene))

    def test_all_task_failures_preserve_physical_pass_and_never_execute(self):
        cap,out,prov=authored_case(speed=4.,half_road=20.)
        out['decision']['target_region']=rect(-8.,30.,-.05,.05)
        prov['output_digest']=digest(out);scene=t1.construct(cap,out,prov,True)
        selected,ledger,failure=candidate_search.plan(scene,profile=t2.SEARCH_PROFILE_V2,
            task_filter=lambda trajectory:evaluate_turn(scene,trajectory))
        self.assertIsNone(selected);self.assertEqual(failure['code'],'TURN_TASK_NOT_DEMONSTRATED')
        physical=[row for row in ledger['candidates'] if row['T4']=='PASS']
        self.assertTrue(physical);self.assertTrue(all(row['TURN_TASK']=='FAIL' for row in physical))
        self.assertTrue(all('failure' not in row for row in physical))
        spec=importlib.util.spec_from_file_location('task_filter_runner',ROOT/'implementation/experiments/run_local_prototype.py')
        runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory(prefix='authored-task-filter-') as tmp:
            # T1 is separately validated above in explicit synthetic mode. This
            # negative runtime test must stop before constructing the executor.
            with patch.object(runner.t1,'construct',return_value=scene),patch.object(runner,'SimulatedExecutor',side_effect=AssertionError('Task failure reached executor')):
                result,reason,stages=runner.physical(cap,out,prov,cap,0,Path(tmp),
                    policy=t2.SEARCH_PROFILE_V2,require_turn=True)
            self.assertIsNone(result);self.assertEqual(reason['code'],'TURN_TASK_NOT_DEMONSTRATED')
            self.assertEqual((stages['T4'],stages['TURN_TASK']),('PASS','FAIL'))
            self.assertEqual(stages['EXECUTION'],'NOT_REACHED')
            self.assertFalse((Path(tmp)/'execution.json').exists())


if __name__=='__main__':unittest.main()
