# 画像モデルの GitHub 配布

EC2 から Hugging Face に接続できないため、固定版の Qwen3.5-4B と imajev-4b アダプターを取得して GitHub Release `imajev-4b-v1` に再配布する。EC2 のインストーラーは GitHub Release から取得する。推論コードも GitHub の固定コミットから取得し、Python 依存パッケージは通常のパッケージインデックスから取得する。

## 配布物

- `imajev-model.tar.gz.part-0000` から始まる最大 1 GiB の分割 asset
- `manifest.json`: 各 asset のサイズと SHA-256、結合 archive の SHA-256、各モデルファイルのサイズと SHA-256、出典と固定コミット
- archive 内の `model/imajev/base/`: Qwen3.5-4B の重み、設定、トークナイザー、モデルカード、LICENSE
- `model/imajev/adapter/`: imajev-4b のアダプター、readout、校正、モデルカード、上流の SHA256SUMS
- `model/imajev/licenses/APACHE-2.0.txt` と `model/imajev/provenance.json`

ベースとアダプターの公開ライセンスは Apache-2.0。重みを変更せずに配布し、ライセンスと著作者・出典を示すモデルカードを保持する。[Qwen のモデルカード](https://huggingface.co/Qwen/Qwen3.5-4B)、[imajev のモデルカード](https://huggingface.co/mohit67890/imajev-4b)を出典とする。GitHub は [Release の asset を 2 GiB 未満](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)に制限しているため分割する。モデル重みを通常 Git のコード履歴や Git LFS に追加しない。

## EC2 での取得

```bash
./scripts/install_vision_model.sh
./scripts/start.sh --port 8009
# 別端末で実行。認証を設定している場合は同じ KEV_API_KEY を指定する。
./scripts/smoke_test.sh http://127.0.0.1:8009
```

取得先は `https://github.com/ShunsukeTamura06/jev-local/releases/download/imajev-4b-v1/`。GitHub の asset 配信先へリダイレクトされる。モデル取得時と推論時に Hugging Face へ通信しない。PyPI へのアクセスは Python 環境の構築に必要である。

全分割 asset をブラウザー等で取得済みなら `./scripts/install_vision_model.sh --assets-dir /path/to/assets` でも導入できる。取得先の URL やリモートの manifest の値を信頼するのではなく、コードと一緒に取得した `models/imajev-4b-v1.json` に固定されたハッシュで検証する。

## 配布担当が再作成する場合

この操作は Hugging Face と GitHub の両方に接続できる配布担当の環境で行う。EC2 での通常導入には不要である。

```bash
python3 scripts/package_vision_model.py --output work/vision-release
mkdir -p models
cp work/vision-release/manifest.json models/imajev-4b-v1.json
```

パッケージ作成は一度に一つの配布元ファイルを保存し、1 GiB の asset に逐次梱包する。重みの再圧縮にかかる CPU 時間を避け、gzip の無圧縮モードを使う。配布元が公開する重みの SHA-256 とアダプターの SHA256SUMS を確認してから梱包する。途中で失敗した場合は不完全な配布ファイルを削除して再作成する。出力先の `.downloads/` だけが残っている場合は、ハッシュが一致する取得済み重みを再利用できる。

manifest をコードへコミットした後、そのコミットを対象に Release を作成し、全分割 asset と同じ manifest をアップロードする。重みを更新する場合は新しい Release tag と manifest を作成し、既存 asset を差し替えない。再梱包によって archive のハッシュが変わった場合も manifest のレビューと更新が必要である。

GitHub Actions の `package-vision-model` workflow でも梱包と draft Release へのアップロードを実行できる。コードに固定 manifest がある版では、再梱包せず配布済み asset のサイズ・SHA-256 を検証する。固定 manifest がまだない版では梱包とアップロードを実行するが、公開は自動では行わない。生成した `manifest.json` をコードに固定し、全 asset のサイズ・SHA-256 を確認した後にそのコードのコミットを対象として PoC の prerelease を公開する。既存の同名 Release がある場合は作成を拒否するため、新版の配布では tag と manifest を更新する。

GPU ロードと写真・図表の精度評価は配布検証と別に EC2 上で実行する。この配布経路の変更だけで GPU の動作確認や精度改善を実証したことにはならない。
