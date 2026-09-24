#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
repo="${GH_REPO:-ShunsukeTamura06/jev-local}"
tag="${MODEL_RELEASE_TAG:-model-v1}"
mode="${1:-auto}"
if [[ "$mode" != auto && "$mode" != --git-only && "$mode" != --release-only ]]; then
  echo 'usage: ./scripts/install_model.sh [--git-only|--release-only]' >&2
  exit 2
fi
if [[ -f model/.installed.sha256 && -f model/base/config.json && -f model/adapter/head.pt ]]; then
  echo 'モデルはインストール済みです。再取得には model/ を削除してください。'
  exit 0
fi
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/jev-model.XXXXXX")"
trap 'rm -rf "$temp_dir"' EXIT
source_dir="$temp_dir"
if [[ "$mode" != --git-only ]] && command -v gh >/dev/null 2>&1; then
  if gh release download "$tag" -R "$repo" -D "$temp_dir" -p 'model.tar.gz.part-*' -p 'model.sha256' -p 'model.parts'; then
    echo 'GitHub Release からモデルを取得しました。'
  elif [[ "$mode" == --release-only ]]; then
    echo 'Release asset の取得に失敗しました。' >&2
    exit 1
  else
    echo 'Release asset を取得できないため、通常 Git の分割ファイルを使用します。' >&2
    source_dir=model-parts
  fi
else
  if [[ "$mode" == --release-only ]]; then
    echo 'Release の取得には gh CLI が必要です。' >&2
    exit 1
  fi
  source_dir=model-parts
fi
for required in model.sha256 model.parts; do
  [[ -s "$source_dir/$required" ]] || { echo "$source_dir/$required がありません" >&2; exit 1; }
done
parts=()
while IFS= read -r part; do
  [[ "$part" =~ ^model\.tar\.gz\.part-[0-9]{4}$ ]] || { echo "不正な part 名: $part" >&2; exit 1; }
  [[ -s "$source_dir/$part" ]] || { echo "不足している part: $part" >&2; exit 1; }
  parts+=("$source_dir/$part")
done < "$source_dir/model.parts"
[[ ${#parts[@]} -gt 0 ]] || { echo 'part がありません' >&2; exit 1; }
expected="$(awk '{print $1}' "$source_dir/model.sha256")"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo '不正な SHA-256 manifest' >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(cat "${parts[@]}" | sha256sum | awk '{print $1}')"
else
  actual="$(cat "${parts[@]}" | shasum -a 256 | awk '{print $1}')"
fi
[[ "$actual" == "$expected" ]] || { echo "SHA-256 が一致しません: $actual" >&2; exit 1; }
echo "SHA-256 検証済み: $actual"
mkdir -p "$temp_dir/extract"
cat "${parts[@]}" | tar -xz -C "$temp_dir/extract"
[[ -f "$temp_dir/extract/model/base/config.json" && -f "$temp_dir/extract/model/adapter/head.pt" ]] || {
  echo 'archive に必須モデルファイルがありません' >&2; exit 1;
}
printf '%s\n' "$actual" > "$temp_dir/extract/model/.installed.sha256"
rm -rf model
mv "$temp_dir/extract/model" model
echo "model/ へ展開しました。"
