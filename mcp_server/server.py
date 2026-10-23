"""StandardPlatform MCP 서버 — Claude 가 온톨로지를 읽고 채우게 하는 도구.

백엔드(FastAPI)와 의존성이 충돌(mcp ↔ starlette/pydantic)하므로 **별도 프로세스·
별도 venv** 로 돌리고, 백엔드와는 **REST API** 로만 통신한다. 사용자의 개인 액세스
토큰(Authorization)을 그대로 백엔드에 전달해 **그 토큰의 권한·범위**로 동작한다
(만능 토큰 X). 검증도 권한도 서버가 한다 — 여기에 규칙을 두면 「MCP 로는 되는데
화면에서는 안 되는」 상태가 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다. 하나 있는
예외는 **좁히는 쪽**이다 — 위험 도구(`DESTRUCTIVE_TOOLS`)는 시스템 관리자만(ADR 0030).

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
import contextvars
import functools
import hashlib
import inspect
import json
import os
import re
import shlex
import time
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import quote, urlsplit

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

#: 모든 설치에 같은 안내 — 그 앞에 **이 플랫폼의 자기소개**(`identity`)가 접속마다 붙는다.
BASE_INSTRUCTIONS = (
    "이 설치의 온톨로지를 읽고 쓴다. **`get_guide` 와 `ontology_schema` 를 먼저 "
    "부른다** — 무엇을 만들 수 있고 각 타입이 어떤 값을 받는지가 거기 다 있다.\n"
    "\n"
    "하려는 일 → 부를 것:\n"
    "  어느 타입에 있는지 모른다       search           ← 타입을 모를 때의 첫 걸음\n"
    "  이름으로 무언가를 가리킨다      object_resolve   ← 참조·관계 잇기의 첫 걸음\n"
    "  내 부서 · 내 권한               whoami           (create 의 workspace_slug)\n"
    "  이 플랫폼의 자기소개를 적는다   platform_profile → platform_profile_update\n"
    "  그 타입에 무엇이 있나           objects_list\n"
    "  몇 건인가 · 어떻게 갈리나       objects_summary  (세려고 전부 받지 마라)\n"
    "  요즘 이상한 것 · 무엇부터 볼까  insights         (근거는 metric_analyze)\n"
    "  이 객체의 모든 것               object_get · object_references · object_rollup\n"
    "  계층을 한 단계씩                object_tree\n"
    "  이것과 이어진 것들              graph_neighbors  (타입 지형은 graph_overview)\n"
    "  누가 언제 무엇을 바꿨나         object_history(하나) · audit_recent(전체)\n"
    "  여러 객체의 한 칸을 한 값으로   bulk_edit (apply=false 로 먼저 · undo 있음)\n"
    "  객체를 지운다 · 둘을 합친다     objects_delete · object_merge (apply=false 먼저)\n"
    "  객체를 이력의 그 값으로         object_restore\n"
    "  잘못 이은 관계                  relation_update · relation_remove\n"
    "  여러 타입을 건너뛰는 물음       rdf_query (SPARQL)\n"
    "  어디부터 채우나                 fill_priorities  (무엇이 나쁜가는 quality_report)\n"
    "  못 찾은 이름을 별칭으로         alias_candidates → alias_candidate_apply\n"
    "  여러 행 · 묶음을 넣는다         objects_import · bundle_import → job_apply\n"
    "  원천 파일을 한꺼번에(수만 줄)   정제 도구 키트(사용자 PC 의 MCP) — "
    "화면 「내 정보」 에서 받는다\n"
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
    "`errors`)은 우회하지 않는다 — 먼저 할 일을 사람에게 말한다. 지우기 · 합치기 · "
    "되돌리기 · 계획 적용(`job_apply`) 같은 **위험 도구는 시스템 관리자만** 쓴다(미리 "
    "보기도) — `admin_only` 오류면 화면에서 하거나 시스템 관리자에게 요청하라고 전한다."
)

mcp = FastMCP(
    # 번들 하나로 여러 플랫폼을 띄우므로 이름은 설치(유닛의 APP_SLUG)에서 온다.
    os.environ.get("APP_SLUG", "standardplatform"),
    json_response=_JSON_RESPONSE,
    instructions=BASE_INSTRUCTIONS,
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
    with (
        contextlib.suppress(OSError),
        open(str(_TRACE_PATH), "a", encoding="utf-8") as f,
    ):
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def tool() -> Any:
    """`@tool()` 과 같되 자취를 남긴다 — 등록과 측정을 한 자리에서 정한다.

    도구마다 따로 감싸면 새 도구를 넣은 사람이 그 한 줄을 잊고, 그러면 **그 도구만
    측정에서 빠진다** — 빠진 줄은 「안 쓴 도구」 처럼 보인다. **위험 도구의 관리자 확인**
    (`_admin_only`)도 같은 이유로 여기서 건다 — 새 위험 도구를 목록에 넣는 것만으로 걸린다.
    """

    def wrap(fn: Any) -> Any:
        return mcp.tool()(_traced(_admin_only(fn)))

    return wrap


ADMIN_ONLY_MESSAGE = (
    "`{name}` 은 위험 도구(지우기 · 되돌리기 · 덮어쓰기 · 멈추기)라 MCP 로는 "
    "**시스템 관리자만** 씁니다 — 미리 보기도 마찬가지입니다. 사용자에게 화면에서 "
    "하거나 시스템 관리자에게 요청하라고 전합니다(다른 도구로 우회하지 않습니다)."
)


def _admin_only(fn: Any) -> Any:
    """**위험 도구는 시스템 관리자만**(ADR 0030) — `DESTRUCTIVE_TOOLS` 의 도구.

    이 파일의 원칙은 「권한은 서버가 본다」 다 — 그런데 서버의 권한은 화면의 권한이라
    부서 관리자도 자기 부서 객체를 지우고 합친다. 사람이 화면에서 누르는 것과 AI 가 기계
    속도로 부르는 것은 무게가 달라서, **MCP 로는 그보다 좁힌다**(넓히지는 않는다 — 「MCP
    로는 되는데 화면에서는 안 되는」 상태는 여전히 안 생긴다). 확인은 부를 때마다
    `GET /api/auth/me` 한 번 — 토큰의 주인이 그 사이 관리자에서 빠지면 바로 막힌다.

    `extension_call` 은 확장의 **읽기(GET)** 에도 쓰이므로 읽기는 그대로 두고 쓰기만 막는다.
    """
    if fn.__name__ not in DESTRUCTIVE_TOOLS:
        return fn
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def inner(ctx: Any, *args: Any, **kwargs: Any) -> Any:
        if fn.__name__ == "extension_call":
            bound = signature.bind(ctx, *args, **kwargs)
            bound.apply_defaults()
            if str(bound.arguments.get("method") or "GET").upper() == "GET":
                return await fn(ctx, *args, **kwargs)
        me = await _get(ctx, "/api/auth/me")
        if isinstance(me, dict) and "error" in me:
            return me
        if not (isinstance(me, dict) and me.get("is_system_admin")):
            return {"error": ADMIN_ONLY_MESSAGE.format(name=fn.__name__), "admin_only": True}
        return await fn(ctx, *args, **kwargs)

    return inner


#: **한 번의 잘못 부름이 곧 지움 · 덮어쓰기 · 되돌리기 · 멈춤이 되는 도구** — HWAX 게이트웨이에
#: 범용 실행기(`invoke_tool`)로는 못 부르게 `_INVOKE_DENY_EXACT` 에 넣어 달라고 요청하는
#: 목록이다 (요청서 「위험 도구」, ADR 0028). 게이트웨이의 이름 규칙(`delete_` · `remove_` ·
#: `cancel_` 로 **시작**)은 우리 이름(`objects_delete` · `relation_remove` · `job_cancel`)에 안
#: 걸린다.
#:
#: 차단은 게이트웨이가 **해석한 원래 이름**으로 보므로 인스턴스가
#: 여럿이어도(`<id>_objects_delete`) 이 목록 하나로 덮인다. 사람이 고른 도구(직접 바인딩)는
#: 막히지 않는다. 여기에 더하거나 빼면 요청서 틀(`docs/[참고] HWAX포탈_연동_요청서.md`)도 같이
#: — 시험이 둘을 견준다. 더하는 기준은 「미리 보기 없이 바로 바꾸거나, apply 한 번에 여러 줄을
#: 바꾸거나, 사라진 것을 되살릴 수 없는가」.
#:
#: **이 목록의 도구는 MCP 로는 시스템 관리자만 쓴다**(`_admin_only`, ADR 0030) — 게이트웨이
#: 차단과 별개로, 사람이 직접 고른 도구(직접 바인딩)여도 그렇다.
DESTRUCTIVE_TOOLS = frozenset(
    {
        "objects_delete",  # 객체 삭제(여럿)
        "object_merge",  # 둘을 하나로 — 하나가 지워진다
        "object_restore",  # 지금 값을 이력의 그 값으로 덮는다
        "attachment_remove",  # 첨부 삭제
        "relation_remove",  # 관계 삭제
        "ontology_delete",  # 정의(타입 · 속성 · 관계 종류) 삭제
        "ontology_restore",  # 정의 전체를 그때로 되돌린다
        "ontology_retype",  # 속성 종류 변경 — 모든 객체의 값을 바꾼다
        "ontology_rename_option",  # 고를 값 이름 변경 — 모든 객체의 값을 바꾼다
        "bulk_edit",  # 여러 객체의 한 칸을 한 값으로
        "bulk_edit_undo",
        "bundle_undo",  # 묶음으로 넣은 것을 걷어낸다
        "job_apply",  # 계획(수천 줄)을 반영한다
        "job_cancel",
        "filestore_gc",  # 가리키는 곳 없는 파일을 지운다
        "review_decide",  # 검토함 결정을 객체에 반영한다
        "review_undo",  # 검토함 묶음을 되돌린다
        "config_delete",  # 지표 · 경보 · 데이터 소스 · 뷰 · 공지 · 부서 삭제
        "jobs_purge",  # 작업 파일 · 끝난 작업 기록 삭제
        "extension_call",  # 확장 API 를 그대로 부른다 — DELETE 도 된다
    }
)


def _forward_headers(ctx: Context) -> dict[str, str]:
    """들어온 MCP HTTP 요청의 인증 헤더를 백엔드로 전달.

    MCP 는 **사용자의 토큰으로** 동작한다. 백엔드가 토큰 이름을 감사 기록에 남기므로
    사람이 한 건지 기계가 한 건지는 거기서 갈린다.

    **`Authorization` 만 넘긴다.** HWAX 게이트웨이가 사람별 호출에 함께 싣는 소속
    (`X-Heax-User-Affiliation` · 증명 `X-Heax-Aff-Proof`)은 버린다 — 이 플랫폼은 무엇을 볼 수
    있나를 부서와 권한으로 정하고 「같은 소속이면 읽기」 같은 공유를 두지 않는다. 받아 넘기면
    언젠가 누가 그 머리를 믿는 코드를 쓰고, 그 머리는 게이트웨이 밖에서 아무나 적을 수 있다."""
    headers: dict[str, str] = {}
    req = getattr(getattr(ctx, "request_context", None), "request", None)
    if req is not None:
        v = req.headers.get("authorization")
        if v:
            # 백엔드가 기대하는 표기로.
            headers["Authorization"] = v
    return headers


class BackendUnauthorized(Exception):
    """백엔드가 **이 토큰을 거절했다**(401) — 도구 결과가 아니라 예외로 올린다.

    HWAX 게이트웨이는 사람마다 그 사람의 위임 토큰(우리 `POST /api/auth/sso` 가 하루짜리로
    낸다)을 12시간 들고 있다가, 도구 결과가 이 꼴일 때만 **새로 받아 한 번 다시 부른다**:

        ^Error executing tool \\S+: (GET|POST|PUT|PATCH|DELETE) \\S+ → HTTP 401

    FastMCP 는 도구에서 난 예외를 `Error executing tool <이름>: <예외 문구>` 로 감싸 오류
    결과로 낸다 — 그래서 문구를 `<METHOD> <경로> → HTTP 401: <서버의 말>` 로 지으면 그 정규식에
    맞는다. 401 을 다른 오류처럼 `{"error": "[AUTH-…] …"}` 결과로 돌려주던 때는 그 신호가 안
    닿아, 회수된 토큰으로 12시간 동안 「토큰이 유효하지 않습니다」 만 받았을
    것이다(게이트웨이가 ste 에서 겪은 일). 401 은 인증에서 끊긴 것이라 도구는 아무것도 안
    했다 — 다시 불러도 두 번 실행되지 않는다.

    게이트웨이를 안 거치는 사람(자기 토큰으로 직접)에게도 같은 문구가 간다 — 무엇을 부르다 왜
    막혔는지가 그대로 읽힌다(토큰을 새로 만들면 된다).
    """


def _rejected(r: httpx.Response) -> BackendUnauthorized:
    """`<METHOD> <경로> → HTTP 401: <서버의 말>` — 경로는 물음표 앞까지(게이트웨이의
    `\\S+`)."""
    try:
        method = r.request.method
        path = r.request.url.raw_path.decode("ascii", "replace").split("?", 1)[0]
    except RuntimeError:  # 요청 없이 만든 응답(시험) — 실제 호출에는 늘 있다
        method, path = "GET", "/"
    said = "토큰이 없거나 유효하지 않습니다"
    try:
        body = r.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        said = f"[{error.get('code') or 401}] {error.get('message') or said}"
    return BackendUnauthorized(f"{method} {path} → HTTP 401: {said}")


def _unwrap(r: httpx.Response) -> Any:
    """백엔드 응답 언래핑. 오류 봉투 {error: {code, message, details}} 면 {error,...}.

    **401 만은 예외로 올린다**(`BackendUnauthorized`) — 게이트웨이가 토큰을 다시 받는
    신호다."""
    if r.status_code == 401:
        raise _rejected(r)
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


#: GET 을 얼마나 기다리나 — 백엔드의 읽기 시간 상한(기본 60초)보다 길어야 한다(`_get`).
_READ_WAIT_SECONDS = 90.0


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE, timeout=timeout, transport=_TRANSPORT)


# --------------------------------------------------------------------------- #
# 이 플랫폼의 자기소개 — 같은 도구를 가진 플랫폼 여럿 가운데 어디에 물을지
# --------------------------------------------------------------------------- #
#: 같은 틀(Standard Platform)로 띄운 플랫폼은 도구 이름 · 설명이 전부 같다. 한 에이전트에
#: 여럿이 붙으면 고를 단서가 서버 이름(slug) 하나뿐이라, **접속마다** 안내문 첫머리에 이
#: 플랫폼의 자기소개를 싣는다(ADR 0019): 사람이 쓴 소개 + **접속 때 센 자동 요약**(담긴 것 ·
#: 들어오는 곳 · 지표) + 사람이 쓴 뒤 달라진 것(낡음). 요약은 접속한 사람의 토큰으로 읽는다 —
#: 토큰이 없으면 로그인 없이 읽히는 사람의 문장만.
PROFILE_TTL = 60.0
PROFILE_PATH = "/api/server/profile"
PROFILE_LIVE_PATH = "/api/server/profile/live"

#: 안내문을 만드는 자리는 동기 함수다(라이브러리의 `create_initialization_options`) — 시험이
#: 여기에 가짜 응답을 붙인다.
_SYNC_TRANSPORT: httpx.BaseTransport | None = None
#: 토큰마다(지문으로) 붙들어 둔다 — 접속마다 백엔드에 묻지 않게, 그러나 사람마다 보이는 것이
#: 다를 수 있어 섞지 않는다.
_profile_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_MAX = 256

#: 접속(초기화) 요청에 실려 온 `Authorization` — `_RememberToken` 이 요청마다 넣는다.
#: 라이브러리는 그 요청을 처리하는 흐름에서 접속 작업을 띄우므로 작업이 이 값을 물려받는다.
_CONNECTING_TOKEN: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "connecting_token", default=None
)

SAME_TOOLS_RULE = (
    "같은 틀로 띄운 다른 플랫폼도 **같은 이름의 도구**를 낸다. 물음의 대상이 이 "
    "플랫폼에 있는지 위 소개와 `ontology_schema` 로 먼저 보고, 여기서 0건이거나 그런 "
    "타입이 없으면 「없다」 가 아니라 「이 플랫폼에는 없다」 로 말한다 — 다른 플랫폼에 "
    "있을 수 있다. 같은 코어 타입이 여러 플랫폼에 있으면 소개가 말하는 정본에 묻는다."
)


def _fetch_profile(authorization: str | None) -> dict[str, Any] | None:
    """토큰이 있으면 `live`(요약 · 낡음까지), 없거나 거절되면 로그인 없는 소개."""
    headers = {"Authorization": authorization} if authorization else {}
    paths = [PROFILE_LIVE_PATH, PROFILE_PATH] if authorization else [PROFILE_PATH]
    try:
        with httpx.Client(base_url=API_BASE, timeout=2.0, transport=_SYNC_TRANSPORT) as client:
            for path in paths:
                got = client.get(path, headers=headers if path == PROFILE_LIVE_PATH else {})
                if got.status_code == 200:
                    body = got.json()
                    if isinstance(body, dict):
                        return body
    except (httpx.HTTPError, ValueError):
        return None
    return None


def profile_now(authorization: str | None = None) -> dict[str, Any] | None:
    """자기소개 — 토큰마다 짧게 붙들어 둔다. 백엔드가 안 닿으면 붙들던 것(없으면 None)."""
    key = hashlib.sha256((authorization or "").encode()).hexdigest()[:16]
    held = _profile_cache.get(key)
    if held and time.monotonic() - held[0] < PROFILE_TTL:
        return held[1]
    fresh = _fetch_profile(authorization)
    if fresh is None:
        return held[1] if held else None
    if len(_profile_cache) >= _CACHE_MAX:
        _profile_cache.clear()
    _profile_cache[key] = (time.monotonic(), fresh)
    return fresh


def identity(profile: dict[str, Any] | None) -> str:
    """안내문 첫머리 — 이 서버가 **어느 플랫폼의 것이고 지금 무엇을 담나.**"""
    slug = str((profile or {}).get("slug") or os.environ.get("APP_SLUG", "standardplatform"))
    if not profile:
        head = (
            f"**이 서버는 플랫폼 `{slug}` 의 것이다.** (자기소개를 읽지 못했다 — "
            "`whoami` 의 `platform` 을 본다.)"
        )
        return f"{head}\n{SAME_TOOLS_RULE}\n\n"
    name = str(profile.get("name") or slug)
    tagline = str(profile.get("tagline") or "").strip()
    lines = [
        f"**이 서버는 「{name}」(`{slug}`)의 것이다.**" + (f" {tagline}" if tagline else "")
    ]
    summary = str(profile.get("summary") or "").strip()
    written = str(profile.get("updated_at") or "")[:10]
    facts = [one for one in profile.get("facts") or [] if isinstance(one, dict)]
    # 토큰 없이 접속했으면 자동 요약이 없다 — 그때는 어디서 보는지 말한다.
    elsewhere = "아래 「지금 담긴 것」" if facts else "`whoami` 의 `platform.facts`"
    lines.append(
        f"담는 것(사람이 쓴 소개{', ' + written if written else ''}): {summary}"
        if summary
        else f"담는 것: (사람이 쓴 소개가 아직 없다 — {elsewhere} 을 본다)"
    )
    notes = str(profile.get("notes") or "").strip()
    if notes:
        lines.append(f"다른 플랫폼과의 사이: {notes}")
    if facts:
        lines.append("지금 담긴 것(접속 때 센 것):")
        for one in facts:
            for line in one.get("lines") or []:
                lines.append(f"  - {one.get('label')}: {line}")
    stale = [str(one) for one in profile.get("stale") or [] if summary]
    if stale:
        lines.append(
            "⚠ 사람이 쓴 소개는 그 뒤 달라진 것을 반영하지 않았다 — "
            + " · ".join(stale[:5])
            + (f" 외 {len(stale) - 5}" if len(stale) > 5 else "")
            + ". 엇갈리면 「지금 담긴 것」 을 믿는다."
        )
    lines.append(SAME_TOOLS_RULE)
    return "\n".join(lines) + "\n\n"


def _install_identity() -> None:
    """접속(세션)마다 안내문을 다시 만든다 — 라이브러리가 접속마다
    `create_initialization_options` 를 부르고 그때 `instructions` 를 읽는다. 시험의 가짜
    FastMCP 에는 그 자리가 없다."""
    low = getattr(mcp, "_mcp_server", None)
    original = getattr(low, "create_initialization_options", None)
    if low is None or original is None:
        return

    def with_identity(*args: Any, **kwargs: Any) -> Any:
        profile = profile_now(_CONNECTING_TOKEN.get())
        low.instructions = identity(profile) + BASE_INSTRUCTIONS
        return original(*args, **kwargs)

    low.create_initialization_options = with_identity


_install_identity()


class _RememberToken:
    """접속 요청의 토큰을 기억하는 껍데기 — 안내문을 **그 사람의 눈으로** 만들려고. 토큰을
    어디에도 남기지 않는다(요청이 끝나면 되돌린다)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        token = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers") or []
                if name.lower() == b"authorization"
            ),
            None,
        )
        reset = _CONNECTING_TOKEN.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _CONNECTING_TOKEN.reset(reset)


