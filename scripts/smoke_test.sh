#!/usr/bin/env bash
set -euo pipefail
base_url="${1:-http://127.0.0.1:8008}"
cd "$(dirname "$0")/.."
[[ -x .venv/bin/python ]] || { echo '.venv がありません。' >&2; exit 1; }
.venv/bin/python - "$base_url" <<'PY'
"""画像経路と 3 種類の回答形式を検査する。"""
import base64
import json
import os
import struct
import sys
import urllib.request
import zlib

url = sys.argv[1]
headers = {"Content-Type": "application/json"}
if os.getenv("KEV_API_KEY"):
    headers["Authorization"] = "Bearer " + os.environ["KEV_API_KEY"]


def call(path: str, payload: dict | None = None) -> dict:
    """ローカル API を呼び出す。

    Args:
        path: API パス。
        payload: 送信する JSON。省略時は GET。

    Returns:
        JSON 応答。
    """
    data = None if payload is None else json.dumps(payload).encode()
    with urllib.request.urlopen(urllib.request.Request(url + path, data=data, headers=headers), timeout=120) as response:
        return json.load(response)


def chunk(kind: bytes, data: bytes) -> bytes:
    """PNG チャンクを組み立てる。

    Args:
        kind: チャンクの識別子。
        data: チャンクの内容。

    Returns:
        長さと CRC を含む PNG チャンク。
    """
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)


# 画像経路を通すための小さな合成画像。意味上の精度はラベル付き事例で測定する。
pixels = b"".join(b"\x00" + b"\xff\xff\xff" * 64 for _ in range(64))
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
model = call("/v1/models")
assert model["model"] == "imajev-4b" and model["backend"] == "torch", model
request = {
    "request_id": "smoke_1", "state": "A customer attached an image.",
    "images": ["data:image/png;base64," + base64.b64encode(png).decode()],
    "fields": [
        {"id": "route", "type": "choice", "question": "Which team should review it?", "options": [
            {"value": "billing", "description": "Payment issue"}, {"value": "other", "description": "Other issue"}]},
        {"id": "visible", "type": "boolean", "question": "Is an image visible?"},
        {"id": "urgency", "type": "ordinal", "question": "Rate urgency.", "levels": [
            {"value": 0, "description": "low"}, {"value": 1, "description": "medium"}, {"value": 2, "description": "high"}]},
    ],
}
body = call("/v1/decisions", request)
answers = body["results"]
assert body["request_id"] == request["request_id"], body
assert set(answers) == {field["id"] for field in request["fields"]}, body
for qid, result in answers.items():
    assert result["status"] in ("answered", "abstained"), body
    assert abs(sum(result["scores"].values()) - 1) < 1e-6, body
    assert "__unknown__" in result["scores"] and "raw_logits" not in result, body
    if result["status"] == "abstained":
        assert result["value"] is None and result["reason"] == "insufficient_evidence", body
    elif qid == "route":
        assert result["value"] in ("billing", "other"), body
    elif qid == "visible":
        assert type(result["value"]) is bool, body
    else:
        assert result["value"] in (0, 1, 2), body
assert len(body["usage"]["images"]) == 1, body
print(json.dumps(answers, ensure_ascii=False, indent=2))
PY
