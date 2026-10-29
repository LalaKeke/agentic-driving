"""Small in-process Responses transport tests. No network or real credential use."""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'implementation'), str(Path(__file__).resolve().parent)]
from agentic.contracts import Failure, digest
from agentic.openai_adapter import OpenAIResponsesAgent, load_api_key
from test_seed_candidates import authored_case

WIRE = ROOT / 'implementation/results/exp2_wire_enum_day_pilot_20261028_v1/registration/wire_bundle'
FAKE_KEY = 'sk-authored-test-credential-never-real'
ENV = dict(DRIVING_API_KEY=FAKE_KEY, DRIVING_API_KEY_FILE='',
           DRIVING_API_BASE='https://api.openai.com/v1', DRIVING_MODEL='authored-test-model',
           DRIVING_PROXY_URL='', DRIVING_CA_BUNDLE='')


def response(raw, **changes):
    data = dict(id='resp_authored', model='authored-test-model', status='completed',
        error=None, incomplete_details=None, usage=dict(input_tokens=12, output_tokens=34, total_tokens=46),
        output=[dict(type='reasoning', summary=[]), dict(type='message', role='assistant', status='completed',
            content=[dict(type='output_text', text=raw, annotations=[])])])
    data.update(changes)
    return data


class OpenAIAdapterTests(unittest.TestCase):
    def make(self, root, handler, **options):
        cap, out, _ = authored_case(speed=3., half_road=10.)
        image = root / 'authored.png'
        Image.new('RGB', (4, 4), 'white').save(image)
        for observation in cap['observations']:
            observation['path'] = str(image)
        client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        agent = OpenAIResponsesAgent(evidence_dir=root / 'transport', client=client,
            wire_schema_bundle=WIRE, prompt_version='driving_prototype_v3',
            experiment_profile='local_conditional_2s_prototype_v1', **options)
        agent.preflight()
        return cap, out, agent

    @patch.dict(os.environ, ENV)
    def test_native_preserved_wire_images_business_validation_and_source(self):
        with tempfile.TemporaryDirectory(prefix='authored-openai-') as directory:
            root = Path(directory)
            expected = authored_case(speed=3., half_road=10.)[1]
            raw = ' \n' + json.dumps(expected, ensure_ascii=False) + '\n'
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(200, json=response(raw), headers={'x-request-id': 'req_authored'})
            cap, out, agent = self.make(root, handler)
            before = deepcopy((cap, out))
            output, provenance = agent.decide(cap, dict(code='AUTHORED_FEEDBACK'))
            self.assertEqual(len(requests), 1)
            payload = json.loads(requests[0].content)
            self.assertEqual(str(requests[0].url), 'https://api.openai.com/v1/responses')
            self.assertEqual(payload['instructions'], (ROOT / 'implementation/prompts/driving_prototype_v3.txt').read_text())
            self.assertEqual(payload['tools'], [])
            self.assertFalse(payload['store']); self.assertFalse(payload['stream'])
            self.assertEqual(payload['reasoning'], {'effort': 'medium'})
            self.assertEqual(payload['text']['format']['schema'], json.loads((WIRE / 'wire_schema.json').read_text()))
            parts = payload['input'][0]['content']
            self.assertEqual(parts[1]['type'], 'input_image')
            self.assertTrue(parts[1]['image_url'].startswith('data:image/png;base64,'))
            context = json.loads(parts[0]['text'])
            self.assertEqual(context['tool_contract']['planner_profile'], 'bounded_seed_candidates_v2')
            self.assertEqual(context['capture']['observations'][0]['path'], 'attached:frame_000.png')
            self.assertEqual(output, expected)
            self.assertEqual((cap, out), before)
            self.assertEqual((root / 'transport/attempt_01/final_agent_message.txt').read_text(), raw)
            self.assertEqual(provenance['source'], 'mock_transport')
            self.assertEqual(provenance['model_id'], 'authored-test-model')
            self.assertEqual(provenance['request_id'], 'req_authored')
            self.assertEqual(provenance['usage']['total_tokens'], 46)
            self.assertEqual(provenance['output_digest'], digest(expected))
            self.assertEqual(provenance['image_sha256'][cap['observations'][0]['id']],
                             hashlib.sha256((root / 'authored.png').read_bytes()).hexdigest())
            self.assertIsNone(provenance['server_reported_snapshot'])
            self.assertNotIn(FAKE_KEY, json.dumps(agent.public_config))

    @patch.dict(os.environ, ENV)
    def test_invalid_native_not_repaired_and_budget_not_retried(self):
        for kind in ('bad_type', 'duplicate', 'nonfinite'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix='authored-openai-invalid-') as directory:
                root = Path(directory); requests = []
                invalid = authored_case(speed=3., half_road=10.)[1]
                invalid['decision']['desired_speed_mps'] = '3.0'
                raw = json.dumps(invalid) if kind == 'bad_type' else ('{"x":1,"x":2}' if kind == 'duplicate' else '{"x":NaN}')
                def handler(request):
                    requests.append(request)
                    return httpx.Response(200, json=response(raw))
                cap, out, agent = self.make(root, handler, max_calls=1)
                with self.assertRaises(Failure) as failed:
                    agent.decide(cap)
                self.assertEqual(failed.exception.record['code'], 'SCHEMA_ERROR' if kind == 'bad_type' else 'API_RESPONSE')
                self.assertEqual(len(requests), 1)
                self.assertEqual((root / 'transport/attempt_01/final_agent_message.txt').read_text(), raw)
                if kind == 'bad_type':
                    saved = json.loads((root / 'transport/attempt_01/native_output.json').read_text())
                    self.assertEqual(saved['decision']['desired_speed_mps'], '3.0')
                    # A native business-Schema failure remains distinct from
                    # a service/transport failure for the driving stage ledger.
                    summary = json.loads((root / 'transport/attempt_01/attempt_summary.json').read_text())
                    self.assertTrue(summary['model_service_returned'])
                    self.assertTrue(summary['native_json_returned'])
                    self.assertEqual(summary['failure']['code'], 'SCHEMA_ERROR')
                    self.assertEqual(summary['http_status'], 200)
                    self.assertEqual(summary['api_post_requests'], 1)
                with self.assertRaises(Failure) as budget:
                    agent.decide(cap)
                self.assertEqual(budget.exception.record['code'], 'API_BUDGET')
                self.assertEqual(len(requests), 1)

    @patch.dict(os.environ, ENV)
    def test_refusal_partial_tool_and_unregistered_model_are_rejected(self):
        valid = json.dumps(authored_case(speed=3., half_road=10.)[1])
        cases = [
            (response(valid, status='incomplete', incomplete_details={'reason': 'max_output_tokens'}), 'API_INCOMPLETE'),
            (response(valid, output=[dict(type='function_call', name='hidden_tool')]), 'API_TOOL_USE'),
            (response(valid, output=[dict(type='message', role='assistant', status='completed',
                                        content=[dict(type='refusal', refusal='AUTHORED refusal')])]), 'API_REFUSAL'),
            (response(valid, model='unregistered-test-model'), 'MODEL_SUBSTITUTION'),
            (response(valid, output=None), 'API_RESPONSE'),
        ]
        for data, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory(prefix='authored-openai-protocol-') as directory:
                root = Path(directory); requests = []
                def handler(request):
                    requests.append(request)
                    return httpx.Response(200, json=data)
                cap, out, agent = self.make(root, handler)
                with self.assertRaises(Failure) as caught:
                    agent.decide(cap)
                self.assertEqual(caught.exception.record['code'], code)
                self.assertEqual(len(requests), 1)
                self.assertFalse((root / 'transport/attempt_01/native_output.json').exists())
                if code in ('MODEL_SUBSTITUTION', 'API_INCOMPLETE'):
                    self.assertEqual((root / 'transport/attempt_01/final_agent_message.txt').read_text(), valid)

    @patch.dict(os.environ, ENV)
    def test_service_and_transport_failure_are_single_attempt_and_credential_safe(self):
        for kind in ('429', 'redirect', 'timeout'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix='authored-openai-service-') as directory:
                root = Path(directory); requests = []
                def handler(request):
                    requests.append(request)
                    if kind == 'timeout':
                        raise httpx.ReadTimeout('must not expose ' + FAKE_KEY)
                    return httpx.Response(429 if kind == '429' else 302,
                        headers={'location': 'https://authored.invalid'},
                        json=dict(error=dict(type='rate_limit_error', code='authored',
                                             message='bad secret ' + FAKE_KEY)))
                cap, out, agent = self.make(root, handler)
                with self.assertRaises(Failure) as caught:
                    agent.decide(cap)
                self.assertEqual(caught.exception.record['code'], 'API_TRANSPORT' if kind == 'timeout' else 'API_HTTP')
                self.assertEqual(len(requests), 1)
                for path in (root / 'transport').rglob('*'):
                    if path.is_file():
                        self.assertNotIn(FAKE_KEY, path.read_text())

    @patch.dict(os.environ, ENV)
    def test_local_key_loading_official_origin_and_wire_pins(self):
        with tempfile.TemporaryDirectory(prefix='authored-openai-pins-') as directory:
            root = Path(directory)
            private = root / 'authored-secret.txt'; private.write_text(FAKE_KEY + '\n')
            with patch.dict(os.environ, DRIVING_API_KEY='', DRIVING_API_KEY_FILE=str(private)):
                self.assertEqual(load_api_key(), FAKE_KEY)
            with patch.dict(os.environ, DRIVING_API_BASE='https://authored.invalid/v1'):
                with self.assertRaises(Failure) as endpoint:
                    self.make(root, lambda request: self.fail('No HTTP request allowed'))
                self.assertEqual(endpoint.exception.record['code'], 'API_CONFIGURATION')
            cap, out, agent = self.make(root, lambda request: self.fail('Changed wire must not send'))
            altered = deepcopy(agent.schema_transport); altered['wire_schema_sha256'] = 'authored_wrong_pin'
            with patch.object(agent, '_transport_schema', return_value=({}, altered)):
                with self.assertRaises(Failure) as pin:
                    agent.decide(cap)
                self.assertEqual(pin.exception.record['code'], 'WIRE_SCHEMA_CHANGED')
            self.assertEqual(agent.call_count, 0)

    @patch.dict(os.environ, ENV)
    def test_ten_image_order_matches_metadata_and_original_byte_hashes(self):
        with tempfile.TemporaryDirectory(prefix='authored-openai-images-') as directory:
            root = Path(directory); requests = []
            valid = json.dumps(authored_case(speed=3., half_road=10.)[1])
            def handler(request):
                requests.append(request)
                return httpx.Response(200, json=response(valid))
            cap, _, agent = self.make(root, handler)
            original = cap['observations'][0]
            cap['observations'] = []
            expected = []
            for index in range(10):
                image = root / f'authored_frame_{index}.png'
                Image.new('RGB', (4 + index, 4), (index * 20, 80, 160)).save(image)
                observation = deepcopy(original)
                observation.update(id=f'authored_{index}', path=str(image),
                                   camera_id=f'authored_camera_{index % 6}')
                cap['observations'].append(observation)
                expected.append(hashlib.sha256(image.read_bytes()).hexdigest())
            before = deepcopy(cap)
            _, provenance = agent.decide(cap)
            parts = json.loads(requests[0].content)['input'][0]['content']
            metadata = json.loads(parts[0]['text'])['capture']['observations']
            self.assertEqual(len(parts[1:]), 10)
            for index, (frame, image_part, expected_hash) in enumerate(zip(metadata, parts[1:], expected)):
                self.assertEqual(frame['id'], f'authored_{index}')
                self.assertEqual(frame['camera_id'], f'authored_camera_{index % 6}')
                self.assertEqual(frame['path'], f'attached:frame_{index:03d}.png')
                actual = hashlib.sha256(base64.b64decode(image_part['image_url'].split(',', 1)[1])).hexdigest()
                self.assertEqual(actual, expected_hash)
                self.assertEqual(provenance['image_sha256'][frame['id']], expected_hash)
            self.assertEqual(cap, before)
            self.assertEqual(provenance['source'], 'mock_transport')


if __name__ == '__main__':
    unittest.main()
