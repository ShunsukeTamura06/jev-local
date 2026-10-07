"""GitHub Release から固定版 imajev-4b の重みを検証して配置する。"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO

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
RELEASE_MANIFEST = ROOT / "models" / "imajev-4b-v1.json"
GITHUB_REPO = "ShunsukeTamura06/jev-local"


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


class PartReader:
    """分割 asset を結合ファイルを作らず順番に読み込む。"""

    def __init__(self, paths: list[Path]) -> None:
        """入力 asset の順番を設定する。

        Args:
            paths: manifest 順の分割ファイル。
        """
        self.paths = iter(paths)
        self.current: BinaryIO | None = None

    def read(self, size: int) -> bytes:
        """指定サイズ以下の連続バイト列を読む。

        Args:
            size: 読み込む最大バイト数。

        Returns:
            分割境界をまたいだバイト列。
        """
        if size < 0:
            raise ValueError("archive 全体のメモリ読み込みは許可しません")
        data = bytearray()
        while len(data) < size:
            if self.current is None:
                path = next(self.paths, None)
                if path is None:
                    break
                self.current = path.open("rb")
            block = self.current.read(size - len(data))
            if not block:
                self.current.close()
                self.current = None
            else:
                data.extend(block)
        return bytes(data)

    def close(self) -> None:
        """読み込み中の asset を閉じる。"""
        if self.current is not None:
            self.current.close()
            self.current = None


def validate_manifest(manifest: dict) -> None:
    """固定版情報と asset 名を検証する。

    Args:
        manifest: リポジトリーに同梱した配布 manifest。
    """
    expected = {"schema_version": 1, "source_commit": SOURCE_COMMIT,
                "base_repo": BASE_REPO, "base_commit": BASE_COMMIT,
                "adapter_repo": ADAPTER_REPO, "adapter_commit": ADAPTER_COMMIT}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("モデル manifest の固定版が一致しません")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", manifest.get("release_tag", "")):
        raise ValueError("Release tag が不正です")
    parts = manifest.get("parts", [])
    if not parts:
        raise ValueError("manifest に分割 asset がありません")
    for index, part in enumerate(parts):
        if part.get("name") != f"imajev-model.tar.gz.part-{index:04d}":
            raise ValueError("分割 asset の名前または順序が不正です")
    files = manifest.get("files", [])
    names = [item.get("path", "") for item in files]
    if len(names) != len(set(names)):
        raise ValueError("manifest のファイル名が重複しています")
    required = {f"base/{name}" for name in REQUIRED_BASE_FILES} | {f"adapter/{name}" for name in ADAPTER_FILES}
    if not required.issubset(names):
        raise ValueError("manifest に必須モデルファイルがありません")
    for name in names:
        path = Path(name)
        if not path.parts or path.is_absolute() or ".." in path.parts or name != path.as_posix() or "\\" in name:
            raise ValueError(f"manifest のファイルパスが不正です: {name}")
        if path.parts[0] not in ("base", "adapter", "licenses", "provenance.json"):
            raise ValueError(f"manifest のファイルパスが不正です: {name}")
    for item in parts + files:
        if not isinstance(item.get("size"), int) or item["size"] <= 0:
            raise ValueError("manifest のサイズが不正です")
        if not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
            raise ValueError("manifest の SHA-256 が不正です")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("archive", {}).get("sha256", "")):
        raise ValueError("archive の SHA-256 が不正です")


def verify_part(path: Path, part: dict) -> bool:
    """asset のサイズと固定ハッシュが一致するか確認する。

    Args:
        path: 保存済み asset。
        part: manifest の期待値。

    Returns:
        サイズと SHA-256 が一致する場合は True。
    """
    return path.is_file() and path.stat().st_size == part["size"] and sha256(path) == part["sha256"]


def weights_installed(manifest: dict, destination: Path) -> bool:
    """固定版の導入マーカーとモデルファイルの完全性を確認する。

    Args:
        manifest: 固定配布 manifest。
        destination: インストール済みモデルの保存先。

    Returns:
        同一版の全ファイルが正常な場合は True。
    """
    marker = destination / "installed.json"
    if not marker.is_file():
        return False
    try:
        version = json.loads(marker.read_text())
    except (OSError, ValueError):
        return False
    expected = {key: manifest[key] for key in ("source_commit", "base_commit", "adapter_commit")}
    return version == expected and all(verify_part(destination / item["path"], item)
                                       for item in manifest["files"])


def download_parts(manifest: dict, cache: Path, repo: str) -> list[Path]:
    """GitHub Release の asset を再開可能な方式で取得する。

    Args:
        manifest: 固定配布 manifest。
        cache: 再試行用の保存先。
        repo: GitHub の owner/repository。

    Returns:
        検証済み asset のパス一覧。
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub リポジトリー ID が不正です")
    cache.mkdir(parents=True, exist_ok=True)
    paths = []
    for part in manifest["parts"]:
        path = cache / part["name"]
        if not verify_part(path, part):
            if path.exists() and path.stat().st_size >= part["size"]:
                path.unlink()
            url = f"https://github.com/{repo}/releases/download/{manifest['release_tag']}/{part['name']}"
            print(f"GitHub から取得: {part['name']}", flush=True)
            subprocess.run(["curl", "-fsSL", "--retry", "6", "--retry-all-errors", "--connect-timeout", "30",
                            "--max-time", "1800", "--continue-at", "-", "--output", str(path), url], check=True)
            if not verify_part(path, part):
                path.unlink(missing_ok=True)
                raise RuntimeError(f"Release asset の SHA-256 またはサイズが一致しません: {part['name']}")
        paths.append(path)
    return paths


