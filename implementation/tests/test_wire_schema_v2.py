"""Targeted enum extension regressions; no backend or model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft7Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentic import wire_schema as v1
from agentic import wire_schema_v2 as v2
from agentic.fixtures_v3 import metric_case


ROOT = Path(__file__).resolve().parents[2]
BUSINESS = ROOT / 'implementation/schemas/agent_output_v3_exp2.schema.json'
CORPUS = [None, False, True, 0, 1, 1.0, '', 'A', 'B', 'C', [], {}, ['A'], {'x': 'A'}]


class StringEnumWireV2Tests(unittest.TestCase):
    def equivalent(self, source, corpus=CORPUS):
        before = deepcopy(source)
        wire, manifest = v2.generate_wire_schema(json.dumps(source).encode())
        self.assertEqual(source, before)
        for instance in corpus:
            with self.subTest(instance=instance):
                self.assertEqual(Draft7Validator(source).is_valid(instance),
                                 Draft7Validator(wire).is_valid(instance))
        return wire, manifest

    def test_all_string_enum_redundancy_preserves_positive_and_negative_instances(self):
        wire, manifest = self.equivalent({'enum': ['A', 'B']})
        self.assertEqual(wire, {'enum': ['A', 'B'], 'type': 'string'})
        self.assertEqual(manifest['string_enum_type_annotations'], 1)
        self.assertEqual(manifest['transformations'][0]['enum_values'], ['A', 'B'])
        self.assertFalse(Draft7Validator(wire).is_valid('C'))
        self.assertFalse(Draft7Validator(wire).is_valid(None))

    def test_non_string_or_mixed_enum_only_refuses_without_guess_or_coercion(self):
        for values in ([1, 2], [True, False], [None], ['A', None], ['A', 1],
                       [{'x': 'A'}], [['A']], [1.0, 2.5]):
            with self.subTest(values=values), self.assertRaises(v1.WireSchemaConflict) as caught:
                v2.generate_wire_schema(json.dumps({'enum': values}).encode())
            row = caught.exception.conflicts[0]
            self.assertEqual(row['code'], 'ENUM_ONLY_NON_STRING_UNSUPPORTED')
            self.assertEqual(row['original_values'], values)
            self.assertEqual(len(row['value_types']), len(values))

    def test_existing_nullable_type_and_enum_unchanged(self):
        source = {'type': ['string', 'null'], 'enum': ['A', None]}
        wire, manifest = self.equivalent(source)
        self.assertEqual(wire, source)
        self.assertEqual(manifest['string_enum_type_annotations'], 0)
        self.assertEqual(manifest['enum_inventory'][0]['disposition'], 'EXISTING_TYPE_UNCHANGED')

    def test_literal_data_objects_remain_literal_and_not_schema_containers(self):
        literal = {'properties': {'mode': {'enum': ['A', 'B']}},
                   'enum': ['A', 'B'], 'anyOf': [{'enum': ['A', 'B']}]}
        source = {'const': literal, 'default': literal, 'examples': [literal]}
        wire, manifest = self.equivalent(source, CORPUS + [literal])
        self.assertEqual(wire['const'], literal)
        self.assertEqual(wire['default'], literal)
        self.assertEqual(wire['examples'], [literal])
        self.assertEqual(manifest['enum_inventory'], [])
        self.assertEqual(manifest['const_type_annotations'], 1)

    def test_union_parent_not_typed_and_branches_keep_null(self):
        source = {'anyOf': [{'enum': ['A', 'B']}, {'type': 'null'}]}
        wire, manifest = self.equivalent(source)
        self.assertNotIn('type', wire)
        self.assertEqual(wire['anyOf'][1], {'type': 'null'})
        self.assertEqual(manifest['string_enum_type_annotations'], 1)

    def test_reference_node_not_typed_definition_is_annotated(self):
        source = {'definitions': {'mode': {'enum': ['A', 'B']}}, '$ref': '#/definitions/mode',
                  'enum': [None]}
        wire, manifest = self.equivalent(source)
        self.assertNotIn('type', wire)
        self.assertEqual(wire['enum'], [None])
        self.assertEqual(wire['definitions']['mode']['type'], 'string')
        self.assertEqual(manifest['string_enum_type_annotations'], 1)

    def test_actual_eight_nodes_and_twenty_total_transformations(self):
        raw = BUSINESS.read_bytes()
        original = json.loads(raw)
        old_wire, old_manifest = v1.generate_wire_schema(raw)
        wire, manifest = v2.generate_wire_schema(raw)
        self.assertEqual(manifest['source_schema_sha256'], 'ae599d4744b9fe37ced1fd96cd883646daf6d0057dbc749cb805ed8a0819a6bd')
        self.assertEqual(manifest['transformation_count'], 20)
        self.assertEqual(manifest['const_type_annotations'], 12)
        self.assertEqual(manifest['string_enum_type_annotations'], 8)
        self.assertEqual(len(manifest['enum_inventory']), 8)
        self.assertTrue(all(row['all_values_strings'] for row in manifest['enum_inventory']))
        self.assertEqual(manifest['inherited_const_pass']['wire_schema_sha256'], old_manifest['wire_schema_sha256'])
        restored_v1 = deepcopy(wire)
        for pointer, node in v1.iter_schema_nodes(restored_v1):
            if any(row['schema_pointer'] == pointer and row['operation'] == 'add_redundant_string_enum_type'
                   for row in manifest['transformations']):
                del node['type']
        self.assertEqual(restored_v1, old_wire)
        self.assertEqual(BUSINESS.read_bytes(), raw)
        _, valid, _ = metric_case()
        corpus = [valid]
        for path, value in ((('decision', 'behavior'), 'invalid'), (('decision', 'behavior'), None),
                            (('hypotheses', 'scale_basis'), False),
                            (('decision', 'hard_requirements'), ['unknown_requirement'])):
            variant = deepcopy(valid)
            target = variant
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            corpus.append(variant)
        for i, instance in enumerate(corpus):
            expected = i == 0
            self.assertEqual(Draft7Validator(original).is_valid(instance), expected)
            self.assertEqual(Draft7Validator(old_wire).is_valid(instance), expected)
            self.assertEqual(Draft7Validator(wire).is_valid(instance), expected)

    def test_exact_version_dispatch_keeps_sealed_v1_loadable(self):
        bundle = ROOT / 'implementation/results/wire_schema_compatibility_20261028_v1/offline/wire_bundle'
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir() if p.is_file()}
        _, manifest = v2.load_versioned_wire_bundle(bundle, BUSINESS)
        self.assertEqual(manifest['wire_schema_version'], v1.WIRE_SCHEMA_VERSION)
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir() if p.is_file()})

    def test_v2_bundle_integrity_and_version_downgrade_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / 'wire'
            manifest = v2.export_wire_bundle(BUSINESS, bundle)
            _, loaded = v2.load_versioned_wire_bundle(bundle, BUSINESS)
            self.assertEqual(manifest, loaded)
            self.assertEqual(manifest['backend_acceptance'], 'NOT_TESTED')
            (bundle / 'wire_schema.json').write_text('{}')
            with self.assertRaises(v1.WireSchemaConflict):
                v2.load_versioned_wire_bundle(bundle, BUSINESS)
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / 'wire'
            manifest = v2.export_wire_bundle(BUSINESS, bundle)
            manifest['wire_schema_version'] = v1.WIRE_SCHEMA_VERSION
            manifest['generator_version'] = v1.GENERATOR_VERSION
            (bundle / 'transformation_manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(v1.WireSchemaConflict):
                v2.load_versioned_wire_bundle(bundle, BUSINESS)

    def test_unknown_version_or_inconsistent_version_pair_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / 'wire'
            manifest = v2.export_wire_bundle(BUSINESS, bundle)
            for wire_version, generator_version in [('unknown', v2.GENERATOR_VERSION),
                                                     (v2.WIRE_SCHEMA_VERSION, v1.GENERATOR_VERSION)]:
                changed = deepcopy(manifest)
                changed.update(wire_schema_version=wire_version, generator_version=generator_version)
                (bundle / 'transformation_manifest.json').write_text(json.dumps(changed))
                with self.assertRaises(v1.WireSchemaConflict) as caught:
                    v2.load_versioned_wire_bundle(bundle, BUSINESS)
                self.assertEqual(caught.exception.conflicts[0]['code'], 'WIRE_VERSION_UNSUPPORTED')


if __name__ == '__main__':
    unittest.main()
