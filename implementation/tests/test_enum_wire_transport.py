"""One authored local RPC verifies explicit v2 without changing business gates."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.contracts import validate
from agentic.pilot_transport import SCHEMA
from agentic.wire_schema_v2 import export_wire_bundle
import test_wire_transport as wire_test_fixture


class EnumWireTransportTests(unittest.TestCase):
    def test_explicit_v2_rpc_uses_string_enum_and_original_business_gate(self):
        # Compose the existing local-process fixture, not its test suite.
        fixture = wire_test_fixture.WireTransportTests()
        fixture.setUp()
        try:
            fixture.bundle = fixture.root / 'v2'
            manifest = export_wire_bundle(Path(__file__).resolve().parents[1] / 'schemas' / SCHEMA,
                                          fixture.bundle)
            agent = fixture.agent()
            with patch('agentic.pilot_transport.validate', wraps=validate) as gate:
                output, provenance = agent.decide(fixture.cap)
            self.assertEqual(output, fixture.output)
            self.assertEqual(provenance['source'], 'mock_transport')
            self.assertEqual(provenance['schema_transport']['wire_schema_version'], 'so_const_string_enum_type_v2')
            self.assertEqual(provenance['schema_transport']['wire_schema_sha256'], manifest['wire_schema_sha256'])
            self.assertIn(SCHEMA, [call.args[0] for call in gate.call_args_list])
            requests = json.loads((agent.evidence_dir / 'attempt_01/requests.json').read_text())
            sent = next(r for r in requests if r['method'] == 'turn/start')['params']['outputSchema']
            self.assertEqual(sent['properties']['decision']['properties']['behavior'],
                             dict(enum=['stop_yield', 'hold_lane', 'resume'], type='string'))
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
