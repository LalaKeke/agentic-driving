"""Authored local RPC only: wire compatibility never repairs native instances."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.contracts import Failure, validate
from agentic.fixtures_v3 import metric_case
from agentic.pilot_transport import PilotSubscriptionAgent, SCHEMA
from agentic.wire_schema import export_wire_bundle
from test_pilot_transport import SIMULATOR


class WireTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wire-transport-tests-')
        self.root = Path(self.temp.name)
        self.cap, self.output, _ = metric_case()
        image = self.root / 'authored.png'
        Image.new('RGB', (3, 3), color='white').save(image)
        for frame in self.cap['observations']:
            frame['path'] = str(image)
        self.script = self.root / 'rpc.py'
        self.script.write_text(SIMULATOR)
        self.bundle = self.root / 'wire'
        self.manifest = export_wire_bundle(ROOT / 'implementation/schemas' / SCHEMA, self.bundle)

    def tearDown(self):
        self.temp.cleanup()

    def agent(self, raw=None, wire=True):
        native = self.root / 'authored_native.txt'
        native.write_text(json.dumps(self.output) if raw is None else raw)
        def factory(command, **kwargs):
            return subprocess.Popen([sys.executable, str(self.script), 'valid', str(native)], **kwargs)
        agent = PilotSubscriptionAgent(evidence_dir=self.root / 'evidence', process_factory=factory,
            wire_schema_bundle=self.bundle if wire else None)
        agent.preflight_ok = True
        agent.version = 'mock-local-rpc'
        return agent

    def test_wire_is_sent_but_original_business_schema_validates_native_output(self):
        agent = self.agent()
        with patch('agentic.pilot_transport.validate', wraps=validate) as gate:
            output, prov = agent.decide(self.cap)
        self.assertEqual(output, self.output)
        self.assertEqual(prov['source'], 'mock_transport')
        self.assertIn(SCHEMA, [call.args[0] for call in gate.call_args_list])
        folder = agent.evidence_dir / 'attempt_01'
        requests = json.loads((folder / 'requests.json').read_text())
        sent = next(r for r in requests if r['method'] == 'turn/start')['params']['outputSchema']
        business = json.loads((ROOT / 'implementation/schemas' / SCHEMA).read_text())
        node = sent['properties']['hypotheses']['properties']['coordinate_frame']
        self.assertEqual(node, dict(const='ego_at_reference', type='string'))
        self.assertNotIn('type', business['properties']['hypotheses']['properties']['coordinate_frame'])
        self.assertEqual(prov['schema_transport']['wire_schema_sha256'], self.manifest['wire_schema_sha256'])
        self.assertEqual(prov['schema_transport']['business_schema_sha256'], self.manifest['source_schema_sha256'])
        self.assertFalse(prov['schema_transport']['response_repair'])
        inputs = json.loads((folder / 'input_manifest.json').read_text())
        self.assertEqual(inputs['schema_sha256'], self.manifest['source_schema_sha256'])
        self.assertEqual(inputs['schema_transport'], prov['schema_transport'])
        self.assertEqual((folder / 'final_agent_message.txt').read_text(), json.dumps(self.output))

    def test_missing_and_wrong_native_values_remain_unchanged_and_rejected(self):
        raw = ' \n{"unknown_only": null, "contract_version": 3}\n'
        agent = self.agent(raw)
        with self.assertRaises(Failure) as caught:
            agent.decide(self.cap)
        self.assertEqual(caught.exception.record['code'], 'SCHEMA_ERROR')
        folder = agent.evidence_dir / 'attempt_01'
        self.assertEqual((folder / 'final_agent_message.txt').read_text(), raw)
        self.assertEqual(json.loads((folder / 'native_output.json').read_text()), json.loads(raw))
        self.assertFalse(json.loads((folder / 'provenance.json').read_text())['schema_transport']['response_repair'])
        self.assertEqual(agent.call_count, 1)

    def test_bundle_tamper_after_construction_stops_before_turn(self):
        agent = self.agent()
        target = self.bundle / 'wire_schema.json'
        target.write_text('{"type":"object"}')
        with self.assertRaises(Failure) as caught:
            agent.decide(self.cap)
        self.assertEqual(caught.exception.record['code'], 'WIRE_SCHEMA_INVALID')
        self.assertEqual(agent.call_count, 0)
        self.assertFalse((agent.evidence_dir / 'attempt_01').exists())

    def test_forged_wire_and_manifest_cannot_disable_business_requirements(self):
        (self.bundle / 'wire_schema.json').write_text('{}')
        (self.bundle / 'transformation_manifest.json').write_text('{}')
        with self.assertRaises(Failure) as caught:
            self.agent()
        self.assertEqual(caught.exception.record['code'], 'WIRE_SCHEMA_INVALID')
        self.assertFalse((self.root / 'evidence').exists())

    def test_legacy_default_sends_original_schema_without_wire_metadata(self):
        agent = self.agent(wire=False)
        _, prov = agent.decide(self.cap)
        requests = json.loads((agent.evidence_dir / 'attempt_01/requests.json').read_text())
        sent = next(r for r in requests if r['method'] == 'turn/start')['params']['outputSchema']
        self.assertEqual(sent, json.loads((ROOT / 'implementation/schemas' / SCHEMA).read_text()))
        self.assertNotIn('schema_transport', prov)
        self.assertNotIn('schema_transport', agent.public_config)

    def test_prepared_trial_cannot_open_budget_or_transport(self):
        spec = importlib.util.spec_from_file_location('wire_pilot_guard',
            ROOT / 'implementation/experiments/exp2_controlled_pilot.py')
        pilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pilot)
        path = self.root / 'prepared.json'
        path.write_text(json.dumps(dict(launched=False, execution_authorized=False,
            wire_schema_bundle=dict(path='never-read'), runtime_path=str(self.root / 'runtime'))))
        with patch.object(pilot, 'PilotSubscriptionAgent') as transport, patch.object(pilot, 'CallBudget') as budget:
            with self.assertRaises(Failure) as caught:
                pilot.run(path)
        self.assertEqual(caught.exception.record['code'], 'PILOT_NOT_AUTHORIZED')
        transport.assert_not_called()
        budget.assert_not_called()
        self.assertFalse((self.root / 'runtime').exists())

    def test_execution_flag_cannot_override_zero_or_inherited_budget(self):
        spec = importlib.util.spec_from_file_location('wire_authorization_guard',
            ROOT / 'implementation/experiments/exp2_controlled_pilot.py')
        pilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pilot)
        for approved, inherited in ((0, 0), (2, 1)):
            path = self.root / f'contradictory_{approved}_{inherited}.json'
            path.write_text(json.dumps(dict(execution_authorized=True, authorized_model_invocations=approved,
                wire_schema_bundle=dict(path='never-read'), old_pilot_relation=dict(
                    approved_limit=approved, inherited_unused_calls=inherited, new_independent_budget=True))))
            with patch.object(pilot, 'verify_inputs') as verify, patch.object(pilot, 'PilotSubscriptionAgent') as transport:
                with self.assertRaises(Failure) as caught:
                    pilot.run(path)
            self.assertEqual(caught.exception.record['code'], 'PILOT_NOT_AUTHORIZED')
            verify.assert_not_called()
            transport.assert_not_called()

    def test_frozen_manifest_sha_rejects_even_semantically_equivalent_reformat(self):
        spec = importlib.util.spec_from_file_location('wire_pin_guard',
            ROOT / 'implementation/experiments/exp2_controlled_pilot.py')
        pilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pilot)
        config = json.loads((ROOT / 'implementation/experiments/exp2_single_day_pilot_config.json').read_text())
        path = self.bundle / 'transformation_manifest.json'
        config['wire_schema_bundle'] = dict(path=str(self.bundle), transformation_manifest_sha256=
            hashlib.sha256(path.read_bytes()).hexdigest(), **{key: self.manifest[key] for key in (
                'source_schema_sha256', 'wire_schema_sha256', 'wire_schema_version', 'generator_version')})
        path.write_text(json.dumps(json.loads(path.read_text()), sort_keys=True))
        with self.assertRaises(Failure) as caught:
            pilot.verify_inputs(config, self.root / 'never-read-config.json')
        self.assertEqual(caught.exception.record['code'], 'WIRE_SCHEMA_PIN')


if __name__ == '__main__':
    unittest.main()
