#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
repo="${GH_REPO:-ShunsukeTamura06/jev-local}"
tag="${MODEL_RELEASE_TAG:-model-v1}"
fallback_ref="${MODEL_FALLBACK_REF:-model-parts-v1}"
pinned_sha256="${MODEL_EXPECTED_SHA256:-a76e5cc80e7a12ca1b9743f1661c5b0fc69fd3522b0d7ce62de09ff56c517a5a}"
mode="${1:-auto}"
if [[ "$mode" != auto && "$mode" != --git-only && "$mode" != --release-only ]]; then
  echo 'usage: ./scripts/install_model.sh [--git-only|--release-only]' >&2
  exit 2
fi
if [[ -f model/.installed.sha256 && -f model/base/config.json && -f model/adapter/head.pt ]] &&
   [[ "$(cat model/.installed.sha256)" == "$pinned_sha256" ]]; then
  echo 'モデルはインストール済みです。再取得には model/ を削除してください。'
  exit 0
fi
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/jev-model.XXXXXX")"
trap 'rm -rf "$temp_dir"' EXIT
release_parts_complete() {
  local directory="$1" part count=0
  [[ -s "$directory/model.parts" && -s "$directory/model.sha256" ]] || return 1
  while IFS= read -r part; do
    [[ "$part" =~ ^model\.tar\.gz\.part-[0-9]{4}$ && -s "$directory/$part" ]] || return 1
    count=$((count + 1))
  done < "$directory/model.parts"
  [[ "$count" -gt 0 ]]
}
source_dir="$temp_dir"
fallback_commit=""
if [[ "$mode" != --git-only ]] && command -v gh >/dev/null 2>&1; then
  if gh release download "$tag" -R "$repo" -D "$temp_dir" -p 'model.tar.gz.part-*' -p 'model.sha256' -p 'model.parts' &&
     release_parts_complete "$temp_dir"; then
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
if [[ "$source_dir" == model-parts && ! -s model-parts/model.parts ]]; then
  echo "通常 Git の fallback ブランチ $fallback_ref を取得します。"
  git fetch --no-tags --depth=1 origin "refs/heads/$fallback_ref"
  fallback_commit="$(git rev-parse FETCH_HEAD)"
  git show "$fallback_commit:model-parts/model.parts" > "$temp_dir/model.parts"
  git show "$fallback_commit:model-parts/model.sha256" > "$temp_dir/model.sha256"
  source_dir="$temp_dir"
fi
for required in model.sha256 model.parts; do
  [[ -s "$source_dir/$required" ]] || { echo "$source_dir/$required がありません" >&2; exit 1; }
done
parts=()
while IFS= read -r part; do
  [[ "$part" =~ ^model\.tar\.gz\.part-[0-9]{4}$ ]] || { echo "不正な part 名: $part" >&2; exit 1; }
  if [[ -n "$fallback_commit" ]]; then
    git cat-file -e "$fallback_commit:model-parts/$part" || { echo "不足している part: $part" >&2; exit 1; }
    parts+=("$part")
  else
    [[ -s "$source_dir/$part" ]] || { echo "不足している part: $part" >&2; exit 1; }
    parts+=("$source_dir/$part")
  fi
done < "$source_dir/model.parts"
[[ ${#parts[@]} -gt 0 ]] || { echo 'part がありません' >&2; exit 1; }
stream_parts() {
  if [[ -n "$fallback_commit" ]]; then
    for part in "${parts[@]}"; do git show "$fallback_commit:model-parts/$part"; done
  else
    cat "${parts[@]}"
  fi
}
expected="$(awk '{print $1}' "$source_dir/model.sha256")"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo '不正な SHA-256 manifest' >&2; exit 1; }
[[ "$expected" == "$pinned_sha256" ]] || { echo 'model.sha256 がこの版の固定値と一致しません' >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(stream_parts | sha256sum | awk '{print $1}')"
else
  actual="$(stream_parts | shasum -a 256 | awk '{print $1}')"
fi
[[ "$actual" == "$expected" ]] || { echo "SHA-256 が一致しません: $actual" >&2; exit 1; }
echo "SHA-256 検証済み: $actual"
mkdir -p "$temp_dir/extract"
stream_parts | tar -xz -C "$temp_dir/extract"
[[ -f "$temp_dir/extract/model/base/config.json" && -f "$temp_dir/extract/model/adapter/head.pt" ]] || {
  echo 'archive に必須モデルファイルがありません' >&2; exit 1;
}
printf '%s\n' "$actual" > "$temp_dir/extract/model/.installed.sha256"
printf '\n' > "$temp_dir/extract/model/.gitkeep"
rm -rf model
mv "$temp_dir/extract/model" model
echo "model/ へ展開しました。"
