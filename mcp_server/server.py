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

import asyncio
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
        "  어느 타입에 있는지 모른다       search           ← 타입을 모를 때의 첫 걸음\n"
        "  이름으로 무언가를 가리킨다      object_resolve   ← 참조·관계 잇기의 첫 걸음\n"
        "  내 부서 · 내 권한               whoami           (create 의 workspace_slug)\n"
        "  그 타입에 무엇이 있나           objects_list\n"
        "  몇 건인가 · 어떻게 갈리나       objects_summary  (세려고 전부 받지 마라)\n"
        "  이 객체의 모든 것               object_get · object_references · object_rollup\n"
        "  계층을 한 단계씩                object_tree\n"
        "  이것과 이어진 것들              graph_neighbors  (타입 지형은 graph_overview)\n"
        "  누가 언제 무엇을 바꿨나         object_history(하나) · audit_recent(전체)\n"
        "  여러 객체의 한 칸을 한 값으로   bulk_edit (apply=false 로 먼저 · undo 있음)\n"
        "  객체를 지운다 · 둘을 합친다     objects_delete · object_merge (apply=false 먼저)\n"
        "  객체를 이력의 그 값으로         object_restore\n"
        "  잘못 이은 관계                  relation_update · relation_remove\n"
        "  여러 타입을 건너뛰는 물음       rdf_query (SPARQL)\n"
        "  여러 행 · 묶음을 넣는다         objects_import · bundle_import → job_apply\n"
        "  정의를 바꾼다                   ontology_import (apply=false 로 먼저)\n"
        "  정의를 지운다                   ontology_delete (apply=false 로 먼저)\n"
        "  속성 종류 · 고를 값 이름 · 승격  ontology_retype · ontology_rename_option · "
        "ontology_promote\n"
        "  정의를 그때로 되돌린다          ontology_restore\n"
        "  이 설치에만 있는 기능           extensions_schema → extension_call\n"
        "\n"
        "지켜야 할 셋:\n"
        "1. **이름은 해소하고 쓴다.** `object_resolve` 가 `candidates` 를 주면 고르지 "
        "말고 사람에게 묻는다 — 목록의 첫 줄을 집으면 틀린 줄도 집힌다.\n"
        "2. **0건은 「없다」 가 아니다.** 목록이 0건이면 응답에 `diagnosis` 가 붙는다 "
        "— 안 채운 타입인지, 부서 밖이라 안 보이는지, 조건이 좁은지 거기 적혀 있다. "
        "그것을 읽기 전에 「없습니다」 라고 답하지 마라.\n"
        "3. **바꾸거나 지울 때는 미리 보기 먼저.** `ontology_import` · `ontology_delete` · "
        "`objects_delete` 같은 도구를 기본값(apply=false)으로 불러 계획과 경고를 사람에게 "
        "보여 주고, 판단을 받은 뒤에 apply=true 로 부른다. 계획이 막는 것(`blocking` · "
        "`errors`)은 우회하지 않는다 — 먼저 할 일을 사람에게 말한다."
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


async def _delete(ctx: Context, path: str, params: dict[str, Any] | None = None) -> Any:
    async with _client(60) as client:
        return _unwrap(await client.delete(path, params=params, headers=_forward_headers(ctx)))


async def _post_form(
    ctx: Context,
    path: str,
    fields: dict[str, Any],
    file: tuple[str, bytes, str] | None,
) -> Any:
    async with _client(120) as client:
        return _unwrap(
            await client.post(
                path,
                data=fields,
                files={"file": file} if file is not None else None,
                headers=_forward_headers(ctx),
            )
        )


JOB_WAIT_MAX = 25.0
"""한 도구 호출이 작업을 기다리는 최대 초. 클라이언트의 도구 시간 한도(대개 60초)보다 넉넉히
짧게 — 그 안에 안 끝나면 `job_status` 로 다시 묻는다. 작은 파일은 첫 호출 안에 끝난다."""


async def _wait_job(ctx: Context, job: Any, wait_seconds: float) -> Any:
    """작업이 끝나거나 `wait_seconds` 가 지날 때까지 본다. **끝났다는 말을 지어내지
    않는다** — 안 끝났으면 `status` 가 `queued`/`running` 인 채로 돌려주고 `next` 가 무엇을
    할지 말한다."""
    if not isinstance(job, dict) or "id" not in job:
        return job
    deadline = time.monotonic() + max(0.0, min(wait_seconds, JOB_WAIT_MAX))
    current = job
    while current.get("status") in ("queued", "running") and time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        current = await _get(ctx, f"/api/jobs/{job['id']}")
        # 작업 행에는 `error` 칸이 늘 있다(없으면 null) — 오류 봉투는 `id` 가 없는 것으로
        # 가른다.
        if not isinstance(current, dict) or "id" not in current:
            return current
    return _job_view(current)


def _job_view(job: Any) -> Any:
    """AI 에게 주는 작업 모양 — 상태와 결과, 그리고 **다음에 할 일**."""
    if not isinstance(job, dict) or "id" not in job:
        return job
    status = job.get("status")
    out: dict[str, Any] = {
        "job_id": job["id"],
        "kind": job.get("kind"),
        "status": status,
        "progress": job.get("progress"),
    }
    if status == "done":
        out["result"] = job.get("result")
        result = job.get("result") or {}
        applied = bool(result.get("applied"))
        has_errors = (
            bool(result.get("errors"))
            or bool((result.get("counts") or {}).get("error"))
            or result.get("ok") is False
        )
        if applied:
            out["next"] = "적용됐다. 결과를 사용자에게 요약한다."
        elif has_errors:
            out["next"] = (
                "오류가 있는 계획이다 — 적용할 수 없다. 오류를 사용자에게 보이고 고쳐서 다시 "
                "넣는다."
            )
        else:
            out["next"] = (
                "계획이다 — 아직 아무것도 안 들어갔다. 사용자에게 보여 주고 판단을 받은 뒤 "
                "job_apply(job_id) 로 적용한다."
            )
    elif status == "failed":
        out["error"] = job.get("error")
        out["next"] = "실패했다. 이유를 사용자에게 그대로 전한다 — 우회하지 않는다."
    elif status == "cancelled":
        out["next"] = "취소됐다."
    else:
        out["next"] = "아직 도는 중이다. 몇 초 뒤 job_status(job_id) 로 다시 묻는다."
    return out


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
      - `extensions` 이 설치에만 있는 기능(확장) — 무엇을 부를 수 있나

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
# 나 · 찾기 — 타입을 모를 때의 첫 걸음
# --------------------------------------------------------------------------- #
@tool()
async def whoami(ctx: Context) -> Any:
    """**이 토큰의 주인** — 이름 · 내 부서(`home_workspace_slug`) · 속한 부서와 역할
    (`memberships`) · 시스템 관리자인지.

    `object_create` 의 `workspace_slug` 에 무엇을 넣을지는 여기서 안다. 비우면 전역이
    되어 시스템 관리자가 아니면 거절된다 — **부서를 짐작해 넣지 말고 여기서 읽는다.**
    쓰기가 거절되면 `memberships[].role` 을 보고 사용자에게 알린다."""
    return await _get(ctx, "/api/auth/me")


