"""Redundant Draft 7 ``const`` type annotations for a separate wire schema.

This is a deliberately small, offline transformation.  It does not implement a
general Structured Outputs compiler or establish backend acceptance.  Runtime
responses must still be validated against the original business schema.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import unquote

from jsonschema import Draft7Validator

from .evidence_io import reserve_directory, write_json_new


WIRE_SCHEMA_VERSION = 'so_const_type_v1'
GENERATOR_VERSION = '1.0.0'
_DRAFT7_URIS = {
    'http://json-schema.org/draft-07/schema#',
    'http://json-schema.org/draft-07/schema',
    'https://json-schema.org/draft-07/schema#',
    'https://json-schema.org/draft-07/schema',
}
_MAP_SCHEMAS = ('properties', 'patternProperties', 'definitions')
_SINGLE_SCHEMAS = ('additionalProperties', 'additionalItems', 'propertyNames',
                   'contains', 'not', 'if', 'then', 'else')
_ARRAY_SCHEMAS = ('allOf', 'anyOf', 'oneOf')
_SERIALIZATION = 'UTF-8; ensure_ascii=false; indent=2; allow_nan=false; LF terminator'


class WireSchemaConflict(ValueError):
    """No wire artifact is produced when the supported scope is exceeded."""

    def __init__(self, conflicts):
        self.conflicts = conflicts
        super().__init__('Wire schema generation refused: ' +
                         '; '.join(c['code'] + ' at ' + (c.get('pointer') or '/')
                                   for c in conflicts))


def _escape(part):
    return str(part).replace('~', '~0').replace('/', '~1')


def _child(pointer, key):
    return pointer + '/' + _escape(key)


def iter_schema_nodes(schema):
    """Yield syntactic Draft 7 schema nodes, never instances or annotations.

    ``$ref`` siblings are ignored by Draft 7 evaluation.  They remain syntactic
    schema nodes for traversal (another local reference may target them), but a
    node containing ``$ref`` is never itself type-annotated.  Definitions are
    visited once syntactically, so recursive references do not recurse here.
    Unknown keywords, ``$defs`` (not Draft 7), const, enum, default and examples
    are not schema containers in this grammar.
    """
    def visit(node, pointer):
        if not isinstance(node, (dict, bool)):
            return
        yield pointer, node
        if not isinstance(node, dict):
            return
        for keyword in _MAP_SCHEMAS:
            value = node.get(keyword)
            if isinstance(value, dict):
                for name, child in value.items():
                    yield from visit(child, _child(_child(pointer, keyword), name))
        for keyword in _SINGLE_SCHEMAS:
            if keyword in node:
                yield from visit(node[keyword], _child(pointer, keyword))
        for keyword in _ARRAY_SCHEMAS:
            value = node.get(keyword)
            if isinstance(value, list):
                for index, child in enumerate(value):
                    yield from visit(child, _child(_child(pointer, keyword), index))
        items = node.get('items')
        if isinstance(items, list):
            for index, child in enumerate(items):
                yield from visit(child, _child(_child(pointer, 'items'), index))
        elif 'items' in node:
            yield from visit(items, _child(pointer, 'items'))
        dependencies = node.get('dependencies')
        if isinstance(dependencies, dict):
            for name, child in dependencies.items():
                # A list here is a property dependency, not a schema array.
                if isinstance(child, (dict, bool)):
                    yield from visit(child, _child(_child(pointer, 'dependencies'), name))
    yield from visit(schema, '')


def wire_bytes(schema):
    """Exact serialization whose byte SHA is recorded in every bundle."""
    return (json.dumps(schema, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _no_duplicates(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate JSON object key: ' + name)
        result[name] = value
    return result


def _parse(source_bytes):
    if not isinstance(source_bytes, bytes):
        raise WireSchemaConflict([dict(code='SOURCE_NOT_BYTES', pointer='',
                                       reason='Exact original UTF-8 bytes are required.')])
    try:
        def reject_constant(value):
            raise ValueError('Non-JSON numeric constant: ' + value)
        def lossless_float(token):
            value = float(token)
            if not math.isfinite(value):
                raise ValueError('Non-finite parsed JSON number: ' + token)
            # Compare mathematical decimal values, rather than the internal
            # binary approximation.  0.1 -> 0.1 is safe; 1.0000000000000001 ->
            # 1.0 and 1e-400 -> 0.0 are not.  Apply this to ALL JSON numbers,
            # including physical bounds, enum entries and annotation data.
            serialized = json.dumps(value, allow_nan=False)
            if Decimal(token) != Decimal(serialized):
                raise ValueError('JSON number would change mathematical value on serialization: ' + token)
            return value
        value = json.loads(source_bytes.decode('utf-8'), object_pairs_hook=_no_duplicates,
                           parse_constant=reject_constant, parse_float=lossless_float)
        # JSON numbers such as 1e400 overflow Python float despite being parsed.
        def check_json(node, pointer=''):
            if isinstance(node, float) and not math.isfinite(node):
                raise ValueError('Non-finite parsed JSON number at ' + pointer)
            if isinstance(node, str):
                node.encode('utf-8')
            if isinstance(node, dict):
                for key, child in node.items():
                    key.encode('utf-8')
                    check_json(child, _child(pointer, key))
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    check_json(child, _child(pointer, index))
        check_json(value)
    except (ValueError, UnicodeError) as error:
        raise WireSchemaConflict([dict(code='INVALID_JSON_SOURCE', pointer='',
                                       reason=str(error))]) from None
    return value


def _literal_type(value):
    # bool must precede int; Python bool subclasses int but JSON does not.
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
    if isinstance(value, dict):
        return 'object'
    raise AssertionError('Only parsed JSON literals reach _literal_type')


def _reference_target(reference):
    if not reference.startswith('#'):
        return None, 'EXTERNAL_REFERENCE_UNSUPPORTED'
    fragment = unquote(reference[1:])
    if not fragment:
        return '', None
    if not fragment.startswith('/'):
        return None, 'ANCHOR_REFERENCE_UNSUPPORTED'
    parts = []
    for part in fragment[1:].split('/'):
        index = 0
        while index < len(part):
            if part[index] == '~':
                if index + 1 >= len(part) or part[index + 1] not in '01':
                    return None, 'INVALID_REFERENCE_POINTER'
                index += 1
            index += 1
        parts.append(part.replace('~1', '/').replace('~0', '~'))
    return ''.join('/' + _escape(part) for part in parts), None


def generate_wire_schema(source_bytes):
    """Return ``(wire_schema, manifest)`` or fail with a conflict list.

    Scope: standard Draft 7, local JSON-pointer references to actual syntactic
    schema nodes, no nested resource IDs.  The only mutation is adding a type
    implied by an existing const literal.  No service is contacted.
    """
    source = _parse(source_bytes)
    conflicts = []
    if not isinstance(source, (dict, bool)):
        raise WireSchemaConflict([dict(code='SCHEMA_ROOT_INVALID', pointer='',
                                       reason='Draft 7 schema must be an object or boolean.')])
    nodes = list(iter_schema_nodes(source))
    node_pointers = {pointer for pointer, _ in nodes}
    for pointer, node in nodes:
        if not isinstance(node, dict):
            continue
        dialect = node.get('$schema')
        if dialect is not None and (not isinstance(dialect, str) or dialect not in _DRAFT7_URIS):
            conflicts.append(dict(code='DIALECT_UNSUPPORTED', pointer=_child(pointer, '$schema'),
                                  original_value=dialect, reason='Only Draft 7 semantics are supported.'))
        if pointer and '$id' in node:
            conflicts.append(dict(code='NESTED_RESOURCE_ID_UNSUPPORTED', pointer=_child(pointer, '$id'),
                                  original_value=node['$id'], reason='Nested base-URI resolution is outside this generator scope.'))
        reference = node.get('$ref')
        if isinstance(reference, str):
            target, error = _reference_target(reference)
            if error:
                conflicts.append(dict(code=error, pointer=_child(pointer, '$ref'),
                                      original_value=reference,
                                      reason='No network dereferencing or anchor inference is performed.'))
            elif target not in node_pointers:
                conflicts.append(dict(code='REFERENCE_TARGET_NOT_SCHEMA_NODE', pointer=_child(pointer, '$ref'),
                                      original_value=reference, target_pointer=target,
                                      reason='Target is absent or ordinary instance/annotation data; dual-role conversion is refused.'))
    try:
        Draft7Validator.check_schema(source)
    except Exception as error:
        # check_schema validates the local meta-schema, not application instances.
        conflicts.append(dict(code='INVALID_DRAFT7_SCHEMA', pointer='', reason=str(error)))
    if conflicts:
        raise WireSchemaConflict(conflicts)

    wire = deepcopy(source)
    wire_nodes = dict(iter_schema_nodes(wire))
    changes = []
    skipped = []
    for pointer, node in nodes:
        if not isinstance(node, dict) or 'const' not in node or 'type' in node:
            continue
        if '$ref' in node:
            skipped.append(dict(pointer=pointer, reason='Draft 7 ignores $ref siblings; no type added at a reference node.'))
            continue
        inferred = _literal_type(node['const'])
        # No inference from anyOf, enum, a reference, examples or annotations.
        before = deepcopy(node)
        wire_nodes[pointer]['type'] = inferred
        changes.append(dict(
            json_pointer=_child(pointer, 'type'), schema_pointer=pointer,
            operation='add_redundant_const_type', original_present=False,
            original_value=None, new_value=inferred,
            const_json_pointer=_child(pointer, 'const'), const_value=deepcopy(node['const']),
            original_node=before, new_node=deepcopy(wire_nodes[pointer]),
            equivalence_basis=('Under standard Draft 7 validation, const equality implies JSON type ' +
                               inferred + '; intersecting with that type preserves the legal instance set. '
                               'JSON numbers compare mathematically; booleans are distinct from numbers. '
                               'No literal data or other constraint is changed.')))
    Draft7Validator.check_schema(wire)
    manifest = dict(
        artifact_type='structured_outputs_wire_schema_bundle',
        wire_schema_version=WIRE_SCHEMA_VERSION,
        generator_version=GENERATOR_VERSION,
        source_schema_sha256=_sha(source_bytes),
        source_schema_canonical_sha256=_sha(_canonical_bytes(source)),
        wire_schema_sha256=_sha(wire_bytes(wire)),
        wire_schema_canonical_sha256=_sha(_canonical_bytes(wire)),
        hash_semantics=dict(source_schema_sha256='exact supplied business schema bytes',
                            wire_schema_sha256='exact wire_schema.json bytes using recorded serialization',
                            canonical='JSON sorted keys, compact separators, UTF-8; not original file bytes'),
        wire_serialization=_SERIALIZATION,
        schema_dialect='Draft 7 (declared, or explicit generator default when absent)',
        numeric_scope=('Every parsed JSON number must preserve its mathematical decimal value '
                       'under wire serialization. Arbitrary-precision JSON integers remain integers; '
                       'float overflow, underflow or lost decimal precision is refused. '
                       'Equivalent numeric spellings such as 1 and 1.0 denote the same JSON Schema number.'),
        transformation_count=len(changes), transformations=changes,
        reference_nodes_skipped=skipped, schema_node_count=len(nodes),
        scope='Only redundant const types; standard Draft 7 local JSON-pointer references; no nested resource IDs.',
        guarantee_limit=('Each listed transformation has a local const-implies-type argument. '
                         'Differential samples are regression evidence, not a general schema equivalence proof '
                         'or evidence that a backend accepts this schema.'),
        runtime_requirement='Preserve raw native JSON and validate original business schema before tools; no instance repair.',
        real_model_calls=0, backend_acceptance='NOT_TESTED')
    return wire, manifest


def export_wire_bundle(source_path, out):
    """Generate fully before reserving a fresh, non-evidence output directory."""
    source_path = Path(source_path)
    wire, manifest = generate_wire_schema(source_path.read_bytes())
    target = reserve_directory(out, readonly_inputs=(source_path,))
    write_json_new(target / 'wire_schema.json', wire)
    write_json_new(target / 'transformation_manifest.json', manifest)
    return manifest


def load_wire_bundle(bundle_dir, business_schema_path):
    """Recompute the complete artifact; a tampered wire/manifest is rejected."""
    bundle = Path(bundle_dir)
    expected_wire, expected_manifest = generate_wire_schema(Path(business_schema_path).read_bytes())
    try:
        actual_wire_bytes = (bundle / 'wire_schema.json').read_bytes()
        actual_manifest = _parse((bundle / 'transformation_manifest.json').read_bytes())
        actual_wire = _parse(actual_wire_bytes)
    except (OSError, WireSchemaConflict) as error:
        raise WireSchemaConflict([dict(code='BUNDLE_READ_ERROR', pointer='', reason=str(error))]) from None
    # Equality must distinguish JSON booleans from numbers and numeric spellings
    # in the actual stored file; exact generated bytes establish this as well.
    if (actual_wire_bytes != wire_bytes(expected_wire) or
            _canonical_bytes(actual_manifest) != _canonical_bytes(expected_manifest)):
        raise WireSchemaConflict([dict(code='BUNDLE_INTEGRITY_MISMATCH', pointer='',
                                       reason='Wire bytes or transformation manifest differ from regeneration using business schema bytes.')])
    return actual_wire, actual_manifest
