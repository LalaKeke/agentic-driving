"""One charged official API probe, no automatic retries and no key logging."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import ssl
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
import httpx
from agentic.openai_adapter import load_api_key
from agentic.evidence_io import reserve_directory,write_json_new,write_text_new

def main(out,capture=None):
    folder=reserve_directory(out);key=load_api_key();kind='images' if capture else 'text'
    content=[dict(type='input_text',text='Return ok=true and diagnostic="'+kind+'" as JSON. This is a transport check only, not a driving result.')]
    images=[]
    if capture:
        cap=json.loads(capture.read_text())
        for frame in cap['observations']:
            p=Path(frame['path']);data=p.read_bytes()
            content.append(dict(type='input_image',image_url='data:image/jpeg;base64,'+base64.b64encode(data).decode(),detail='auto'))
            images.append(dict(id=frame['id'],sha256=hashlib.sha256(data).hexdigest()))
    schema=dict(type='object',properties=dict(ok=dict(type='boolean',const=True),diagnostic=dict(type='string',enum=[kind])),required=['ok','diagnostic'],additionalProperties=False)
    body=dict(model=os.environ.get('DRIVING_MODEL','gpt-6.1-sol'),input=[dict(role='user',content=content)],
        text=dict(format=dict(type='json_schema',name='connection_probe',strict=True,schema=schema)),
        reasoning=dict(effort='low'),max_output_tokens=1024,tools=[],store=False,stream=False)
    request_id=str(uuid.uuid4())
    write_json_new(folder/'charge.json',dict(charged_before_request=True,kind=kind,user_authorization='unlimited calls; one necessary probe',automatic_retries=0))
    write_json_new(folder/'request.json',dict(http_method='POST',endpoint='/v1/responses',model=body['model'],client_request_id=request_id,schema=schema,image_sha256=images,reasoning_effort='low',max_output_tokens=1024))
    result=dict(kind=kind,api_post_requests=1,new_driving_model_calls=0,model_requested=body['model'],native_text=None,completed=False,automatic_retries=0)
    started=time.monotonic()
    try:
        ca=os.environ.get('DRIVING_CA_BUNDLE','/etc/ssl/certs/ca-certificates.crt')
        with httpx.Client(proxy=os.environ.get('DRIVING_PROXY_URL'),verify=ssl.create_default_context(cafile=ca),trust_env=False,follow_redirects=False,timeout=httpx.Timeout(120,connect=15)) as client:
            response=client.post('https://api.openai.com/v1/responses',headers={'Authorization':'Bearer '+key,'X-Client-Request-Id':request_id},json=body)
        result.update(http_status=response.status_code,request_id=response.headers.get('x-request-id'))
        data=response.json()
        if response.status_code!=200:
            e=data.get('error') or {};result.update(error_type=e.get('type'),error_code=e.get('code'),error_param=e.get('param'))
        else:
            result.update(response_id=data.get('id'),server_reported_model=data.get('model'),response_status=data.get('status'),usage=data.get('usage'))
            text=''.join(p.get('text','') for item in data.get('output',[]) if item.get('type')=='message' for p in item.get('content',[]) if p.get('type')=='output_text')
            if text:write_text_new(folder/'native_message.txt',text)
            result['native_text']=text
            try:parsed=json.loads(text)
            except ValueError:parsed=None
            result['completed']=data.get('status')=='completed' and parsed=={'ok':True,'diagnostic':kind}
            if parsed is not None:write_json_new(folder/'native_output.json',parsed)
    except Exception as error:
        result.update(error_class=type(error).__name__,error='Transport or response parsing failure; raw message suppressed')
    result['elapsed_s']=time.monotonic()-started
    write_json_new(folder/'result.json',result);print(json.dumps(result))
    return 0 if result['completed'] else 3

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--capture',type=Path)
    args=p.parse_args();raise SystemExit(main(args.out,args.capture))
