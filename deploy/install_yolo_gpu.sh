#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -x "$project_dir/.venv-yolo/bin/python" ]; then
  uv venv --python 3.12 "$project_dir/.venv-yolo"
fi
uv pip install --python "$project_dir/.venv-yolo/bin/python" 'torch==2.6.0+cu124' 'torchvision==0.21.0+cu124' --index-url https://download.pytorch.org/whl/cu124
if ! uv pip install --python "$project_dir/.venv-yolo/bin/python" -r "$project_dir/deploy/yolo-gpu-requirements.txt" -c "$project_dir/deploy/yolo-gpu-constraints.txt"; then
  uv pip install --offline --python "$project_dir/.venv-yolo/bin/python" -r "$project_dir/deploy/yolo-gpu-requirements.txt" -c "$project_dir/deploy/yolo-gpu-constraints.txt"
fi
