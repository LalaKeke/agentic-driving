"""Controlled official OpenAI Responses transport, separate from legacy adapters.

One ``decide`` sends at most one HTTP request. The caller owns its experiment
budget and must debit it before calling; native text is never repaired. A
provided client is explicitly a mock transport, never an online-model source.
"""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx
from PIL import Image

from .contracts import ROOT, Failure, digest, need, planning_contract, validate
from .evidence_io import reserve_directory, write_json_new, write_text_new
from .wire_schema_v2 import load_versioned_wire_bundle, WireSchemaConflict

CONTRACT = '3.0.0-exp2'
SCHEMA = 'agent_output_v3_exp2.schema.json'
OFFICIAL_BASE = 'https://api.openai.com/v1'
PROTOTYPE_PROFILE = 'local_conditional_2s_prototype_v1'


def load_api_key():
    """Read only the configured private credential; never return it in diagnostics."""
    value = os.environ.get('DRIVING_API_KEY', '').strip()
    if not value and os.environ.get('DRIVING_API_KEY_FILE'):
        try:
            value = Path(os.environ['DRIVING_API_KEY_FILE']).expanduser().read_text().strip()
        except (OSError, UnicodeError):
            raise Failure('API_CONFIGURATION', 'Cannot read the configured private API-key file') from None
    need(bool(value) and not any(c.isspace() for c in value)
         and not value.startswith(('{', '[', 'export ', 'DRIVING_API_KEY=')),
         'API_CONFIGURATION', 'Configure one plain API key through DRIVING_API_KEY or DRIVING_API_KEY_FILE')
    return value


