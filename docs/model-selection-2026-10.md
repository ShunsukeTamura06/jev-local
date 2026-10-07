# 画像対応 Decision モデル選定（2026-10-07）

## 要件と結論

従来の実行環境である EC2 g5.4xlarge / A10G 24 GiB を前提に、社内で写真と図表を使う decision API の既定候補として **imajev-4b** を選んだ。既存の `choice` / `noul` / `score` を維持し、画像と棄権情報を追加する。推論時の外部通信を禁じ、上流コード・ベース・アダプターを固定版でローカルに置く。

この選定は導入候補の確定であり、社内データでの精度改善を実証したものではない。本番切り替えの判断には写真・図表・テキストそれぞれのラベル付き事例が必要である。

## 公開候補の比較

| 候補 | 画像 | 公開ライセンス | A10G 24 GiB | 公開評価と主な制限 |
| --- | --- | --- | --- | --- |
| [Kev-4B](https://huggingface.co/jaredpalmer/kev-4b) | なし | Apache-2.0 | 現行実績あり | 現行テキスト専用。画像経路を追加するには別モデルが必要。 |
| [Decision-2.0-Nox-4B](https://huggingface.co/vllm-sr/Decision-2.0-Nox-4B) | なし | Apache-2.0 | 4B で候補 | JevArena 63.6、human-labelled transfer 52.3 は公開カードの値。入力は text / JSON と明記。 |
| [decider-2b-vision](https://huggingface.co/Mapika/decider-2b-vision) | あり | Apache-2.0 | bf16 重み約 4.1 GB | Visual7W 300 件で 0.89 / ECE 0.03。写真・図表も使えるが、テキスト部分は旧 v5 で、最新テキストモデルの改善は含まれない。評価は主に選択式 VQA とゲーム。 |
| [Laya Vision](https://huggingface.co/thaitea/laya-vision) | あり | CC BY-NC-SA 4.0 | 201M で余裕 | 34 検証集合 59,427 件の全体正答率 69.1%。非商用・継承条項が社内利用の条件に合わない可能性があるため採用しない。 |
| [Jev-Omni](https://huggingface.co/akhilaaa3/Jev-Omni) | あり | Apache-2.0 | FP32 重み約 50 GB と明記され対象外 | Gemma 4 12B。音声・動画も扱えるが現行 GPU では同カードの推奨構成を満たさない。 |
| [imajev-4b](https://huggingface.co/mohit67890/imajev-4b) | あり | Apache-2.0 | ベース重み約 9.3 GB + アダプター約 0.5 GB | ImajevBench 279 件で 83.9%（画像のみ 109/120、画像＋状態 100/122）。構成した図表 300 件 95.7%、文書画像 300 件 90.3%。英語中心、最大 2 画像・8 質問・4,096 トークン。 |

数値は各開発者の異なる評価集合・実行条件であり、横並びの順位を意味しない。imajev の構成図表は同じ生成器系統の学習データを含む評価で、社内の図表を代表する証拠ではない。[モデルカード](https://huggingface.co/mohit67890/imajev-4b)は公開 ImajevBench のテスト集合がリリース選択にも使われたこと、未知正解 14 件中 3 件で棄権に失敗したことも明記している。写真のみの確率校正は公開カード上で不安定なので社内で再評価する。

## 選定理由

- 写真と構造化状態の照合、図表読み取り、候補外の `unknown` が現行業務に近い。[上流の API 実装](https://github.com/mohit67890/imajev/blob/ccf586d43d2a580319b6535c893668904d909eb9/scripts/playground/server.py)が `POST /v1/systemone` で画像と 3 種の質問を受け、同じ [応答変換](https://github.com/mohit67890/imajev/blob/ccf586d43d2a580319b6535c893668904d909eb9/src/vision_decision/jev_api.py)で確率と棄権を返す。
- [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) とアダプターが公開されている。アダプターの公開 `SHA256SUMS` をインストール時に検証する。
- 4B が A10G 24 GiB の容量候補に入る。ただし実機のピーク VRAM、同時要求時の遅延、CUDA 対応はこの作業環境から検証できていない。
- 画像を使わない既存 `/v1/systemone` の利用も続けられる。新しい応答は `unknown_probability` と `abstained` を追加するため、これらを無視する既存クライアントと、厳密な JSON スキーマを持つクライアントの両方を社内で確認する。

## 再現性と配布

- 推論コード: `mohit67890/imajev` @ `ccf586d43d2a580319b6535c893668904d909eb9`（[コード](https://github.com/mohit67890/imajev/tree/ccf586d43d2a580319b6535c893668904d909eb9)）
- ベース: `Qwen/Qwen3.5-4B` @ `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- アダプター: `mohit67890/imajev-4b` @ `f8d8234cebc6c99065c07731e59716dc0a6e27ab`
- 応答校正: 上記アダプター内の `calibration-rot4.json`。候補の表示順を 4 回変えて平均する上流の公開評価条件に合わせる。
- 依存関係: `requirements.vision.lock`、Python 3.12、x86_64 Linux。torch 2.8.0 と torchvision 0.23.0 の組合せは [PyTorch の案内](https://pytorch.org/get-started/previous-versions/)にある。

コードと重みの表示ライセンスは Apache-2.0。学習データの権利はモデルのライセンスと別であり、上流が記録した [データ出典・条件](https://github.com/mohit67890/imajev/tree/ccf586d43d2a580319b6535c893668904d909eb9/results)を社内の配布・商用利用方針に照らして確認する。重みそのものはこの Git リポジトリに追加しない。

## 導入判定

1. `./scripts/install_vision_model.sh`、`./scripts/start.sh`、`./scripts/smoke_test.sh` を EC2 上で実行し、ピーク VRAM と p50 / p95 遅延を記録する。
2. 社内の正解付き事例を写真・図表・テキスト各 100 件以上集め、撮影条件、図表の解像度、候補外の事例を含める。`scripts/evaluate_model.py` で imajev を集計する。テキストは同じ事例を既存 Kev にも通して比較する。
3. 事前に業務ごとの正答率、誤棄権率、確率校正、遅延の許容値を定める。特に図表の数値読み取りと不鮮明な写真の誤答を別に確認する。未達ならモデル・閾値・運用方法を再検討する。
4. 段階的にトラフィックを切り替える。旧 Kev の起動経路は `app.server` と `scripts/install_model.sh` に残している。

## 決定と残課題

既定起動モデルを imajev-4b に変更する。公開重みをローカルへ固定取得し、社内評価後に本番トラフィックの置き換えを決める。今後の課題は A10G 実機ロード確認、社内データでの精度比較、特に日本語の質問・図表の値読み取り・写真のみの校正である。
