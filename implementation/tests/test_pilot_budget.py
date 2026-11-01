"""Append-only budget accounting, without creating a transport or calling models."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
spec=importlib.util.spec_from_file_location('controlled_pilot',ROOT/'implementation/experiments/exp2_controlled_pilot.py')
pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)
from agentic.contracts import Failure
from agentic.fixtures import capture


class PilotBudgetTests(unittest.TestCase):
    def test_failure_still_charges_and_third_call_cannot_be_claimed(self):
        with tempfile.TemporaryDirectory() as p:
            ledger=pilot.CallBudget(Path(p)/'budget')
            for n in (1,2):
                self.assertEqual(ledger.claim(capture(),'authored test; no transport'),n)
                ledger.finish(n,dict(turn_start_requests=1),dict(status='FAILED_AUTHORED_TEST'))
            with self.assertRaises(Failure):ledger.claim(capture(),'forbidden third')
            result=ledger.seal();self.assertEqual(result['charged'],2);self.assertEqual(result['unused'],0)

    def test_existing_ledger_cannot_restart_budget(self):
        with tempfile.TemporaryDirectory() as p:
            path=Path(p)/'budget';ledger=pilot.CallBudget(path);ledger.claim(capture(),'first')
            with self.assertRaises(Failure):pilot.CallBudget(path)

    def test_not_reached_stages_are_never_passed(self):
        self.assertTrue(all(x['status']=='NOT_REACHED' for x in pilot.stages().values()))
        self.assertFalse(pilot.not_reached_acceptance('upstream_failed')['strict_live_physical_success'])


if __name__=='__main__':unittest.main()