def _connection():
    proxy = os.environ.get('DRIVING_PROXY_URL') or None
    if proxy:
        try:
            parsed = urlsplit(proxy)
            valid = (parsed.scheme in ('http', 'https') and parsed.hostname and parsed.port
                     and not parsed.username and not parsed.password
                     and parsed.path in ('', '/') and not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        need(valid, 'API_CONFIGURATION', 'DRIVING_PROXY_URL must contain only HTTP(S) host and port')
    ca = os.environ.get('DRIVING_CA_BUNDLE') or None
    need(not ca or Path(ca).is_file(), 'API_CONFIGURATION', 'Configured CA bundle does not exist')
    return proxy, ca


class OpenAIResponsesAgent:
    def __init__(self, *, evidence_dir, model=None, timeout_s=None,
                 wire_schema_bundle, prompt_version='driving_v3_exp2',
                 experiment_profile=None, max_calls=2, expected_models=None,
                 client=None):
        self.base = os.environ.get('DRIVING_API_BASE', OFFICIAL_BASE).rstrip('/')
        need(self.base == OFFICIAL_BASE, 'API_CONFIGURATION',
             'This transport sends credentials only to the official OpenAI HTTPS /v1 endpoint')
        self.model = model or os.environ.get('DRIVING_MODEL', '')
        self.timeout = float(timeout_s if timeout_s is not None else os.environ.get('DRIVING_TIMEOUT_S', '600'))
        need(isinstance(self.model, str) and bool(self.model) and 1 <= self.timeout <= 600
             and isinstance(max_calls, int) and not isinstance(max_calls, bool) and max_calls > 0,
             'API_CONFIGURATION', 'Explicit model, positive call budget and timeout in 1..600 seconds required')
        prototype = experiment_profile == PROTOTYPE_PROFILE
        need((prompt_version == 'driving_v3_exp2' and experiment_profile is None)
             or (prototype and prompt_version in ('driving_prototype_v1', 'driving_prototype_v2', 'driving_prototype_v3')),
             'API_CONFIGURATION', 'Prompt and explicit experiment profile are incompatible')
        self.prompt_version, self.experiment_profile = prompt_version, experiment_profile
        self.max_calls, self.call_count = max_calls, 0
        self.expected_models = tuple(expected_models) if expected_models is not None else (self.model,)
        need(self.expected_models and all(isinstance(x, str) and x for x in self.expected_models),
             'API_CONFIGURATION', 'Model identities must be explicitly registered strings')
        self.wire_schema_bundle = Path(wire_schema_bundle)
        _, self.schema_transport = self._transport_schema()
        self.key = load_api_key()
        proxy, ca = _connection()
        self.source = 'online_model' if client is None else 'mock_transport'
        self.client = client or httpx.Client(timeout=self.timeout, follow_redirects=False,
            trust_env=False, transport=httpx.HTTPTransport(retries=0, proxy=proxy,
                                                          verify=ca or True, trust_env=False))
        self.evidence_dir = reserve_directory(evidence_dir)
        self.preflight_ok = False
        self.last_attempt_summary = None
        self.public_config = dict(backend='official_openai_responses', api_base=self.base,
            model=self.model, expected_response_models=list(self.expected_models),
            model_identity_policy='exact_registered_ids', authentication='api_key',
            timeout_s=self.timeout, adapter_retries=0, request_max_retries=0, stream=False,
            follow_redirects=False, trust_env=False, tool_access=False, tool_calls=0,
            model_substitution=False, prompt_version=self.prompt_version,
            agent_output_contract=CONTRACT, experiment_profile=self.experiment_profile,
            max_calls=self.max_calls, source=self.source, reasoning_effort='medium',
            store=False, schema_transport=self.schema_transport,
            connection_profile=dict(proxy_url=proxy, ca_bundle=ca,
                                    certificate_verification_disabled=False))

    def _transport_schema(self):
        try:
            schema, manifest = load_versioned_wire_bundle(self.wire_schema_bundle, ROOT / 'schemas' / SCHEMA)
        except WireSchemaConflict as error:
            raise Failure('WIRE_SCHEMA_INVALID', 'Wire bundle differs from the original business Schema',
                          conflicts=error.conflicts) from None
        return schema, dict(business_schema_sha256=manifest['source_schema_sha256'],
            wire_schema_sha256=manifest['wire_schema_sha256'],
            wire_schema_version=manifest['wire_schema_version'],
            generator_version=manifest['generator_version'],
            transformation_manifest_sha256=hashlib.sha256(
                (self.wire_schema_bundle / 'transformation_manifest.json').read_bytes()).hexdigest(),
            business_validation=SCHEMA, response_repair=False)

    def preflight(self):
        """Local configuration and pin check only; sends no HTTP/model request."""
        need(not self.preflight_ok, 'API_PREFLIGHT', 'Preflight already recorded')
        _, current = self._transport_schema()
        need(current == self.schema_transport, 'WIRE_SCHEMA_CHANGED', 'Wire changed before preflight')
        write_json_new(self.evidence_dir / 'public_config.json', self.public_config)
        self.preflight_ok = True
        return dict(ok=True, model_inference_calls=0, network_requests=0,
                    backend='official_openai_responses', source=self.source)

    def _safe_error(self, response):
        result = dict(http_status=response.status_code)
        try:
            error = response.json().get('error') or {}
            for key in ('type', 'code', 'param', 'message'):
                value = error.get(key)
                if isinstance(value, str):
                    value = value.replace(self.key, '[REDACTED]')
                    value = re.sub(r'(?i)(?:bearer\s+\S+|sk-[A-Za-z0-9_-]+)', '[REDACTED]', value)
                    result[key] = value[:2000]
        except (ValueError, TypeError, AttributeError):
            pass
        return result

    @staticmethod
    def _native_text(data):
        need(isinstance(data, dict), 'API_RESPONSE', 'Malformed Responses object')
        need(data.get('status') == 'completed' and not data.get('error') and not data.get('incomplete_details'),
             'API_INCOMPLETE', 'Refused, failed or incomplete response; no retry')
        output = data.get('output')
        need(isinstance(output, list), 'API_RESPONSE', 'Missing Responses output list')
        messages = []
        for item in output:
            need(isinstance(item, dict) and item.get('type') in ('message', 'reasoning'),
                 'API_TOOL_USE', 'Tool or unknown output item is prohibited')
            if item.get('type') == 'message':
                need(item.get('status') == 'completed' and item.get('role') == 'assistant',
                     'API_INCOMPLETE', 'Incomplete or non-assistant native message')
                messages.append(item)
        need(len(messages) == 1, 'API_RESPONSE', 'Expected one completed native assistant message')
        parts = messages[0].get('content')
        need(isinstance(parts, list) and bool(parts), 'API_RESPONSE', 'Missing native content')
        texts = []
        for part in parts:
            need(isinstance(part, dict) and part.get('type') != 'refusal', 'API_REFUSAL', 'Model refused the request')
            need(part.get('type') == 'output_text' and isinstance(part.get('text'), str),
                 'API_RESPONSE', 'Unexpected native content type')
            texts.append(part['text'])
        text = ''.join(texts)
        need(bool(text), 'API_RESPONSE', 'Empty native final text')
        return text

    def decide(self, capture, feedback=None):
        need(self.preflight_ok, 'API_PREFLIGHT_REQUIRED', 'Call local preflight first')
        need(self.call_count < self.max_calls, 'API_BUDGET', 'Explicit transport budget exhausted')
        validate('capture_v2.schema.json', capture)
        schema, current = self._transport_schema()
        need(current == self.schema_transport, 'WIRE_SCHEMA_CHANGED', 'Pinned wire changed; no request sent')
        self.call_count += 1
        folder = reserve_directory(self.evidence_dir / f'attempt_{self.call_count:02d}')
        start = time.monotonic()
        self.last_attempt_summary = dict(attempt=self.call_count, source=self.source,
            transport='official_openai_responses', status='STARTED', adapter_retries=0,
            http_requests=0, api_post_requests=0,
            model_service_returned='UNKNOWN', native_json_returned=False)
        try:
            safe = deepcopy(capture)
            prompt = (ROOT / 'prompts' / (self.prompt_version + '.txt')).read_text()
            hashes, images = {}, []
            for number, (original, metadata) in enumerate(zip(capture['observations'], safe['observations'])):
                image_path = Path(original['path'])
                data = image_path.read_bytes()
                need(0 < len(data) <= 20 * 1024 * 1024, 'IMAGE_INPUT', 'Image size invalid')
                with Image.open(image_path) as image:
                    need(image.format in ('JPEG', 'PNG', 'WEBP'), 'IMAGE_INPUT', 'Unsupported image type')
                    mime, suffix = {'JPEG': ('image/jpeg', '.jpg'), 'PNG': ('image/png', '.png'),
                                    'WEBP': ('image/webp', '.webp')}[image.format]
                    image.verify()
                hashes[original['id']] = hashlib.sha256(data).hexdigest()
                metadata['path'] = f'attached:frame_{number:03d}{suffix}'
                images.append(dict(type='input_image', image_url=f'data:{mime};base64,' +
                                   base64.b64encode(data).decode('ascii'), detail='auto'))
            contract = planning_contract(capture, CONTRACT)
            if self.experiment_profile:
                contract['experiment_profile'] = self.experiment_profile
                contract['scope_interpretation'] = 'Coverage is conditional on the MODEL-declared finite local domain and adopted two-second motion conditions; not exhaustive whole-world futures. All declared obstacles and local unknowns retain the same checks; tools never delete, forecast or fill them.'
            if self.prompt_version in ('driving_prototype_v2', 'driving_prototype_v3'):
                contract['planner_profile'] = 'bounded_seed_candidates_v2'
            text = json.dumps(dict(capture=safe, feedback=feedback, tool_contract=contract), ensure_ascii=False)
            write_text_new(folder / 'model_input.txt', prompt + '\n' + text)
            prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
            write_json_new(folder / 'input_manifest.json', dict(image_sha256=hashes,
                prompt_version=self.prompt_version, prompt_sha256=prompt_sha,
                output_contract=CONTRACT, schema_sha256=current['business_schema_sha256'],
                schema_transport=current, instructions=prompt,
                user_text_sha256=hashlib.sha256(text.encode()).hexdigest()))
            body = dict(model=self.model, instructions=prompt,
                input=[dict(role='user', content=[dict(type='input_text', text=text)] + images)],
                text=dict(format=dict(type='json_schema', name='driving_v3_exp2', strict=True, schema=schema)),
                tools=[], reasoning=dict(effort='medium'), stream=False, store=False)
            self.last_attempt_summary['http_requests'] = 1
            self.last_attempt_summary['api_post_requests'] = 1
            try:
                response = self.client.post(self.base + '/responses',
                    headers={'Authorization': 'Bearer ' + self.key}, json=body, timeout=self.timeout)
            except (httpx.TimeoutException, httpx.TransportError) as error:
                raise Failure('API_TRANSPORT', 'OpenAI connection failed; no retry; raw exception suppressed',
                              exception_type=type(error).__name__) from None
            self.last_attempt_summary.update(http_status=response.status_code,
                request_id=response.headers.get('x-request-id'))
            need(response.status_code == 200, 'API_HTTP', 'Official API rejected the request; no retry',
                 diagnostic=self._safe_error(response))
            try:
                data = response.json()
            except ValueError:
                raise Failure('API_RESPONSE', 'API returned non-JSON response metadata') from None
            need(isinstance(data, dict), 'API_RESPONSE', 'Malformed Responses metadata')
            metadata = dict(response_id=data.get('id'), response_model=data.get('model'),
                status=data.get('status'), usage=data.get('usage'),
                http_status=response.status_code, request_id=response.headers.get('x-request-id'),
                output_item_types=[item.get('type') if isinstance(item, dict) else None
                                   for item in data.get('output', [])] if isinstance(data.get('output'), list) else None)
            write_json_new(folder / 'response_metadata.json', metadata)
            self.last_attempt_summary.update(response_model=data.get('model'),
                server_reported_model=data.get('model'), server_reported_snapshot=None,
                usage=data.get('usage'))
            # Preserve any returned final/partial native text before protocol,
            # identity or business validation, without repairing its contents.
            observed_output = data.get('output') if isinstance(data.get('output'), list) else []
            observed_text = ''.join(part['text'] for item in observed_output
                if isinstance(item, dict) and isinstance(item.get('content'), list)
                for part in item['content'] if isinstance(part, dict) and isinstance(part.get('text'), str))
            if observed_text:
                write_text_new(folder / 'final_agent_message.txt', observed_text)
                self.last_attempt_summary['model_service_returned'] = True
            native_text = self._native_text(data)
            need(data.get('model') in self.expected_models, 'MODEL_SUBSTITUTION',
                 'Response model differs from explicitly registered identities',
                 requested=self.model, actual=data.get('model'), expected=list(self.expected_models))
            try:
                def reject_constant(value):
                    raise ValueError(value)
                def unique_keys(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError('Duplicate JSON member')
                        result[key] = value
                    return result
                output = json.loads(native_text, parse_constant=reject_constant, object_pairs_hook=unique_keys)
            except ValueError:
                raise Failure('API_RESPONSE', 'Native final text is not unambiguous finite JSON; preserved unchanged') from None
            write_json_new(folder / 'native_output.json', output)
            self.last_attempt_summary['native_json_returned'] = True
            provenance = dict(source=self.source, transport='official_openai_responses',
                authentication='api_key', requested_model=self.model, model_id=data['model'],
                server_reported_model=data['model'], server_reported_snapshot=None,
                model_identity_basis='Responses response.model exact registered ID; independent snapshot authentication unavailable',
                response_id=data.get('id'), request_id=response.headers.get('x-request-id'), usage=data.get('usage'),
                output_digest=digest(output), image_sha256=hashes, prompt_version=self.prompt_version,
                agent_output_contract=CONTRACT, prompt_sha256=prompt_sha,
                attempts=1, adapter_retries=0, request_max_retries=0, tool_calls=0,
                schema_transport=current, elapsed_s=time.monotonic() - start)
            if self.experiment_profile:
                provenance['experiment_profile'] = self.experiment_profile
            write_json_new(folder / 'provenance.json', provenance)
            validate(SCHEMA, output)
            self.last_attempt_summary.update(status='NATIVE_SCHEMA_VALID', structured_output=True)
            return output, provenance
        except Failure as error:
            self.last_attempt_summary.update(status='FAILED', failure=error.record)
            raise
        except (OSError, ValueError) as error:
            failure = Failure('API_INPUT', 'Local input or configuration failed; raw exception suppressed',
                              exception_type=type(error).__name__)
            self.last_attempt_summary.update(status='FAILED', failure=failure.record)
            raise failure from None
        finally:
            self.last_attempt_summary['elapsed_s'] = time.monotonic() - start
            write_json_new(folder / 'attempt_summary.json', self.last_attempt_summary)
