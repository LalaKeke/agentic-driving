#!/bin/bash
#SBATCH --job-name=agentic-proxy-probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:05:00
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
set -euo pipefail
export PATH=/usr/bin:/bin
PROJECT_ROOT=$1
PROXY_PORT=$2
RUN_DIR="$PROJECT_ROOT/runs/$SLURM_JOB_ID"
mkdir "$RUN_DIR"
hostname > "$RUN_DIR/compute_node.txt"
# Obtain the compute host key through the authenticated scheduler path,
# from the allocated node's own loopback SSH service. Never accept arbitrary keys.
for SSH_PORT in 10022 22; do
  ssh-keyscan -T 3 -p "$SSH_PORT" -t ed25519 127.0.0.1 2>/dev/null > "$RUN_DIR/compute_key.tmp" || true
  if [[ -s "$RUN_DIR/compute_key.tmp" ]]; then
    printf '%s\n' "$SSH_PORT" > "$RUN_DIR/compute_ssh_port.txt"
    cp "$RUN_DIR/compute_key.tmp" "$RUN_DIR/compute_host_key.txt"
    break
  fi
done
[[ -s "$RUN_DIR/compute_host_key.txt" ]] || { printf 'No SSH host key on the allocated node.\n' >&2; exit 2; }
printf '%s\n' "$PROXY_PORT" > "$RUN_DIR/proxy_port.txt"
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/hpc/probe_proxy.py" --run-dir "$RUN_DIR" --port "$PROXY_PORT"
