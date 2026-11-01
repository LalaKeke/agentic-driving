"""Local protocol simulation only; no network, authentication or model calls."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentic.contracts import Failure
from agentic.fixtures_v3 import metric_case
from agentic.pilot_transport import (PilotProtocol, PilotSubscriptionAgent,
                                    PROVIDER, OFFICIAL_BASE, provider_config, redact, public_event)


class PilotProtocolTests(unittest.TestCase):
    def protocol(self):
        p = PilotProtocol('gpt-6.1-sol')
        p.thread_response(dict(model='gpt-6.1-sol', modelProvider=PROVIDER,
                               thread=dict(id='test-thread'), instructionSources=[]))
        return p

    def event(self, method, **params):
        return dict(method=method, params=dict(threadId='test-thread', **params))

    def test_resolved_alias_is_required_and_not_requested_copy(self):
        for model in (None, 'another-model'):
            p = PilotProtocol('gpt-6.1-sol')
            with self.assertRaises(Failure) as caught:
                p.thread_response(dict(model=model, modelProvider=PROVIDER, thread=dict(id='a')))
            self.assertEqual(caught.exception.record['code'], 'MODEL_SUBSTITUTION')

    def test_wrong_provider_or_loaded_instruction_file_is_rejected(self):
        for changes in (dict(modelProvider='other'), dict(instructionSources=['/a/AGENTS.md'])):
            data = dict(model='gpt-6.1-sol', modelProvider=PROVIDER, thread=dict(id='a'))
            data.update(changes)
            with self.assertRaises(Failure):
                PilotProtocol('gpt-6.1-sol').thread_response(data)

    def test_rerouting_and_auth_recovery_are_stop_conditions(self):
        for method in ('model/rerouted', 'modelProvider/authRecoveryStarted',
                       'modelProvider/authRecoveryCompleted'):
            with self.assertRaises(Failure):
                self.protocol().observe(self.event(method))

    def test_extra_turn_and_duplicate_start_are_rejected(self):
        p = self.protocol()
        p.observe(self.event('turn/started', turn=dict(id='turn-one')))
        for event in (self.event('item/started', turnId='turn-two', item=dict(type='reasoning')),
                      self.event('turn/started', turn=dict(id='turn-one'))):
            with self.assertRaises(Failure):
                p.observe(event)

    def test_tools_and_server_requests_are_rejected(self):
        for event in (self.event('item/started', item=dict(type='commandExecution')),
                      dict(id=10, method='item/tool/call', params={})):
            with self.assertRaises(Failure):
                self.protocol().observe(event)

    def test_completed_response_does_not_accept_failed_turn(self):
        with self.assertRaises(Failure):
            self.protocol().observe(self.event('turn/completed', turn=dict(
                id='one', status='failed', items=[], error=dict(message='test'))))

    def test_final_text_keeps_native_bytes_including_whitespace(self):
        p = self.protocol()
        raw = ' \n{"native": true}\n'
        p.observe(self.event('item/completed', turnId='one', item=dict(
            type='agentMessage', id='message', phase='final_answer', text=raw)))
        p.observe(self.event('turn/completed', turn=dict(id='one', status='completed', items=[])))
        self.assertEqual(p.final_text(), raw)

    def test_official_provider_has_zero_retries_and_explicit_route(self):
        c = provider_config()
        self.assertEqual(c[f'model_providers.{PROVIDER}.base_url'], OFFICIAL_BASE)
        self.assertTrue(c[f'model_providers.{PROVIDER}.requires_openai_auth'])
        for key in ('request_max_retries', 'stream_max_retries'):
            self.assertEqual(c[f'model_providers.{PROVIDER}.{key}'], 0)
        self.assertFalse(c[f'model_providers.{PROVIDER}.supports_websockets'])
        self.assertFalse(c['orchestrator.mcp.enabled'])

    def test_public_redaction_covers_structured_and_text_credentials(self):
        raw = dict(access_token='opaque', headers=dict(Authorization='Bearer opaque'),
                   message='test Bearer not-a-real-credential', accountId='private-id')
        cleaned = redact(raw)
        self.assertNotIn('opaque', json.dumps(cleaned))
        self.assertNotIn('private-id', json.dumps(cleaned))
        self.assertEqual(raw['access_token'], 'opaque')

    def test_account_and_remote_metadata_never_enter_public_events(self):
        for method in ('account/updated', 'remoteControl/status/changed'):
            event = dict(method=method, params=dict(id='private-account-id', other='opaque-personal-value'))
            clean = public_event(event)
            self.assertEqual(clean['method'], method)
            self.assertTrue(clean['params_private'])
            self.assertNotIn('private-account-id', json.dumps(clean))
            self.assertNotIn('opaque-personal-value', json.dumps(clean))


SIMULATOR = '''import json,sys,time
mode=sys.argv[1]
native=open(sys.argv[2]).read()
def emit(value):
 print(json.dumps(value),flush=True)
for line in sys.stdin:
 event=json.loads(line)
 method=event['method']
 if method=='initialize':emit({'id':event['id'],'result':{'serverInfo':'local mock'}})
 elif method=='thread/start':
  emit({'id':event['id'],'result':{'model':event['params']['model'],
   'modelProvider':event['params']['modelProvider'],'instructionSources':[],
   'thread':{'id':'mock-thread'}}})
 elif method=='turn/start':
  emit({'id':event['id'],'result':{'turn':{'id':'mock-turn','status':'inProgress','items':[]}}})
  emit({'method':'turn/started','params':{'threadId':'mock-thread','turn':{'id':'mock-turn'}}})
  if mode=='timeout':time.sleep(20)
  elif mode=='tool':
   emit({'method':'item/started','params':{'threadId':'mock-thread','turnId':'mock-turn','item':{'id':'tool','type':'commandExecution'}}})
  elif mode=='service_failure':
   emit({'method':'error','params':{'threadId':'mock-thread','turnId':'mock-turn','error':{'message':'test Bearer private-token'},'willRetry':False}})
  elif mode=='partial_failure':
   emit({'method':'item/agentMessage/delta','params':{'threadId':'mock-thread','turnId':'mock-turn','itemId':'partial','delta':' {"partial": '}})
   emit({'method':'error','params':{'threadId':'mock-thread','turnId':'mock-turn','error':{'message':'authored interruption'},'willRetry':False}})
  else:
   emit({'method':'item/completed','params':{'threadId':'mock-thread','turnId':'mock-turn','item':{'id':'mock-message','type':'agentMessage','phase':'final_answer','text':native}}})
   emit({'method':'turn/completed','params':{'threadId':'mock-thread','turn':{'id':'mock-turn','status':'completed','error':None,'items':[]}}})
'''


class PilotLocalProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pilot-tests-')
        self.root = Path(self.temporary.name)
        self.cap, self.native, _ = metric_case()
        image = self.root / 'authored.png'
        Image.new('RGB', (3, 3), color='white').save(image)
        for frame in self.cap['observations']:
            frame['path'] = str(image)
        self.script = self.root / 'local_process.py'
        self.script.write_text(SIMULATOR)

    def tearDown(self):
        self.temporary.cleanup()

    def agent(self, mode='valid', text=None, timeout=5):
        native = self.root / ('native_' + mode + '.txt')
        native.write_text(json.dumps(self.native) if text is None else text)
        def factory(command, **kwargs):
            # No Codex invocation: substitute an authored local JSON-RPC process.
            return subprocess.Popen([sys.executable, str(self.script), mode, str(native)], **kwargs)
        agent = PilotSubscriptionAgent(evidence_dir=self.root / ('evidence_' + mode),
                                       process_factory=factory, timeout_s=timeout)
        agent.preflight_ok = True
        agent.version = 'mock-local-protocol'
        return agent

    def test_authored_native_schema_round_trip_one_turn_and_honest_mock_source(self):
        agent = self.agent()
        output, source = agent.decide(self.cap)
        self.assertEqual(output, self.native)
        self.assertEqual(source['source'], 'mock_transport')
        self.assertEqual(source['cli_resolved_alias'], 'gpt-6.1-sol')
        self.assertIsNone(source['server_reported_snapshot'])
        attempt = agent.evidence_dir / 'attempt_01'
        wire = json.loads((attempt / 'wire_manifest.json').read_text())
        self.assertEqual(wire['turn_start_requests'], 1)
        self.assertEqual((attempt / 'final_agent_message.txt').read_text(), json.dumps(self.native))
        requests = json.loads((attempt / 'requests.json').read_text())
        thread = next(r for r in requests if r['method'] == 'thread/start')
        self.assertFalse(thread['params']['allowProviderModelFallback'])
        self.assertEqual(thread['params']['cwd'].split('/')[-1][:21], 'driving-pilot-images-')

    def test_schema_invalid_native_message_is_preserved_without_fields_added(self):
        raw = ' {"unknown_only": null}\n'
        agent = self.agent('invalid', raw)
        with self.assertRaises(Failure) as caught:
            agent.decide(self.cap)
        self.assertEqual(caught.exception.record['code'], 'SCHEMA_ERROR')
        attempt = agent.evidence_dir / 'attempt_01'
        self.assertEqual((attempt / 'final_agent_message.txt').read_text(), raw)
        self.assertEqual(json.loads((attempt / 'native_output.json').read_text()), dict(unknown_only=None))
        provenance = json.loads((attempt / 'provenance.json').read_text())
        self.assertEqual(provenance['source'], 'mock_transport')
        self.assertEqual(provenance['cli_resolved_alias'], 'gpt-6.1-sol')
        self.assertTrue(agent.last_attempt_summary['model_service_returned'])
        self.assertTrue(agent.last_attempt_summary['native_json_returned'])

    def test_non_json_native_message_is_preserved(self):
        raw = 'This is an authored invalid model substitute.\n'
        agent = self.agent('not_json', raw)
        with self.assertRaises(Failure) as caught:
            agent.decide(self.cap)
        self.assertEqual(caught.exception.record['code'], 'CODEX_RESPONSE')
        self.assertEqual((agent.evidence_dir / 'attempt_01/agent_message_00.txt').read_text(), raw)

    def test_tools_fail_without_second_turn_or_retry(self):
        agent = self.agent('tool')
        with self.assertRaises(Failure) as caught:
            agent.decide(self.cap)
        self.assertEqual(caught.exception.record['code'], 'CODEX_TOOL_USE')
        wire = json.loads((agent.evidence_dir / 'attempt_01/wire_manifest.json').read_text())
        self.assertEqual(wire['turn_start_requests'], 1)

    def test_service_failure_retains_private_stream_and_redacts_public_event(self):
        agent = self.agent('service_failure')
        with self.assertRaises(Failure):
            agent.decide(self.cap)
        attempt = agent.evidence_dir / 'attempt_01'
        self.assertIn('private-token', (attempt / '.private/stdout.raw').read_text())
        self.assertNotIn('private-token', (attempt / 'events.redacted.jsonl').read_text())
        self.assertEqual((attempt / '.private').stat().st_mode & 0o777, 0o700)
        self.assertEqual((attempt / '.private/stdout.raw').stat().st_mode & 0o777, 0o600)
        self.assertIn('*', (attempt / '.private/.gitignore').read_text())

    def test_timeout_keeps_partial_events_and_does_not_retry(self):
        agent = self.agent('timeout', timeout=1)
        with self.assertRaises(Failure) as caught:
            agent.decide(self.cap)
        self.assertEqual(caught.exception.record['code'], 'CODEX_TIMEOUT')
        wire = json.loads((agent.evidence_dir / 'attempt_01/wire_manifest.json').read_text())
        self.assertEqual(wire['turn_start_requests'], 1)

    def test_partial_text_before_failure_is_kept_without_claiming_completion(self):
        agent = self.agent('partial_failure')
        with self.assertRaises(Failure):
            agent.decide(self.cap)
        attempt = agent.evidence_dir / 'attempt_01'
        self.assertEqual((attempt / 'partial_agent_deltas_00.txt').read_text(), ' {"partial": ')
        layer = agent.last_attempt_summary['service_layer']
        self.assertTrue(layer['native_model_text_observed'])
        self.assertFalse(layer['completed_turn_observed'])
        self.assertTrue(layer['error_notification_observed'])
        self.assertTrue(agent.last_attempt_summary['model_service_returned'])

    def test_failures_consume_local_two_attempt_limit(self):
        agent = self.agent('invalid_twice', '{}')
        for unused in range(2):
            with self.assertRaises(Failure):
                agent.decide(self.cap)
        with self.assertRaises(Failure) as caught:
            agent.decide(self.cap)
        self.assertEqual(caught.exception.record['code'], 'PILOT_BUDGET')
        self.assertFalse((agent.evidence_dir / 'attempt_03').exists())

    def test_existing_evidence_destination_is_never_reused(self):
        path = self.root / 'already_exists'
        path.mkdir()
        with self.assertRaises(Failure) as caught:
            PilotSubscriptionAgent(evidence_dir=path)
        self.assertEqual(caught.exception.record['code'], 'OUTPUT_EXISTS')

    def test_state_and_logs_use_task_private_paths_without_home_overrides(self):
        agent = self.agent('state_paths')
        command = agent.command()
        config = [command[i + 1] for i, item in enumerate(command) if item == '-c']
        self.assertIn('sqlite_home=' + json.dumps(str(agent.sqlite_home)), config)
        self.assertIn('log_dir=' + json.dumps(str(agent.log_dir)), config)
        self.assertEqual(agent.private_state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(agent.sqlite_home.stat().st_mode & 0o777, 0o700)
        self.assertEqual(agent.log_dir.stat().st_mode & 0o777, 0o700)
        self.assertIn('*', (agent.private_state / '.gitignore').read_text())

    def test_preflight_never_publishes_full_account_configuration(self):
        agent = self.agent('preflight_redaction')
        responses = [
            subprocess.CompletedProcess([], 0, 'codex-cli 0.164.0\n', ''),
            subprocess.CompletedProcess([], 0, '', 'Logged in using ChatGPT\n'),
            subprocess.CompletedProcess([], 0, '{"unrecognized_secret":"opaque-private-value"}', ''),
        ]
        with patch('agentic.pilot_transport.subprocess.run', side_effect=responses):
            result = agent.preflight()
        self.assertEqual(result['model_inference_calls'], 0)
        self.assertNotIn('opaque-private-value', json.dumps(result))
        public = (agent.evidence_dir / 'startup_config.json').read_text()
        self.assertNotIn('opaque-private-value', public)
        private = agent.private_state / 'preflight/startup_config.stdout'
        self.assertIn('opaque-private-value', private.read_text())
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
