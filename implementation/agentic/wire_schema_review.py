"""Read-only, three-valued Structured Outputs subset review.

This inventory is not a backend validator.  In particular the app-server RPC
``outputSchema`` parameter's generic JSON type is not a service capability test.
No schema or generated instance is modified by this module.
"""
from collections import Counter
from copy import deepcopy
from urllib.parse import unquote

from .wire_schema import iter_schema_nodes


REVIEW_VERSION = 'so_documented_subset_review_v1'
GUIDE = 'https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses'
REVIEWED_ON = '2026-10-28'
SUPPORTED = 'CONFIRMED_SUPPORTED'
CONFLICT = 'EXPLICIT_CONFLICT'
UNKNOWN = 'UNCONFIRMED'
OBSERVED_CONST_POINTER = '/properties/hypotheses/properties/coordinate_frame'
_BASE_TYPES = {'object', 'array', 'string', 'number', 'integer', 'boolean', 'null'}
_UNSUPPORTED = {'allOf', 'not', 'dependentRequired', 'dependentSchemas', 'if', 'then', 'else'}
_GENERAL_CONSTRAINTS = {'pattern', 'format', 'multipleOf', 'minimum', 'maximum',
                        'exclusiveMinimum', 'exclusiveMaximum', 'minItems', 'maxItems'}
_FORMATS = {'date-time', 'time', 'date', 'duration', 'email', 'hostname', 'ipv4', 'ipv6', 'uuid'}
_KNOWN = {'type', 'properties', 'required', 'additionalProperties', 'items', 'anyOf',
          'enum', 'const', '$ref', '$defs', 'definitions', '$schema', '$id',
          'title', 'description', 'default', 'examples'} | _UNSUPPORTED | _GENERAL_CONSTRAINTS
_ANNOTATIONS_UNCONFIRMED = {'$schema', '$id', 'title', 'default', 'examples'}
_FINE_TUNED_CONFLICTS = {'minLength', 'maxLength', 'pattern', 'format', 'minimum',
                         'maximum', 'multipleOf', 'patternProperties', 'minItems', 'maxItems'}


