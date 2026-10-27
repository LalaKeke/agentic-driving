#!/bin/bash
# Overrides are explicit: HPC_ACCOUNT / HPC_PARTITION / AGENT_ENV_DIR.
set -euo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ENV_DIR=${AGENT_ENV_DIR:-"$PROJECT_ROOT/.venv"}
ACCOUNT=${HPC_ACCOUNT:-p_p21012}
PARTITION=${HPC_PARTITION:-intel}
[[ -x "$ENV_DIR/bin/python" ]] || { printf 'Run bash hpc/bootstrap_env.sh first.\n' >&2; exit 2; }
git -C "$PROJECT_ROOT" diff --quiet
git -C "$PROJECT_ROOT" diff --cached --quiet
COMMIT=$(git -C "$PROJECT_ROOT" rev-parse HEAD)
mkdir -p "$PROJECT_ROOT/logs" "$PROJECT_ROOT/runs"
# No GPU request. Runtime is bounded to five minutes on one CPU.
sbatch --parsable --account="$ACCOUNT" --partition="$PARTITION" \
  --chdir="$PROJECT_ROOT" --export=NONE \
  "$PROJECT_ROOT/hpc/smoke.sbatch" "$PROJECT_ROOT" "$ENV_DIR" "$COMMIT"
