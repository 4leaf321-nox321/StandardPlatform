#!/usr/bin/env python3
"""StandardPlatform 정제 파이프라인 — AI 결과물을 검토받고 넣는 길.

원천 데이터를 AI(Claude Code · Gemini CLI)가 곧바로 플랫폼에 넣으면
그룹마다 판단이 달라지고, 다시 넣을 때마다 같은 것이 둘씩 생기고,
무엇이 왜 들어갔는지 남지 않는다. 그래서 AI 는 **실행 폴더(run)** 에
결과물 묶음을 만들고, 사람은 이 도구로 검증 · 미리 보기 · 적재한다.

    python sp_pipeline.py init     runs/2026-09-13-parts
    python sp_pipeline.py validate runs/2026-09-13-parts
    python sp_pipeline.py preview  runs/2026-09-13-parts
    python sp_pipeline.py apply    runs/2026-09-13-parts
    python sp_pipeline.py docx     00-원천/설계서.docx      # Word → 글 초안 + 그림(sp_docx)

서버 주소와 토큰은 `SP_SERVER` · `SP_TOKEN` 환경 변수(또는 `--server`
· `--token`). 토큰은 플랫폼 「내 정보」 에서 발급한 개인 토큰이다.

표준 라이브러리만 쓴다 — 받는 사람 PC 에 무엇을 더 깔게 하지 않는다.
실행 폴더의 모양은 같은 폴더의 `AGENTS.md` 가 정본이다.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT = "sp-bundle/1"
MANIFEST = "bundle.json"
ONTOLOGY = "ontology.json"
OBJECTS_DIR = "objects"
RELATIONS_DIR = "relations"
#: 행에 붙이는 **문서 본문**(마크다운) —
#: `"_documents": {"<문서 칸 키>": "documents/<파일>.md"}`. 경로는 실행 폴더 기준이고, 보낼
#: 때 본문을 읽어 묶음의 `documents` 에 싣는다(플랫폼이 그 객체의 문서 칸에 판을 하나 올려
#: 쓴다). 객체는 식별자(`key`)로 찾는다.
DOCUMENTS_KEY = "_documents"
DOCUMENTS_DIR = "documents"
DOCUMENT_SUFFIXES = (".md", ".markdown", ".txt")
DOCUMENT_MAX_CHARS = 1_000_000
TOMBSTONES = "tombstones.json"
UNRESOLVED = "unresolved.json"
PREVIEW = "preview.json"
#: 앞선 미리 보기들 — 같은 실행을 다시 미리 보면 `preview.json` 은 마지막 것만 남는다. 앞의
#: 잡을 화면에서 이미 적용했는지 물으려면 그 잡들을 기억해야 한다.
PREVIEWS = "previews.json"
APPLIED = "applied.json"
#: 쓰지 않기로 한 실행 — 작업 상태의 할 일에서 뺀다(`run_close`).
CLOSED = "closed.json"
#: 적용이 거절된 마지막 결과 — 잠그지 않고 까닭만 남긴다(다시 미리 보고 적용).
APPLY_FAILED = "apply_failed.json"
#: **넣었는데 끝을 못 본 적용** — 플랫폼이 받았고(202) 아직 대기열 · 도는 중이거나, 기다리는
#: 사이 상태를 묻지 못했다. 거절이 아니다: 적용 작업의 번호를 두고 `work_status` · 다음
#: `run_apply` 가 다시 물어 `applied.json`(끝남) · `apply_failed.json`(실패)으로 옮긴다.
APPLYING = "applying.json"

#: 감사 기록에 남는 통로 이름 — 사람이 화면에서 넣은 것과 가른다.
CLIENT = "sp-pipeline"

# --- 이 PC 가 겨누는 플랫폼들 -----------------------------------------------------
#
# **한 PC 가 여러 플랫폼에 넣는다**(허브 · 쌍둥이 여럿). 그래서 주소 · 토큰을 한 벌로 두지
# 않고 **이름을 붙여 여럿** 둔다. 설치(`sp_setup.py`)를 플랫폼마다 한 번씩 돌리면 더해진다 —
# 앞에 등록한 것은 남는다.
#
# **작업 폴더가 자기 플랫폼을 기억한다**(`work.json` 의 `platform`). 검증 · 미리 보기는 그
# 플랫폼으로, 적용은 **미리 본 그 플랫폼으로만** 간다. 둘 이상인데 정해지지 않았으면 짐작하지
# 않고 묻는다 — 엉뚱한 플랫폼에 넣은 수만 줄은 되돌리기 전까지 그곳의 데이터다.
#
# 설정 파일은 키트 폴더가 아니라 **사용자 설정 폴더**에 둔다. 키트를 새 판으로 다시 풀어도
# 등록한 플랫폼이 남아야 하고, 사람이 명령 창에서 치는 적용 · 되돌리기도 같은 곳을 읽어야
# 한다(예전에는 주소 · 토큰을 AI 클라이언트의 MCP 설정에만 넣어서, 명령 창의 적용이 맨 끝에서
# 「서버와 토큰이 필요합니다」 로 멈췄다).
#
#     {"work_root": "D:\\온톨로지작업",
#      "platforms": {"rootdesign": {"server": "http://…:3030/rootdesign", "token": "spt_…"}},
#      "hub": {"server": "…", "token": "…"}}

#: 플랫폼 이름 — 그 설치의 slug 를 쓴다(화면의 설치 명령이 채워 준다).
PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def settings_path() -> Path:
    """사용자 설정 폴더의 `sp-pipeline/settings.json` — `SP_SETTINGS` 로 바꿀 수 있다(시험)."""
    explicit = os.environ.get("SP_SETTINGS", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "sp-pipeline" / "settings.json"


def load_settings() -> dict[str, Any]:
    """없거나 읽을 수 없으면 빈 것 — 「무엇이 없다」 는 부르는 쪽이 말한다."""
    try:
        loaded = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_settings(body: dict[str, Any]) -> Path:
    """토큰이 평문이다 — Claude Desktop 설정에 두던 것과 같은 값 · 같은 PC 다. 이 사용자만
    읽게 둔다(POSIX. Windows 는 사용자 폴더의 권한을 따른다)."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def platforms() -> dict[str, dict[str, str]]:
    """등록한 플랫폼들 — `{이름: {server, token}}`."""
    found = load_settings().get("platforms")
    if not isinstance(found, dict):
        return {}
    return {
        str(name): {
            "server": str(one.get("server") or ""),
            "token": str(one.get("token") or ""),
        }
        for name, one in found.items()
        if isinstance(one, dict)
    }


def setting(name: str) -> str:
    """환경 변수 → 설정 파일 차례로 — 작업 폴더 뿌리(`SP_WORK_ROOT`)와 허브(`SP_HUB_*`).

    **환경이 이긴다** — 창에서 잠깐 다른 곳을 볼 때. 플랫폼 주소 · 토큰은 여기가 아니라
    `target()` 이 정한다(이름으로 고른다).
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    settings = load_settings()
    hub = settings.get("hub") if isinstance(settings.get("hub"), dict) else {}
    found = {
        "SP_WORK_ROOT": settings.get("work_root"),
        "SP_HUB_SERVER": hub.get("server"),
        "SP_HUB_TOKEN": hub.get("token"),
    }.get(name)
    return str(found or "").strip()


@dataclass(frozen=True)
class Target:
    """넣을 곳 — 이름 · 주소 · 토큰. 이름이 비면 환경 변수로 준 것(옛 설치)이다."""

    name: str
    server: str
    token: str

    @property
    def shown(self) -> str:
        return f"{self.name} ({self.server})" if self.name else self.server


def _listing(known: dict[str, dict[str, str]]) -> str:
    return " · ".join(f"{name}({one['server']})" for name, one in sorted(known.items()))


def target(platform: str = "") -> Target:
    """**어느 플랫폼으로 가나** — 이름을 주면 그것, 아니면 하나뿐일 때만 그것.

    1. 이름이 있으면 등록한 것 중에서 — 없으면 무엇이 등록돼 있는지와 더하는 법을 말한다.
    2. `SP_SERVER` · `SP_TOKEN` 환경 변수 — 옛 설치(MCP 설정의 env)와 한 번만 다른 곳을 볼 때.
    3. 등록한 것이 **하나뿐이면** 그것.
    4. 여럿인데 이름이 없으면 **짐작하지 않고 멈춘다.**
    """
    known = platforms()
    if platform:
        found = known.get(platform)
        if found is None:
            raise Stop(
                f"이 PC 에 등록되지 않은 플랫폼입니다: {platform} — "
                f"등록된 것: {_listing(known) or '없음'}. 그 플랫폼 화면 「내 정보」 의 "
                "정제 도구 키트에 있는 설치 명령을 이 PC 에서 한 번 실행하면 더해집니다."
            )
        if not found["server"] or not found["token"]:
            raise Stop(f"플랫폼 {platform} 의 주소나 토큰이 비어 있습니다 — 설치 명령을 다시")
        return Target(platform, found["server"], found["token"])
    server = os.environ.get("SP_SERVER", "").strip()
    token = os.environ.get("SP_TOKEN", "").strip()
    if server and token:
        return Target("", server, token)
    if len(known) == 1:
        return target(next(iter(known)))
    if not known:
        raise Stop(
            "이 PC 에 등록된 플랫폼이 없습니다 — 플랫폼 화면 「내 정보」 의 정제 도구 키트에 "
            "있는 설치 명령을 실행하세요"
        )
    raise Stop(
        f"플랫폼이 여럿입니다 — {_listing(known)}. 어느 곳인지 정해야 합니다: 작업 폴더는 "
        "`work_platform`(또는 만들 때 platform=), 명령이면 --platform"
    )


def work_platform(run: Path) -> str:
    """실행 폴더가 든 **작업 폴더의 플랫폼** — `runs/<실행>` 의 두 단계 위 `work.json`.

    작업 폴더 밖의 실행(명령으로 바로 만든 것)이면 빈 값이다.
    """
    if run.parent.name != "runs":
        return ""
    body = _read_json(run.parent.parent / "work.json", [])
    return str(body.get("platform") or "") if isinstance(body, dict) else ""


#: 플랫폼이 한 번에 받는 행 수(파일로 넣기와 같다).
MAX_ROWS = 5000
RELATION_FIELDS = ("src", "relation", "dst", "evidence_note", "properties")
"""관계 한 줄의 칸. `properties` 는 **그 관계 종류의 속성**이다(인과 관계의 근거 건수처럼) —
모양은 플랫폼이 관계 종류의 정의로 본다."""
#: 이보다 낮은 확신도의 행은 넣지 않는다 — 모델링 규약 5장. unresolved.json 으로 간다.
LOW_CONFIDENCE = 0.7
ONTOLOGY_KEYS = {"groups", "interfaces", "types", "relation_types"}
#: **객체 행의 플랫폼 칸** — 플랫폼 일괄 입력의 고정 열(`objects/bulk.FIXED_COLUMNS`)과 같다.
#: 같은 키로 속성을 정의하면 행의 그 칸은 속성이 아니라 이것으로 읽힌다 — `status` 칸에 업무
#: 상태를 적으면 플랫폼은 수명(active · deprecated)으로 읽어 행마다 거절한다(실측: 검사는 오류
#: 0 으로 통과하고 미리 보기에서 463행이 막혔다).
RESERVED_ROW_KEYS = (
    "id",
    "key",
    "label",
    "description",
    "aliases",
    "status",
    "valid_from_year",
    "valid_to_year",
    "renamed_from",
    "previous_keys",
)
#: 객체의 수명 — 행의 `status` 가 받는 값.
LIFECYCLE = ("active", "deprecated")

#: 대량 적재(백필)에 켜는 칸들 — `bundle.json` 의 `backfill` 에 적거나 `--backfill` 로 켠다.
#:
#: 넷 다 기본값이 아닌 이유: 평소 적재에서는 기본값이 맞다(전부 아니면 무 · 줄마다 기록).
#: 수만 줄에서는 그 기본값이 발목을 잡는다 — 미리 보기가 적용을 두 번 돌고, 웹훅이 수만 번
#: 나가고, 감사 로그가 뒤덮이고, 한 줄이 전체를 막는다.
BACKFILL = {
    "preview": "plan",
    "events": "summary",
    "audit": "summary",
    "missing_refs": "blank",
}
#: 묶음에 실을 수 있는 칸과 그 값 — 실행 폴더가 적은 것을 여기서 검사한다.
BUNDLE_OPTIONS = {
    "preview": ("full", "plan"),
    "events": ("each", "summary"),
    "audit": ("each", "summary"),
    "missing_refs": ("error", "blank"),
    # **사람이 화면에서 고친 칸**을 비켜 갈지 덮을지. 기본(안 적으면)은 비켜 가는 것이고,
    # 백필에서도 그대로다 — 수만 줄을 넣는다고 사람의 수정을 되돌릴 이유는 없다.
    "human_edits": ("keep", "overwrite"),
}

#: (method, url, headers, body) -> (status, json). 시험이 갈아 끼운다.
Sender = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any]]

#: 플랫폼의 slug 규칙 — 백엔드 `ontology/services.SLUG_RE`(타입 · 관계 종류 · 묶음)와 같다.
#: 키트는 slug 로 **파일 이름과 주소의 한 마디**를 만든다. 대응 파일의 `"type_slug":
#: "../../../../<어디든>/claude_desktop_config"` 가 작업 폴더 밖의 JSON 을 덮었다 — 플랫폼이
#: 받지 않을 꼴은 키트가 먼저 막는다. (`fullmatch` 로 본다 — `$` 는 끝의 줄바꿈을 지나친다.)
SLUG_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
#: 속성 키 — 백엔드 `KEY_RE`. 문서 속성의 키가 주소의 한 마디가 된다.
KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,47}")


def require_slug(value: Any, what: str) -> str:
    """플랫폼 slug 꼴의 값만 — 아니면 무엇이 틀렸는지 말하고 멈춘다."""
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        raise Stop(
            f"{what} {value!r} 는 플랫폼의 slug 꼴이 아닙니다 — 소문자로 시작하는 영문 · "
            "숫자 · 밑줄 32자 이내(예: model_spec)"
        )
    return value


def require_key(value: Any, what: str) -> str:
    """플랫폼 속성 키 꼴의 값만."""
    if not isinstance(value, str) or not KEY_RE.fullmatch(value):
        raise Stop(
            f"{what} {value!r} 는 플랫폼의 속성 키 꼴이 아닙니다 — 소문자로 시작하는 영문 · "
            "숫자 · 밑줄 48자 이내"
        )
    return value


def api_path(*segments: Any) -> str:
    """플랫폼 주소의 경로 — **마디마다 감싸서**(`quote(…, safe="")`) 잇는다.

    값을 f-문자열로 그대로 이으면 값 속의 `/` · `?` · `#` 이 주소의 다른 자리가 된다:
    `documents/{key}` 의 키가 `x?y` 면 질의가, `../..` 면 다른 경로가 된다(플랫폼 MCP 도 같은
    것을 고쳤다, ADR 0102 §5). `.` · `..` 는 감싸도 그대로라 여기서 거절한다."""
    parts: list[str] = []
    for one in segments:
        text = str(one)
        if text in ("", ".", ".."):
            raise Stop(f"주소의 한 마디가 비었거나 {text!r} 입니다 — 값을 확인하세요")
        parts.append("/" + urllib.parse.quote(text, safe=""))
    return "".join(parts)


def api_url(server: str, path: str, params: dict[str, Any] | None = None) -> str:
    """주소 + 질의. **질의는 `params` 로만** 붙인다(`urlencode`) — 경로에 `?` 를 이어 쓰면 값이
    질의의 다른 칸(`&apply=true`)이 될 수 있다."""
    if "?" in path or "#" in path:
        raise Stop(f"경로에 질의 · 조각을 잇지 않습니다(params 로): {path!r}")
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    return f"{server.rstrip('/')}{path}{query}"


#: 주소 어디에도 있으면 안 되는 글자 — 제어 문자 · 빈칸, `\`(Windows 꼴 구분자 — 중계가 `/`
#: 로 읽는다), `#`(그 뒤가 조각이 되어 서버에 안 간다 — 꼬리를 잘라 다른 길이 된다).
_URL_BAD = re.compile(r"[\x00-\x20\x7f\\#]")
#: 경로에 있으면 안 되는 감싼 글자 — `.` · `/` · `\`. 키트의 값(slug · 키 · uuid)에는 이것이
#: 들 일이 없고, 중계 · 서버가 풀어 읽으면 `..` · 구분자가 된다.
_PATH_ESCAPES = ("%2e", "%2f", "%5c")


def check_url(url: str) -> str:
    """**키트가 보내는 모든 요청이 지나는 마지막 검사** — 주소가 다른 자리를 가리키면 보내지
    않는다. 주소를 만드는 자리마다 감싸는 것(`api_path`)이 먼저고, 이것은 그 하나를 빠뜨린
    날을 위한 것이다."""
    parts = urllib.parse.urlsplit(url)
    bad = _URL_BAD.search(url)
    segments = parts.path.split("/")
    lowered = parts.path.lower()
    why = ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        why = "http · https 주소가 아님"
    elif bad:
        why = f"{bad.group(0)!r} 가 들어 있음"
    elif any(one in (".", "..") for one in segments):
        why = "`.` · `..` 마디"
    elif any(one in lowered for one in _PATH_ESCAPES):
        why = "감싼 `.` · `/` · `\\`(%2e · %2f · %5c)"
    if why:
        raise Stop(
            f"주소가 다른 자리를 가리킬 수 있어 보내지 않았습니다({why}): "
            f"{parts.path[:200]!r} — 값(slug · 키 · 번호)을 확인하세요"
        )
    return url


def _origin(url: str) -> tuple[str, str, int | None]:
    """(scheme, 호스트, 포트) — 포트를 안 적었으면 그 scheme 의 기본 포트."""
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return (
        scheme,
        (parts.hostname or "").lower(),
        port or {"http": 80, "https": 443}.get(scheme),
    )


def _shown_origin(url: str) -> str:
    scheme, host, port = _origin(url)
    return f"{scheme}://{host}" + (f":{port}" if port not in (None, 80, 443) else "")


def _redirected(here: str, there: str, code: int) -> str:
    return (
        f"다른 주소로 넘겨져 멈췄습니다({code} → {_shown_origin(there)}) — 포털 로그인이 "
        "끝났을 수 있습니다. 토큰은 그 주소로 보내지 않았습니다. 브라우저로 그 플랫폼에 다시 "
        f"로그인한 뒤 다시 하세요. 늘 그렇다면 등록한 주소({_shown_origin(here)})가 플랫폼의 "
        "주소가 맞는지(http · https · 포트까지) 확인하세요."
    )


class _SameOriginRedirects(urllib.request.HTTPRedirectHandler):
    """**다른 곳(scheme · 호스트 · 포트)으로 넘기는 답은 따라가지 않는다** — 토큰이 그리로
    가지 않게.

    urllib 의 기본 처리는 넘겨진 곳에 머리글을 그대로 싣는다 — `Authorization: Bearer
    <토큰>` 까지. 포털(HWAX) 뒤의 플랫폼은 포털 세션이 끝나면 다른 호스트의 로그인(인증
    서버)으로 넘기고, 키트의 토큰은 만료가 없다. 같은 곳 안에서 넘기는 것(`/x/` → `/x`)만
    따른다."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if _origin(newurl) != _origin(req.full_url):
            with contextlib.suppress(Exception):
                fp.close()
            raise Stop(_redirected(req.full_url, newurl, code))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


