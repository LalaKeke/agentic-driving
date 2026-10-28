"""Single-turn official ChatGPT transport for the explicitly budgeted exp2 pilot.

This is separate from the historical adapter. A caller must persist its budget
debit BEFORE calling ``decide``. No inference compatibility probe is performed.
The CLI-resolved alias is evidence from thread/start, not a server snapshot ID.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from PIL import Image

from .codex_adapter import DISABLED_FEATURES, subscription_environment
from .contracts import ROOT, Failure, digest, need, planning_contract, validate
from .evidence_io import reserve_directory, write_json_new, write_text_new
from .wire_schema_v2 import load_versioned_wire_bundle as load_wire_bundle, WireSchemaConflict

PROVIDER = 'pilot-official'
OFFICIAL_BASE = 'https://chatgpt.com/backend-api/codex'
CONTRACT = '3.0.0-exp2'
PROMPT = 'driving_v3_exp2'
SCHEMA = 'agent_output_v3_exp2.schema.json'
SENSITIVE_KEY = re.compile(r'(?:authorization|access.?token|refresh.?token|id.?token|api.?key|password|cookie|secret|email|account.?id)', re.I)
SENSITIVE_TEXT = re.compile(r'(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]+|sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)')


def redact(value):
    """Public diagnostics only. Exact wire streams remain in an ignored 0700 dir."""
    if isinstance(value, dict):
        return {k: '[REDACTED]' if SENSITIVE_KEY.search(k) else redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return SENSITIVE_TEXT.sub('[REDACTED]', value)
    return value


def public_event(event):
    """Account and remote-control metadata are irrelevant to image inference."""
    method = event.get('method', '')
    if method.startswith(('account/', 'remoteControl/')):
        return dict(method=method, params_private=True,
                    params_sha256=hashlib.sha256(json.dumps(event.get('params'), sort_keys=True).encode()).hexdigest())
    return redact(event)


def provider_config():
    result = {
        'model_provider': PROVIDER,
        'forced_login_method': 'chatgpt',
        'model_reasoning_effort': 'medium',
        'web_search': 'disabled',
        'orchestrator.mcp.enabled': False,
        'skills.include_instructions': False,
        'skills.bundled.enabled': False,
        'mcp_servers': {},
        f'model_providers.{PROVIDER}.name': 'Official ChatGPT controlled pilot',
        f'model_providers.{PROVIDER}.base_url': OFFICIAL_BASE,
        f'model_providers.{PROVIDER}.wire_api': 'responses',
        f'model_providers.{PROVIDER}.requires_openai_auth': True,
        f'model_providers.{PROVIDER}.supports_websockets': False,
        f'model_providers.{PROVIDER}.request_max_retries': 0,
        f'model_providers.{PROVIDER}.stream_max_retries': 0,
    }
    result.update({f'features.{key}': False for key in DISABLED_FEATURES})
    return result


def config_arguments():
    args = []
    for key, value in provider_config().items():
        # JSON booleans/strings/empty objects are also valid TOML value forms.
        args += ['-c', key + '=' + json.dumps(value)]
    return args


class PilotProtocol:
    """Validate the app-server transcript independently of subprocess I/O."""
    def __init__(self, model, provider=PROVIDER):
        self.model = model
        self.provider = provider
        self.resolved_model = None
        self.thread_id = None
        self.turn_id = None
        self.turn_started = 0
        self.completed = False
        self.messages = {}
        self.message_order = []
        self.partial_deltas = {}
        self.error_notification = False
        self.notification_methods = []

    def thread_response(self, result):
        self.resolved_model = result.get('model')
        need(self.resolved_model == self.model, 'MODEL_SUBSTITUTION',
             'CLI resolved alias differs from requested model',
             requested=self.model, resolved=self.resolved_model)
        need(result.get('modelProvider') == self.provider, 'MODEL_PROVIDER_MISMATCH',
             'CLI resolved a different provider')
        need(not result.get('instructionSources'), 'UNEXPECTED_INSTRUCTIONS',
             'Isolated image task loaded instruction files')
        self.thread_id = result.get('thread', {}).get('id')
        need(isinstance(self.thread_id, str) and self.thread_id,
             'CODEX_PROTOCOL', 'Missing thread identity')

    def _turn(self, turn_id):
        need(isinstance(turn_id, str) and turn_id, 'CODEX_PROTOCOL', 'Missing turn identity')
        if self.turn_id is None:
            self.turn_id = turn_id
        need(turn_id == self.turn_id, 'EXTRA_MODEL_TURN',
             'An additional model turn is prohibited')

    def _item(self, item):
        kind = item.get('type')
        need(kind in ('userMessage', 'agentMessage', 'reasoning'),
             'CODEX_TOOL_USE', 'Tool or other non-text activity invalidates pilot', item_type=kind)
        if kind == 'agentMessage':
            item_id = item.get('id')
            need(isinstance(item_id, str), 'CODEX_PROTOCOL', 'Missing agent message identity')
            if item_id not in self.messages:
                self.message_order.append(item_id)
            self.messages[item_id] = dict(text=item.get('text', ''), phase=item.get('phase'))

    def observe(self, event):
        method = event.get('method')
        if not method:
            return
        self.notification_methods.append(method)
        need('id' not in event, 'CODEX_TOOL_REQUEST',
             'Server initiated a request; inference-only pilot cannot answer tools')
        params = event.get('params') or {}
        need(method not in ('model/rerouted', 'modelProvider/authRecoveryStarted',
                            'modelProvider/authRecoveryCompleted'),
             'MODEL_REROUTE_OR_RECOVERY', 'Model rerouting or automatic auth recovery is prohibited',
             event_method=method)
        if params.get('threadId') is not None and self.thread_id is not None:
            need(params['threadId'] == self.thread_id, 'EXTRA_MODEL_THREAD',
                 'Notification belongs to another thread')
        if params.get('turnId') is not None:
            self._turn(params['turnId'])
        if method == 'error':
            self.error_notification = True
            raise Failure('CODEX_SERVICE_FAILURE', 'Official service reported an error; no retry',
                          diagnostic=redact(params))
        need(not params.get('willRetry'), 'CODEX_RETRY_PROHIBITED', 'Automatic retry prohibited')
        if method == 'turn/started':
            self.turn_started += 1
            need(self.turn_started == 1, 'EXTRA_MODEL_TURN', 'Second turn/started prohibited')
            self._turn(params.get('turn', {}).get('id'))
        if method in ('item/started', 'item/completed'):
            self._item(params.get('item') or {})
        if method == 'item/agentMessage/delta':
            item_id, delta = params.get('itemId'), params.get('delta')
            need(isinstance(item_id, str) and isinstance(delta, str),
                 'CODEX_PROTOCOL', 'Malformed agent message delta')
            self.partial_deltas.setdefault(item_id, []).append(delta)
        if method == 'turn/completed':
            turn = params.get('turn') or {}
            self._turn(turn.get('id'))
            for item in turn.get('items', []):
                self._item(item)
            need(turn.get('status') == 'completed' and not turn.get('error'),
                 'CODEX_SERVICE_FAILURE', 'Turn failed or was interrupted; no retry',
                 diagnostic=redact(turn.get('error')))
            need(not self.completed, 'EXTRA_MODEL_TURN', 'Duplicate completed turn prohibited')
            self.completed = True

    def final_text(self):
        need(self.completed and self.message_order, 'CODEX_INCOMPLETE',
             'No completed native model response')
        final_ids = [i for i in self.message_order if self.messages[i]['phase'] == 'final_answer']
        return self.messages[(final_ids or self.message_order)[-1]]['text']


class PilotSubscriptionAgent:
    def __init__(self, *, evidence_dir, model='gpt-6.1-sol', timeout_s=600,
                 binary=None, expected_cli_version='0.164.0', process_factory=None,
                 wire_schema_bundle=None):
        self.model = model
        self.timeout = float(timeout_s)
        need(model and 1 <= self.timeout <= 600, 'PILOT_CONFIGURATION',
             'Explicit model and timeout in 1..600 seconds required')
        default = ROOT.parent / 'runs/tools/codex-0.164.0/node_modules/.bin/codex'
        self.binary = str(binary or default)
        need(shutil.which(self.binary), 'CODEX_MISSING', 'Official project CLI is required')
        self.expected_version = expected_cli_version
        # Explicit opt-in only. Legacy trials continue sending their original
        # business schema. Validate a new bundle before creating any evidence.
        self.wire_schema_bundle = Path(wire_schema_bundle) if wire_schema_bundle is not None else None
        _, self.schema_transport = self._transport_schema()
        self.evidence_dir = reserve_directory(evidence_dir)
        self.private_state = reserve_directory(self.evidence_dir / '.private_state')
        self.private_state.chmod(0o700)
        write_text_new(self.private_state / '.gitignore', '*\n!.gitignore\n')
        self.sqlite_home = reserve_directory(self.private_state / 'sqlite')
        self.log_dir = reserve_directory(self.private_state / 'logs')
        self.sqlite_home.chmod(0o700)
        self.log_dir.chmod(0o700)
        self.process_factory = process_factory or subprocess.Popen
        self.source = 'online_model' if process_factory is None else 'mock_transport'
        self.call_count = 0
        self.preflight_ok = False
        self.last_attempt_summary = None
        self.public_config = dict(backend='official_codex_app_server', model=model,
            authentication='chatgpt_subscription', timeout_s=self.timeout,
            adapter_retries=0, request_max_retries=0, stream_max_retries=0,
            websocket_enabled=False, tool_access=False, model_substitution=False,
            prompt_version=PROMPT, agent_output_contract=CONTRACT,
            provider=PROVIDER, api_base=OFFICIAL_BASE,
            model_identity_basis='thread/start CLI-resolved alias; server snapshot unexposed')
        if self.schema_transport is not None:
            self.public_config['schema_transport'] = self.schema_transport

    def _transport_schema(self):
        business_path = ROOT / 'schemas' / SCHEMA
        if self.wire_schema_bundle is None:
            return json.loads(business_path.read_text()), None
        try:
            schema, manifest = load_wire_bundle(self.wire_schema_bundle, business_path)
        except WireSchemaConflict as error:
            raise Failure('WIRE_SCHEMA_INVALID', 'Wire bundle does not match the original business schema; no request sent',
                          conflicts=error.conflicts) from None
        return schema, dict(
            business_schema_sha256=manifest['source_schema_sha256'],
            wire_schema_sha256=manifest['wire_schema_sha256'],
            wire_schema_version=manifest['wire_schema_version'],
            generator_version=manifest['generator_version'],
            transformation_manifest_sha256=hashlib.sha256(
                (self.wire_schema_bundle / 'transformation_manifest.json').read_bytes()).hexdigest(),
            business_validation=SCHEMA, response_repair=False,
            backend_acceptance='NOT_ESTABLISHED_BY_OFFLINE_REVIEW')

    def command(self, *, startup_only=False):
        args = [self.binary, 'app-server', '--strict-config']
        args += ['--print-startup-config'] if startup_only else ['--stdio']
        return args + config_arguments() + [
            '-c', 'sqlite_home=' + json.dumps(str(self.sqlite_home)),
            '-c', 'log_dir=' + json.dumps(str(self.log_dir))]

    def preflight(self):
        """Local auth/version/configuration only; never sends initialize or a turn."""
        records = []
        private = reserve_directory(self.private_state / 'preflight')
        private.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix='driving-pilot-preflight-') as cwd:
            for label, command in (
                    ('version', [self.binary, '--version']),
                    ('login_status', [self.binary, 'login', 'status']),
                    ('startup_config', self.command(startup_only=True))):
                process = subprocess.run(command, cwd=cwd, env=subscription_environment(),
                                         capture_output=True, text=True, timeout=20)
                for name, text in (('stdout', process.stdout), ('stderr', process.stderr)):
                    target = private / (label + '.' + name)
                    with target.open('xb') as stream:
                        stream.write(text.encode())
                    target.chmod(0o600)
                records.append(dict(label=label, command=command, returncode=process.returncode,
                    stdout_sha256=hashlib.sha256(process.stdout.encode()).hexdigest(),
                    stderr_sha256=hashlib.sha256(process.stderr.encode()).hexdigest(),
                    raw_streams_private=True,
                    declared_provider_config=provider_config() if label == 'startup_config' else None))
                write_json_new(self.evidence_dir / (label + '.json'), records[-1])
                need(process.returncode == 0, 'CODEX_STARTUP', 'Zero-inference preflight failed', stage=label)
                if label == 'version':
                    self.version = process.stdout.strip()
                    need(self.version == 'codex-cli ' + self.expected_version,
                         'CODEX_VERSION_MISMATCH', 'CLI version differs from frozen pilot')
                if label == 'login_status':
                    need('Logged in using ChatGPT' in process.stdout + process.stderr,
                         'CODEX_CHATGPT_LOGIN_REQUIRED', 'Existing ChatGPT login required')
        self.preflight_ok = True
        self.public_config['cli_version'] = self.version
        write_json_new(self.evidence_dir / 'public_config.json', self.public_config)
        return dict(ok=True, model_inference_calls=0, checks=records)

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)

    def _rpc(self, work, input_items, schema, folder):
        protocol = PilotProtocol(self.model)
        process = self.process_factory(self.command(), cwd=work, env=subscription_environment(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
        events = queue.Queue()
        raw_out, raw_err, public_events = [], [], []
        private = reserve_directory(folder / '.private')
        private.chmod(0o700)
        write_text_new(private / '.gitignore', '*\n!.gitignore\n')

        def output_reader():
            for line in iter(process.stdout.readline, b''):
                raw_out.append(line)
                events.put(line)
            events.put(None)

        def error_reader():
            for chunk in iter(lambda: process.stderr.read(4096), b''):
                raw_err.append(chunk)

        readers = [threading.Thread(target=output_reader, daemon=True),
                   threading.Thread(target=error_reader, daemon=True)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + self.timeout
        sent = []

        def send(method, params, rpc_id=None):
            message = dict(method=method, params=params)
            if rpc_id is not None:
                message['id'] = rpc_id
            sent.append(message)
            process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode())
            process.stdin.flush()

        def receive(until_id=None, until_completed=False):
            if until_completed and protocol.completed:
                return
            while True:
                remaining = deadline - time.monotonic()
                need(remaining > 0, 'CODEX_TIMEOUT', 'Single model call timed out; no retry')
                try:
                    line = events.get(timeout=remaining)
                except queue.Empty:
                    raise Failure('CODEX_TIMEOUT', 'Single model call timed out; no retry') from None
                need(line is not None, 'CODEX_INCOMPLETE', 'App server exited before response')
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    raise Failure('CODEX_PROTOCOL', 'App server emitted non-JSON output') from None
                public_events.append(public_event(event))
                if 'id' in event and 'method' not in event:
                    need(event['id'] == until_id, 'CODEX_PROTOCOL', 'Unexpected RPC response identity')
                    need('error' not in event, 'CODEX_RPC_FAILURE', 'App-server request failed; no retry',
                         diagnostic=redact(event.get('error')))
                    return event.get('result') or {}
                protocol.observe(event)
                if until_completed and protocol.completed:
                    return

        try:
            send('initialize', dict(clientInfo=dict(name='agentic_driving_controlled_pilot',
                 version='1.0'), capabilities=dict(experimentalApi=True)), 1)
            receive(until_id=1)
            send('initialized', {})
            send('thread/start', dict(model=self.model, modelProvider=PROVIDER,
                 allowProviderModelFallback=False, ephemeral=True, cwd=str(work),
                 approvalPolicy='never', sandbox='read-only',
                 baseInstructions='A single image-only driving inference. No tools, files, web, agents or external datasets. Return only the requested native JSON.',
                 developerInstructions='Attached images and supplied acquisition/ego metadata are the only environment evidence. Never call tools.',
                 config=provider_config()), 2)
            protocol.thread_response(receive(until_id=2))
            send('turn/start', dict(threadId=protocol.thread_id, input=input_items,
                 outputSchema=schema, model=self.model, effort='medium'), 3)
            result = receive(until_id=3)
            protocol._turn(result.get('turn', {}).get('id'))
            receive(until_completed=True)
            return protocol
        finally:
            self._stop(process)
            for reader in readers:
                reader.join(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            for name, data in (('stdout.raw', b''.join(raw_out)), ('stderr.raw', b''.join(raw_err))):
                target = private / name
                with target.open('xb') as stream:
                    stream.write(data)
                target.chmod(0o600)
            write_json_new(folder / 'requests.json', redact(sent))
            write_text_new(folder / 'events.redacted.jsonl', ''.join(json.dumps(v, ensure_ascii=False) + '\n' for v in public_events))
            write_json_new(folder / 'wire_manifest.json', dict(
                private_raw_streams_publish=False,
                stdout_sha256=hashlib.sha256(b''.join(raw_out)).hexdigest(),
                stderr_sha256=hashlib.sha256(b''.join(raw_err)).hexdigest(),
                event_count=len(public_events), turn_start_requests=sum(v['method'] == 'turn/start' for v in sent),
                requested_model=self.model, cli_resolved_alias=protocol.resolved_model,
                server_reported_snapshot=None, notification_methods=protocol.notification_methods))
            model_text_observed = any(v['text'] for v in protocol.messages.values()) or any(
                any(parts) for parts in protocol.partial_deltas.values())
            if self.last_attempt_summary is not None:
                self.last_attempt_summary['service_layer'] = dict(
                    native_model_text_observed=bool(model_text_observed),
                    completed_turn_observed=protocol.completed,
                    error_notification_observed=protocol.error_notification,
                    error_origin='not_established' if protocol.error_notification else None,
                    turn_start_sent=any(v['method'] == 'turn/start' for v in sent))
                if model_text_observed:
                    self.last_attempt_summary['model_service_returned'] = True
            for number, item_id in enumerate(protocol.message_order):
                write_text_new(folder / f'agent_message_{number:02d}.txt', protocol.messages[item_id]['text'])
            for number, (item_id, parts) in enumerate(protocol.partial_deltas.items()):
                write_text_new(folder / f'partial_agent_deltas_{number:02d}.txt', ''.join(parts))

    def decide(self, capture, feedback=None):
        need(self.preflight_ok, 'PILOT_PREFLIGHT_REQUIRED', 'Call zero-inference preflight first')
        need(self.call_count < 2, 'PILOT_BUDGET', 'Transport supports at most two decide calls')
        validate('capture_v2.schema.json', capture)
        schema, schema_transport = self._transport_schema()
        need(schema_transport == self.schema_transport, 'WIRE_SCHEMA_CHANGED',
             'The pinned wire bundle changed after adapter construction; no request sent')
        self.call_count += 1
        folder = reserve_directory(self.evidence_dir / f'attempt_{self.call_count:02d}')
        self.last_attempt_summary = dict(attempt=self.call_count, source=self.source,
            status='STARTED', adapter_retries=0, model_service_returned='UNKNOWN')
        start = time.monotonic()
        try:
            safe = deepcopy(capture)
            prompt = (ROOT / 'prompts' / (PROMPT + '.txt')).read_text()
            hashes = {}
            with tempfile.TemporaryDirectory(prefix='driving-pilot-images-') as directory:
                work = Path(directory)
                images = []
                for number, (original, metadata) in enumerate(zip(capture['observations'], safe['observations'])):
                    data = Path(original['path']).read_bytes()
                    need(0 < len(data) <= 20 * 1024 * 1024, 'IMAGE_INPUT', 'Image size invalid')
                    with Image.open(original['path']) as im:
                        need(im.format in ('JPEG', 'PNG', 'WEBP'), 'IMAGE_INPUT', 'Unsupported image type')
                        suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[im.format]
                        im.verify()
                    path = work / f'frame_{number:03d}{suffix}'
                    path.write_bytes(data)
                    images.append(dict(type='localImage', path=str(path)))
                    metadata['path'] = 'attached:' + path.name
                    hashes[original['id']] = hashlib.sha256(data).hexdigest()
                text = prompt + '\n' + json.dumps(dict(capture=safe, feedback=feedback,
                    tool_contract=planning_contract(capture, CONTRACT)), ensure_ascii=False)
                write_text_new(folder / 'model_input.txt', text)
                input_manifest = dict(image_sha256=hashes,
                    prompt_version=PROMPT, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                    output_contract=CONTRACT, schema_sha256=hashlib.sha256((ROOT / 'schemas' / SCHEMA).read_bytes()).hexdigest())
                if schema_transport is not None:
                    input_manifest['schema_transport'] = schema_transport
                write_json_new(folder / 'input_manifest.json', input_manifest)
                protocol = self._rpc(work, [dict(type='text', text=text)] + images, schema, folder)
            text = protocol.final_text()
            write_text_new(folder / 'final_agent_message.txt', text)
            self.last_attempt_summary['model_service_returned'] = True
            try:
                def reject_constant(value):
                    raise ValueError(value)
                output = json.loads(text, parse_constant=reject_constant)
            except ValueError:
                raise Failure('CODEX_RESPONSE', 'Native model final text is not JSON; preserved unchanged') from None
            write_json_new(folder / 'native_output.json', output)
            provenance = dict(source=self.source, transport='official_codex_app_server',
                authentication='chatgpt_subscription', requested_model=self.model,
                cli_resolved_alias=protocol.resolved_model, model_id=protocol.resolved_model,
                model_identity_basis='app-server thread/start resolved alias, not reported server snapshot',
                server_reported_snapshot=None, model_reroute_observed=False,
                cli_version=getattr(self, 'version', None), attempts=1, adapter_retries=0,
                request_max_retries=0, stream_max_retries=0, supports_websockets=False,
                prompt_version=PROMPT, agent_output_contract=CONTRACT,
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(), image_sha256=hashes,
                output_digest=digest(output), tool_calls=0, elapsed_s=time.monotonic() - start)
            if schema_transport is not None:
                provenance['schema_transport'] = schema_transport
            write_json_new(folder / 'provenance.json', provenance)
            self.last_attempt_summary.update(native_json_returned=True,
                                             cli_resolved_alias=protocol.resolved_model)
            validate(SCHEMA, output)
            self.last_attempt_summary.update(status='NATIVE_SCHEMA_VALID', structured_output=True,
                                             cli_resolved_alias=protocol.resolved_model)
            return output, provenance
        except Failure as error:
            self.last_attempt_summary.update(status='FAILED', failure=error.record)
            raise
        except (OSError, subprocess.SubprocessError) as error:
            self.last_attempt_summary.update(status='FAILED', failure=dict(code='CODEX_PROCESS', exception_type=type(error).__name__))
            raise Failure('CODEX_PROCESS', 'Official CLI process failure; no retry', exception_type=type(error).__name__) from None
        finally:
            self.last_attempt_summary['elapsed_s'] = time.monotonic() - start
            write_json_new(folder / 'attempt_summary.json', self.last_attempt_summary)
