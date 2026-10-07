"""画像 Decision モデルの型付き結果をそのまま公開する API。"""

import logging
import re
from time import perf_counter
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field, StrictStr
from starlette.concurrency import run_in_threadpool
from starlette.routing import Mount
from vision_decision.contracts import DecisionField, Request, Result
from scripts.playground.server import PlaygroundError, decode_data_url, decode_images

log = logging.getLogger(__name__)


class DecisionRequest(Request):
    """上流の判断契約に画像と公開 API の質問数上限を加える。"""

    fields: list[DecisionField] = Field(min_length=1, max_length=8)
    images: list[StrictStr] = Field(default_factory=list, max_length=2)


class DecisionResult(Result):
    """モデル結果のスコアを維持し、内部 logits を応答から除く。"""

    raw_logits: dict[str, float] = Field(exclude=True)


class DecisionResponse(BaseModel):
    """要求識別子、モデル名、判断結果と計測情報を返す。"""

    schema_version: str = "1.0"
    request_id: str
    model: str
    results: dict[str, DecisionResult]
    usage: dict[str, Any]


def register_decision_api(app: FastAPI, backend: Any, calibration: Any) -> None:
    """既存アプリにモデル本来の判断契約を追加する。

    Args:
        app: 互換 API と GPU ロックを持つアプリ。
        backend: ロード済みモデルの推論バックエンド。
        calibration: 固定版の校正器。
    """

    @app.post("/v1/decisions", response_model=DecisionResponse,
              summary="画像・テキストから判断または判断保留を返す")
    async def decisions(payload: DecisionRequest) -> DecisionResponse:
        """画像を検証し、未知の候補を含む型付き判断結果を返す。

        Args:
            payload: 画像、記録と判断項目を含む要求。

        Returns:
            判断保留時に値を持たないモデル結果。
        """
        started = perf_counter()
        for field in payload.fields:
            if field.type == "choice" and len(field.options) > backend.max_options:
                raise PlaygroundError(422, "invalid_request", f"選択肢は最大 {backend.max_options} 個です")
        loaded = decode_images([decode_data_url(value, i) for i, value in enumerate(payload.images)])
        images = [image for image, _ in loaded]
        request = Request.model_validate(payload.model_dump(exclude={"images"}))

        def run() -> tuple[list[Result], dict[str, Any]]:
            """互換 API と同じ GPU ロックで推論する。

            Returns:
                上流の判断結果と計測情報。
            """
            with app.state.lock:
                try:
                    return backend.score(images, request)
                except ValueError as exc:
                    # 固定版上流のトークン上限エラーだけを入力エラーとして扱う。
                    if re.fullmatch(r"Processed request exceeds the \d+-token limit", str(exc)):
                        raise PlaygroundError(422, "input_too_long", str(exc)) from exc
                    raise

        try:
            results, usage = await run_in_threadpool(run)
            if len(results) != len(request.fields):
                raise RuntimeError("判断項目数と結果数が一致しません")
            calibrated = [calibration.calibrate_result(
                result, field.type, len(result.scores) - 1,
                image=bool(images), photo_only=bool(images) and not request.state,
            ) for field, result in zip(request.fields, results)]
            public_results = {field.id: DecisionResult.model_validate(result.model_dump())
                              for field, result in zip(request.fields, calibrated)}
        except PlaygroundError:
            raise
        except Exception as exc:
            log.exception("decision inference failed: request_id=%s", request.request_id)
            raise PlaygroundError(500, "inference_failed", "推論に失敗しました。サーバーログを確認してください") from exc
        return DecisionResponse(
            request_id=request.request_id, model=backend.model, results=public_results,
            usage={**usage, "total_ms": round((perf_counter() - started) * 1000, 1),
                   "images": [{k: meta[k] for k in ("sha256", "width", "height")} for _, meta in loaded]},
        )

    # 上流の playground が / を mount するため、新しい API より先に要求を受けないよう末尾へ置く。
    mounts = [route for route in app.router.routes if isinstance(route, Mount) and route.path == ""]
    app.router.routes[:] = [route for route in app.router.routes if route not in mounts] + mounts
