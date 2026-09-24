#!/usr/bin/env bash
# 연결 점검 — 수신 코드를 붙이기 전에 세 가지를 확인합니다.
set -u
BASE="${SP_BASE:-{BASE}}"
TOKEN="${SP_TOKEN:-}"
[ -n "$TOKEN" ] || { echo "SP_TOKEN 환경변수를 지정하세요."; exit 1; }

echo "1. 인증 없이 호출하면 거부되는가 (401 이 정상)"
curl -s -o /dev/null -w "   HTTP %{http_code}\n" "$BASE/core"

echo "2. 카탈로그를 읽을 수 있는가"
curl -s "$BASE/core" -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('   시스템', d['system'], '· 공개 타입', [t['slug'] for t in d['types']])"

echo "3. 첫 페이지를 받을 수 있는가"
FIRST=$(curl -s "$BASE/core" -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys; print(json.load(sys.stdin)['types'][0]['slug'])")
curl -s "$BASE/core/$FIRST?limit=2" -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('  ', d['type_slug'], '· 행', len(d['items']), '· 다음 페이지', bool(d['next']))"
