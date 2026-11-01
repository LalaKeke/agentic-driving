"""Explicit v3 planner labels with unchanged legacy requests; mock HTTP only."""
from copy import deepcopy
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'implementation'),str(Path(__file__).resolve().parent),
              str(ROOT/'implementation/experiments')]
from agentic.contracts import Failure, planning_contract
from agentic.openai_adapter import (OpenAIResponsesAgent, PLANNER_V2, PLANNER_V3,
                                    PLANNER_V3_NOTE, PLANNER_V4, PLANNER_V4_NOTE, PROTOTYPE_PROFILE)
import test_openai_adapter as fixture
import test_openai_clip as clip_fixture
import run_local_prototype as local_runner


class OpenAIPlannerProfileTests(unittest.TestCase):
    @patch.dict(os.environ,fixture.ENV)
    def test_none_and_explicit_v2_preserve_legacy_request_bytes(self):
        captured=[]
        with tempfile.TemporaryDirectory(prefix='mock-planner-profile-') as temp:
            for index,profile in enumerate((None,PLANNER_V2)):
                root=Path(temp)/str(index);root.mkdir()
                def handler(request):
                    captured.append(request.content)
                    return httpx.Response(200,json=fixture.response(json.dumps(fixture.authored_case(speed=3.,half_road=10.)[1])))
                cap,expected,agent=fixture.OpenAIAdapterTests().make(root,handler,planner_profile=profile)
                feedback={'code':'AUTHORED_FEEDBACK'}
                returned,provenance=agent.decide(cap,feedback)
                self.assertEqual(returned,expected);self.assertEqual(provenance['source'],'mock_transport')
                self.assertEqual(agent.public_config['planner_profile'],PLANNER_V2)
                # Reproduce the pre-parameter legacy request construction, not
                # merely the presence of a new planner field.
                safe=deepcopy(cap);images=[]
                for number,(observed,metadata) in enumerate(zip(cap['observations'],safe['observations'])):
                    metadata['path']=f'attached:frame_{number:03d}.png'
                    images.append(dict(type='input_image',image_url='data:image/png;base64,'+
                                       base64.b64encode(Path(observed['path']).read_bytes()).decode('ascii'),detail='auto'))
                contract=planning_contract(cap,'3.0.0-exp2')
                contract['experiment_profile']=PROTOTYPE_PROFILE
                contract['scope_interpretation']='Coverage is conditional on the MODEL-declared finite local domain and adopted two-second motion conditions; not exhaustive whole-world futures. All declared obstacles and local unknowns retain the same checks; tools never delete, forecast or fill them.'
                contract['planner_profile']=PLANNER_V2
                text=json.dumps(dict(capture=safe,feedback=feedback,tool_contract=contract),ensure_ascii=False)
                old=dict(model='authored-test-model',instructions=(ROOT/'implementation/prompts/driving_prototype_v3.txt').read_text(),
                    input=[dict(role='user',content=[dict(type='input_text',text=text)]+images)],
                    text=dict(format=dict(type='json_schema',name='driving_v3_exp2',strict=True,
                        schema=json.loads((fixture.WIRE/'wire_schema.json').read_text()))),
                    tools=[],reasoning=dict(effort='medium'),stream=False,store=False)
                self.assertEqual(captured[-1],json.dumps(old,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode())
                self.assertNotIn('planner_profile_note',contract)
                agent.client.close()
        self.assertEqual(captured[0],captured[1])

    @patch.dict(os.environ,fixture.ENV)
    def test_explicit_v3_changes_dynamic_contract_only_and_preserves_native(self):
        with tempfile.TemporaryDirectory(prefix='mock-v3-planner-') as temp:
            root=Path(temp);requests=[]
            expected=fixture.authored_case(speed=3.,half_road=10.)[1]
            raw=' \n'+json.dumps(expected)+'\n'
            def handler(request):
                requests.append(request)
                return httpx.Response(200,json=fixture.response(raw))
            cap,_,agent=fixture.OpenAIAdapterTests().make(root,handler,planner_profile=PLANNER_V3)
            before=deepcopy(cap);returned,provenance=agent.decide(cap)
            payload=json.loads(requests[0].content)
            context=json.loads(payload['input'][0]['content'][0]['text'])
            self.assertEqual(context['tool_contract']['planner_profile'],PLANNER_V3)
            self.assertEqual(context['tool_contract']['planner_profile_note'],PLANNER_V3_NOTE)
            self.assertEqual(agent.public_config['planner_profile'],PLANNER_V3)
            self.assertEqual(payload['instructions'],(ROOT/'implementation/prompts/driving_prototype_v3.txt').read_text())
            self.assertIn('bounded_seed_candidates_v2',payload['instructions'])
            self.assertEqual(returned,expected);self.assertEqual(cap,before)
            self.assertEqual((root/'transport/attempt_01/final_agent_message.txt').read_text(),raw)
            self.assertEqual(provenance['source'],'mock_transport')
            self.assertEqual(len(requests),1)
            self.assertEqual(agent.last_attempt_summary['api_post_requests'],1)
            agent.client.close()

    @patch.dict(os.environ,fixture.ENV)
    def test_explicit_v4_is_labeled_and_preserves_native_business_values(self):
        with tempfile.TemporaryDirectory(prefix='mock-v4-planner-') as temp:
            root=Path(temp);requests=[]
            expected=fixture.authored_case(speed=3.,half_road=10.)[1]
            raw=' \n'+json.dumps(expected)+'\n'
            def handler(request):
                requests.append(request)
                return httpx.Response(200,json=fixture.response(raw))
            cap,_,agent=fixture.OpenAIAdapterTests().make(root,handler,planner_profile=PLANNER_V4)
            before=deepcopy(cap);returned,provenance=agent.decide(cap)
            payload=json.loads(requests[0].content)
            context=json.loads(payload['input'][0]['content'][0]['text'])
            self.assertEqual(context['tool_contract']['planner_profile'],PLANNER_V4)
            self.assertEqual(context['tool_contract']['planner_profile_note'],PLANNER_V4_NOTE)
            self.assertEqual(payload['instructions'],(ROOT/'implementation/prompts/driving_prototype_v3.txt').read_text())
            self.assertEqual(returned,expected);self.assertEqual(cap,before)
            self.assertEqual((root/'transport/attempt_01/final_agent_message.txt').read_text(),raw)
            self.assertEqual(provenance['source'],'mock_transport')
            self.assertEqual(len(requests),1);agent.client.close()

    @patch.dict(os.environ,fixture.ENV)
    def test_explicit_profiles_rejected_for_generic_or_incompatible_prompt(self):
        cases=[('driving_v3_exp2',None,PLANNER_V2),('driving_v3_exp2',None,PLANNER_V3),
               ('driving_prototype_v1',PROTOTYPE_PROFILE,PLANNER_V3),
               ('driving_prototype_v3',PROTOTYPE_PROFILE,'unknown_planner')]
        with tempfile.TemporaryDirectory(prefix='mock-planner-reject-') as temp:
            for index,(prompt,experiment,planner) in enumerate(cases):
                with self.subTest(prompt=prompt,planner=planner):
                    path=Path(temp)/str(index)
                    with httpx.Client(transport=httpx.MockTransport(lambda _:self.fail('No request allowed'))) as client:
                        with self.assertRaises(Failure) as caught:
                            OpenAIResponsesAgent(evidence_dir=path,wire_schema_bundle=fixture.WIRE,
                                prompt_version=prompt,experiment_profile=experiment,planner_profile=planner,client=client)
                    self.assertEqual(caught.exception.record['code'],'API_CONFIGURATION')
                    self.assertFalse(path.exists())

    @patch.dict(os.environ,fixture.ENV)
    def test_v3_service_failure_is_one_post_without_retry(self):
        with tempfile.TemporaryDirectory(prefix='mock-v3-one-post-') as temp:
            requests=[]
            def handler(request):
                requests.append(request)
                return httpx.Response(429,json={'error':{'type':'rate_limit_error','code':'authored'}})
            cap,_,agent=fixture.OpenAIAdapterTests().make(Path(temp),handler,planner_profile=PLANNER_V3,max_calls=1)
            with self.assertRaises(Failure) as first:agent.decide(cap)
            self.assertEqual(first.exception.record['code'],'API_HTTP')
            with self.assertRaises(Failure) as second:agent.decide(cap)
            self.assertEqual(second.exception.record['code'],'API_BUDGET')
            self.assertEqual(len(requests),1)
            self.assertEqual(agent.public_config['request_max_retries'],0)
            self.assertEqual(agent.last_attempt_summary['api_post_requests'],1)
            agent.client.close()

    def test_clip_passes_actual_v3_profile_before_any_request(self):
        with tempfile.TemporaryDirectory(prefix='mock-clip-planner-') as temp:
            root=Path(temp);path=clip_fixture.ClipTests().prepare(root)
            plan=json.loads(path.read_text());plan['planner_profile']=PLANNER_V3;path.write_text(json.dumps(plan))
            observed=[]
            def stop_at_constructor(**kwargs):
                observed.append(kwargs)
                raise Failure('API_CONFIGURATION','AUTHORED constructor stop; no credential or HTTP access')
            with patch.dict(os.environ,DRIVING_MODEL='authored-mock-model'),patch.object(clip_fixture.runner,'OpenAIResponsesAgent',stop_at_constructor):
                self.assertEqual(clip_fixture.runner.run(path),3)
            self.assertEqual(observed[0]['planner_profile'],PLANNER_V3)
            self.assertEqual(clip_fixture.runner.read(root/'batch_summary.json')['charged_requests'],0)

    def test_local_online_passes_actual_v3_profile_before_any_request(self):
        with tempfile.TemporaryDirectory(prefix='mock-local-planner-') as temp:
            root=Path(temp);capture=root/'capture.json';capture.write_text(json.dumps(fixture.authored_case(speed=3.,half_road=10.)[0]))
            args=SimpleNamespace(replay=None,capture=capture,out=root/'result',wire=fixture.WIRE,
                planner_profile=PLANNER_V3,prompt_version='driving_prototype_v3',backend='openai_api',
                max_requests=2,deadline_utc='2099-01-01T00:00:00Z',timeout_s=450)
            observed=[]
            def stop_at_constructor(**kwargs):
                observed.append(kwargs)
                raise Failure('API_CONFIGURATION','AUTHORED constructor stop; no credential or HTTP access')
            with patch.dict(os.environ,fixture.ENV),patch.object(local_runner,'OpenAIResponsesAgent',stop_at_constructor):
                self.assertEqual(local_runner.main(args),3)
            self.assertEqual(observed[0]['planner_profile'],PLANNER_V3)
            self.assertEqual(json.loads((root/'result/summary.json').read_text())['charged_requests'],0)


if __name__=='__main__':
    unittest.main()
