#!/bin/bash
# The city chain and its theory on one V100 (32 GB).
#   sbatch [--export=ALL,PART=k/n] jz/city_gpu.slurm <step> ...     step: smoke | theory | chain
#SBATCH --job-name=tipe-city
#SBATCH --partition=gpu_p13
#SBATCH -C v100-32g
#SBATCH --qos=qos_gpu-dev
#SBATCH --account=wbw@v100
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=10
#SBATCH --hint=nomultithread
#SBATCH --time=02:00:00
#SBATCH --output=logs/%x_%j.out
set -eo pipefail
module purge
module load pytorch-gpu/py3/2.8.0
set -u
cd "$WORK/tipe"
export TMPDIR="$SCRATCH/tipe_tmp/$SLURM_JOB_ID" XDG_CACHE_HOME="$SCRATCH/tipe_tmp/cache"
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME"
export PYTHONPATH="$WORK/tipe"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
for step in "$@"; do python -m rerouting.city "$step"; done
