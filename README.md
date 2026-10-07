# jev-local

公開情報のみを扱う社内 PoC の画像対応 decision API です。既定モデルは [imajev-4b](https://huggingface.co/mohit67890/imajev-4b) で、写真・図表とテキストを合わせて `choice`、`noul`、`score` を返します。モデルはサーバー内のローカルファイルから読み、推論時に外部 API へ送りません。採用理由、比較、未検証事項は [調査記録](docs/model-selection-2026-10.md) を参照してください。

既存の Kev-4B は残してあり、旧セットアップの説明は [Kev 運用記録](docs/kev-legacy.md) にあります。既存の `model-parts-v1` ブランチや GitHub Release は imajev の配布には使いません。

## 必要な環境

- x86_64 Linux、Python 3.12、Git、NVIDIA A10G 24 GiB（従来の EC2 g5.4xlarge を想定）
- PyTorch 2.8.0 / CUDA 12.8 に対応する NVIDIA ドライバー 570.26 以降
- ベース重み約 9.3 GB、アダプター約 0.5 GB と Python 環境を置く EBS 空き容量。安全な目安は 35 GiB 以上
- セットアップ時に GitHub、Hugging Face、Python パッケージインデックスへ HTTPS 接続可能であること

## インストールと起動

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
./scripts/install_vision_model.sh
nvidia-smi
./scripts/start.sh
```

インストーラーは上流の推論コード、Qwen3.5-4B ベース、imajev-4b アダプターを固定コミットで `model/imajev/` に保存します。アダプターは上流の `SHA256SUMS` で検証します。ダウンロードに失敗しても完了マーカーを作らないため、同じコマンドで再試行できます。`start.sh` は CUDA がない場合やファイルが欠けている場合に起動しません。既定の待受先は `127.0.0.1:8008` です。

外部から到達できるようにする場合は認証付きプロキシを用意し、`KEV_API_KEY` を設定してください。既存の環境変数名を継続して使用します。

```bash
export KEV_API_KEY='your-secret'
./scripts/start.sh
./scripts/smoke_test.sh
```

## API

テキストのみの呼び出しは従来と同じ `/v1/systemone` です。画像を使う場合は `images` に PNG、JPEG、WebP の data URL を最大 2 枚指定できます。`state` は写真に付随する記録を入れます。

```python
import base64
import json
import urllib.request
from pathlib import Path

image = base64.b64encode(Path("chart.png").read_bytes()).decode()
request = {
    "model": "imajev-4b",
    "state": {"note": "Compare the two bars in the attached chart."},
    "images": [f"data:image/png;base64,{image}"],
    "questions": {
        "higher": {"type": "choice", "instructions": "Which bar is higher?", "criteria": {"left": None, "right": None}},
        "clear": {"type": "noul", "instructions": "Are both bars clearly visible?"},
    },
}
headers = {"Content-Type": "application/json"}
# KEV_API_KEY を設定した場合は Authorization: Bearer <key> も送る。
http_request = urllib.request.Request(
    "http://127.0.0.1:8008/v1/systemone",
    data=json.dumps(request).encode(), headers=headers,
)
with urllib.request.urlopen(http_request, timeout=120) as response:
    print(json.load(response))
```

レスポンスには `answers` とともに `unknown_probability`、`abstained` が含まれます。選択肢の確率は既知の候補で再正規化され、候補外の確率は別フィールドです。`GET /v1/models` でロード済みモデルを確認できます。上流の入力上限は 2 画像、8 質問、4,096 トークンで、画像は最大 20 MiB / 20 メガピクセルです。質問や選択肢は英語での学習・評価が中心です。

## PoC 評価と切り替え

`./scripts/smoke_test.sh` は画像を含む呼び出しの入出力契約だけを確認します。公開情報から作った正解付き事例で精度を測るには、1 行に 1 事例の JSONL を用意します。画像パスは JSONL と同じディレクトリーを基準に指定します。入力内容は集計レポートに保存しません。

```json
{"category":"photo","request":{"state":{"note":"Check the photo."},"questions":{"damage":{"type":"noul","instructions":"Is the item damaged?"}}},"images":["photo.jpg"],"gold":{"damage":true}}
```

```bash
.venv/bin/python scripts/evaluate_model.py --cases public-eval/cases.jsonl --output public-eval/imajev-report.json
```

写真、図表、テキストのみを分けて収集し、Kev を使っている既存業務に近い公開テキスト事例も同じ正解で比較してください。少なくとも choice / noul の正答率、校正誤差、棄権率、score の MAE、遅延を確認し、用途ごとに許容値を決めてから PoC の接続先を切り替えてください。開発者の公開ベンチマークの数値だけでは、この PoC での精度を保証できません。非商用 PoC の軽量な比較候補である Laya Vision についても [調査記録](docs/model-selection-2026-10.md) に制約を記載しています。

旧 Kev を再起動する場合は `./scripts/install_model.sh` で旧重みを配置し、`.venv/bin/python -m app.server` を実行します。現在の `start.sh` は imajev を起動します。