@tool()
async def search(
    ctx: Context, q: str, type_slug: str | None = None, limit: int = 20, offset: int = 0
) -> Any:
    """**타입을 모를 때** 이름·식별자·별칭으로 전부 찾는다 — 「앤시스 관련된 거 뭐 있어」.

    `types[]` 가 타입별 건수라 **어느 타입에 있는지**가 먼저 보인다. 타입을 알면
    `objects_list`(조건 거르기) · `object_resolve`(하나로 정하기)가 낫다 — 이것은
    「어디 있나」 를 묻는 도구지 「어느 것인가」 를 정하는 도구가 아니다.
    볼 수 있는 것만 나온다(남의 부서 것은 수에도 안 잡힌다). `type_slug` 에 인터페이스를
    주면 구현 타입 전부에서 찾는다.

    **기록**(`usage="log"` — 시장 서비스 건 · 시험 결과)은 섞어 찾을 때 줄로 안 오고
    `types[]` 의 건수와 `records` 로만 온다. 기록에서 찾으려면 그 타입을 `type_slug` 로
    준다."""
    params: list[tuple[str, Any]] = [("q", q), ("limit", limit), ("offset", offset)]
    if type_slug:
        params.append(("type", type_slug))
    return await _get(ctx, "/api/search", params=params)