def _esc(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def _contains_type(node, type_name):
    types = node.get('type')
    return types == type_name or (isinstance(types, list) and type_name in types)


def _is_container(node):
    return _contains_type(node, 'object') or _contains_type(node, 'array')


def _nodes(schema):
    # The transformation's Draft7 walker never enters ordinary literal data.
    # Here also inventory modern $defs, which the official guide illustrates.
    seen = set()
    pending = [('', schema)]
    while pending:
        base, root = pending.pop()
        for suffix, node in iter_schema_nodes(root):
            pointer = base + suffix
            if pointer in seen:
                continue
            seen.add(pointer)
            yield pointer, node
            if isinstance(node, dict) and isinstance(node.get('$defs'), dict):
                for name, child in node['$defs'].items():
                    pending.append((pointer + '/$defs/' + _esc(name), child))


def _local_target(schema, reference, nodes):
    if not isinstance(reference, str) or not reference.startswith('#'):
        return None, 'External or non-string reference has not been fetched.'
    fragment = unquote(reference[1:])
    if fragment == '':
        return '', None
    if not fragment.startswith('/'):
        return None, 'Anchor-style reference is not resolved by this inventory.'
    pointer = ''
    value = schema
    try:
        for token in fragment[1:].split('/'):
            key = token.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
            pointer += '/' + _esc(key)
    except (KeyError, ValueError, IndexError, TypeError):
        return None, 'Local reference does not resolve.'
    if pointer not in nodes:
        return None, 'Reference resolves outside recognized schema-node roles.'
    return pointer, None


def build_compatibility_review(schema, *, fine_tuned=None,
                               observed_const_pointer=OBSERVED_CONST_POINTER):
    """Return an exhaustive node/keyword inventory without granting execution.

    ``fine_tuned=None`` deliberately makes no model-family claim.  Confirmed
    entries describe the documented general API subset, not this account, model
    alias, CLI release or deployment.  Only the prior observed pointer is a
    directly observed missing-type failure; analogous nodes remain unconfirmed.
    """
    original = deepcopy(schema)
    node_items = list(_nodes(schema))
    node_map = dict(node_items)
    checks = []

    def add(pointer, rule, status, value, basis, **extra):
        checks.append(dict(json_pointer=pointer, rule=rule, status=status,
                           value=deepcopy(value), basis=basis, source=GUIDE, **extra))

    root_ok = isinstance(schema, dict) and schema.get('type') == 'object' and 'anyOf' not in schema
    add('', 'ROOT_OBJECT_NO_ANYOF', SUPPORTED if root_ok else CONFLICT,
        {k: schema.get(k) for k in ('type', 'anyOf')} if isinstance(schema, dict) else schema,
        'Guide: root object requirement.')
    properties_total = enum_total = string_chars = 0
    container_depth = 0
    refs = []
    for pointer, node in node_items:
        if isinstance(node, bool):
            if not pointer.endswith(('/additionalProperties', '/additionalItems')):
                add(pointer, 'BOOLEAN_SCHEMA', UNKNOWN, node,
                    'Boolean-schema backend form is not established by the cited examples.')
            continue
        if not isinstance(node, dict):
            continue
        # A syntactic container upper bound, with no $ref expansion.  It is not
        # a claim that the backend counts depth with this exact algorithm.
        ancestors = [n for p, n in node_items if isinstance(n, dict)
                     and (p == '' or pointer.startswith(p + '/'))]
        depth = sum(_is_container(n) for n in ancestors)
        if _is_container(node):
            depth += 1
        # The root is already an ancestor of itself only when pointer == ''.
        if pointer == '' and _is_container(node):
            depth -= 1
        container_depth = max(container_depth, depth)
        if not node:
            add(pointer, 'UNCONSTRAINED_SCHEMA', UNKNOWN, node,
                'No documented form establishes this unconstrained node.')
        if 'type' in node:
            type_value = node['type']
            supported_type = (isinstance(type_value, str) and type_value in _BASE_TYPES)
            if isinstance(type_value, list):
                supported_type = (len(type_value) == 2 and 'null' in type_value
                                  and all(t in _BASE_TYPES for t in type_value))
            add(pointer + '/type', 'TYPE_FORM', SUPPORTED if supported_type else UNKNOWN,
                type_value, 'Guide: named types and nullable union examples.')
        if _contains_type(node, 'object') or 'properties' in node:
            props = node.get('properties', {})
            required = node.get('required', [])
            property_names = list(props) if isinstance(props, dict) else []
            properties_total += len(property_names)
            string_chars += sum(len(s) for s in property_names)
            required_ok = (isinstance(required, list) and set(required) == set(property_names))
            add(pointer + '/required', 'ALL_PROPERTIES_REQUIRED',
                SUPPORTED if required_ok else CONFLICT, required,
                'Guide: required fields, including nullable fields.',
                property_names=property_names)
            add(pointer + '/additionalProperties', 'CLOSED_OBJECT',
                SUPPORTED if node.get('additionalProperties') is False else CONFLICT,
                node.get('additionalProperties'), 'Guide: additionalProperties=false.')
        if 'const' in node:
            value = node['const']
            if isinstance(value, str):
                string_chars += len(value)
            status = UNKNOWN
            rule = 'CONST_FORM_UNCONFIRMED'
            basis = 'Guide mentions const string budget but does not fully specify accepted const forms.'
            if 'type' not in node and pointer == observed_const_pointer:
                status, rule = CONFLICT, 'OBSERVED_CONST_MISSING_TYPE'
                basis = 'Prior pinned-CLI pilot error explicitly names this missing type; no new request.'
            elif 'type' not in node:
                rule = 'CONST_MISSING_TYPE_ANALOGUE'
                basis = 'Same const-only shape as prior error; this path was not separately evaluated by the service.'
            add(pointer, rule, status, {k: node[k] for k in ('const', 'type') if k in node}, basis)
        if 'enum' in node:
            values = node['enum']
            if isinstance(values, list):
                enum_total += len(values)
                string_chars += sum(len(v) for v in values if isinstance(v, str))
                enum_chars = sum(len(v) for v in values if isinstance(v, str))
                limit_ok = len(values) <= 250 or enum_chars <= 15000
                add(pointer + '/enum', 'SINGLE_ENUM_STRING_LIMIT', SUPPORTED if limit_ok else CONFLICT,
                    dict(count=len(values), string_characters=enum_chars, large_enum_limit=15000),
                    'Guide: large string enum limit.')
            add(pointer + '/enum', 'ENUM_EXPLICIT_TYPE', SUPPORTED if 'type' in node else UNKNOWN,
                {k: node[k] for k in ('enum', 'type') if k in node},
                'Guide supports enum; illustrated explicit-type form does not establish enum-only acceptance.')
        if '$ref' in node:
            target, reason = _local_target(schema, node['$ref'], node_map)
            refs.append(dict(pointer=pointer, reference=node['$ref'], target=target))
            add(pointer + '/$ref', 'REFERENCE', UNKNOWN if reason else SUPPORTED, node['$ref'],
                reason or 'Guide illustrates local definitions and recursive references.',
                target_pointer=target)
        if 'definitions' in node:
            string_chars += sum(len(k) for k in node['definitions'])
            add(pointer + '/definitions', 'LEGACY_DEFINITIONS_SPELLING', UNKNOWN,
                list(node['definitions']), 'Guide examples use $defs; Draft7 spelling is not deployment proof.')
        if '$defs' in node:
            string_chars += sum(len(k) for k in node['$defs'])
            add(pointer + '/$defs', 'LOCAL_DEFINITIONS', SUPPORTED,
                list(node['$defs']), 'Guide: local definitions example.')
        for keyword, value in node.items():
            key_pointer = pointer + '/' + _esc(keyword)
            if keyword in _UNSUPPORTED:
                add(key_pointer, 'UNSUPPORTED_COMPOSITION', CONFLICT, value,
                    'Guide: explicitly unsupported composition keyword.')
            elif keyword == 'anyOf':
                add(key_pointer, 'ANYOF_BRANCHES_REVIEWED_SEPARATELY', SUPPORTED, len(value),
                    'Guide: nested anyOf; every branch must also satisfy the subset.')
            elif keyword in _GENERAL_CONSTRAINTS:
                status = CONFLICT if fine_tuned is True and keyword in _FINE_TUNED_CONFLICTS else SUPPORTED
                if keyword == 'format' and value not in _FORMATS:
                    status = UNKNOWN
                add(key_pointer, 'DOCUMENTED_TYPED_CONSTRAINT', status, value,
                    'Guide: general-model typed constraint; fine-tuned exclusions remain conditional.',
                    fine_tuned=fine_tuned)
            elif keyword in _ANNOTATIONS_UNCONFIRMED:
                add(key_pointer, 'ANNOTATION_OR_DIALECT_FORM', UNKNOWN, value,
                    'No deployment-specific acceptance established for this annotation or dialect marker.')
            elif keyword in ('minLength', 'maxLength', 'patternProperties'):
                add(key_pointer, 'MODEL_SPECIFIC_KEYWORD', CONFLICT if fine_tuned is True else UNKNOWN,
                    value, 'Guide explicitly excludes this for fine-tuned models; general support is not explicitly established.',
                    fine_tuned=fine_tuned)
            elif keyword not in _KNOWN:
                add(key_pointer, 'UNDOCUMENTED_KEYWORD', UNKNOWN, value,
                    'Keyword is outside this cited documented inventory; no automatic removal.')
            elif keyword == 'items' and isinstance(value, list):
                add(key_pointer, 'TUPLE_ITEMS_FORM', UNKNOWN, value,
                    'Tuple-form items backend support is not established by guide examples.')
    for rule, value, limit in [('TOTAL_PROPERTIES', properties_total, 5000),
                               ('TOTAL_ENUM_VALUES', enum_total, 1000),
                               ('TRACKED_STRING_CHARACTERS', string_chars, 120000)]:
        add('', rule, SUPPORTED if value <= limit else CONFLICT,
            dict(observed=value, limit=limit), 'Guide: published structural limit.')
    add('', 'PUBLISHED_NESTING_LIMIT', SUPPORTED, 10, 'Guide: published nesting limit.')
    add('', 'SYNTACTIC_CONTAINER_DEPTH', UNKNOWN,
        dict(object_array_path_bound=container_depth, published_limit=10, refs_expanded=False),
        'Exact backend counter is not documented; this is only a conservative syntactic object/array path inventory.')
    add('', 'PINNED_CLI_BACKEND_SUBSET', UNKNOWN, {'cli_version': '0.164.0'},
        'RPC outputSchema generic JSON shape only establishes a client parameter, not backend schema acceptance.')
    add('', 'DEPLOYMENT_MODEL_AND_ACCOUNT', UNKNOWN, {'requested_alias': 'gpt-6.1-sol'},
        'No generation request is sent; model snapshot, entitlement and deployment subset are unverified.')
    assert schema == original, 'Read-only inventory unexpectedly changed schema.'
    counts = Counter(row['status'] for row in checks)
    return dict(review_version=REVIEW_VERSION, reviewed_on=REVIEWED_ON,
                official_guide=GUIDE, model_family_fine_tuned=fine_tuned,
                scope='Documented general API subset plus prior observed failure; not a service capability test.',
                offline_review_completed=True, service_acceptance_established=False,
                automatic_transforms_applied=0, checks=checks,
                status_counts={status: counts[status] for status in (SUPPORTED, CONFLICT, UNKNOWN)},
                metrics=dict(schema_nodes=len(node_items), total_properties=properties_total,
                             total_enum_values=enum_total, tracked_string_characters=string_chars,
                             syntactic_container_depth=container_depth, references=refs),
                limitations=['Unconfirmed nodes remain unchanged and visible.',
                             'General API documentation does not certify the Codex backend deployment.',
                             'No runtime acceptance rule is changed by this inventory.'])
