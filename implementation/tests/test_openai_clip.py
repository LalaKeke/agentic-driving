"""Clip orchestration only: explicit authored transport and physical mocks."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.contracts import Failure
import test_local_prototype as fixtures

spec = importlib.util.spec_from_file_location('authored_openai_clip', ROOT / 'implementation/experiments/run_openai_clip.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ClipTests(unittest.TestCase):
    def prepare(self, root):
        capture, _, _ = fixtures.case()
        image = root / 'mock-image.png'
        image.write_bytes(b'AUTHORED TRANSPORT MOCK, not a real image')
        cases = []
        for index in range(2):
            cap = deepcopy(capture)
            sample = 'authored_' + str(index)
            cap['metadata']['scene_id'] = 'nusc_' + sample
            for frame in cap['observations']:
                frame['path'] = str(image)
            path = root / ('capture_' + str(index) + '.json')
            path.write_text(json.dumps(cap))
            cases.append(dict(case_id='frame_' + str(index), sample_token=sample,
                              capture=str(path), capture_sha256=runner.sha(path),
                              image_sha256={frame['id']: runner.sha(image) for frame in cap['observations']},
                              require_turn=bool(index)))
        plan = dict(execution_authorized=True, old_budget_inherited=0, backend='openai_api',
                    prompt_version='driving_prototype_v3', planner_profile='bounded_seed_candidates_v2',
                    per_window_limit=2, operational_call_ceiling=4, requested_model='authored-mock-model',
                    deadline_utc='2099-01-01T00:00:00Z', cases=cases, wire_bundle='unused-mock-wire',
                    source_sha256={'implementation/experiments/run_openai_clip.py': runner.sha(runner.__file__)},
                    recording_boundary='AUTHORED test fixture only, not online evidence')
        path = root / 'plan.json'
        path.write_text(json.dumps(plan))
        return path

    def fake(self, fail_service=False):
        instances = []

        class AuthoredAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.evidence_dir = Path(kwargs['evidence_dir'])
                self.evidence_dir.mkdir()
                self.public_config = dict(source='mock_transport', adapter_retries=0)
                self.call_count = 0
                self.calls = []
                self.preflights = 0
                self.last_attempt_summary = None
                self.closed = False
                self.client = SimpleNamespace(close=lambda: setattr(self, 'closed', True))
                instances.append(self)

            def preflight(self):
                self.preflights += 1

            def decide(self, cap, feedback):
                self.call_count += 1
                self.calls.append((deepcopy(cap), deepcopy(feedback)))
                folder = self.evidence_dir / f'attempt_{self.call_count:02d}'
                folder.mkdir()
                self.last_attempt_summary = dict(api_post_requests=1, source='mock_transport',
                                                 model_service_returned=not fail_service)
                (folder / 'attempt_summary.json').write_text(json.dumps(self.last_attempt_summary))
                if fail_service:
                    raise Failure('API_HTTP', 'AUTHORED service failure; no retry')
                output = dict(authored=True)
                provenance = dict(source='mock_transport')
                (folder / 'native_output.json').write_text(json.dumps(output))
                (folder / 'provenance.json').write_text(json.dumps(provenance))
                return output, provenance

        return AuthoredAgent, instances

    def test_one_client_reference_isolation_revision_and_global_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = self.prepare(root)
            fake, agents = self.fake()
            physical_calls = []

            def authored_physical(cap, output, provenance, initial, revision, folder, **kwargs):
                physical_calls.append(kwargs)
                if len(physical_calls) == 1:
                    return None, dict(code='AUTHORED_TOOL_REJECTION'), dict(T1='FAIL')
                return dict(prototype_conditional_success=True), None, dict(T1='PASS', EXECUTION='PASS')

            with patch.dict(runner.os.environ, DRIVING_MODEL='authored-mock-model'), \
                 patch.object(runner, 'OpenAIResponsesAgent', fake), \
                 patch.object(runner, 'physical', authored_physical):
                self.assertEqual(runner.run(plan), 0)
            self.assertEqual(len(agents), 1)
            agent = agents[0]
            self.assertEqual(agent.preflights, 1)
            self.assertEqual(agent.kwargs['max_calls'], 4)
            self.assertNotIn('client', agent.kwargs)  # Real route must not inject a mock-labelled client.
            self.assertEqual(agent.call_count, 3)
            self.assertEqual([c['require_turn'] for c in physical_calls], [False, False, True])
            self.assertIsNone(agent.calls[0][1])
            self.assertEqual(agent.calls[1][1]['failure']['code'], 'AUTHORED_TOOL_REJECTION')
            self.assertIsNone(agent.calls[2][1])
            self.assertEqual([c[0]['metadata']['revision'] for c in agent.calls], [0, 1, 0])
            alias = root / 'runtime/frame_1/transport/attempt_01'
            self.assertTrue(alias.is_symlink())
            self.assertEqual(alias.resolve(), root / 'transport/attempt_03')
            mapping = runner.read(alias.parent / 'mapping.json')['mappings'][0]
            self.assertEqual(mapping['global_attempt'], 3)
            summary = runner.read(root / 'batch_summary.json')
            self.assertEqual(summary['charged_requests'], 3)
            self.assertEqual(summary['api_post_requests'], 3)
            self.assertEqual([r['charged_requests'] for r in summary['cases']], [2, 1])
            self.assertTrue(agent.closed)

    def test_service_failure_stops_later_references_and_signals_other_clips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = self.prepare(root)
            fake, agents = self.fake(fail_service=True)
            marker = root / 'stop.json'
            with patch.dict(runner.os.environ, DRIVING_MODEL='authored-mock-model'), \
                 patch.object(runner, 'OpenAIResponsesAgent', fake), \
                 patch.object(runner, 'physical', side_effect=AssertionError('No native output')):
                self.assertEqual(runner.run(plan, marker), 3)
            self.assertEqual(agents[0].call_count, 1)
            self.assertEqual(runner.read(marker)['failure_code'], 'API_HTTP')
            summary = runner.read(root / 'batch_summary.json')
            self.assertEqual(summary['charged_requests'], 1)
            self.assertEqual(summary['cases'][1]['status'], 'NOT_ATTEMPTED')
            self.assertFalse((root / 'runtime/frame_1').exists())

    def test_existing_stop_marker_prevents_even_preflight_or_key_access(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = self.prepare(root)
            marker = root / 'stop.json'
            marker.write_text('{"source":"AUTHORED external stop"}')
            with patch.dict(runner.os.environ, DRIVING_MODEL='authored-mock-model'), \
                 patch.object(runner, 'OpenAIResponsesAgent', side_effect=AssertionError('No client permitted')):
                self.assertEqual(runner.run(plan, marker), 3)
            summary = runner.read(root / 'batch_summary.json')
            self.assertEqual(summary['charged_requests'], 0)
            self.assertEqual(summary['api_post_requests'], 0)
            self.assertEqual(summary['stop_reason'], 'EXTERNAL_STOP_BEFORE_PREFLIGHT')
            self.assertTrue(all(r['status'] == 'NOT_ATTEMPTED' for r in summary['cases']))


if __name__ == '__main__':
    unittest.main()
