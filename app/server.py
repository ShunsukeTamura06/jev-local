"""ローカル配置した Kev-4B を CUDA で提供する。"""

import argparse
import os
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

import torch
import uvicorn

from kev.checkpoint import Checkpoint, LoadOptions
from kev.serve import Server, app


def main() -> None:
    """モデルをローカルパスから読み込み、decision API を起動する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, default=Path("model"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8008)
    args = parser.parse_args()
    model_dir = args.model_dir.resolve(strict=True)
    base_dir = model_dir / "base"
    adapter_dir = model_dir / "adapter"
    for expected in (base_dir / "config.json", adapter_dir / "head.pt"):
        if not expected.is_file():
            parser.error(f"モデルファイルがありません: {expected}; ./scripts/install_model.sh を実行してください")
    if not torch.cuda.is_available():
        parser.error("CUDA が利用できません。NVIDIA ドライバーと CUDA 対応 PyTorch を確認してください")
    checkpoint = Checkpoint(str(adapter_dir))
    checkpoint.meta.base = str(base_dir)
    checkpoint.meta.base_revision = None
    options = LoadOptions(dtype=torch.bfloat16, merge=False, backend="torch", cuda_graphs=os.getenv("KEV_CUDA_GRAPHS", "0") == "1")
    tokenizer, model = checkpoint.load("cuda", options)
    app.state.server = Server(checkpoint, tokenizer, model, "cuda")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