def install_weights(manifest: dict, paths: list[Path], destination: Path) -> None:
    """検証済み分割 archive を安全に展開してモデルを置き換える。

    Args:
        manifest: 固定配布 manifest。
        paths: manifest 順の分割 asset。
        destination: model/imajev の保存先。
    """
    validate_manifest(manifest)
    if len(paths) != len(manifest["parts"]):
        raise ValueError("分割 asset が不足しています")
    digest = hashlib.sha256()
    for path, part in zip(paths, manifest["parts"]):
        if not verify_part(path, part):
            raise RuntimeError(f"分割 asset の SHA-256 またはサイズが一致しません: {path.name}")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
    if digest.hexdigest() != manifest["archive"]["sha256"]:
        raise RuntimeError("結合 archive の SHA-256 が一致しません")
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected_files = {f"model/imajev/{item['path']}": item for item in manifest["files"]}
    with tempfile.TemporaryDirectory(prefix=".imajev-extract-", dir=destination.parent) as temp:
        staging = Path(temp)
        reader = PartReader(paths)
        seen = set()
        try:
            with tarfile.open(fileobj=reader, mode="r|gz") as archive:
                for member in archive:
                    item = expected_files.get(member.name)
                    if item is None or not member.isfile() or member.size != item["size"] or member.name in seen:
                        raise RuntimeError(f"archive に不正なファイルがあります: {member.name}")
                    archive.extract(member, staging, filter="data")
                    seen.add(member.name)
        finally:
            reader.close()
        if seen != set(expected_files):
            raise RuntimeError("archive の必須ファイルが不足しています")
        model = staging / "model" / "imajev"
        for item in manifest["files"]:
            if sha256(model / item["path"]) != item["sha256"]:
                raise RuntimeError(f"展開したファイルの SHA-256 が一致しません: {item['path']}")
        verify_adapter(model / "adapter")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "installed.json").unlink(missing_ok=True)
        for child in model.iterdir():
            target = destination / child.name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            os.replace(child, target)


def main() -> None:
    """GitHub から重みと推論コードを取得し、導入完了を記録する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=GITHUB_REPO)
    parser.add_argument("--assets-dir", type=Path, help="取得済み Release asset のディレクトリー")
    parser.add_argument("--force", action="store_true", help="正常な導入済み重みも再取得する")
    args = parser.parse_args()
    if not RELEASE_MANIFEST.is_file():
        parser.error("固定 Release manifest がありません")
    manifest = json.loads(RELEASE_MANIFEST.read_text())
    validate_manifest(manifest)
    cache = DESTINATION.parent / ".imajev-download"
    if not args.force and weights_installed(manifest, DESTINATION):
        print("同じ固定版のモデル重みを検証しました。再取得を省略します。", flush=True)
    else:
        if args.assets_dir:
            paths = [args.assets_dir / part["name"] for part in manifest["parts"]]
        else:
            paths = download_parts(manifest, cache, args.repo)
        install_weights(manifest, paths, DESTINATION)
    marker = DESTINATION / "installed.json"
    source = DESTINATION / "source"
    base = DESTINATION / "base"
    adapter = DESTINATION / "adapter"
    install_source(source)
    for name in REQUIRED_BASE_FILES:
        if not (base / name).is_file():
            raise RuntimeError(f"ベースモデルの必須ファイルがありません: {name}")
    verify_adapter(adapter)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", str(source)], check=True)
    (DESTINATION / "bundle.json").write_text(json.dumps(
        {"repo": BASE_REPO, "revision": BASE_COMMIT, "path": str(base.resolve())}, indent=2
    ) + "\n")
    marker.write_text(json.dumps({"source_commit": SOURCE_COMMIT, "base_commit": BASE_COMMIT,
                                  "adapter_commit": ADAPTER_COMMIT}, indent=2) + "\n")
    if not args.assets_dir and cache.is_dir():
        shutil.rmtree(cache)
    print(f"imajev-4b を {DESTINATION} にインストールしました")


if __name__ == "__main__":
    main()
