"""固定版の Qwen ベースと Kev アダプターを Release/Git 共通の分割 archive にする。"""

import gzip
import hashlib
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

BASE_REPO = "Qwen/Qwen3.5-4B-Base"
BASE_REVISION = "1001bb4d826a52d1f399e183466143f4da7b741b"
CHUNK_SIZE = 95 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
PARTS = ROOT / "model-parts"
ADAPTER_SHA256 = "bd8581d41b121f36a7c9e823603dd68051d8375c4ffa0fc6bf54ed50810ae151"
ARCHIVE_NAME = "model.tar.gz"


class SplitWriter:
    """圧縮済みの連続バイト列を固定サイズの Git 通常ファイルに書く。"""

    def __init__(self, directory: Path) -> None:
        """出力先と SHA-256 状態を初期化する。"""
        self.directory = directory
        self.index = 0
        self.current = None
        self.current_size = 0
        self.digest = hashlib.sha256()
        self.parts = []

    def writable(self) -> bool:
        """gzip の書き込み可能インターフェースを示す。"""
        return True

    def write(self, data: bytes) -> int:
        """バイト列を境界で分割しながら保存する。"""
        self.digest.update(data)
        offset = 0
        while offset < len(data):
            if self.current is None:
                name = f"{ARCHIVE_NAME}.part-{self.index:04d}"
                self.current = (self.directory / name).open("wb")
                self.parts.append(name)
                self.current_size = 0
                self.index += 1
            count = min(CHUNK_SIZE - self.current_size, len(data) - offset)
            self.current.write(data[offset : offset + count])
            self.current_size += count
            offset += count
            if self.current_size == CHUNK_SIZE:
                self.current.close()
                self.current = None
        return len(data)

    def flush(self) -> None:
        """現在の part をディスクへ反映する。"""
        if self.current is not None:
            self.current.flush()

    def close(self) -> None:
        """最後の part とチェックサム一覧を確定する。"""
        if self.current is not None:
            self.current.close()
        (self.directory / "model.sha256").write_text(f"{self.digest.hexdigest()}  {ARCHIVE_NAME}\n")
        (self.directory / "model.parts").write_text("\n".join(self.parts) + "\n")


def sha256(path: Path) -> str:
    """ファイルの SHA-256 を計算する。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(path: str, expected_size: int, expected_sha256: str | None, temp: Path) -> Path:
    """固定リビジョンのファイルを再開可能な curl で取得して検証する。"""
    url = f"https://huggingface.co/{BASE_REPO}/resolve/{BASE_REVISION}/{path}"
    if temp.exists() and temp.stat().st_size > expected_size:
        temp.unlink()
    subprocess.run(["curl", "-fL", "--retry", "6", "--retry-all-errors", "--continue-at", "-", "--output", str(temp), url], check=True)
    if temp.stat().st_size != expected_size:
        raise RuntimeError(f"{path}: size mismatch: {temp.stat().st_size} != {expected_size}")
    if expected_sha256 and sha256(temp) != expected_sha256:
        raise RuntimeError(f"{path}: SHA-256 mismatch")
    return temp


def add_file(archive: tarfile.TarFile, source: Path, destination: str) -> None:
    """再現可能なメタデータでファイルを archive に追加する。"""
    info = tarfile.TarInfo(destination)
    info.size = source.stat().st_size
    info.mtime = 0
    info.mode = 0o644
    with source.open("rb") as stream:
        archive.addfile(info, stream)


def fetch_adapter(directory: Path) -> Path:
    """Kev の固定 Release asset を取得し、SHA-256 を確認して展開する。"""
    subprocess.run(
        ["gh", "release", "download", "kev-family", "-R", "jaredpalmer/kev", "-p", "kev-4b.tar.gz", "-D", str(directory)],
        check=True,
    )
    source = directory / "kev-4b.tar.gz"
    if sha256(source) != ADAPTER_SHA256:
        raise RuntimeError("Kev-4B Release asset の SHA-256 が一致しません")
    with tarfile.open(source, "r:gz") as archive:
        archive.extractall(directory, filter="data")
    adapter = directory / "kev-4b"
    if not (adapter / "head.pt").is_file():
        raise RuntimeError("Kev アダプターに head.pt がありません")
    return adapter


def main() -> None:
    """ベースとアダプターを一つの検証可能な分割 archive にする。"""
    PARTS.mkdir(exist_ok=True)
    if list(PARTS.glob("*.part-*")):
        raise RuntimeError("model-parts に既存の part があります。別ディレクトリーへ退避してください")
    api_url = f"https://huggingface.co/api/models/{BASE_REPO}/tree/{BASE_REVISION}?recursive=true&expand=true"
    listing = subprocess.run(["curl", "-fsSL", api_url], check=True, capture_output=True).stdout
    files = [item for item in json.loads(listing) if item.get("type") == "file" and item["path"] != ".gitattributes"]
    needed = {"LICENSE", "config.json", "model.safetensors.index.json", "tokenizer.json", "tokenizer_config.json"}
    if not needed.issubset({item["path"] for item in files}):
        raise RuntimeError("ベースモデルに必須ファイルがありません")
    adapter_cache = Path(tempfile.mkdtemp(prefix="kev-adapter-", dir=ROOT.parent))
    adapter = fetch_adapter(adapter_cache)
    writer = SplitWriter(PARTS)
    temp = ROOT.parent / "base-download.tmp"
    try:
        with gzip.GzipFile(filename="", mode="wb", fileobj=writer, compresslevel=1, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for item in files:
                    name = item["path"]
                    print(f"base: {name}", flush=True)
                    lfs = item.get("lfs") or {}
                    source = download(name, item["size"], lfs.get("oid"), temp)
                    add_file(archive, source, f"model/base/{name}")
                    source.unlink()
                for source in sorted(adapter.iterdir()):
                    if source.is_file():
                        print(f"adapter: {source.name}", flush=True)
                        add_file(archive, source, f"model/adapter/{source.name}")
        writer.close()
    finally:
        temp.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(adapter_cache)
    print(f"parts={len(writer.parts)} sha256={writer.digest.hexdigest()}")


if __name__ == "__main__":
    main()
