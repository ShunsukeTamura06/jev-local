# imajev の判断 API

新しい接続には `POST /v1/decisions` を使います。上流の `vision_decision.contracts.Request` / `Result` を公開契約として使い、JSON に画像を追加しています。自由文を生成するチャット API ではありません。OpenAPI は `/openapi.json`、対話式ドキュメントは `/docs` です。

## 入力

```json
{
  "request_id": "chart_001",
  "state": {"note": "Compare the bars in the attached chart."},
  "images": ["data:image/png;base64,<base64>"],
  "fields": [
    {
      "id": "higher",
      "type": "choice",
      "question": "Which bar is higher?",
      "options": [{"value": "left"}, {"value": "right"}]
    },
    {"id": "clear", "type": "boolean", "question": "Are both bars clearly visible?"},
    {
      "id": "quality",
      "type": "ordinal",
      "question": "Rate the chart's readability.",
      "levels": [{"value": 1, "description": "poor"}, {"value": 5, "description": "good"}]
    }
  ]
}
```

- `request_id`: 必須。1〜128文字。応答に同じ値を返します。
- `schema_version`: 省略時 `"1.0"`。現在はこの版のみ。
- `state`: 画像に付随する記録。オブジェクトまたは文字列。省略時 `{}`。画像の data URL は `images` に入れます。
- `images`: 省略可能な PNG / JPEG / WebP の base64 data URL の配列。0〜2枚。2枚の場合は1枚目が参照、2枚目が比較対象です。外部画像 URL の取得は行いません。新 API は JSON 入力のみです。
- `fields`: 1〜8件。各 `id` は英字から始まる英数字・アンダースコアで最大64文字、重複不可。`question` は必須で1〜2,000文字。
- `choice`: 2個以上の異なる文字列の `options[].value` と、省略可能な `description`。上限はロード済みモデルの `/v1/models` の `max_options`。`__unknown__` は予約値です。
- `boolean`: 真偽判断。必要なら `yes_description` / `no_description` を指定します。
- `ordinal`: 2〜10段階。`levels[].value` は異なる整数を昇順に指定し、各段階に `description` を付けます。応答はモデルが選んだ整数であり、段階の期待値ではありません。

`state` のコンパクト UTF-8 JSON は最大128 KiB、入れ子は最大8段です。処理後の入力は質問ごとに画像を含め4,096トークンまで。画像は1枚につき最大20 MiB / 20メガピクセルです。上限超過を切り捨てず、エラーにします。`model` / `thinking` / `questions` など新契約にない項目は拒否します。ロード済みの単一モデルを使用します。`execution` は上流の既定値（`mode: "inspect"`、`allow_external_fallback: false`）のみで、外部 API にフォールバックしません。

## 出力

以下は形を示す例で、実機の測定結果ではありません。

```json
{
  "schema_version": "1.0",
  "request_id": "chart_001",
  "model": "imajev-4b",
  "results": {
    "higher": {
      "status": "abstained",
      "value": null,
      "scores": {"left": 0.1, "right": 0.1, "__unknown__": 0.8},
      "score_semantics": "calibrated_normalized_scores",
      "calibration_version": "<校正版>",
      "reason": "insufficient_evidence"
    }
  },
  "usage": {"input_tokens": 123, "total_ms": 100.0, "images": []}
}
```

- `status: "answered"`: `value` は choice の文字列、boolean の真偽値、ordinal の整数。`reason` は `null`。
- `status: "abstained"`: `value` は必ず `null`、`reason` は `"insufficient_evidence"`。候補を勝手に補って業務処理へ進めないでください。
- `scores`: すべての既知候補と `__unknown__` を含み、合計1。boolean のキーは `"true"` / `"false"`、ordinal は整数を文字列化したキーです。既知候補だけでの再正規化や、不明スコアの真偽値への混合を行いません。
- `score_semantics`: 校正が適用された場合 `calibrated_normalized_scores` と校正版、それ以外は `uncalibrated_normalized_scores` と `calibration_version: null`。スコアは用途ごとの正答確率を保証するものではありません。
- `usage`: バックエンドの入力トークン数・計測値と、全体時間 `total_ms`、画像の SHA-256・幅・高さ。内部 logits と入力内容は応答に含めません。

`KEV_API_KEY` を設定した場合は `Authorization: Bearer <key>` を送ります。正常・エラー応答には `x-typesafe-request-id` も付きます。本文の `request_id` は別の識別子です。

## エラーと互換 API

入力形式・候補・トークン上限・不正画像は HTTP 422、画像のサイズ上限は413、認証失敗は401、推論失敗は500です。入力や推論エラーは `error` と `detail`、認証エラーは `detail` を返します。新 API の推論失敗では内部例外を応答に出さずサーバーログに記録します。

`POST /v1/systemone` は上流 Jev 形式の互換経路として維持します。`state` / `questions`、`choice` / `noul` / `score`、画像の data URL と multipart アップロードを扱います。新 API に移る場合は `questions` を `fields` に置き換え、`criteria` を `options` / `levels`、`instructions` を `question` にします。`noul` は `boolean`、`score` は `ordinal` です。応答は `answers` から `results` に変更し、`status` を確認してから `value` を使います。新 API に `confidence` や `noul` はありません。

## GPU なしの契約テスト

固定済み上流ソースのルートを `upstream_dir` として、モデル重みなしで実行できます。

```bash
python -m pip install -c requirements.vision.lock pydantic fastapi httpx numpy pillow python-multipart
PYTHONPATH="$upstream_dir/src:$upstream_dir" python -m unittest discover -s tests -p 'test_*.py' -v
```

CI はインストーラーと同じ `SOURCE_COMMIT` の上流ソースを取得して検証します。CUDA ロードと実機の画像精度は別途 `smoke_test.sh` と正解付き評価で確認します。
