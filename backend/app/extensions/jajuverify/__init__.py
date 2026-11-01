"""확장 `jajuverify` — 자주검증 조직의 회의(심사회 · 포스트모텀 · 설명회) 한 장 보고(ADR 0044).

**먼저 독립된 회의 표다**(사용자 결정, 2026-10-30) — 전용 표(`jv_meetings` · `jv_rows`)에 두고,
칸의 사진은 공통 첨부로 붙인다. 표를 만들어 써 본 뒤 온톨로지 연결(열마다 받을 타입 · 고르기)을
다음 단계에서 그 안에서 시험한다. 이 확장을 안 켠 설치에는 메뉴도 경로도 없다 — 켜짐은 관리 ›
서버 › 「확장 모듈」 에서 정한다.

**코어는 이 확장을 모른다.** 여기서 코어를 부르되 그 반대는 없다 — 구조 시험이 지킨다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.extensions.jajuverify import routes, services
from app.shared import access_log, scopes
from app.shared import extensions as extension_points

#: 이 확장이 하는 일 한 줄 — 플랫폼 자기소개(MCP 안내문)와 서버 화면이 이름 곁에 싣는다.
DESCRIPTION = (
    "자주검증 회의 표 — 심사회 · 포스트모텀 · 설명회(항목 · 문제점 · 개선대책 · 일정 · 비고)"
)


def register(api: APIRouter) -> None:
    """코어가 부르는 유일한 자리 — 라우터 · 첨부 자리 · 공통 화면 · PAT 범위를 여기서 연다."""
    api.include_router(routes.router)
    # **칸의 사진은 공통 첨부다** — 공통 틀(files)은 이 표를 모르고, 붙일 자리가 있나 · 보이나
    # · 고칠 수 있나 · 그 칸이 사진 칸인가를 이 확장이 답한다(ADR 0012). 끈 설치에서는 그
    # 자리가 없다고 답한다(`services.enabled`).
    extension_points.register_attachment_owner(services.OWNER_TABLE, services.attachment_owner)
    # 에이전트가 「자주검증 회의 표」 가 어디 있는지 알게 — 꺼진 설치에서는 말하지 않는다.
    extension_points.register_profile_facts(services.profile_facts)
    extension_points.register_stats(services.stats)
    # **부서를 지우거나 합칠 때** 회의가 걸린다 — 등록하지 않으면 부서 삭제 확인에 안 뜨고
    # (FK 가 RESTRICT 라 DB 가 거절해 500), 통폐합이 회의를 못 옮긴다.
    extension_points.register_workspace_reference(services.workspace_reference)
    extension_points.register_workspace_content(services.workspace_content)
    # 기계 자격(PAT)으로 고치려면 이 범위가 있어야 한다 — 읽기는 `read` 로 된다.
    scopes.register_write_scope("/api/ext/jajuverify", "jajuverify:write")
    # **폴링 경로는 접근 로그에서 뺀다** — 한 장을 연 사람마다 10~15초에 한 번(바뀌었나)과
    # 10초에 한 번(지금 수정하는 칸)이다. 안 빼면 접근 로그 · 요청 시간 기록이 그 줄로 가득
    # 찬다.
    for template in routes.POLLING_ROUTES:
        access_log.register_polling_route(template)
