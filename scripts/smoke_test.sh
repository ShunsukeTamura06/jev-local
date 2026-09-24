#!/usr/bin/env bash
set -euo pipefail
base_url="${1:-http://127.0.0.1:8008}"
auth=()
if [[ -n "${KEV_API_KEY:-}" ]]; then auth=(-H "Authorization: Bearer ${KEV_API_KEY}"); fi
curl -fsS "${auth[@]}" "$base_url/v1/models" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["models"][0]["device"] == "cuda", d'
curl -fsS "${auth[@]}" -H 'Content-Type: application/json' \
  -d '{"model":"kev-latest","state":"The customer requested a refund for a duplicate charge.","questions":{"route":{"type":"choice","instructions":"Choose the correct team.","criteria":{"billing":"Handles payment issues","shipping":"Handles deliveries"}},"needs_billing":{"type":"noul","instructions":"Is this a billing issue?"},"urgency":{"type":"score","instructions":"Rate urgency.","criteria":["low","medium","high"]}}}' \
  "$base_url/v1/systemone" | python3 -c 'import json,sys; a=json.load(sys.stdin)["answers"]; assert a["route"]["type"] == "choice"; assert set(a["route"]["probabilities"]) == {"billing","shipping"}; assert a["needs_billing"]["type"] == "noul"; assert 0 <= a["needs_billing"]["noul"] <= 1; assert a["urgency"]["type"] == "score"; assert 0 <= a["urgency"]["score"] <= 2; print(json.dumps(a,ensure_ascii=False,indent=2))'