#: 키트의 HTTP — 기본 처리기 그대로에 넘김만 바꾼 것(`urlopen` 의 전역 opener 는 건드리지
#: 않는다).
_OPENER = urllib.request.build_opener(_SameOriginRedirects)


def _urllib_send(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float = 600,
) -> tuple[int, Any]:
    request = urllib.request.Request(check_url(url), data=body, method=method, headers=headers)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
            kind = str(response.headers.get("Content-Type") or "")
    except urllib.error.HTTPError as failure:
        raw = failure.read()
        try:
            return failure.code, json.loads(raw or b"null")
        except ValueError:
            text = raw.decode("utf-8", errors="replace")
            return failure.code, {"error": {"message": text[:500]}}
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        # **2xx 인데 JSON 이 아니다** — 대개 포털(HWAX)의 로그인 화면(HTML)이다: 포털 세션이
        # 끝나면 포털이 플랫폼 대신 로그인 쪽을 200 으로 준다. 그대로 두면 사람은
        # 「Expecting value: line 1 column 1」 만 보고 무엇을 고칠지 모른다(0.4.107 점검).
        raise Stop(_not_json(url, status, kind, raw)) from None


def _not_json(url: str, status: int, kind: str, raw: bytes) -> str:
    head = raw[:400].decode("utf-8", errors="replace").lower()
    page = "html" in kind.lower() or "<html" in head or "<!doctype" in head
    where = url.split("?", 1)[0]
    said = f"{status}" + (f" · {kind.split(';')[0]}" if kind else "")
    return f"플랫폼이 JSON 이 아닌 답을 보냈습니다({said}): {where} — " + (
        "로그인 화면(HTML)으로 보입니다. 포털(HWAX) 뒤의 플랫폼이면 포털의 로그인이 끝났을 "
        "수 있습니다 — 브라우저로 그 플랫폼에 다시 로그인한 뒤 다시 하세요. 등록한 주소가 "
        "플랫폼의 주소가 맞는지도 확인하세요."
        if page
        else "등록한 주소가 플랫폼의 주소가 맞는지, 사이의 중계(포털 · 프록시)가 답을 바꾸지 "
        "않는지 확인하세요."
    )


SEND: Sender = _urllib_send
#: 작업을 기다리는 사이의 잠. 시험이 no-op 으로 바꾼다.
WAIT: Callable[[float], None] = time.sleep
POLL_SECONDS = 1.5
#: 「대기」 가 이만큼 이어지면 멈추고 말한다 — **집을 워커가 없다는 뜻이다.** 말없이 계속
#: 기다리면 사람은 제 묶음이 잘못된 줄 알고 몇 번을 다시 만든다. 도는 중(running)이면 안 센다.
QUEUED_LIMIT_SECONDS = 120.0


# --------------------------------------------------------------------------
# 실행 폴더 읽기
# --------------------------------------------------------------------------


@dataclass
class Batch:
    type_slug: str
    file: str
    rows: list[Any]
    workspace_slug: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    """묶음에 붙은 **그 밖의 것** — `aliases_mode`(별칭을 더할지 맞출지) · `mode`(관계를
    더할지 맞출지). 허브가 적어 보낸 것을 **그대로 전해야** 한다: 여기서 떨어뜨리면 허브에서
    뺀 별칭과 끊은 선이 받는 쪽에 남아 둘이 갈린다."""


@dataclass
class Run:
    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    ontology: dict[str, Any] | None = None
    objects: list[Batch] = field(default_factory=list)
    relations: list[Batch] = field(default_factory=list)
    tombstones: dict[str, Any] | None = None
    """허브에서 **사라진 것** — 지운 객체와 끊긴 선. 받는 쪽이 사용 중지 · 합치기 · 끊기를
    계획에 올린다."""
    unresolved: list[Any] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """파일을 읽다가 난 문제 — 모양을 검사하기도 전에 막힌 것."""


def _read_json(path: Path, problems: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as failure:
        problems.append(f"{path.name}: JSON 을 읽을 수 없습니다 ({failure})")
        return None


def _batches(run: Run, folder: str) -> list[Batch]:
    base = run.path / folder
    if not base.is_dir():
        return []
    found: dict[str, Batch] = {}
    for file in sorted(base.glob("*.json")):
        body = _read_json(file, run.problems)
        if body is None:
            continue
        if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
            run.problems.append(f'{folder}/{file.name}: {{"rows": [...]}} 모양이어야 합니다')
            continue
        slug = str(body.get("type_slug") or file.stem)
        found[file.name] = Batch(
            type_slug=slug,
            file=f"{folder}/{file.name}",
            rows=body["rows"],
            workspace_slug=body.get("workspace_slug"),
            extra={
                key: body[key] for key in ("aliases_mode", "mode") if body.get(key) is not None
            },
        )
    # **적는 차례대로 넣는다.** 참조하는 타입은 참조되는 타입 뒤에 와야 한다.
    order = [str(one) for one in run.manifest.get(f"{folder}_order") or []]
    ranked = sorted(
        found.values(),
        key=lambda one: order.index(one.type_slug) if one.type_slug in order else len(order),
    )
    return ranked


def load(path: Path) -> Run:
    run = Run(path=path)
    manifest = _read_json(path / MANIFEST, run.problems)
    run.manifest = manifest if isinstance(manifest, dict) else {}
    if manifest is None and not (path / MANIFEST).exists():
        run.problems.append(f"{MANIFEST} 가 없습니다 — 먼저 init 하세요")
    ontology = _read_json(path / ONTOLOGY, run.problems)
    if isinstance(ontology, dict) and any(ontology.get(key) for key in ONTOLOGY_KEYS):
        run.ontology = ontology
    elif ontology is not None and not isinstance(ontology, dict):
        run.problems.append(f"{ONTOLOGY}: 객체({{...}})여야 합니다")
    run.objects = _batches(run, OBJECTS_DIR)
    run.relations = _batches(run, RELATIONS_DIR)
    graves = _read_json(path / TOMBSTONES, run.problems)
    if isinstance(graves, dict) and (graves.get("objects") or graves.get("relations")):
        run.tombstones = graves
    elif graves is not None and not isinstance(graves, dict):
        run.problems.append(f"{TOMBSTONES}: 객체({{...}})여야 합니다")
    unresolved = _read_json(path / UNRESOLVED, run.problems)
    run.unresolved = unresolved if isinstance(unresolved, list) else []
    return run


# --------------------------------------------------------------------------
# 문서 본문 — 행에 붙인 마크다운 · 이름 · 전화 검사
# --------------------------------------------------------------------------

#: 전화 · 메일 · 주민등록번호 꼴 — 본문에 있으면 **오류**(가리거나 빼고 보낸다). 번호는 국내
#: 앞자리(0 으로 시작, 국제 표기는 +82)만 본다 — 「18-2024-0012」 같은 문서 · 시험 번호가
#: 전화로 막혔다(0.4.106).
PHONE = re.compile(
    r"(?<![\d-])(?:\+82[-.\s]?(?:1[016789]|[2-6]\d?|70)|\(?0(?:1[016789]|2|[3-6]\d|70|50\d)\)?)"
    r"[-.\s)]\s?\d{3,4}[-.\s]\d{4}(?![\d-])"
    r"|(?<!\d)01[016789]\d{7,8}(?!\d)"
)
#: 끝이 글자 도메인(.com · .kr …)인 것만 — 「react@18.3.1」 같은 판 표기는 메일이 아니다.
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?![\w-])")
#: 앞 여섯이 날짜(YYMMDD)이고 뒤 첫 자리가 1 ~ 8 인 13자리. 가운데 줄표가 있거나 검증
#: 숫자가 맞으면 오류, 줄표 없이 붙은 13자리는 검증 숫자가 안 맞으면 **경고** — 바코드 ·
#: 밀리초 시각 · 일련번호도 13자리다(2020-10 뒤 새 번호는 검증 숫자가 없어 경고로 남는다).
RESIDENT = re.compile(r"(?<![\d-])(\d{2})(\d{2})(\d{2})([-\s]?)([1-8])(\d{6})(?![\d-])")
_RESIDENT_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)


def _resident_kind(found: re.Match[str]) -> str:
    """「error」 · 「warning」 · 「」(주민등록번호가 아님)."""
    _year, month, day, joint, first, rest = found.groups()
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        return ""
    if joint:
        return "error"
    digits = [int(one) for one in f"{found.group(1)}{month}{day}{first}{rest}"]
    # 바코드(EAN-13 — 국내 상품은 880 으로 시작해 날짜처럼 읽힌다)의 검증 숫자가 맞으면 경고만.
    barcode = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12])) % 10) % 10
    if barcode == digits[12]:
        return "warning"
    check = (
        11 - sum(a * b for a, b in zip(digits[:12], _RESIDENT_WEIGHTS, strict=True)) % 11
    ) % 10
    return "error" if check == digits[12] else "warning"


#: 사람 이름 꼴 — 직함이 붙은 이름 · 「담당: 홍길동」. 짐작이라 **경고**(사람에게 묻는다).
PERSON = re.compile(
    r"(?<![가-힣])[가-힣]{2,4}\s?(?:님|씨|책임|선임|수석|과장|대리|부장|차장|팀장|사원|주임|"
    # 직함 뒤에는 조사가 붙는다(「김철수 책임이」) — 뒤를 막지 않는다. 짐작이라 경고뿐이다.
    r"매니저|연구원|위원|교수|박사)"
    r"|(?:이름|성명|담당자?|작성자|연락처)\s*[:\uff1a]\s*[가-힣]{2,4}(?![가-힣])"
)