# --------------------------------------------------------------------------- #
# 정의
# --------------------------------------------------------------------------- #
@tool()
async def ontology_schema(ctx: Context) -> Any:
    """이 설치의 **정의 전부** — 묶음·인터페이스·타입·속성·관계 종류.

    무엇을 만들 수 있고 각 타입이 어떤 값을 받는지가 여기 다 있다. **다른 도구를
    부르기 전에 이것부터 읽는다.** 인터페이스는 여러 타입이 따르는 공통 모양이다 —
    타입의 `interface_slugs` 가 구현이고, 속성의 `interface_slug` 가 붙어 있으면 공통
    속성이다."""
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

    더하고 고치기만 한다. **스키마에 없다고 지우지 않는다.** 여러 타입이 같은 개념이면
    `interfaces` 에 공통 속성을 적고 타입이 `interface_slugs` 로 구현한다 — 모양이 다른
    같은 키가 있으면 `errors` 가 무엇이 다른지 말한다(짐작으로 맞추지 말고 사람에게 보인다).
    속성의 `data_type` 을 바꾸면 저장된 값도 변환된다 — 변환할 수 없는 값은 `errors` 가 값과
    함께 말한다(대체 값은 사람이 화면의 「종류 변경」 에서 정한다).
    자세한 것은 `get_guide("schema")`."""
    return await _post(
        ctx,
        "/api/ontology/import",
        schema,
        params={"dry_run": "false" if apply else "true"},
    )


@tool()
async def table_infer(ctx: Context, rows: list[dict[str, Any]]) -> Any:
    """**표(행 목록)에서 기록 타입의 정의를 제안받는다** — 열마다 역할 · 종류, 그리고 **어느
    있는 타입을 가리키나**(참조 후보). 아무것도 안 바꾼다. 시스템 관리자만 된다.

    `rows` 는 `{열 이름: 값}` 목록(한 번에 5,000행까지 — 견본으로 충분하다). 열마다:
    `role`(label · key · description · aliases · property · ignore) · `key` · `data_type` ·
    `note`(왜 그렇게 맞혔나), 그리고 `ref_candidates` — 값이 그 타입의 객체로 **하나로 풀림
    (`one`) · 여럿에 맞음(`many`) · 못 찾음(`none`)** 의 수와 견본. 넣을 때와 같은 이름 풀이
    (식별자 → 별칭 → 이름)로 셌다 — 여기서 하나로 풀린 값은 넣을 때도 풀린다.

    확실할 때만(하나로 90% 이상 · 3종 이상 · 짧은 숫자 아님) `data_type` 이 `object_ref` 로
    바뀌어 온다(`ref_type_slug`). 아니면 글자 그대로고 `ref_note` 가 왜인지 말한다. **후보를
    스스로 확정하지 않는다** — 열 · 후보 · 못 찾은 견본을 사람에게 보이고, 그가 고친 열로
    `table_build` 를 부른다. 여럿에 맞는 값은 넣을 때 거절된다(식별자로 적어야 한다).

    다음: `table_build` → `ontology_import`(계획 → 적용) → `objects_import`."""
    body = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
    got = await _post_form(
        ctx, "/api/ontology/infer", {}, ("rows.json", body, "application/json")
    )
    if isinstance(got, dict):
        # 보낸 행을 그대로 돌려받을 까닭이 없다 — `table_build` 에 같은 행을 다시 넘긴다.
        got.pop("raw_rows", None)
    return got


@tool()
async def table_build(
    ctx: Context,
    slug: str,
    label: str,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    key_policy: str = "optional",
    nav_group_slug: str | None = None,
    usage: str = "log",
) -> Any:
    """`table_infer` 의 열(사람이 고친 것)과 같은 행 → **정의 스키마**와 **넣을 행**.
    아무것도 안 바꾼다. `usage` 는 기본 `log`(기록) — 표는 대개 축을 가리키는 쪽이다. 개발모델
    · 과제처럼 **가리켜지는 쪽**을 표로 만들면 `axis`(축).

    `columns` 는 `table_infer` 가 준 열 그대로에서 고칠 것만 고친다 — `role` · `key` ·
    `label` · `data_type` · `multi` · `enum_options`, 참조로 둘 열은
    `data_type="object_ref"` 와 `ref_type_slug`(있는 타입 · 인터페이스). 이름(label) 역할
    열이 정확히 하나여야 한다.

    돌아온 `schema` 를 `ontology_import` 로(계획을 보이고 `apply=True`), `import_rows` 를
    `objects_import(type_slug=slug, rows=import_rows, …)` 로 넣는다. 참조 칸의 값은 글자
    그대로 가고 넣을 때 풀린다."""
    return await _post(
        ctx,
        "/api/ontology/infer/build",
        {
            "slug": slug,
            "label": label,
            "key_policy": key_policy,
            "nav_group_slug": nav_group_slug,
            "usage": usage,
            "columns": columns,
            "raw_rows": rows,
        },
    )


#: 지울 수 있는 정의와 그 자리. 미리 보기(`GET /api/ontology/delete-plan`)도 같은 이름이다.
_DELETE_PATHS = {
    "group": "/api/ontology/groups/{slug}",
    "type": "/api/ontology/types/{slug}",
    "interface": "/api/ontology/interfaces/{slug}",
    "relation_type": "/api/ontology/relation-types/{slug}",
    "property": "/api/ontology/types/{slug}/properties/{key}",
    "interface_property": "/api/ontology/interfaces/{slug}/properties/{key}",
}


@tool()
async def ontology_delete(
    ctx: Context,
    kind: str,
    slug: str,
    key: str | None = None,
    apply: bool = False,
    accept_core: bool = False,
    purge_deleted: bool = False,
) -> Any:
    """정의 하나를 **지운다.** `kind` 는 `group`(묶음) · `type` · `interface` ·
    `relation_type` · `property`(타입의 속성) · `interface_property`(공통 속성) — 속성
    둘은 `key` 가 있어야 한다. 시스템 관리자만 된다.

    `apply=False`(기본)면 **아무것도 안 지우고** 계획만 온다: `blocking`(먼저 할 일 —
    하나라도 있으면 지금은 못 지운다) · `removes`(함께 사라지는 것) · `keeps`(남는 것 —
    속성을 지워도 저장값은 남는다) · `warnings`. 사람에게 보이고 판단을 받은 뒤
    `apply=True`.

    **막는 것을 우회하지 않는다.** 살아 있는 객체가 든 타입 · 관계가 맺힌 관계 종류는 못
    지운다 — 그만 쓰려는 것이면 `ontology_import` 로 `is_active: false`(자료는 남고 화면에서만
    빠진다). 타입에 **지운 객체만** 남았으면 계획의 `purge_deleted` 가 그 수다 — 함께
    **영구 삭제**되고 되돌릴 수 없으니, `removes` 를 사람에게 보이고 확인받아
    `purge_deleted=True`(ADR 0008). 외부 공개 타입의 속성은 `core_consumers` 를 보이고
    통보를 확인받아 `accept_core=True`. 지우기 직전 정의는 스냅샷으로 남는다 — 되살리기는
    `ontology_restore`(객체는 스냅샷에 없다)."""
    if kind not in _DELETE_PATHS:
        return {"error": f"kind 는 {' · '.join(_DELETE_PATHS)} 중 하나입니다: {kind}"}
    if kind.endswith("property") and not key:
        return {"error": f"{kind} 를 지우려면 key(속성 키)가 있어야 합니다."}
    if not apply:
        asked = {"kind": kind, "slug": slug, **({"key": key} if key else {})}
        return await _get(ctx, "/api/ontology/delete-plan", params=asked)
    path = _DELETE_PATHS[kind].format(slug=slug, key=key)
    params: dict[str, Any] = {}
    if accept_core and kind == "property":
        params["accept_core"] = "true"
    if purge_deleted and kind == "type":
        params["purge_deleted"] = "true"
    got = await _delete(ctx, path, params=params or None)
    if isinstance(got, dict) and got.get("ok"):
        return {"ok": True, "message": f"지웠습니다 — {kind} {slug}{f'.{key}' if key else ''}"}
    return got


def _property_owner(owner: str) -> str | None:
    """`type` · `interface` → 경로의 자리. 모르는 것은 None — 짐작해 다른 데로 안 보낸다."""
    return {"type": "types", "interface": "interfaces"}.get(owner)


@tool()
async def ontology_retype(
    ctx: Context,
    slug: str,
    key: str,
    data_type: str,
    owner: str = "type",
    mapping: dict[str, str | None] | None = None,
    enum_options: list[str] | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    decimals: int | None = None,
    pattern: str | None = None,
    unit: str | None = None,
    unique: bool | None = None,
    ref_type_slug: str | None = None,
    inverse_label: str | None = None,
    accept_core: bool = False,
    apply: bool = False,
) -> Any:
    """속성의 **종류를 바꾼다** — 저장값도 같은 규칙으로 변환된다(종류 변경, ADR 0007).
    `owner="interface"` 면 공통 속성이고 구현 타입 전부가 한 번에 바뀐다.

    **글 → 참조**(`data_type="object_ref"`, `ref_type_slug` 필수)는 이미 넣은 기록을 축에
    잇는다(ADR 0009) — 값마다 일괄 입력과 같은 이름 풀이(식별자 → 별칭 → 이름 → id)로
    바꾸고, 이름이 여럿에 맞거나 못 찾은 값이 `failures` 로 온다(대체 값은 상대의 식별자 ·
    이름 · id). **참조 → 글**은 상대의 식별자(없으면 이름)가 된다. 참조는 글 · 긴 글 ·
    선택과만 오간다.

    값이 있는 객체가 2만 건을 넘으면 **작업이 된다**(`apply` 와 상관없이 계획 작업) — 돌아온
    `result` 가 계획이다. 사용자에게 보여 주고 판단을 받은 뒤 `job_apply(job_id)` 로 적용한다.
    대체 값을 고치면 이 도구를 다시 부른다(새 계획 작업).

    `apply=False`(기본)면 계획: 타입별 변환 건수 · `failures`(변환할 수 없는 값 · 건수 ·
    견본) · `warnings` · `errors`. 변환할 수 없는 값이 하나라도 남으면 **적용되지
    않는다** — 값마다 `mapping={값: 대체 값 | null(값 삭제)}` 을 적는다(열쇠는 계획의
    `value` 그대로). **대체 값을 짐작하지 않는다** — 목록을 사람에게 보이고 그가 정한
    값만 적는다. 적용하면 직전 정의가 스냅샷으로 남는다(`snapshot_id`). 공개 타입이면
    `core_consumers` 를 보이고 `accept_core=True`.

    `ontology_import` 도 종류를 바꾸지만 대체 값을 적을 자리가 없다 — 그때 이 도구다."""
    base = _property_owner(owner)
    if base is None:
        return {"error": f"owner 는 type · interface 중 하나입니다: {owner}"}
    body: dict[str, Any] = {
        "data_type": data_type,
        "mapping": mapping or {},
        "accept_core": accept_core,
        "apply": apply,
    }
    for name, value in (
        ("enum_options", enum_options),
        ("min_value", min_value),
        ("max_value", max_value),
        ("decimals", decimals),
        ("pattern", pattern),
        ("unit", unit),
        ("unique", unique),
        ("ref_type_slug", ref_type_slug),
        ("inverse_label", inverse_label),
    ):
        if value is not None:
            body[name] = value
    path = f"/api/ontology/{base}/{slug}/properties/{key}/retype"
    got = await _post(ctx, path, body)
    if isinstance(got, dict) and "ONTOLOGY-0067" in str(got.get("error", "")):
        # 값이 많은 타입(기록) — 요청 안에서 안 끝나 작업이 된다. 작업은 늘 계획부터다.
        job = await _post(ctx, f"{path}/job", {**body, "apply": False})
        return await _wait_job(ctx, job, JOB_WAIT_MAX)
    return got


@tool()
async def ontology_rename_option(
    ctx: Context,
    slug: str,
    key: str,
    from_value: str,
    to_value: str,
    owner: str = "type",
    apply: bool = False,
) -> Any:
    """고를 값(enum)의 **이름을 바꾼다 — 저장된 값까지 함께.** 이름만 바꾸면 옛 이름으로 저장된
    값이 거르기에서 빠지고, 그 사실은 아무 데도 안 뜬다. `owner="interface"` 면 구현 타입 전부.

    `apply=False`(기본)면 몇 개가 함께 바뀌는지(`objects_with_value`)만 말한다."""
    base = _property_owner(owner)
    if base is None:
        return {"error": f"owner 는 type · interface 중 하나입니다: {owner}"}
    return await _post(
        ctx,
        f"/api/ontology/{base}/{slug}/properties/{key}/rename-option",
        {"from": from_value, "to": to_value, "apply": apply},
    )


@tool()
async def ontology_promote(
    ctx: Context,
    slug: str,
    key: str,
    target_type_slug: str | None = None,
    new_slug: str | None = None,
    new_label: str | None = None,
    nav_group_slug: str | None = None,
    apply: bool = False,
) -> Any:
    """고를 값(enum) 속성을 **코드표(참조 타입)로 승격한다** — 고를 값마다 객체가
    생기고(또는 `target_type_slug` 의 있는 객체에 붙고), 저장된 글자가 그 객체를
    가리키게 바뀐다. 새 코드표면 `new_slug` · `new_label`.

    `apply=False`(기본)면 계획(값마다 새로 만들지 · 붙일지). 값 이전은 스냅샷이 못 되돌린다 —
    사람의 판단을 받은 뒤에만 `apply=True`."""
    return await _post(
        ctx,
        f"/api/ontology/types/{slug}/properties/{key}/promote",
        {
            "target_type_slug": target_type_slug,
            "new_slug": new_slug,
            "new_label": new_label,
            "nav_group_slug": nav_group_slug,
            "apply": apply,
        },
    )


@tool()
async def ontology_restore(
    ctx: Context, snapshot_id: str | None = None, apply: bool = False
) -> Any:
    """정의를 **그때의 모습으로 되돌린다**(스냅샷). `snapshot_id` 를 비우면 되돌릴 수
    있는 스냅샷 목록(최근 50개 — 언제 · 누가 · 왜)이 온다. 가져오기 · 종류 변경 · 승격 ·
    삭제 · 되돌리기 직전마다 하나씩 남는다.

    `apply=False`(기본)면 계획(가져오기의 미리 보기와 같은 모양). **그 뒤에 새로 만든
    정의는 안 지운다.** 종류가 바뀐 뒤의 복원은 저장값도 다시 변환한다. 되돌리기
    직전도 스냅샷으로 남는다. 객체는 스냅샷에 없다."""
    if snapshot_id is None:
        listed = await _get(ctx, "/api/ontology/snapshots")
        return {"snapshots": listed} if isinstance(listed, list) else listed
    return await _post(
        ctx,
        f"/api/ontology/snapshots/{snapshot_id}/restore",
        None,
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

    `type_slug` 에 **인터페이스**를 주면 구현 타입 전부가 한 목록으로 온다(읽기만) —
    줄마다 `type_slug` 가 그 객체의 실제 타입이고, 상세 · 고치기는 그 타입으로 한다.
    조건은 공통 속성(키가 같다)과 `label`·`key`·`status` 로 건다.

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
    빼면 몇 건인지, 그 중 **값이 비어 있어서** 빠진 것이 몇 건인지까지 있다(인터페이스는
    「구현한 타입이 없음(no_implementers)」 도 있다). 읽지 않고
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
    「Ansys」 가 나온다. **포함으로 하나만 걸려도 `exact` 가 아니다** — 포함은 짐작이다.

    `type_slug` 에 인터페이스를 주면 구현 타입 전부에서 찾는다. 식별자는 타입마다 따로라
    **같은 식별자가 두 타입에 있으면 `candidates`** 다 — 후보의 `type_slug` 로 가른다."""
    return await _get(ctx, f"/api/objects/{type_slug}/resolve", params=[("name", name)])


