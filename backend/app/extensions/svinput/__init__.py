"""확장 `svinput` — 자주검증 입력: 포스트모텀을 사람이 쉽게 입력 · 열람하는 화면(ADR 0051).

허브에 이미 있는 온톨로지 타입(`sv_pm_*`) 위에 **읽는 방향이 다른 화면**만 얹는다 —
문서 목록(모델별)
→ 문제점 리스트 → 문제점 한 페이지 → 집계. 표도 타입도 새로 만들지 않고, 어느 타입 · 칸이 어느
역할인지는 설정 한 줄이 가리킨다. 쓰기는 코어 객체 API 로 간다. 이 확장을 안 켠 설치에는 메뉴도
경로도 없다 — 켜짐은 관리 › 서버 › 「확장 모듈」 에서 정한다.

**코어는 이 확장을 모른다.** 여기서 코어를 부르되 그 반대는 없다 — 구조 시험이 지킨다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.extensions.svinput import definitions as D
from app.extensions.svinput import routes, services
from app.shared import extensions as extension_points
from app.shared import scopes

#: 이 확장이 하는 일 한 줄 — 플랫폼 자기소개(MCP 안내문)와 서버 화면이 이름 곁에 싣는다.
DESCRIPTION = (
    "자주검증 입력 — 포스트모텀 문서 · 문제점 · 단계별 판정을 쉽게 입력 · 열람하고 집계한다"
)


def register(api: APIRouter) -> None:
    """코어가 부르는 유일한 자리 — 라우터 · 공통 화면 · PAT 범위를 여기서 연다."""
    api.include_router(routes.router)
    # 에이전트가 「자주검증 입력」 이 어디 있는지 알게 — 꺼진 설치에서는 말하지 않는다.
    extension_points.register_profile_facts(services.profile_facts)
    extension_points.register_stats(services.stats)
    # 설정이 안 된 설치는 홈의 「남은 일」 이 시스템 관리자에게 말한다.
    extension_points.register_maintenance(services.maintenance)
    # 기계 자격(PAT)으로 설정을 고치려면 이 범위가 있어야 한다 — 객체 자체는 `objects:write`.
    scopes.register_write_scope(f"/api/ext/{D.NAME}", f"{D.NAME}:write")