def _masked(text: str) -> str:
    """보고서에 싣는 꼴 — 앞 셋만 두고 가린다(보고서는 사내 밖으로 옮겨진다)."""
    shown = text.strip()
    return shown[:3] + "*" * max(0, len(shown) - 3)


def personal_info(body: str) -> tuple[list[str], list[str]]:
    """(오류, 경고) — 전화 · 메일 · 주민등록번호는 오류, 사람 이름 꼴은 경고. 줄 번호와 가린
    값만 적는다."""
    errors: list[str] = []
    warnings: list[str] = []
    for number, line in enumerate(body.splitlines(), start=1):
        for what, pattern, out in (
            ("전화번호", PHONE, errors),
            ("메일 주소", EMAIL, errors),
            ("사람 이름 꼴", PERSON, warnings),
        ):
            for found in pattern.finditer(line):
                out.append(f"{number}줄 {what} {_masked(found.group(0))}")
        for found in RESIDENT.finditer(line):
            kind = _resident_kind(found)
            if kind == "error":
                errors.append(f"{number}줄 주민등록번호 꼴 {_masked(found.group(0))}")
            elif kind == "warning":
                warnings.append(
                    f"{number}줄 주민등록번호일 수 있는 13자리 {_masked(found.group(0))} — "
                    "바코드 · "
                    "일련번호면 그대로 둔다"
                )
    return errors, warnings


def nul_line(body: str) -> int:
    """본문에 NUL(`\\x00`)이 있으면 그 줄 번호(1부터), 없으면 0. PostgreSQL 의 글자 칸은 NUL
    을 못 담는다 — 키트를 지나 플랫폼의 적용 묶음에서야 DataError 로 통째로 멈췄다(0.4.107
    점검)."""
    at = body.find("\x00")
    return body.count("\n", 0, at) + 1 if at >= 0 else 0


@dataclass
class Document:
    """보낼 문서 본문 하나 — 행에 붙인 파일에서 읽었다."""

    where: str
    type_slug: str
    key: str
    property: str
    path: str
    body: str | None
    problem: str = ""
    workspace_slug: str | None = None


def documents_of(run: Run) -> list[Document]:
    """행마다 붙인 본문 파일 — 읽지 못한 것은 `problem` 에 까닭(검사가 오류로 올린다)."""
    out: list[Document] = []
    for batch in run.objects:
        for index, row in enumerate(batch.rows, start=1):
            if not isinstance(row, dict) or DOCUMENTS_KEY not in row:
                continue
            where = f"{batch.file} {index}행"
            attached = row[DOCUMENTS_KEY]
            key = str(row.get("key") or "").strip()
            if not isinstance(attached, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in attached.items()
            ):
                out.append(
                    Document(
                        where,
                        batch.type_slug,
                        key,
                        "",
                        "",
                        None,
                        f'{DOCUMENTS_KEY} 는 {{"문서 칸 키": "documents/파일.md"}} '
                        "이어야 합니다",
                    )
                )
                continue
            for prop, relative in attached.items():
                doc = Document(
                    where,
                    batch.type_slug,
                    key,
                    prop,
                    relative,
                    None,
                    workspace_slug=batch.workspace_slug,
                )
                out.append(doc)
                if not key:
                    doc.problem = "문서 본문을 붙이려면 식별자(key)가 있어야 합니다"
                    continue
                path = (run.path / relative).resolve()
                if run.path.resolve() not in path.parents:
                    doc.problem = f"실행 폴더 밖의 파일입니다: {relative}"
                    continue
                if path.suffix.lower() not in DOCUMENT_SUFFIXES:
                    doc.problem = f"마크다운(.md)이 아닙니다: {relative}"
                    continue
                try:
                    body = path.read_text(encoding="utf-8-sig")
                except FileNotFoundError:
                    doc.problem = f"파일이 없습니다: {relative}"
                    continue
                except (OSError, UnicodeDecodeError) as failure:
                    doc.problem = f"읽을 수 없습니다({relative}): {failure}"
                    continue
                if len(body) > DOCUMENT_MAX_CHARS:
                    doc.problem = (
                        f"본문이 {len(body):,}자입니다 — {DOCUMENT_MAX_CHARS:,}자까지"
                        f"({relative})"
                    )
                    continue
                nul = nul_line(body)
                if nul:
                    doc.problem = (
                        f"본문 {nul}번째 줄에 NUL 문자(\\x00)가 있습니다 — 플랫폼이 저장하지 "
                        f"못해 적용 전체가 멈춥니다. 지우고 보낸다({relative})"
                    )
                    continue
                doc.body = body
    return out


def _check_documents(run: Run, report: Report) -> None:
    """붙인 본문 — 파일 · 식별자 · 문서 칸(실행 폴더의 정의가 말하면)과 **이름 · 전화**."""
    kinds: dict[str, dict[str, str]] = {
        str(one.get("slug")): {
            str(prop.get("key")): str(prop.get("data_type") or "")
            for prop in one.get("properties") or []
            if isinstance(prop, dict)
        }
        for one in ((run.ontology or {}).get("types") or [])
        if isinstance(one, dict)
    }
    seen: dict[tuple[str, str, str, str], str] = {}
    for doc in documents_of(run):
        label = f"{doc.where} 문서 {doc.property or '?'}"
        if doc.problem:
            report.errors.append(f"{label}: {doc.problem}")
            continue
        mark = (doc.type_slug, doc.workspace_slug or "", doc.key, doc.property)
        if mark in seen:
            report.errors.append(
                f"{label}: {seen[mark]} 과 같은 객체 · 문서 칸입니다 — 본문은 하나만 붙인다"
            )
            continue
        seen[mark] = doc.where
        props = kinds.get(doc.type_slug)
        if props is not None and props.get(doc.property) not in (None, "document"):
            report.errors.append(
                f"{label}: {doc.type_slug}.{doc.property} 는 문서 칸이 아닙니다"
                f"({props[doc.property]}) — data_type 이 document 인 칸에만 본문을 붙입니다"
            )
        elif props is not None and doc.property not in props:
            report.warnings.append(
                f"{label}: 실행 폴더의 정의에 {doc.type_slug}.{doc.property} 가 없습니다 — "
                "플랫폼에 그 문서 칸이 있어야 합니다(미리 보기가 확인한다)"
            )
        found, maybe = personal_info(doc.body or "")
        if found:
            shown = " · ".join(found[:5]) + (f" 외 {len(found) - 5}" if len(found) > 5 else "")
            report.errors.append(
                f"{label}({doc.path}): 개인 정보 꼴 {len(found)}곳 — {shown}. 가리거나 빼고 "
                "보낸다(문서는 판으로 남아 지워도 옛 판에 남는다)"
            )
        if maybe:
            shown = " · ".join(maybe[:5]) + (f" 외 {len(maybe) - 5}" if len(maybe) > 5 else "")
            report.warnings.append(
                f"{label}({doc.path}): 확인할 꼴 {len(maybe)}곳 — {shown}. 실은 사람 이름 · "
                "주민등록번호면 직함 · 역할로 바꾸거나 빼고, 아니면 사람에게 확인받는다"
            )


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """`_` 로 시작하는 칸(출처 · 확신도 · 메모)은 **플랫폼에 안 보낸다.**

    출처는 행 옆에 두어야 AI 가 빠뜨리지 않고, 사람이 행을 보며 확인한다. 그런데
    플랫폼은 모르는 열을 거절한다 — 그래서 보내기 전에 여기서 뗀다.
    """
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def payload(run: Run) -> dict[str, Any]:
    """플랫폼 `POST /api/bundles/import` 에 보내는 몸통(`apply` 제외).

    허브에서 받은 실행(`bundle.json` 의 `source`)이면 그 이름을 싣는다 — 플랫폼은 그 허브가
    관리하는 정의 · 객체만 이 길로 고치게 한다."""
    body = {
        "ontology": run.ontology,
        "objects": [
            {
                "type_slug": one.type_slug,
                "workspace_slug": one.workspace_slug,
                "rows": [_clean(row) for row in one.rows if isinstance(row, dict)],
                **one.extra,
            }
            for one in run.objects
        ],
        "relations": [
            {
                "type_slug": one.type_slug,
                "rows": [_clean(row) for row in one.rows if isinstance(row, dict)],
                **one.extra,
            }
            for one in run.relations
        ],
    }
    attached = [
        {
            "type_slug": one.type_slug,
            "key": one.key,
            "property": one.property,
            "body": one.body,
        }
        # 부서 범위 식별자 타입이면 어느 부서의 것인지 — 객체 묶음의 부서를 그대로.
        | ({"workspace_slug": one.workspace_slug} if one.workspace_slug else {})
        for one in documents_of(run)
        if one.body is not None and not one.problem
    ]
    if attached:
        body["documents"] = attached
    if run.tombstones:
        body["tombstones"] = run.tombstones
    source = str(run.manifest.get("source") or "").strip()
    if source:
        body["source"] = source
    # **백필 칸을 그대로 싣는다** — 실행 폴더가 정한다(`bundle.json`). 여기서 떨어뜨리면
    # 그 넷은 API 로 직접 부르는 쪽만 쓸 수 있고, 도구로 넣는 백필은 기본값으로 돈다.
    body.update(bundle_options(run.manifest))
    return body


def bundle_options(manifest: dict[str, Any]) -> dict[str, str]:
    """`bundle.json` 이 정한 묶음 칸 — `backfill: true` 면 넷을 한꺼번에 켠다."""
    out: dict[str, str] = {}
    if manifest.get("backfill"):
        out.update(BACKFILL)
    for name, allowed in BUNDLE_OPTIONS.items():
        value = manifest.get(name)
        if value is None:
            continue
        if str(value) not in allowed:
            raise Stop(
                f"{MANIFEST}: {name} 은 {' · '.join(allowed)} 중 하나입니다 ({value!r})"
            )
        out[name] = str(value)
    return out


def digest(body: dict[str, Any]) -> str:
    """보낼 몸통의 지문. **미리 본 것과 적용하는 것이 같은지** 이것으로 가른다."""
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------
# 검증 — 서버에 보내기 전에 잡을 수 있는 것
# --------------------------------------------------------------------------


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


#: (타입, 식별자들) → 플랫폼에 **이미 있는** 식별자 — 이름이 빈 행이 있는 객체를 고치는
#: 것인지 가른다. 플랫폼에 안 물으면 None.
KnownKeys = Callable[[str, list[str]], set[str]]


def validate(
    run: Run, *, allow_unresolved: bool = False, known: KnownKeys | None = None
) -> Report:
    report = Report(errors=list(run.problems))
    if run.manifest:
        try:
            bundle_options(run.manifest)
        except Stop as bad:
            report.errors.append(str(bad))
    if run.manifest and run.manifest.get("format") != FORMAT:
        report.errors.append(
            f"{MANIFEST}: format 이 {FORMAT} 가 아닙니다 ({run.manifest.get('format')!r})"
        )

    if run.ontology is not None:
        unknown = sorted(set(run.ontology) - ONTOLOGY_KEYS)
        if unknown:
            report.errors.append(f"{ONTOLOGY}: 모르는 항목 {', '.join(unknown)}")
        for index, one in enumerate(run.ontology.get("types") or [], start=1):
            if not isinstance(one, dict) or not one.get("slug") or not one.get("label"):
                report.errors.append(f"{ONTOLOGY}: types[{index}] 에 slug · label 이 없습니다")
                continue
            for prop in one.get("properties") or []:
                key = str(prop.get("key") or "") if isinstance(prop, dict) else ""
                if key in RESERVED_ROW_KEYS:
                    report.errors.append(
                        f"{ONTOLOGY}: {one['slug']} 의 속성 키 {key!r} 는 객체 행의 플랫폼 "
                        f"칸과 겹칩니다 — 행의 {key!r} 는 속성이 아니라 플랫폼 칸으로 읽힙니다"
                        + (
                            "(수명: active · deprecated). 업무 상태면 다른 키로"
                            "(예: work_status)"
                            if key == "status"
                            else ". 다른 키로"
                        )
                    )

    if run.ontology is None and not run.objects and not run.relations:
        report.errors.append(
            "묶음이 비어 있습니다 — 정의 · 객체 · 관계 중 하나는 있어야 합니다"
        )

    received = bool(str(run.manifest.get("source") or "").strip())
    keys: dict[str, dict[str, str]] = {}
    for batch in run.objects:
        _check_rows(batch, report, max_rows=MAX_ROWS, received=received)
        if not batch.workspace_slug and not received:
            report.warnings.append(
                f"{batch.file}: workspace_slug 이 없어 **전역**으로 들어갑니다"
                " (시스템 관리자만)"
            )
        seen = keys.setdefault(batch.type_slug, {})
        nameless: list[tuple[int, str]] = []
        lifecycle: list[int] = []
        years: list[int] = []
        for index, row in enumerate(batch.rows, start=1):
            if not isinstance(row, dict):
                continue
            if not str(row.get("label") or "").strip():
                if not str(row.get("key") or "").strip():
                    report.errors.append(f"{batch.file} {index}행: label 도 key 도 없습니다")
                else:
                    nameless.append((index, str(row["key"]).strip()))
            status = row.get("status")
            if status not in (None, "") and str(status).strip() not in LIFECYCLE:
                lifecycle.append(index)
            for name in ("valid_from_year", "valid_to_year"):
                raw = row.get(name)
                if raw not in (None, "") and not str(raw).strip().lstrip("-").isdigit():
                    years.append(index)
                    break
            key = str(row.get("key") or "").strip()
            if key:
                where = f"{batch.file} {index}행"
                if key in seen:
                    report.errors.append(
                        f"{where}: 식별자 {key!r} 가 겹칩니다 (먼저 {seen[key]})"
                    )
                else:
                    seen[key] = where
        if lifecycle:
            report.errors.append(
                f"{batch.file}: status 가 플랫폼의 수명 칸(active · deprecated)이 아닌 행 "
                f"{len(lifecycle)}개({_rows(lifecycle)}행) — 행의 status 는 수명으로 "
                "읽힙니다. 업무 상태면 다른 칸 키로(예: work_status)"
            )
        if years:
            report.errors.append(
                f"{batch.file}: valid_from_year · valid_to_year 가 연도(숫자)가 아닌 행 "
                f"{len(years)}개({_rows(years)}행)"
            )
        if nameless:
            # 플랫폼에 이미 있는 식별자면 그 객체를 고치는 행이다 — 이름 없이도 된다.
            existing = (
                known(batch.type_slug, [key for _index, key in nameless])
                if known is not None
                else set()
            )
            missing = [index for index, key in nameless if key not in existing]
            if missing:
                report.errors.append(
                    f"{batch.file}: label(이름)이 빈 행 {len(missing)}개"
                    f"({_rows(missing)}행) — 새로 만드는 객체는 이름이 있어야 합니다"
                    + (
                        ""
                        if known is not None
                        else ". 이미 있는 객체를 고치는 행이면 플랫폼에 묻는 검사로 다시 "
                        "(run_validate ask_platform=true)"
                    )
                )

    for batch in run.relations:
        _check_rows(batch, report, max_rows=MAX_ROWS, received=received)
        no_evidence = 0
        for index, row in enumerate(batch.rows, start=1):
            if not isinstance(row, dict):
                continue
            missing = [name for name in ("src", "relation", "dst") if not row.get(name)]
            if missing:
                report.errors.append(
                    f"{batch.file} {index}행: {', '.join(missing)} 가 없습니다"
                )
            extra = sorted(
                key
                for key in row
                if not str(key).startswith("_") and key not in RELATION_FIELDS
            )
            if extra:
                report.errors.append(
                    f"{batch.file} {index}행: 관계 행에 없는 칸 {', '.join(extra)}"
                    f" (쓸 수 있는 것: {', '.join(RELATION_FIELDS)})"
                )
            if "properties" in row and not isinstance(row["properties"], dict):
                report.errors.append(
                    f"{batch.file} {index}행: properties 는 {{키: 값}} 이어야 합니다"
                )
            if not row.get("evidence_note"):
                no_evidence += 1
        if no_evidence and not received:
            report.warnings.append(
                f"{batch.file}: 근거(evidence_note)가 없는 관계 {no_evidence}줄"
                " — 왜 이었는지 남지 않습니다"
            )

    _check_documents(run, report)

    if run.unresolved:
        message = f"{UNRESOLVED}: 미해결 {len(run.unresolved)}건 — 사람이 판단한 뒤 비우세요"
        (report.warnings if allow_unresolved else report.errors).append(message)
    return report


