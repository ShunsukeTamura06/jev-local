"""固定済み imajev-4b を社内向け decision API として起動する。"""

import argparse
import hmac
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Awaitable, Callable

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

import torch
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "model" / "imajev"


def load_app(model_dir: Path) -> FastAPI:
    """ローカルの固定版モデルと認証付き API を構築する。

    Args:
        model_dir: インストール済みモデルのディレクトリー。

    Returns:
        起動可能な FastAPI アプリケーション。

    Raises:
        RuntimeError: モデルが不完全、または CUDA が利用できない場合。
    """
    marker = model_dir / "installed.json"
    if not marker.is_file():
        raise RuntimeError(f"モデルがありません: {marker}; ./scripts/install_vision_model.sh を実行してください")
    from scripts.install_vision_model import SOURCE_COMMIT, BASE_COMMIT, ADAPTER_COMMIT
    expected = {"source_commit": SOURCE_COMMIT, "base_commit": BASE_COMMIT, "adapter_commit": ADAPTER_COMMIT}
    if json.loads(marker.read_text()) != expected:
        raise RuntimeError("インストール済みモデルの固定版が一致しません。再インストールしてください")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA が利用できません。NVIDIA ドライバーを確認してください")
    source = model_dir / "source"
    sys.path.insert(0, str(source))
    from scripts.playground.server import build_backend, create_app
    from vision_decision.calibration import TemperatureCalibrator

    base = model_dir / "base"
    adapter = model_dir / "adapter"
    calibration = adapter / "calibration-rot4.json"
    for required in (base / "config.json", adapter / "adapter_model.safetensors", calibration):
        if not required.is_file():
            raise RuntimeError(f"モデルファイルがありません: {required}")
    bundle = model_dir / "bundle.json"
    if not bundle.is_file() or json.loads(bundle.read_text()) != {
        "repo": "Qwen/Qwen3.5-4B", "revision": BASE_COMMIT, "path": str(base)
    }:
        raise RuntimeError("モデル bundle.json が不正です。再インストールしてください")
    backend = build_backend("torch", adapter=adapter, bundle=bundle, rotations=4, max_input_tokens=4096)
    backend.model = "imajev-4b"
    app = create_app(backend, calibration=TemperatureCalibrator.load(calibration))
    key = os.getenv("KEV_API_KEY")

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """設定済みの API キーで v1 API を保護する。

        Args:
            request: HTTP 要求。
            call_next: 次の処理を呼ぶ関数。

        Returns:
            認証済み要求への応答または 401 応答。
        """
        if key and request.url.path.startswith("/v1/") and not hmac.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {key}"
        ):
            response = JSONResponse({"detail": "invalid API key"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
        else:
            response = await call_next(request)
        response.headers["x-typesafe-request-id"] = request.headers.get("x-typesafe-request-id") or uuid.uuid4().hex
        return response

    return app


def main() -> None:
    """単一 GPU 用のローカル HTTP サーバーを起動する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, default=MODEL)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8008)
    args = parser.parse_args()
    uvicorn.run(load_app(args.model_dir.resolve()), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
