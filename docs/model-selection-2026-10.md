# 画像対応 Decision モデル選定（2026-10-07）

## 要件と結論

従来の実行環境である EC2 g5.4xlarge / A10G 24 GiB を前提に、公開情報のみを入力する社内 PoC の既定候補として **imajev-4b** を選んだ。写真と図表を使い、既存の `choice` / `noul` / `score` に画像と棄権情報を追加する。推論時の外部通信を禁じ、上流コード・ベース・アダプターを固定版でローカルに置く。

この選定は PoC 用の実装候補を決めたものであり、Kev に対する精度改善を実証したものではない。PoC の利用範囲に合う公開画像・図表・テキストから独立した正解付き事例を作り、実機で比較してから社内利用を切り替える。

## 公開候補の比較

| 候補 | 画像 | 公開ライセンス | A10G 24 GiB | 公開評価と主な制限 |
| --- | --- | --- | --- | --- |
| [Kev-4B](https://huggingface.co/jaredpalmer/kev-4b) | なし | Apache-2.0 | 現行実績あり | 現行テキスト専用。画像経路を追加するには別モデルが必要。 |
| [Decision-2.0-Nox-4B](https://huggingface.co/vllm-sr/Decision-2.0-Nox-4B) | なし | Apache-2.0 | 4B で候補 | JevArena 63.6、human-labelled transfer 52.3 は公開カードの値。入力は text / JSON と明記。 |
| [decider-2b-vision](https://huggingface.co/Mapika/decider-2b-vision) | あり | Apache-2.0 | bf16 重み約 4.1 GB | Visual7W 300 件で 0.89 / ECE 0.03。写真・図表も使えるが、テキスト部分は旧 v5 で、最新テキストモデルの改善は含まれない。評価は主に選択式 VQA とゲーム。 |
| [Laya Vision](https://huggingface.co/thaitea/laya-vision) | あり | CC BY-NC-SA 4.0 | 201M で余裕 | 34 検証集合 59,427 件の全体正答率 69.1%。非商用 PoC では軽量な比較候補。画像は 1 枚・512 px、公開評価には写真や図表が含まれるが、この PoC の用途で imajev より高精度かは未検証。 |
| [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) | あり | Apache-2.0 | 重み約 9.3 GB | imajev と同じベースの汎用 VLM。公開カードでは RealWorldQA 79.5、MathVista mini 85.1、CharXiv RQ 70.8。汎用 VQA の比較基準として重要だが、Decision 固有の校正済み確率・棄権は別途実装・評価が必要。 |
| [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct) | あり | Apache-2.0 | 4B で候補 | 汎用 VLM。画像付き chat API を公開。現行 Qwen3.5-4B と同一事例で比較する場合の追加候補。 |
| [Gemma 4 E4B-it](https://huggingface.co/google/gemma-4-E4B-it) | あり | Apache-2.0 | 重み約 16 GB、余裕は要実測 | 汎用 VLM。公開カードでは MMMU-Pro 52.6%、OmniDocBench 1.5 の平均編集距離 0.181。Decision 固有の確率・棄権は別途実装・評価が必要。 |
| [Jev-Omni](https://huggingface.co/akhilaaa3/Jev-Omni) | あり | Apache-2.0 | FP32 重み約 50 GB と明記され対象外 | Gemma 4 12B。音声・動画も扱えるが現行 GPU では同カードの推奨構成を満たさない。 |
| [imajev-4b](https://huggingface.co/mohit67890/imajev-4b) | あり | Apache-2.0 | ベース重み約 9.3 GB + アダプター約 0.5 GB | ImajevBench 279 件で 83.9%（画像のみ 109/120、画像＋状態 100/122）。構成した図表 300 件 95.7%、文書画像 300 件 90.3%。英語中心、最大 2 画像・8 質問・4,096 トークン。 |

数値は各開発者の異なる評価集合・実行条件であり、横並びの順位を意味しない。imajev の構成図表は同じ生成器系統の学習データを含む評価で、PoC の図表を代表する証拠ではない。[モデルカード](https://huggingface.co/mohit67890/imajev-4b)は公開 ImajevBench のテスト集合がリリース選択にも使われたこと、未知正解 14 件中 3 件で棄権に失敗したことも明記している。写真のみの確率校正は公開カード上で不安定なので、この PoC の事例で再評価する。

## 選定理由

- 写真と構造化状態の照合、図表読み取り、候補外の `unknown` が現行業務に近い。[上流の API 実装](https://github.com/mohit67890/imajev/blob/ccf586d43d2a580319b6535c893668904d909eb9/scripts/playground/server.py)が `POST /v1/systemone` で画像と 3 種の質問を受け、同じ [応答変換](https://github.com/mohit67890/imajev/blob/ccf586d43d2a580319b6535c893668904d909eb9/src/vision_decision/jev_api.py)で確率と棄権を返す。
- [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) とアダプターが公開されている。アダプターの公開 `SHA256SUMS` をインストール時に検証する。
- 4B が A10G 24 GiB の容量候補に入る。ただし実機のピーク VRAM、同時要求時の遅延、CUDA 対応はこの作業環境から検証できていない。
- Laya Vision は非商用 PoC なら比較可能な軽量候補。ただし公開ベンチマークは直接比較できず、1 枚・512 px 制約が写真と図表の細部読取に影響しうる。PoC の正解付き事例で imajev と比較し、必要なら既定候補を見直す。
- 汎用 VLM は写真・図表の読解力で優れる可能性がある。特に同じベース重みの Qwen3.5-4B を比較しないまま、imajev が最も正確だとは結論できない。imajev は確率と棄権を返す Decision API の完成度で既定候補とし、PoC の評価で汎用 VLM が上回れば選定を更新する。
- 画像を使わない既存 `/v1/systemone` の利用も続けられる。新しい応答は `unknown_probability` と `abstained` を追加するため、これらを無視する既存クライアントと、厳密な JSON スキーマを持つクライアントの両方を社内で確認する。

## 再現性と配布

- 推論コード: `mohit67890/imajev` @ `ccf586d43d2a580319b6535c893668904d909eb9`（[コード](https://github.com/mohit67890/imajev/tree/ccf586d43d2a580319b6535c893668904d909eb9)）
- ベース: `Qwen/Qwen3.5-4B` @ `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`
- アダプター: `mohit67890/imajev-4b` @ `f8d8234cebc6c99065c07731e59716dc0a6e27ab`
- 配布: [GitHub Release `imajev-4b-v1`](https://github.com/ShunsukeTamura06/jev-local/releases/tag/imajev-4b-v1)。EC2 の Hugging Face 接続制限に合わせ、固定版の重みを変更せず再梱包する。各 asset とファイルの SHA-256 を `models/imajev-4b-v1.json` に固定する。[配布手順](model-distribution.md)を参照。
- 応答校正: 上記アダプター内の `calibration-rot4.json`。候補の表示順を 4 回変えて平均する上流の公開評価条件に合わせる。
- 依存関係: `requirements.vision.lock`、Python 3.12、x86_64 Linux。torch 2.8.0 と torchvision 0.23.0 の組合せは [PyTorch の案内](https://pytorch.org/get-started/previous-versions/)にある。

コードと重みの表示ライセンスは Apache-2.0。入力は公開情報に限定する。学習データの権利はモデルのライセンスと別なので、上流が記録した [データ出典・条件](https://github.com/mohit67890/imajev/tree/ccf586d43d2a580319b6535c893668904d909eb9/results)と PoC で使う画像の利用条件を確認する。Laya Vision を比較に使う場合は CC BY-NC-SA 4.0 の条件も確認する。重みそのものはこの Git リポジトリに追加しない。

## 導入判定

1. `./scripts/install_vision_model.sh`、`./scripts/start.sh`、`./scripts/smoke_test.sh` を EC2 上で実行し、ピーク VRAM と p50 / p95 遅延を記録する。
2. PoC で使う公開情報から正解付き事例を写真・図表・テキスト各 100 件以上集め、撮影条件、図表の解像度、候補外の事例を含める。開発者の公開ベンチマークに含まれない事例を優先し、`scripts/evaluate_model.py` で imajev を集計する。テキストは同じ事例を既存 Kev にも通して比較する。画像は Qwen3.5-4B の汎用 VLM 応答と同じ正解で比較し、Laya Vision も同じ事例で比較すると軽量化の判断ができる。汎用 VLM の回答を評価する際は、choice・noul の正答率と出力形式違反率を測り、校正済み確率として扱わない。
3. PoC 用途ごとの正答率、誤棄権率、確率校正、遅延の許容値を先に定める。特に図表の数値読み取りと不鮮明な写真の誤答を別に確認する。未達ならモデル・閾値・運用方法を再検討する。
4. 判定を満たしたら PoC の接続先を切り替える。旧 Kev の起動経路は `app.server` と `scripts/install_model.sh` に残している。

## 決定と残課題

既定起動モデルを imajev-4b に変更する。公開重みをローカルへ固定取得し、公開事例による比較と A10G 実機検証後に PoC の接続先を切り替える。残課題は日本語の質問、図表の値読み取り、写真のみの確率校正、Qwen3.5-4B と Laya Vision との同条件比較である。