def _rows(indexes: list[int], limit: int = 5) -> str:
    shown = ", ".join(str(one) for one in indexes[:limit])
    return shown + (f" 외 {len(indexes) - limit}" if len(indexes) > limit else "")


def _check_rows(
    batch: Batch, report: Report, *, max_rows: int, received: bool = False
) -> None:
    if len(batch.rows) > max_rows:
        report.errors.append(
            f"{batch.file}: {len(batch.rows)}행 — 한 파일에 {max_rows}행까지입니다. 나누세요"
        )
    not_dict = sum(1 for row in batch.rows if not isinstance(row, dict))
    if not_dict:
        report.errors.append(f"{batch.file}: 객체({{...}})가 아닌 행 {not_dict}개")
    rows = [row for row in batch.rows if isinstance(row, dict)]
    weak = [
        index
        for index, row in enumerate(rows, start=1)
        if isinstance(row.get("_confidence"), int | float)
        and row["_confidence"] < LOW_CONFIDENCE
    ]
    if weak:
        report.errors.append(
            f"{batch.file}: 확신도 {LOW_CONFIDENCE} 미만인 행 {len(weak)}개({_rows(weak)}행)"
            " — 넣지 말고 unresolved.json 으로 옮기세요"
        )
    if received:
        # 허브에서 받은 행은 원천이 허브다 — 출처 · 인용을 행마다 요구하지 않는다.
        return
    # 문서(쪽 · 슬라이드)에서 뽑은 행은 원문 인용이 있어야 검토하는 사람이 원문을 안 연다.
    unquoted = [
        index
        for index, row in enumerate(rows, start=1)
        if isinstance(row.get("_source"), dict)
        and ("page" in row["_source"] or "slide" in row["_source"])
        and not row["_source"].get("quote")
    ]
    if unquoted:
        report.warnings.append(
            f"{batch.file}: 문서에서 뽑았는데 원문 인용(_source.quote)이 없는 행"
            f" {len(unquoted)}개({_rows(unquoted)}행) — 검토할 때 원문을 열어야 합니다"
        )
    no_source = sum(
        1 for row in batch.rows if isinstance(row, dict) and not row.get("_source")
    )
    if no_source:
        report.warnings.append(
            f"{batch.file}: 출처(_source)가 없는 행 {no_source}개 — 어디서 왔는지 모릅니다"
        )


# --------------------------------------------------------------------------
# 플랫폼에 보내기
# --------------------------------------------------------------------------


class Stop(Exception):
    """사람에게 말하고 멈출 일."""


class Pending(Stop):
    """플랫폼이 작업을 **받았는데 끝을 못 봤다** — 대기열에 오래 있거나(워커가 밤 계산 등으로
    바쁨 · 꺼짐), 기다리는 사이 상태를 묻지 못했다. **거절이 아니다**: 작업은 플랫폼에 남아
    워커가 집으면 그대로 돈다. 미리 보기 · 받기에서는 여느 멈춤과 같고, 적용에서는 「거절됨」
    으로 적지 않고 그 작업의 번호를 둔다(`APPLYING`)."""

    def __init__(self, message: str, job_id: str = "") -> None:
        super().__init__(message)
        self.job_id = job_id


def job_uuid(value: Any, what: str = "작업 번호") -> str:
    """작업 · 판의 번호 — **uuid 꼴만**, 표준 꼴(소문자 · 줄표)로. 주소에 넣는 번호는 모두
    여기를 거친다: `run_undo(run_id="<uuid>/undo?apply=true#")` 처럼 번호 자리에 주소를 이어
    붙이면 계획만 볼 자리에서 되돌리기가 곧장 적용됐다(0.4.107 점검)."""
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except ValueError:
        raise Stop(f"{what}가 아닙니다: {value!r} — uuid 꼴이어야 합니다") from None