@tool()
async def objects_resolve_many(ctx: Context, type_slug: str, names: list[str]) -> Any:
    """이름 **여럿을 한 번에** 푼다 — 판정 규칙은 `object_resolve` 와 같다.

    한 줄에 한 번 물으면 이천 줄짜리 원천에 왕복이 이천 번이다. **넣기 전에** 이것으로
    한 번 물어 「없는 것」 과 「여럿과 맞는 것」 을 먼저 걸러라 — 그러면 묶음 전체가
    거절되는 일이 줄고, 사람에게 물을 것만 남는다.

    답은 **보낸 차례대로** 오고 줄마다 물은 이름(`name`)이 붙는다. `counts` 가 몇 개가
    `exact` · `candidates` · `none` 인지 한 줄로 말한다. 한 번에 500개까지.

    `candidates` 는 **쓰지 마라** — 어느 것인지 사람에게 묻는다. `none` 도 짐작하지 마라."""
    return await _post(ctx, f"/api/objects/{type_slug}/resolve-many", {"names": names})


@tool()
async def aliases_pending(ctx: Context, type_slug: str, limit: int = 100) -> Any:
    """**사람이 아직 안 본 별칭** — 기계가 붙인 것이 그대로 정본처럼 쓰이지 않게.

    적재는 별칭을 수천 개 붙인다. 그 안에는 오타 표기와 남의 이름이 섞이는데, 본 것과 안 본
    것을 가르는 칸이 없으면 전부 정본처럼 쓰인다. 화면에서 사람이 붙인 것은 붙이는 순간
    확인한 것이라 여기 안 뜬다 — 여기 남는 것은 **파일 · 기계가 붙인 것**뿐이다.

    줄마다 그 별칭이 **어디서 왔는지**(`source`)와 메모가 온다. 사람에게 보여 주고 판단을
    받은 뒤 `aliases_review` 로 확인하거나 지운다. **네가 스스로 확인해 주지 마라** —
    그러면 검수라는 자리가 없는 것과 같다."""
    return await _get(
        ctx, f"/api/objects/{type_slug}/aliases/pending", params=[("limit", str(limit))]
    )


