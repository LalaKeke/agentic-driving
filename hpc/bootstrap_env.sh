#!/bin/bash
# Lightweight environment installation only. Tests must run through Slurm.
set -euo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ENV_DIR=${AGENT_ENV_DIR:-"$PROJECT_ROOT/.venv"}
PYTHON_BIN=${AGENT_BOOTSTRAP_PYTHON:-python3}
WHEEL_DIR=${AGENT_WHEELHOUSE:-"$PROJECT_ROOT/wheelhouse"}

if [[ ! -x "$ENV_DIR/bin/python" ]]; then
  if [[ -e "$ENV_DIR" ]]; then
    printf 'Refusing to overwrite incomplete environment: %s\n' "$ENV_DIR" >&2
    exit 2
  fi
  "$PYTHON_BIN" -m venv "$ENV_DIR"
fi
if [[ -d "$WHEEL_DIR" ]]; then
  "$ENV_DIR/bin/python" -m pip install --no-index --find-links "$WHEEL_DIR" \
    -r "$PROJECT_ROOT/hpc/requirements-lock.txt"
else
  "$ENV_DIR/bin/python" -m pip install -r "$PROJECT_ROOT/hpc/requirements-lock.txt"
fi
printf 'Environment ready: %s\nSubmit tests using: bash hpc/submit_smoke.sh\n' "$ENV_DIR"