def _post_bundle(server: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    """묶음을 보내고 **작업이 끝날 때까지 기다린다.**

    플랫폼은 묶음을 요청 안에서 처리하지 않는다 — 1만 객체에 44초라 클라이언트가 먼저 끊었다.
    202 로 작업이 오고, 여기서 그 작업을 보다가 끝난 결과(옛 응답과 같은 모양)를 돌려준다.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # 감사 기록에 「이 도구가 넣었다」 가 남는다.
        "X-Client": CLIENT,
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = api_url(server, "/api/bundles/import")
    status, answer = _send(server, "POST", url, headers, raw, who="플랫폼")
    if status != 202:
        _refused("플랫폼", status, answer)
    job = _wait_job(server, token, answer)
    result = job.get("result")
    if not isinstance(result, dict):
        raise Stop(f"플랫폼 응답을 읽을 수 없습니다: {job!r}")
    # 작업 번호를 함께 — 미리 본 계획을 그 플랫폼 화면 「작업」 에서 열어 적용하는 길이다.
    return {**result, "job_id": str(job.get("id") or "")}


def _transmit(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    timeout: float | None = None,
) -> tuple[int, Any]:
    """**플랫폼 · 허브로 가는 요청이 모두 지나는 한 곳** — 주소를 마지막으로 보고(`check_url`)
    보낸다. `SEND` 를 직접 부르지 않는다(시험이 갈아 끼운 가짜도 이 검사를 거치게)."""
    check_url(url)
    if timeout is not None and SEND is _urllib_send:
        return _urllib_send(method, url, headers, body, timeout=timeout)
    return SEND(method, url, headers, body)


def _send(
    server: str,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    who: str,
) -> tuple[int, Any]:
    try:
        return _transmit(method, url, headers, body)
    except (urllib.error.URLError, OSError) as failure:
        # **닿지 않은 것과 거절당한 것을 가른다.** 트레이스백을 보여 주면 사람은 무엇을
        # 고칠지(주소 · 서버가 떠 있나 · 사내망) 모른다.
        reason = getattr(failure, "reason", failure)
        raise Stop(
            f"{who}에 닿지 않습니다: {server} ({reason}) — 주소와 서버가 떠 있는지 확인하세요"
        ) from failure


def _refused(who: str, status: int, answer: Any) -> None:
    error = answer.get("error", {}) if isinstance(answer, dict) else {}
    raise Stop(
        f"{who}이 거절했습니다 ({status}): [{error.get('code', '?')}] "
        f"{error.get('message', answer)}"
    )


def _wait_job(server: str, token: str, job: Any, *, who: str = "플랫폼") -> dict[str, Any]:
    """작업이 끝날 때까지 본다. 실패 · 취소면 그 이유로 멈춘다 — 「끝났다」 로 얼버무리지
    않는다."""
    if not isinstance(job, dict) or not job.get("id"):
        raise Stop(f"{who}이 작업을 돌려주지 않았습니다: {job!r}")
    job_id = job_uuid(job["id"])
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    url = api_url(server, api_path("api", "jobs", job_id))
    shown = ""
    queued_for = 0.0
    while True:
        try:
            status, current = _send(server, "GET", url, headers, None, who=who)
            if status != 200:
                _refused(who, status, current)
            if not isinstance(current, dict):
                raise Stop(f"{who} 응답을 읽을 수 없습니다: {current!r}")
        except Stop as failure:
            # **받은 작업의 상태를 못 물은 것은 거절이 아니다** — 작업은 플랫폼에 남아 돈다.
            raise Pending(
                f"{who}의 작업(<{job_id}>)이 끝났는지 묻지 못했습니다 — {failure}. 작업은 "
                f"{who}에 남아 있습니다(받아들여졌습니다).",
                job_id,
            ) from failure
        state = str(current.get("status"))
        progress = current.get("progress") or {}
        line = (
            f"{progress.get('stage', '')} {progress.get('done', 0)}/{progress.get('total', 0)}"
        )
        if line != shown and state == "running":
            print(f"  … {line}", file=sys.stderr)
            shown = line
        if state == "done":
            return current
        if state in ("failed", "cancelled"):
            raise Stop(
                f"{who}의 작업이 {'실패했습니다' if state == 'failed' else '취소됐습니다'}: "
                f"{current.get('error') or ''}"
            )
        queued_for = queued_for + POLL_SECONDS if state == "queued" else 0.0
        if queued_for >= QUEUED_LIMIT_SECONDS:
            raise Pending(
                f"{who}의 작업(<{job_id}>)을 {int(queued_for)}초 동안 아무도 집어 가지 "
                "않았습니다 — 작업 워커가 다른 일(밤 계산 등)로 바쁘거나 꺼져 있습니다. "
                "오래 그대로면 운영자에게 'systemctl status <slug>-worker' 를 확인해 달라고 "
                "하세요. 작업은 서버에 남아 있으니 워커가 집으면 그대로 이어집니다.",
                job_id,
            )
        WAIT(POLL_SECONDS)


def _get_json(
    server: str,
    token: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    who: str = "허브",
) -> Any:
    """`path` 는 `api_path(…)`(또는 값이 없는 고정 경로), 질의는 `params` 로."""
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    status, answer = _send(
        server, "GET", api_url(server, path, params), headers, None, who=who
    )
    if status != 200:
        _refused(who, status, answer)
    return answer


#: 상태를 **묻기만** 할 때 기다리는 시간 — 적재(600초)만큼 매달리면 작업 상태 하나 보는 데 몇
#: 분이 걸린다. 못 닿으면 「모른다」 로 넘어간다.
PROBE_SECONDS = 5.0


def _probe_job(server: str, token: str, job_id: str) -> tuple[int, Any] | None:
    """작업 하나를 **묻기만** — 짧게 기다리고, 못 닿으면(포털의 로그인 화면 포함) None."""
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    try:
        url = api_url(server, api_path("api", "jobs", job_uuid(job_id)))
        return _transmit("GET", url, headers, None, timeout=PROBE_SECONDS)
    except (urllib.error.URLError, OSError, ValueError, Stop):
        return None


def plan_apply_job(server: str, token: str, job_id: str) -> str | None:
    """그 계획을 **적용한(또는 적용 중인) 작업**의 번호 — 플랫폼의 `applied_by`. 없으면 빈 값,
    못 닿으면 None.

    키트가 미리 본 계획을 사람은 그 플랫폼 화면 「작업」 에서 적용한다. 키트는 그것을
    모르므로, 안 물으면 작업 상태가 계속 「적용 전 — 적용하라」 고 조르고, 사람은 같은 것을
    또 넣으려 한다. **번호만으로 「적용함」 이 아니다** — 플랫폼은 대기 · 도는 중인 적용도
    `applied_by` 로 센다(`counts_as_apply`). 끝났는지는 `apply_job_state` 로 그 작업에 묻는다.
    """
    seen = _probe_job(server, token, job_id)
    if seen is None or seen[0] != 200 or not isinstance(seen[1], dict):
        return None
    found = seen[1].get("applied_by")
    return job_uuid(found) if found else ""


def _hidden_apply(
    server: str, token: str, plan_job_id: str, apply_job_id: str
) -> dict[str, Any]:
    """이 토큰에 **안 보이는 적용**(시스템 관리자가 남의 전역 계획을 화면에서 적용함 — 그
    적용은 관리자의 것이다) — 계획 작업이 싣는 `applied_by` · `applied_status` 로 가른다.

    전에는 안 보이면 곧장 「적용함」 으로 쳐, 대기 중이던 그 적용이 뒤에 실패해도 실행이
    영영 잠겼다(0.4.107 재점검). `applied_status` 가 없는 옛 플랫폼만 예전처럼 「적용함」
    으로 둔다."""
    seen = _probe_job(server, token, plan_job_id)
    if seen is not None and seen[0] == 404:
        return {
            "state": "failed",
            "result": {},
            "message": f"적용 작업({apply_job_id})도 그 계획도 이 플랫폼에 없습니다 — 다른 "
            "플랫폼이거나 지워졌습니다",
        }
    if seen is None or seen[0] != 200 or not isinstance(seen[1], dict):
        return {"state": "unknown", "result": {}, "message": "플랫폼에 닿지 않습니다"}
    plan = seen[1]
    found = str(plan.get("applied_by") or "").lower()
    if found != apply_job_id.lower():
        # 플랫폼은 실패 · 취소된 적용을 `applied_by` 에서 거둔다.
        return {
            "state": "failed",
            "result": {},
            "message": "다른 사람이 넣은 적용이 들어가지 않았습니다(실패 · 취소) — 다시 미리 "
            "본 뒤 적용할 수 있습니다",
        }
    status = plan.get("applied_status")
    if status in ("queued", "running"):
        return {"state": "waiting", "status": status, "result": {}, "message": ""}
    return {
        "state": "applied",
        "status": status or "",
        "result": {"applied": True},
        "message": "다른 사람이 넣은 적용이라 이 토큰으로는 그 작업이 안 보입니다 — 계획 "
        "작업이 끝났다고 알린다",
    }


def apply_job_state(
    server: str, token: str, apply_job_id: str, *, plan_job_id: str | None = None
) -> dict[str, Any]:
    """적용 작업 하나가 **어떻게 됐나** — `state`: `applied`(끝나 들어감) · `failed`(실패 ·
    취소 · 끝났는데 안 들어감 · 플랫폼에 그 작업이 없음) · `waiting`(대기 · 도는 중) ·
    `unknown`(못 닿음). `result` 는 끝난 작업의 결과.

    `plan_job_id`(그 적용의 계획 작업)를 주면, 적용 작업이 이 토큰에 안 보일 때(화면에서 남이
    넣은 적용) 계획 작업이 싣는 상태로 가른다(`_hidden_apply`)."""
    seen = _probe_job(server, token, apply_job_id)
    if seen is None:
        return {"state": "unknown", "result": {}, "message": "플랫폼에 닿지 않습니다"}
    status, job = seen
    if status == 404 and plan_job_id:
        return _hidden_apply(server, token, plan_job_id, apply_job_id)
    if status == 404:
        return {
            "state": "failed",
            "result": {},
            "message": f"적용 작업({apply_job_id})이 플랫폼에 없습니다 — 지워졌거나 다른 "
            "플랫폼입니다",
        }
    if status != 200 or not isinstance(job, dict):
        return {
            "state": "unknown",
            "result": {},
            "message": f"플랫폼이 답하지 않습니다({status})",
        }
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    state = str(job.get("status") or "")
    if state in ("queued", "running"):
        return {"state": "waiting", "status": state, "result": {}, "message": ""}
    if state == "done" and result.get("applied") is not False:
        return {"state": "applied", "status": state, "result": result, "message": ""}
    if state == "done":
        return {
            "state": "failed",
            "status": state,
            "result": result,
            "message": "적용 작업이 끝났는데 들어가지 않았습니다(적용 때 다시 판정한 계획에 "
            "오류)",
        }
    return {
        "state": "failed",
        "status": state,
        "result": result,
        "message": f"적용 작업이 {'실패했습니다' if state == 'failed' else '취소됐습니다'}: "
        f"{job.get('error') or ''}",
    }


def waiting_message(server: str, apply_job_id: str, why: str = "") -> str:
    """대기 중인 적용을 사람에게 — **받아들여졌고 거절된 것이 아니다**, 또 넣지 않는다."""
    return (
        f"적용을 넣었고 플랫폼이 받았는데 아직 끝나지 않았습니다(적용 작업 {apply_job_id}). "
        "거절된 것이 아닙니다 — 워커가 집으면 그대로 적용됩니다. **다시 적용하지 않는다**"
        "(플랫폼이 같은 계획을 두 번 넣지 않는다). 끝났는지는 work_status(문서 올리기면 "
        f"document_apply 를 다시)로, 또는 그 플랫폼 화면 「작업」 에서 본다: "
        f"{jobs_link(server, apply_job_id)}" + (f" — {why}" if why else "")
    )


def apply_plan(server: str, token: str, job_id: str) -> dict[str, Any]:
    """플랫폼의 **그 계획 작업**을 적용한다 — 화면의 「적용」 과 같은 길(같은 파일 · 같은 지문,
    계획을 세운 토큰의 주인으로). 사람이 대화에서 계획을 보고 「좋다」 한 뒤에만 부른다.

    돌려주는 `state`: `applied`(이번에 적용함) · `already`(화면 등에서 먼저 적용해 끝남 — 그
    적용 작업의 결과를 싣는다, JOBS-22) · `waiting`(**받아들여졌는데 아직 안 끝남** — 대기열 ·
    도는 중, 또는 기다리는 사이 상태를 못 물음. 먼저 넣은 적용이 아직 대기 중인 `already` 도
    이것) · `refused`(플랫폼이 받지 않음 — 오류가 있는 계획 · 권한 · 남의 계획) ·
    `failed`(적용하다 멈춤 — 그 사이 누가 바꿔 지문이 다름 · 끝났는데 다시 판정한 계획에 오류가
    있어 안 들어감 등). 멈추지 않고 돌려준다 — 여러 작업을 한 번에 적용하는 쪽이 줄마다
    가른다.
    """
    job_id = job_uuid(job_id, "계획 작업의 번호")
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    url = api_url(server, api_path("api", "jobs", job_id, "apply"))
    status, answer = _send(server, "POST", url, headers, None, who="플랫폼")
    if status != 202:
        error = answer.get("error", {}) if isinstance(answer, dict) else {}
        applied_by = str((error.get("details") or {}).get("applied_by") or "")
        if applied_by:
            earlier_id = job_uuid(applied_by)
            # **대기 · 도는 중인 적용도 `already` 로 온다**(`counts_as_apply`) — 그것을
            # 「적용함」 으로 적으면, 그 적용이 나중에 실패해도 실행이 영영 잠긴다
            # (0.4.107 점검).
            earlier = apply_job_state(server, token, earlier_id, plan_job_id=job_id)
            if earlier["state"] == "applied":
                return {
                    "state": "already",
                    "apply_job_id": earlier_id,
                    "status": earlier.get("status", ""),
                    "result": earlier["result"],
                }
            if earlier["state"] == "failed":
                return {
                    "state": "failed",
                    "apply_job_id": earlier_id,
                    "message": earlier["message"],
                    "result": earlier["result"],
                }
            return {
                "state": "waiting",
                "apply_job_id": earlier_id,
                "message": waiting_message(server, earlier_id, earlier["message"]),
                "result": {},
            }
        return {
            "state": "refused",
            "message": f"[{error.get('code', status)}] {error.get('message', answer)}",
            "result": {},
        }
    accepted = job_uuid((answer or {}).get("id") if isinstance(answer, dict) else "")
    try:
        finished = _wait_job(server, token, answer)
    except Pending as pending:
        # **받아들여졌는데 끝을 못 봤다** — 대기열(워커가 밤 계산으로 바쁨)이거나 기다리는
        # 사이 못 닿았다. 거절로 적으면 사람은 다시 미리 보고 또 넣으려 하는데, 그사이 앞의
        # 적용이 돌아 들어간다(0.4.107 점검).
        return {
            "state": "waiting",
            "apply_job_id": accepted,
            "message": waiting_message(server, accepted, str(pending)),
            "result": {},
        }
    except Stop as failure:
        return {
            "state": "failed",
            "apply_job_id": accepted,
            "message": str(failure),
            "result": {},
        }
    result = finished.get("result")
    result = result if isinstance(result, dict) else {}
    if result.get("applied") is False:
        # 끝났는데 들어가지 않았다 — 적용 때 다시 판정한 계획에 오류(전부 아니면 무).
        # 「적용함」 이라 말하지 않는다.
        return {
            "state": "failed",
            "apply_job_id": accepted,
            "message": "적용 때 다시 판정한 계획에 오류가 있어 들어가지 않았습니다 — 아무것도 "
            "바뀌지 않았습니다",
            "result": result,
        }
    return {"state": "applied", "apply_job_id": accepted, "result": result}


def jobs_link(server: str, job_id: str) -> str:
    """그 플랫폼 화면의 「작업」 — **이 계획을 펼친 채로** 연다(`?job=`).

    적용은 사람이 거기서 「적용」 을 누른다. 명령 창보다 쉽고, **그 플랫폼의 화면에서** 누르니
    엉뚱한 곳에 넣을 일이 없다. 계획을 본 사람(이 토큰의 주인)만 적용할 수 있다.
    """
    return api_url(server, "/jobs", {"job": job_id})


def command_line(*args: str) -> str:
    """사람이 명령 창에 붙여 넣는 이 도구의 명령 — **지금 이것을 돌리는 파이썬으로.**

    PC 에 파이썬이 없을 수 있다 — Windows 키트는 제 파이썬(`python/`)을 들고 다니고 PATH 에
    안 올린다. `python …` 으로 주면 그 PC 에서는 「python 을 찾을 수 없다」 로 멈춘다.
    `-X utf8` 은 MCP 서버의 PYTHONUTF8 과 같은 것 — 명령 창에는 그 환경 변수가 없다.
    """
    return f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" ' + " ".join(args)


def platform_schema(server: str, token: str, types: list[str] | None = None) -> dict[str, Any]:
    """플랫폼의 **지금 정의** — 정의 초안을 잡기 전에 읽는다(무엇이 이미 있나).

    **키트가 직접 읽는다** — 그래서 정제만 하는 사람은 서버 MCP 를 따로 붙이지 않아도 된다
    (Node.js · 설정 JSON 손편집이 빠진다). `types` 를 주면 그 타입들과, 그 타입에 닿는 관계
    종류만 — 타입이 백 개면 전부는 길다.
    """
    schema = _get_json(server, token, "/api/ontology/schema", who="플랫폼")
    if not isinstance(schema, dict) or not types:
        return schema if isinstance(schema, dict) else {}
    wanted = set(types)
    picked = [one for one in schema.get("types") or [] if one.get("slug") in wanted]
    missing = sorted(wanted - {str(one.get("slug")) for one in picked})
    relations = [
        one
        for one in schema.get("relation_types") or []
        if wanted & set(one.get("src_type_slugs") or [])
        | wanted & set(one.get("dst_type_slugs") or [])
    ]
    out = {
        "groups": schema.get("groups") or [],
        "types": picked,
        "relation_types": relations,
        "all_type_slugs": [one.get("slug") for one in schema.get("types") or []],
    }
    if missing:
        out["missing"] = missing
    return out


def _export_bundle(hub: str, hub_token: str, groups: list[str]) -> Any:
    """허브의 내보내기도 작업이다 — 넣고, 기다리고, 결과 파일을 받는다.

    **묶음을 여럿 받는다.** 코어를 축별로 나눠 둔 설치에서 하나씩 받으면, 축끼리 가리키는
    참조 때문에 어느 쪽도 못 내보낸다(허브가 거절한다).
    """
    headers = {
        "Authorization": f"Bearer {hub_token}",
        "Content-Type": "application/json",
        "X-Client": CLIENT,
    }
    raw = json.dumps({"groups": groups}, ensure_ascii=False).encode("utf-8")
    status, answer = _send(
        hub, "POST", api_url(hub, "/api/bundles/export"), headers, raw, who="허브"
    )
    if status != 202:
        _refused("허브", status, answer)
    job = _wait_job(hub, hub_token, answer, who="허브")
    return _get_json(hub, hub_token, api_path("api", "jobs", job_uuid(job["id"]), "download"))


def resolve_names(
    server: str, token: str, type_slug: str, names: list[str]
) -> dict[str, dict[str, Any]] | None:
    """이름 여럿을 **플랫폼의 판정으로** 푼다 — `POST /objects/{타입}/resolve-many`.

    도구가 스스로 맞추지 않는 이유: 식별자 → 별칭 → 이름 순서와 겹침 규칙은 플랫폼의 것이고,
    두 벌로 두면 「도구는 된다는데 넣으면 안 되는」 상태가 생긴다.

    slug 꼴이 아닌 타입은 **묻지 않고** None(플랫폼에 그런 타입은 있을 수 없다 — 404 와 같다)
    — 그 값으로 주소를 만들면 다른 길(`x/../../jobs/<번호>/apply#`)이 된다.
    """
    if not isinstance(type_slug, str) or not SLUG_RE.fullmatch(type_slug):
        return None
    out: dict[str, dict[str, Any]] = {}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Client": CLIENT,
    }
    unique = list(dict.fromkeys(one for one in names if one))
    url = api_url(server, api_path("api", "objects", type_slug, "resolve-many"))
    for at in range(0, len(unique), 500):
        raw = json.dumps({"names": unique[at : at + 500]}, ensure_ascii=False).encode("utf-8")
        status, body = _send(server, "POST", url, headers, raw, who="플랫폼")
        if status == 404:
            # **그 타입이 플랫폼에 아직 없다.** 같은 묶음의 정의가 만들 것일 수 있으므로
            # 여기서 멈추지 않는다 — 부르는 쪽이 판단한다.
            return None
        if status != 200:
            _refused("플랫폼", status, body)
        for one in (body or {}).get("items") or []:
            out[str(one.get("name"))] = one
    return out


def fetch_keys(server: str, token: str, type_slug: str) -> set[str]:
    """플랫폼에서 그 타입의 식별자를 전부 받는다(쪽마다) — 원천의 코드가 코어에 붙나를
    볼 때."""
    require_slug(type_slug, "코어 대조의 타입")
    keys: set[str] = set()
    offset = 0
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    while True:
        url = api_url(
            server, api_path("api", "objects", type_slug), {"limit": 500, "offset": offset}
        )
        try:
            status, body = _transmit("GET", url, headers, None)
        except (urllib.error.URLError, OSError) as failure:
            reason = getattr(failure, "reason", failure)
            raise Stop(
                f"플랫폼에 닿지 않습니다: {server} ({reason}) — "
                "주소(SP_SERVER)와 서버가 떠 있는지 확인하세요"
            ) from failure
        if status != 200 or not isinstance(body, dict):
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise Stop(
                f"코어 식별자를 받지 못했습니다({type_slug}, {status}): "
                f"{error.get('message', body)}"
            )
        items = body.get("items") or []
        keys.update(str(one["key"]) for one in items if one.get("key"))
        offset += len(items)
        if not items or offset >= int(body.get("total") or 0):
            return keys


def read_keys_file(path: Path) -> set[str]:
    """식별자 파일 — 한 줄에 하나. 허브에 닿지 않는 PC 에서 코어 식별자를 옮겨 올 때."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as failure:
        raise Stop(f"식별자 파일을 읽을 수 없습니다: {path} ({failure})") from failure
    return {line.strip() for line in lines if line.strip()}


def summarize(result: dict[str, Any], *, limit: int = 20) -> str:
    """사람이 읽는 요약 — 오류는 **행 번호와 이유**까지."""
    lines: list[str] = []
    state = (
        "적용함" if result.get("applied") else ("괜찮음" if result.get("ok") else "오류 있음")
    )
    lines.append(f"결과: {state}")
    if result.get("preview") == "plan" and not result.get("applied"):
        # **가벼운 미리 보기(백필)에는 지문이 없다** — 적용이 그 시점에 다시 판정하므로, 그
        # 사이 바뀐 것(지워진 참조 대상 등)을 플랫폼이 「미리 본 것과 다름」 으로 거절하지
        # 않는다.
        lines.append(
            "가벼운 미리 보기(백필) — 지문이 없어 적용할 때 다시 판정합니다: 그 사이 바뀐 "
            "것을 거절하지 않고 그때의 판정대로 넣습니다(전부 아니면 무는 그대로, 못 찾은 "
            "참조는 missing_refs 대로). 사람에게 이것도 함께 말한다"
        )
    ontology = result.get("ontology")
    if ontology:
        changed = [c for c in ontology.get("changes", []) if c.get("action") != "unchanged"]
        lines.append(f"정의: 바뀌는 것 {len(changed)}건")
        for change in changed[:limit]:
            lines.append(f"  {change.get('action')} {change.get('kind')} {change.get('slug')}")
        for warning in ontology.get("warnings", []):
            lines.append(f"  경고: {warning}")
        for error in ontology.get("errors", []):
            lines.append(f"  오류: {error}")
    for name, label in (("objects", "객체"), ("relations", "관계")):
        for batch in result.get(name, []):
            plan = batch.get("plan") or {}
            counts = " · ".join(
                f"{k} {v}" for k, v in sorted((plan.get("counts") or {}).items())
            )
            lines.append(f"{label} {batch.get('type_slug')}: {counts or '-'}")
            if batch.get("error"):
                lines.append(f"  오류: {batch['error']}")
            for error in plan.get("errors", []):
                lines.append(f"  오류: {error}")
            bad = [row for row in plan.get("rows", []) if row.get("action") == "error"]
            for row in bad[:limit]:
                lines.append(
                    f"  {row.get('row')}행 {row.get('label') or ''}: {row.get('message')}"
                )
            if len(bad) > limit:
                lines.append(f"  … 외 {len(bad) - limit}행")
            # **끊을 선은 따로 센다** — 수에만 섞어 두면 「무엇이 끊기나」 를 보려고 사람이
            # 표를 펴야 하는데, 그 자리에서 사람이 보는 것은 이 요약뿐이다.
            cut = [row for row in plan.get("rows", []) if row.get("action") == "unlink"]
            for row in cut[:limit]:
                lines.append(f"  끊음: {row.get('label') or ''} — {row.get('message') or ''}")
            if len(cut) > limit:
                lines.append(f"  … 외 {len(cut) - limit}줄 끊음")
            # 오류가 아닌 **말**(별칭이 빠졌다 · 같은 파일 앞줄이 먼저 쓴다 …)도 보인다.
            notes = [
                row
                for row in plan.get("rows", [])
                if row.get("message") and row.get("action") not in ("error", "unlink")
            ]
            for row in notes[:limit]:
                where = f"{row.get('row')}행 {row.get('label') or ''}"
                lines.append(f"  알림: {where}: {row.get('message')}")
            if len(notes) > limit:
                lines.append(f"  … 외 {len(notes) - limit}행 알림")
    graves = result.get("tombstones") or {}
    if graves.get("rows"):
        counts = " · ".join(
            f"{k} {v}" for k, v in sorted((graves.get("counts") or {}).items())
        )
        lines.append(f"사라진 것: {counts or '-'}")
        for row in graves["rows"][:limit]:
            lines.append(
                f"  {row.get('action')} {row.get('label') or ''}: {row.get('message') or ''}"
            )
        if len(graves["rows"]) > limit:
            lines.append(f"  … 외 {len(graves['rows']) - limit}줄")
    for error in result.get("errors", []):
        lines.append(f"오류: {error}")
    return "\n".join(lines)


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write(path: Path, body: Any) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# 명령
# --------------------------------------------------------------------------


def cmd_init(path: Path, *, title: str = "", backfill: bool = False) -> str:
    if (path / MANIFEST).exists():
        raise Stop(f"이미 실행 폴더입니다: {path} — 새 실행은 새 폴더로")
    (path / OBJECTS_DIR).mkdir(parents=True, exist_ok=True)
    (path / RELATIONS_DIR).mkdir(parents=True, exist_ok=True)
    _write(
        path / MANIFEST,
        {
            "format": FORMAT,
            "title": title,
            "created_at": _stamp(),
            "sources": [],
            "objects_order": [],
            "relations_order": [],
            "notes": "",
            # **대량 적재인가.** 켜면 넷이 함께 켜진다(`BACKFILL`) — 미리 보기는 계획만,
            # 웹훅 · 감사는 묶음 한 건, 못 찾은 참조는 그 칸만 비운다.
            "backfill": backfill,
        },
    )
    _write(path / ONTOLOGY, {"groups": [], "types": [], "relation_types": []})
    _write(path / UNRESOLVED, [])
    return f"실행 폴더를 만들었습니다: {path}"


def cmd_pull(
    path: Path,
    *,
    hub: str,
    hub_token: str,
    group: str | list[str],
    source: str = "hub",
) -> str:
    """허브가 내보낸 묶음을 **새 실행 폴더로** 받는다(사이드바 묶음 여럿도 한 번에).

    받은 것도 여느 실행과 같다 — `validate` → `preview`(받는 플랫폼) → 사람이 확인 → `apply`.
    `bundle.json` 의 `source` 가 실려 가므로, 받는 플랫폼은 그 타입들을 허브 관리로 둔다.

    **허브가 적어 보낸 것을 떨어뜨리지 않는다** — 별칭·관계의 「맞춤」(`aliases_mode` ·
    `mode`)과 사라진 것(`tombstones`). 떨어뜨리면 허브에서 뺀 별칭 · 끊은 선 · 지운 객체가
    받는 쪽에 남고, 두 설치는 그때부터 조용히 갈린다.
    """
    wanted = [group] if isinstance(group, str) else list(group)
    groups = [one.strip() for one in wanted if one and one.strip()]
    if not groups:
        raise Stop("받을 사이드바 묶음(--group)이 필요합니다 — PLM 기준정보면 plm")
    body = _export_bundle(hub, hub_token, groups)
    if not isinstance(body, dict) or body.get("format") != FORMAT:
        raise Stop(f"허브의 응답이 묶음이 아닙니다: {str(body)[:200]}")
    # 파일 이름이 허브가 보낸 slug 에서 나온다 — slug 꼴만(밖의 경로를 가리키지 못하게). 실행
    # 폴더를 만들기 전에 본다(반쯤 쓴 폴더를 남기지 않는다).
    files: list[tuple[str, str, Any]] = []
    for folder, listed in (
        (OBJECTS_DIR, body.get("objects") or []),
        (RELATIONS_DIR, body.get("relations") or []),
    ):
        for index, one in enumerate(listed, start=1):
            slug = require_slug(one.get("type_slug"), "허브가 보낸 묶음의 타입")
            files.append((folder, f"{index:03d}-{slug}", one))
    cmd_init(path, title=f"허브에서 받기 — {', '.join(groups)}")
    manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    manifest["source"] = source
    manifest["sources"] = [
        {
            "name": hub,
            "group": ",".join(groups),
            "exported_at": body.get("exported_at"),
            "counts": body.get("counts") or {},
        }
    ]
    batches = body.get("objects") or []
    manifest["objects_order"] = list(dict.fromkeys(str(one["type_slug"]) for one in batches))
    manifest["notes"] = "\n".join(str(one) for one in body.get("warnings") or [])
    _write(path / MANIFEST, manifest)
    _write(path / ONTOLOGY, body.get("ontology") or {})
    for folder, name, batch in files:
        _write(path / folder / f"{name}.json", batch)
    graves = body.get("tombstones") or {}
    if graves.get("objects") or graves.get("relations"):
        _write(path / TOMBSTONES, graves)
    counts = body.get("counts") or {}
    lines = [
        f"받았습니다: {path}",
        f"허브 {hub} · 묶음 {', '.join(groups)} · 내보낸 때 {body.get('exported_at')}",
        (
            "타입 {types} · 관계 종류 {relation_types} · 객체 {objects} · 관계 {relations}"
            " · 사라진 것 {tombstones}"
        ).format(
            **{
                key: counts.get(key, 0)
                for key in ("types", "relation_types", "objects", "relations", "tombstones")
            }
        ),
        *[f"경고: {one}" for one in body.get("warnings") or []],
        f"다음: python sp_pipeline.py validate {path}"
        " → preview → 확인 → apply (받는 플랫폼에)",
    ]
    return "\n".join(lines)


def check_endpoints(run: Run, server: str, token: str) -> Report:
    """**끝점이 풀리나** — 플랫폼에 물어 미리 본다(검증의 선택 단계).

    예전에는 이것을 `preview` 에서야 알았다. 미리 보기는 계획을 세우느라 몇 분이 걸리고,
    그 몇 분 뒤에 「이름이 여럿과 맞는다」 를 듣는다. 물어서 아는 것은 먼저 묻는다.

    보는 것 넷:
    - 관계의 출발점(`src`)이 그 타입에서 **하나로 정해지나**
    - 관계의 **도착점(`dst`)** 도 — 관계 종류가 허락한 타입에서(여럿이면 그 중 하나에서).
      예전에는 출발점만 봐서, 도착점이 없는 파일이 미리 보기까지 가서야 막혔다
    - 객체 행이 가리키는 참조 칸(실행 폴더의 `ontology.json` 이 말하는 `object_ref`)이 풀리나
    - **별칭으로 풀린 것**은 경고로 — 다른 객체의 이름과 같은 별칭이 있으면 조용히 그쪽에
      붙는다(그 사실은 아무 데도 안 적힌다)
    """
    report = Report()
    # 이 실행의 정의가 만드는 타입 · 인터페이스 — 플랫폼에 아직 없어도 된다.
    defined: set[str] = {
        str(one.get("slug"))
        for key in ("types", "interfaces")
        for one in (((run.ontology or {}).get(key) or []) if run.ontology else [])
        if isinstance(one, dict)
    }
    ref_of: dict[str, dict[str, str]] = {}
    for one in ((run.ontology or {}).get("types") or []) if run.ontology else []:
        if not isinstance(one, dict):
            continue
        fields = {
            str(prop.get("key")): str(prop.get("ref_type_slug") or "")
            for prop in (one.get("properties") or [])
            if isinstance(prop, dict) and prop.get("data_type") == "object_ref"
        }
        if fields:
            ref_of[str(one.get("slug"))] = fields

    # 관계 종류 → 도착 타입들. 실행 폴더의 정의가 먼저고, 없으면 플랫폼에 묻는다.
    dst_types: dict[str, list[str]] = {
        str(one.get("slug")): [str(x) for x in (one.get("dst_type_slugs") or [])]
        for one in (((run.ontology or {}).get("relation_types") or []) if run.ontology else [])
        if isinstance(one, dict)
    }
    missing_kinds = {
        str(row.get("relation"))
        for batch in run.relations
        for row in batch.rows
        if isinstance(row, dict)
        and row.get("relation")
        and row.get("relation") not in dst_types
    }
    if missing_kinds:
        for one in _get_json(server, token, "/api/ontology/relation-types") or []:
            if isinstance(one, dict) and str(one.get("slug")) in missing_kinds:
                dst_types[str(one["slug"])] = [
                    str(x) for x in (one.get("dst_type_slugs") or [])
                ]

    asked: dict[str, set[str]] = {}
    # 도착점은 **여러 타입 중 하나**에 있으면 된다 — 그 판정은 아래에서 따로 한다.
    either: list[tuple[str, list[str]]] = []
    for batch in run.relations:
        for row in batch.rows:
            if not isinstance(row, dict):
                continue
            if row.get("src"):
                asked.setdefault(batch.type_slug, set()).add(str(row["src"]))
            ends = dst_types.get(str(row.get("relation") or ""))
            if row.get("dst") and ends:
                if len(ends) == 1:
                    asked.setdefault(ends[0], set()).add(str(row["dst"]))
                else:
                    either.append((str(row["dst"]), ends))
    for batch in run.objects:
        for key, target in (ref_of.get(batch.type_slug) or {}).items():
            if not target:
                continue
            for row in batch.rows:
                if not isinstance(row, dict):
                    continue
                value = row.get(key)
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str) and item.strip():
                        asked.setdefault(target, set()).add(item.strip())
    if not asked:
        return report

    # **이 실행이 만드는 것은 아직 없어도 된다** — 같은 묶음의 객체 단계가 넣는다.
    making: dict[str, set[str]] = {}
    for batch in run.objects:
        for row in batch.rows:
            if not isinstance(row, dict):
                continue
            for name in ("key", "label"):
                if row.get(name):
                    making.setdefault(batch.type_slug, set()).add(str(row[name]))

    for text, ends in either:
        for one in ends:
            asked.setdefault(one, set()).add(text)

    # 끝에 **인터페이스**가 적혔으면 그것을 구현한 타입이 만드는 것도 「이 실행이 만드는 것」
    # 이다. 구현은 실행 폴더의 정의가 먼저고, 플랫폼에 이미 있는 것은 물어서 안다.
    members: dict[str, set[str]] = {}
    for one in ((run.ontology or {}).get("types") or []) if run.ontology else []:
        if isinstance(one, dict):
            for name in one.get("interface_slugs") or []:
                members.setdefault(str(name), set()).add(str(one.get("slug")))
    if set(asked) - defined - {batch.type_slug for batch in run.objects}:
        with contextlib.suppress(Stop):
            for one in _get_json(server, token, "/api/ontology/interfaces") or []:
                if isinstance(one, dict):
                    members.setdefault(str(one.get("slug")), set()).update(
                        str(x) for x in one.get("implementers") or []
                    )

    settled: dict[str, set[str]] = {}
    for type_slug, names in sorted(asked.items()):
        made_here = set(making.get(type_slug) or set())
        for member in members.get(type_slug) or ():
            made_here |= making.get(member) or set()
        left = sorted(names - made_here)
        if not left:
            continue
        found = resolve_names(server, token, type_slug, left)
        if found is None:
            where = report.warnings if type_slug in defined else report.errors
            where.append(
                f"{type_slug}: 이 타입이 플랫폼에 아직 없어 끝점을 못 봤습니다 "
                f"({len(left)}건) — 이 묶음의 정의가 만드는 타입이면 정상입니다"
            )
            continue
        missing = [one for one in left if (found.get(one) or {}).get("match") == "none"]
        several = [one for one in left if (found.get(one) or {}).get("match") == "candidates"]
        by_alias = [
            one
            for one in left
            if ((found.get(one) or {}).get("object") or {}).get("matched_by") == "alias"
        ]
        settled[type_slug] = {
            one for one in left if (found.get(one) or {}).get("match") == "exact"
        }
        # 도착 타입이 여럿인 관계는 **그 중 하나**에서 풀리면 된다 — 나머지에서 「없다」 가
        # 나오는 것은 당연하므로 여기서 빼고 아래에서 함께 본다.
        forgiving = {one for one, ends in either if type_slug in ends}
        missing = [one for one in missing if one not in forgiving]
        several = [one for one in several if one not in forgiving]
        if missing:
            report.errors.append(
                f"{type_slug}: 가리키는 것이 플랫폼에 없습니다 {len(missing)}건 — "
                + ", ".join(missing[:10])
                + (" …" if len(missing) > 10 else "")
            )
        if several:
            report.errors.append(
                f"{type_slug}: 이름이 여럿과 맞습니다 {len(several)}건(그때 플랫폼은 "
                "고르지 않는다) — " + ", ".join(several[:10])
            )
        if by_alias:
            report.warnings.append(
                f"{type_slug}: 별칭으로 풀린 것 {len(by_alias)}건 — 다른 객체의 이름과 같은 "
                "별칭이면 그쪽에 붙습니다: " + ", ".join(by_alias[:10])
            )

    # 도착 타입이 여럿인 줄 — **어느 타입에서도** 안 풀린 것만 오류다.
    homeless = sorted(
        {
            text
            for text, ends in either
            if not any(
                text in settled.get(one, set()) or text in (making.get(one) or set())
                for one in ends
            )
        }
    )
    if homeless:
        report.errors.append(
            f"관계의 도착점을 플랫폼에서 찾지 못했습니다 {len(homeless)}건 — "
            + ", ".join(homeless[:10])
            + (" …" if len(homeless) > 10 else "")
        )
    return report


