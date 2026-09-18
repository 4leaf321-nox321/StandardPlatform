"""StandardPlatform MCP 서버 — Claude 가 온톨로지를 읽고 채우게 하는 도구.

백엔드(FastAPI)와 의존성이 충돌(mcp ↔ starlette/pydantic)하므로 **별도 프로세스·
별도 venv** 로 돌리고, 백엔드와는 **REST API** 로만 통신한다. 사용자의 개인 액세스
토큰(Authorization)을 그대로 백엔드에 전달해 **그 토큰의 권한·범위**로 동작한다
(만능 토큰 X). 검증도 권한도 서버가 한다 — 여기에 규칙을 두면 「MCP 로는 되는데
화면에서는 안 되는」 상태가 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다.

## 왜 도구가 타입마다 있지 않은가

타입마다 도구를 만들면 타입 20개에 도구가 80개가 되고, **도구 목록이 길어질수록
모델은 엉뚱한 것을 고른다.** 도구는 몇 개로 고정하고 `ontology_schema` 하나가
「지금 이 설치에 무엇이 있고 각 타입이 무엇을 받는가」 를 말한다 — **동적인 것은
도구가 아니라 스키마다.**

실행:
    PLATFORM_API_BASE=http://localhost:8040 \
    ./venv/bin/python server.py          # streamable-http, 기본 127.0.0.1:8032/mcp

Claude Code 등록(사용자별 토큰):
    claude mcp add --transport http standardplatform http://<host>:8042/mcp \
      --header "Authorization: Bearer <내 토큰>"
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import re
import time
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

API_BASE = os.environ.get("PLATFORM_API_BASE", "http://localhost:8040").rstrip("/")

# 기본은 SSE(streamable-http 스트림) 응답. 다만 중간에 SSE 를 버퍼링하는 프록시/
# VPN/보안장비가 끼면 initialize 응답의 첫 바이트가 클라이언트까지 도달하지 못해
# "무응답 → 타임아웃"이 난다(스트림은 끝나지 않으니 프록시가 붙잡고 안 흘려보냄).
# MCP_JSON_RESPONSE=1 이면 응답을 **단발 JSON**(Content-Length 완결)으로 돌려
# 그런 프록시를 통과시킨다. 대가로 처리 중 서버→클라이언트 스트리밍 메시지(진행률/
# 로그/재개)를 포기하지만, 이 서버는 그 기능들을 쓰지 않으므로 실질 손실이 없다.
_JSON_RESPONSE = os.environ.get("MCP_JSON_RESPONSE") == "1"

mcp = FastMCP(
    # 번들 하나로 여러 플랫폼을 띄우므로 이름은 설치(유닛의 APP_SLUG)에서 온다.
    os.environ.get("APP_SLUG", "standardplatform"),
    json_response=_JSON_RESPONSE,
    instructions=(
        "이 설치의 온톨로지를 읽고 쓴다. **`get_guide` 와 `ontology_schema` 를 먼저 "
        "부른다** — 무엇을 만들 수 있고 각 타입이 어떤 값을 받는지가 거기 다 있다.\n"
        "\n"
        "하려는 일 → 부를 것:\n"
        "  이름으로 무언가를 가리킨다      object_resolve   ← 참조·관계·질문의 첫 걸음\n"
        "  그 타입에 무엇이 있나           objects_list\n"
        "  몇 건인가 · 어떻게 갈리나       objects_summary  (세려고 전부 받지 마라)\n"
        "  이 객체의 모든 것               object_get · object_references · object_rollup\n"
        "  여러 타입을 건너뛰는 물음       rdf_query (SPARQL)\n"
        "  정의를 바꾼다                   ontology_import (apply=false 로 먼저)\n"
        "\n"
        "지켜야 할 셋:\n"
        "1. **이름은 해소하고 쓴다.** `object_resolve` 가 `candidates` 를 주면 고르지 "
        "말고 사람에게 묻는다 — 목록의 첫 줄을 집으면 틀린 줄도 집힌다.\n"
        "2. **0건은 「없다」 가 아니다.** 목록이 0건이면 응답에 `diagnosis` 가 붙는다 "
        "— 안 채운 타입인지, 부서 밖이라 안 보이는지, 조건이 좁은지 거기 적혀 있다. "
        "그것을 읽기 전에 「없습니다」 라고 답하지 마라.\n"
        "3. **정의를 바꿀 때는 미리 보기 먼저.** `ontology_import` 를 기본값"
        "(apply=false)으로 불러 계획과 경고를 사람에게 보여 주고, 판단을 받은 뒤에 "
        "apply=true 로 부른다."
    ),
)

#: 시험이 갈아 끼우는 자리 — 진짜 앱(ASGI)이나 가짜 응답을 여기로 붙인다.
#: 운영에서는 None(실제 네트워크).
_TRANSPORT: httpx.AsyncBaseTransport | None = None

#: 자취를 남길 파일(JSONL). 비어 있으면 **아무것도 안 남긴다** — 기본은 끔이다.
#: 켜면 도구 한 번이 한 줄이 되고, `eval/score.py` 가 그 줄들을 점수로 바꾼다.
_TRACE_PATH = os.environ.get("MCP_TRACE_FILE")


def _signal(got: Any) -> dict[str, Any]:
    """자취에 남길 **모양만.** 값은 안 남긴다.

    자취는 「AI 가 어디서 헤맸나」 를 보려고 남기는 것이지 데이터를 모으려는 게
    아니다. 객체 이름·속성 값이 파일에 쌓이면 그 파일 자체가 유출 경로가 된다 —
    그래서 남기는 것은 **수와 판정뿐**이다(몇 건인가, exact 인가, 왜 0건인가).
    """
    out: dict[str, Any] = {}
    if not isinstance(got, dict):
        return {"outcome": "ok"}
    if "error" in got:
        # 코드만 — 문구에는 객체 이름이 들어간다.
        head = str(got["error"])
        out["outcome"] = "error"
        out["code"] = head[1 : head.find("]")] if head.startswith("[") else "?"
        return out
    if "total" in got:
        out["total"] = got["total"]
        out["outcome"] = "empty" if got["total"] == 0 else "ok"
        found = got.get("diagnosis")
        if isinstance(found, dict):
            out["reason"] = found.get("reason")
        return out
    if "match" in got:
        out["outcome"] = "ok"
        out["match"] = got["match"]
        return out
    out["outcome"] = "ok"
    return out


def _traced(fn: Any) -> Any:
    """도구 한 번 = 한 줄. `MCP_TRACE_FILE` 이 없으면 **감싸지도 않는다.**"""
    if not _TRACE_PATH:
        return fn

    @functools.wraps(fn)
    async def inner(ctx: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            got = await fn(ctx, *args, **kwargs)
        except Exception as caught:
            _write_trace(fn, started, {"outcome": "raised", "code": type(caught).__name__})
            raise
        _write_trace(fn, started, _signal(got))
        return got

    return inner


def _write_trace(fn: Any, started: float, signal: dict[str, Any]) -> None:
    line = {
        "ts": time.time(),
        "tool": fn.__name__,
        "ms": round((time.monotonic() - started) * 1000),
        **signal,
    }
    with contextlib.suppress(OSError), open(str(_TRACE_PATH), "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def tool() -> Any:
    """`@tool()` 과 같되 자취를 남긴다 — 등록과 측정을 한 자리에서 정한다.

    도구마다 따로 감싸면 새 도구를 넣은 사람이 그 한 줄을 잊고, 그러면 **그 도구만
    측정에서 빠진다** — 빠진 줄은 「안 쓴 도구」 처럼 보인다.
    """

    def wrap(fn: Any) -> Any:
        return mcp.tool()(_traced(fn))

    return wrap


def _forward_headers(ctx: Context) -> dict[str, str]:
    """들어온 MCP HTTP 요청의 인증 헤더를 백엔드로 전달.

    MCP 는 **사용자의 토큰으로** 동작한다. 백엔드가 토큰 이름을 감사 기록에 남기므로
    사람이 한 건지 기계가 한 건지는 거기서 갈린다."""
    headers: dict[str, str] = {}
    req = getattr(getattr(ctx, "request_context", None), "request", None)
    if req is not None:
        v = req.headers.get("authorization")
        if v:
            # 백엔드가 기대하는 표기로.
            headers["Authorization"] = v
    return headers


def _unwrap(r: httpx.Response) -> Any:
    """백엔드 응답 언래핑. 오류 봉투 {error: {code, message, details}} 면 {error,...}."""
    if r.status_code < 400 and not r.content:
        # 돌려줄 게 없는 성공(삭제 등). 그대로 흘리면 도구 결과가 **빈 문자열**이라
        # 모델은 성공했는지 알 수 없다 — 조용한 무동작과 구분이 안 된다.
        return {"ok": True, "message": "완료"}
    try:
        body = r.json()
    except ValueError:
        return {"error": f"HTTP {r.status_code} (non-JSON)", "status": r.status_code}
    if r.status_code >= 400:
        # **서버의 말을 그대로 전한다.** 오류 문구에 무엇을 고쳐야 하는지가 적혀
        # 있고, 여기서 고쳐 쓰면 그것을 잃는다.
        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            return {"error": f"HTTP {r.status_code}", "status": r.status_code}
        out: dict[str, Any] = {
            "error": f"[{error.get('code') or r.status_code}] {error.get('message') or ''}"
        }
        if error.get("details"):
            out["details"] = error["details"]
        return out
    return body


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE, timeout=timeout, transport=_TRANSPORT)


async def _get(
    ctx: Context,
    path: str,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
) -> Any:
    async with _client(60) as client:
        return _unwrap(await client.get(path, params=params, headers=_forward_headers(ctx)))


async def _post(
    ctx: Context,
    path: str,
    json_body: Any,
    params: dict[str, Any] | None = None,
) -> Any:
    async with _client(120) as client:
        return _unwrap(
            await client.post(
                path, json=json_body, params=params, headers=_forward_headers(ctx)
            )
        )


async def _patch(ctx: Context, path: str, json_body: Any) -> Any:
    async with _client(120) as client:
        return _unwrap(await client.patch(path, json=json_body, headers=_forward_headers(ctx)))


# --------------------------------------------------------------------------- #
# 사용 가이드 — **서버가 쥔다.** 로컬 스킬에 본문을 두면 사람마다 복사 시점이
# 달라 낡는다. 로컬엔 짧은 스텁만 두고 본문은 여기서 읽어 준다.
# --------------------------------------------------------------------------- #
_GUIDE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guide", "GUIDE.md")
_GUIDE_TOPICS = (
    "overview",
    "modeling",
    "schema",
    "find",
    "objects",
    "bulk",
    "relations",
    "sparql",
)


def _guide_sections() -> tuple[str, dict[str, str]]:
    """GUIDE.md 를 `<!--@ 주제 -->` 로 갈라 {주제: 본문}. 매 호출마다 읽는다 —
    파일 하나라 비용이 무시할 만하고, 서버 재시작 없이 가이드를 고칠 수 있다."""
    try:
        with open(_GUIDE_PATH, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return "?", {}
    version = "?"
    for line in raw.split("\n")[:20]:
        if line.startswith("GUIDE_VERSION:"):
            version = line.split(":", 1)[1].strip()
            break
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for line in raw.split("\n"):
        m = re.match(r"<!--@\s*(\w+)\s*-->", line.strip())
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return version, {k: "\n".join(v).strip() for k, v in out.items()}


@tool()
async def get_guide(ctx: Context, topic: str | None = None) -> dict[str, Any]:
    """**StandardPlatform 작업을 시작하기 전에 먼저 부른다.** 무엇을 어떤 순서로
    쓸지, 정의를 바꿀 때 무엇을 조심할지 이 가이드가 정한다(서버가 최신본을 쥔다).

    `topic` 없이 부르면 **overview** — "하려는 일 → 어떤 도구" 표와 기본 습관.
    대개 이것만으로 충분하고, 세부가 필요하면 그때 주제를 지정한다:
      - `modeling` 무엇을 타입·속성·관계로 만드나 — 원천을 정제하기 전에
      - `schema` 정의 읽기·바꾸기(미리 보기 → 적용)
      - `find` 찾기·거르기·이력·참조·품질
      - `objects` 객체 하나씩 만들고 고치기
      - `bulk` 여러 행 한 번에(upsert)
      - `relations` 객체 잇기(근거)
      - `sparql` 여러 타입을 건너뛰어 잇는 물음 — 질의어로

    한 번에 다 받지 마라 — 필요한 주제만 받는 게 싸다."""
    version, secs = _guide_sections()
    if not secs:
        return {
            "error": "가이드를 읽을 수 없습니다(서버 설치 문제). "
            "도구 설명만으로 진행하되 사용자에게 알리세요."
        }
    if topic:
        key = topic.strip().lower()
        if key not in secs:
            return {"error": f"그런 주제가 없습니다: {topic}", "topics": sorted(secs.keys())}
        return {"guide_version": version, "topic": key, "content": secs[key]}
    return {
        "guide_version": version,
        "topic": "overview",
        "content": secs.get("overview", ""),
        "more_topics": (
            [t for t in _GUIDE_TOPICS if t in secs and t != "overview"]
            # 가이드에 새 주제가 늘어도 알려준다 — 튜플은 **표시 순서**일 뿐,
            # 진실은 GUIDE.md 다(가이드를 서버가 쥐기로 한 이유).
            + sorted(t for t in secs if t not in _GUIDE_TOPICS and t != "overview")
        ),
        "note": "세부가 필요하면 get_guide(topic=...) 로 그 주제만 받아라.",
    }


# --------------------------------------------------------------------------- #
# 정의
# --------------------------------------------------------------------------- #
@tool()
async def ontology_schema(ctx: Context) -> Any:
    """이 설치의 **정의 전부** — 묶음·타입·속성·관계 종류.

    무엇을 만들 수 있고 각 타입이 어떤 값을 받는지가 여기 다 있다. **다른 도구를
    부르기 전에 이것부터 읽는다.**"""
    return await _get(ctx, "/api/ontology/schema")


@tool()
async def ontology_import(
    ctx: Context,
    schema: dict[str, Any],
    apply: bool = False,
) -> Any:
    """정의를 통째로 **한 트랜잭션으로** 적용한다.

    `apply=False`(기본)면 **아무것도 안 바꾸고** 계획만 돌려준다 — 무엇이 새로
    생기고, 무엇이 바뀌고, **무엇을 조용히 잃는지**(경고). 사람이 그 계획을 읽고
    판단할 자리다.

    더하고 고치기만 한다. **스키마에 없다고 지우지 않는다.**"""
    return await _post(
        ctx,
        "/api/ontology/import",
        schema,
        params={"dry_run": "false" if apply else "true"},
    )


# --------------------------------------------------------------------------- #
# 객체
# --------------------------------------------------------------------------- #
@tool()
async def objects_list(
    ctx: Context,
    type_slug: str,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    properties: dict[str, str] | None = None,
    conditions: list[dict[str, str]] | None = None,
    status: str | None = None,
) -> Any:
    """그 타입의 객체 목록 — 화면의 목록과 **같은 거르기**다.

    - `q`: 이름·식별자·검색 속성.
    - `properties`: `{"grade": "A"}` 처럼 속성 키로 「같음」 거르기(짧은 꼴).
    - `conditions`: `[{"field": "power", "op": "gte", "value": "10"}, ...]` — 칸끼리 AND,
      같은 칸에 여럿이면 그 안은 OR. 연산은 칸의 종류가 정한다:
      숫자·날짜 `eq ne gt gte lt lte in` · 글자 `eq ne contains starts in` ·
      선택·참조 `eq ne in` · 참/거짓 `eq` · 모두 `empty notempty`.
      `in` 의 값은 `|` 로 잇는다(`"A|B"`). `field` 는 속성 키 또는 `label`·`key`·`status`.
      **다른 타입의 칸**도 된다 — `ref.vendor.country`(참조 칸이 가리키는 것의 칸),
      `out.used_by`(관계로 이어진 것 자체), `in.resells.country`. 주소는 `object_fields`
      가 준다 — 추측하지 않는다.
    - `status`: `active` · `deprecated`.

    응답의 `total` 이 전체 수다 — 한 쪽(limit ≤ 200)씩 `offset` 으로 넘긴다.
    **몇 건인지 세려면 전부 받지 말고 `objects_summary`.**

    **0건이면 `diagnosis` 가 붙는다** — 「타입에 객체가 없음(empty_type)」 · 「부서 밖이라
    안 보임(not_visible)」 · 「조건이 좁음(filters)」 중 무엇인지, 조건 때문이면 어느 조건을
    빼면 몇 건인지, 그 중 **값이 비어 있어서** 빠진 것이 몇 건인지까지 있다. 읽지 않고
    「없습니다」 라고 답하지 마라 — 있는 것을 없다고 하면 사람은 그것을 새로 만든다."""
    filters = _filter_params(q, status, properties, conditions)
    params: list[tuple[str, Any]] = [("limit", limit), ("offset", offset), *filters]
    found = await _get(ctx, f"/api/objects/{type_slug}", params=params)
    if isinstance(found, dict) and found.get("total") == 0:
        # **0건은 「없다」 가 아니다.** 안 채운 타입일 수도, 부서 밖이라 안 보일 수도,
        # 조건이 좁을 뿐일 수도 있다. 셋을 안 가르면 모델은 「없다」 로 읽고 없는 것을
        # 새로 만든다 — 그래서 0건일 때만 한 번 더 물어 이유를 붙인다.
        # 진단이 실패해도 목록은 돌려준다 — 덤이 본래 답을 막으면 안 된다.
        with contextlib.suppress(Exception):
            found["diagnosis"] = await _get(
                ctx, f"/api/objects/{type_slug}/diagnose", params=list(filters)
            )
    return found


def _filter_params(
    q: str | None,
    status: str | None,
    properties: dict[str, str] | None,
    conditions: list[dict[str, str]] | None,
) -> list[tuple[str, Any]]:
    """목록과 통계가 **같은 거르기**를 보낸다.

    따로 만들면 「목록은 12건인데 통계는 15건」 이 되고, 모델은 어느 쪽을
    믿을지 모른다."""
    params: list[tuple[str, Any]] = []
    if q:
        params.append(("q", q))
    if status:
        params.append(("status", status))
    for key, value in (properties or {}).items():
        params.append((f"p.{key}", value))
    for one in conditions or []:
        field, op, value = one.get("field", ""), one.get("op", "eq"), one.get("value", "")
        params.append((f"f.{field}.{op}", value))
    return params


@tool()
async def object_resolve(ctx: Context, type_slug: str, name: str) -> Any:
    """이름 하나가 **어느 객체인지 정해지는가** — 이름으로 무언가를 가리키기 전에 부른다.

    참조 칸을 채우거나 관계를 잇거나 「그 부품 상태 알려줘」 를 풀 때, 목록에서 첫 줄을
    집으면 **틀린 줄도 첫 줄이면 집힌다.** 그래서 목록이 아니라 판정을 준다:

      - `exact`      → `object.id` 를 그대로 쓴다.
      - `candidates` → **쓰지 마라.** 후보를 사람에게 보여 주고 어느 것인지 묻는다.
      - `none`       → 없다. 오타인지 아직 안 만든 것인지 사람에게 묻는다. 짐작하지 마라.

    식별자 → 별칭 → 이름 → 포함 차례로 맞춘다. 별칭이 있으므로 「앤시스」 로 물어도
    「Ansys」 가 나온다. **포함으로 하나만 걸려도 `exact` 가 아니다** — 포함은 짐작이다."""
    return await _get(ctx, f"/api/objects/{type_slug}/resolve", params=[("name", name)])


@tool()
async def objects_summary(
    ctx: Context,
    type_slug: str,
    group_by: str = "status",
    split_by: str | None = None,
    metric: str = "count",
    metric_field: str | None = None,
    order: str = "desc",
    q: str | None = None,
    properties: dict[str, str] | None = None,
    conditions: list[dict[str, str]] | None = None,
    status: str | None = None,
) -> Any:
    """**통계** — 「부서별 몇 건」 「개발사 국가별 툴 수」. 화면의 「통계」 와 같다.

    **목록을 전부 받아 직접 세지 않는다** — 쪽 상한에서 틀리고, 수천 건이면
    못 한다. 서버가 센다. 거르기(`q`·`properties`·`conditions`·`status`)는
    `objects_list` 와 같다 — 그래서 목록의 `total` 과 여기 `total` 이 같다.

    - `group_by` 기준: `label`·`key`·`status`·`workspace`(소유 부서)·
      `created_year`, 속성은 `properties.<키>`, 다른 타입의 칸은
      `object_fields` 의 주소. 날짜는 해로 센다. 긴 글·파일은 안 된다.
      쓸 수 있는 기준 전부가 응답의 `group_options` 다.
    - `split_by` 세부 기준(같은 규칙). 주면 칸마다 `parts` 로 나뉜다.
    - `metric`: `count`(기본)·`sum`·`avg`·`min`·`max`. `count` 가 아니면
      `metric_field`(숫자 속성 `properties.<키>`)가 필요하다.
    - `order`: `desc`(큰 값부터)·`asc`(작은 값부터 — 「가장 낮은 것」).

    사용자에게 옮길 때 **빼먹지 않는다**:
    - `total` 은 거른 **객체 수**. 「(비어 있음)」 칸도 숨기지 않는다.
    - `other_groups`·`other_count` 가 0 이 아니면 「그 밖에 N종류 M건」.
    - `overlap` 이 true 면 한 객체가 여러 칸에 든다(여러 값 칸, 여럿과 이어진
      관계) — 칸의 합이 `total` 보다 클 수 있다고 함께 말한다.
    - 「그 칸이 뭔데」 는 `buckets[].key` 를 조건 값으로 `objects_list`:
      기준이 `properties.<키>` 면 field 는 `<키>`, 다른 타입의 칸이면 그 주소,
      `status` 면 `status` 인자. key 가 null 이면 op 는 `empty`."""
    params: list[tuple[str, Any]] = [
        ("group_by", group_by),
        ("metric", metric),
        ("order", order),
    ]
    if split_by:
        params.append(("split_by", split_by))
    if metric_field and metric != "count":
        params.append(("metric_field", metric_field))
    params += _filter_params(q, status, properties, conditions)
    return await _get(ctx, f"/api/objects/{type_slug}/summary", params=params)


@tool()
async def object_fields(ctx: Context, type_slug: str) -> Any:
    """**다른 타입의 칸**을 쓰는 주소 — `conditions` 의 `field`, `objects_summary`
    의 `group_by` 에 그대로 넣는다. 자기 칸은 `ontology_schema` 에 있다.

    한 걸음까지다: `ref.<참조 칸>.<칸>`(참조 칸이 가리키는 것의 칸),
    `out.<관계>`(관계로 이어진 것 자체 — 값은 상대 id), `out.<관계>.<칸>`,
    `in.<관계>[.<칸>]`(들어오는 관계). `heading` 이 어느 걸음인지 가른다 —
    참조 칸과 관계가 같은 이름일 수 있다. `data_type` 이 `relation` 이면 상대가
    하나로 안 정해져 `empty`·`notempty` 만 걸린다.

    뜻: 이어진 것이 여럿이면 **그중 하나라도** 맞으면 걸린다. 이어진 것이 없는
    객체는 그 너머의 칸 조건에 안 걸린다(`ne` 도) — 그것은 참조 칸이나 관계의
    `empty` 로 묻는다."""
    return await _get(ctx, f"/api/objects/{type_slug}/fields")


@tool()
async def object_get(ctx: Context, type_slug: str, object_id: str) -> Any:
    """객체 하나 — 속성·첨부·**관련 객체**(양방향)까지."""
    return await _get(ctx, f"/api/objects/{type_slug}/{object_id}")


@tool()
async def object_history(ctx: Context, type_slug: str, object_id: str) -> Any:
    """객체의 **변경 이력** — 최근 것이 앞. 언제·누가·어느 칸을 전→후, 관계를 맺고 끊은 것.

    값 기록에는 `snapshot`(그 시점의 값 전체)이 붙는다. **되돌리기는 여기서 하지 않는다**
    — 사람이 화면에서 「이 값으로 되돌리기」 를 누른다(저장과 같은 검증을 거친다)."""
    return await _get(ctx, f"/api/objects/{type_slug}/{object_id}/history")


@tool()
async def object_rollup(ctx: Context, type_slug: str, object_id: str) -> Any:
    """이 객체 **「아래 전부」 의 숫자를 모은 것** — 어셈블리의 총 무게, 과제의 예산 합계.

    타입의 `list_view.rollups` 가 정한 대로 트리 관계 아래를 펼쳐 `sum·min·max·avg·count`
    로 모은다. 저장된 값이 아니라 볼 때마다 센 것이다. `missing` 이 0 이 아니면 그 합계는
    「전부의 합」 이 아니다 — 사용자에게 함께 말한다. 정의가 없는 타입은 빈 목록."""
    return await _get(ctx, f"/api/objects/{type_slug}/{object_id}/rollup")


@tool()
async def object_references(ctx: Context, type_slug: str, object_id: str) -> Any:
    """이 객체를 **가리키는 것** — 속성으로 가리키는 객체들과 걸린 관계들.

    지우거나 합치기 전에 본다. 남의 부서 것은 수만 온다(`hidden_*`) — 「아무것도 안
    걸렸다」 로 읽고 지우면 그쪽 화면이 깨진다."""
    return await _get(ctx, f"/api/objects/{type_slug}/{object_id}/references")


@tool()
async def quality_report(ctx: Context, kind: str | None = None) -> Any:
    """데이터 품질 — **나빠지고 있는 것.** 홈 「남은 일」 과 같은 것.

    `kind` 로 한 종류만: `missing_required`(필수값 빈 객체) · `orphan`(관계 없는 객체) ·
    `broken_ref`(지워진 것을 가리키는 칸) · `duplicate`(이름이 같은 객체). 종류·타입마다
    수는 전부, 목록은 앞의 몇 개(`sample_limit`)만. **볼 수 있는 것만 센다.**"""
    params = {"kind": kind} if kind else None
    return await _get(ctx, "/api/objects/quality/report", params=params)


@tool()
async def object_create(
    ctx: Context,
    type_slug: str,
    label: str,
    key: str | None = None,
    properties: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    description: str = "",
) -> Any:
    """객체 하나를 만든다.

    `workspace_slug` 를 비우면 **전역**이 되고 시스템 관리자만 만들 수 있다.
    속성은 정의에 없는 키를 넣으면 거절된다 — 먼저 `ontology_schema` 를 읽는다."""
    return await _post(
        ctx,
        f"/api/objects/{type_slug}",
        {
            "key": key,
            "label": label,
            "description": description,
            "properties": properties or {},
            "workspace_slug": workspace_slug,
        },
    )


@tool()
async def object_update(
    ctx: Context,
    type_slug: str,
    object_id: str,
    label: str | None = None,
    properties: dict[str, Any] | None = None,
    description: str | None = None,
    aliases: list[str] | None = None,
) -> Any:
    """객체를 고친다 — **보낸 것만.**

    `properties` 는 보낸 키만 병합한다. 값을 지우려면 그 키에 `null` 을 넣는다 —
    통째로 덮으면 다른 속성이 함께 날아가고 **그 손실은 아무 데도 안 뜬다.**

    `aliases` 는 **다른 이름 전부**(통째로 바꿈). 「Ansys」 를 「앤시스」 로도 부르면 여기
    적는다 — 그 뒤로 찾기·참조·파일이 그 이름으로도 같은 객체를 찾는다. 같은 타입의 다른
    객체가 쓰는 별칭이면 거절된다."""
    body: dict[str, Any] = {}
    if label is not None:
        body["label"] = label
    if description is not None:
        body["description"] = description
    if properties is not None:
        body["properties"] = properties
    got = await _patch(ctx, f"/api/objects/{type_slug}/{object_id}", body) if body else None
    if aliases is not None:
        async with _client(60) as client:
            got = _unwrap(
                await client.put(
                    f"/api/objects/{type_slug}/{object_id}/aliases",
                    json={"aliases": aliases},
                    headers=_forward_headers(ctx),
                )
            )
    return got if got is not None else {"ok": True, "message": "바꿀 것이 없었습니다"}


@tool()
async def objects_import(
    ctx: Context,
    type_slug: str,
    rows: list[dict[str, Any]],
    workspace_slug: str | None = None,
    apply: bool = False,
) -> Any:
    """객체를 **여러 행 한 번에** — 같은 식별자(`key`)면 만들지 않고 고친다(upsert).

    `apply=False`(기본)면 **아무것도 안 바꾸고** 행마다 무엇이 될지(새로/고침/그대로/
    오류)를 돌려준다. 사람에게 보여 주고 판단을 받은 뒤 `apply=True` 로 부른다.
    **한 행이라도 오류면 아무것도 안 넣는다.**

    행은 `{"key": ..., "label": ..., <속성 키>: ...}` 꼴. 없는 키는 안 건드리고,
    비우려면 `null` 을 넣는다. 참조 속성은 상대의 식별자(없으면 이름)로 적어도 된다.
    한 번에 5000행까지."""
    return await _post(
        ctx,
        f"/api/objects/{type_slug}/import-rows",
        {"rows": rows, "workspace_slug": workspace_slug, "apply": apply},
    )


# --------------------------------------------------------------------------- #
# 관계
# --------------------------------------------------------------------------- #
@tool()
async def bundle_import(ctx: Context, bundle: dict[str, Any], apply: bool = False) -> Any:
    """**정의 · 객체 · 관계를 한 묶음으로** — 정제 도구(`pipeline/`)가 만든 결과물.

    `apply=False`(기본)면 **아무것도 저장하지 않고 한 번에 미리 본다** — 정의를
    먼저 적용하지 않아도 그 정의로 객체와 관계를 맞춰 본다. 만든 묶음이 어떻게
    들어갈지 스스로 확인할 때 쓴다.

    `apply=True` 는 **전부 아니면 무** — 한 곳이라도 오류면 아무것도 안 들어간다.
    **넣는 것은 사람이 미리 보기를 확인한 뒤에만** 한다(보통은 `sp_pipeline.py
    apply` 가 한다).

    모양: `{"ontology": <ontology_import 와 같은 스키마, 없으면 생략>,
    "objects": [{"type_slug", "workspace_slug", "rows": [...]}],
    "relations": [{"type_slug", "rows": [{"src","relation","dst","evidence_note"}]}]}`.
    행은 `objects_import` · `relations_import` 와 같다. `objects` 는 **적은 차례대로**
    넣는다 — 참조하는 타입을 뒤에. 정의가 들면 시스템 관리자와 `ontology:write`
    범위가 필요하다."""
    return await _post(ctx, "/api/bundles/import", {**bundle, "apply": apply})


@tool()
async def relation_add(
    ctx: Context,
    type_slug: str,
    object_id: str,
    relation: str,
    dst_object_id: str,
    evidence_note: str = "",
) -> Any:
    """객체 둘을 잇는다.

    **근거를 적는다.** 근거 없는 연결은 시간이 지나면 아무도 못 믿는다 — 맞는지
    확인하려면 처음부터 다시 조사해야 하기 때문이다. 기계가 이은 것이면 더 그렇다."""
    return await _post(
        ctx,
        f"/api/objects/{type_slug}/{object_id}/relations",
        {"relation": relation, "dst_object_id": dst_object_id, "evidence_note": evidence_note},
    )


@tool()
async def relations_import(
    ctx: Context,
    type_slug: str,
    rows: list[dict[str, Any]],
    apply: bool = False,
) -> Any:
    """관계를 **여러 줄 한 번에** — `type_slug` 의 객체에서 출발하는 선들.

    행은 `{"src": ..., "relation": ..., "dst": ..., "evidence_note": ...}` 꼴. 끝점은
    식별자(없으면 이름). 이미 이어진 것은 「그대로」 라 두 번 올려도 두 겹이 안 된다.
    `apply=False` 면 계획만. **근거(evidence_note)를 적는다** — 기계가 이은 것이면 더."""
    return await _post(
        ctx,
        f"/api/objects/{type_slug}/relations/import-rows",
        {"rows": rows, "apply": apply},
    )


# --------------------------------------------------------------------------- #
# 데이터 소스 — 바깥 시스템(OData)에서 읽어 채우기. 정의는 화면에서, 돌리는 것은 여기서도.
# --------------------------------------------------------------------------- #
@tool()
async def datasources_list(ctx: Context) -> Any:
    """정의된 **데이터 소스**(OData → 타입) 목록 — 어느 표를 어느 타입에 넣는지, 마지막 결과.
    시스템 관리자 토큰이어야 보인다."""
    return await _get(ctx, "/api/datasources")


@tool()
async def datasource_sync(ctx: Context, slug: str, apply: bool = False) -> Any:
    """데이터 소스를 **동기화**한다 — 바깥 표를 읽어 그 타입의 객체로.

    `apply=False`(기본)면 **계획만**: 행마다 새로/고침/그대로/오류와 그 이유. 사람에게 보여
    주고 판단을 받은 뒤 `apply=True`. **한 행이라도 오류면 아무것도 안 넣는다.** 같은 객체는
    바깥 식별자 → 식별자 → 별칭·이름 순으로 다시 찾고, 빈 칸은 안 건드린다. 값 대응표에 없는
    값·못 푸는 참조는 오류 행이다 — 사용자에게 무엇을 고쳐야 하는지 말한다."""
    return await _post(
        ctx,
        f"/api/datasources/{slug}/sync",
        None,
        params={"apply": "true" if apply else "false"},
    )


# --------------------------------------------------------------------------- #
# RDF/OWL — 정의와 데이터를 형식 온톨로지로, 그리고 **질의어(SPARQL)로 묻기.**
#
# 목록 · 조건 · 통계는 한 타입 안에서 쉽다. 「코어를 건너뛰어 잇는 물음」(모델 → 과제 →
# 프로젝트를 한 번에, 역관계로 거슬러, 상속으로 묶어)은 질의어가 낫다.
# --------------------------------------------------------------------------- #
@tool()
async def rdf_schema(ctx: Context) -> str:
    """이 설치의 정의를 **OWL(Turtle)** 로 — 클래스 · 속성 · 관계와 그 뜻.

    `rdf_query` 를 쓰기 전에 읽는다. 여기서 클래스 이름(`sp:<타입slug>`) · 속성
    (`sp:<타입>.<속성키>`) · 관계(`sp:rel.<관계slug>`)와 상속(`rdfs:subClassOf`) ·
    역관계(`owl:inverseOf`) · 이행(`owl:TransitiveProperty`)을 확인한다."""
    async with _client(60) as client:
        response = await client.get(
            "/api/rdf/schema", params={"format": "ttl"}, headers=_forward_headers(ctx)
        )
        response.raise_for_status()
        return response.text


@tool()
async def rdf_query(
    ctx: Context,
    query: str,
    types: list[str] | None = None,
    infer: bool = False,
    limit: int = 200,
) -> Any:
    """**SPARQL 로 묻는다** — 여러 타입을 건너뛰어 잇는 물음에.

    읽기만 한다(`SELECT` · `ASK`). 접두어는 답의 `prefixes` 에 오고, `sp:` 가 이 설치의 정의
    자리다. 개체 주소는 `<base>o/<타입>/<식별자>` 다.

    - `types` 로 **범위를 좁히면 빠르다** — 안 주면 전부 올린다.
    - `infer=True` 면 OWL-RL 추론을 켠다: 상속으로 얻은 분류(「개발모델이면 제품이다」),
      역관계(적은 것의 반대 방향), 이행(A→B, B→C 면 A→C)이 답에 들어온다. 느리므로 `types` 로
      좁혀야 하고, 큰 범위는 서버가 거절한다.
    - `truncated` 가 참이면 **잘린 것이다** — 「전부 이것뿐」 으로 읽지 않는다.

    예: 과제마다 개발모델 수
    ```sparql
    PREFIX sp: <…/ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?task (COUNT(?m) AS ?n) WHERE {
      ?m a sp:plm_model ; sp:plm_model.task ?t . ?t rdfs:label ?task
    } GROUP BY ?task ORDER BY DESC(?n)
    ```"""
    return await _post(
        ctx,
        "/api/rdf/query",
        {"query": query, "types": types, "infer": infer, "limit": limit},
    )


if __name__ == "__main__":
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    mcp.settings.host = host
    mcp.settings.port = int(os.environ.get("MCP_PORT", "8042"))

    # FastMCP 는 생성 시점(host=127.0.0.1)에 DNS rebinding 보호를 켜고
    # allowed_hosts 를 localhost(127.0.0.1:* / localhost:* / [::1]:*)로 고정한다.
    # 위에서 host 를 0.0.0.0 등으로 바꿔도 그 설정은 그대로라, 서버 IP·도메인으로
    # 들어온 Host 헤더가 거부돼 421 "Invalid Host header" 가 난다(외부 노출 시).
    # 비-localhost 바인딩이면 여기서 transport_security 를 다시 설정한다.
    if host not in ("127.0.0.1", "localhost", "::1"):
        from mcp.server.transport_security import TransportSecuritySettings

        allowed = [
            h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()
        ]
        if allowed:
            # 권장: 허용할 Host 만 명시. 포트 와일드카드 가능.
            #   MCP_ALLOWED_HOSTS="mcp.example.com,mcp.example.com:*,10.0.0.5:8042"
            # nginx 리버스프록시면 proxy_set_header Host 로 넘어오는 값(도메인)을 넣는다.
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=allowed,
                allowed_origins=[],
            )
        else:
            # 미지정이면 보호를 끈다 — 인증은 PAT(백엔드가 검증), 망 보호는
            # nginx/방화벽에 맡기는 사내망 노출 시나리오. 외부망 노출 시엔
            # MCP_ALLOWED_HOSTS 를 지정해 보호를 유지하길 권장.
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            )

    mcp.run(transport="streamable-http")
