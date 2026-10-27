"""Credential-free HTTPS checks, run on a Slurm compute node through a local tunnel."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import socket
import ssl
import time
import urllib.error
import urllib.request


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--port',type=int,required=True)
    args=p.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):p.error('Run this probe through Slurm.')
    proxy=f'http://127.0.0.1:{args.port}'
    # The explicit ready file is written after SSH has established its forward.
    for _ in range(420):
        if (args.run_dir/'forward_ready').is_file():break
        time.sleep(1)
    else:
        print('No authenticated compute tunnel was marked ready within 420 seconds.',flush=True)
        return 2
    result={'job_id':os.environ['SLURM_JOB_ID'],'hostname':socket.gethostname(),
            'proxy':proxy,'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'authenticated_api_call':False,'checks':[]}
    for label,url,expected in [('github','https://api.github.com',200),
                               ('api_transport','https://api.openai.com/v1/models',401)]:
        attempts=[]
        for tls12 in (False,True):
            context=ssl.create_default_context()
            if tls12:context.minimum_version=context.maximum_version=ssl.TLSVersion.TLSv1_2
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy,'http':proxy}),
                                               urllib.request.HTTPSHandler(context=context))
            status=None; error=None
            try:
                with opener.open(urllib.request.Request(url,headers={'User-Agent':'Agentic-Driving-Network-Probe'}),timeout=15) as response:
                    status=response.status
            except urllib.error.HTTPError as exc:status=exc.code
            except (OSError,urllib.error.URLError) as exc:error=str(exc)
            attempts.append({'tls':'TLS1.2' if tls12 else 'default','status':status,'error':error})
            if status==expected:break
        result['checks'].append({'name':label,'expected_status':expected,'success':status==expected,'attempts':attempts})
    result['success']=all(c['success'] for c in result['checks'])
    (args.run_dir/'proxy_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return 0 if result['success'] else 1


if __name__=='__main__':raise SystemExit(main())
