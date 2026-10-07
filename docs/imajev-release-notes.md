公開情報のみを扱う社内 PoC 向けに、画像対応 Decision モデル imajev-4b と Qwen3.5-4B の固定版重みを配布します。EC2 から Hugging Face に接続せず、GitHub Release から導入できます。

## 導入

```bash
git clone --single-branch --branch feat/imajev-vision https://github.com/ShunsukeTamura06/jev-local.git jev-local-vision
cd jev-local-vision
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
./scripts/install_vision_model.sh
./scripts/start.sh --port 8009
```

別端末で `./scripts/smoke_test.sh http://127.0.0.1:8009` を実行します。`KEV_API_KEY` を使う場合は両端末に同じ値を設定してください。同じ GPU に複数のモデルをロードする場合は VRAM が不足する可能性があります。

必要環境は x86_64 Linux、Python 3.12、Git、curl、NVIDIA A10G 24 GiB、対応ドライバー、35 GiB 以上の空き容量です。GitHub の Release asset 配信先と Python パッケージインデックスへの接続が必要です。

## 重みと検証

- Qwen/Qwen3.5-4B: `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- mohit67890/imajev-4b: `f8d8234cebc6c99065c07731e59716dc0a6e27ab`
- 推論コード mohit67890/imajev: `ccf586d43d2a580319b6535c893668904d909eb9`
- 元の重みは変更していません。最大 1 GiB の分割 archive に再梱包しました。
- `manifest.json` は各 asset・結合 archive・展開ファイルの SHA-256 とサイズを記録しています。同じ manifest をコード内 `models/imajev-4b-v1.json` に固定しています。
- インストーラーは archive の構造・各ハッシュ・上流のアダプター SHA256SUMS を確認してから導入完了を記録します。
- Apache-2.0 のライセンス、モデルカード、出典は archive に含まれます。上流の推論コードの LICENSE も Release に添付します。

配布 archive の完全性と GitHub 配布先の検証を実施しています。A10G 実機での CUDA ロード・ピーク VRAM・遅延と写真・図表の精度比較は未実施です。この Release は PoC 用の prerelease です。

コードの変更一式と評価手順は PR #3 と README を参照してください。

新規接続の推奨 API は `POST /v1/decisions` です。`fields` に choice / boolean / ordinal を指定し、`results` の status / value と不明を含む scores を受け取ります。判断保留時の値は null です。`/v1/systemone` も互換経路として利用できます。新 API は上記のブランチの最新コードで利用してください。この重み配布タグに含まれる旧コードには新 API がありません。モデル重みの asset と manifest はそのまま使います。