@tool()
async def aliases_review(
    ctx: Context, type_slug: str, alias_ids: list[str], action: str = "approve"
) -> Any:
    """고른 별칭을 **한 번에** 확인(`approve`)하거나 지운다(`remove`).

    `aliases_pending` 이 준 `id` 들을 넣는다. **사람이 고른 것만 넣어라** — 한 줄씩 누르게
    하면 수백 줄을 끝까지 보는 사람이 없어서 한 번에 하게 둔 자리이고, 그 판단은 사람의
    것이다. 확인한 것만 정본으로 쓰인다.

    못 한 줄은 `refused` 에 이유와 함께 온다(남의 부서 것, 이미 없는 것) — 하나가 막혀도
    나머지는 간다."""
    return await _post(
        ctx,
        f"/api/objects/{type_slug}/aliases/review",
        {"ids": alias_ids, "action": action},
    )


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
      `type_slug` 가 인터페이스면 `type`(어느 구현 타입인가)도 된다.
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
    `empty` 로 묻는다. 인터페이스 slug 도 받는다 — 공통 속성에서 나가는 걸음이다."""
    return await _get(ctx, f"/api/objects/{type_slug}/fields")


@tool()
async def object_get(ctx: Context, type_slug: str, object_id: str) -> Any:
    """객체 하나 — 속성·첨부·**관련 객체**(양방향)까지."""
    return await _get(ctx, f"/api/objects/{type_slug}/{object_id}")


@tool()
async def object_history(ctx: Context, type_slug: str, object_id: str) -> Any:
    """객체의 **변경 이력** — 최근 것이 앞. 언제·누가·어느 칸을 전→후, 관계를 맺고 끊은 것.

    값 기록에는 `snapshot`(그 시점의 값 전체)이 붙는다. 되돌리기는 `object_restore(entry_id)` —
    **어느 시점으로 갈지는 사람이 정한다**(저장과 같은 검증을 거친다)."""
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
async def object_tree(
    ctx: Context, type_slug: str, parent: str | None = None, orphans: bool = False
) -> Any:
    """계층(트리) **한 단계** — `parent` 없이 부르면 뿌리들, 있으면 그 아래 한 단계.

    타입의 「목록 화면」 에 트리 관계가 정해져 있어야 한다(없으면 빈 트리). 통째로 안
    온다 — 부품 5천 개짜리 트리를 한 번에 받으면 답이 잘린다. **깊이 전부가 필요하면
    `rdf_query` 로 `+` 경로**(`ns:part_of+`)를 쓴다. `orphans=true` 는 어디에도 안 이어진
    것만."""
    params: list[tuple[str, Any]] = [("orphans", "true" if orphans else "false")]
    if parent:
        params.append(("parent", parent))
    return await _get(ctx, f"/api/objects/{type_slug}/tree", params=params)


@tool()
async def graph_neighbors(
    ctx: Context,
    object_id: str,
    depth: int = 1,
    relations: list[str] | None = None,
    types: list[str] | None = None,
    fanout: int | None = None,
    limit: int | None = None,
    records: bool = False,
) -> Any:
    """이 객체에서 **몇 단계 안에 무엇이 이어져 있나** — 질의어 없이 그래프를 훑는다.

    「이것과 연결된 것들」 은 `objects_list` 로는 한 걸음까지이고, 그보다 멀면 질의어를
    써야 했다. 이 도구가 그 사이를 메운다 — **화면의 지식 그래프가 쓰는 것과 같은 길**이라
    답도 화면과 같다.

    - `depth` 는 몇 단계까지(서버가 상한을 강제한다). 1 이면 바로 이웃.
    - `relations` · `types` 로 좁힌다 — 안 좁히면 온갖 관계가 함께 온다.
    - **잘리면 잘렸다고 말한다**(`truncated`, 노드의 `degree`). 「이게 전부」 로 읽지 않는다 —
      좁혀서 다시 부르거나 그 노드에서 다시 펼친다.

    돌려주는 것: `nodes`(id · 이름 · 식별자 · 타입 · 상태 · 부서 · degree)와 `edges`
    (관계 slug · 양끝 · 방향). 값이 필요하면 그 id 로 `object_get` 을 부른다.

    **나를 가리키는 기록**(`usage="log"`)은 기본으로 이웃에 안 싣고 `log_counts`(타입 · 칸 ·
    수)로 준다 — 인기 모델은 기록 10만 건이 가리킨다. 기록 자체를 보려면 `objects_list(기록
    타입, conditions=[칸 = 이 객체])`, 이웃으로 싣고 싶으면 `records=True`.

    깊이 전부를 한 번에(부품 트리 밑바닥까지) 봐야 하면 `rdf_query` 의 `+` 경로를 쓴다.
    """
    params: list[tuple[str, Any]] = [("focus", object_id), ("depth", depth)]
    if fanout is not None:
        params.append(("fanout", fanout))
    if limit is not None:
        params.append(("limit", limit))
    if relations:
        params.append(("relations", ",".join(relations)))
    if types:
        params.append(("types", ",".join(types)))
    if records:
        params.append(("records", "true"))
    return await _get(ctx, "/api/graph/neighborhood", params=params)


@tool()
async def graph_overview(ctx: Context) -> Any:
    """**무엇이 무엇과 이어져 있나** — 타입 사이의 지형 한 장(객체가 아니라 타입 수준).

    어느 타입에서 출발해 어디로 갈 수 있는지를 먼저 보고 나서 `graph_neighbors` ·
    `rdf_query` 로 들어간다. 관계 이름을 짐작해 질의를 쓰면 대개 0건이 나오고, 그 0건은
    「없다」 로 잘못 읽힌다.

    돌려주는 것: 타입마다 객체 수, 타입 사이의 관계 종류와 그 수.
    """
    return await _get(ctx, "/api/graph/overview")


@tool()
async def bulk_edit(
    ctx: Context,
    type_slug: str,
    ids: list[str],
    field: str,
    value: Any = None,
    apply: bool = False,
) -> Any:
    """고른 객체들의 **한 칸**을 같은 값으로 — 「등급 A 인 것 전부 B 로」.

    `field` 는 `status` · `description` · `workspace` · `properties.<칸>`. `apply=false`
    (기본)면 계획만 — 몇 건이 바뀌고, 몇 건은 이미 그 값이고, 몇 건은 **왜 안 되나**
    (남의 부서 것)가 행마다 온다. 사람이 보고 판단한 뒤 `apply=true`. 적용 응답의
    `batch_id` 를 **사용자에게 알려 준다** — `bulk_edit_undo` 가 그것으로 한 번에
    되돌린다. 한 칸씩인 이유: 여러 칸을 동시에 바꾸면 실수 한 번의 크기가 수백 배가 된다."""
    return await _post(
        ctx,
        f"/api/objects/{type_slug}/bulk-edit",
        {"ids": ids, "field": field, "value": value, "apply": apply},
    )


@tool()
async def bulk_edit_undo(
    ctx: Context, type_slug: str, batch_id: str, apply: bool = False
) -> Any:
    """`bulk_edit` 한 묶음을 **통째로 되돌린다.** `apply=false` 면 무엇이 되돌아갈지만.
    그 뒤에 따로 고쳐진 객체는 되돌리지 않고 이유를 적는다 — 남의 손이 닿은 것을
    덮으면 그 손실은 아무 데도 안 뜬다."""
    return await _post(
        ctx, f"/api/objects/{type_slug}/bulk-edit/{batch_id}/undo", {"apply": apply}
    )


@tool()
async def audit_recent(
    ctx: Context,
    action: str | None = None,
    target_table: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """**누가 언제 무엇을 바꿨나** — 객체를 가리지 않고 최근 것부터.

    `object_history` 는 객체 하나의 이력이고, 이것은 「어제 무슨 일이 있었나」 다.
    `action`(예 `object.update` · `relation.add` · `ontology.import`) · `target_table` 로
    거른다. 시간 · 사람으로는 못 거른다 — 최근 것부터 받아 여기서 본다.
    **부서 관리자 이상만** 볼 수 있다. 거절되면 그렇게 알린다."""
    params: list[tuple[str, Any]] = [("limit", limit), ("offset", offset)]
    if action:
        params.append(("action", action))
    if target_table:
        params.append(("target_table", target_table))
    return await _get(ctx, "/api/audit/entries", params=params)


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
    key: str | None = None,
    status: str | None = None,
    valid_from_year: int | None = None,
    valid_to_year: int | None = None,
) -> Any:
    """객체를 고친다 — **보낸 것만.**

    `properties` 는 보낸 키만 병합한다. 값을 지우려면 그 키에 `null` 을 넣는다 —
    통째로 덮으면 다른 속성이 함께 날아가고 **그 손실은 아무 데도 안 뜬다.**

    `key` 는 식별자(타입의 식별자 정책을 따른다), `status` 는 `active` ·
    `deprecated`(그만 쓰지만 남긴다 — 지우기 전에 먼저 생각할 자리),
    `valid_from_year` · `valid_to_year` 는 유효 연도.

    `aliases` 는 **다른 이름 전부**(통째로 바꿈). 「Ansys」 를 「앤시스」 로도 부르면 여기
    적는다 — 그 뒤로 찾기·참조·파일이 그 이름으로도 같은 객체를 찾는다. 같은 타입의 다른
    객체가 쓰는 별칭이면 거절된다."""
    body: dict[str, Any] = {
        name: value
        for name, value in (
            ("label", label),
            ("description", description),
            ("properties", properties),
            ("key", key),
            ("status", status),
            ("valid_from_year", valid_from_year),
            ("valid_to_year", valid_to_year),
        )
        if value is not None
    }
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
async def objects_delete(
    ctx: Context,
    type_slug: str,
    ids: list[str],
    mode: str = "block",
    apply: bool = False,
) -> Any:
    """객체를 **지운다** — 하나든 여럿이든(같은 타입). 지워도 행은 남는다
    (`deleted_at` — 몇 년 뒤에도 무엇이었는지 물을 수 있다).

    `apply=False`(기본)면 계획: 줄마다 지울지 · 거절할지와 이유. `apply=True` 는 **지울 수
    있는 줄만 지우고** 막힌 줄은 남긴다(그 이유가 `rows` 에) — 그 결과를 사람에게 전한다.
    `mode="block"`(기본)은 **다른 것이 가리키는 객체를 거절**하고 몇 개가 걸렸는지 말한다.
    `detach` 는 참조를 비우고 관계를 끊고 지운다 — 사람이 그 목록(`object_references`)을
    보고 고른 뒤에만.
    같은 것이 둘이면 지우지 말고 `object_merge`. 그만 쓰는 것이면 지우기보다
    `object_update(status="deprecated")`. 권한은 화면과 같다(부서 관리자는 자기 부서 것).

    가리키는 기록이 많은 객체(2만 건 넘음 — 인기 모델)는 `detach` 로 지울 때 **작업으로**
    돈다 — 도구가 그 줄을 작업으로 넣고 기다린다(`rows[].job`)."""
    if mode not in ("block", "detach"):
        return {"error": f"mode 는 block · detach 중 하나입니다: {mode}"}
    got = await _post(
        ctx,
        f"/api/objects/{type_slug}/bulk-delete",
        {"ids": ids, "mode": mode, "apply": apply},
    )
    if apply and isinstance(got, dict):
        for row in got.get("rows") or []:
            # 요청 안에서 못 한 줄 — 서버가 준 작업 주소로 넣고 기다린다(같은 검사 · 같은
            # 함수).
            if isinstance(row, dict) and row.get("job_path"):
                job = await _post(ctx, str(row["job_path"]), {})
                row["job"] = await _wait_job(ctx, job, JOB_WAIT_MAX)
    return got


@tool()
async def object_restore(ctx: Context, type_slug: str, object_id: str, entry_id: str) -> Any:
    """객체를 **이력의 그 시점 값으로 되돌린다**(`entry_id` 는 `object_history` 의 값 기록).

    저장과 같은 검증을 거친다 — 그때 가리키던 것이 지워졌거나 규칙(종류)이 바뀌어 맞출 수
    없으면 거절하고 이유를 말한다. 되돌린 것도 이력에 한 줄로 남는다. **어느 시점으로
    갈지는 사람이 정한다** — 그 기록의 `snapshot` 을 보이고 확인을 받는다."""
    return await _post(
        ctx, f"/api/objects/{type_slug}/{object_id}/restore", {"entry_id": entry_id}
    )


@tool()
async def object_merge(
    ctx: Context, type_slug: str, object_id: str, into: str, apply: bool = False
) -> Any:
    """같은 것이 둘일 때 **합친다** — `object_id`(지는 쪽)를 `into`(이기는 쪽, 같은 타입)에.
    가리키던 참조 · 관계가 이긴 쪽으로 옮겨 가고, 지는 쪽은 지워져 옛 링크가 새 것으로 간다.

    `apply=False`(기본)면 **아무것도 안 바꾸고** 두 객체와 지는 쪽을 가리키는 것
    (`references`)을 돌려준다 — 사람에게 보이고 어느 쪽이 남을지 확인받는다. 되돌리기가
    없다. 가리키는 기록이 많으면(2만 건 넘음) 작업으로 돌고 도구가 기다린다."""
    if not apply:
        loser = await _get(ctx, f"/api/objects/{type_slug}/{object_id}")
        if not isinstance(loser, dict) or "error" in loser:
            return loser
        winner = await _get(ctx, f"/api/objects/{type_slug}/{into}")
        if not isinstance(winner, dict) or "error" in winner:
            return winner
        return {
            "applied": False,
            "from": _brief(loser),
            "into": _brief(winner),
            "references": await _get(ctx, f"/api/objects/{type_slug}/{object_id}/references"),
            "next": "사람에게 보이고 어느 쪽이 남을지 확인받은 뒤 apply=True 로 부른다.",
        }
    path = f"/api/objects/{type_slug}/{object_id}/merge"
    got = await _post(ctx, path, {"into": into})
    if isinstance(got, dict) and "OBJECTS-0096" in str(got.get("error", "")):
        # 가리키는 기록이 많다(인기 모델) — 요청 안에서 안 끝나 작업으로 합친다.
        return await _wait_job(
            ctx, await _post(ctx, f"{path}/job", {"into": into}), JOB_WAIT_MAX
        )
    return got


def _brief(detail: dict[str, Any]) -> dict[str, Any]:
    """합치기 미리 보기에 실을 객체 한 줄 — 무엇을 견주는지 알 만큼만(상세의 `object`)."""
    row = detail.get("object") or {}
    return {
        name: row.get(name)
        for name in ("id", "key", "label", "owner_workspace_slug", "status", "properties")
    }


@tool()
async def objects_import(
    ctx: Context,
    type_slug: str,
    rows: list[dict[str, Any]],
    workspace_slug: str | None = None,
    aliases_mode: str = "add",
    human_edits: str = "keep",
) -> Any:
    """객체를 **여러 행 한 번에** — 같은 식별자(`key`)면 만들지 않고 고친다(upsert).

    **작업이 된다.** 워커가 계획(행마다 새로/고침/그대로/오류)을 세우고, 작은 것은 이 호출
    안에 끝난다. 돌아온 `status` 가 `done` 이면 `result` 가 계획이다 — **아직 아무것도 안
    들어갔다.** 사용자에게 보여 주고 판단을 받은 뒤 `job_apply(job_id)` 로 넣는다. 아직 도는
    중이면 `job_status(job_id)` 로 다시 묻는다. **한 행이라도 오류면 아무것도 안 넣는다.**

    행은 `{"key": ..., "label": ..., <속성 키>: ...}` 꼴. 없는 키는 안 건드리고,
    비우려면 `null` 을 넣는다. 참조 속성은 상대의 식별자(없으면 이름)로 적어도 된다.
    `workspace_slug` 는 `whoami` 에서 — 비우면 전역이라 시스템 관리자만 된다.

    **별칭**(`aliases`)은 글자 목록이거나 `{"value","source","note"}` 목록이다. 출처를 적어
    두면 나중에 「이건 어디서 온 이름이냐」 를 물을 수 있다 — 모르면 지워도 되는지 판단할 수
    없어 아무것도 못 지운다. 기계가 붙인 별칭은 **검수 대기**로 남는다
    (`aliases_pending` · `aliases_review`).

    `aliases_mode` 는 별칭 칸을 **더할지 맞출지**다. 기본 `add` — 다시 넣어도 사람이 화면에서
    붙여 둔 별칭이 남는다. `replace` 는 **파일에 없는 별칭을 지운다**: 그 파일을 정본으로 볼
    때만 쓴다(허브가 쌍둥이에 보낼 때가 그렇다).

    **키를 바꿀 때**는 행에 `renamed_from`(옛 식별자)을 적는다 — 없으면 같은 것이 새 객체로
    하나 더 생긴다. 두 번 바뀌었으면 `previous_keys` 에 전부 적는다(오래된 것부터).

    `human_edits` 는 **사람이 화면에서 고친 칸**을 어떻게 할지다. 기본 `keep` — 비켜 가고
    계획의 그 줄에 「사람이 고친 칸은 그대로 둡니다 — 이름 · 공급사」 로 적는다. 칸 단위라
    사람이 안 건드린 칸은 그대로 들어간다. **`overwrite` 를 스스로 고르지 않는다** — 사람의
    수정을 되돌리는 일이고, 되돌린 사실은 그 사람에게 안 보인다. 사용자가 「원천이 정본이다」
    라고 말했을 때만 쓴다."""
    fields: dict[str, Any] = {
        "kind": "objects_import",
        "params": json.dumps(
            {
                "type_slug": type_slug,
                "aliases_mode": aliases_mode,
                "human_edits": human_edits,
            }
        ),
    }
    if workspace_slug:
        fields["workspace_slug"] = workspace_slug
    body = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
    job = await _post_form(ctx, "/api/jobs", fields, ("rows.json", body, "application/json"))
    return await _wait_job(ctx, job, JOB_WAIT_MAX)


@tool()
async def bundle_import(ctx: Context, bundle: dict[str, Any]) -> Any:
    """**정의 · 객체 · 관계를 한 묶음으로** — 정제 도구(`pipeline/`)가 만든 결과물.

    **작업이 된다.** 워커가 **한 번에 미리 본다** — 정의를 먼저 적용하지 않아도 그
    정의로 객체와 관계를 맞춰 본다. 돌아온 `result` 는 계획이고 아직 아무것도 안 들어갔다.
    사람이 확인한 뒤 `job_apply(job_id)` — **전부 아니면 무**, 한 곳이라도 오류면 아무것도
    안 들어간다.

    모양: `{"ontology": <ontology_import 와 같은 스키마, 없으면 생략>,
    "objects": [{"type_slug", "workspace_slug", "rows": [...]}],
    "relations": [{"type_slug", "rows": [{"src","relation","dst","evidence_note"}]}]}`.
    행은 `objects_import` · `relations_import` 와 같다. `objects` 는 **적은 차례대로**
    넣는다 — 참조하는 타입을 뒤에. 정의가 들면 시스템 관리자와 `ontology:write`
    범위가 필요하다."""
    job = await _post(ctx, "/api/bundles/import", {**bundle, "apply": False})
    return await _wait_job(ctx, job, JOB_WAIT_MAX)


@tool()
async def bundle_runs(ctx: Context, limit: int = 20) -> Any:
    """**넣은 판들** — 되돌릴 번호를 여기서 찾는다. 최근 것부터.

    적용한 묶음만 있다(미리 보기는 아무것도 안 바꾼다). `undoable` 이 거짓이면 되돌릴 수
    없다 — 이미 되돌렸거나 보관 기간(30일)이 지나 기록을 지웠다."""
    return await _get(ctx, "/api/bundles/runs", {"limit": limit})


@tool()
async def bundle_undo(ctx: Context, run_id: str, apply: bool = False) -> Any:
    """넣은 판 하나를 **통째로 되돌린다** — 백필이 틀렸을 때의 길.

    **작업이 된다. 기본은 계획이다** — 무엇이 지워지고 무엇이 어느 값으로 돌아가는지 줄마다
    온다(`delete` · `update` · `create`). 그 계획을 **사람에게 보여 주고 판단을 받은 뒤**
    `apply=True`(또는 `job_apply(job_id)`) 로 되돌린다. 스스로 되돌리지 않는다 — 되돌리기는
    남의 하루를 지우는 일일 수 있다.

    되돌리지 않는 줄은 `unchanged` 로 이유가 적혀 온다: 그 사이 남이 고친 줄, 밖에서
    가리키는 것이 생긴 객체, 합치기(참조를 옮긴 것이라 손으로 되돌린다). 그 목록도 함께
    사람에게 보여 준다 — 「되돌렸다」 만 말하면 남은 것을 아무도 모른다."""
    job = await _post(
        ctx, f"/api/bundles/runs/{run_id}/undo", None, {"apply": str(apply).lower()}
    )
    return await _wait_job(ctx, job, JOB_WAIT_MAX)


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
    확인하려면 처음부터 다시 조사해야 하기 때문이다. 기계가 이은 것이면 더 그렇다.

    양끝은 관계 종류가 허용한 타입이어야 한다 — 끝에 인터페이스가 적혔으면 그것을 구현한
    타입이면 된다. 안 맞으면 서버가 무엇만 되는지 이름으로 말하며 거절한다."""
    return await _post(
        ctx,
        f"/api/objects/{type_slug}/{object_id}/relations",
        {"relation": relation, "dst_object_id": dst_object_id, "evidence_note": evidence_note},
    )


