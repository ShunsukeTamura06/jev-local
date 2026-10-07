"""配布担当の環境で固定版の画像モデルを GitHub Release 用に梱包する。"""

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.install_vision_model import (
    ADAPTER_COMMIT, ADAPTER_FILES, ADAPTER_REPO, BASE_COMMIT, BASE_REPO,
    REQUIRED_BASE_FILES, SOURCE_COMMIT, sha256,
)

ARCHIVE_NAME = "imajev-model.tar.gz"
RELEASE_TAG = "imajev-4b-v1"
PART_SIZE = 1024 * 1024 * 1024


class SplitWriter:
    """連続した archive を Release 用の分割ファイルへ書き込む。"""

    def __init__(self, directory: Path, part_size: int = PART_SIZE) -> None:
        """出力先と分割サイズを設定する。

        Args:
            directory: 出力ディレクトリー。
            part_size: 一つの asset の最大バイト数。
        """
        if part_size <= 0:
            raise ValueError("分割サイズは正の整数にしてください")
        self.directory = directory
        self.part_size = part_size
        self.current = None
        self.size = 0
        self.digest = hashlib.sha256()
        self.parts: list[str] = []

    def write(self, data: bytes) -> int:
        """境界で分割しながら archive のバイト列を保存する。

        Args:
            data: 書き込むバイト列。

        Returns:
            保存したバイト数。
        """
        self.digest.update(data)
        offset = 0
        while offset < len(data):
            if self.current is None:
                name = f"{ARCHIVE_NAME}.part-{len(self.parts):04d}"
                self.current = (self.directory / name).open("wb")
                self.parts.append(name)
                self.size = 0
            count = min(self.part_size - self.size, len(data) - offset)
            self.current.write(data[offset:offset + count])
            self.size += count
            offset += count
            if self.size == self.part_size:
                self.current.close()
                self.current = None
        return len(data)

    def flush(self) -> None:
        """書き込み中の asset をディスクへ反映する。"""
        if self.current is not None:
            self.current.flush()

    def close(self) -> None:
        """書き込み中の asset を閉じる。"""
        if self.current is not None:
            self.current.close()
            self.current = None


def listing(repo: str, revision: str) -> list[dict]:
    """固定コミットのファイル一覧を取得する。

    Args:
        repo: Hugging Face のリポジトリー ID。
        revision: 固定コミット。

    Returns:
        通常ファイルのメタデータ一覧。
    """
    url = f"https://huggingface.co/api/models/{repo}/tree/{revision}?recursive=true&expand=true"
    result = subprocess.run(["curl", "-fsSL", "--retry", "6", url], check=True, capture_output=True)
    return sorted((item for item in json.loads(result.stdout) if item["type"] == "file"),
                  key=lambda item: item["path"])


def download(repo: str, revision: str, item: dict, destination: Path) -> None:
    """固定版ファイルを再開可能な方式で取得し、出典のハッシュを検証する。

    Args:
        repo: 配布元リポジトリー ID。
        revision: 固定コミット。
        item: ファイル名、サイズ、公開ハッシュ。
        destination: 一時ファイルの保存先。
    """
    expected = (item.get("lfs") or {}).get("oid")
    if destination.is_file() and destination.stat().st_size == item["size"] and expected:
        if sha256(destination) == expected:
            return
    if destination.exists() and destination.stat().st_size >= item["size"]:
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{item['path']}"
    subprocess.run(["curl", "-fsSL", "--retry", "6", "--retry-all-errors", "--connect-timeout", "30",
                    "--max-time", "1800", "--continue-at", "-", "--output", str(destination), url], check=True)
    if destination.stat().st_size != item["size"]:
        raise RuntimeError(f"配布元のサイズと一致しません: {item['path']}")
    if expected and sha256(destination) != expected:
        raise RuntimeError(f"配布元の SHA-256 と一致しません: {item['path']}")


