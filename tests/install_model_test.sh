#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/jev-install-test.XXXXXX")"
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/project/scripts" "$fixture/project/model-parts" "$fixture/source/model/base" "$fixture/source/model/adapter"
cp "$root/scripts/install_model.sh" "$fixture/project/scripts/"
printf '{}\n' > "$fixture/source/model/base/config.json"
printf 'test\n' > "$fixture/source/model/adapter/head.pt"
tar -czf "$fixture/archive.tar.gz" -C "$fixture/source" model
cp "$fixture/archive.tar.gz" "$fixture/project/model-parts/model.tar.gz.part-0000"
printf 'model.tar.gz.part-0000\n' > "$fixture/project/model-parts/model.parts"
if command -v sha256sum >/dev/null 2>&1; then
  digest="$(sha256sum "$fixture/archive.tar.gz" | awk '{print $1}')"
else
  digest="$(shasum -a 256 "$fixture/archive.tar.gz" | awk '{print $1}')"
fi
printf '%s  model.tar.gz\n' "$digest" > "$fixture/project/model-parts/model.sha256"
"$fixture/project/scripts/install_model.sh" --git-only
[[ -f "$fixture/project/model/base/config.json" && -f "$fixture/project/model/adapter/head.pt" ]]
rm -rf "$fixture/project/model"
printf 'corrupt\n' >> "$fixture/project/model-parts/model.tar.gz.part-0000"
if "$fixture/project/scripts/install_model.sh" --git-only; then
  echo '破損した part が受け入れられました' >&2
  exit 1
fi
[[ ! -e "$fixture/project/model/base/config.json" ]]
git init --bare "$fixture/remote.git" >/dev/null
mkdir -p "$fixture/publisher/scripts" "$fixture/publisher/model-parts"
cp "$root/scripts/install_model.sh" "$fixture/publisher/scripts/"
git -C "$fixture/publisher" init -b main >/dev/null
git -C "$fixture/publisher" add scripts/install_model.sh
git -C "$fixture/publisher" -c user.name=Test -c user.email=test@example.com commit -m 'test: installer' >/dev/null
git -C "$fixture/publisher" remote add origin "$fixture/remote.git"
git -C "$fixture/publisher" push origin main >/dev/null
git --git-dir="$fixture/remote.git" symbolic-ref HEAD refs/heads/main
git -C "$fixture/publisher" switch -c model-parts-v1 >/dev/null
cp "$fixture/archive.tar.gz" "$fixture/publisher/model-parts/model.tar.gz.part-0000"
cp "$fixture/project/model-parts/model.parts" "$fixture/project/model-parts/model.sha256" "$fixture/publisher/model-parts/"
git -C "$fixture/publisher" add model-parts
git -C "$fixture/publisher" -c user.name=Test -c user.email=test@example.com commit -m 'test: model parts' >/dev/null
git -C "$fixture/publisher" push origin model-parts-v1 >/dev/null
git clone "$fixture/remote.git" "$fixture/consumer" >/dev/null
"$fixture/consumer/scripts/install_model.sh" --git-only
[[ -f "$fixture/consumer/model/base/config.json" && -f "$fixture/consumer/model/adapter/head.pt" ]]
echo 'installer success and checksum failure verified'
