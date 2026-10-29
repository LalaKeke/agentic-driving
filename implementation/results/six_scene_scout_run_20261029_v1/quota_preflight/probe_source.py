"""One metadata-only account/rateLimits/read; never starts a model thread/turn."""
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT / 'implementation'))
from agentic.pilot_transport import config_arguments, redact
from agentic.codex_adapter import subscription_environment

out = Path(__file__).resolve().parent
assert not (out / 'result.json').exists(), 'Probe output already exists; do not overwrite.'
private = out / '.private'
private.mkdir(mode=0o700)
(private / '.gitignore').write_text('*\n!.gitignore\n')
for name in ('sqlite', 'logs'):
    (private / name).mkdir(mode=0o700)
binary = PROJECT / 'runs/tools/codex-0.164.0/node_modules/.bin/codex'
command = [str(binary), 'app-server', '--strict-config', '--stdio'] + config_arguments()
command += ['-c', 'sqlite_home=' + json.dumps(str(private / 'sqlite')),
            '-c', 'log_dir=' + json.dumps(str(private / 'logs'))]
environment = subscription_environment()
for key in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'https_proxy', 'http_proxy', 'all_proxy'):
    environment[key] = 'http://127.0.0.1:17891'
sent, raw_out, raw_err = [], [], []
events = queue.Queue()
started = time.monotonic()
result = dict(observed_at=datetime.now(timezone.utc).isoformat(),
              metadata_only=True, generation_requests=0, automatic_retry=False,
              official_documentation='https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt',
              cli_version='0.164.0', proxy='http://127.0.0.1:17891',
              token_extraction=False, model_identity_check='NOT_PERFORMED_NO_GENERATION')

def safe_limits(snapshot):
    if not isinstance(snapshot, dict):
        return None
    keep = {key: snapshot.get(key) for key in ('limitId', 'limitName', 'rateLimitReachedType') if key in snapshot}
    for name in ('primary', 'secondary'):
        window = snapshot.get(name)
        keep[name] = ({key: window.get(key) for key in ('usedPercent', 'windowDurationMins', 'resetsAt')}
                      if isinstance(window, dict) else None)
    return keep

with tempfile.TemporaryDirectory(prefix='driving-quota-metadata-') as work:
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=work, env=environment,
                               start_new_session=True)
    def stdout_reader():
        for line in iter(process.stdout.readline, b''):
            raw_out.append(line)
            events.put(line)
        events.put(None)
    def stderr_reader():
        for part in iter(lambda: process.stderr.read(4096), b''):
            raw_err.append(part)
    readers = [threading.Thread(target=stdout_reader, daemon=True), threading.Thread(target=stderr_reader, daemon=True)]
    for reader in readers:
        reader.start()
    def send(method, params=None, identifier=None):
        assert method in ('initialize', 'initialized', 'account/rateLimits/read')
        message = dict(method=method)
        if params is not None:
            message['params'] = params
        if identifier is not None:
            message['id'] = identifier
        sent.append(message)
        process.stdin.write((json.dumps(message) + '\n').encode())
        process.stdin.flush()
    def receive(identifier):
        while True:
            remaining = 40 - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError('Metadata quota read exceeded 40 seconds; no retry.')
            line = events.get(timeout=remaining)
            if line is None:
                raise RuntimeError('App-server exited before metadata response.')
            event = json.loads(line)
            if event.get('method') in ('modelProvider/authRecoveryStarted', 'account/chatgptAuthTokens/refresh'):
                raise RuntimeError('Unexpected automatic auth recovery; metadata probe stopped.')
            if event.get('id') == identifier and 'method' not in event:
                if 'error' in event:
                    raise RuntimeError(json.dumps(redact(event['error'])))
                return event.get('result')
    try:
        send('initialize', dict(clientInfo=dict(name='agentic_driving_quota_metadata', version='1.0'),
                                capabilities=dict(experimentalApi=True)), 1)
        receive(1)
        send('initialized', {})
        send('account/rateLimits/read', identifier=2)
        response = receive(2)
        result['status'] = 'RETURNED'
        result['rateLimits'] = safe_limits(response.get('rateLimits'))
        result['rateLimitsByLimitId'] = {key: safe_limits(value) for key, value in
                                       (response.get('rateLimitsByLimitId') or {}).items()}
        snapshots = list(result['rateLimitsByLimitId'].values()) or [result['rateLimits']]
        snapshots = [item for item in snapshots if item is not None]
        windows = [item[name] for item in snapshots for name in ('primary', 'secondary') if item.get(name)]
        blocked = any(item.get('rateLimitReachedType') for item in snapshots) or any(
            item.get('usedPercent') is not None and item['usedPercent'] >= 100 for item in windows)
        result['quota_observation'] = ('LIMIT_REACHED_STOP_EXTERNAL_CALLS' if blocked else
            'WINDOWS_BELOW_LIMIT_NOT_GENERATION_ENTITLEMENT_PROOF' if windows else 'UNKNOWN_STOP_EXTERNAL_CALLS')
    except Exception as error:
        result.update(status='UNAVAILABLE', quota_observation='UNKNOWN_STOP_EXTERNAL_CALLS',
                      error=redact(str(error)))
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
for name, data in (('stdout.raw', b''.join(raw_out)), ('stderr.raw', b''.join(raw_err))):
    path = private / name
    with path.open('xb') as stream:
        stream.write(data)
    path.chmod(0o600)
result.update(elapsed_s=time.monotonic() - started, rpc_methods_sent=[item['method'] for item in sent],
              raw_stdout_sha256=hashlib.sha256(b''.join(raw_out)).hexdigest(),
              raw_stderr_sha256=hashlib.sha256(b''.join(raw_err)).hexdigest(),
              source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              generation_started=False, no_quota_reset_or_credits_consumed=True)
with (out / 'result.json').open('x') as stream:
    json.dump(result, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
with (out / 'requests.json').open('x') as stream:
    json.dump(sent, stream, indent=2)
    stream.write('\n')
print(json.dumps(result, ensure_ascii=False))
