"""One verified proxy CONNECT/TLS check; no account or model request, no retry."""
import argparse
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.codex_adapter import subscription_environment

def check(proxy,ca_bundle):
    env=subscription_environment(proxy_url=proxy,ca_bundle=ca_bundle)
    endpoint=urlsplit(env['HTTPS_PROXY'])
    if endpoint.scheme!='http':raise ValueError('This bounded probe supports an HTTP CONNECT proxy')
    start=time.monotonic();stage='tcp_to_proxy'
    result=dict(proxy=dict(scheme=endpoint.scheme,host=endpoint.hostname,port=endpoint.port),
                target='chatgpt.com:443',ca_bundle=env['SSL_CERT_FILE'],generation_requests=0,retries=0)
    try:
        with socket.create_connection((endpoint.hostname,endpoint.port),timeout=5) as conn:
            conn.settimeout(6);stage='proxy_connect'
            conn.sendall(b'CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n')
            header=b''
            while b'\r\n\r\n' not in header:
                part=conn.recv(1)
                if not part:raise ConnectionError('Proxy closed before CONNECT response')
                header+=part
                if len(header)>16384:raise ValueError('Oversized proxy response')
            first=header.split(b'\r\n')[0]
            if len(first.split())<2 or first.split()[1]!=b'200':raise ConnectionError('Proxy did not accept CONNECT')
            stage='tls_handshake'
            context=ssl.create_default_context(cafile=env['SSL_CERT_FILE'])
            context.set_alpn_protocols(['h2','http/1.1'])
            with context.wrap_socket(conn,server_hostname='chatgpt.com') as secured:
                result.update(status='TLS_VERIFIED',tls_version=secured.version(),alpn=secured.selected_alpn_protocol())
    except (OSError,ValueError) as error:
        result.update(status='FAILED',failed_stage=stage,error_type=type(error).__name__,error=str(error))
    result['elapsed_s']=time.monotonic()-start
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--proxy',default=os.environ.get('DRIVING_PROXY_URL','http://127.0.0.1:17891'))
    p.add_argument('--ca-bundle',default=os.environ.get('DRIVING_CA_BUNDLE','/etc/ssl/certs/ca-certificates.crt'))
    args=p.parse_args();result=check(args.proxy,args.ca_bundle)
    print(json.dumps(result,ensure_ascii=False));sys.exit(0 if result['status']=='TLS_VERIFIED' else 2)
