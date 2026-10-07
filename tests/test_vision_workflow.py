"""画像モデル導入時の検証と評価集計をテストする。"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts import evaluate_model, install_vision_model


class VisionWorkflowTest(unittest.TestCase):
    """モデル破損と評価値の境界動作を検査する。"""

    def test_adapter_checksum_detects_corruption(self) -> None:
        """公開 manifest と異なる重みを拒否する。"""
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            names = [n for n in install_vision_model.ADAPTER_FILES if n not in ("SHA256SUMS", "README.md")]
            checksums = []
            for name in names:
                (directory / name).write_bytes(b"correct")
                checksums.append(f"{hashlib.sha256(b'correct').hexdigest()}  {name}")
            (directory / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
            install_vision_model.verify_adapter(directory)
            (directory / "adapter_model.safetensors").write_bytes(b"corrupt")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                install_vision_model.verify_adapter(directory)

    def test_eval_groups_image_and_text(self) -> None:
        """画像とテキストを分け、正答率と棄権率を集計する。"""
        rows = [
            {"images": ["photo.jpg"], "category": "photo", "gold": {"route": "damaged"}, "response": {"answers": {
                "route": {"type": "choice", "choice": "damaged", "probabilities": {"damaged": 0.9, "other": 0.1},
                          "unknown_probability": 0.1, "abstained": False}}}},
            {"images": [], "gold": {"visible": True}, "response": {"answers": {
                "visible": {"type": "noul", "noul": 0.2, "abstained": False}}}},
        ]
        metrics = evaluate_model.summarize(rows)
        self.assertEqual(metrics["photo/choice"]["accuracy"], 1)
        self.assertEqual(metrics["text/noul"]["accuracy"], 0)
        self.assertEqual(metrics["photo/choice"]["abstention_rate"], 0)

    def test_eval_rejects_path_escape(self) -> None:
        """事例 JSONL の外にある画像を読まない。"""
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "外"):
                evaluate_model.predict("http://127.0.0.1", {"request": {}, "images": ["../secret.png"]},
                                       Path(temp), None, 1)


if __name__ == "__main__":
    unittest.main()