def add_file(archive: tarfile.TarFile, path: Path, relative: str) -> dict:
    """再現可能なメタデータでファイルを archive へ追加する。

    Args:
        archive: 書き込み先の tar archive。
        path: 入力ファイル。
        relative: model/imajev 内の相対パス。

    Returns:
        固定 manifest に保存するハッシュとサイズ。
    """
    info = tarfile.TarInfo(f"model/imajev/{relative}")
    info.size = path.stat().st_size
    info.mode = 0o644
    info.mtime = 0
    with path.open("rb") as stream:
        archive.addfile(info, stream)
    return {"path": relative, "size": info.size, "sha256": sha256(path)}


def main() -> None:
    """配布元の重みを逐次取得し、分割 asset と固定 manifest を生成する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(path.name != ".downloads" for path in output.iterdir()):
        parser.error("出力先に既存の配布ファイルがあります。新しいディレクトリーを指定してください")
    base_files = [item for item in listing(BASE_REPO, BASE_COMMIT) if item["path"] != ".gitattributes"]
    adapter_files = [item for item in listing(ADAPTER_REPO, ADAPTER_COMMIT) if item["path"] in ADAPTER_FILES]
    if not set(REQUIRED_BASE_FILES).issubset({item["path"] for item in base_files}):
        raise RuntimeError("ベースモデルに必須ファイルがありません")
    if set(ADAPTER_FILES) != {item["path"] for item in adapter_files}:
        raise RuntimeError("アダプターに必須ファイルがありません")
    cache = output / ".downloads"
    temp = cache / "provenance.json"
    checksum_item = next(item for item in adapter_files if item["path"] == "SHA256SUMS")
    checksums_path = cache / "adapter" / "SHA256SUMS"
    download(ADAPTER_REPO, ADAPTER_COMMIT, checksum_item, checksums_path)
    adapter_checksums = {name.strip(): digest for digest, name in
                         (line.split(maxsplit=1) for line in checksums_path.read_text().splitlines())}
    provenance = {"source_commit": SOURCE_COMMIT, "base_repo": BASE_REPO, "base_commit": BASE_COMMIT,
                  "adapter_repo": ADAPTER_REPO, "adapter_commit": ADAPTER_COMMIT,
                  "license": "Apache-2.0", "weights_modified": False}
    writer = SplitWriter(output)
    files = []
    try:
        # 浮動小数の重みを再圧縮する CPU 時間を避け、gzip の無圧縮モードで逐次梱包する。
        with gzip.GzipFile(filename="", mode="wb", fileobj=writer, compresslevel=0, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for repo, revision, prefix, items in (
                    (BASE_REPO, BASE_COMMIT, "base", base_files),
                    (ADAPTER_REPO, ADAPTER_COMMIT, "adapter", adapter_files),
                ):
                    for item in items:
                        print(f"{prefix}: {item['path']} ({item['size']:,} bytes)", flush=True)
                        source = cache / prefix / item["path"]
                        download(repo, revision, item, source)
                        if prefix == "adapter" and item["path"] not in ("SHA256SUMS", "README.md"):
                            if sha256(source) != adapter_checksums.get(item["path"]):
                                raise RuntimeError(f"上流 SHA256SUMS と一致しません: {item['path']}")
                        files.append(add_file(archive, source, f"{prefix}/{item['path']}"))
                        if prefix == "base" and item["path"] == "LICENSE":
                            files.append(add_file(archive, source, "licenses/APACHE-2.0.txt"))
                        source.unlink()
                temp.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
                files.append(add_file(archive, temp, "provenance.json"))
        writer.close()
        manifest = {"schema_version": 1, "release_tag": RELEASE_TAG, **provenance,
                    "archive": {"name": ARCHIVE_NAME, "sha256": writer.digest.hexdigest()},
                    "parts": [{"name": name, "size": (output / name).stat().st_size,
                               "sha256": sha256(output / name)} for name in writer.parts],
                    "files": files}
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(f"完成: {len(writer.parts)} assets, archive SHA-256 {writer.digest.hexdigest()}", flush=True)
    finally:
        writer.close()
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
