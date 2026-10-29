"""One explicitly charged, short native model request; no driving or tool calls.

Uses the existing strict app-server RPC transport, not the driving decide API.
It never retries. Text and ten-image probes share one independent limit of two.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.pilot_transport import PilotSubscriptionAgent
from agentic.contracts import Failure
from agentic.evidence_io import reserve_directory,write_json_new,write_text_new

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main(args):
    root=args.root.resolve()
    budget=json.loads((root/'generation_budget_activation.json').read_text())
    assert budget['authorized_max_requests']==2 and budget['driving_requests_authorized']==0
    existing=list(root.glob('diagnostic_charge_*.json'))
    assert len(existing)<2,'Independent diagnostic request budget exhausted'
    index=len(existing)+1
    folder=reserve_directory(root/f'generation_probe_{index:02d}_{args.mode}')
    os.environ['DRIVING_PROXY_URL']=args.proxy
    os.environ['DRIVING_CA_BUNDLE']='/etc/ssl/certs/ca-certificates.crt'
    # Connection logs are private. Debug only connection setup, never HTTP body/header traces.
    os.environ['RUST_LOG']='codex_core::responses_retry=debug,codex_core::client=debug,reqwest=debug,hyper_util=debug'
    agent=PilotSubscriptionAgent(evidence_dir=folder/'transport',timeout_s=120)
    schema=dict(type='object',properties=dict(ok=dict(type='boolean'),diagnostic=dict(type='string')),
                required=['ok','diagnostic'],additionalProperties=False)
    for key in ('prompt_version','agent_output_contract'):agent.public_config.pop(key,None)
    agent.public_config.update(purpose='CONNECTION_DIAGNOSTIC_ONLY',business_schema_sent=False,
                               driving_prompt_sent=False,diagnostic_mode=args.mode,
                               proxy_endpoint=args.proxy,certificate_validation=True)
    agent.preflight() # Local version/auth/config only; no model turn.
    images=[];image_hashes={}
    prompt='Connectivity diagnostic only. Do not analyze the scene or make driving decisions. No tools. Return exactly {"ok":true,"diagnostic":"connection"}.'
    write_text_new(folder/'diagnostic_prompt.txt',prompt)
    write_json_new(folder/'diagnostic_schema.json',schema)
    result=dict(mode=args.mode,purpose='CONNECTION_DIAGNOSTIC_ONLY',source='online_model_diagnostic',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        script_sha256=sha(__file__),transport_source_sha256=sha(ROOT/'implementation/agentic/pilot_transport.py'),
        environment_helper_sha256=sha(ROOT/'implementation/agentic/codex_adapter.py'),
        model_requested=agent.model,server_model_snapshot=None,driver_or_physics_tools=0,
        automatic_transport_retries=0,internal_http_attempts=None)
    start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='driving-connection-diagnostic-') as temporary:
        work=Path(temporary)
        if args.mode=='images':
            capture=json.loads(args.capture.read_text())
            assert len(capture['observations'])==10,'The declared image probe uses ten actual inputs'
            for i,frame in enumerate(capture['observations']):
                original=Path(frame['path']);data=original.read_bytes();path=work/(f'input_{i:02d}'+original.suffix)
                path.write_bytes(data);images.append(dict(type='localImage',path=str(path)))
                image_hashes[frame['id']]=hashlib.sha256(data).hexdigest()
        write_json_new(folder/'input_manifest.json',dict(image_sha256=image_hashes,image_count=len(images),
            prompt_sha256=sha(folder/'diagnostic_prompt.txt'),schema_sha256=sha(folder/'diagnostic_schema.json'),
            actual_proxy_scheme_host_port=args.proxy,ca_bundle='/etc/ssl/certs/ca-certificates.crt'))
        write_json_new(root/f'diagnostic_charge_{index:02d}.json',dict(request=index,mode=args.mode,
            charged_before_turn=True,counts_even_if_failed=True,new_independent_budget=2,
            old_budget_inherited=0))
        agent.call_count=index
        agent.last_attempt_summary=dict(source='online_model_diagnostic',attempt=index,adapter_retries=0)
        try:
            protocol=agent._rpc(work,[dict(type='text',text=prompt)]+images,schema,folder)
            text=protocol.final_text();write_text_new(folder/'native_final_message.txt',text)
            output=json.loads(text);write_json_new(folder/'native_output.json',output)
            result.update(status='PASS' if output=={'ok':True,'diagnostic':'connection'} else 'UNEXPECTED_NATIVE_OUTPUT',
                native_response=output,cli_resolved_alias=protocol.resolved_model)
        except Failure as error:
            result.update(status='FAILED',failure=error.record)
        finally:
            result['elapsed_s']=time.monotonic()-start
            write_json_new(folder/'result.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['status']=='PASS' else 3

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--mode',choices=['text','images'],required=True);p.add_argument('--proxy',default='http://127.0.0.1:17891')
    p.add_argument('--capture',type=Path,default=ROOT/'implementation/results/six_scene_scout_preparation_20261029_v1/prepared_inputs/main/ordinary_straight/capture.json')
    sys.exit(main(p.parse_args()))
