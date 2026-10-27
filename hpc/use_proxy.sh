#!/bin/bash
# Source in the SAME shell/process that will call the API.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'Use: source hpc/use_proxy.sh [remote_proxy_port]\n' >&2
  exit 2
fi
_agent_proxy_port=${1:-17890}
if [[ ! "$_agent_proxy_port" =~ ^[0-9]{4,5}$ ]] || ((10#$_agent_proxy_port < 1024 || 10#$_agent_proxy_port > 65535)); then
  printf 'Invalid proxy port.\n' >&2
  unset _agent_proxy_port
  return 2
fi
export http_proxy="http://127.0.0.1:$_agent_proxy_port"
export https_proxy="$http_proxy"
export HTTP_PROXY="$http_proxy" HTTPS_PROXY="$http_proxy"
export no_proxy="localhost,127.0.0.1,::1,.tongji.edu.cn"
export NO_PROXY="$no_proxy"
printf 'This shell uses proxy %s. The SSH tunnel must stay connected.\n' "$http_proxy"
unset _agent_proxy_port