def known_on(server: str, token: str) -> KnownKeys:
    """플랫폼에 이미 있는 식별자를 묻는 검사의 눈 — 검사 · 미리 보기 · 적용이 **같은 것**을
    쓴다. 검사만 묻고 미리 보기가 안 물으면, 검사를 통과한 고치기 행(이름 없이 식별자만)이
    미리 보기에서 「이름이 빈 행」 으로 막힌다(0.4.106)."""

    def known(type_slug: str, keys: list[str]) -> set[str]:
        found = resolve_names(server, token, type_slug, keys) or {}
        return {
            name
            for name, one in found.items()
            if one.get("match") == "exact" and (one.get("object") or {}).get("key") == name
        }

    return known


def cmd_validate(
    path: Path,
    *,
    allow_unresolved: bool = False,
    server: str = "",
    token: str = "",
) -> tuple[bool, str]:
    run = load(path)
    report = validate(
        run,
        allow_unresolved=allow_unresolved,
        known=known_on(server, token) if server and token else None,
    )
    asked = ""
    if server and token:
        remote = check_endpoints(run, server, token)
        report.errors.extend(remote.errors)
        report.warnings.extend(remote.warnings)
        asked = " · 끝점 확인함"
    lines = [f"오류: {one}" for one in report.errors] + [
        f"경고: {one}" for one in report.warnings
    ]
    total = sum(len(one.rows) for one in run.objects)
    links = sum(len(one.rows) for one in run.relations)
    head = (
        f"정의 {'있음' if run.ontology else '없음'} · 객체 {total}행({len(run.objects)}파일)"
        f" · 관계 {links}줄{asked} · 오류 {len(report.errors)} · 경고 {len(report.warnings)}"
    )
    return not report.errors, "\n".join([head, *lines])


