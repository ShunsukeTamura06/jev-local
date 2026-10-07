"""GitHub 配布 archive の完全性と安全な展開を検証する。"""

import gzip
import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import install_vision_model as installer
from scripts import package_vision_model as packager
from scripts.package_vision_model import SplitWriter, add_file


def make_release(root: Path, unsafe_member: bool = False) -> tuple[dict, list[Path]]:
    """小さな正しいモデル archive を作成する。

    Args:
        root: テスト用の保存先。
        unsafe_member: manifest 外の不正パスを追加するか。

    Returns:
        配布 manifest と分割ファイル一覧。
    """
    model = root / "fixture"
    model.mkdir()
    entries = {f"base/{name}": b"{}\n" for name in installer.REQUIRED_BASE_FILES}
    entries.update({f"adapter/{name}": b"adapter fixture\n" for name in installer.ADAPTER_FILES})
    entries["adapter/SHA256SUMS"] = "".join(
        f"{hashlib.sha256(entries[f'adapter/{name}']).hexdigest()}  {name}\n"
        for name in installer.ADAPTER_FILES if name not in ("SHA256SUMS", "README.md")
    ).encode()
    assets = root / "assets"
    assets.mkdir()
    writer = SplitWriter(assets, part_size=256)
    files = []
    with gzip.GzipFile(filename="", mode="wb", fileobj=writer, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|") as archive:
            for name, data in entries.items():
                source = model / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(data)
                files.append(add_file(archive, source, name))
            if unsafe_member:
                info = tarfile.TarInfo("../escaped.txt")
                info.size = 0
                archive.addfile(info)
    writer.close()
    paths = [assets / name for name in writer.parts]
    manifest = {
        "schema_version": 1, "release_tag": "imajev-4b-v1",
        "source_commit": installer.SOURCE_COMMIT,
        "base_repo": installer.BASE_REPO, "base_commit": installer.BASE_COMMIT,
        "adapter_repo": installer.ADAPTER_REPO, "adapter_commit": installer.ADAPTER_COMMIT,
        "archive": {"sha256": writer.digest.hexdigest()},
        "parts": [{"name": path.name, "size": path.stat().st_size, "sha256": installer.sha256(path)}
                  for path in paths], "files": files,
    }
    return manifest, paths


class VisionReleaseTest(unittest.TestCase):
    """分割配布からの復元と破損・不正パスの拒否を確認する。"""

    def test_install_valid_release(self) -> None:
        """分割境界をまたぐ archive から全ファイルを復元する。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, paths = make_release(root)
            destination = root / "model" / "imajev"
            installer.install_weights(manifest, paths, destination)
            self.assertGreater(len(paths), 1)
            for item in manifest["files"]:
                self.assertEqual(installer.sha256(destination / item["path"]), item["sha256"])

    def test_corrupt_release_preserves_installed_model(self) -> None:
        """asset の破損時は既存モデルと完了マーカーを保持する。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, paths = make_release(root)
            destination = root / "installed"
            destination.mkdir()
            marker = destination / "installed.json"
            marker.write_text("previous model")
            paths[-1].write_bytes(b"corrupt")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                installer.install_weights(manifest, paths, destination)
            self.assertEqual(marker.read_text(), "previous model")

    def test_unsafe_archive_rejected(self) -> None:
        """正しい asset ハッシュでも manifest 外のパスを展開しない。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, paths = make_release(root, unsafe_member=True)
            with self.assertRaisesRegex(RuntimeError, "不正なファイル"):
                installer.install_weights(manifest, paths, root / "installed")
            self.assertFalse((root / "escaped.txt").exists())
            self.assertFalse((root / "installed").exists())

    def test_expanded_file_checksum_rejected(self) -> None:
        """archive が正常でもファイル別ハッシュの不一致を拒否する。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, paths = make_release(root)
            manifest["files"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "展開したファイル"):
                installer.install_weights(manifest, paths, root / "installed")
            self.assertFalse((root / "installed").exists())

    def test_missing_part_rejected(self) -> None:
        """asset 不足を展開前に報告する。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, paths = make_release(root)
            with self.assertRaisesRegex(ValueError, "不足"):
                installer.install_weights(manifest, paths[:-1], root / "installed")

    def test_cached_release_needs_no_network(self) -> None:
        """取得済みで検証が通る asset は再取得しない。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, paths = make_release(root)
            with patch.object(installer.subprocess, "run") as run:
                result = installer.download_parts(manifest, root / "assets", installer.GITHUB_REPO)
            self.assertEqual(result, paths)
            run.assert_not_called()

    def test_installed_weights_checked_before_reuse(self) -> None:
        """同一版を再利用でき、破損があれば再導入が必要と判定する。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, paths = make_release(root)
            destination = root / "installed"
            installer.install_weights(manifest, paths, destination)
            version = {key: manifest[key] for key in ("source_commit", "base_commit", "adapter_commit")}
            (destination / "installed.json").write_text(json.dumps(version))
            self.assertTrue(installer.weights_installed(manifest, destination))
            (destination / "adapter" / "adapter_model.safetensors").write_bytes(b"corrupt")
            self.assertFalse(installer.weights_installed(manifest, destination))

    def test_download_uses_github_only(self) -> None:
        """取得先を GitHub Release に限定し、サイズとハッシュを検証する。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, paths = make_release(root)
            fixtures = {path.name: path.read_bytes() for path in paths}
            urls = []

            def download(command: list[str], check: bool) -> None:
                """GitHub 取得をローカル fixture のコピーで置き換える。

                Args:
                    command: curl の引数。
                    check: 失敗時に例外を発生させる設定。
                """
                urls.append(command[-1])
                target = Path(command[command.index("--output") + 1])
                target.write_bytes(fixtures[target.name])

            with patch.object(installer.subprocess, "run", side_effect=download):
                result = installer.download_parts(manifest, root / "cache", installer.GITHUB_REPO)
            self.assertEqual(len(result), len(paths))
            self.assertTrue(all(url.startswith("https://github.com/ShunsukeTamura06/jev-local/releases/download/")
                                for url in urls))

    def test_local_assets_complete_install(self) -> None:
        """取得済み asset だけで重み・bundle・導入マーカーを生成する。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = make_release(root)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            destination = root / "model" / "imajev"
            with patch.object(installer, "RELEASE_MANIFEST", manifest_path), \
                 patch.object(installer, "DESTINATION", destination), \
                 patch.object(installer, "install_source") as source, \
                 patch.object(installer.subprocess, "run") as run, \
                 patch.object(installer, "download_parts") as download, \
                 patch.object(installer.sys, "argv", ["install", "--assets-dir", str(root / "assets")]):
                installer.main()
            download.assert_not_called()
            source.assert_called_once_with(destination / "source")
            self.assertEqual(json.loads((destination / "installed.json").read_text())["base_commit"],
                             installer.BASE_COMMIT)
            self.assertEqual(json.loads((destination / "bundle.json").read_text())["path"],
                             str((destination / "base").resolve()))
            self.assertIn("--no-build-isolation", run.call_args.args[0])

    def test_manifest_path_escape_rejected(self) -> None:
        """manifest の親ディレクトリー参照を拒否する。"""
        with tempfile.TemporaryDirectory() as temp:
            manifest, _ = make_release(Path(temp))
            manifest["files"].append({"path": "../escape", "size": 1, "sha256": "0" * 64})
            with self.assertRaisesRegex(ValueError, "パス"):
                installer.validate_manifest(manifest)

    def test_publisher_reuses_verified_source_weight(self) -> None:
        """梱包用に取得済みの重みを公開ハッシュの検証後に再利用する。"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "weight"
            payload = b"source weight fixture"
            path.write_bytes(payload)
            item = {"path": "weight.safetensors", "size": len(payload),
                    "lfs": {"oid": hashlib.sha256(payload).hexdigest()}}
            with patch.object(packager.subprocess, "run") as run:
                packager.download("Qwen/test", "fixed", item, path)
            run.assert_not_called()

    def test_publisher_rejects_wrong_source_weight(self) -> None:
        """配布元の期待ハッシュと違う重みを梱包しない。"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "weight"
            item = {"path": "weight.safetensors", "size": 3, "lfs": {"oid": "0" * 64}}
            with patch.object(packager.subprocess, "run", side_effect=lambda *args, **kwargs: path.write_bytes(b"bad")):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    packager.download("Qwen/test", "fixed", item, path)


if __name__ == "__main__":
    unittest.main()
