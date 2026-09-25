"""확장 `caegroup` — CAE 그룹의 기능 묶음.

첫 기능은 **디지털 트윈 역량**이다(`docs/디지털트윈-역량-이식-계획.md`). 기본 기능이
아니므로 이 확장을 안 켠 설치에는 메뉴도 경로도 없다 — 켜짐은 관리 › 서버 › 「확장 모듈」
에서 정한다.

**코어는 이 확장을 모른다.** 여기서 코어를 부르되 그 반대는 없다 — 구조 시험이 지킨다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.extensions.caegroup import routes, services
from app.shared import extensions as extension_points


def register(api: APIRouter) -> None:
    """코어가 부르는 유일한 자리 — 라우터 · 확장 지점 · PAT 범위를 여기서 연다."""
    api.include_router(routes.router)
    # **홈의 「남은 일」 에 올린다.** 대시보드를 열어야만 보이는 자료는 안 채워진다 —
    # 그리고 안 채운 자료는 「모름」 과 구별되지 않는다. 0 건인 항목은 레지스트리가 거른다.
    extension_points.register_maintenance(services.maintenance)
