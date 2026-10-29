"""Offline budget/control tests; all transport and physics are explicit mocks."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agentic.contracts import Failure
import test_local_prototype as fixtures
spec=importlib.util.spec_from_file_location('budgeted_prototype',ROOT/'implementation/experiments/run_local_prototype.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


class PrototypeRequestBudgetTests(unittest.TestCase):
    def run_mock(self,limit,mode):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cap,out,prov=fixtures.case();path=root/'capture.json';path.write_text(json.dumps(cap))
            class AuthoredTransport:
                calls=0
                def __init__(self,**kwargs):pass
                def preflight(self):pass
                def decide(self,*args):
                    AuthoredTransport.calls+=1
                    if mode=='service':raise Failure('CODEX_SERVICE_FAILURE','AUTHORED offline service failure')
                    return out,dict(source='mock_transport')
            args=argparse.Namespace(out=root/'run',capture=path,wire=root/'unused',replay=None,
                timeout_s=1,deadline_utc='2099-01-01T00:00:00Z',max_requests=limit)
            stages={k:'NOT_REACHED' for k in ('T1','T2','T3','T4','EXECUTION','ACCEPTOR')}
            physical=({'prototype_conditional_success':True},None,stages) if mode=='success' else (None,dict(code='AUTHORED_TOOL_REFUSAL'),stages)
            with patch.object(runner,'PilotSubscriptionAgent',AuthoredTransport),patch.object(runner,'physical',return_value=physical):
                runner.main(args)
            summary=json.loads((args.out/'summary.json').read_text())
            return AuthoredTransport.calls,summary

    def test_two_request_quota_counts_both_semantic_failures(self):
        calls,summary=self.run_mock(2,'semantic');self.assertEqual(calls,2)
        self.assertEqual(summary['charged_requests'],2);self.assertEqual(summary['remaining_unused'],0)

    def test_service_error_stops_without_using_remaining_quota(self):
        calls,summary=self.run_mock(3,'service');self.assertEqual(calls,1)
        self.assertEqual(summary['charged_requests'],1)

    def test_consumed_success_stops_before_revision(self):
        calls,summary=self.run_mock(3,'success');self.assertEqual(calls,1)
        self.assertTrue(summary['prototype_conditional_success'])

    def test_explicit_api_route_preserves_terminal_error_and_public_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cap,_,_=fixtures.case();path=root/'capture.json';path.write_text(json.dumps(cap))
            class AuthoredAPI:
                calls=0
                def __init__(self,**kwargs):
                    self.public_config=dict(backend='official_openai_responses',source='mock_transport',
                        model=kwargs['model'],adapter_retries=0)
                    self.last_attempt_summary=None
                    self.kwargs=kwargs
                def preflight(self):pass
                def decide(self,*args):
                    AuthoredAPI.calls+=1
                    self.last_attempt_summary=dict(api_post_requests=1,model_service_returned=False)
                    raise Failure('API_HTTP','AUTHORED API service failure, no retry')
            args=argparse.Namespace(out=root/'run',capture=path,wire=root/'unused',replay=None,
                timeout_s=1,deadline_utc='2099-01-01T00:00:00Z',max_requests=2,backend='openai_api')
            with patch.dict(runner.os.environ,{'DRIVING_MODEL':'registered-mock-model'}), \
                 patch.object(runner,'OpenAIResponsesAgent',AuthoredAPI), \
                 patch.object(runner,'PilotSubscriptionAgent',side_effect=AssertionError('Wrong backend')):
                runner.main(args)
            summary=json.loads((args.out/'summary.json').read_text())
            config=json.loads((args.out/'config.json').read_text())
            public=json.loads((args.out/'transport_config.json').read_text())
            self.assertEqual(AuthoredAPI.calls,1)
            self.assertEqual(summary['backend'],'openai_api')
            self.assertEqual(summary['charged_requests'],1)
            self.assertEqual(summary['remaining_unused'],1)
            self.assertEqual(summary['fatal']['code'],'API_HTTP')
            self.assertEqual(config['backend'],'openai_api')
            self.assertIn('implementation/agentic/openai_adapter.py',config['actual_source_sha256'])
            self.assertEqual(public['source'],'mock_transport')
            self.assertEqual(public['adapter_retries'],0)


if __name__=='__main__':unittest.main()
