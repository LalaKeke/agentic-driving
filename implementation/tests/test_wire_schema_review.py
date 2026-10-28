"""Document support, observed failures and unknown forms stay distinct."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentic.wire_schema import generate_wire_schema
from agentic.wire_schema_review import (build_compatibility_review, SUPPORTED,
                                         CONFLICT, UNKNOWN, OBSERVED_CONST_POINTER)


def root(fields):
    return dict(type='object', properties=fields, required=list(fields), additionalProperties=False)


class WireSchemaSupportReviewTests(unittest.TestCase):
    def rows(self, schema, rule, **options):
        return [r for r in build_compatibility_review(schema, **options)['checks'] if r['rule'] == rule]

    def test_three_statuses_are_not_backend_pass(self):
        schema = root({'mode': {'enum': ['A', 'B']}})
        report = build_compatibility_review(schema)
        self.assertFalse(report['service_acceptance_established'])
        self.assertEqual(0, report['automatic_transforms_applied'])
        self.assertEqual(UNKNOWN, self.rows(schema, 'ENUM_EXPLICIT_TYPE')[0]['status'])
        self.assertEqual(SUPPORTED, self.rows(schema, 'CLOSED_OBJECT')[0]['status'])

    def test_root_conflicts_not_auto_wrapped(self):
        schema = {'anyOf': [{'type': 'string'}, {'type': 'null'}]}
        before = deepcopy(schema)
        self.assertEqual(CONFLICT, self.rows(schema, 'ROOT_OBJECT_NO_ANYOF')[0]['status'])
        self.assertEqual(before, schema)

    def test_required_nullable_is_not_optional(self):
        schema = root({'x': {'type': ['number', 'null']}})
        self.assertEqual(SUPPORTED, self.rows(schema, 'ALL_PROPERTIES_REQUIRED')[0]['status'])
        schema['required'] = []
        self.assertEqual(CONFLICT, self.rows(schema, 'ALL_PROPERTIES_REQUIRED')[0]['status'])
        self.assertEqual([], schema['required'])

    def test_object_must_be_closed(self):
        schema = root({'x': {'type': 'number'}})
        del schema['additionalProperties']
        self.assertEqual(CONFLICT, self.rows(schema, 'CLOSED_OBJECT')[0]['status'])

    def test_nullable_object_also_requires_closure_and_required(self):
        schema = root({'x': {'type': ['object', 'null'], 'properties': {'a': {'type': 'string'}}}})
        rows = self.rows(schema, 'CLOSED_OBJECT')
        self.assertEqual(CONFLICT, rows[1]['status'])
        self.assertEqual('/properties/x/additionalProperties', rows[1]['json_pointer'])
        self.assertEqual(CONFLICT, self.rows(schema, 'ALL_PROPERTIES_REQUIRED')[1]['status'])

    def test_literal_data_is_not_schema_inventory(self):
        literal = {'allOf': [{'const': 3}], 'properties': {'x': {'enum': [1, 2]}}}
        schema = root({'x': {'const': literal, 'default': literal,
                              'examples': [literal], 'enum': [literal]}})
        report = build_compatibility_review(schema)
        self.assertFalse(any(r['rule'] == 'UNSUPPORTED_COMPOSITION' for r in report['checks']))
        self.assertEqual(1, report['metrics']['total_enum_values'])

    def test_exact_observed_failure_and_analogue_differ(self):
        schema = root({'hypotheses': root({'coordinate_frame': {'const': 'ego_at_reference'},
                                          'other': {'const': 'X'}})})
        self.assertEqual(CONFLICT, self.rows(schema, 'OBSERVED_CONST_MISSING_TYPE')[0]['status'])
        self.assertEqual(OBSERVED_CONST_POINTER,
                         self.rows(schema, 'OBSERVED_CONST_MISSING_TYPE')[0]['json_pointer'])
        self.assertEqual(UNKNOWN, self.rows(schema, 'CONST_MISSING_TYPE_ANALOGUE')[0]['status'])

    def test_unsupported_composition_kept(self):
        schema = root({'x': {'type': 'integer', 'not': {'const': 3}}})
        self.assertEqual(CONFLICT, self.rows(schema, 'UNSUPPORTED_COMPOSITION')[0]['status'])
        self.assertIn('not', schema['properties']['x'])

    def test_general_and_fine_tuned_constraints_separate(self):
        schema = root({'x': {'type': 'string', 'pattern': '^a', 'minLength': 2}})
        self.assertEqual(SUPPORTED, self.rows(schema, 'DOCUMENTED_TYPED_CONSTRAINT')[0]['status'])
        self.assertEqual(CONFLICT,
                         self.rows(schema, 'DOCUMENTED_TYPED_CONSTRAINT', fine_tuned=True)[0]['status'])
        self.assertEqual(UNKNOWN, self.rows(schema, 'MODEL_SPECIFIC_KEYWORD')[0]['status'])
        self.assertEqual(CONFLICT, self.rows(schema, 'MODEL_SPECIFIC_KEYWORD', fine_tuned=True)[0]['status'])

    def test_local_recursive_ref_does_not_expand_forever(self):
        schema = root({'next': {'anyOf': [{'$ref': '#'}, {'type': 'null'}]}})
        rows = self.rows(schema, 'REFERENCE')
        self.assertEqual(SUPPORTED, rows[0]['status'])
        self.assertEqual('', rows[0]['target_pointer'])
        self.assertEqual(UNKNOWN, self.rows(schema, 'SYNTACTIC_CONTAINER_DEPTH')[0]['status'])

    def test_external_and_data_ref_are_unconfirmed(self):
        for ref in ('https://invalid.example/schema', '#/properties/x/default'):
            schema = root({'x': {'default': {'type': 'string'}, '$ref': ref}})
            self.assertEqual(UNKNOWN, self.rows(schema, 'REFERENCE')[0]['status'])

    def test_modern_definitions_are_reviewed_legacy_spelling_unconfirmed(self):
        schema = root({'x': {'$ref': '#/$defs/name'}})
        schema['$defs'] = {'name': {'type': 'string', 'not': {'const': 'bad'}}}
        self.assertEqual(SUPPORTED, self.rows(schema, 'LOCAL_DEFINITIONS')[0]['status'])
        self.assertEqual(CONFLICT, self.rows(schema, 'UNSUPPORTED_COMPOSITION')[0]['status'])
        schema['definitions'] = {'name': {'type': 'string'}}
        self.assertEqual(UNKNOWN, self.rows(schema, 'LEGACY_DEFINITIONS_SPELLING')[0]['status'])

    def test_limits_count_instances_not_schema_literal_data(self):
        schema = root({'x': {'type': 'string', 'enum': ['V'] * 1001}})
        self.assertEqual(CONFLICT, self.rows(schema, 'TOTAL_ENUM_VALUES')[0]['status'])
        large = root({'x': {'type': 'string', 'enum': ['a' * 61] * 251}})
        self.assertEqual(CONFLICT, self.rows(large, 'SINGLE_ENUM_STRING_LIMIT')[0]['status'])

    def test_business_wire_still_has_unconfirmed_constraints(self):
        path = Path(__file__).resolve().parents[1] / 'schemas' / 'agent_output_v3_exp2.schema.json'
        original_bytes = path.read_bytes()
        original = json.loads(original_bytes)
        wire, _ = generate_wire_schema(original_bytes)
        original_review = build_compatibility_review(original)
        wire_review = build_compatibility_review(wire)
        self.assertEqual(1, len([r for r in original_review['checks']
                                if r['rule'] == 'OBSERVED_CONST_MISSING_TYPE']))
        self.assertFalse(any(r['rule'] == 'OBSERVED_CONST_MISSING_TYPE' for r in wire_review['checks']))
        self.assertEqual(8, len([r for r in wire_review['checks']
                                if r['rule'] == 'ENUM_EXPLICIT_TYPE' and r['status'] == UNKNOWN]))
        self.assertEqual(41, len([r for r in wire_review['checks']
                                 if r['rule'] == 'MODEL_SPECIFIC_KEYWORD']))
        self.assertFalse(wire_review['service_acceptance_established'])
        self.assertEqual(original_bytes, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
