"""디지털 트윈 역량 — 확장 라우터.

경로에 `dt` 한 단을 둔다(`/api/ext/caegroup/dt/…`) — `caegroup` 에 CAE 그룹의 **다른**
기능이 붙는 날, 그때 경로를 바꾸면 사람들이 즐겨찾기한 주소가 죽는다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.extensions.caegroup import definitions as D
from app.extensions.caegroup import services
from app.extensions.caegroup.schemas import (
    AssessmentIn,
    AssessmentOut,
    CoverageOut,
    DefsOut,
    HistoryOut,
    PairIn,
    PairOut,
    SetupStatusOut,
)
from app.modules.accounts.models import User
from app.shared import permissions
from app.shared.auth import current_user, require_system_admin

router = APIRouter(prefix="/ext/caegroup/dt", tags=["ext:caegroup"])


@router.get("/defs", response_model=DefsOut)
def defs(_: User = Depends(current_user)) -> DefsOut:
    """정의를 그대로 내려 준다 — **축 이름과 척도를 화면에 박지 않는다.**

    문구를 고치는 일이 배포 한 번으로 끝나고, 화면은 고칠 데가 없다.
    """
    return DefsOut(
        sector=D.SECTOR,
        sector_label=D.SECTOR_LABEL,
        subject_label=D.SUBJECT_LABEL,
        agent_label=D.AGENT_LABEL,
        axes=D.AXES,
        evidence_tiers=D.EVIDENCE_TIERS,
        accuracy_thresholds=D.ACCURACY_THRESHOLDS,
        accuracy_rules=D.ACCURACY_RULES,
    )


@router.get("/setup", response_model=SetupStatusOut)
def setup_status(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> SetupStatusOut:
    return SetupStatusOut(**services.status(db))


@router.post("/setup", response_model=SetupStatusOut)
def setup(
    user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> SetupStatusOut:
    """기준 정보 타입·속성을 만든다 — **시스템 관리자만.**

    온톨로지 정의를 바꾸는 일이라 부서 권한으로 할 일이 아니다. 이미 있는 타입·속성은
    건드리지 않는다(가져오기는 더하고 고치기만 한다).
    """
    return SetupStatusOut(**services.setup(db, user))


@router.get("/pairs", response_model=list[PairOut])
def pair_list(
    workspace: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[PairOut]:
    """연계 목록 — **부서로 가리지 않는다**(전사 역량은 조직을 가로지르는 물음이다).

    부서를 주면 그 부서만 좁혀 본다. 고치는 것은 그 부서 멤버만이다.
    """
    chosen = permissions.workspace_by_slug(db, workspace) if workspace else None
    return [PairOut(**one) for one in services.pairs(db, user, workspace=chosen)]


@router.post("/pairs", response_model=PairOut, status_code=201)
def pair_create(
    payload: PairIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> PairOut:
    workspace = permissions.workspace_by_slug(db, payload.workspace_slug)
    row = services.link(
        db, user, subject_id=payload.subject_id, agent_id=payload.agent_id, workspace=workspace
    )
    found = [
        one for one in services.pairs(db, user, workspace=workspace) if one["id"] == row.id
    ]
    return PairOut(**found[0])


@router.delete("/pairs/{pair_id}", status_code=204)
def pair_delete(
    pair_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> None:
    services.unlink(db, user, pair_id=pair_id)


@router.get("/pairs/{pair_id}/assessments", response_model=list[AssessmentOut])
def assessment_list(
    pair_id: uuid.UUID, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[AssessmentOut]:
    """그 연계의 평가 — 축 순서대로. **안 매긴 축은 목록에 없다.**

    빈 줄을 만들어 내려 주면 화면이 「안 매긴 것」 과 「0 으로 매긴 것」 을 구별할 수 없다.
    """
    return [AssessmentOut(**one) for one in services.assessments(db, pair_id=pair_id)]


@router.put("/pairs/{pair_id}/assessments/{axis}", response_model=AssessmentOut)
def assessment_save(
    pair_id: uuid.UUID,
    axis: str,
    payload: AssessmentIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AssessmentOut:
    """평가를 적는다 — **그 부서 멤버만.**

    근거는 필수이고, 근거 등급이 「확인」 · 「검증」 이면 근거 자료도 필수다. 가상검증률의
    수준은 값이 정한다(보낸 수준은 무시한다).
    """
    return AssessmentOut(
        **services.save_assessment(
            db, user, pair_id=pair_id, axis_key=axis, payload=payload.model_dump()
        )
    )


@router.get("/pairs/{pair_id}/history", response_model=list[HistoryOut])
def assessment_history(
    pair_id: uuid.UUID, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[HistoryOut]:
    """평가가 바뀐 기록 — **담당자가 본다**(감사 기록은 시스템 관리자만 읽는다)."""
    return [HistoryOut(**one) for one in services.history(db, pair_id=pair_id)]


@router.get("/coverage", response_model=CoverageOut)
def coverage(
    workspace: str | None = Query(default=None),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CoverageOut:
    """축마다의 평가 완료율. 3단계 대시보드가 이것으로 그린다."""
    chosen = permissions.workspace_by_slug(db, workspace) if workspace else None
    return CoverageOut(**services.coverage(db, workspace=chosen))