@tool()
async def relation_update(
    ctx: Context,
    type_slug: str,
    object_id: str,
    relation_id: str,
    evidence_note: str | None = None,
    properties: dict[str, Any] | None = None,
) -> Any:
    """관계의 **근거와 속성**을 고친다 — 보낸 것만. **양끝과 종류는 못 바꾼다** — 그건
    다른 관계다(끊고 새로 잇는다). `relation_id` 는 `object_get` 의 `related[].relation_id`."""
    body: dict[str, Any] = {}
    if evidence_note is not None:
        body["evidence_note"] = evidence_note
    if properties is not None:
        body["properties"] = properties
    return await _patch(
        ctx, f"/api/objects/{type_slug}/{object_id}/relations/{relation_id}", body
    )


@tool()
async def relation_remove(
    ctx: Context, type_slug: str, object_id: str, relation_id: str
) -> Any:
    """관계를 **끊는다.** 되돌리기가 없다 — 관계는 기록이 아니라 두 기록 사이의 말이라
    진짜로 지운다(감사 기록에는 남는다). **틀리게 이은 것을 되돌리는 자리**다. 잘못
    이었는지 확실하지 않으면 끊지 말고 사람에게 `object_get` 결과를 보여 준다."""
    return await _delete(ctx, f"/api/objects/{type_slug}/{object_id}/relations/{relation_id}")