def http_app() -> Any:
    """운영이 띄우는 앱 — 라이브러리의 streamable-http 앱을 토큰 껍데기로 감싼다."""
    return _RememberToken(mcp.streamable_http_app())


async def _get(
    ctx: Context,
    path: str,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
) -> Any:
    # 백엔드의 읽기 시간 상한(`READ_QUERY_SECONDS`, 기본 60초 — ADR 0031)보다 길게 기다린다.
    # 같거나 짧으면 백엔드가 「좁히거나 지표로」 를 504 로 내기 전에 여기서 끊겨, AI 는 그
    # 안내 대신 맨 시간 초과를 받는다.
    async with _client(_READ_WAIT_SECONDS) as client:
        return _unwrap(await client.get(path, params=params, headers=_forward_headers(ctx)))


async def _post(
    ctx: Context,
    path: str,
    json_body: Any,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
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


async def _put(ctx: Context, path: str, json_body: Any) -> Any:
    async with _client(60) as client:
        return _unwrap(await client.put(path, json=json_body, headers=_forward_headers(ctx)))


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


#: 계획 → **`job_apply`** 로 적용하는 작업 종류(백엔드 `jobs/kinds.py` 의 두 단계 종류).
_APPLY_BY_JOB = frozenset(
    {
        "objects_import",
        "relations_import",
        "bundle_import",
        "bundle_undo",
        "ontology_retype",
        "filestore_gc",
    }
)
#: 계획을 낸 **도구를 `apply=true` 로 다시 불러** 적용하는 종류 — `job_apply` 로는 안 된다.
_APPLY_BY_TOOL = {"datasource_sync": "datasource_sync(slug, apply=true)"}


def _job_view(job: Any) -> Any:
    """AI 에게 주는 작업 모양 — 상태와 결과, 그리고 **다음에 할 일**(종류마다 다르다:
    `job_apply` 로 적용하는 계획 · 도구를 `apply=true` 로 다시 부르는 계획 · 파일을 내는
    작업 · 그냥 끝나는 작업)."""
    if not isinstance(job, dict) or "id" not in job:
        return job
    status = job.get("status")
    kind = job.get("kind")
    out: dict[str, Any] = {
        "job_id": job["id"],
        "kind": kind,
        "status": status,
        "progress": job.get("progress"),
    }
    if status == "done":
        out["result"] = job.get("result")
        result = job.get("result") or {}
        applied = bool(result.get("applied"))
        has_errors = bool(result.get("errors")) or bool(
            (result.get("counts") or {}).get("error")
        )
        # 두 단계 종류는 늘 계획을 낸다 — 결과에 `applied` 가 없어도.
        is_plan = not applied and ("applied" in result or kind in _APPLY_BY_JOB)
        if applied:
            out["next"] = "적용됐다. 결과를 사용자에게 요약한다."
        elif job.get("applied_by"):
            out["applied_by"] = job["applied_by"]
            out["next"] = (
                "이 계획은 이미 적용됐다(또는 적용 중이다) — `applied_by` 작업을 "
                "job_status 로 본다."
            )
        elif is_plan and has_errors:
            out["next"] = (
                "오류가 있는 계획이다 — 적용할 수 없다. 오류를 사용자에게 보이고 고쳐서 다시 "
                "넣는다."
            )
        elif is_plan and result.get("ok") is False:
            out["next"] = (
                "적용할 것이 없는 계획이다(`result.summary` · `result.errors` 가 까닭) — "
                "그것을 사용자에게 전한다."
            )
        elif is_plan and kind in _APPLY_BY_JOB:
            out["next"] = (
                "계획이다 — 아직 아무것도 안 들어갔다. 사용자에게 보여 주고 판단을 받은 뒤 "
                "job_apply(job_id) 로 적용한다."
            )
        elif is_plan and kind in _APPLY_BY_TOOL:
            out["next"] = (
                "계획이다 — 아직 아무것도 안 들어갔다. 사용자에게 보여 주고 판단을 받은 뒤 "
                f"{_APPLY_BY_TOOL[kind]} 로 적용한다(job_apply 로는 안 된다)."
            )
        elif job.get("has_output"):
            out["next"] = (
                "끝났다 — 만든 파일은 화면의 「작업」 에서 받는다. 결과를 사용자에게 요약한다."
            )
        else:
            out["next"] = "끝났다. 결과를 사용자에게 요약한다."
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
    "metrics",
    "reports",
    "platforms",
    "admin",
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
      - `metrics` 지표 — 미리 세어 둔 값으로 비율 · 추이 · 코호트에 답하기
      - `extensions` 이 설치에만 있는 기능(확장) — 무엇을 부를 수 있나
      - `reports` 보고서 기록 — RA 에서 쌓인 보고서에 묻기
      - `platforms` 같은 도구를 가진 플랫폼이 여럿일 때 — 어디에 물을지 · 자기소개
      - `admin` 운영 · 설정 — 코어 공개 현황 · 부서 · 뷰 · 지켜보기 · 서버 · 지우기

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
            return {
                "error": f"그런 주제가 없습니다: {topic}",
                "topics": sorted(secs.keys()),
            }
        return {"guide_version": version, "topic": key, "content": secs[key]}
    # **어느 플랫폼의 안내서인가** — 같은 안내서를 여러 플랫폼이 낸다.
    platform = await _get(ctx, PROFILE_LIVE_PATH)
    return {
        "guide_version": version,
        "topic": "overview",
        "platform": platform
        if isinstance(platform, dict) and "error" not in platform
        else None,
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
    쓰기가 거절되면 `memberships[].role` 을 보고 사용자에게 알린다.

    `platform` 은 **지금 어느 플랫폼에 묻고 있나** — 이름 · 사람이 쓴 소개(`summary` ·
    `notes`) · 지금 담긴 것(`facts`, 읽을 때 센다) · 사람이 쓴 뒤 달라진 것(`stale`). 같은
    도구를 가진 플랫폼이 여럿 붙어 있으면 이것으로 가른다."""
    me = await _get(ctx, "/api/auth/me")
    if isinstance(me, dict) and "error" not in me:
        platform = await _get(ctx, PROFILE_LIVE_PATH)
        if isinstance(platform, dict) and "error" not in platform:
            me = {**me, "platform": platform}
    return me


@tool()
async def platform_profile(ctx: Context) -> Any:
    """**이 플랫폼의 자기소개 · 지금 담긴 것 · 낡음** — 소개를 고치기 전에 부른다.

    같은 틀로 띄운 플랫폼 여럿이 한 에이전트에 붙으면 도구가 전부 같아서, 에이전트는 접속 때
    안내문 첫머리에 실리는 이 소개로 어디에 물을지 고른다.

    - `summary` · `notes` — **사람이 쓴 소개**(무엇을 담나 · 다른 플랫폼과의 사이). 쓴 날에
      멈춘다.
    - `facts` — **지금 담긴 것**(읽을 때 센다, 저장 안 함): 기록 · 축과 건수, 들어오는
      곳(데이터 소스), 정본이 바깥인 타입(`managed_by`), 코어 공개, 지표, 확장. 늘 지금이라
      손댈 것이 없다.
    - `stale` — 사람이 쓴 뒤 **생기고 없어진 것**(타입 · 데이터 소스 · 정본 · 켠 확장).
      비었으면 소개가 지금과 맞다. 차 있으면 시스템 관리자의 홈 「남은 일」 에도 뜬다.

    **소개를 고치는 법:** `facts` 와 `stale` 을 보고 `summary`(300자 — 무엇을 담나, 어디서
    오나)와 `notes`(2,000자 — 정본은 어디인가, 무엇은 여기 없나)를 고쳐
    `platform_profile_update(apply=false)` 로 사람에게 보인다. 건수처럼 자동 요약에 이미 있는
    것은 옮겨 적지 않는다(사람의 글은 **뜻** — 무엇을 하러 오는 곳인가 — 을 쓴다). **로그인
    없이 읽힌다** — 토큰 · 내부 주소 · 사람 이름을 적지 않는다."""
    return await _get(ctx, PROFILE_LIVE_PATH)


@tool()
async def platform_profile_update(
    ctx: Context,
    summary: str | None = None,
    notes: str | None = None,
    apply: bool = False,
) -> Any:
    """자기소개를 고친다 — **시스템 관리자만**(토큰이면 `ontology:write`). 안 보낸 칸은 그대로.

    `apply=false`(기본)는 미리 보기: 지금 → 바꿀 것, 그리고 **다른 에이전트가 볼 안내문
    첫머리**(자동 요약까지). 사람이 확인한 뒤 `apply=true`. 저장하면 「지금 담긴 것을 보고
    썼다」 가 남아 낡음(`stale`)이 비워진다 — 글을 안 바꾸고 저장만 해도 그 확인이 된다. 새
    접속부터 실린다(1분 안). 비우려면 `""` 를 보낸다."""
    if summary is None and notes is None:
        return {"error": "summary 나 notes 중 하나는 보내야 합니다."}
    current = await _get(ctx, PROFILE_LIVE_PATH)
    if not isinstance(current, dict) or "error" in current:
        return current
    after = {
        "summary": (current.get("summary") or "") if summary is None else summary.strip(),
        "notes": (current.get("notes") or "") if notes is None else notes.strip(),
    }
    problems = [
        f"{key} 는 {limit:,}자까지입니다(지금 {len(after[key]):,}자)."
        for key, limit in (("summary", 300), ("notes", 2000))
        if len(after[key]) > limit
    ]
    if not apply:
        return {
            "applied": False,
            "before": {
                "summary": current.get("summary"),
                "notes": current.get("notes"),
            },
            "after": after,
            "instructions_preview": identity({**current, **after, "stale": []}),
            "problems": problems,
            "note": "사람이 확인한 뒤 apply=true 로 부른다.",
        }
    if problems:
        return {"error": " ".join(problems)}
    async with _client(60) as client:
        saved = _unwrap(
            await client.put(PROFILE_PATH, json=after, headers=_forward_headers(ctx))
        )
    if isinstance(saved, dict) and "error" not in saved:
        _profile_cache.clear()  # 다음 접속이 새로 읽는다
        return {
            "applied": True,
            "profile": saved,
            "note": "새 접속부터 안내문에 실린다 — 이미 열린 접속은 그대로다.",
        }
    return saved


@tool()
async def search(
    ctx: Context, q: str, type_slug: str | None = None, limit: int = 20, offset: int = 0
) -> Any:
    """**타입을 모를 때** 이름·식별자·별칭으로 전부 찾는다 — 「앤시스 관련된 거 뭐 있어」.

    `types[]` 가 타입별 건수라 **어느 타입에 있는지**가 먼저 보인다. 타입을 알면
    `objects_list`(조건 거르기) · `object_resolve`(하나로 정하기)가 낫다 — 이것은
    「어디 있나」 를 묻는 도구지 「어느 것인가」 를 정하는 도구가 아니다.
    볼 수 있는 것만 나온다(기본은 전부 — 조회를 부서로 좁힌 설치는 남의 부서 것이 수에도 안
    잡힌다). `type_slug` 에 인터페이스를
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
        return {
            "ok": True,
            "message": f"지웠습니다 — {kind} {slug}{f'.{key}' if key else ''}",
        }
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
# 긴 글 칸(보고서 본문 같은 것)은 잘라서 준다 — 목록 50건이 본문째 오면 답 하나가 문맥을
# 채우고, 정작 읽어야 할 것을 밀어낸다. 잘랐으면 **잘랐다고** 적는다(`clipped`) — 앞부분을
# 전부로 읽지 않게.
LIST_TEXT = 300
GET_TEXT = 6000


def _clip(row: Any, *, start: int, size: int) -> None:
    """`row.properties` 의 `size` 보다 긴 글을 `[start, start+size)` 만 남기고, 무엇을
    잘랐는지 `row.clipped[<칸>] = {from, to, length}` 로 적는다."""
    properties = row.get("properties") if isinstance(row, dict) else None
    if not isinstance(properties, dict):
        return
    clipped: dict[str, dict[str, int]] = {}
    for key, value in properties.items():
        if isinstance(value, str) and len(value) > size:
            begin = min(max(start, 0), len(value))
            end = min(len(value), begin + size)
            properties[key] = value[begin:end]
            clipped[key] = {"from": begin, "to": end, "length": len(value)}
    if clipped:
        row["clipped"] = clipped


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
      선택·참조 `eq ne in` · 참/거짓 `eq` · 모두 `empty notempty`. 참조 칸 · 자신(`id`)에는
      `under`(하위 전체 — 그 객체이거나 트리로 그 아래 전부, 트리가 있는 타입만).
      `in` 의 값은 `|` 로 잇는다(`"A|B"`). `field` 는 속성 키 또는 `label`·`key`·`status`.
      **다른 타입의 칸**도 된다 — `ref.vendor.country`(참조 칸이 가리키는 것의 칸),
      `out.used_by`(관계로 이어진 것 자체), `in.resells.country`. 주소는 `object_fields`
      가 준다 — 추측하지 않는다.
      **제외**: 조건에 `"exclude": 1`(묶음 번호)을 주면 같은 번호끼리 AND 로 묶어 **그 묶음에
      맞는 것을 뺀다**(`nf1.<칸>.<연산>`). 칸이 비어 비교가 안 되는 기록은 남는다. 지표 건
      보기의 `nf<번호>.<칸>.<연산>` 키는 `{"field", "op", "value", "exclude": <번호>}` 로 푼다.
    - `status`: `active` · `deprecated`.

    응답의 `total` 이 전체 수다 — 한 쪽(limit ≤ 200)씩 `offset` 으로 넘긴다.
    **몇 건인지 세려면 전부 받지 말고 `objects_summary`.**

    **0건이면 `diagnosis` 가 붙는다** — 「타입에 객체가 없음(empty_type)」 · 「부서 밖이라
    안 보임(not_visible)」 · 「조건이 좁음(filters)」 중 무엇인지, 조건 때문이면 어느 조건을
    빼면 몇 건인지, 그 중 **값이 비어 있어서** 빠진 것이 몇 건인지까지 있다(인터페이스는
    「구현한 타입이 없음(no_implementers)」 도 있다). 읽지 않고
    「없습니다」 라고 답하지 마라 — 있는 것을 없다고 하면 사람은 그것을 새로 만든다.

    **긴 글 칸(보고서 본문 같은 것)은 앞 300자만** 온다 — 줄의 `clipped` 가 그 칸의 전체
    길이를 말한다. 내용으로 답하려면 그 객체를 `object_get` 으로 읽는다."""
    filters = _filter_params(q, status, properties, conditions)
    params: list[tuple[str, Any]] = [("limit", limit), ("offset", offset), *filters]
    found = await _get(ctx, f"/api/objects/{type_slug}", params=params)
    if isinstance(found, dict):
        for row in found.get("items") or []:
            _clip(row, start=0, size=LIST_TEXT)
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
        field, op, value = (
            one.get("field", ""),
            one.get("op", "eq"),
            one.get("value", ""),
        )
        # 제외 묶음(ADR 0015) — 같은 번호끼리 AND 로 묶어 뺀다. 지표 건 보기의 `nf<번호>.` 키.
        group = str(one.get("exclude") or "").strip()
        head = f"nf{group}" if group and group != "0" else "f"
        params.append((f"{head}.{field}.{op}", value))
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
async def alias_candidates(
    ctx: Context, type_slug: str | None = None, status: str = "pending", limit: int = 20
) -> Any:
    """**못 찾은 말**(별칭 후보) — 사람과 AI 가 이름으로 찾다가(`object_resolve` · `search` ·
    `objects_list(q=)`) 아무것도 못 찾은 글자, 많이 · 여럿이 찾은 것부터(`hits` · `people`).

    그 말이 어떤 객체의 다른 이름이면 `alias_candidate_apply(action="attach")` 로 그 객체의
    별칭으로 붙인다 — **다음부터 찾힌다.** 줄마다 `suggestions`(「이것 아닐까」 — 이름 ·
    식별자 · 별칭이 비슷한 것)가 오지만 **짐작이다**: 사람에게 보이고 어느 객체인지 받은 뒤에
    붙인다. 별칭이 될 말이 아니면(오타 · 엉뚱한 말) `action="ignore"`.

    `type_slug` 로 그 타입에서 못 찾은 것만(빈 글자 `""` 면 통합 검색에서 못 찾은 것).
    `status` 는 `pending`(기본) · `attached` · `ignored` · `all`. 부서 관리자 이상. 누가
    찾았는지는 남지 않는다 — 몇 사람인지만."""
    params: list[tuple[str, Any]] = [("status", status), ("limit", str(limit))]
    if type_slug is not None:
        params.append(("type", type_slug))
    return await _get(ctx, "/api/objects/alias-candidates", params=params)


@tool()
async def alias_candidate_apply(
    ctx: Context,
    candidate_id: str,
    action: str = "attach",
    object_id: str | None = None,
    value: str | None = None,
    apply: bool = False,
) -> Any:
    """별칭 후보 하나를 처리한다 — `attach`(그 객체의 별칭으로) · `ignore`(무시) ·
    `restore`(대기로 되돌림).

    **`apply=False`(기본)면 아무것도 안 바꾼다** — `attach` 는 계획(붙기 전 · 뒤 별칭,
    `warnings`, `blocking`)을, `ignore` · `restore` 는 그 후보를 돌려준다. 사람에게 보이고
    확인받은 뒤 `apply=True` 로 부른다. `blocking` 이 있으면(다른 객체가 그 별칭을 쓴다) 붙지
    않는다 — 우회하지 말고 사람에게 말한다.

    `object_id` 는 `alias_candidates` 의 `suggestions` 나 `object_resolve` 로 정한 id 다 —
    **스스로 고르지 마라.** 별칭은 그 뒤로 이름 풀이 · 검색 · 파일 적재가 전부 쓰므로, 틀리게
    붙인 별칭은 틀린 객체로 풀린다. `value` 를 주면 그 표기로 붙인다(비우면 못 찾은 말
    그대로)."""
    if action == "attach":
        if not object_id:
            return {
                "error": "attach 에는 object_id 가 필요합니다 — 어느 객체의 별칭인지 사람에게 "
                "받으세요(suggestions 는 짐작입니다)."
            }
        body: dict[str, Any] = {"object_id": object_id, "apply": apply}
        if value is not None:
            body["value"] = value
        return await _post(
            ctx, f"/api/objects/alias-candidates/{quote(candidate_id, safe='')}/attach", body
        )
    if action not in ("ignore", "restore"):
        return {"error": f"action 은 attach · ignore · restore 중 하나입니다: {action}"}
    if not apply:
        got = await _get(ctx, f"/api/objects/alias-candidates/{quote(candidate_id, safe='')}")
        if not isinstance(got, dict) or "error" in got:
            return got
        return {
            "applied": False,
            "action": action,
            "candidate": got,
            "next": "사람에게 보이고 확인받은 뒤 apply=True 로 부른다.",
        }
    got = await _post(
        ctx, "/api/objects/alias-candidates/decide", {"ids": [candidate_id], "action": action}
    )
    if isinstance(got, dict) and "error" not in got:
        return {"applied": True, "action": action, **got}
    return got


#: 「추천대로 모두」 의 기본 문턱 — 백엔드(`review/services.DEFAULT_THRESHOLD`)와 같다.
REVIEW_THRESHOLD = 0.9


def _review_filters(
    kind: str | None,
    type_slug: str | None,
    workspace: str | None,
    min_confidence: float | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "type": type_slug,
        "workspace": workspace,
        "min_confidence": min_confidence,
    }


@tool()
async def review_list(
    ctx: Context,
    kind: str | None = None,
    type_slug: str | None = None,
    workspace: str | None = None,
    min_confidence: float | None = None,
    group: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> Any:
    """**검토함** — 사람이 판단할 일을 한 곳에서(ADR 0027). 별칭 후보(못 찾은 말) · 검수 대기
    별칭 · 병합 후보(이름이 같은 객체) · 지워진 것을 가리키는 칸, 그리고 원래 화면에서만
    처리하는 적용 대기 계획 · 선 기다림.

    줄마다 `question`(물음) · `evidence`(근거) · `choices`(후보, `score`) · `recommended` ·
    `confidence`(0~1) · `reason`(왜 이 추천인가) · `rows`(한 결정이 손대는 줄 수) · `blocked`
    (이 토큰의 주인이 처리할 수 없는 까닭) · `link`(원래 화면). `kinds` 가 종류마다 수를 센다
    (`total` 원래 자리에 있는 수 · `matched` 거르기에 맞는 수) — **요약할 때 이것으로.**

    거르기: `kind`(종류 하나) · `type_slug` · `workspace`(부서 slug, `@global` 은 전역) ·
    `min_confidence`. `group=True` 면 같은 말 · 같은 지워진 객체 · 같은 출처의 줄을 한 줄로
    접는다(`members` 가 펼친 줄의 id — 결정은 그것마다).

    **추천은 규칙의 짐작이다** — 처리는 `review_decide` 로, 사람에게 보이고 확인받은 뒤에.
    부서 관리자 이상."""
    params: list[tuple[str, Any]] = [("limit", str(limit)), ("offset", str(offset))]
    for key, value in _review_filters(kind, type_slug, workspace, min_confidence).items():
        if value is not None:
            params.append((key, str(value)))
    if group:
        params.append(("group", "true"))
    return await _get(ctx, "/api/review/items", params=params)


@tool()
async def review_decide(
    ctx: Context,
    decisions: list[dict[str, Any]] | None = None,
    recommended: bool = False,
    threshold: float = REVIEW_THRESHOLD,
    kind: str | None = None,
    type_slug: str | None = None,
    workspace: str | None = None,
    apply: bool = False,
    fingerprint: str | None = None,
) -> Any:
    """검토함의 결정 — **늘 `apply=False`(기본) 미리 보기 먼저.** 무엇이 무엇으로 되나
    (`outcomes[].effect`) · 막히는 것(`counts.blocked` · `outcomes[].message`) · 되돌릴 수
    있나(`by_kind[].undoable` · `undo_note` — 병합은 복원이 없다)를 **사람에게 보이고 확인받은
    뒤** 같은 인자에 `apply=True` 와 미리 보기의 `fingerprint` 를 넣어 다시 부른다. 그 사이
    바뀌었으면 아무것도 안 하고 거절된다(다시 미리 본다).

    두 길:
    - `decisions=[{kind, item_id, action, value?}]` — 고른 것. `action` 은 `accept`(추천대로) ·
      `choose`(다른 것으로 — `value` 에 `choices[].value`) · `ignore`(무시). `item_id` 는
      `review_list` 의 `id`(묶은 줄이면 `members` 의 id 들).
    - `recommended=True` — **추천대로 모두**: `kind` · `type_slug` · `workspace` 에 맞고
      확신도가 `threshold`(기본 0.9) 이상인 것 전부. 문턱 아래는 `skipped` 로 센다(사람이 볼
      것).

    **스스로 고르지 마라** — 추천은 규칙의 짐작이다. 사람이 「추천대로 해」 라고 했을 때만
    `recommended=True`, 줄마다 정한 것은 `decisions` 로. 큰 묶음은 작업이 되어 `job` 으로
    온다(끝날 때까지 기다려 결과를 준다). 적용하면 `batch_id` — 화면의 검토함 › 최근 적용에서
    복원한다."""
    if not recommended and not decisions:
        return {
            "error": "decisions 를 주거나 recommended=True 로 부르세요 — 무엇을 정할지 "
            "사람에게 받은 뒤에."
        }
    body: dict[str, Any] = {
        "mode": "recommended" if recommended else "decisions",
        "decisions": decisions or [],
        "filters": {
            key: value
            for key, value in _review_filters(kind, type_slug, workspace, None).items()
            if value is not None
        },
        "threshold": threshold,
    }
    plan = await _post(ctx, "/api/review/plan", body)
    if not isinstance(plan, dict) or "fingerprint" not in plan:
        return plan
    if not apply:
        plan["applied"] = False
        plan["next"] = (
            "미리 보기다 — 아직 아무것도 안 바뀌었다. outcomes 의 effect · message 와 막힌 "
            "수 · 복원할 수 없는 것을 사람에게 보이고, 확인받으면 같은 인자에 apply=True 와 "
            f'fingerprint="{plan["fingerprint"]}" 를 넣어 다시 부른다.'
        )
        return plan
    if not fingerprint:
        return {
            "error": "apply=True 에는 미리 보기의 fingerprint 가 필요합니다 — 먼저 "
            "apply=False 로 미리 보고 사람에게 확인받으세요."
        }
    if plan["fingerprint"] != fingerprint:
        return {
            "error": "미리 본 것과 달라졌습니다(그 사이 누군가 처리했거나 후보가 "
            "바뀌었습니다) — 아무것도 안 했습니다. 다시 미리 보고 사람에게 확인받으세요.",
            "plan": plan,
        }
    done = await _post(
        ctx,
        "/api/review/apply",
        {
            "decisions": plan["decisions"],
            "fingerprint": fingerprint,
            "mode": "recommended" if recommended else "selected",
            "threshold": threshold if recommended else None,
        },
    )
    if isinstance(done, dict) and done.get("job_id"):
        job = await _get(ctx, f"/api/jobs/{done['job_id']}")
        return {"applied": False, "job": await _wait_job(ctx, job, JOB_WAIT_MAX)}
    return done


@tool()
async def objects_summary(
    ctx: Context,
    type_slug: str,
    group_by: str = "status",
    split_by: str | None = None,
    metric: str = "count",
    metric_field: str | None = None,
    order: str = "desc",
    grain: str | None = None,
    q: str | None = None,
    properties: dict[str, str] | None = None,
    conditions: list[dict[str, str]] | None = None,
    status: str | None = None,
) -> Any:
    """**통계** — 「부서별 몇 건」 「개발사 국가별 툴 수」. 화면의 「통계」 와 같다.

    **목록을 전부 받아 직접 세지 않는다** — 쪽 상한에서 틀리고, 수천 건이면
    못 한다. 서버가 센다. 거르기(`q`·`properties`·`conditions`·`status`)는
    `objects_list` 와 같다 — 그래서 목록의 `total` 과 여기 `total` 이 같다.
    **시간 상한이 있다**(기본 60초) — 기록 수천만 건을 지나가는 기준 · 조건은 끊긴다
    (`[APP-COMMON-0504]`). 그러면 끝이 좁은 조건을 먼저 걸어 다시 묻거나, 자주 보는 물음이면
    지표(`metric_query`)로 — 같은 것을 그대로 다시 부르지 않는다.

    - `group_by` 기준: `label`·`key`·`status`·`workspace`(소유 부서)·
      `created_year`, 속성은 `properties.<키>`, 다른 타입의 칸은
      `object_fields` 의 주소. 걸음은 이어 적을 수 있다(`ref.model.ref.base.series`,
      넷까지). 긴 글·파일은 안 된다.
      `type_slug` 가 인터페이스면 `type`(어느 구현 타입인가)도 된다.
      쓸 수 있는 기준 전부가 응답의 `group_options` 다.
    - `split_by` 세부 기준(같은 규칙). 주면 칸마다 `parts` 로 나뉜다.
    - `metric`: `count`(기본)·`sum`·`avg`·`min`·`max`. `count` 가 아니면
      `metric_field`(숫자 속성 `properties.<키>`)가 필요하다.
    - `order`: `desc`(큰 값부터)·`asc`(작은 값부터 — 「가장 낮은 것」)·`key`(키 순 —
      날짜 축을 **시간순**으로 볼 때).
    - `grain`: 날짜 축의 기간 단위 `day`·`week`·`month`·`quarter`·`year`(기본). 「월별
      추이」 는 `grain="month", order="key"`. 날짜 칸의 `buckets[].key` 는 그 기간의
      **시작일**이고 `range` 가 `{gte, lt}` 다 — 그 칸을 보려면 `objects_list` 에
      `{field: <칸>, op: "gte"}` 와 `{op: "lt"}` 둘을 건다(`eq` 는 0건).

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
    if grain:
        params.append(("grain", grain))
    params += _filter_params(q, status, properties, conditions)
    return await _get(ctx, f"/api/objects/{type_slug}/summary", params=params)


@tool()
async def object_fields(ctx: Context, type_slug: str) -> Any:
    """**다른 타입의 칸**을 쓰는 주소 — `conditions` 의 `field`, `objects_summary`
    의 `group_by` 에 그대로 넣는다. 자기 칸은 `ontology_schema` 에 있다.

    두 걸음까지 준다: `ref.<참조 칸>.<칸>`(참조 칸이 가리키는 것의 칸),
    `out.<관계>`(관계로 이어진 것 자체 — 값은 상대 id), `out.<관계>.<칸>`,
    `in.<관계>[.<칸>]`(들어오는 관계), `in.<타입>:<참조 칸>[.<칸>]`(나를 가리키는 것 —
    그 타입의 참조 칸이 이 객체를 가리키는 것, 개발모델에서 `in.svc_case:model.symptom` 은
    「이 모델을 가리키는 서비스 기록의 증상」), 그리고 그 너머 한 걸음 더
    (`ref.model.ref.task.project` — 모델의 과제의 프로젝트). `heading` 이 어느 걸음인지
    가른다 — 참조 칸과 관계가 같은 이름일 수 있다. `data_type` 이 `relation` 이면 상대가
    하나로 안 정해져 `empty`·`notempty` 만 걸린다.

    뜻: 이어진 것이 여럿이면 **그중 하나라도** 맞으면 걸린다. 이어진 것이 없는
    객체는 그 너머의 칸 조건에 안 걸린다(`ne` 도) — 그것은 참조 칸이나 관계의
    `empty` 로 묻는다. 인터페이스 slug 도 받는다 — 공통 속성에서 나가는 걸음이다."""
    return await _get(ctx, f"/api/objects/{type_slug}/fields")


@tool()
async def objects_similar(
    ctx: Context,
    type_slug: str,
    object_id: str | None = None,
    tags: dict[str, list[str]] | None = None,
    fields: list[str] | None = None,
    limit: int = 10,
) -> Any:
    """**비슷한 기록**(ADR 0022) — 축 태그(참조 칸 — 제품 · 부품 · 고장 메커니즘 ·
    해석법 …)가 많이 겹치는 같은 타입의 기록. 「이 이슈와 비슷한 과거 사례」 「이 부품 ·
    메커니즘 조합을 다룬 보고」.

    `object_id` 를 주면 그 기록의 태그로, 아직 기록이 없는 이슈면 `tags`(`{참조 칸: [객체
    id]}` — 이름은 `object_resolve` 로 먼저 푼다)로 묻는다. `fields` 로 볼 참조 칸을 좁힌다
    (비우면 전부).

    태그마다 무게 `weight` = ln(N / 그 태그를 가진 기록 수) — **드문 태그가 겹칠수록
    비슷하다**. `score` 는 무게를 단 자카드(0~1). 항목마다 `shared`(겹친 태그 — 「왜
    비슷한가」)를 함께 말한다. 본문의 뜻으로 찾지 않는다 — 찾은 기록의 본문은 `object_get`
    으로 읽어 비교한다."""
    limit = max(1, min(limit, 50))
    if object_id:
        params: dict[str, Any] = {"limit": limit}
        if fields:
            params["fields"] = ",".join(fields)
        return await _get(
            ctx,
            f"/api/objects/{type_slug}/{object_id}/similar",
            params=list(params.items()),
        )
    if not tags:
        return {"error": "object_id 나 tags 중 하나를 줍니다."}
    return await _post(
        ctx,
        f"/api/objects/{type_slug}/similar",
        {"tags": tags, "fields": fields, "limit": limit},
    )


@tool()
async def object_get(ctx: Context, type_slug: str, object_id: str, text_from: int = 0) -> Any:
    """객체 하나 — 속성·첨부·**관련 객체**(양방향)까지.

    첨부(`attachments[]`)는 이름 · 크기 · `is_image` · 가로세로뿐이다 — **바이트는
    오지 않는다**(사진은 화면에서 본다). `owner_field` 가 그 파일 칸의 키다.

    **긴 글 칸은 6,000자씩** 온다. `clipped[<칸>] = {from, to, length}` 가 있으면 잘린
    것이다 — `to < length` 면 `text_from=<to>` 로 다시 불러 이어 읽는다. 다 읽기 전에
    「본문에 그런 내용은 없다」 고 답하지 마라."""
    found = await _get(ctx, f"/api/objects/{type_slug}/{object_id}")
    _clip(found, start=text_from, size=GET_TEXT)
    return found


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

    지우거나 합치기 전에 본다. 못 보는 것은 수만 온다(`hidden_*` — 조회를 부서로 좁힌
    설치에서만) — 「아무것도 안 걸렸다」 로 읽고 지우면 그쪽 화면이 깨진다."""
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

    「이것과 연결된 것들」 은 `objects_list` 로는 두 걸음까지이고, 그보다 멀면 질의어를
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
async def graph_path(
    ctx: Context,
    source_id: str,
    target_id: str,
    max_depth: int = 6,
    relations: list[str] | None = None,
    types: list[str] | None = None,
    records: bool = False,
    limit: int = 3,
) -> Any:
    """**두 객체가 어떻게 이어져 있나** — 가장 짧은 길들(같은 길이로 `limit` 개까지).

    「이 부품은 이 프로젝트와 무슨 사이인가」 처럼 양 끝을 알 때 쓴다. `graph_neighbors` 를
    여러 번 펼쳐 이어 붙이면 노드마다 잘린 곳의 길을 「없다」 로 읽게 된다 — 이 도구는 자르지
    않고 찾는다. 관계와 참조 칸을 **방향과 상관없이** 따라간다(선의 방향은 `edges` 에 실린다).

    - `paths[].nodes` · `paths[].edges` 가 차례다 — `edges[k]` 가 `nodes[k]` 와
      `nodes[k+1]` 을 잇는다. 이름은 `nodes`(id · 이름 · 타입)에서, 관계의 말은
      `edges`(label · inverse_label)에서.
    - `found=false` 면 `max_depth`(기본 6, 최대 8) 안에 길이 없다. 단 **`truncated` 면
      「없다」 가 아니라 「찾다가 멈췄다」** 다(들른 노드 · 노드당 선 상한) — `relations` ·
      `types` 로 좁혀 다시 부른다.
    - **기록**(`usage="log"`)은 `records=True` 일 때만 거친다 — 인기 모델은 기록 10만 건이
      가리켜서, 기록을 거치면 거의 모든 것이 두 걸음 안에 이어진다.

    사람에게 옮길 때는 「A —(관계)→ B ←(관계)— C」 처럼 **이름과 관계의 말**로 말한다.
    """
    params: list[tuple[str, Any]] = [
        ("source", source_id),
        ("target", target_id),
        ("max_depth", max_depth),
        ("limit", limit),
    ]
    if relations:
        params.append(("relations", ",".join(relations)))
    if types:
        params.append(("types", ",".join(types)))
    if records:
        params.append(("records", "true"))
    return await _get(ctx, "/api/graph/path", params=params)


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
    """데이터 품질 — **나빠지고 있는 것**, 타입마다 어느 객체인지까지. 홈 「남은 일」 중 데이터
    쪽을 펼친 것이다(운영 쪽 — 실패한 작업 · 지표 · 동기화 · 승인 대기는 `server_maintenance`).

    `kind` 로 한 종류만: `missing_required`(필수값 빈 객체) · `orphan`(관계 없는 객체) ·
    `broken_ref`(지워진 것을 가리키는 칸) · `duplicate`(이름이 같은 객체). 종류·타입마다
    수는 전부, 목록은 앞의 몇 개(`sample_limit`)만. **볼 수 있는 것만 센다.**"""
    params = {"kind": kind} if kind else None
    return await _get(ctx, "/api/objects/quality/report", params=params)


@tool()
async def fill_priorities(ctx: Context, type_slug: str | None = None, limit: int = 20) -> Any:
    """**어디부터 채우나** — 이 플랫폼에서 지금 먼저 채울 곳을 줄 세운다. 「무엇이 나쁜가」 는
    `quality_report`, 「요즘 챙길 것(운영)」 은 `server_maintenance` 다.

    타입마다(볼 수 있는 것만, `types`) 객체 수 · 필수 칸이 빈 객체 · 칸마다 채움률(낮은
    칸부터) · 관계 종류마다 선이 없는 객체 · 지워진 것을 가리키는 칸 · 데이터 소스가 끝점을
    기다리는 선 · 별칭 없는 객체 · 객체 없는 타입을 세고, 그 칸 · 타입을 **쓰는 곳**(필수 ·
    지표 · 코어 공개 · 뷰 · 개수 제약 · 트리)으로 가중해 줄을 세운다(`priorities`). 줄마다
    `gain` 이 「이것을 채우면 무엇이 좋아지나」, `link` 가 빈 것만 거른 화면 주소다 — 답할 때
    둘을 함께 전한다.

    **큰 타입은 표본에서 센 어림이다**(`estimated`, `notes`) — 사람에게 수를 전할 때 「약」 을
    붙인다. `type_slug` 를 주면 그 타입(인터페이스면 구현 타입들)만, 칸은 전부 온다."""
    params: list[tuple[str, Any]] = [("limit", str(limit))]
    if type_slug:
        params.append(("type", type_slug))
    return await _get(ctx, "/api/objects/fill-priorities", params=params)


@tool()
async def server_maintenance(ctx: Context) -> Any:
    """홈의 **「남은 일」** — 손이 가야 하는데 아무도 안 본 것들(승인 대기 계정 · 적용 안 한
    계획 · 실패한 작업 · 멈춘 워커 · 실패하거나 멎은 지표 · 동기화 실패 · 웹훅 실패 · 낡은
    백업 · 데이터 품질 · 여러 번 찾았는데 못 찾은 이름 · 확장이 올린 것). 줄마다 `label` ·
    `count` · `severity`(`warning` 이면 미루면 안 되는 것) · `link`(화면 주소). **보는 사람의
    권한대로** — 시스템 관리자에게만 보이는 줄이 있다. 「요즘 챙길 것이 뭐야」 에 먼저
    부른다."""
    return await _get(ctx, "/api/server/maintenance")


@tool()
async def notifications(ctx: Context, unread_only: bool = True) -> Any:
    """**내 알림** 최근 것부터(100개까지) — 지켜보는 객체가 바뀜 · 경보가 울림 · 내 작업이
    끝남/실패 · 가입 승인 같은 것. 줄마다 `kind` · 제목 · 본문 · 링크 · 읽음 여부.
    `unread_only=false` 면 읽은
    것까지. **읽음 처리는 하지 않는다** — 사람이 화면에서 본 것으로 둔다."""
    params = {"unread_only": "true"} if unread_only else None
    return await _get(ctx, "/api/notifications", params=params)


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


def _public_origin(ctx: Context) -> str | None:
    """사용자 PC 가 **이 MCP 에 들어온 주소**의 앞부분 — 업로드 주소를 거기에 붙인다.

    nginx 뒤면 `Host` 에 포트가 없다(`$host`) — 앱도 같은 호스트 · 같은 포트다. MCP
    포트로 바로 들어왔으면 앱은 그 -2 다(설치 규칙: MCP = 앱 +2). 그 둘로 안 되는 설치는
    `MCP_PUBLIC_URL` 을 적고, 그때는 백엔드가 완전한 주소를 준다(`upload_url`)."""
    req = getattr(getattr(ctx, "request_context", None), "request", None)
    headers = getattr(req, "headers", None) or {}
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip()
    if not host:
        return None
    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if not proto:
        proto = getattr(getattr(req, "url", None), "scheme", None) or "http"
    parts = urlsplit(f"//{host}")
    if parts.hostname is None:
        return None
    name = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = parts.port
    if port is not None and port == int(os.environ.get("MCP_PORT", "8042")):
        port -= 2
    default = port is None or (proto, port) in (("http", 80), ("https", 443))
    return f"{proto}://{name}" if default else f"{proto}://{name}:{port}"


@tool()
async def attachment_upload_prepare(
    ctx: Context,
    type_slug: str,
    object_id: str,
    local_path: str,
    field: str | None = None,
    filename: str | None = None,
) -> Any:
    """사진 · 파일을 객체에 붙일 **curl 명령**을 만든다 — 파일 바이트는 모델을 거치지 않는다.

    ⚠️ 파일을 읽어 base64 로 옮기거나 내용을 도구 인자에 넣지 않는다 — 1MB 가 수십만 토큰이다.
    이 도구는 한 번 쓰는 업로드 표(5분)를 받아 명령을 돌려줄 뿐이고, **파일은 당신의 셸이
    `curl` 로 직접** 올린다. 셸이 없는 클라이언트면 사람에게 화면에서 올려 달라고 한다.

    - `local_path`: 셸에서 보이는 그 파일의 경로 — 이 서버는 그 파일을 열지 않는다
      (명령에만 넣는다).
    - `field`: 파일 칸의 키(`object_fields` · `ontology_schema` 의 `data_type: file`).
      비우면 「그 밖의 첨부」. 칸의 `accept` 가 `image` 면 **서버가 이미지(PNG · JPEG ·
      GIF · WebP)로 읽은 것만** 붙는다.
    - 돌아온 `curl` 을 그대로 한 번 실행한다. 응답은 붙은 첨부(id · `is_image` ·
      가로세로)이고, 실패하면 `{"error": ...}` 봉투가 온다 — 그 말을 사용자에게 전한다.
    - 객체를 고칠 수 있어야 한다(소유 부서 관리자 · 토큰은 `objects:write`). 안 되면 표가
      안 나온다.
    """
    name = filename or PureWindowsPath(local_path).name or "이름없음"
    issued = await _post(
        ctx,
        "/api/attachments/tickets",
        {
            "owner_table": "objects",
            "owner_id": object_id,
            "owner_field": field,
            "filename": name,
        },
    )
    if not isinstance(issued, dict) or "error" in issued:
        return issued
    url = issued.get("upload_url")
    if not url:
        origin = _public_origin(ctx)
        if origin is None:
            return {
                "error": "이 MCP 에 들어온 주소를 알 수 없어 업로드 주소를 못 만듭니다 — "
                "운영자에게 `.env` 의 MCP_PUBLIC_URL 을 적어 달라고 하세요.",
            }
        url = f"{origin}{issued['upload_path']}"
    command = (
        f"curl -sS -T {shlex.quote(local_path)} "
        f"-H 'X-Upload-Ticket: {issued['ticket']}' "
        f"'{url}?filename={quote(name, safe='')}'"
    )
    return {
        "curl": command,
        "type_slug": type_slug,
        "object_id": object_id,
        "field": field,
        "accept": issued.get("accept"),
        "max_bytes": issued.get("max_bytes"),
        "expires_in_seconds": issued.get("expires_in_seconds"),
        "next": "셸에서 curl 을 그대로 한 번 실행합니다(5분 안). 응답 JSON 의 id · is_image "
        "로 붙었는지 확인하고 사용자에게 알립니다. Windows PowerShell 이면 curl.exe 로 "
        "부릅니다.",
    }


@tool()
async def attachment_remove(ctx: Context, attachment_id: str) -> Any:
    """붙은 첨부를 뗀다 — id 는 `object_get` 의 `attachments[].id`. 객체의 이력에 남는다.

    **사람이 떼라고 한 것만.** 잘못 올린 것을 고칠 때 쓴다 — 파일 자체는 저장소에 남는다."""
    return await _delete(ctx, f"/api/attachments/{attachment_id}")


def _brief(detail: dict[str, Any]) -> dict[str, Any]:
    """합치기 미리 보기에 실을 객체 한 줄 — 무엇을 견주는지 알 만큼만(상세의 `object`)."""
    row = detail.get("object") or {}
    return {
        name: row.get(name)
        for name in (
            "id",
            "key",
            "label",
            "owner_workspace_slug",
            "status",
            "properties",
        )
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
    범위가 필요하다.

    **백필(한꺼번에 많이)** 이면 묶음에 칸 넷을 더 적는다 — 서버가 받는다.
    `preview: "plan"`(적용을 두 번 안 돈다) · `events: "summary"` · `audit: "summary"`
    (웹훅 · 감사가 줄마다 쌓이지 않게) · `missing_refs: "blank"`(못 찾은 참조 하나가
    전체를 막지 않게 그 칸만 비운다). **사용자가 백필이라고 정했을 때만** 쓴다 —
    넷 다 「전부 아니면 무 · 줄마다 기록」 을 느슨하게 하는 것이다.

    **행이 이 호출에 실린다** — 모델이 한 번에 쓸 수 있는 만큼(대개 수백 ~ 천 줄)이
    한계다. 수만 줄은 사용자 PC 의 **정제 도구 MCP**(`sp-pipeline` 키트)로 넣는다:
    `run_init(backfill=True)` → `run_validate` → `run_preview` — 파일이 모델을 거치지
    않고 서버로 간다. 그 MCP 가 안 붙어 있으면 「백필 기능이 없다」 고 답하지 않는다 —
    **이 플랫폼 화면의 「내 정보」 에서 키트를 받는다**(서버가 같은 판을 들고 있다). 받기 ·
    「이 PC 에 등록 정보 복사」 · `install.cmd` 더블클릭이 거기 안내돼 있다 — 사용자에게
    그리로 안내한다."""
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
        {
            "relation": relation,
            "dst_object_id": dst_object_id,
            "evidence_note": evidence_note,
        },
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
    """작업이 어디까지 됐나 — 작업을 만든 도구(`objects_import` · `relations_import` ·
    `bundle_import` · `datasource_sync` · `metric_define` · `metric_recompute` ·
    `filestore_gc` …)가 돌려준 `job_id` 로. `wait_seconds`(최대 25) 동안 끝나기를 기다렸다가
    돌려준다 — 그래도 안 끝났으면 `status` 가 `running` 인 채로 오고, 그때 다시 부른다.
    **끝났다고 지어내지 않는다.** `next` 가 다음에 할 일을 말한다(종류마다 다르다)."""
    job = await _get(ctx, f"/api/jobs/{job_id}")
    return await _wait_job(ctx, job, float(wait_seconds))


@tool()
async def job_apply(
    ctx: Context,
    job_id: str | None = None,
    wait_seconds: int = 20,
    job_ids: list[str] | None = None,
) -> Any:
    """계획을 **사람이 확인한 뒤** 적용한다 — 같은 파일 · 같은 지문으로 적용 작업을 만든다.

    미리 본 뒤에 누군가 그 사이에 같은 것을 바꿨으면 서버가 거절한다(「미리 본 것과
    달라졌습니다」) — 그러면 다시 미리 본다. 오류가 있는 계획은 적용 작업 자체가 안 만들어진다.
    **사용자의 판단 없이 부르지 않는다.**

    `job_ids` 로 **여러 계획을 한 번에** — 줄마다 결과(`status` · `message` · 적용 작업
    `job_id`)가 온다. 하나가 거절돼도 나머지는 간다 — 거절된 줄을 사용자에게 전한다."""
    if job_ids:
        return await _post(ctx, "/api/jobs/apply", {"ids": list(job_ids)})
    if not job_id:
        return {"error": "job_id 또는 job_ids 를 줍니다."}
    job = await _post(ctx, f"/api/jobs/{job_id}/apply", None)
    return await _wait_job(ctx, job, float(wait_seconds))


@tool()
async def jobs_list(
    ctx: Context,
    limit: int = 20,
    status: str | None = None,
    kind: str | None = None,
    mine: bool = False,
) -> Any:
    """작업 최근 것부터 — 무엇이 돌고 있고 무엇이 실패했나. 볼 수 있는 것만(내가 시킨 것 ·
    내 부서 것, 시스템 관리자는 타이머가 넣은 것까지 전부). 워커가 살아 있는지도 함께
    (`workers[].alive`) —
    워커가 없으면 작업은 영영 대기다. 그때는 운영자에게 알린다.

    거르기: `status`(`queued` · `running` · `done` · `failed` · `cancelled`) · `kind`(작업
    종류 — `datasource_sync` · `metrics_recompute` · `objects_import` …) · `mine`(내가 시킨
    것만). 「어젯밤 동기화가 실패했나」 는 `kind="datasource_sync", status="failed"`."""
    params: list[tuple[str, Any]] = [("limit", limit)]
    if status:
        params.append(("status", status))
    if kind:
        params.append(("kind", kind))
    if mine:
        params.append(("mine", "true"))
    listed = await _get(ctx, "/api/jobs", params=params)
    workers = await _get(ctx, "/api/jobs/workers")
    if isinstance(listed, dict) and "items" in listed:
        listed["items"] = [_job_view(one) for one in listed["items"]]
        listed["workers"] = workers
    return listed


@tool()
async def job_cancel(
    ctx: Context, job_id: str | None = None, job_ids: list[str] | None = None
) -> Any:
    """작업을 **멈춘다** — 줄에서 기다리는 것은 바로, 도는 것은 단계 사이에서 멈춘다(그때까지
    커밋한 것은 남는다 — 지표는 다 센 지표까지). 시킨 사람만(시스템 관리자는 모두). 이미 끝난
    작업은 거절된다. **사용자가 멈추라고 한 것만** 멈춘다. `job_ids` 로 여럿을 한 번에(줄마다
    결과)."""
    if job_ids:
        return await _post(ctx, "/api/jobs/cancel", {"ids": list(job_ids)})
    if not job_id:
        return {"error": "job_id 또는 job_ids 를 줍니다."}
    job = await _post(ctx, f"/api/jobs/{job_id}/cancel", None)
    return _job_view(job)


@tool()
async def filestore_gc(ctx: Context, wait_seconds: int = 20) -> Any:
    """첨부 저장소의 **고아 파일**(아무 첨부도 안 가리키는 파일 · 끊긴 업로드의 찌꺼기)을 센다
    — **계획만**: 지울 파일 수 · 크기 · 예시. 사람이 보고 판단한 뒤 `job_apply(job_id)` 로
    지운다. 하루가 안 지난 것과 저장소 모양이 아닌 것은 안 건드린다. 워커가 매일 한 번 스스로도
    지운다(`FILESTORE_GC_HOURS`). **시스템 관리자만.** 지운 파일은 되살릴 수 없다."""
    job = await _post_form(ctx, "/api/jobs", {"kind": "filestore_gc", "params": "{}"}, None)
    return await _wait_job(ctx, job, float(wait_seconds))


# --------------------------------------------------------------------------- #
# 데이터 소스 — 바깥 시스템(OData)에서 읽어 채우기. 정의는 화면에서, 돌리는 것은 여기서도.
# --------------------------------------------------------------------------- #
@tool()
async def datasources_list(
    ctx: Context, slug: str | None = None, suggest: bool = False, boards: bool = False
) -> Any:
    """정의된 **데이터 소스**(OData · REST · 파일 폴더 · 다른 플랫폼 → 타입) 목록 — 어느 표를
    어느 타입에 넣는지, 주기, 마지막 결과. 시스템 관리자 토큰이어야 보인다. 실패 이유 · 지난
    결과는 `datasource_runs`, 칸 대응이 맞는지는 `datasource_preview`.

    `slug` 를 주면 **그 소스 하나**(주소 · 대상 표 · 칸 대응 · 주기 · 인증 방식 — 비밀은
    `has_secret` 로만). 덧붙여:
    - `suggest=True` — 다른 플랫폼(`sp_core`) 소스면 상대 카탈로그를 읽어 **칸 대응 초안**을
      만든다(저장 안 함 — 고쳐서 `datasource_save` 의 `fields.mapping` 으로).
    - `boards=True` — RA 보고서 소스면 RA 의 조직(게시판) 트리 — 받을 게시판을 고를 때. 만들기
      · 고치기는 `datasource_save`, 지우기는 `config_delete(kind="datasource")`."""
    if slug is None:
        return await _get(ctx, "/api/datasources")
    out: dict[str, Any] = {"source": await _get(ctx, f"/api/datasources/{slug}")}
    if _failed(out["source"]):
        return out["source"]
    if suggest:
        out["suggest"] = await _post(ctx, f"/api/datasources/{slug}/core-suggest", None)
    if boards:
        out["boards"] = await _get(ctx, f"/api/datasources/{slug}/ra-boards")
    return out


@tool()
async def datasource_runs(ctx: Context, slug: str) -> Any:
    """데이터 소스의 **최근 실행 기록** — 언제 · 누가(타이머) · 계획/적용 · 수(`counts`: 새로 ·
    고침 · 그대로 · 오류 · 바깥에서 사라져 「사용 안 함」 으로 돌린 것 `deprecated` · 관계는
    `relations_*`) · 오류 행. 「어젯밤 동기화가 왜 실패했나」 · 「얼마나 들어왔나」 는 여기서.
    끝점을 못 찾아 **다음 동기화로 미룬 관계**는 `relations_waiting`. 시스템 관리자만."""
    return await _get(ctx, f"/api/datasources/{slug}/runs")


@tool()
async def datasource_preview(ctx: Context, slug: str, limit: int = 5) -> Any:
    """바깥 표의 **앞 몇 행을 그대로 + 칸 대응한 뒤로** — 대응이 맞는지 볼 때. 아무것도 안
    바꾼다. `mapping_error` 가 있으면 대응이 틀린 것이다 — 고치는 것은 화면에서. 시스템
    관리자만."""
    return await _post(
        ctx,
        f"/api/datasources/{slug}/preview",
        None,
        params={"limit": max(1, min(limit, 50))},
    )


@tool()
async def datasource_sync(ctx: Context, slug: str, apply: bool = False) -> Any:
    """데이터 소스를 **동기화**한다 — 바깥 표를 읽어 그 타입의 객체로.

    `apply=False`(기본)면 **계획만**: 행마다 새로/고침/그대로/오류와 그 이유. 사람에게 보여
    주고 판단을 받은 뒤 `apply=True`. **한 행이라도 오류면 아무것도 안 넣는다.** 같은 객체는
    바깥 식별자 → 식별자 → 별칭·이름 순으로 다시 찾는다(이미 다른 바깥 행과 이어진 객체는
    이름으로 다시 안 고른다). 값 대응표에 없는 값·못 푸는 참조는 오류 행이다 — 사용자에게
    무엇을 고쳐야 하는지 말한다.

    **빈 칸 · 빠진 별칭** — 보통은 빈 칸을 안 건드린다(더하기 · 바꾸기만). 다른 플랫폼
    (`sp_core`)이나 「바깥이 정본」(거울)으로 둔 소스는 바깥에서 비운 칸을 **비우고** 빠진
    별칭을 **뺀다** — 계획의 「고침」 에 그것이 보인다. 관계의 끝점이 아직 안 들어왔으면 그
    관계는 다음 동기화에서 다시 잇는다(`relations_waiting`).

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
        if response.status_code == 401:
            raise _rejected(response)  # 게이트웨이가 토큰을 다시 받는 신호(`_unwrap` 과 같다)
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

    - `types` 로 **범위를 좁히면 빠르다** — 안 주면 **기록 타입을 뺀** 전부를 올린다(빠진
      것은 답의 `left_out`). 질의가 빠진 기록 타입을 부르면 `[RDF-0006]` — 그 기록 타입과
      지나는 축 타입을 `types` 에 적어 다시 부른다. 기록을 세는 물음이면 `objects_summary` ·
      지표가 빠르다.
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


# --- 지표 — 기록을 미리 세어 둔 값 (ADR 0013) ------------------------------------
#
# **세는 일은 목록으로 받아 직접 하지 않는다.** 서비스 기록은 한 타입에 200만 건이고, 사람이
# 묻는 것은 「판매월 코호트의 누적 인입률」 「생산월 x 공장별 건수」 처럼 분모가 있고 시간이
# 있는 물음이다. 지표는 그것을 밤마다 세어 둔 것이다 — 계산은 플랫폼이 하고, AI 는 읽고
# 해석만 한다. 지표에 없는 물음은 `metric_define(apply=false)` 로 **정의를 제안**한다.

#: 읽기 모양 → 경로.
_METRIC_SHAPES = {
    "table": "values",
    "series": "series",
    "cohort": "cohort",
    "dim_values": "dims",
}


@tool()
async def metric_list(ctx: Context) -> Any:
    """**지표 목록** — 기록을 미리 세어 둔 값들. 「몇 건 · 비율 · 추이 · 코호트」 를 묻기 전에
    여기서 **이미 세어 둔 지표가 있나** 본다. 있으면 `metric_query`, 없으면 `objects_summary`
    (그때그때 센다 — 기준 하나 · 세부 기준 하나뿐), 그래도 안 되면 `metric_define(apply=false)`
    로 정의를 제안한다.

    줄마다: `slug` · `label` · `source_type_slug`(원천 기록 타입) · `measure` · `grain`(기간
    단위) · `cohort_grain` · `dims[]`(기준 이름 · 주소 · 종류) · `denominator`(분모 지표 —
    비율이 나온다) · `last_run_at`(계산 시각) · `stale`(세 주기가 지나도록 안 셈) · `overlap`
    (한 기록이 여러 셀에 든다) · `broken`(정의가 깨진 이유 — 있으면 값이 낡는다) ·
    `analyses[]`(이 지표에 되는 분석 `{recipe, label, ok, reason}` — `metric_analyze`)."""
    rows = await _get(ctx, "/api/metrics")
    if not isinstance(rows, list):
        return rows
    out = []
    for one in rows:
        spec = one.get("spec") or {}
        out.append(
            {
                "slug": one.get("slug"),
                "label": one.get("label"),
                "description": one.get("description"),
                "source_type_slug": one.get("source_type_slug"),
                "measure": spec.get("measure"),
                "measure_field": spec.get("measure_field"),
                "grain": one.get("grain"),
                "cohort_grain": one.get("cohort_grain"),
                "time": spec.get("time"),
                "cohort": spec.get("cohort"),
                "dims": one.get("dims"),
                "filters": spec.get("filters"),
                "denominator": spec.get("denominator"),
                "settle_days": spec.get("settle_days"),
                "is_active": one.get("is_active"),
                "overlap": one.get("overlap"),
                "cells": one.get("cells"),
                "last_run_at": one.get("last_run_at"),
                "last_status": one.get("last_status"),
                "stale": one.get("stale"),
                "broken": one.get("broken"),
                "analyses": one.get("analyses"),
            }
        )
    return {"total": len(out), "metrics": out}


@tool()
async def metric_query(
    ctx: Context,
    slug: str,
    shape: str = "table",
    dims: list[str] | None = None,
    by: list[str] | None = None,
    filters: dict[str, str | None] | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
    cohort_from: str | None = None,
    cohort_to: str | None = None,
    split: str | None = None,
    cumulative: bool = False,
    search: str | None = None,
    composition: str | bool | None = None,
) -> Any:
    """**지표 읽기** — 세어 둔 셀 위에서 비율 · 누적 · 전기 · 전년 동기 · 건 보기까지.
    보이는 것을 더한다(목록 · 통계와 같은 규칙 — 기본은 전부).

    - `shape="table"`: `dims`(묶을 기준 이름들, `metric_list` 의 `dims[].name`)와 `by`
      (`period` · `cohort` · `age` 중 묶을 것)별 셀. 셀마다 `count` · `value`(이 지표의
      집계) · `ratio`(분모가 있을 때) · `closed`(닫힌 기간) · `drill`.
    - `shape="series"`: 기간순 추이. 빈 기간은 0, `prev`(전기) · `yoy`(전년 동기). `split`
      으로 선을 나눈다(`lines[]`).
    - `shape="cohort"`: 코호트 x 경과 행렬(`rows[].cells[]`). `cumulative=true` 면 경과순
      누적 — 「판매 후 n개월째까지의 누적 인입률」. 행마다 `denominator`(분모가 코호트로
      짝지어졌을 때 — 판매 대수).
    - `shape="dim_values"`: 기준 **하나**(`dims=[이름]`)의 값과 이름 · 건수 — `filters` 에
      넣을 값(참조는 id)을 찾을 때. `search` 로 이름에 든 글자를 거른다.
    - `filters`: `{기준 이름: 값}` — 값은 `drill` 이나 `dim_values` 의 값(참조는 id).
      `null` 은 「(비어 있음)」. 분모도 같은 기준으로 걸린다.
    - `period_from` · `period_to`(앞까지) · `cohort_from` · `cohort_to`: `YYYY-MM-DD`.
    - `composition`(구성비): `table` 이면 기준 이름(`dims` 중 하나) — 셀마다 그 기준의 값들
      사이의 몫(`composition`, %, 나머지 칸이 같은 셀끼리 합 100). `series` 면 `true` —
      점마다 그 기간의 선(`split` 의 값)들 사이의 몫. 「성격별 구성비 추이」 는
      `shape="series", split=<규칙 기준>, composition=true`. 건수 · 합계의 몫이다(비율의
      몫이 아니다 — 평균 · 최소 · 최대는 거절).

    사용자에게 옮길 때 **빼먹지 않는다**:
    - `computed_at`(계산 시각)과 `stale` — 세어 둔 값이다. `closed` 가 false 인 기간은 더
      들어올 수 있다.
    - `overlap` 이 true 면 셀의 합이 기록 수보다 크다(여러 값 기준). `unbucketed` 는 시간 칸이
      비었거나 못 읽어 기간이 없는 기록 수, `truncated` 는 상한에서 잘렸다는 뜻 — 좁혀서 다시.
    - `denominator.missing` 이 0 이 아니면 분모가 없어 비율이 빈 셀이 있다.
    - 「그 수가 뭔데」 는 셀의 `drill.params` 를 `objects_list` 의 `conditions` 로 —
      `f.<칸>.<연산>` 키를 `{field, op, value}` 로, `nf<번호>.<칸>.<연산>`(제외 묶음 — 규칙
      기준의 「앞 규칙에 안 맞음」)은 `{field, op, value, exclude: <번호>}` 로 풀면 된다
      (`drill.partial` 이 비어 있을 때 수가 같다)."""
    where = _METRIC_SHAPES.get((shape or "table").strip().lower())
    if where is None:
        return {"error": f"shape 는 {', '.join(_METRIC_SHAPES)} 중 하나입니다: {shape!r}"}
    if where == "dims":
        if not dims or len(dims) != 1:
            return {"error": "dim_values 는 기준 하나를 dims=[이름] 으로 줍니다."}
        asked: list[tuple[str, Any]] = [("name", dims[0])]
        if search:
            asked.append(("q", search))
        return await _get(ctx, f"/api/metrics/{slug}/dims", params=asked)
    params: list[tuple[str, Any]] = []
    if dims:
        params.append(("dims", ",".join(dims)))
    if by:
        params.append(("by", ",".join(by)))
    for key, value in (
        ("period_from", period_from),
        ("period_to", period_to),
        ("cohort_from", cohort_from),
        ("cohort_to", cohort_to),
        ("split", split),
    ):
        if value:
            params.append((key, value))
    if cumulative:
        params.append(("cumulative", "true"))
    if composition not in (None, False, ""):
        params.append(("composition", "true" if composition is True else str(composition)))
    for name, value in (filters or {}).items():
        params.append((f"d.{name}", "" if value is None else str(value)))
    got = await _get(ctx, f"/api/metrics/{slug}/{where}", params=params)
    if isinstance(got, dict) and "error" not in got and "total" not in got:
        # 자취 점수가 「몇 건인가」 를 읽는 자리 — 모양마다 세는 것이 다르다.
        if "cells" in got:
            got["total"] = got.get("total_count", len(got["cells"]))
        elif "lines" in got:
            got["total"] = sum(len(one.get("points", [])) for one in got["lines"])
        elif "rows" in got:
            got["total"] = sum(len(one.get("cells", [])) for one in got["rows"])
    return got


#: 분석 레시피 → 받는 options(공통 범위 포함). 모르는 키는 백엔드가 조용히 버리므로 여기서
#: 거절한다 — 「걸렀다고 믿었는데 안 걸린」 답이 제일 나쁘다.
_RANGES = {"period_from", "period_to", "cohort_from", "cohort_to"}
_RECIPE_OPTIONS: dict[str, set[str]] = {
    "pareto": {"dim", "top", "include_empty", "by_period", "compare_from", "compare_to"}
    | _RANGES,
    "life": {"model", "max_age", "basis", "cohort_from", "cohort_to"},
    "control": {"axis", "window", "split", "baseline_to"} | _RANGES,
    "changes": {"axis", "window", "by", "top"} | _RANGES,
    "sprt": {
        "target",
        "reference",
        "reference_via",
        "dim",
        "rho",
        "alpha",
        "beta",
        "by",
        "top",
    },
    "groups": {"dim", "axis", "window"} | _RANGES,
    "cutin": {"at", "axis", "window", "skip_first", "effect"} | _RANGES,
    "coverage": {"levels", "via"} | _RANGES,
    "recurrence": {"generation", "signature", "via"} | _RANGES,
    "profile": {"dim", "value"} | _RANGES,
    "forecast": {
        "horizon",
        "warranty",
        "model",
        "basis",
        "cost",
        "backtest",
        "cohort_from",
        "cohort_to",
    },
    "logit": {"factors", "min_count"} | _RANGES,
    "assoc": {"rows", "cols", "min_count"} | _RANGES,
}


@tool()
async def metric_analyze(
    ctx: Context,
    slug: str,
    recipe: str,
    options: dict[str, Any] | None = None,
    filters: dict[str, str | None] | None = None,
    compact: bool = True,
) -> Any:
    """**분석** — 세어 둔 지표의 셀 위에서 통계까지 **플랫폼이** 한다(ADR 0014). 셀을 받아
    직접 계산하지 않는다 — 같은 물음에 같은 방법 · 같은 답 · 같은 주의가 나와야 한다.
    `metric_list` 의 `analyses[]` 가 그 지표에 되는 분석과 **안 되는 이유**를 말한다.

    `recipe` 와 `options`:
    - `pareto` — 몫 · 누적 · ABC · HHI · 유효 개수 · 지니 · CR. `dim`(필수) · `top` ·
      `include_empty` · `by_period`(기간별 집중도) · `compare_from` · `compare_to`(그 기간과
      몫을 견준다 — 동질성 χ² 와 값마다 수정 잔차, 「구성비가 바뀌었나」)
    - `life` — 와이블(표준 · 결함) 맞춤과 B1 · B5 · B10. `model`(`auto` · `weibull` ·
      `defective`) · `max_age` · `basis`(`first_visits` 면 시리얼마다 첫 방문만 — 방문 기준이
      있는 지표)
    - `control` — 라니 u-관리도와 넬슨 규칙. `axis`(`period` · `cohort`) · `window`(코호트
      축 — 출고 뒤 몇 기간) · `split`(분모 짝에 있는 기준) · `baseline_to`(한계를 이 날
      앞으로만)
    - `changes` — 계절 지수와 변화점. `axis` · `window` · **`by`**(값마다 훑기 — 「계절을 빼면
      늘고 있는 증상은?」: 증상마다 변화점, 벌점 +2·ln K, 마지막 변화가 오름인 값부터) · `top`
    - `sprt` — 새 모델 vs 전작 순차 검정. `target`(필수 — 기준 값, 참조면 id) ·
      `reference` 또는 `reference_via`(새 모델 객체의 전작 칸) · `dim` · `rho` · `alpha` ·
      `beta` · **`by`**(값마다 훑기 — 「전작보다 빨리 늘고 있는 증상은?」: 증상마다 그 증상으로
      거른 검정, 유의수준 α/K, 「나쁨」 이 선 값부터. `new` 는 전작에 없던 값) · `top`
    - `groups` — **집단 비교**(SKU · 공장 · 기본 모델마다 비율이 다른가). `dim`(분모 짝에 있는
      기준) · `axis` · `window`. 이질성 χ², 집단 대 나머지 정확 검정(BH q), 작은 집단의 과장을
      줄인 비율(`shrunk` · 구간 · `shrinkage`)
    - `coverage` — **커버리지**(다뤄야 할 축 조합 중 기록이 다룬 것 · 빈 칸). `levels`(필수 —
      축인 기준을 차례로, 2~4개, 예: 모델 · 부품 · 메커니즘 · 해석법) · `via`(기준 사이마다의
      길 — 비운 자리는 온톨로지에 하나뿐인 길, 여럿이면 거절하며 후보를 말한다). 첫 기준은
      `filters` 로 거른다. `gaps`(빈 칸 — `leaves` 는 그 아래 끝 조합 수), `extras`(기대 밖),
      `roots`(첫 기준 값마다 다룬 몫)
    - `recurrence` — **재발**(전작에서 나온 축 조합이 다음 모델에서 다시 나왔나).
      `generation`(필수 — 세대 기준, 예: 모델) · `signature`(필수 — 서명 기준 1~3개, 예: 부품 ·
      메커니즘) · `via`(세대 축에서 전작으로 가는 길 — 비우면 하나뿐인 앞쪽 길). 쌍마다
      다시 나온 것 · `rate`(재발률) · `new`, 모든 쌍의 `rate`, 되풀이된 `signatures`
    - `profile` — **한 장 요약**(기준 값 하나 — 예: 메커니즘 「피로」). `dim` · `value`(필수 —
      참조면 객체 id, `object_resolve` 로 먼저). 건수 · 몫 · 기간 추이(`points`), 다른
      기준마다 함께 나온 값(`related` — 몫 · `lift` 향상도 · `drill`)
    - `cutin` — **전후 비교**(대책 적용일 뒤에 만든 것부터 줄었나). `at`(필수 — 적용일
      `YYYY-MM-DD`) · `axis` · `window` · `skip_first`(앞쪽에서 뺄 처음 부분군 — 출시 초기) ·
      `effect`(의미 있는 차이, 기본 0.2). 모델은 `filters` 로 거른다 — 앞쪽은 그 모델의 첫
      출고부터. 비(뒤/앞)와 구간, `decision`(`reduced` · `increased` · `no_difference` ·
      `too_early`), `more_subgroups`(결론까지 더 닫혀야 할 부분군), `pre_trend`(앞쪽 흐름)
    - `forecast` — **클레임 예측**(이미 판 물량에서 앞으로 몇 건 · 얼마). 수명과 같은 지표 ·
      같은 맞춤. `horizon`(앞으로 몇 기간, 기본 12) · `warranty`(보증 기간 — 주면 그 경과부터
      안 세고 `remaining` 에 보증 끝까지의 총량) · `cost`(건당 비용 — 금액) · `model` ·
      `basis` · `backtest`(되짚어 보기 기간, 기본 6). 기간마다 `expected` 와 80 · 95% 구간,
      `total`, `backtest`(그때 예측했다면 실제를 맞혔나)
    - `logit` — 재방문 위험 요인(방문 기준 「재방문」 이 있는 지표). `factors`(필수 — 값이 적은
      기준 이름 넷까지, 목록) · `min_count`
    - `assoc` — 연관 · 묶음. `rows` · `cols`(필수 — 증상 · 부품처럼 기준 둘) · `min_count`.
      향상도 · 정확 검정(BH q), 행의 묶음, 대응 분석 지도, **원인분산도**(`dispersion` — 행마다
      유효 원인 수 1/Σ몫², 가장 많은 원인과 그 몫)
    - 범위: `period_from` · `period_to`(앞까지) · `cohort_from` · `cohort_to` — `YYYY-MM-DD`

    `filters`: `{기준 이름: 값}` — 지표 읽기와 같다(분모 · 두 모델에 함께 걸린다).
    `compact=true`(기본)는 대화에 맞게 줄인 응답이다 — 줄 · 곡선 · 점을 줄인다.

    **옮길 때 규칙.**
    - `caveats[]` 의 `message` 를 **그대로** 전한다. `warn` 은 결과보다 먼저.
    - 수명: `status="unreachable"` 인 B수명은 **값이 없다** — 외삽으로 지어 말하지 않는다.
      「판매의 p 만 결국 고장 나 그 비율에 이르지 않는다」 로 말하고, 필요하면
      `conditional_age`(결국 고장 나는 것들 중의 그 비율)를 **그 이름으로** 말한다.
      `extrapolated` 는 「관측 밖으로 늘려 읽은 값」 이다.
    - 순차 검정: `continue` 는 「아직 결론 없음」 이지 「문제없음」 이 아니다. `not_worse` 는
      「ρ 배 나쁘지는 않다」 이지 「같다」 가 아니다.
    - 관리도의 신호는 「조사할 곳」 이지 원인이 아니다. 변화점의 `provisional` 은 잠정이다.
    - 재발: 다시 나온 서명은 「전작에서도 이 조합의 기록이 있었다」 이다 — 날짜 순서는 안
      본다(`order_ignored`). 재발률은 전작 서명 중의 몫이다.
    - 커버리지: 빈 칸은 「이 플랫폼에 그 조합의 기록이 없다」 이지 「검토하지 않았다」 가
      아니다. `extras`(기대 밖)는 온톨로지 관계나 태깅을 고칠 곳이다.
    - 클레임 예측: 평균(`expected`)만 말하지 않고 구간과 함께, `backtest.within` 이 false
      면 그것을 먼저. 이미 판 것만이다 — 앞으로 팔 것 · 리콜은 없다.
    - 전후 비교: `reduced` 는 「적용일 뒤에 줄었다」 이지 「대책 때문에」 가 아니다.
      `too_early` 는 「효과 없음」 이 아니다 — `more_subgroups` 로 언제쯤인지 말한다.
      `pre_trend` 주의가 있으면 그 흐름을 먼저 말한다.
    - 위험 요인의 오즈비 · 연관의 향상도는 「함께 나옴」 이지 원인이 아니다. `unstable` 인 값은
      오즈비를 말하지 않는다. `aliased` 면 요인끼리 겹쳐 오즈비를 가를 수 없다.
    - `method`(방법과 판) · `computed_at`(계산 시각)을 함께 말한다. 근거는 `drill.params` →
      `objects_list` 의 `conditions`(지표 읽기와 같다)."""
    name = (recipe or "").strip().lower()
    allowed = _RECIPE_OPTIONS.get(name)
    if allowed is None:
        return {"error": f"recipe 는 {', '.join(_RECIPE_OPTIONS)} 중 하나입니다: {recipe!r}"}
    unknown = sorted(set(options or {}) - allowed)
    if unknown:
        return {
            "error": f"{name} 이 모르는 options: {', '.join(unknown)} — 받는 것: "
            f"{', '.join(sorted(allowed))}"
        }
    params: list[tuple[str, Any]] = []
    for key, value in (options or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, list | tuple):
            # 빈 자리는 빈 글자로 — 커버리지의 `via` 처럼 「이 자리는 하나뿐인 길」 을 뜻한다.
            value = ",".join("" if one is None else str(one) for one in value)
        params.append((key, str(value)))
    for dim_name, value in (filters or {}).items():
        params.append((f"d.{dim_name}", "" if value is None else str(value)))
    params.append(("compact", "true" if compact else "false"))
    path = f"/api/metrics/{slug}/analysis/{name}"
    if name in ("sprt", "changes") and (options or {}).get("by"):
        path += "/scan"  # 값마다 훑기 — 응답 모양이 달라 경로가 따로다
    return await _get(ctx, path, params=params)


@tool()
async def insights(
    ctx: Context,
    metric: str | None = None,
    kind: str | None = None,
    days: int | None = None,
    include_past: bool = False,
    limit: int = 20,
) -> Any:
    """**눈여겨볼 것** — 플랫폼이 스스로 지표를 훑어 찾은 변화 · 차이(ADR 0032). 「요즘 뭐가
    이상해?」 「무엇부터 봐야 하나」 에 **먼저** 부른다 — 사람이 지표 · 기준 · 값을 골라 묻지
    않아도 서는 결론이다(증상 x 모델 같은 조합까지 훑는다).

    - 점수 높은 것부터. 제목에 「몇 배 · 몇 건」 이 든다 — 그대로 옮긴다. 점수는 **적게 잡은
      비**(`detail.ratio_bound`, 비의 95% 구간 중 1 에 가까운 끝) x 건수라 작은 칸의 큰 비는
      아래에 선다.
    - `kind`: `change`(변화점) · `worse`(새 모델이 전작보다 나쁨) · `high`(다른 집단보다 높음).
      `days` 는 최근 N일에 처음 본 것만, `include_past` 는 마지막 훑기에 안 나온 것도.
    - 결론마다 `link` 가 그 분석을 그 인자로 연다 — 근거를 보이려면 `metric_analyze` 로 같은
      인자를 부른다(`recipe` · 링크의 쿼리 — 제목의 「… 기준 12개월 안」 은 `window=12`).
      `also` 는 같은 줄이 선 다른 지표 — 한 번만 말한다.
    - **후보다** — 원인을 단정하지 않는다. q 값 · 잠정 · 지남을 함께 말한다. 결론은 그 지표의
      셀이 든 부서를 모두 보는 사람에게만 보인다 — 비었다고 「이상 없음」 이 아니다.

    읽기만. 그만 보기 · 지표마다 끄기 · 지금 훑기는 화면에서(시스템 관리자)."""
    params: list[tuple[str, Any]] = [("limit", str(max(1, min(limit, 200))))]
    for key, value in (("metric", metric), ("kind", kind), ("days", days)):
        if value:
            params.append((key, str(value)))
    if include_past:
        params.append(("include_past", "true"))
    return await _get(ctx, "/api/insights", params=params)


@tool()
async def metric_alerts(
    ctx: Context,
    slug: str | None = None,
    limit: int = 20,
    alert_id: str | None = None,
    check: bool = False,
) -> Any:
    """**내 경보와 최근 발생** — 「요즘 무엇이 울렸나」 · 「내가 지켜보는 게 뭐였지」
    (ADR 0016).

    경보는 저장한 분석(순차 검정 · 관리도 · 변화점)을 지표를 다시 셀 때마다 **그 사람의
    눈으로** 돌려, 처음 보는 결론만 그 사람에게 알린다. 이 도구는 **읽기만** 한다 — 걸기 · 이름
    · 켜고 끄기는 `metric_alert_save`, 지우기는 `config_delete(kind="metric_alert")`.

    `slug` 와 `alert_id` 를 주면 **그 경보 하나의 발생**(처음부터 있던 것도 표시와 함께),
    `check=True` 면 **지금 확인** — 지금 셀에서 보이는 결론과 그중 아직 안 알린 것(적지도
    알리지도 않는다).

    `slug` 를 주면 그 지표에 건 내 경보(`alerts[]` — 이름 · 분석 · 인자 · 켜짐 · 마지막 확인 ·
    실패 이유)와 그 지표의 발생만, 비우면 내 모든 경보의 최근 발생 `limit` 개(새것부터).

    발생(`events[]`)마다 `title`(한 줄 결론) · `detail`(수) · `created_at` · `link`(분석 탭을
    그 인자로 여는 주소). `link` 의 인자를 `metric_analyze` 의 `options` 와 `filters`
    (`d.<기준>`)로 옮기면 지금 셀로 같은 분석을 다시 본다.

    **옮길 때 규칙.** 발생은 「그때 처음 본 결론」 이지 원인이 아니다 — 관리도 신호는 조사할
    곳, 순차 검정 「나쁨」 은 정한 배수 쪽이라는 판정, 변화점의 「잠정」 은 잠정이다. 지금도
    그런지는 `metric_analyze` 로 본다."""
    if alert_id is not None:
        if not slug:
            return {"error": "alert_id 를 주면 그 경보의 지표 slug 도 줍니다."}
        if check:
            return await _post(ctx, f"/api/metrics/{slug}/alerts/{alert_id}/check", None)
        return await _get(
            ctx,
            f"/api/metrics/{slug}/alerts/{alert_id}/events",
            params=[("limit", str(max(1, min(limit, 200))))],
        )
    events = await _get(
        ctx,
        "/api/metrics/alerts/events",
        params=[("limit", str(max(1, min(limit, 200))))],
    )
    if not isinstance(events, list):
        return events
    if slug is None:
        return {"events": events}
    alerts = await _get(ctx, f"/api/metrics/{slug}/alerts")
    if not isinstance(alerts, list):
        return alerts
    return {
        "alerts": alerts,
        "events": [one for one in events if one.get("metric") == slug],
    }


@tool()
async def metric_define(
    ctx: Context,
    slug: str,
    label: str,
    source_type_slug: str,
    spec: dict[str, Any],
    apply: bool = False,
    description: str = "",
    interval_hours: int | None = None,
    is_active: bool | None = None,
    wait_seconds: float = 20.0,
) -> Any:
    """**지표 정의** — 계획(`apply=false`, 기본) → 사람 확인 → 저장하고 바로 센다
    (`apply=true`). **시스템 관리자만.** 사용자의 판단 없이 `apply=true` 를 부르지 않는다.

    `spec`:
      - `measure`: `count` · `sum` · `avg` · `min` · `max`(+ `measure_field`:
        `properties.<숫자 칸>`) · `share`(조건 비율 — `share_when` 에 거르기와 같은 꼴의
        조건을 주면 셀마다 조건 건수(`value`) / 전체 건수(`count`) = `ratio`(%). 분모 지표는
        두지 않는다. 「센터마다 NTF 비율」 · 「대분류 중 SW 의 몫」)
      - `time`: `{"address": "properties.<날짜 칸>", "grain": "month"}` — 자기 타입의 날짜
        칸만. 단위는 `day` · `week` · `month` · `quarter` · `year`
      - `cohort`: 같은 모양(선택) — 「판매월」 처럼 묶어 둘 둘째 날짜. 단위는 `time` 과
        같아야 한다
      - `dimensions`: `[{"name": "base_model", "address": "ref.model.base_model"}]` 여덟까지
        — 주소는 `objects_summary` 의 `group_by` 와 같다(걸음 넷까지). 날짜 기준에는 `grain`.
        **규칙 기준**(ADR 0015 — 성격 분류): 주소 대신 `{"name": "nature", "label": "성격",
        "rules": [{"label": "SW", "when": [{"field": "action", "op": "starts", "value":
        "SW"}]}, …], "else": "기타"}` — 기록마다 위에서부터 **처음 맞는 규칙**의 이름이 값,
        안 맞으면 `else`. `when` 은 `objects_list` 의 조건과 같은 모양(모두 맞아야, 너머 칸
        도). 규칙 스물 · 규칙마다 조건 열까지, 이름은 규칙마다 달라야 한다(한 칸 안의 「또는」
        은 `in`). 계획의 `rules[]` 가 규칙마다 붙인 수(`won`) · 막힌 규칙(`blocked` — 앞
        규칙이 다 가져감) · 기타의 수(`other_count`)를 말한다 — 그대로 사람에게 보인다
      - `filters`: `[{"field": "status", "op": "eq", "value": "active"}]` — `objects_list`
        의 조건
      - `denominator`: `{"metric": "<지표>", "on": ["base_model"], "time": "cohort",
        "per": 100}` — 비율의 분모. `on` 은 양쪽에 같은 이름 · 같은 값 종류로 있는 기준,
        `time` 은 `period`(기간끼리) · `cohort`(분모의 기간 = 분자의 코호트) · `null`(기간
        없이 전부 합)
      - `settle_days`: 이만큼 지난 기간은 「닫힘」
      - `visits`: `{"key": "properties.<시리얼 칸>", "within_days": 90}`(선택) — 같은 제품의
        방문. 있으면 기준 주소로 `visit.number`(시리얼마다 날짜순 차례 1 · 2 · 3 · 4+)와
        `visit.repeat`(그 뒤 within_days 안에 다시 왔나 — yes · no · open)를 쓸 수 있다.
        재방문 위험 요인(`metric_analyze(recipe="logit")`)과 첫 방문 수명이 이것을 쓴다
      - `stay`: `{"periods": 24, "periods_from": "ref.country.warranty_months"}`(선택) —
        머무는 기간. 기록을 그 기간부터 N기간 동안 센다(기간마다 최근 N기간의 합). 판매
        집계에 두면 「보증 중 대수」 — 접수월 인입률의 분모(`denominator.time="period"`).
        `periods_from` 은 기록마다 기간 수를 읽을 숫자 칸(비면 · 넘으면 `periods`). 코호트 ·
        방문과 함께 못 둔다

    `interval_hours` — 타이머가 다시 세는 주기. `0` 은 손으로만(`metric_recompute`), 1 ~ 23
    은 그 시간마다(낮에도), `24` 는 매일 밤, `48` · `72` … 는 N일마다 밤(날 단위만 —
    36 같은 값은 거절된다). 새로 만들 때 비우면 매일 밤, 고칠 때 비우면 그대로 둔다.
    `is_active=false` 면 타이머 · 적재 뒤에 안 센다(값은 남는다).

    타이머 · 적재 뒤의 계산은 **되면 바뀐 기간만** 다시 센다 — 정의를 바꾼 뒤 첫 계산 ·
    일주일마다 · 다른 객체 너머가 바뀌었을 때는 전부(`metric_runs` 가 어느 쪽이었는지와 그
    까닭을 말한다).

    계획은 **오류 전부** · 경고 · 거르기를 통과한 기록 수 · 어림한 셀 수 · 기준의 종류를
    돌려준다 — 그것을 사람에게 보여 주고 판단을 받는다. 같은 slug 가 이미 있으면 그 정의를
    고친다(원천 타입은 못 바꾼다). 저장 뒤 계산은 작업이다 — `wait_seconds` 만큼 기다렸다가
    돌려주고, 아직이면 `job_status(job_id)` 로 다시 묻는다."""
    if not apply:
        return await _post(
            ctx,
            "/api/metrics/plan",
            {"slug": slug, "source_type_slug": source_type_slug, "spec": spec},
        )
    existing = await _get(ctx, f"/api/metrics/{slug}")
    exists = isinstance(existing, dict) and existing.get("slug") == slug
    # 비운 것은 안 보낸다 — 고칠 때는 그대로 두고, 만들 때는 서버의 기본(매일 밤 · 켜짐).
    extra = {
        key: value
        for key, value in (("interval_hours", interval_hours), ("is_active", is_active))
        if value is not None
    }
    if exists:
        saved = await _patch_with_params(
            ctx,
            f"/api/metrics/{slug}",
            {"label": label, "description": description, "spec": spec, **extra},
            {"recompute": "true"},
        )
    else:
        saved = await _post(
            ctx,
            "/api/metrics",
            {
                "slug": slug,
                "label": label,
                "description": description,
                "source_type_slug": source_type_slug,
                "spec": spec,
                **extra,
            },
            params={"recompute": "true"},
        )
    if not isinstance(saved, dict) or "metric" not in saved:
        return saved
    job = await _wait_job(ctx, saved.get("job"), wait_seconds)
    return {"metric": saved["metric"], "job": job, "updated": exists}


@tool()
async def metric_recompute(ctx: Context, slug: str, wait_seconds: float = 20.0) -> Any:
    """지표를 **지금 전부 다시** 센다 — 화면의 「다시 세기」 와 같다. **시스템 관리자만.**

    값이 이상해 보일 때 · 바깥에서 기록을 고친 직후 바로 볼 때. 평소에는 타이머가 주기마다
    (되면 바뀐 기간만) 세므로 부를 일이 드물다. 같은 지표의 작업이 줄에서 기다리고 있으면
    그것을 전부 세기로 올려 돌려준다(이미 도는 중이면 새로 넣는다). **작업이 된다** —
    `wait_seconds` 만큼 기다렸다가 돌려주고, 아직이면 `job_status(job_id)` 로 다시 묻는다."""
    job = await _post(ctx, f"/api/metrics/{slug}/recompute", None)
    return await _wait_job(ctx, job, wait_seconds)


@tool()
async def metric_runs(ctx: Context, slug: str) -> Any:
    """지표의 **최근 계산 기록** — 언제 · 성공/실패와 이유 · 기록 수 · 셀 수 · 날짜를 못 읽은
    기록 수(`stats.unbucketed`) · **방식**(`mode`: `full` 전부 / `incremental` 바뀐 기간만 —
    `periods` 가 다시 센 기간) · `note`(전부였으면 왜 전부였나) · `rules`(규칙 기준이 있으면
    그 계산이 쓴 **규칙 판** — 지문 · 규칙 수 · 그 규칙을 저장한 때 · 사람). 「값이 왜
    그대로인가」 · 「언제 셌나」 · 「지난달과 왜 다른가(규칙이 바뀌었나)」 는 여기서 — 실패한
    계산은 옛 값을 그대로 두고 이유를 남긴다."""
    return await _get(ctx, f"/api/metrics/{slug}/runs")


@tool()
async def metric_home(
    ctx: Context,
    slug: str,
    workspace_slug: str | None = None,
    split: str | None = None,
    remove: bool = False,
) -> Any:
    """지표를 **부서 홈에 올린다**(최근 열두 기간의 추이 그림) · 내린다 · 어디 올라가 있나
    본다.

    - `workspace_slug` 없이: 이 지표가 올라간 부서 홈들(`split` 포함).
    - `workspace_slug` 를 주면 그 부서 홈에 올린다 — `split` 은 선을 나눌 기준 이름
      (`metric_list` 의 `dims[].name`, 비우면 합계 한 줄). 이미 있으면 기준만 바꾼다.
    - `remove=true` 면 그 부서 홈에서 내린다(지표는 안 지운다).

    **그 부서의 관리자만** 올리고 내린다(목록 통계를 홈에 올리는 것과 같다). 홈은 그 부서
    사람 모두가 보는 자리다 — 사용자가 올리자고 한 것만 올린다."""
    if workspace_slug is None:
        return await _get(ctx, f"/api/metrics/{slug}/home")
    if remove:
        return await _delete(ctx, f"/api/metrics/{slug}/home", {"workspace": workspace_slug})
    return await _put(
        ctx,
        f"/api/metrics/{slug}/home",
        {"workspace_slug": workspace_slug, "split": split},
    )


async def _patch_with_params(
    ctx: Context, path: str, json_body: Any, params: dict[str, Any]
) -> Any:
    async with _client(120) as client:
        return _unwrap(
            await client.patch(
                path, json=json_body, params=params, headers=_forward_headers(ctx)
            )
        )


# --------------------------------------------------------------------------- #
# 운영 · 설정 — 화면의 관리 기능을 AI 도 쓰게(2026-10-15)
#
# 도메인마다 **「읽기 하나 · 고치기 하나」** 로 묶고, 지우기는 `config_delete` 하나로 모은다
# (`ontology_delete` 와 같은 모양). 기능마다 도구를 내면 목록이 두 배가 되고, 목록이 길어질수록
# 모델은 엉뚱한 것을 고른다 — 이 파일 머리의 규칙이다. 고치는 도구는 **미리 보기가 기본**이다
# (`apply=False` — 무엇이 되는지만, 사람의 판단 뒤 `apply=True`).
#
# **읽기만 하는 것** — 부서 · 공지 · 웹훅 · 서버. 그 쓰기(부서 만들기 · 옮기기 · 통폐합 ·
# 지우기, 공지 올리기 · 지우기, 웹훅 다시 보내기, 확장 켜기)는 **토큰에 안 열린다** — 「계정 ·
# 부서 · 서버 관리는 어느 범위로도 안 열린다」(`auth/delegation.py`, `shared/scopes.py` 의
# 「모르는 것은 막는다」). 그 원칙을 MCP 때문에 넓히지 않는다(2026-10-15 — 만들어 진짜 앱에
# 붙여 보니 그 자리가 403 「화면에서 하세요」 였다).
#
# **넣지 않은 것**(보안 · 사람 전용) — 로그인 · 토큰 · 계정, 부서 구성원 · 역할, 웹훅 등록 ·
# 수정 · 삭제, 온톨로지 초기화, 접근 기록(사람의 열람 기록), 데이터 소스의 인증 비밀, 알림
# 읽음(사람이 본 것으로 둔다), 파일 내려받기 · 사진 묶음 올리기(바이트는 모델을 거치지 않는다 —
# 내보내기는 작업만 시작하고 파일은 화면에서 받는다).
# --------------------------------------------------------------------------- #


def _failed(found: Any) -> bool:
    """백엔드가 거절했나 — `_unwrap` 의 오류 봉투."""
    return isinstance(found, dict) and "error" in found


def _preview(would: Any, *, current: Any = None, note: str = "") -> dict[str, Any]:
    """미리 보기의 모양 — **아직 아무것도 안 바뀌었다**는 말과 다음 할 일을 함께."""
    out: dict[str, Any] = {"applied": False, "would": would}
    if current is not None:
        out["current"] = current
    out["next"] = (f"{note} " if note else "") + (
        "아직 아무것도 안 바뀌었다. 사용자에게 보여 주고 판단을 받은 뒤 같은 인자에 "
        "apply=true 로 다시 부른다."
    )
    return out


def _pick(rows: Any, key: str, value: str) -> Any:
    """목록에서 하나 — 없으면 오류 봉투(그 말이 곧 사람에게 전할 말이다)."""
    if _failed(rows):
        return rows
    for one in rows if isinstance(rows, list) else []:
        if str(one.get(key)) == str(value):
            return one
    return {
        "error": f"{key}={value} 인 것을 찾지 못했습니다 — 목록을 먼저 읽어 값을 확인합니다."
    }


@tool()
async def metric_alert_save(
    ctx: Context,
    slug: str,
    name: str | None = None,
    recipe: str | None = None,
    params: dict[str, Any] | None = None,
    alert_id: str | None = None,
    is_active: bool | None = None,
    apply: bool = False,
) -> Any:
    """**경보를 건다 · 고친다** — 저장한 분석을 지표를 다시 셀 때마다 **내 눈으로** 돌려, 처음
    보는 결론만 나에게 알린다(ADR 0016). 「A60 이 전작보다 나빠지면 알려 줘」.

    - **새로 걸기** — `alert_id` 없이 `name` · `recipe`(`sprt` 순차 검정 · `control` 관리도 ·
      `changes` 변화점) · `params`. `params` 는 `metric_analyze` 의 `options` 를 **분석 경로의
      쿼리 모양 그대로**(값은 글자) 쓰고, 거르기는 `d.<기준>` 키로 — 예: `{"reference": "A50",
      "d.base_model": "A60"}`. 경보만의 인자: `recent`(최근 몇 기간의 결론만) ·
      `launched_within` · `notify_not_worse`. 미리 보기(`apply=False`)는 그 인자로 **지금
      분석을 한 번 돌려** 무엇이 보일지 보여 준다 — 저장하면 지금 있는 결론은 「처음부터 있던
      것」 으로 적고 알리지 않는다(새로 생기는 것만 알린다).
    - **고치기** — `alert_id` 와 `name` · `is_active`(끄기 · 켜기)만. **인자는 못 바꾼다** —
      바꾸면 지난 발생이 다른 물음의 것이 된다. 새로 걸고 옛 것은
      `config_delete(kind="metric_alert")`.

    경보는 그것을 건 사람의 것이다(알림도 그 사람에게). 지금 건 경보와 발생은
    `metric_alerts`."""
    if alert_id is not None:
        if recipe is not None or params is not None:
            return {
                "error": "경보의 인자(recipe · params)는 못 바꿉니다 — 새로 걸고 옛 경보는 "
                "config_delete(kind='metric_alert') 로 지웁니다."
            }
        body = {key: value for key, value in (("name", name), ("is_active", is_active))}
        body = {key: value for key, value in body.items() if value is not None}
        if not body:
            return {"error": "고칠 것(name · is_active)을 줍니다."}
        if not apply:
            current = _pick(await _get(ctx, f"/api/metrics/{slug}/alerts"), "id", alert_id)
            return current if _failed(current) else _preview(body, current=current)
        return await _patch(ctx, f"/api/metrics/{slug}/alerts/{alert_id}", body)
    if not name or not recipe:
        return {"error": "새 경보에는 name 과 recipe(sprt · control · changes)가 필요합니다."}
    body = {"name": name, "recipe": recipe, "params": dict(params or {})}
    if not apply:
        query = [
            (key, "" if value is None else str(value)) for key, value in body["params"].items()
        ]
        now = await _get(
            ctx,
            f"/api/metrics/{slug}/analysis/{recipe}",
            params=[*query, ("compact", "true")],
        )
        return _preview(
            body,
            note="`now` 는 그 인자로 지금 돌린 분석이다 — 저장하면 이 결론들은 알리지 않고, "
            "다음 계산부터 새로 생기는 결론만 알린다.",
        ) | {"now": now}
    return await _post(ctx, f"/api/metrics/{slug}/alerts", body)


#: `config_delete` 가 지우는 것 — 종류마다 「무엇을 보고 확인하나 · 어디를 지우나」.
_CONFIG_KINDS = (
    "metric",
    "metric_alert",
    "datasource",
    "view",
)


@tool()
async def config_delete(
    ctx: Context,
    kind: str,
    slug: str | None = None,
    id: str | None = None,
    apply: bool = False,
) -> Any:
    """설정 하나를 **지운다** — 지표 · 경보 · 데이터 소스 · 저장된 뷰.
    `apply=False`(기본)면 **무엇이 지워지나만** 보인다 — 사용자의 판단 뒤 `apply=True`.

    | kind | 인자 | 지우면 |
    | --- | --- | --- |
    | `metric` | `slug` | 지표 · 세어 둔 값 · 경보 · 홈 게시(기록은 남는다). 시스템 관리자 |
    | `metric_alert` | `slug`(지표) · `id`(경보) | 경보와 그 발생 기록. 내 경보만 |
    | `datasource` | `slug` | 소스 정의만 — **객체는 남는다**. 시스템 관리자 |
    | `view` | `slug`(타입) · `id`(뷰) | 저장된 거르기 조건. 데이터는 그대로 |

    되살리는 길이 없다 — **사용자가 지우라고 한 것만** 지운다. 부서 · 공지 · 웹훅 같은 관리
    설정은 MCP 로 안 지운다(관리는 토큰에 안 열린다 — 사람이 화면에서)."""
    name = (kind or "").strip()
    if name not in _CONFIG_KINDS:
        return {"error": f"kind 는 {', '.join(_CONFIG_KINDS)} 중 하나입니다: {kind!r}"}
    needs_id = name in ("metric_alert", "view")
    if not slug or (needs_id and not id):
        wanted = "slug · id" if needs_id else "slug"
        return {"error": f"{name} 을 지우려면 {wanted} 가 필요합니다."}
    if name == "metric":
        path, current = f"/api/metrics/{slug}", await _get(ctx, f"/api/metrics/{slug}")
    elif name == "metric_alert":
        path = f"/api/metrics/{slug}/alerts/{id}"
        current = _pick(await _get(ctx, f"/api/metrics/{slug}/alerts"), "id", str(id))
    elif name == "datasource":
        path, current = f"/api/datasources/{slug}", await _get(ctx, f"/api/datasources/{slug}")
    else:
        path = f"/api/objects/{slug}/views/{id}"
        current = _pick(await _get(ctx, f"/api/objects/{slug}/views"), "id", str(id))
    if _failed(current):
        return current
    if not apply:
        return _preview(
            {"kind": name, "slug": slug, "id": id},
            current=current,
            note="되살리는 길이 없다.",
        )
    done = await _delete(ctx, path)
    return (
        done
        if _failed(done)
        else {"applied": True, "deleted": {"kind": name, "slug": slug, "id": id}}
    )


@tool()
async def datasource_save(
    ctx: Context,
    slug: str | None = None,
    fields: dict[str, Any] | None = None,
    ra_report_type: dict[str, Any] | None = None,
    apply: bool = False,
) -> Any:
    """데이터 소스를 **만들거나 고친다** — 바깥 표(OData · REST · 파일 폴더 · 다른 플랫폼 · RA
    보고서)를 어느 타입에 어떻게 넣나. 시스템 관리자만.

    - `slug` 의 소스가 있으면 `fields` 에 준 칸만 고치고, 없으면 새로 만든다. 새 소스에 필요한
      칸: `name` · `entity_set`(바깥 표) · `type_slug`(넣을 타입). 그 밖: `kind`(`odata` 기본 ·
      `rest` · `folder` · `sp_core` · `ra_reports` …) · `base_url` · `options` · `filter` ·
      `select` · `auth_kind` · `auth_user` · `page_size` · `workspace_slug` · `mapping`(칸 대응
      — `datasources_list(slug, suggest=True)` 의 초안) · `source_name` · `deprecate_missing` ·
      `interval_minutes`(0 이면 손으로만) · `is_active`. 고칠 때는 `since_mark` 도 된다.
    - **인증 비밀(`auth_secret`)은 받지 않는다** — 비밀이 대화와 기록에 남는다. 만든 뒤 화면
      (관리 › 데이터 소스)에서 사람이 넣는다. `has_secret` 이 그 여부를 말한다.
    - `ra_report_type` — RA 보고서를 받을 **보고서 기록 타입**을 표준 칸으로 만든다: `{"slug":
      "ra_report", "label": "보고서", "axes": ["plm_model"], "nav_group_slug": null}` (`axes`
      는 보고서에 축 태그로 걸 타입). 이때 `slug` · `fields` 는 안 쓴다.

    미리 보기(`apply=False`)는 고칠 때 지금 값과 바뀔 값을 함께 보인다. 넣은 뒤 대응이 맞는지는
    `datasource_preview`, 돌리기는 `datasource_sync`."""
    if ra_report_type is not None:
        if not apply:
            return _preview({"ra_report_type": ra_report_type})
        return await _post(ctx, "/api/datasources/ra-report-type", dict(ra_report_type))
    if not slug or not fields:
        return {"error": "slug 와 fields(만들거나 고칠 칸)를 줍니다."}
    if "auth_secret" in fields:
        return {
            "error": "인증 비밀은 MCP 로 받지 않습니다 — 소스를 저장한 뒤 화면(관리 › 데이터 "
            "소스)에서 사람이 넣습니다."
        }
    listed = await _get(ctx, "/api/datasources")
    if _failed(listed):
        return listed
    exists = any(one.get("slug") == slug for one in listed if isinstance(one, dict))
    if exists:
        if not apply:
            current = await _get(ctx, f"/api/datasources/{slug}")
            shown = (
                {key: current.get(key) for key in fields}
                if isinstance(current, dict)
                else current
            )
            return _preview(
                {"action": "update", "slug": slug, "fields": fields}, current=shown
            )
        return await _patch(ctx, f"/api/datasources/{slug}", dict(fields))
    if not apply:
        return _preview({"action": "create", "slug": slug, "fields": fields})
    return await _post(ctx, "/api/datasources", {"slug": slug, **fields})


@tool()
async def core_status(ctx: Context) -> Any:
    """**외부 공개(코어) 현황** — 무엇이 공개되어 있고, 누가 읽을 수 있고, 누가 받아 갔나,
    그리고 **무엇이 안 나가나**. 시스템 관리자만.

    - `types` — 공개 타입마다 객체 수 · 최종 변경 · 칸 수 · 나가는 관계 종류(`relations`).
    - `consumers` — 코어를 읽을 수 있는 토큰(이름 · 계정 · `narrow` 면 `core:read` 만 · 최종
      사용 · 만료). 한 번도 안 쓴 토큰은 아직 안 붙은 연동이다.
    - `recent` — 최근 수신 기록(어느 토큰이 어느 타입을 몇 행).
    - `withheld_relations` — 공개 타입에 닿지만 **안 나가는 관계 종류**와 사유(`closed_types`
      끝 타입 미공개 · `no_open_implementer` · `partial` · `unconstrained` 끝 타입 미지정),
      공개하면 나가는 타입(`missing_labels`), 빠지는 관계 수(`edges`). 「관계까지 다 나가나」
      는 여기로 답한다.
    - `withheld_references` — 공개 안 한 타입을 가리키는 참조 속성(값은 나가지만 상대를 못
      받는다).
    - `watermark_lag_seconds` — 수신 기준 시각이 지금보다 얼마나 뒤인가(긴 적재 중이면 커진다).

    공개 지정은 타입의 `core` — `ontology_import` 로 바꾼다(공개는 외부 시스템에 대한 약속이라
    사용자의 판단을 받는다)."""
    found = await _get(ctx, "/api/ontology/core-status")
    if isinstance(found, dict):
        for one in found.get("types") or []:
            # 칸 목록은 길다 — 수만. 자세한 것은 `ontology_schema`.
            one["property_count"] = len(one.pop("properties", None) or [])
            one.pop("relation_kinds", None)
    return found


@tool()
async def workspaces(ctx: Context, slug: str | None = None, all: bool = False) -> Any:
    """**부서** — 비우면 부서 목록(트리 — `path` · `depth` · 상위 · 정렬 · 구성원 수 · 내
    역할). `all=True` 면 쓰지 않는 부서까지. `slug` 를 주면 **그 부서가 가진 것**(`contents` —
    종류마다 건수)과 **그 부서를 가리키는 것**(`references` — `blocks_delete` 면 지우기를 막는
    것). 부서 하나의 내용은 시스템 관리자만.

    객체의 소유 부서는 `object_create` 의 `workspace_slug` 다 — 내 소속은 `whoami`. **읽기만
    한다** — 부서 만들기 · 이름 · 옮기기 · 통폐합 · 지우기와 구성원 · 역할은 사람이 화면(관리 ›
    부서)에서 한다(부서 관리는 토큰에 안 열린다)."""
    if slug is None:
        return await _get(ctx, "/api/workspaces", params={"all": "true" if all else "false"})
    contents = await _get(ctx, f"/api/workspaces/{slug}/contents")
    if _failed(contents):
        return contents
    return {
        "slug": slug,
        "contents": contents,
        "references": await _get(ctx, f"/api/workspaces/{slug}/references"),
    }


@tool()
async def views(ctx: Context, type_slug: str) -> Any:
    """그 타입의 **저장된 뷰** — 내 뷰와 내 부서와 함께 쓰는 뷰(부서 것이 먼저). 뷰마다 이름 ·
    거르기(`query` — `q` · `conditions` · `status`, `objects_list` 와 같은 모양) · 통계 모양
    (`summary`) · 홈 게시 차례 · 고칠 수 있나(`can_edit`). 뷰의 거르기를 `objects_list` ·
    `objects_summary` 에 그대로 옮기면 같은 목록 · 통계다. 만들기 · 고치기는 `view_save`."""
    return await _get(ctx, f"/api/objects/{type_slug}/views")


@tool()
async def view_save(
    ctx: Context,
    type_slug: str,
    name: str | None = None,
    query: dict[str, Any] | None = None,
    view_id: str | None = None,
    workspace_slug: str | None = None,
    summary: dict[str, Any] | None = None,
    on_home: bool | None = None,
    home_position: int | None = None,
    apply: bool = False,
) -> Any:
    """**뷰를 저장한다 · 고친다** — 자주 쓰는 거르기(와 통계 모양)에 이름을 붙여 둔다.

    - `query` — `{"q": "...", "conditions": [{"field", "op", "value", "exclude"}], "status":
      ...}` (`objects_list` 의 거르기와 같다 — 주소는 `object_fields`).
    - `summary` — 통계 모양(`group_by` · `split_by` · `metric` · `metric_field` · `chart` ·
      `order` · `grain`, `objects_summary` 와 같다). 없으면 목록 뷰.
    - `workspace_slug` — 주면 **그 부서와 함께 쓰는** 뷰(그 부서의 관리자만), 비우면 내 것.
      새로 만들 때만 정한다.
    - `on_home` · `home_position` — 부서 홈에 올리기와 그 차례.
    - `view_id` 를 주면 그 뷰를 고친다(준 칸만), 없으면 새로(`name` · `query` 필요).

    미리 보기(`apply=False`)가 기본. 지우기는 `config_delete(kind="view", slug=타입,
    id=뷰)`."""
    if view_id is None:
        if not name or query is None:
            return {"error": "새 뷰에는 name 과 query 가 필요합니다."}
        body: dict[str, Any] = {
            "name": name,
            "query": query,
            "workspace_slug": workspace_slug,
            "summary": summary,
            "on_home": bool(on_home),
        }
        if not apply:
            return _preview({"action": "create", "type_slug": type_slug, **body})
        return await _post(ctx, f"/api/objects/{type_slug}/views", body)
    body = {
        key: value
        for key, value in (
            ("name", name),
            ("query", query),
            ("summary", summary),
            ("on_home", on_home),
            ("home_position", home_position),
        )
        if value is not None
    }
    if not body:
        return {"error": "고칠 것(name · query · summary · on_home · home_position)을 줍니다."}
    if not apply:
        current = _pick(await _get(ctx, f"/api/objects/{type_slug}/views"), "id", view_id)
        return current if _failed(current) else _preview(body, current=current)
    return await _patch(ctx, f"/api/objects/{type_slug}/views/{view_id}", body)


@tool()
async def object_watch(
    ctx: Context,
    type_slug: str | None = None,
    object_id: str | None = None,
    on: bool | None = None,
    limit: int = 50,
) -> Any:
    """**지켜보기** — 객체가 바뀌면 내 알림(`notifications`)으로 받는다.

    - `type_slug` · `object_id` · `on=True` 면 지켜보기 시작, `on=False` 면 그만 — 응답은 지금
      지켜보나와 지켜보는 사람 수.
    - 아무것도 안 주면 **내가 지켜보는 것**(최근 바뀐 것부터, `limit` 개).

    사용자가 「이거 바뀌면 알려 줘」 라고 한 것만 건다 — 알림은 그 사람에게 간다."""
    if type_slug is None and object_id is None:
        return await _get(
            ctx, "/api/objects/watching", params={"limit": str(max(1, min(limit, 200)))}
        )
    if not type_slug or not object_id or on is None:
        return {"error": "지켜보기를 바꾸려면 type_slug · object_id · on 을 모두 줍니다."}
    return await _put(ctx, f"/api/objects/{type_slug}/{object_id}/watch", {"on": on})


@tool()
async def object_years(
    ctx: Context,
    type_slug: str,
    object_id: str,
    years: list[int] | None = None,
    apply: bool = False,
) -> Any:
    """객체의 **연도 배정** — 해마다 쓰이는 축(`temporal_kind='yearly'`, 예: 그해의 과제 ·
    연간 계획)에서 이 객체가 **명시적으로 배정된** 연도들. `years` 를 비우면 읽기만, 주면
    **통째로** 그 연도들로 정한다(빠진 해는 풀린다 — 미리 보기에서 지금 값과 견준다). 목록의
    `year` 거르기가 이 배정을 본다."""
    current = await _get(ctx, f"/api/objects/{type_slug}/{object_id}/years")
    if years is None or _failed(current):
        return current
    wanted = sorted({int(one) for one in years})
    if not apply:
        return _preview({"years": wanted}, current=current)
    return await _put(ctx, f"/api/objects/{type_slug}/{object_id}/years", wanted)


@tool()
async def webhooks(ctx: Context, webhook_id: str | None = None) -> Any:
    """**웹훅** — 바뀐 것을 바깥 주소로 알리는 설정. 비우면 목록(이름 · 주소 · 이벤트 · 타입 ·
    「코어 공개 타입만」 · 켜짐 · 마지막 결과 — 서명 비밀은 `has_secret` 로만), `webhook_id` 를
    주면 **그 웹훅의 보낸 기록**(언제 · 무엇을 · 응답 코드 · 실패 이유 · 시도 수). 시스템
    관리자만.

    「왜 안 오지」 는 보낸 기록부터 본다. **읽기만 한다** — 다시 보내기 · 시험 발송 · 등록 ·
    수정 · 삭제는 화면(관리 › 웹훅)에서(바깥 주소로 자료를 내보내는 설정이고 서명 비밀을
    다룬다)."""
    if webhook_id is None:
        return await _get(ctx, "/api/webhooks")
    return await _get(ctx, f"/api/webhooks/{webhook_id}/deliveries")


@tool()
async def notices(ctx: Context, include_drafts: bool = False) -> Any:
    """**공지** — 지금 게시 중인 것(제목 · 본문 · 수준 · 팝업 · 게시일 · 만료 · 쓴 사람 · 내가
    읽었나). `include_drafts=True` 면 게시 전 것까지(시스템 관리자). **읽기만 한다** — 올리기 ·
    지우기는 화면(관리 › 공지)에서. 사용자가 문안을 원하면 써서 보여 주고 화면에 올리게
    한다."""
    params = {"include_drafts": "true"} if include_drafts else None
    return await _get(ctx, "/api/notices", params=params)


@tool()
async def review_history(
    ctx: Context,
    kind: str | None = None,
    batch: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """**검토함의 지난 결정** — 무엇을 누가 언제 어떻게 정했나, 추천을 따랐나(`decisions`),
    **추천이 얼마나 맞았나**(`accuracy` — 추천이 있던 결정 중 추천대로 한 몫), 최근 적용한
    묶음(`batches` — 되돌릴 수 있는 것이 몇 남았나). `kind`(별칭 후보 · 검수 대기 …) ·
    `batch`(묶음 id)로 거른다. 되돌리기는 `review_undo`."""
    params: list[tuple[str, Any]] = [
        ("limit", str(max(1, min(limit, 200)))),
        ("offset", str(offset)),
    ]
    if kind:
        params.append(("kind", kind))
    if batch:
        params.append(("batch", batch))
    return {
        "accuracy": await _get(ctx, "/api/review/decisions/accuracy"),
        "decisions": await _get(ctx, "/api/review/decisions", params=params),
        "batches": await _get(ctx, "/api/review/batches", params={"limit": "20"}),
    }


@tool()
async def review_undo(ctx: Context, batch_id: str | None = None, apply: bool = False) -> Any:
    """검토함에서 **한 번에 적용한 묶음을 되돌린다** — 종류마다 원래 기능의 되돌리기로(붙인
    별칭은 떼고, 합친 것은 …). `batch_id` 를 비우면 **되돌릴 수 있는 묶음 목록**(최근 것부터 —
    남은 수 `undoable`). `apply=False`(기본)면 무엇이 되돌아가나만(`lines`) — 사용자의 판단 뒤
    `apply=True`. 이미 그 뒤에 바뀐 것은 되돌리지 않고 이유를 말한다."""
    if batch_id is None:
        return await _get(ctx, "/api/review/batches", params={"limit": "20"})
    return await _post(ctx, f"/api/review/batches/{batch_id}/undo", {"apply": apply})


#: `server_status(performance=…)` 가 부르는 자리.
_PERFORMANCE = {
    "requests": "/api/server/performance/requests",
    "cases": "/api/server/performance/requests/cases",
    "jobs": "/api/server/performance/jobs",
    "cut": "/api/server/performance/cut",
}


@tool()
async def server_status(
    ctx: Context,
    performance: str | None = None,
    period: str | None = None,
    sort: str | None = None,
    route: str | None = None,
    method: str | None = None,
    limit: int = 20,
    suggest: bool = False,
) -> Any:
    """**서버 상태** — 판 · 환경 · DB 스키마가 최신인가(`schema_behind`) · 디스크 · 백업 ·
    건수 · 기동 시각, 확장 모듈과 켜짐(`extensions` — 켜고 끄기는 화면에서), 정제 도구 키트가
    있나(`pipeline_kit`). 시스템 관리자만(일부 칸은 누구나).

    `performance` 로 **느린 곳**을 본다(시스템 관리자):
    - `requests` — 경로 틀 · 메서드마다 호출 수 · p50 · p95 · 최대 시간 · 응답 크기. `period`
      (`1h` · `24h` · `7d` …) · `sort`.
    - `cases` — 한 경로의 최근 최악 사례. `route`(경로 틀) · `method` 필수.
    - `jobs` — 작업 종류마다 끝난 수 · 소요 · 대기.
    - `cut` — **끊긴 질문**(ADR 0031): 시간 상한 · SPARQL 크기에 걸려 답을 못 받은 물음을
      같은 모양끼리(타입 · 기준 · 조건의 칸과 연산 — 값은 없다, SPARQL 은 질의문). 자주
      끊기는 물음은 지표(`metric_define`)로 미리 세어 둘 후보다 — 사람에게 제안한다.
      `suggest=True` 면 줄마다 **지표 정의 초안**(`suggestion`)이 실린다 — 그대로
      `metric_define(apply=false)` 에 넣어 계획을 보여 주고 사람의 판단을 받는다(저장은
      사람이).

    손이 가야 하는 일(승인 대기 · 적용 안 한 계획 · 실패한 동기화)은 `server_maintenance`."""
    if performance is not None:
        path = _PERFORMANCE.get(performance)
        if path is None:
            return {"error": f"performance 는 {', '.join(_PERFORMANCE)} 중 하나입니다."}
        params: list[tuple[str, Any]] = []
        for key, value in (
            ("period", period),
            ("sort", sort),
            ("route", route),
            ("method", method),
        ):
            if value:
                params.append((key, value))
        if performance != "jobs":
            params.append(("limit", str(max(1, min(limit, 200)))))
        if performance == "cut" and suggest:
            params.append(("suggest", "true"))
        return await _get(ctx, path, params=params)
    return {
        "status": await _get(ctx, "/api/server/status"),
        "extensions": await _get(ctx, "/api/server/extensions"),
        "pipeline_kit": await _get(ctx, "/api/server/pipeline-kit/info"),
    }


@tool()
async def jobs_purge(ctx: Context, apply: bool = False) -> Any:
    """**기한 지난 작업 파일과 끝난 작업 기록**을 지운다 — 워커도 한 시간마다 하는 정리를
    지금. 시스템 관리자만. 지운 기록은 `jobs_list` 에서 사라지고 그 파일은 내려받을 수 없다.
    미리 보기(`apply=False`)는 지금 작업 수만 보인다 — 무엇이 지워질지는 서버의 기한이
    정한다."""
    if not apply:
        listed = await _get(ctx, "/api/jobs", params={"limit": "1"})
        total = listed.get("total") if isinstance(listed, dict) else None
        return _preview(
            {"action": "purge"},
            current={"jobs_total": total},
            note="기한 지난 파일과 끝난 작업 기록이 지워진다(되살릴 수 없다).",
        )
    return await _post(ctx, "/api/jobs/maintenance/purge", None)


#: `export_start` 가 만드는 작업.
_EXPORTS = {
    "objects": "/api/objects/{type_slug}/export",
    "relations": "/api/objects/{type_slug}/relations/export",
}


@tool()
async def export_start(
    ctx: Context,
    type_slug: str,
    kind: str = "objects",
    format: str = "csv",
    q: str | None = None,
    status: str | None = None,
    properties: dict[str, str] | None = None,
    conditions: list[dict[str, str]] | None = None,
) -> Any:
    """**파일로 내보내기를 시작한다** — 쪽 상한 없이 전부. 작업이 되고, **파일은 사람이 화면의
    「작업」 에서 받는다**(바이트는 모델을 거치지 않는다).

    - `kind="objects"` — 그 타입의 객체를 `objects_list` 와 **같은 거르기**(`q` · `status` ·
      `properties` · `conditions`)로.
    - `kind="relations"` — 그 타입에서 출발하는 관계 전부(`src` · `relation` · `dst` · 근거).
    - `format` — `csv` · `json`.

    내용을 읽어 답하려면 내보내지 말고 `objects_list` · `objects_summary`."""
    template = _EXPORTS.get(kind)
    if template is None:
        return {"error": f"kind 는 {', '.join(_EXPORTS)} 중 하나입니다: {kind!r}"}
    if format not in ("csv", "json"):
        return {"error": "format 은 csv · json 입니다."}
    params: list[tuple[str, Any]] = [("format", format)]
    if kind == "objects":
        params += _filter_params(q, status, properties, conditions)
    job = await _post(ctx, template.format(type_slug=type_slug), None, params=params)
    return await _wait_job(ctx, job, JOB_WAIT_MAX)


# --- 기동 ----------------------------------------------------------------------
#
# **이 블록은 파일의 맨 끝이어야 한다.** `mcp.run()` 이 블로킹이라 그 뒤에 적힌 `@tool()` 은
# 운영(`python server.py`)에서 등록되지 않는다 — 확장 도구 둘이 그렇게 빠져 있었다(시험은
# 모듈을 import 해 보므로 못 잡았다). `tests/test_server.py` 가 순서를 지킨다.
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

    # 라이브러리의 `run(transport="streamable-http")` 과 같되, 접속 요청의 토큰을 기억하는
    # 껍데기로 감싼다 — 안내문의 자동 요약을 그 사람의 눈으로 센다(ADR 0019).
    import uvicorn

    uvicorn.run(
        http_app(),
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
