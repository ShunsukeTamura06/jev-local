# jev-local

Kev-4B を EC2 g5.4xlarge（x86_64、NVIDIA A10G）で動かすためのローカル decision API です。`choice`、`noul`（yes/no）、`score` を 1 回のリクエストで処理する `/v1/systemone` を提供します。モデルウェイトは GitHub Release と通常 Git の `model-parts-v1` ブランチの両方に格納し、実行時は `model/` のローカルファイルだけを読みます。

## モデルと再配布条件

| 対象 | 固定版 | ライセンス | 配布内容 |
| --- | --- | --- | --- |
| [Kev-4B](https://huggingface.co/jaredpalmer/kev-4b) | [Kev family Release](https://github.com/jaredpalmer/kev/releases/tag/kev-family) の `kev-4b.tar.gz` | Apache-2.0 | LoRA アダプター、pointer head、トークナイザー関連ファイル |
| [Qwen3.5-4B-Base](https://huggingface.co/Qwen/Qwen3.5-4B-Base) | `1001bb4d826a52d1f399e183466143f4da7b741b` | [Apache-2.0](https://huggingface.co/Qwen/Qwen3.5-4B-Base/blob/main/LICENSE) | ベースの全重み、設定、トークナイザー、ライセンス |
| [Kev 推論コード](https://github.com/jaredpalmer/kev/tree/c9c1f855505336ac32092a5f68305d397f7fcc3e/kev) | `c9c1f855505336ac32092a5f68305d397f7fcc3e` | [Apache-2.0](https://github.com/jaredpalmer/kev/blob/main/LICENSE) | `kev/` に必要なモジュールを同梱 |

Apache-2.0 は複製と再配布を許可します。上記のライセンス文面は `KEV-LICENSE` と archive 内 `model/base/LICENSE` に保持します。学習データセットはこのリポジトリに含めません。Kev-4B は研究プレビューで、未学習の複雑なルールや日付推論では誤判定があり得ます。重要な判断には実データで精度と確率の校正を評価してください。

## 必要な環境

- Amazon Linux 2023 または同等の glibc 2.28 以降を備えた x86_64 Linux
- NVIDIA A10G 24 GiB、利用可能な CUDA 対応ドライバー
- Python 3.12、`python3.12-venv` 相当、Git、`curl`、`tar`、`sha256sum`
- モデル取得時に GitHub へ HTTPS 接続できること。private repository の Release を使う場合は `gh auth login` 済みの [GitHub CLI](https://cli.github.com/) または `GH_TOKEN` が必要
- インストール時に Python パッケージインデックスへ接続できること。実行時のネットワーク接続は不要
- 圧縮ファイルと展開済みモデルを置くため十分な EBS 空き容量（目安 25 GiB 以上）

モデルは約 9.3 GB のベース重みと約 129 MB の Kev アダプターです。fallback の大容量ファイルは別ブランチに置き、通常の clone では取得しません。Git LFS は使用していません。

## EC2 でのセットアップ

```bash
git clone https://github.com/ShunsukeTamura06/jev-local.git
cd jev-local
./scripts/install_model.sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock
nvidia-smi
./scripts/start.sh
```

別の端末で次を実行します。

```bash
cd jev-local
./scripts/smoke_test.sh
```

`install_model.sh` は Release asset を取得し、`model.parts` の順に結合したストリームの SHA-256 を検証してから `model/` に展開します。Release asset の取得に失敗した場合は `model-parts-v1` ブランチの通常 Git ファイルを fetch して使います。fallback を明示するには `./scripts/install_model.sh --git-only`、Release のみ試すには `--release-only` を使います。再インストールするには `model/` を削除して実行します。

`start.sh` は CUDA がない場合に明示的なエラーで終了します。GPU メモリを節約するため bf16、未結合 LoRA、CUDA graph 無効を初期値とします。CUDA graph を試す場合は `KEV_CUDA_GRAPHS=1 ./scripts/start.sh` を指定します。初期値では `127.0.0.1:8008` のみで待ち受けます。外部へ公開する場合は `KEV_API_KEY` を設定し、適切な認証付きプロキシを介してください。

## API の例

```bash
curl -sS http://127.0.0.1:8008/v1/systemone \
  -H 'Content-Type: application/json' \
  -d '{"model":"kev-latest","state":"Customer reports a duplicate card charge.","questions":{"route":{"type":"choice","instructions":"Choose a team.","criteria":{"billing":"Payments","shipping":"Delivery"}},"billing_issue":{"type":"noul","instructions":"Is this a billing issue?"},"urgency":{"type":"score","instructions":"Rate urgency.","criteria":["low","medium","high"]}}}'
```

レスポンスの `answers.route.probabilities` は選択肢ごとの確率、`answers.billing_issue.noul` は yes の確率、`answers.urgency.score` は 0～2 の加重平均です。`GET /v1/models` でデバイス・精度・モデルのロード情報を確認できます。`noul` は Kev と TypeSafe の yes/no 型の名称です。

## オフライン動作とモデル更新

サーバーは `model/base` と `model/adapter` の絶対ローカルパスを指定し、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` を設定します。Kev 側が保持する元の Hugging Face モデル ID は実行時にローカルパスで上書きします。起動時・推論時に Hugging Face への通信は必要ありません。

再梱包は `scripts/package_model.py` を実行します。これは固定コミットのベースと Kev Release のアダプターを取得し、95 MiB ごとの `model.tar.gz.part-0000` 形式、`model.parts`、`model.sha256` を生成します。通常運用では固定済みの Release と Git のファイルをそのまま使用します。ライセンス、固定版、SHA-256 を変更する場合は同時に README、fallback ブランチ、Release tag を更新してください。

## 検証範囲

`scripts/smoke_test.sh` は CUDA 上のロード情報と 3 種類の API 応答を検査します。構築時の検証結果と、まだ実機確認が必要な項目は [Release の説明](https://github.com/ShunsukeTamura06/jev-local/releases/tag/model-v1) に記録します。
