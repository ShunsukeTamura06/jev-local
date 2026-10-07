# jev-local

公開情報のみを扱う社内 PoC の画像対応 decision API です。既定モデルは [imajev-4b](https://huggingface.co/mohit67890/imajev-4b) で、写真・図表とテキストを合わせて選択・真偽・段階判断と判断保留を返します。モデルはサーバー内のローカルファイルから読み、推論時に外部 API へ送りません。採用理由、比較、未検証事項は [調査記録](docs/model-selection-2026-10.md) を参照してください。

既存の Kev-4B は残してあり、旧セットアップの説明は [Kev 運用記録](docs/kev-legacy.md) にあります。imajev の重みは専用の [GitHub Release `imajev-4b-v1`](https://github.com/ShunsukeTamura06/jev-local/releases/tag/imajev-4b-v1) から取得します。EC2 側から Hugging Face に接続する必要はありません。

## 必要な環境

- x86_64 Linux、Python 3.12、Git、curl、NVIDIA A10G 24 GiB（従来の EC2 g5.4xlarge を想定）
- PyTorch 2.8.0 / CUDA 12.8 に対応する NVIDIA ドライバー 570.26 以降
- ベース重み約 9.3 GB、アダプター約 0.5 GB と Python 環境を置く EBS 空き容量。安全な目安は 35 GiB 以上
- セットアップ時に GitHub（Release asset の配信先を含む）と Python パッケージインデックスへ HTTPS 接続可能であること

## インストールと起動

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
./scripts/install_vision_model.sh
nvidia-smi
./scripts/start.sh
```

インストーラーは GitHub Release の分割 asset を取得し、[固定 manifest](models/imajev-4b-v1.json) のサイズ・SHA-256、結合 archive の SHA-256、展開した各ファイルの SHA-256 を検証して `model/imajev/` に保存します。アダプターは上流の `SHA256SUMS` でも検証します。重みは元の固定コミットから変更していません。ライセンスと出典も同梱しています。推論コードは GitHub の固定コミットから取得します。

取得に失敗した場合は同じコマンドで再試行できます。分割 asset は `model/.imajev-download/` に保存され、検証済み asset を再利用し、途中の取得を再開します。導入成功後に取得用 cache を削除します。正常な同一版の導入済み重みは再取得しません。再取得を指定する場合は `./scripts/install_vision_model.sh --force` を使います。`start.sh` は CUDA がない場合やファイルが欠けている場合に起動しません。既定の待受先は `127.0.0.1:8008` です。

Release asset を別の端末で取得して EC2 に搬入した場合は、全分割ファイルがあるディレクトリーを `./scripts/install_vision_model.sh --assets-dir /path/to/assets` で指定できます。この場合も固定 manifest で検証します。GitHub CLI、Git LFS、Hugging Face のアカウントは不要です。配布物の再作成は [配布手順](docs/model-distribution.md) を参照してください。

外部から到達できるようにする場合は認証付きプロキシを用意し、`KEV_API_KEY` を設定してください。既存の環境変数名を継続して使用します。

```bash
export KEV_API_KEY='your-secret'
./scripts/start.sh
./scripts/smoke_test.sh
```

## API

新規接続には `POST /v1/decisions` を使います。モデル本来の `choice` / `boolean` / `ordinal` と、判断保留時の `value: null` を扱います。画像は `images` に PNG、JPEG、WebP の data URL を最大2枚指定し、`state` に付随する記録を入れます。画像なしでも同じ API を使えます。[契約と移行手順](docs/decision-api.md)、[設計判断](docs/adr/0001-native-decision-api.md) を参照してください。

```python
import base64
import json
import urllib.request
from pathlib import Path

image = base64.b64encode(Path("chart.png").read_bytes()).decode()
request = {
    "request_id": "chart_001",
    "state": {"note": "Compare the two bars in the attached chart."},
    "images": [f"data:image/png;base64,{image}"],
    "fields": [
        {"id": "higher", "type": "choice", "question": "Which bar is higher?",
         "options": [{"value": "left"}, {"value": "right"}]},
        {"id": "clear", "type": "boolean", "question": "Are both bars clearly visible?"},
    ],
}
headers = {"Content-Type": "application/json"}
# KEV_API_KEY を設定した場合は Authorization: Bearer <key> も送る。
http_request = urllib.request.Request(
    "http://127.0.0.1:8008/v1/decisions",
    data=json.dumps(request).encode(), headers=headers,
)
with urllib.request.urlopen(http_request, timeout=120) as response:
    body = json.load(response)
for field_id, result in body["results"].items():
    print(field_id, result["value"] if result["status"] == "answered" else "判断保留")
```

`results` の各結果には `status`、`value`、`scores`、校正情報、`reason` が含まれます。`scores` は候補と `__unknown__` の合計1で、再正規化や不明スコアの混合を行いません。`status: "abstained"` の値は必ず `null` です。`ordinal` の値は選ばれた段階の整数です。`GET /v1/models` でロード済みモデルを確認でき、`/docs` に OpenAPI の利用画面があります。入力上限は2画像、8項目、処理後4,096トークンで、画像は最大20 MiB / 20メガピクセルです。質問や選択肢は英語での学習・評価が中心です。

`POST /v1/systemone` も Jev 形式の互換経路として残しています。従来の `state` / `questions`、`choice` / `noul` / `score` と画像入力を扱います。新 API ではモデル本来の判断結果を利用できます。

## PoC 評価と切り替え

`./scripts/smoke_test.sh` は画像を含む呼び出しの入出力契約だけを確認します。公開情報から作った正解付き事例で精度を測るには、1 行に 1 事例の JSONL を用意します。画像パスは JSONL と同じディレクトリーを基準に指定します。入力内容は集計レポートに保存しません。

```json
{"category":"photo","request":{"request_id":"photo_001","state":{"note":"Check the photo."},"fields":[{"id":"damage","type":"boolean","question":"Is the item damaged?"}]},"images":["photo.jpg"],"gold":{"damage":true}}
```

```bash
.venv/bin/python scripts/evaluate_model.py --cases public-eval/cases.jsonl --output public-eval/imajev-report.json
```

既定の評価先は `/v1/decisions` です。旧形式の事例を使う場合は `--url http://127.0.0.1:8008/v1/systemone` を明示します。比較用の集計名は boolean を `noul`、ordinal を `score` に対応させます。不明が正解の事例は `gold` に `"__unknown__"` を指定します。MAE は判断保留を除く回答分だけで測り、判断保留率と全体正答率・回答分の正答率も併せて確認します。

写真、図表、テキストのみを分けて収集し、Kev を使っている既存業務に近い公開テキスト事例も同じ正解で比較してください。少なくとも選択・真偽の正答率、校正誤差、棄権率、段階値の MAE、遅延を確認し、用途ごとに許容値を決めてから PoC の接続先を切り替えてください。開発者の公開ベンチマークの数値だけでは、この PoC での精度を保証できません。画像の精度は同じベースの汎用 VLM である Qwen3.5-4B とも比較が必要です。非商用 PoC の軽量な比較候補である Laya Vision についても [調査記録](docs/model-selection-2026-10.md) に制約を記載しています。

旧 Kev を再起動する場合は `./scripts/install_model.sh` で旧重みを配置し、`.venv/bin/python -m app.server` を実行します。現在の `start.sh` は imajev を起動します。
