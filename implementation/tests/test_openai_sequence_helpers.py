"""Small offline checks for API request accounting and unchanged RPC counts."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

IMPLEMENTATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(IMPLEMENTATION))
sys.path.insert(0, str(IMPLEMENTATION / 'experiments'))


def module(name):
    spec = importlib.util.spec_from_file_location(name, IMPLEMENTATION / 'experiments' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


runner = module('run_openai_bend_sequence')
summary = module('summarize_six_scene_scout')


class OpenAISequenceHelperTests(unittest.TestCase):
    def test_post_usage_count_and_retry_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            attempt = root / 'transport' / 'attempt_01'
            attempt.mkdir(parents=True)
            path = attempt / 'attempt_summary.json'
            path.write_text(json.dumps(dict(api_post_requests=1, server_reported_model='registered-api-model',
                                            usage=dict(input_tokens=11, output_tokens=5, total_tokens=16))))
            totals = runner.transport_totals(root)
            self.assertEqual(totals['api_post_requests'], 1)
            self.assertEqual(totals['usage']['total_tokens'], 16)
            self.assertEqual(totals['server_reported_models'], ['registered-api-model'])
            path.write_text(json.dumps(dict(api_post_requests=2)))
            with self.assertRaises(AssertionError):
                runner.transport_totals(root)

    def test_api_failure_keeps_post_count_without_inventing_rpc_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'attempt_01'
            transport = root / 'transport' / 'attempt_01'
            folder.mkdir()
            transport.mkdir(parents=True)
            (transport / 'attempt_summary.json').write_text(json.dumps(dict(
                api_post_requests=1, transport='official_openai_responses', source='online_model',
                http_status=429, status='FAILED')))
            (folder / 'failure.json').write_text(json.dumps(dict(code='API_HTTP', message='no retry')))
            row = summary.attempt(folder, transport, {}, True, 'fixed-sample')
            self.assertEqual(row['api_post_requests'], 1)
            self.assertEqual(row['turn_start_count'], 0)
            self.assertEqual(row['transport'], 'official_openai_responses')
            self.assertTrue(row['service_or_interface_failure'])
            self.assertFalse(row['native_json_present'])
            self.assertFalse(row['complete_conditional_chain'])

    def test_legacy_absent_api_metadata_means_no_api_posts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'attempt_01'
            transport = root / 'transport' / 'attempt_01'
            folder.mkdir()
            transport.mkdir(parents=True)
            (transport / 'requests.json').write_text(json.dumps([dict(method='turn/start')]))
            row = summary.attempt(folder, transport, {}, True, 'fixed-sample')
            self.assertEqual(row['api_post_requests'], 0)
            self.assertEqual(row['turn_start_count'], 1)


if __name__ == '__main__':
    unittest.main()
