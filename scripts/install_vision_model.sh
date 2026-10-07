#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -x .venv/bin/python ]] || { echo '.venv がありません。先に Python 3.12 の仮想環境を作成してください。' >&2; exit 1; }
.venv/bin/python -m pip install -r requirements.vision.lock
exec .venv/bin/python scripts/install_vision_model.py
