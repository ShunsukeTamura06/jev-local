"""社内のラベル付き事例で decision API の精度を測定する。"""

import argparse
import base64
import json
import mimetypes
import math
import os
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path


def predict(url: str, case: dict, root: Path, api_key: str | None, timeout: float) -> dict:
    """一件の事例をローカル API に送信する。

    Args:
        url: /v1/systemone を含む URL。
        case: request、images、gold を含む事例。
        root: 画像パスの基準ディレクトリー。
        api_key: 設定されている場合の Bearer キー。
        timeout: 通信タイムアウト秒数。

    Returns:
        API の JSON 応答。
    """
    payload = dict(case["request"])
    images = []
    for name in case.get("images", []):
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError(f"画像パスが事例ディレクトリー外です: {name}")
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            raise ValueError(f"未対応の画像形式です: {name}")
        images.append(f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}")
    if images:
        payload["images"] = images
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"API が HTTP {error.code} を返しました: {error.read(500).decode(errors='replace')}") from error


def summarize(rows: list[dict]) -> dict:
    """質問型と画像有無ごとに正答率・校正誤差などを集計する。

    Args:
        rows: gold と API 回答の組。

    Returns:
        秘密の入力内容を含まない集計値。
    """
    groups = defaultdict(list)
    for row in rows:
        for qid, gold in row["gold"].items():
            answer = row["response"]["answers"][qid]
            kind = answer["type"]
            if kind == "choice":
                prediction = "__unknown__" if answer.get("abstained") else answer["choice"]
                unknown = answer.get("unknown_probability", 0)
                confidence = unknown if answer.get("abstained") else max(answer["probabilities"].values()) * (1 - unknown)
            elif kind == "noul":
                prediction = "__unknown__" if answer.get("abstained") else bool(answer["noul"] >= 0.5)
                confidence = answer.get("unknown_probability", 0) if answer.get("abstained") else max(
                    answer["noul"], 1 - answer["noul"]
                )
            elif kind == "score":
                prediction = "__unknown__" if answer.get("abstained") else round(answer["score"])
                confidence = None
            else:
                raise ValueError(f"未対応の質問型です: {kind}")
            category = row.get("category") or ("image" if row["images"] else "text")
            groups[(category, kind)].append(
                (prediction == gold, abs(answer["score"] - gold) if kind == "score" and gold != "__unknown__" else None,
                 bool(answer.get("abstained", False)), confidence)
            )
    report = {}
    for (modality, kind), values in sorted(groups.items()):
        n = len(values)
        confidences = [v for v in values if v[3] is not None]
        bins = [[] for _ in range(10)]
        for correct, _, _, confidence in confidences:
            bins[min(int(confidence * 10), 9)].append((correct, confidence))
        ece = sum(len(bucket) / len(confidences) * abs(
            sum(int(c) for c, _ in bucket) / len(bucket) - sum(p for _, p in bucket) / len(bucket)
        ) for bucket in bins if bucket) if confidences else None
        report[f"{modality}/{kind}"] = {
            "n": n, "accuracy": sum(int(v[0]) for v in values) / n,
            "mae": (sum(v[1] for v in values if v[1] is not None) /
                    sum(v[1] is not None for v in values)) if kind == "score" and
                   any(v[1] is not None for v in values) else None,
            "abstention_rate": sum(int(v[2]) for v in values) / n,
            "ece_10": ece,
        }
    return report


def main() -> None:
    """JSONL 事例を実行し、集計だけを JSON として保存する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8008/v1/systemone")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--api-key")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    rows = []
    with args.cases.open() as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            case = json.loads(line)
            try:
                response = predict(args.url, case, args.cases.parent,
                                   args.api_key or os.getenv("KEV_API_KEY"), args.timeout)
                rows.append({"gold": case["gold"], "images": case.get("images", []),
                             "category": case.get("category"), "response": response})
            except (OSError, ValueError, RuntimeError, KeyError) as error:
                raise RuntimeError(f"{args.cases}:{line_number}: {error}") from error
    if not rows:
        parser.error("事例がありません")
    latencies = sorted(float(row["response"]["usage"]["total_ms"]) for row in rows
                       if "total_ms" in row["response"].get("usage", {}))
    latency = {"n": len(latencies), "p50": latencies[math.ceil(len(latencies) * 0.5) - 1],
               "p95": latencies[math.ceil(len(latencies) * 0.95) - 1]} if latencies else None
    report = {"cases": len(rows), "metrics": summarize(rows), "latency_ms": latency}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
