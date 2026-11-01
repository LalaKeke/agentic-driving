"""Independent positive/negative instance checks for the offline wire layer.

These finite examples check the generator's narrow const-implies-type argument;
they do not prove arbitrary Schema equivalence or backend acceptance.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft7Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentic.fixtures_v3 import metric_case
from agentic.wire_schema import (
    WireSchemaConflict, generate_wire_schema, iter_schema_nodes, wire_bytes,
)


DIALECT = 'http://json-schema.org/draft-07/schema#'
CORPUS = [
    None, True, False, 0, 1, 1.0, 1.25, -1, '', 'tag', 'other', [],
    [1], [True], ['tag'], {}, {'x': 1}, {'x': True}, {'tag': 'tag'},
    {'x': [1, None]}, {'type': {'const': 'ordinary data'}},
]


class WireSchemaSemanticTests(unittest.TestCase):
    def generate(self, source):
        raw = json.dumps(source, ensure_ascii=False, allow_nan=False).encode()
        before = deepcopy(source)
        wire, manifest = generate_wire_schema(raw)
        self.assertEqual(source, before, 'generation must not mutate input data')
        self.assertEqual(manifest['source_schema_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(manifest['wire_schema_sha256'], hashlib.sha256(wire_bytes(wire)).hexdigest())
        return wire, manifest

    def equivalent_on(self, source, values):
        wire, manifest = self.generate(source)
        business_validator = Draft7Validator(source)
        wire_validator = Draft7Validator(wire)
        for value in values:
            with self.subTest(source=source, instance=value):
                self.assertEqual(business_validator.is_valid(value), wire_validator.is_valid(value))
        return wire, manifest

    def test_scalar_const_acceptance_and_rejection_are_preserved(self):
        for value in ('tag', True, False, None, 1, 1.0, 1.25):
            source = {'$schema': DIALECT, 'const': value}
            wire, manifest = self.equivalent_on(source, CORPUS)
            self.assertTrue(Draft7Validator(wire).is_valid(value))
            self.assertEqual(manifest['transformation_count'], 1)
            self.assertEqual(wire['const'], value)

    def test_boolean_and_number_are_not_interchangeable(self):
        for literal, wrong in ((True, 1), (False, 0), (1, True), (0, False)):
            source = {'$schema': DIALECT, 'const': literal}
            wire, _ = self.equivalent_on(source, [literal, wrong])
            self.assertFalse(Draft7Validator(wire).is_valid(wrong))

    def test_mathematically_equal_integer_and_float_stay_accepted(self):
        for literal in (1, 1.0):
            wire, _ = self.equivalent_on({'$schema': DIALECT, 'const': literal}, [1, 1.0, True, 1.01])
            self.assertTrue(Draft7Validator(wire).is_valid(1))
            self.assertTrue(Draft7Validator(wire).is_valid(1.0))
            self.assertFalse(Draft7Validator(wire).is_valid(True))

    def test_nested_literal_object_and_array_remain_ordinary_data(self):
        literal = {
            'properties': {'x': {'const': 3}},
            'anyOf': [{'const': False}],
            'type': {'const': 'ordinary data'},
            '$schema': 'not a schema dialect inside data',
        }
        for value in (literal, [literal, {'const': None}]):
            source = {'$schema': DIALECT, 'type': 'object', 'properties': {'payload': {'const': value}},
                      'required': ['payload'], 'additionalProperties': False}
            wire, manifest = self.equivalent_on(source, [{'payload': value}, {}, {'payload': None},
                                                         {'payload': value, 'extra': 1}, {'payload': deepcopy(literal)}])
            self.assertEqual(wire['properties']['payload']['const'], value)
            self.assertEqual(manifest['transformation_count'], 1)
            paths = [pointer for pointer, _ in iter_schema_nodes(wire)]
            self.assertFalse(any('/const/' in pointer for pointer in paths))

    def test_enum_default_examples_and_unknown_annotations_are_untouched(self):
        literal = {'properties': {'x': {'const': 1}}, '$schema': 'ordinary literal'}
        source = {'$schema': DIALECT, 'enum': [literal, None], 'default': literal,
                  'examples': [literal], 'x-extension': {'const': 'annotation'}}
        wire, manifest = self.equivalent_on(source, CORPUS + [literal])
        self.assertEqual(wire, source)
        self.assertEqual(manifest['transformation_count'], 0)
        self.assertEqual(list(iter_schema_nodes(source)), [('', source)])

    def test_enum_only_and_anyof_parents_do_not_receive_guessed_types(self):
        source = {'$schema': DIALECT, 'anyOf': [{'enum': ['tag', None]}, {'const': 1}]}
        wire, manifest = self.equivalent_on(source, CORPUS)
        self.assertNotIn('type', wire)
        self.assertNotIn('type', wire['anyOf'][0])
        self.assertEqual(manifest['transformation_count'], 1)

    def test_local_reference_definition_changes_but_reference_node_does_not(self):
        source = {'$schema': DIALECT, 'type': 'object',
                  'definitions': {'tag': {'const': 'tag'}},
                  'properties': {'x': {'$ref': '#/definitions/tag'}},
                  'required': ['x'], 'additionalProperties': False}
        wire, manifest = self.equivalent_on(source, CORPUS + [{'x': 'tag'}, {'x': 'other'}, {'x': None}])
        self.assertEqual(wire['properties']['x'], source['properties']['x'])
        self.assertEqual(manifest['transformation_count'], 1)
        self.assertEqual(manifest['transformations'][0]['schema_pointer'], '/definitions/tag')

    def test_draft7_ref_sibling_const_is_ignored_and_never_type_annotated(self):
        source = {'$schema': DIALECT, 'definitions': {'number': {'type': 'number'}},
                  '$ref': '#/definitions/number', 'const': 'ignored sibling'}
        wire, manifest = self.equivalent_on(source, CORPUS)
        self.assertEqual(wire, source)
        self.assertTrue(Draft7Validator(wire).is_valid(1))
        self.assertFalse(Draft7Validator(wire).is_valid('ignored sibling'))
        self.assertEqual(manifest['transformation_count'], 0)

    def test_recursive_local_reference_stays_finite_to_generate(self):
        source = {'$schema': DIALECT, 'definitions': {'node': {
            'anyOf': [{'const': None}, {'type': 'object', 'properties': {
                'tag': {'const': 'tag'}, 'next': {'$ref': '#/definitions/node'}},
                'required': ['tag', 'next'], 'additionalProperties': False}]}},
            '$ref': '#/definitions/node'}
        self.equivalent_on(source, [None, {'tag': 'tag', 'next': None},
                                    {'tag': 'tag', 'next': {'tag': 'tag', 'next': None}},
                                    {'tag': 'wrong', 'next': None}, {'tag': 'tag'}, False])

    def test_reference_into_literal_data_refuses_instead_of_mutating_data(self):
        source = {'$schema': DIALECT, 'definitions': {'holder': {'const': {'const': 1}}},
                  'properties': {'x': {'$ref': '#/definitions/holder/const'}}}
        with self.assertRaises(WireSchemaConflict) as ctx:
            self.generate(source)
        self.assertTrue(any(item['code'] == 'REFERENCE_TARGET_NOT_SCHEMA_NODE'
                            for item in ctx.exception.conflicts))

    def test_external_ref_and_unknown_dialect_stop_automatic_generation(self):
        for source in ({'$schema': DIALECT, '$ref': 'https://example.invalid/schema'},
                       {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'const': 'tag'}):
            with self.subTest(source=source), self.assertRaises(WireSchemaConflict):
                self.generate(source)

    def test_non_json_numeric_values_and_duplicate_keys_are_refused(self):
        for raw in (b'{"const":NaN}', b'{"const":Infinity}', b'{"const":1e400}',
                    b'{"const":1,"const":2}'):
            with self.subTest(raw=raw), self.assertRaises(WireSchemaConflict):
                generate_wire_schema(raw)

    def test_numeric_tokens_that_cannot_roundtrip_do_not_silently_move_bounds(self):
        # Refuse a source token that Python float parsing would alter, including
        # values outside const. No unproved rounding of physical constraints.
        for raw in (b'{"const":1.0000000000000001}', b'{"const":1e-400}',
                    b'{"type":"number","minimum":1.0000000000000001}',
                    b'{"enum":[1.0000000000000001]}',
                    b'{"default":1.0000000000000001}',
                    b'{"examples":[1.0000000000000001]}'):
            with self.subTest(raw=raw), self.assertRaises(WireSchemaConflict):
                generate_wire_schema(raw)
        for raw in (b'{"const":0.1}', b'{"const":1.0}',
                    b'{"const":999999999999999999999999999999999999}'):
            source = json.loads(raw)
            wire, _ = generate_wire_schema(raw)
            for value in (source['const'], None, True, 'other'):
                self.assertEqual(Draft7Validator(source).is_valid(value),
                                 Draft7Validator(wire).is_valid(value))

    def test_existing_type_and_physical_numeric_constraints_are_not_rewritten(self):
        source = {'$schema': DIALECT, 'type': 'object', 'properties': {
            'x': {'const': 1, 'type': 'integer', 'minimum': 0, 'maximum': 2},
            'speed': {'type': 'number', 'minimum': 0, 'maximum': 12}},
            'required': ['x', 'speed'], 'additionalProperties': False}
        wire, manifest = self.equivalent_on(source, [{'x': 1, 'speed': 6}, {'x': 1.0, 'speed': 0},
                                                     {'x': 1, 'speed': 13}, {'x': 1, 'speed': -1},
                                                     {'x': 1}, {'x': True, 'speed': 6}])
        self.assertEqual(wire, source)
        self.assertEqual(manifest['transformation_count'], 0)

    def test_property_dependency_names_are_not_misread_as_schemas(self):
        source = {'$schema': DIALECT, 'type': 'object', 'dependencies': {'x': ['y']},
                  'properties': {'x': {'const': 1}, 'y': {'const': 'tag'}}}
        wire, _ = self.equivalent_on(source, [{}, {'x': 1}, {'x': 1, 'y': 'tag'}, {'y': 'tag'},
                                             {'x': 2, 'y': 'tag'}])
        self.assertEqual(wire['dependencies'], source['dependencies'])

    def test_boolean_schemas_keep_all_or_empty_instance_sets(self):
        for source in (True, False):
            wire, manifest = self.equivalent_on(source, CORPUS)
            self.assertIs(wire, source)
            self.assertEqual(manifest['transformation_count'], 0)

    def test_actual_business_fixture_and_negative_mutations_remain_equivalent(self):
        schema_path = Path(__file__).resolve().parents[1] / 'schemas' / 'agent_output_v3_exp2.schema.json'
        raw = schema_path.read_bytes()
        source = json.loads(raw)
        wire, manifest = generate_wire_schema(raw)
        _, valid, _ = metric_case()
        mutations = [valid]
        for path, value in ((('contract_version',), '3.0.0-exp1'),
                            (('coverage_profile',), 'other'),
                            (('hypotheses', 'coordinate_frame'), 'global'),
                            (('hypotheses', 'units'), None)):
            variant = deepcopy(valid)
            target = variant
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            mutations.append(variant)
        missing = deepcopy(valid)
        del missing['contract_version']
        mutations.append(missing)
        extra = deepcopy(valid)
        extra['injected'] = 'not allowed'
        mutations.append(extra)
        self.assertTrue(Draft7Validator(source).is_valid(valid))
        for index, value in enumerate(mutations):
            expected = index == 0
            self.assertEqual(Draft7Validator(source).is_valid(value), expected)
            self.assertEqual(Draft7Validator(wire).is_valid(value), expected)
        self.assertEqual(manifest['source_schema_sha256'], hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    unittest.main()