@tool()
async def relations_import(
    ctx: Context,
    type_slug: str,
    rows: list[dict[str, Any]],
    mode: str = "add",
) -> Any:
    """관계를 **여러 줄 한 번에** — `type_slug` 의 객체에서 출발하는 선들. **작업이 된다.**

    행은 `{"src": ..., "relation": ..., "dst": ..., "evidence_note": ...}` 꼴. 끝점은
    식별자(없으면 이름). 이미 이어진 것은 「그대로」 이고, **근거나 붙은 값이 다르면 「고침」**
    이다. 돌아온 `result` 는 계획이다 — 사람이 확인한 뒤 `job_apply(job_id)`.
    **근거(evidence_note)를 적는다** — 기계가 이은 것이면 더.

    선에 붙는 값은 `{"properties": {...}}` 로(인과 관계의 근거 건수처럼). 모양은 그 **관계
    종류의 속성 정의**로 본다 — 정의에 없는 키는 그 줄이 오류다.

    `mode` 는 **더할지 맞출지**다. 사라진 관계를 정리할 길이 이것뿐이다:

      - `add`(기본)      더하기만 한다.
      - `replace`        이 파일에 나온 **(출발 객체 · 관계 종류)** 범위에서, 파일에 없는
                         선을 「끊음」 으로 계획에 올린다.
      - `replace_type`   **(출발 타입 · 관계 종류) 전체**에서. 파일이 그 타입의 **전부**일
                         때만 쓴다 — 일부만 담은 파일로 돌리면 나머지가 다 끊긴다.

    끊는 것은 계획에 `unlink` 로 먼저 보인다 — 사람이 보고 적용한다."""
    fields: dict[str, Any] = {
        "kind": "relations_import",
        "params": json.dumps({"type_slug": type_slug, "relations_mode": mode}),
    }
    body = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
    job = await _post_form(ctx, "/api/jobs", fields, ("rows.json", body, "application/json"))
    return await _wait_job(ctx, job, JOB_WAIT_MAX)


