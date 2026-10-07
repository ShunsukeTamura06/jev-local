"""GPU を使わずに新しい判断 API の公開契約を検証する。"""

import base64
import io
import unittest
from typing import Any

import httpx
from PIL import Image
from vision_decision.contracts import Request, Result
from scripts.playground.server import create_app
from app.decision_api import register_decision_api


class FakeBackend:
    """判断保留を含む固定結果を返す推論器。"""

    model = "imajev-4b"
    adapter = "test"
    name = "test"
    load_seconds = 0.0
    max_options = 254

    def score(self, images: list, request: Request) -> tuple[list[Result], dict]:
        """入力型ごとの結果を作る。

        Args:
            images: 復号済み画像。
            request: 検証済み要求。

        Returns:
            型付き結果と計測情報。
        """
        if request.state == "failure":
            raise RuntimeError("private backend details")
        if request.state == "token_overflow":
            raise ValueError("Processed request exceeds the 4096-token limit")
        results = []
        for field in request.fields:
            if field.type == "boolean":
                keys, value = ["false", "true"], True
            elif field.type == "choice":
                keys = [option.value for option in field.options]
                value = keys[0]
            else:
                keys = [str(level.value) for level in field.levels]
                value = field.levels[0].value
            abstain = field.question == "unknown"
            scores = {key: 0.1 / len(keys) for key in keys}
            scores["__unknown__"] = 0.9 if abstain else 0.1
            if not abstain:
                selected = "true" if value is True else str(value)
                scores[selected] += 0.8
            results.append(Result(status="abstained" if abstain else "answered", value=None if abstain else value,
                                  reason="insufficient_evidence" if abstain else None,
                                  scores=scores, raw_logits={key: 0.0 for key in scores}))
        return results, {"input_tokens": 32}


class FakeCalibration:
    """同じ結果を返し、画像条件の伝達を記録する校正器。"""

    def __init__(self) -> None:
        """呼び出し履歴を初期化する。"""
        self.calls: list[tuple] = []

    def calibrate_result(self, result: Result, kind: str, count: int, **kwargs: Any) -> Result:
        """校正条件を記録する。

        Args:
            result: 推論結果。
            kind: 判断型。
            count: 既知候補数。
            **kwargs: 画像・記録の有無。

        Returns:
            入力と同じ結果。
        """
        self.calls.append((kind, count, kwargs))
        return result


class DecisionApiTest(unittest.IsolatedAsyncioTestCase):
    """公開スキーマ、画像、判断保留とエラー処理を確認する。"""

    async def asyncSetUp(self) -> None:
        """上流 playground と同じアプリに新 API を登録する。"""
        self.backend = FakeBackend()
        self.calibration = FakeCalibration()
        self.app = create_app(self.backend, examples=[], calibration=self.calibration)
        register_decision_api(self.app, self.backend, self.calibration)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.payload = {"request_id": "case_1", "state": {}, "fields": [
            {"id": "visible", "type": "boolean", "question": "Is the chart visible?"},
            {"id": "higher", "type": "choice", "question": "Which bar is higher?", "options": [
                {"value": "left"}, {"value": "right"}]},
            {"id": "urgency", "type": "ordinal", "question": "Rate urgency.", "levels": [
                {"value": 1, "description": "low"}, {"value": 5, "description": "high"}]},
        ]}

    async def asyncTearDown(self) -> None:
        """HTTP クライアントを閉じる。"""
        await self.client.aclose()

    async def test_native_values_and_scores(self) -> None:
        """真偽値・候補・序数をスコア改変なしで返す。"""
        response = await self.client.post("/v1/decisions", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["request_id"], "case_1")
        self.assertIs(body["results"]["visible"]["value"], True)
        self.assertEqual(body["results"]["higher"]["value"], "left")
        self.assertEqual(body["results"]["urgency"]["value"], 1)
        for result in body["results"].values():
            self.assertNotIn("raw_logits", result)
            self.assertAlmostEqual(sum(result["scores"].values()), 1.0)
            self.assertEqual(result["scores"]["__unknown__"], 0.1)
            self.assertEqual(result["score_semantics"], "uncalibrated_normalized_scores")
        self.assertIn("total_ms", body["usage"])

    async def test_abstention_has_no_value(self) -> None:
        """判断不能時に候補や平均スコアを回答として返さない。"""
        for field in self.payload["fields"]:
            field["question"] = "unknown"
        body = (await self.client.post("/v1/decisions", json=self.payload)).json()
        for result in body["results"].values():
            self.assertEqual(result["status"], "abstained")
            self.assertIsNone(result["value"])
            self.assertEqual(result["reason"], "insufficient_evidence")

    async def test_images_and_calibration(self) -> None:
        """写真のみの条件と画像メタデータを引き渡す。"""
        data = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(data, format="PNG")
        self.payload["images"] = ["data:image/png;base64," + base64.b64encode(data.getvalue()).decode()]
        response = await self.client.post("/v1/decisions", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["usage"]["images"][0]["width"], 64)
        self.assertTrue(all(call[2] == {"image": True, "photo_only": True} for call in self.calibration.calls))

    async def test_rejects_invalid_requests(self) -> None:
        """余分な項目、過大入力、不正画像と候補を拒否する。"""
        invalid = [
            {**self.payload, "model": "other"},
            {**self.payload, "images": ["bad"]},
            {**self.payload, "images": ["bad"] * 3},
            {**self.payload, "fields": self.payload["fields"] * 3},
            {**self.payload, "fields": [{"id": "a", "type": "choice", "question": "Choose", "options": [
                {"value": str(i)} for i in range(255)]}]},
            {**self.payload, "fields": [{"id": "a", "type": "boolean", "question": ""}]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual((await self.client.post("/v1/decisions", json=payload)).status_code, 422)

    async def test_inference_error_does_not_expose_backend_details(self) -> None:
        """推論失敗を明示し、内部例外を外部へ返さない。"""
        with self.assertLogs("app.decision_api", level="ERROR"):
            response = await self.client.post("/v1/decisions", json={**self.payload, "state": "failure"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "inference_failed")
        self.assertNotIn("private", response.text)

    async def test_token_overflow_is_an_input_error(self) -> None:
        """モデルの処理後トークン上限をクライアントの入力エラーにする。"""
        response = await self.client.post("/v1/decisions", json={**self.payload, "state": "token_overflow"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "input_too_long")

    async def test_openapi_documents_native_contract(self) -> None:
        """ドキュメントにネイティブ要求と公開結果を表示する。"""
        schema = (await self.client.get("/openapi.json")).json()
        self.assertIn("/v1/decisions", schema["paths"])
        result_schema = schema["components"]["schemas"]["DecisionResult"]
        self.assertNotIn("raw_logits", result_schema["properties"])

    async def test_compatibility_endpoint_still_works(self) -> None:
        """追加 API が従来の systemone 経路を妨げない。"""
        response = await self.client.post("/v1/systemone", json={"state": {}, "questions": {
            "visible": {"type": "noul", "instructions": "Visible?"}}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("noul", response.json()["answers"]["visible"])


if __name__ == "__main__":
    unittest.main()
