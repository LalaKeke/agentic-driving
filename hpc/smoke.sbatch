#!/bin/bash
#SBATCH --job-name=agentic-driving-smoke
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:05:00
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
set -euo pipefail
export PATH=/usr/bin:/bin
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
PROJECT_ROOT=$1
ENV_DIR=$2
COMMIT=$3
[[ -n "${SLURM_JOB_ID:-}" ]] || { printf 'This script must run under Slurm.\n' >&2; exit 2; }
RUN_DIR="$PROJECT_ROOT/runs/$SLURM_JOB_ID"
# Each job gets a fresh source snapshot and separate mutable example results.
mkdir "$RUN_DIR"
git -C "$PROJECT_ROOT" archive "$COMMIT" implementation hpc | tar -x -C "$RUN_DIR"
"$ENV_DIR/bin/python" "$RUN_DIR/hpc/run_smoke.py" \
  --source-root "$RUN_DIR/implementation" --output-dir "$RUN_DIR" --commit "$COMMIT"