def settle_pending(
    path: Path, server: str, token: str, *, moved: bool = False
) -> dict[str, Any] | None:
    """넣었는데 끝을 못 본 적용(`applying.json`)을 **다시 묻는다** — 끝났으면 `applied.json`,
    실패했으면 `apply_failed.json` 으로 옮긴다. 기록이 없으면 None, 있으면 `state`(`applied` ·
    `failed` · `waiting`)와 `apply_job_id` · `message` · `result`.

    `server` 는 그 적용을 넣은 곳이어야 한다 — 다르면 묻지 않고 대기로 둔다(다른 플랫폼의
    토큰으로 남의 작업을 묻지 않는다). `moved` 는 **같은 이름의 플랫폼이 주소만 바뀐 것**
    (`sp_work._settle`)이라 지금 주소로 묻는다."""
    pending = _read_json(path / APPLYING, [])
    if not isinstance(pending, dict):
        return None
    apply_id = str(pending.get("apply_job_id") or "")
    base = {key: pending.get(key) for key in ("at", "server", "digest", "job_id", "via")}
    if not moved and str(pending.get("server") or "").rstrip("/") != server.rstrip("/"):
        return {
            "state": "waiting",
            "apply_job_id": apply_id,
            "message": f"{pending.get('server')} 에 넣은 적용이 끝났는지 아직 모릅니다 — 그 "
            "플랫폼으로 work_status 를 봅니다",
            "result": {},
        }
    try:
        outcome = apply_job_state(
            server,
            token,
            job_uuid(apply_id),
            plan_job_id=str(pending.get("job_id") or "") or None,
        )
    except Stop:
        outcome = {
            "state": "failed",
            "result": {},
            "message": f"적용 작업의 번호가 틀립니다: {apply_id!r}",
        }
    if outcome["state"] == "applied":
        _write(
            path / APPLIED,
            base | {"at": _stamp(), "apply_job_id": apply_id, "result": outcome["result"]},
        )
        (path / APPLY_FAILED).unlink(missing_ok=True)
        (path / APPLYING).unlink(missing_ok=True)
    elif outcome["state"] == "failed":
        # **실패한 적용은 잠그지 않는다** — 플랫폼도 그 계획을 다시 적용할 수 있게 둔다.
        _write(
            path / APPLY_FAILED,
            base
            | {
                "at": _stamp(),
                "apply_job_id": apply_id,
                "result": outcome["result"],
                "message": outcome["message"],
            },
        )
        (path / APPLYING).unlink(missing_ok=True)
    else:
        outcome = {
            **outcome,
            "state": "waiting",
            "message": waiting_message(server, apply_id, outcome.get("message", "")),
        }
    return {**outcome, "apply_job_id": apply_id}


def preview_platform(run: Path) -> str:
    """미리 본 플랫폼의 이름 — **적용은 미리 본 곳으로만** 간다."""
    seen = _read_json(run / PREVIEW, [])
    return str(seen.get("platform") or "") if isinstance(seen, dict) else ""


def cmd_preview(
    path: Path, *, server: str, token: str, platform: str = ""
) -> tuple[bool, str]:
    run = load(path)
    report = validate(run, known=known_on(server, token))
    if report.errors:
        raise Stop(
            "검증을 먼저 통과하세요:\n" + "\n".join(f"오류: {e}" for e in report.errors)
        )
    body = payload(run)
    if (path / APPLIED).exists():
        raise Stop(
            "이미 적용한 실행입니다 — 다시 미리 보면 같은 묶음의 적용 링크가 또 생깁니다. "
            "바꿔 넣으려면 새 실행으로"
        )
    if (path / CLOSED).exists():
        raise Stop("닫은 실행입니다(run_close) — 새 실행으로")
    if (path / APPLYING).exists():
        pending = settle_pending(path, server, token)
        if pending and pending["state"] == "applied":
            raise Stop(
                "이미 적용한 실행입니다 — 넣은 적용이 그 사이 끝났습니다. 바꿔 넣으려면 "
                "새 실행으로"
            )
        if pending and pending["state"] == "waiting":
            raise Stop(
                "이 실행의 적용이 아직 플랫폼의 대기열에 있습니다 — 다시 미리 보지 않습니다. "
                + pending["message"]
            )
    result = _post_bundle(server, token, {**body, "apply": False})
    earlier = _read_json(path / PREVIEW, [])
    if isinstance(earlier, dict) and (earlier.get("result") or {}).get("job_id"):
        history = _read_json(path / PREVIEWS, [])
        history = history if isinstance(history, list) else []
        history.append(
            {key: earlier.get(key) for key in ("at", "server", "platform", "digest")}
            | {"job_id": (earlier.get("result") or {}).get("job_id")}
        )
        _write(path / PREVIEWS, history)
    _write(
        path / PREVIEW,
        {
            "at": _stamp(),
            "server": server,
            "platform": platform,
            "digest": digest(body),
            "result": result,
        },
    )
    return bool(result.get("ok")), summarize(result)


def cmd_apply(path: Path, *, server: str, token: str) -> tuple[bool, str]:
    run = load(path)
    report = validate(run, known=known_on(server, token))
    if report.errors:
        raise Stop(
            "검증을 먼저 통과하세요:\n" + "\n".join(f"오류: {e}" for e in report.errors)
        )
    body = payload(run)
    if (path / APPLIED).exists():
        raise Stop("이미 적용한 실행입니다 — 바꿔 넣으려면 새 실행으로")
    seen = _read_json(path / PREVIEW, [])
    # **미리 본 것만 넣는다.** 미리 본 뒤 파일을 고쳤으면 사람이 본 적 없는 것이 들어간다.
    if not isinstance(seen, dict):
        raise Stop("먼저 preview 로 미리 보고 확인하세요")
    if seen.get("digest") != digest(body):
        raise Stop("미리 본 뒤에 묶음이 바뀌었습니다 — preview 를 다시 하고 확인하세요")
    if seen.get("server") != server:
        raise Stop(
            f"미리 본 서버({seen.get('server')})와 다른 서버입니다 — preview 를 다시 하세요"
        )
    if not (seen.get("result") or {}).get("ok"):
        raise Stop("미리 보기에서 오류가 있었습니다 — 고치고 preview 를 다시 하세요")
    if (path / APPLYING).exists():
        pending = settle_pending(path, server, token)
        if pending and pending["state"] == "applied":
            raise Stop("이미 적용한 실행입니다 — 앞서 넣은 적용이 그 사이 끝났습니다")
        if pending and pending["state"] == "waiting":
            raise Stop(
                "앞서 넣은 적용이 아직 끝나지 않아 또 보내지 않습니다 — " + pending["message"]
            )
    try:
        result = _post_bundle(server, token, {**body, "apply": True})
    except Pending as pending:
        # 받아들여진 적용 — 또 보내면 같은 묶음이 두 번 든다. 번호를 두고 다음에 묻는다.
        if pending.job_id:
            _write(
                path / APPLYING,
                {
                    "at": _stamp(),
                    "server": server,
                    "digest": digest(body),
                    "job_id": "",
                    "apply_job_id": pending.job_id,
                    "via": "command",
                },
            )
            (path / APPLY_FAILED).unlink(missing_ok=True)
        raise Stop(waiting_message(server, pending.job_id, str(pending))) from pending
    record = {"at": _stamp(), "server": server, "digest": digest(body), "result": result}
    if result.get("applied"):
        _write(path / APPLIED, record)
        (path / APPLY_FAILED).unlink(missing_ok=True)
    else:
        # **적용이 안 됐으면 잠그지 않는다** — `applied.json` 이 있으면 실행이 「적용함」 이
        # 되어
        # 다시 미리 보지도 닫지도 못한다. 적용 때 다시 판정해 거절된 것(그새 지워진 참조 대상 ·
        # 권한)은 고친 뒤 다시 미리 보고 적용한다.
        _write(path / APPLY_FAILED, record)
    return bool(result.get("applied")), summarize(result)


def cmd_apply_plan(path: Path, *, server: str, token: str) -> dict[str, Any]:
    """미리 본 **그 계획**(`preview.json` 의 작업)을 적용한다 — 키트 MCP 의 `run_apply`.

    `cmd_apply`(명령 창)는 묶음을 다시 보내 새 작업으로 넣는다. 여기는 화면의 「적용」 과 같은
    길이다: 사람이 본 계획의 지문으로 넣고(그 사이 누가 바꿨으면 거절된다), 화면에서 먼저
    적용했으면 또 넣지 않는다. 미리 본 뒤 파일이 바뀌었거나 미리 보기에 오류가 있었으면 보내지
    않는다.
    """
    run = load(path)
    body = payload(run)
    if (path / APPLIED).exists():
        raise Stop("이미 적용한 실행입니다 — 바꿔 넣으려면 새 실행으로")
    if (path / CLOSED).exists():
        raise Stop("닫은 실행입니다(run_close) — 새 실행으로")
    if (path / APPLYING).exists():
        # **앞서 넣은 적용이 끝을 못 봤다** — 또 넣지 않고 그 적용에 묻는다. 끝났으면
        # 「적용함」, 실패했으면 「거절됨」 으로 옮겨 그대로 전한다(다시 넣는 것은 그다음
        # 일이다).
        pending = settle_pending(path, server, token)
        if pending is not None:
            return _apply_answer(pending)
    read = _read_json(path / PREVIEW, [])
    seen: dict[str, Any] = read if isinstance(read, dict) else {}
    job = str((seen.get("result") or {}).get("job_id") or "")
    if not job:
        raise Stop("먼저 run_preview 로 미리 보고, 그 요약을 사람에게 보여 확인받으세요")
    if seen.get("digest") != digest(body):
        raise Stop(
            "미리 본 뒤에 묶음이 바뀌었습니다 — run_preview 를 다시 하고 그 요약을 사람에게 "
            "다시 보이세요"
        )
    if str(seen.get("server") or "").rstrip("/") != server.rstrip("/"):
        raise Stop(
            f"미리 본 플랫폼({seen.get('server')})과 다른 곳입니다 — 그 플랫폼으로 다시 "
            "미리 보세요"
        )
    if not (seen.get("result") or {}).get("ok"):
        raise Stop("미리 보기에 오류가 있었습니다 — 고쳐 새 실행으로 다시 미리 보세요")
    outcome = apply_plan(server, token, job)
    result = outcome["result"]
    state = outcome["state"]
    record = {
        "at": _stamp(),
        "server": server,
        "digest": digest(body),
        "job_id": job,
        "apply_job_id": outcome.get("apply_job_id", ""),
        "via": "kit" if state in ("applied", "waiting") else "screen",
        "result": result,
    }
    if state in ("applied", "already") and result.get("applied"):
        # **끝나 들어간 것만 「적용함」 으로 잠근다** — 대기 · 도는 중인 화면의 적용(`already`
        # 의 대기)을 적용함으로 적으면 그 적용이 실패해도 실행이 영영 잠겼다(0.4.107 점검).
        _write(path / APPLIED, record)
        (path / APPLY_FAILED).unlink(missing_ok=True)
    elif state == "waiting":
        record.pop("result")
        _write(path / APPLYING, record | {"message": outcome.get("message", "")})
        (path / APPLY_FAILED).unlink(missing_ok=True)
    elif state in ("applied", "already", "failed"):
        # **적용이 안 됐으면 잠그지 않는다**(`cmd_apply` 와 같다) — 까닭을 고쳐 다시 미리 본다.
        _write(path / APPLY_FAILED, record | {"message": outcome.get("message", "")})
    return _apply_answer(outcome)


