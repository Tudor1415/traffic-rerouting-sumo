#!/bin/bash
# The SUMO steps of the city test on the 10 cores of one V100 share (SUMO does not use the GPU).
#   sbatch [--export=ALL,PART=k/n] jz/city_cpu.slurm <step> ...     step: streets | demand | freeflow | sumo | score
#SBATCH --job-name=tipe-citysumo
#SBATCH --partition=gpu_p13
#SBATCH --qos=qos_gpu-dev
#SBATCH --account=wbw@v100
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=10
#SBATCH --hint=nomultithread
#SBATCH --time=02:00:00
#SBATCH --output=logs/%x_%j.out
set -eo pipefail
module purge
module load python/3.12.7
set -u
cd "$WORK/tipe"
export TMPDIR="$SCRATCH/tipe_tmp/$SLURM_JOB_ID" XDG_CACHE_HOME="$SCRATCH/tipe_tmp/cache" PIP_NO_CACHE_DIR=1
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME"
export PYTHONPATH="$WORK/tipe/pylibs:$WORK/tipe"
export SUMO_HOME="$(python -c 'import sumo; print(sumo.SUMO_HOME)')"
export PATH="$SUMO_HOME/bin:$PATH"
for step in "$@"; do python -m rerouting.city "$step" --workers 10; done
