"""정답 셋(`golden/sp-golden-1.jsonl`)의 정답 호출을 지금 서버에서 돌려 본다 — 읽기만.

    PLATFORM_API_BASE=http://127.0.0.1:8041 SP_TOKEN=<토큰> \\
        python mcp_server/eval/golden_probe.py [g01,g02]

MCP 도구 함수를 그대로 부른다(도구가 하는 HTTP 변환까지 같게). `{"$ref": "<타입>:<이름>"}`
은 이름 풀이로 id 를 넣고, 하나로 안 풀리면 멈춘다. 문항마다 결과를 한 줄로 찍는다 — 채점이
아니라 「정답 호출이 돌고 무엇을 내나」.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import server  # noqa: E402

SET = HERE / "golden" / "sp-golden-1.jsonl"


def _ctx(token: str) -> Any:
    request = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


async def _fill(ctx: Any, value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$ref"}:
        type_slug, name = str(value["$ref"]).split(":", 1)
        got = await server.object_resolve(ctx, type_slug, name)
        if got.get("match") != "exact":
            raise SystemExit(f"$ref {value['$ref']} 이 하나로 안 풀립니다: {got.get('match')}")
        return got["object"]["id"]
    if isinstance(value, dict):
        return {key: await _fill(ctx, one) for key, one in value.items()}
    if isinstance(value, list):
        return [await _fill(ctx, one) for one in value]
    return value


async def main() -> None:
    token = os.environ.get("SP_TOKEN", "").strip()
    if not token:
        raise SystemExit("SP_TOKEN(그 서버의 개인 토큰 · 접근 토큰)을 주세요.")
    ctx = _ctx(token)
    only = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
    for line in SET.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if only and item["id"] not in only:
            continue
        args = await _fill(ctx, item["gold"]["args"])
        out = await getattr(server, item["gold"]["tool"])(ctx, **args)
        said = json.dumps(out, ensure_ascii=False, default=str)
        print(f"{item['id']} {item['q']}\n  {said[:400]}")


if __name__ == "__main__":
    asyncio.run(main())