# --------------------------------------------------------------------------- #
# 작업 — 오래 걸리는 일은 요청이 아니라 표에 산다(docs/작업-워커-설계.md)
# --------------------------------------------------------------------------- #
@tool()
async def job_status(ctx: Context, job_id: str, wait_seconds: int = 20) -> Any:
    """작업이 어디까지 됐나 — `objects_import` · `relations_import` · `bundle_import` 가 돌려준
    `job_id` 로. `wait_seconds`(최대 25) 동안 끝나기를 기다렸다가 돌려준다 — 그래도 안 끝났으면
    `status` 가 `running` 인 채로 오고, 그때 다시 부른다. **끝났다고 지어내지 않는다.**"""
    job = await _get(ctx, f"/api/jobs/{job_id}")
    return await _wait_job(ctx, job, float(wait_seconds))


@tool()
async def job_apply(ctx: Context, job_id: str, wait_seconds: int = 20) -> Any:
    """계획을 **사람이 확인한 뒤** 적용한다 — 같은 파일 · 같은 지문으로 적용 작업을 만든다.

    미리 본 뒤에 누군가 그 사이에 같은 것을 바꿨으면 서버가 거절한다(「미리 본 것과
    달라졌습니다」) — 그러면 다시 미리 본다. 오류가 있는 계획은 적용 작업 자체가 안 만들어진다.
    **사용자의 판단 없이 부르지 않는다.**"""
    job = await _post(ctx, f"/api/jobs/{job_id}/apply", None)
    return await _wait_job(ctx, job, float(wait_seconds))


@tool()
async def jobs_list(ctx: Context, limit: int = 20) -> Any:
    """내 작업 최근 것부터 — 무엇이 돌고 있고 무엇이 실패했나. 워커가 살아 있는지도 함께
    (`workers[].alive`) — 워커가 없으면 작업은 영영 대기다. 그때는 운영자에게 알린다."""
    listed = await _get(ctx, "/api/jobs", params=[("limit", limit)])
    workers = await _get(ctx, "/api/jobs/workers")
    if isinstance(listed, dict) and "items" in listed:
        listed["items"] = [_job_view(one) for one in listed["items"]]
        listed["workers"] = workers
    return listed


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
    값·못 푸는 참조는 오류 행이다 — 사용자에게 무엇을 고쳐야 하는지 말한다.

    **작업이 된다** — 바깥 표를 읽는 시간은 그쪽이 정한다. 끝나기를 잠깐 기다렸다가 돌려주고,
    아직이면 `job_status(job_id)` 로 다시 묻는다. 결과(`result`)가 계획 · 기록이다."""
    job = await _post(
        ctx,
        f"/api/datasources/{slug}/sync",
        None,
        params={"apply": "true" if apply else "false"},
    )
    return await _wait_job(ctx, job, JOB_WAIT_MAX)


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
    (`sp:<타입>.<속성키>`) · 관계(`sp:rel.<관계slug>`)와 인터페이스 구현(`rdfs:subClassOf`,
    공통 속성은 `rdfs:subPropertyOf`) · 역관계(`owl:inverseOf`) ·
    이행(`owl:TransitiveProperty`)을 확인한다."""
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


# --- 확장 기능 ---------------------------------------------------------------
#
# **확장마다 도구를 만들지 않는다.** 도구 20개에 확장 셋이 붙으면 목록이 40개가 되고,
# 목록이 길어질수록 모델은 엉뚱한 것을 고른다 — 이 파일 머리의 규칙과 같은 이유다.
# 부를 수 있는 것이 무엇인지는 `extensions_schema` 가 말하고, 부르는 일은 한 도구가 한다.

#: 확장 이름에 허용되는 글자 — 경로를 짓는 값이라 좁게 본다.
_EXT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: 부를 수 있는 메서드. 이 밖은 거절한다 — 도구가 무엇이든 보낼 수 있으면 안 된다.
_EXT_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


@tool()
async def extensions_schema(ctx: Context) -> Any:
    """이 설치에서 **켠 확장**과 그 안에서 부를 수 있는 자리 — 확장 기능의 첫 걸음.

    확장은 이 설치에만 있는 기능 묶음이다(예: 디지털 트윈 역량 — 연계 · 평가 · 인력 ·
    인프라). **`extension_call` 로 부르기 전에 여기서 경로를 본다** — 짐작한 경로는 404 다.

    돌려주는 것: 확장 이름마다 `endpoints[]` = `{method, path, summary, query, body}`.
    `path` 는 확장 뿌리부터이고(`dt/pairs`), 이름 뒤에 `*` 는 **필수**다.

    꺼진 확장은 목록에 없다 — 부를 수도 없다(문이 404 로 답한다).
    """
    return await _get(ctx, "/api/server/extension-api")


@tool()
async def extension_call(
    ctx: Context,
    extension: str,
    path: str,
    method: str = "GET",
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    """확장의 자리 하나를 부른다 — **경로는 `extensions_schema` 가 말한 그대로.**

    `extension` 은 확장 이름(`caegroup`), `path` 는 그 뿌리부터의 경로(`dt/pairs`)다.
    `query` 는 물음표 뒤에 붙고, `body` 는 본문이 있는 메서드에만 쓴다.

    - **검증 · 권한은 서버가 한다.** 내 토큰의 권한으로 도는 것이고, 오류 문구에 무엇을
      고쳐야 하는지가 적혀 있다 — 그것을 그대로 사람에게 전한다.
    - **되돌릴 수 없는 것은 사람에게 먼저 묻는다**(DELETE, 그리고 「일괄」 이 붙은 자리).
      표를 한 번에 저장하는 자리는 한 번에 여러 줄을 바꾼다.
    - 파일을 주는 자리(내려받기)는 여기서 받지 않는다 — 화면에서 내려받는다.
    """
    name = (extension or "").strip()
    if not _EXT_NAME.match(name):
        return {"error": f"확장 이름이 아닙니다: {extension!r}"}
    way = (method or "GET").strip().upper()
    if way not in _EXT_METHODS:
        allowed = ", ".join(_EXT_METHODS)
        return {"error": f"부를 수 없는 메서드입니다: {method!r} (쓸 수 있는 것: {allowed})"}
    # **뿌리 밖으로 못 나간다.** 경로를 문자로 받는 자리라, `..` 나 절대 주소가 들어오면
    # 이 도구가 확장과 무관한 자리를 부르는 길이 된다.
    tail = (path or "").strip().lstrip("/")
    if "://" in tail or ".." in tail or "?" in tail:
        return {
            "error": f"경로에 쓸 수 없는 것이 들어 있습니다: {path!r}"
            " (물음표 뒤는 query 로 줍니다)"
        }
    target = f"/api/ext/{name}/{tail}".rstrip("/")

    async with _client(120) as client:
        response = await client.request(
            way,
            target,
            params=query or None,
            json=body if way in ("POST", "PUT", "PATCH") else None,
            headers=_forward_headers(ctx),
        )
    kind = response.headers.get("content-type", "")
    if response.status_code < 400 and "json" not in kind:
        # 엑셀 · CSV 를 주는 자리다. 바이트를 도구 결과로 흘리면 대화가 쓰레기로 찬다.
        size = len(response.content)
        return {
            "ok": True,
            "message": f"파일 응답입니다({kind or '형식 미지정'}, {size}바이트)"
            " — 내려받기는 화면에서 합니다.",
        }
    return _unwrap(response)
