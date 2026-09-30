#!/bin/bash
# Copies the code, the prepared data and the built network to $WORK/tipe on Jean Zay (files only).
set -euo pipefail
cd "$(dirname "$0")/.."
REMOTE=jean-zay:/lustre/fswork/projects/rech/wbw/ups45kr/tipe
rsync -a --mkpath rerouting data jz pyproject.toml "$REMOTE/"
rsync -a --mkpath build/larochelle/larochelle.net.xml "$REMOTE/build/larochelle/"
ssh jean-zay "mkdir -p /lustre/fswork/projects/rech/wbw/ups45kr/tipe/logs /lustre/fswork/projects/rech/wbw/ups45kr/tipe/results"
