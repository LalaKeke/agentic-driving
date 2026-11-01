"""Contract plumbing under an in-process mock transport, never real inference."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.adapter import OnlineAgent
from agentic.contracts import Failure,resolve_prompt
from agentic.diagnostics import diagnose
from agentic.fixtures_v3 import metric_case
from agentic.g2_metric import SEMANTICS
from agentic.metric_contract import VERSION,MEASUREMENT_SEMANTICS
from agentic import t1


class MetricIntegrationTests(unittest.TestCase):
    def test_http_schema_prompt_tool_contract_and_provenance_are_explicit(self):
        cap,output,_=metric_case()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'authored.png'
            # A signature stub is enough for adapter input validation; it is not
            # a real image/model experiment and the mock does not decode pixels.
            path.write_bytes(b'\x89PNG\r\n\x1a\nAUTHORED_MOCK_ONLY')
            for f in cap['observations']:f['path']=str(path)
            sent=[]
            def mock(request):
                sent.append(json.loads(request.content))
                return httpx.Response(200,json={'model':'configured-test-service',
                    'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output)}}]})
            env=dict(DRIVING_API_BASE='https://fixture.invalid/v1',DRIVING_MODEL='configured-test-service',
                     DRIVING_API_KEY='test-only-not-real',DRIVING_OUTPUT_CONTRACT=VERSION,
                     DRIVING_PROMPT_VERSION='driving_v3_exp2',DRIVING_RETRIES='0',DRIVING_OUTPUT_MODE='json_schema')
            with patch.dict(os.environ,env),httpx.Client(transport=httpx.MockTransport(mock)) as client:
                result,prov=OnlineAgent(client=client).decide(cap)
            self.assertEqual(len(sent),1)
            self.assertEqual(prov['source'],'mock_transport')
            self.assertEqual(prov['agent_output_contract'],VERSION)
            self.assertEqual(prov['prompt_version'],'driving_v3_exp2')
            payload=json.loads(sent[0]['messages'][1]['content'][0]['text'])
            self.assertEqual(payload['tool_contract']['measurement_semantics'],MEASUREMENT_SEMANTICS)
            self.assertEqual(sent[0]['response_format']['json_schema']['schema']['properties']['contract_version']['const'],VERSION)
            with self.assertRaises(Failure) as ctx:t1.construct(cap,result,prov,True)
            self.assertEqual(ctx.exception.record['code'],'SOURCE')

    def test_coarse_v3_coverage_does_not_receive_legacy_grid_feedback(self):
        cap,out,_=metric_case()
        self.assertFalse(any(x['code']=='OCCUPANCY_GAP' for x in diagnose(cap,out)))

    def test_prompt_version_cannot_cross_output_contract(self):
        with self.assertRaises(Failure):resolve_prompt('2.1.0','driving_v3_exp2')
        with self.assertRaises(Failure):resolve_prompt(VERSION,'driving_v2_3')

    def test_runtime_and_offline_evaluator_use_identical_definitions(self):
        self.assertEqual(SEMANTICS,MEASUREMENT_SEMANTICS)


if __name__=='__main__':unittest.main()