def _apply_answer(outcome: dict[str, Any]) -> dict[str, Any]:
    result = outcome.get("result") or {}
    done = outcome["state"] in ("applied", "already") and bool(result.get("applied"))
    return {
        **outcome,
        "result": result,
        "applied": done,
        "summary": summarize(result) if result else outcome.get("message", ""),
        "run_id": str(result.get("run_id") or ""),
    }


def cmd_runs(*, server: str, token: str, limit: int = 20) -> tuple[bool, str]:
    """넣은 판들 — **되돌릴 번호를 여기서 찾는다.**"""
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    url = api_url(server, "/api/bundles/runs", {"limit": max(1, min(int(limit), 100))})
    status, answer = _send(server, "GET", url, headers, None, who="플랫폼")
    if status != 200:
        _refused("플랫폼", status, answer)
    if not isinstance(answer, list) or not answer:
        return True, "넣은 판이 없습니다."
    lines = ["판 번호                               넣은 때        누가      무엇"]
    for one in answer:
        at = str(one.get("at") or "")[:16].replace("T", " ")
        mark = "" if one.get("undoable") else "  (되돌릴 수 없음)"
        lines.append(
            f"{one.get('id')}  {at}  {str(one.get('actor') or '')[:8]:8}  "
            f"{str(one.get('label') or '')[:40]}{mark}"
        )
    return True, "\n".join(lines)


def cmd_undo(run_id: str, *, server: str, token: str, apply: bool = False) -> tuple[bool, str]:
    """그 판을 되돌린다 — 기본은 계획이다(아무것도 안 바뀐다).

    **계획을 사람이 읽은 뒤** `--apply` 로 되돌린다. 그 사이 남이 고친 줄은 되돌리지 않고
    이유가 줄에 적혀 온다 — 남의 변경을 조용히 덮지 않는다.
    """
    ok, text, _ = undo_job(run_id, server=server, token=token, apply=apply)
    return ok, text


def undo_job(
    run_id: str, *, server: str, token: str, apply: bool = False
) -> tuple[bool, str, str]:
    """`cmd_undo` 와 같고 **계획 작업의 번호**도 — 키트 MCP 의 `run_undo_apply` 가 사람이 본
    그 계획을 적용한다."""
    # **번호 자리에 주소를 이어 붙이지 못하게** — `<uuid>/undo?apply=true#` 를 주면 계획만 볼
    # 자리에서 되돌리기가 곧장 적용됐다(0.4.107 점검).
    run_id = job_uuid(run_id, "되돌릴 판의 번호")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Client": CLIENT,
    }
    url = api_url(
        server,
        api_path("api", "bundles", "runs", run_id, "undo"),
        {"apply": "true" if apply else "false"},
    )
    status, answer = _send(server, "POST", url, headers, b"", who="플랫폼")
    if status != 202:
        _refused("플랫폼", status, answer)
    job = _wait_job(server, token, answer)
    result = job.get("result")
    if not isinstance(result, dict):
        raise Stop(f"플랫폼 응답을 읽을 수 없습니다: {job!r}")
    return bool(result.get("ok")), undo_summary(result), str(job.get("id") or "")


def cmd_undo_apply(run_id: str, plan_job: str, *, server: str, token: str) -> dict[str, Any]:
    """**사람이 본 되돌리기 계획**(`run_undo` 의 `plan_job`)을 적용한다 — 사람이 대화에서
    「되돌려」 한 뒤에만. 그 계획이 이 판의 되돌리기 계획인지 먼저 본다(다른 작업을 이 길로
    적용하지 않는다). 그 사이 누가 고쳐 계획과 달라졌으면 플랫폼이 거절한다(지문)."""
    try:
        plan_job = job_uuid(plan_job)
    except Stop:
        raise Stop(
            f"계획 작업의 번호가 아닙니다: {plan_job!r} — run_undo 가 준 plan_job 을 주세요"
        ) from None
    # 판 번호는 **표준 꼴로 견준다** — 대문자로 적은 같은 번호를 「다른 판」 으로 거절했다.
    run_id = job_uuid(run_id, "되돌릴 판의 번호")
    plan = _get_json(server, token, api_path("api", "jobs", plan_job), who="플랫폼")
    params = (plan.get("params") or {}) if isinstance(plan, dict) else {}
    try:
        planned_run = job_uuid(params.get("run_id"))
    except Stop:
        planned_run = ""
    if (
        not isinstance(plan, dict)
        or plan.get("kind") != "bundle_undo"
        or planned_run != run_id
        or params.get("apply")
    ):
        raise Stop(
            f"{plan_job} 은(는) 판 {run_id} 의 되돌리기 계획이 아닙니다 — run_undo 로 계획을 "
            "다시 보고 그 plan_job 을 주세요"
        )
    outcome = apply_plan(server, token, plan_job)
    result = outcome["result"]
    return {
        **outcome,
        "undone": outcome["state"] in ("applied", "already") and bool(result.get("applied")),
        "summary": undo_summary(result) if result else outcome.get("message", ""),
    }


def undo_summary(result: dict[str, Any]) -> str:
    """되돌리기 계획 · 결과를 사람이 읽는 말로 — 건너뛰는 줄의 이유까지."""
    counts = result.get("counts") or {}
    did = "되돌렸습니다" if result.get("applied") else "계획입니다 — 아무것도 안 바뀌었습니다"
    head = (
        f"{did}"
        f" · 지움 {counts.get('delete', 0)} · 되돌림 {counts.get('update', 0)}"
        f" · 다시 이음 {counts.get('create', 0)} · 건너뜀 {counts.get('unchanged', 0)}"
        f" · 오류 {counts.get('error', 0)}"
    )
    skipped = [
        f"  건너뜀: {one.get('label')} — {one.get('message')}"
        for one in (result.get("rows") or [])
        if one.get("action") == "unchanged" and one.get("message")
    ][:20]
    errors = [f"  오류: {one}" for one in (result.get("errors") or [])]
    return "\n".join([head, *errors, *skipped])


def _chosen(args: argparse.Namespace, platform: str) -> Target:
    """명령 줄의 `--server` 가 있으면 그것(등록 없이 한 번만), 아니면 이름으로 고른다."""
    if args.server:
        token = args.token or os.environ.get("SP_TOKEN", "").strip()
        if not token:
            raise Stop("--server 를 줬으면 --token 도 줍니다(또는 SP_TOKEN)")
        return Target("", args.server, token)
    return target(args.platform or platform)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_pipeline", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="빈 실행 폴더를 만든다")
    init.add_argument("run", type=Path)
    init.add_argument("--title", default="")
    init.add_argument(
        "--backfill",
        action="store_true",
        help="대량 적재 — 미리 보기는 계획만 · 웹훅과 감사는 묶음 한 건 · 못 찾은 참조는 비움",
    )

    check = sub.add_parser("validate", help="서버에 보내기 전에 모양을 본다")
    check.add_argument("run", type=Path)
    check.add_argument("--allow-unresolved", action="store_true")
    # **주소와 토큰이 있으면 끝점을 미리 묻는다.** 없으면 모양만 본다(예전과 같다).
    check.add_argument(
        "--platform", default="", help="등록한 플랫폼 이름(작업 폴더의 것이 기본)"
    )
    check.add_argument("--server", default="", help="한 번만 다른 곳 — 등록 없이")
    check.add_argument("--token", default="")

    pull = sub.add_parser("pull", help="허브가 내보낸 묶음을 새 실행 폴더로 받는다")
    pull.add_argument("run", type=Path)
    pull.add_argument(
        "--group",
        required=True,
        action="append",
        help="사이드바 묶음 slug — PLM 기준정보면 plm. **여러 번 적어도 된다**",
    )
    pull.add_argument("--hub", default=setting("SP_HUB_SERVER"))
    pull.add_argument("--hub-token", default=setting("SP_HUB_TOKEN"))
    pull.add_argument("--source", default="hub")

    runs = sub.add_parser("runs", help="넣은 판들 — 되돌릴 번호를 찾는다")
    runs.add_argument("--platform", default="", help="등록한 플랫폼 이름(하나뿐이면 생략)")
    runs.add_argument("--server", default="")
    runs.add_argument("--token", default="")
    runs.add_argument("--limit", type=int, default=20)

    undo = sub.add_parser("undo", help="넣은 판 하나를 통째로 되돌린다(기본은 계획)")
    undo.add_argument("run_id", help="`runs` 가 보여 준 판 번호")
    undo.add_argument("--platform", default="", help="등록한 플랫폼 이름(하나뿐이면 생략)")
    undo.add_argument("--server", default="")
    undo.add_argument("--token", default="")
    undo.add_argument(
        "--apply", action="store_true", help="계획을 읽은 뒤 — 이것 없이는 아무것도 안 바뀐다"
    )

    docx = sub.add_parser(
        "docx",
        help="Word(.docx) → 그림 자리가 든 마크다운 초안 + 그림 파일(플랫폼에는 안 보낸다)",
    )
    docx.add_argument("source", type=Path, help="원천 .docx")
    docx.add_argument(
        "out",
        type=Path,
        nargs="?",
        default=None,
        help="출력 폴더 — 비우면 작업 폴더의 docs/<원천 이름>"
        "(작업 폴더 밖이면 원천 옆 <이름>-초안)",
    )

    for name, what in (
        ("preview", "아무것도 저장하지 않고 미리 본다"),
        ("apply", "미리 본 것을 넣는다"),
    ):
        one = sub.add_parser(name, help=what)
        one.add_argument("run", type=Path)
        one.add_argument(
            "--platform",
            default="",
            help="등록한 플랫폼 이름 — 미리 보기는 작업 폴더의 것, 적용은 미리 본 곳이 기본",
        )
        one.add_argument("--server", default="", help="한 번만 다른 곳 — 등록 없이")
        one.add_argument("--token", default="")
        one.add_argument(
            "--backfill",
            action="store_true",
            help="이 실행을 대량 적재로 — `bundle.json` 에 적어 둔다(다음부터도 그렇게 간다)",
        )

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(cmd_init(args.run, title=args.title, backfill=args.backfill))
            return 0
        if args.command == "validate":
            # 플랫폼이 정해지면 끝점까지 묻고, 아니면 모양만 본다 — 그 사실을 말한다.
            asked: Target | None = None
            note = ""
            try:
                asked = _chosen(args, work_platform(args.run))
            except Stop as why:
                note = f"\n(끝점은 묻지 않았습니다 — {why})"
            ok, text = cmd_validate(
                args.run,
                allow_unresolved=args.allow_unresolved,
                server=asked.server if asked else "",
                token=asked.token if asked else "",
            )
            print(text + note)
            return 0 if ok else 1
        if args.command == "pull":
            if not args.hub or not args.hub_token:
                raise Stop(
                    "허브 주소와 토큰이 필요합니다 — SP_HUB_SERVER · SP_HUB_TOKEN 또는 "
                    "--hub · --hub-token"
                )
            print(
                cmd_pull(
                    args.run,
                    hub=args.hub,
                    hub_token=args.hub_token,
                    group=args.group,
                    source=args.source,
                )
            )
            return 0
        if args.command == "docx":
            # 필요할 때만 읽는다 — sp_docx 가 이 파일(Stop · _write)을 쓴다. 명령으로 돌면 이
            # 파일은 `__main__` 이라 sp_docx 의 Stop 은 **다른 클래스**다 — 따로 받는다.
            import sp_docx

            try:
                print(sp_docx.cmd_docx(args.source, args.out))
            except sp_docx.Stop as stop:
                print(str(stop), file=sys.stderr)
                return 2
            return 0
        if args.command == "runs":
            chosen = _chosen(args, "")
            print(f"플랫폼: {chosen.shown}")
            ok, text = cmd_runs(server=chosen.server, token=chosen.token, limit=args.limit)
            print(text)
            return 0 if ok else 1
        if args.command == "undo":
            chosen = _chosen(args, "")
            print(f"플랫폼: {chosen.shown}")
            ok, text = cmd_undo(
                args.run_id, server=chosen.server, token=chosen.token, apply=args.apply
            )
            print(text)
            return 0 if ok else 1
        if args.backfill:
            # **실행 폴더에 적어 둔다** — 미리 보기와 적용이 같은 칸으로 가야 한다(미리 본
            # 것과 넣는 것이 다르면 미리 본 뜻이 없다).
            mark = args.run / MANIFEST
            body = _read_json(mark, [])
            if not isinstance(body, dict):
                raise Stop(f"{MANIFEST} 을 읽을 수 없습니다: {args.run}")
            body["backfill"] = True
            _write(mark, body)
        if args.command == "preview":
            chosen = _chosen(args, work_platform(args.run))
            print(f"미리 보는 곳: {chosen.shown}")
            ok, text = cmd_preview(
                args.run, server=chosen.server, token=chosen.token, platform=chosen.name
            )
        else:
            # **적용은 미리 본 곳으로.** 그 이름을 실행 폴더가 기억한다.
            chosen = _chosen(args, preview_platform(args.run) or work_platform(args.run))
            print(f"넣는 곳: {chosen.shown}")
            ok, text = cmd_apply(args.run, server=chosen.server, token=chosen.token)
        print(text)
        return 0 if ok else 1
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
