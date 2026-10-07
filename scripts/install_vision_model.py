"""固定した imajev-4b と Qwen ベースをローカルに配置する。"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "model" / "imajev"
SOURCE_REPO = "https://github.com/mohit67890/imajev.git"
SOURCE_COMMIT = "ccf586d43d2a580319b6535c893668904d909eb9"
BASE_REPO = "Qwen/Qwen3.5-4B"
BASE_COMMIT = "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
ADAPTER_REPO = "mohit67890/imajev-4b"
ADAPTER_COMMIT = "f8d8234cebc6c99065c07731e59716dc0a6e27ab"
ADAPTER_FILES = (
    "adapter_config.json", "adapter_model.safetensors", "decision_readout.json",
    "decision_readout.safetensors", "calibration-rot4.json", "SHA256SUMS", "README.md",
)
REQUIRED_BASE_FILES = ("config.json", "model.safetensors.index.json", "preprocessor_config.json")


def sha256(path: Path) -> str:
    """大きなファイルをメモリに載せず SHA-256 を求める。

    Args:
        path: 検査対象ファイル。

    Returns:
        小文字 16 進の SHA-256。
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_adapter(directory: Path) -> None:
    """公開 manifest を使用してアダプターの必須ファイルを検証する。

    Args:
        directory: 固定版アダプターの保存先。
    """
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        checksums[name.strip()] = digest
    for name in ADAPTER_FILES:
        if name in ("SHA256SUMS", "README.md"):
            continue
        expected = checksums.get(name)
        if expected is None or len(expected) != 64:
            raise RuntimeError(f"SHA256SUMS に必須ファイルがありません: {name}")
        actual = sha256(directory / name)
        if actual != expected:
            raise RuntimeError(f"アダプターの SHA-256 が一致しません: {name}")


def install_source(directory: Path) -> None:
    """上流の推論コードを固定コミットに配置する。

    Args:
        directory: 推論コードの保存先。
    """
    if not directory.exists():
        subprocess.run(["git", "clone", "--filter=blob:none", SOURCE_REPO, str(directory)], check=True)
    subprocess.run(["git", "-C", str(directory), "fetch", "--depth=1", "origin", SOURCE_COMMIT], check=True)
    subprocess.run(["git", "-C", str(directory), "checkout", "--detach", SOURCE_COMMIT], check=True)
    actual = subprocess.check_output(["git", "-C", str(directory), "rev-parse", "HEAD"], text=True).strip()
    if actual != SOURCE_COMMIT:
        raise RuntimeError(f"推論コードの commit が一致しません: {actual}")


def main() -> None:
    """必要なファイルを取得し、検証後に導入完了を記録する。"""
    from huggingface_hub import snapshot_download

    DESTINATION.mkdir(parents=True, exist_ok=True)
    marker = DESTINATION / "installed.json"
    marker.unlink(missing_ok=True)
    source = DESTINATION / "source"
    base = DESTINATION / "base"
    adapter = DESTINATION / "adapter"
    install_source(source)
    snapshot_download(BASE_REPO, revision=BASE_COMMIT, local_dir=base,
                      ignore_patterns=["*.md", ".gitattributes"])
    snapshot_download(ADAPTER_REPO, revision=ADAPTER_COMMIT, local_dir=adapter,
                      allow_patterns=list(ADAPTER_FILES))
    for name in REQUIRED_BASE_FILES:
        if not (base / name).is_file():
            raise RuntimeError(f"ベースモデルの必須ファイルがありません: {name}")
    verify_adapter(adapter)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "-e", str(source)], check=True)
    (DESTINATION / "bundle.json").write_text(json.dumps(
        {"repo": BASE_REPO, "revision": BASE_COMMIT, "path": str(base.resolve())}, indent=2
    ) + "\n")
    marker.write_text(json.dumps({"source_commit": SOURCE_COMMIT, "base_commit": BASE_COMMIT,
                                  "adapter_commit": ADAPTER_COMMIT}, indent=2) + "\n")
    print(f"imajev-4b を {DESTINATION} にインストールしました")


if __name__ == "__main__":
    main()
