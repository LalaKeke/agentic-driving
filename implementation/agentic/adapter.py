"""Explicit Chat Completions capability adapter; real requests never impersonated by fixtures."""
import base64
import json
import mimetypes
import os
from pathlib import Path
import time
import httpx
from .contracts import ROOT,validate,need,Failure,digest,model_contract,resolve_prompt,planning_contract

class OnlineAgent:
    def __init__(self,client=None):
        self.base=os.environ.get('DRIVING_API_BASE','').rstrip('/')
        self.model=os.environ.get('DRIVING_MODEL','');self.key=os.environ.get('DRIVING_API_KEY','')
        self.mode=os.environ.get('DRIVING_OUTPUT_MODE','json_schema')
        self.output_contract=os.environ.get('DRIVING_OUTPUT_CONTRACT','2.0.0')
        self.schema_name,self.prompt_version=resolve_prompt(self.output_contract,os.environ.get('DRIVING_PROMPT_VERSION'))
        self.timeout=float(os.environ.get('DRIVING_TIMEOUT_S','90'));self.retries=int(os.environ.get('DRIVING_RETRIES','2'))
        need(self.base.startswith('https://') and self.model and self.key,'API_CONFIGURATION','Set HTTPS DRIVING_API_BASE, DRIVING_MODEL and DRIVING_API_KEY')
        need(self.mode in ('json_schema','json_object','prompt_json') and 0<=self.retries<=4 and 0<self.timeout<=300,'API_CONFIGURATION','Invalid API mode/timeout/retries')
        self.source='mock_transport' if client is not None else 'online_model'
        self.public_config=dict(model=self.model,output_mode=self.mode,timeout_s=self.timeout,retries=self.retries,api_style='chat_completions',prompt_version=self.prompt_version,agent_output_contract=self.output_contract)
        self.client=client or httpx.Client(timeout=self.timeout,follow_redirects=False)
    def decide(self,capture,feedback=None):
        validate('capture_v2.schema.json',capture)
        schema=json.loads((ROOT/'schemas'/self.schema_name).read_text())
        prompt=(ROOT/'prompts'/(self.prompt_version+'.txt')).read_text();safe_capture=json.loads(json.dumps(capture))
        parts=[];hashes={}
        for f,meta in zip(capture['observations'],safe_capture['observations']):
            path=Path(f['path']);data=path.read_bytes();mime=mimetypes.guess_type(path.name)[0]
            need(mime in ('image/png','image/jpeg','image/webp') and 0<len(data)<=20*1024*1024,'IMAGE_INPUT','Use PNG/JPEG/WebP <=20MiB')
            need(data.startswith(b'\x89PNG\r\n\x1a\n') if mime=='image/png' else data.startswith(b'\xff\xd8') if mime=='image/jpeg' else data[:4]==b'RIFF' and data[8:12]==b'WEBP','IMAGE_INPUT','Image signature mismatch')
            hashes[f['id']]=__import__('hashlib').sha256(data).hexdigest();meta['path']='attached:'+f['id']
            parts.extend([{'type':'text','text':json.dumps(meta)},{'type':'image_url','image_url':{'url':f'data:{mime};base64,'+base64.b64encode(data).decode()}}])
        parts.insert(0,{'type':'text','text':json.dumps(dict(capture=safe_capture,feedback=feedback,output_schema=schema,**({'tool_contract':planning_contract(capture,self.output_contract)} if self.prompt_version in ('driving_v2_2','driving_v2_3','driving_v3_exp1') else {})))})
        body=dict(model=self.model,messages=[{'role':'system','content':prompt},{'role':'user','content':parts}])
        if self.mode=='json_schema':body['response_format']={'type':'json_schema','json_schema':{'name':'driving_v2','strict':True,'schema':schema}}
        elif self.mode=='json_object':body['response_format']={'type':'json_object'}
        for attempt in range(self.retries+1):
            try:
                response=self.client.post(self.base+'/chat/completions',headers={'Authorization':'Bearer '+self.key},json=body,timeout=self.timeout)
            except (httpx.TimeoutException,httpx.TransportError):
                if attempt<self.retries:time.sleep(min(2**attempt,4));continue
                raise Failure('API_TRANSPORT','API timeout/transport failure; raw exception suppressed to protect credentials') from None
            if response.status_code in (408,429,500,502,503,504) and attempt<self.retries:
                time.sleep(min(2**attempt,4));continue
            need(response.status_code==200,'API_HTTP','API request rejected; inspect service capability configuration',http_status=response.status_code)
            try:
                data=response.json();choice=data['choices'][0];message=choice['message']
                need(not message.get('refusal') and not message.get('tool_calls') and not message.get('function_call') and choice.get('finish_reason')=='stop','API_INCOMPLETE','Refused or incomplete model response')
                output=json.loads(message['content']);validate(self.schema_name,output)
            except (KeyError,TypeError,IndexError,ValueError):raise Failure('API_RESPONSE','Malformed model response') from None
            # Never log HTTP bodies, headers, endpoint URLs or image bytes.
            provenance=dict(source=self.source,requested_model=self.model,model_id=data.get('model',self.model),output_digest=digest(output),image_sha256=hashes,prompt_version=self.prompt_version,agent_output_contract=self.output_contract,prompt_sha256=__import__('hashlib').sha256(prompt.encode()).hexdigest(),attempts=attempt+1,output_mode=self.mode,tool_calls=0)
            return output,provenance


def make_agent(backend=None,model=None):
    backend=backend or os.environ.get('DRIVING_BACKEND','http')
    if backend=='codex_cli':
        from .codex_adapter import CodexSubscriptionAgent
        return CodexSubscriptionAgent(model=model)
    need(backend=='http','MODEL_BACKEND','Use http or codex_cli')
    need(model is None,'MODEL_CONFIGURATION','HTTP model is configured through DRIVING_MODEL')
    return OnlineAgent()
