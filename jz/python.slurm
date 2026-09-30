#!/bin/bash
# La Rochelle simulations on one V100 share of Jean Zay (the GPU is unused: SUMO runs on the 10 CPU cores).
#   sbatch jz/python.slurm -m <module> [args]
#SBATCH --job-name=tipe-py
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
# HOME is full on this account: keep every cache and temporary file on the scratch space
export TMPDIR="$SCRATCH/tipe_tmp/$SLURM_JOB_ID" XDG_CACHE_HOME="$SCRATCH/tipe_tmp/cache" PIP_NO_CACHE_DIR=1
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME"
export PYTHONPATH="$WORK/tipe/pylibs:$WORK/tipe"
if [ ! -f pylibs/.installed ]; then
    pip install --no-index --find-links wheels --target pylibs eclipse-sumo sumolib shapely pyproj rtree numpy scipy
    touch pylibs/.installed
fi
if [ ! -f pylibs/.installed_extra4 ]; then
    pip install --no-index --find-links wheels --target pylibs pyarrow pytest matplotlib pillow pandas
    touch pylibs/.installed_extra4
fi
export SUMO_HOME="$(python -c 'import sumo; print(sumo.SUMO_HOME)')"
export PATH="$SUMO_HOME/bin:$PATH"
python "$@"
