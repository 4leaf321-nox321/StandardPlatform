"""자주검증 입력 — 확장 라우터. 쓰기는 코어 객체 API 로 간다(ADR 0051)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.extensions.svinput import definitions as D
from app.extensions.svinput import services
from app.extensions.svinput.schemas import (
    CandidatesOut,
    ReportSheetOut,
    SettingsIn,
    StatusOut,
)
from app.modules.accounts.models import User
from app.shared.auth import current_user, require_system_admin

router = APIRouter(prefix=f"/ext/{D.NAME}", tags=[f"ext:{D.NAME}"])


@router.get("/status", response_model=StatusOut)
def status(_: User = Depends(current_user), db: Session = Depends(get_db)) -> StatusOut:
    """설정 상태 — 역할마다 고른 타입 · 칸과 빠진 것, 쓸 준비가 됐나(`ready`)."""
    return StatusOut(**services.status(db))


@router.get("/candidates", response_model=CandidatesOut)
def candidates(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> CandidatesOut:
    """설정 후보 — 허브 접두(`sv_pm_`)가 맞는 타입과 속성 키. 시스템 관리자."""
    return CandidatesOut(**services.candidates(db))


@router.put("/settings", response_model=StatusOut)
def settings(
    body: SettingsIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> StatusOut:
    """역할 → 타입 · 칸 저장(보낸 역할만, type=null 이면 비움). 시스템 관리자 — 사람에게
    먼저 묻는다."""
    out = services.save_roles(
        db, user, {key: one.model_dump() for key, one in body.roles.items()}
    )
    db.commit()
    return StatusOut(**out)


@router.get("/reports/{report_id}", response_model=ReportSheetOut)
def report_sheet(
    report_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReportSheetOut:
    """포스트모텀 문서 한 장 — 머리 · 문제점 줄(일정 상태 · 하위 기록 수 · 판정 통과/실패) ·
    지표.

    문제점 · 하위 기록을 고치려면 코어 객체 API(`PATCH /api/objects/<타입>/<id>`)를 쓴다.
    """
    return ReportSheetOut(**services.report_sheet(db, user, report_id))
