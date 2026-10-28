"""Finite wire v2 extension: explicit type on all-string enum-only nodes.

The v1 generator remains immutable. This extension reuses its const pass and
Schema-node traversal, then adds only string types implied by string enums.
It never rewrites a model response or establishes backend acceptance.
"""
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft7Validator

from . import wire_schema as v1
from .evidence_io import reserve_directory, write_json_new


WIRE_SCHEMA_VERSION = 'so_const_string_enum_type_v2'
GENERATOR_VERSION = '2.0.0'
WireSchemaConflict = v1.WireSchemaConflict
wire_bytes = v1.wire_bytes
iter_schema_nodes = v1.iter_schema_nodes


def _json_type(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, float):
        return 'number'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, list):
        return 'array'
    return 'object'


def generate_wire_schema(source_bytes):
    """Add redundant const types plus all-string enum types, or fail closed.

    Non-string enum-only values are individually reported, without inferred
    unions, coercion, null replacement or partial artifact generation.
    """
    wire, base = v1.generate_wire_schema(source_bytes)
    source = v1._parse(source_bytes)
    source_nodes = list(iter_schema_nodes(source))
    wire_nodes = dict(iter_schema_nodes(wire))
    enum_inventory, conflicts, additions = [], [], []
    for pointer, node in source_nodes:
        if not isinstance(node, dict) or 'enum' not in node:
            continue
        values = node['enum']
        row = dict(schema_pointer=pointer, enum_json_pointer=pointer + '/enum',
                   original_values=deepcopy(values),
                   value_types=[dict(index=i, value=deepcopy(value), json_type=_json_type(value))
                                for i, value in enumerate(values)],
                   original_type_present='type' in node,
                   original_type=deepcopy(node.get('type')),
                   all_values_strings=bool(values) and all(isinstance(v, str) for v in values))
        if 'type' in node:
            row['disposition'] = 'EXISTING_TYPE_UNCHANGED'
        elif '$ref' in node:
            row['disposition'] = 'REFERENCE_SIBLING_UNCHANGED'
            row['reason'] = 'Draft 7 ignores $ref siblings; no type is inferred at the reference node.'
        elif not row['all_values_strings']:
            row['disposition'] = 'CONFLICT_NON_STRING_ENUM_ONLY'
            conflicts.append(dict(code='ENUM_ONLY_NON_STRING_UNSUPPORTED', pointer=pointer + '/enum',
                original_values=deepcopy(values), value_types=deepcopy(row['value_types']),
                reason='This limited extension annotates only nonempty all-string enums; no other type or union is inferred.'))
        elif 'type' in wire_nodes[pointer]:
            row['disposition'] = 'TYPE_ALREADY_ANNOTATED_BY_CONST'
        else:
            row['disposition'] = 'ADD_REDUNDANT_STRING_TYPE'
            before = deepcopy(wire_nodes[pointer])
            wire_nodes[pointer]['type'] = 'string'
            additions.append(dict(json_pointer=pointer + '/type', schema_pointer=pointer,
                operation='add_redundant_string_enum_type', original_present=False,
                original_value=None, new_value='string', enum_json_pointer=pointer + '/enum',
                enum_values=deepcopy(values), original_node=before,
                new_node=deepcopy(wire_nodes[pointer]),
                equivalence_basis=('Under standard Draft 7 validation, membership in this nonempty '
                    'enumeration of JSON strings already implies type string. Intersecting that '
                    'constraint with type string preserves the legal instance set; every enum '
                    'value and all other constraints remain unchanged.')))
        enum_inventory.append(row)
    if conflicts:
        raise WireSchemaConflict(conflicts)
    Draft7Validator.check_schema(wire)
    manifest = deepcopy(base)
    manifest.update(
        wire_schema_version=WIRE_SCHEMA_VERSION, generator_version=GENERATOR_VERSION,
        wire_schema_sha256=v1._sha(wire_bytes(wire)),
        wire_schema_canonical_sha256=v1._sha(v1._canonical_bytes(wire)),
        inherited_const_pass=dict(wire_schema_version=v1.WIRE_SCHEMA_VERSION,
            generator_version=v1.GENERATOR_VERSION, wire_schema_sha256=base['wire_schema_sha256'],
            transformation_count=base['transformation_count']),
        transformation_count=base['transformation_count'] + len(additions),
        transformations=deepcopy(base['transformations']) + additions,
        const_type_annotations=base['transformation_count'], string_enum_type_annotations=len(additions),
        enum_inventory=enum_inventory,
        scope='Draft 7 const-type pass from immutable v1 plus nonempty all-string enum-only type annotations; no union/reference inference.',
        guarantee_limit=('Each listed transformation has a local const-implies-type or string-enum-implies-string argument. '
            'Finite differential tests are regression evidence, not a general Schema equivalence proof '
            'or evidence that the backend accepts this wire schema.'),
        real_model_calls=0, backend_acceptance='NOT_TESTED')
    return wire, manifest


def export_wire_bundle(source_path, out):
    source_path = Path(source_path)
    wire, manifest = generate_wire_schema(source_path.read_bytes())
    target = reserve_directory(out, readonly_inputs=(source_path,))
    write_json_new(target / 'wire_schema.json', wire)
    write_json_new(target / 'transformation_manifest.json', manifest)
    return manifest


def load_wire_bundle(bundle_dir, business_schema_path):
    """Regenerate v2 fully; never accept a best-effort or fallback variant."""
    bundle = Path(bundle_dir)
    expected_wire, expected_manifest = generate_wire_schema(Path(business_schema_path).read_bytes())
    try:
        raw = (bundle / 'wire_schema.json').read_bytes()
        manifest = v1._parse((bundle / 'transformation_manifest.json').read_bytes())
        wire = v1._parse(raw)
    except (OSError, WireSchemaConflict) as error:
        raise WireSchemaConflict([dict(code='BUNDLE_READ_ERROR', pointer='', reason=str(error))]) from None
    if raw != wire_bytes(expected_wire) or v1._canonical_bytes(manifest) != v1._canonical_bytes(expected_manifest):
        raise WireSchemaConflict([dict(code='BUNDLE_INTEGRITY_MISMATCH', pointer='',
            reason='Wire v2 bytes or transformation manifest differ from regeneration using original business bytes.')])
    return wire, manifest


def load_versioned_wire_bundle(bundle_dir, business_schema_path):
    """Explicit v1/v2 dispatch based on a subsequently verified version pair."""
    try:
        manifest = v1._parse((Path(bundle_dir) / 'transformation_manifest.json').read_bytes())
    except (OSError, WireSchemaConflict) as error:
        raise WireSchemaConflict([dict(code='BUNDLE_READ_ERROR', pointer='', reason=str(error))]) from None
    if not isinstance(manifest, dict):
        raise WireSchemaConflict([dict(code='BUNDLE_MANIFEST_INVALID', pointer='', reason='Manifest must be an object.')])
    version_pair = (manifest.get('wire_schema_version'), manifest.get('generator_version'))
    if version_pair == (v1.WIRE_SCHEMA_VERSION, v1.GENERATOR_VERSION):
        return v1.load_wire_bundle(bundle_dir, business_schema_path)
    if version_pair == (WIRE_SCHEMA_VERSION, GENERATOR_VERSION):
        return load_wire_bundle(bundle_dir, business_schema_path)
    raise WireSchemaConflict([dict(code='WIRE_VERSION_UNSUPPORTED', pointer='',
        wire_schema_version=version_pair[0], generator_version=version_pair[1],
        reason='Only exact registered v1 or v2 version pairs are allowed; no downgrade or inferred fallback.')])
