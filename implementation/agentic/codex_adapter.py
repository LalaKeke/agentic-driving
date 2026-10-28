"""Official Codex CLI model transport using existing ChatGPT authentication.

No token extraction, API-key fallback, tools, GT files, or model substitution.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from PIL import Image
from .contracts import ROOT,Failure,need,validate,digest,model_contract,resolve_prompt,planning_contract

DISABLED_FEATURES=('shell_tool','unified_exec','view_image','apps','plugins','multi_agent',
                   'code_mode_host','browser_use','computer_use','image_generation','hooks',
                   'goals','skill_search','memories','remote_plugin','shell_snapshot')
API_KEY_VARIABLES=('OPENAI_API_KEY','CODEX_API_KEY','DRIVING_API_KEY')


def subscription_environment():
    env=dict(os.environ)
    for key in API_KEY_VARIABLES:env.pop(key,None)
    return env


def parse_events(stdout,expected_model):
    """Only text reasoning/final messages accepted; reject any tool use."""
    events=[];messages=[];usage=None;completed=False;notices=[]
    for line in stdout.splitlines():
        if not line.strip():continue
        try:event=json.loads(line)
        except ValueError:raise Failure('CODEX_PROTOCOL','CLI did not emit JSON events') from None
        kind=event.get('type');events.append(kind)
        if event.get('model') is not None:
            need(event['model']==expected_model,'MODEL_SUBSTITUTION','CLI reported a different model')
        if kind=='turn.completed':completed=True;usage=event.get('usage')
        if kind in ('item.started','item.updated','item.completed'):
            item=event.get('item',{});item_type=item.get('type')
            if item_type=='error':
                need(item.get('message','').startswith('Code Mode is unavailable because code-mode host is disabled.'),'CODEX_RUNTIME_DIAGNOSTIC','Unexpected CLI diagnostic; response not accepted')
                notices.append('code_mode_host_disabled');continue
            need(item_type in ('reasoning','agent_message'),'CODEX_TOOL_USE',
                 'Tool use invalidates image-only experiment',item_type=item_type)
            if kind=='item.completed' and item_type=='agent_message':messages.append(item.get('text',''))
        if kind in ('turn.failed','error'):raise Failure('CODEX_SERVICE_FAILURE','Codex service rejected or failed the request; raw response suppressed')
    need(completed and messages,'CODEX_INCOMPLETE','No completed structured model response')
    try:output=json.loads(messages[-1])
    except ValueError:raise Failure('CODEX_RESPONSE','Final response was not JSON') from None
    return output,dict(event_types=events,usage=usage,tool_calls=0,disabled_capability_notices=notices)


class CodexSubscriptionAgent:
    def __init__(self,model=None,timeout_s=None,binary=None):
        self.model=model or os.environ.get('DRIVING_MODEL','gpt-6.1-sol')
        self.timeout=float(timeout_s if timeout_s is not None else os.environ.get('DRIVING_CODEX_TIMEOUT_S','180'))
        project_cli=ROOT.parent/'runs/tools/codex-0.164.0/node_modules/.bin/codex'
        self.binary=binary or os.environ.get('DRIVING_CODEX_BIN') or (str(project_cli) if project_cli.is_file() else 'codex')
        need(shutil.which(self.binary) is not None,'CODEX_MISSING','Install official Codex CLI')
        need(self.model and 1<=self.timeout<=900,'CODEX_CONFIGURATION','Explicit model and timeout in 1..900 seconds required')
        try:
            status=subprocess.run([self.binary,'login','status'],capture_output=True,text=True,env=subscription_environment(),timeout=15)
            version=subprocess.run([self.binary,'--version'],capture_output=True,text=True,env=subscription_environment(),timeout=15)
        except (OSError,subprocess.TimeoutExpired):raise Failure('CODEX_STARTUP','Cannot inspect official CLI authentication/version') from None
        need(status.returncode==0 and 'Logged in using ChatGPT' in status.stdout+status.stderr,
             'CODEX_CHATGPT_LOGIN_REQUIRED','Use official codex login with ChatGPT; API-key login is not accepted')
        self.version=version.stdout.strip()
        self.output_contract=os.environ.get('DRIVING_OUTPUT_CONTRACT','2.0.0')
        self.schema_name,self.prompt_version=resolve_prompt(self.output_contract,os.environ.get('DRIVING_PROMPT_VERSION'))
        self.public_config=dict(backend='codex_cli',model=self.model,authentication='chatgpt_subscription',
            cli_version=self.version,cli_binary=self.binary,timeout_s=self.timeout,adapter_retries=0,
            cli_transport_retries='CLI defaults, bounded by process-group timeout',reasoning_effort='medium',
            tool_access=False,api_key_environment_removed=True,model_substitution=False,prompt_version=self.prompt_version,agent_output_contract=self.output_contract)

    def command(self,work,images):
        cmd=[self.binary,'exec','--ignore-user-config','--ephemeral','--skip-git-repo-check',
             '--sandbox','read-only','--model',self.model,'--json','--color','never',
             '--config','forced_login_method="chatgpt"','--config','model_reasoning_effort="medium"',
             '--config','web_search="disabled"']
        for name in DISABLED_FEATURES:cmd+=['--disable',name]
        cmd+=['--output-schema',str(work/'output.schema.json')]
        for image in images:cmd+=['--image',str(image)]
        return cmd+['-']

    def decide(self,capture,feedback=None):
        validate('capture_v2.schema.json',capture)
        safe=deepcopy(capture);prompt=(ROOT/'prompts'/(self.prompt_version+'.txt')).read_text()
        hashes={};start=time.monotonic()
        # Isolated cwd outside the repository; attached files have opaque names.
        with tempfile.TemporaryDirectory(prefix='agentic-vision-') as folder:
            work=Path(folder);images=[]
            (work/'output.schema.json').write_text((ROOT/'schemas'/self.schema_name).read_text())
            for i,(f,meta) in enumerate(zip(capture['observations'],safe['observations'])):
                original=Path(f['path']);data=original.read_bytes()
                need(0<len(data)<=20*1024*1024,'IMAGE_INPUT','Image outside configured size range')
                with Image.open(original) as im:
                    need(im.format in ('JPEG','PNG','WEBP'),'IMAGE_INPUT','Unsupported image type')
                    im.verify();extension={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}[im.format]
                path=work/f'frame_{i:03d}{extension}';path.write_bytes(data);images.append(path)
                hashes[f['id']]=hashlib.sha256(data).hexdigest();meta['path']='attached:'+path.name
            instructions=(prompt+'\nYou are a single multimodal inference call, not a coding/delegation task. '
                'Use only attached images and allowed acquisition/ego metadata. Tools, files, search, '
                'other agents, and external datasets are unavailable and prohibited. '
                'Images are attached in the exact order of capture.observations. '
                'Return only the requested structured final object.\n'+
                json.dumps(dict(capture=safe,feedback=feedback,**({'tool_contract':planning_contract(capture)} if self.prompt_version in ('driving_v2_2','driving_v2_3') else {})),ensure_ascii=False))
            try:
                process=subprocess.Popen(self.command(work,images),cwd=work,env=subscription_environment(),
                                         stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                         text=True,start_new_session=True)
            except OSError:raise Failure('CODEX_PROCESS','Could not start official Codex CLI') from None
            try:stdout,stderr=process.communicate(instructions,timeout=self.timeout)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                partial_stdout,partial_stderr=process.communicate()
                partial_event_types=[]
                for line in partial_stdout.splitlines():
                    try:partial_event_types.append(json.loads(line).get('type'))
                    except ValueError:pass
                raise Failure('CODEX_TIMEOUT','Membership model call timed out; no output substituted',
                              timeout_s=self.timeout,requested_model=self.model,event_types_before_timeout=partial_event_types) from None
            if process.returncode:
                lower=(stderr+stdout).lower()
                diagnostic_flags=dict(network_timeout='timed out' in lower,connection_error='connection' in lower,readonly_state='read-only file system' in lower or 'readonly database' in lower,unsupported_model='not supported' in lower or 'model_not_found' in lower)
                code='CODEX_CLI_STATE' if diagnostic_flags['readonly_state'] else 'CODEX_MODEL_UNAVAILABLE' if 'not supported' in lower or 'model_not_found' in lower else 'CODEX_TRANSPORT' if 'timed out' in lower or 'connection' in lower else 'CODEX_PROCESS'
                raise Failure(code,'Official CLI failed; authentication and raw diagnostics suppressed',
                              exit_code=process.returncode,requested_model=self.model,diagnostic_flags=diagnostic_flags)
            output,events=parse_events(stdout,self.model);validate(self.schema_name,output)
            provenance=dict(source='online_model',transport='official_codex_cli',authentication='chatgpt_subscription',
                requested_model=self.model,model_id=self.model,model_identity_basis='explicit CLI request; snapshot not exposed',
                cli_version=self.version,output_digest=digest(output),image_sha256=hashes,prompt_version=self.prompt_version,agent_output_contract=self.output_contract,
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),attempts=1,elapsed_s=time.monotonic()-start,
                output_mode='codex_output_schema',**events)
            return output,provenance
