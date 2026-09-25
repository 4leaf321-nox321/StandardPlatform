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
    BoardOut,
    CapacityIn,
    CapacityOut,
    CapacitySummaryOut,
    CoverageOut,
    DefsOut,
    HistoryOut,
    PairBulkIn,
    PairBulkOut,
    PairIn,
    PairOut,
    PairPatchIn,
    SetupStatusOut,
    StaffIn,
    StaffOut,
    StaffSummaryOut,
)
from app.modules.accounts.models import User
from app.shared import permissions
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import Conflict, code

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


@router.post("/pairs/bulk-move", response_model=PairBulkOut)
def pair_bulk_move(
    payload: PairBulkIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> PairBulkOut:
    """고른 연계를 한 부서로 옮긴다 — 권한은 **건마다** 본다."""
    if not payload.workspace_slug:
        raise Conflict(code("CAEGROUP", 17), "옮길 부서를 고르세요.")
    workspace = permissions.workspace_by_slug(db, payload.workspace_slug)
    return PairBulkOut(
        changed=services.bulk_move(db, user, pair_ids=payload.ids, workspace=workspace)
    )


@router.post("/pairs/bulk-unlink", response_model=PairBulkOut)
def pair_bulk_unlink(
    payload: PairBulkIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> PairBulkOut:
    """고른 연계를 해제한다 — **평가와 이력도 함께 간다.**"""
    return PairBulkOut(changed=services.bulk_unlink(db, user, pair_ids=payload.ids))


@router.patch("/pairs/{pair_id}", response_model=PairOut)
def pair_update(
    pair_id: uuid.UUID,
    payload: PairPatchIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PairOut:
    """연계의 소속 부서를 옮긴다 — **양쪽 부서 멤버만.**

    시험 항목 · 해석을 바꾸는 길은 두지 않는다. 그것은 다른 연계이고, 바꾸면 이미 매긴
    평가가 엉뚱한 대상의 평가로 남는다 — 해제하고 다시 등록한다.
    """
    workspace = permissions.workspace_by_slug(db, payload.workspace_slug)
    row = services.move(db, user, pair_id=pair_id, workspace=workspace)
    found = [one for one in services.pairs(db, user, workspace=None) if one["id"] == row.id]
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

    **근거(글)는 필수다.** 가상검증률의 수준은 값이 정한다(보낸 수준은 무시한다).
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


@router.get("/board", response_model=BoardOut)
def board(user: User = Depends(current_user), db: Session = Depends(get_db)) -> BoardOut:
    """대시보드가 그리는 한 벌 — 연계마다의 수준과 최근 변경.

    **타일과 분포를 한 자료로 낸다.** 분포를 서버가 따로 세어 주면 둘이 갈릴 수 있고,
    그때 어느 쪽이 맞는지 아무도 답할 수 없다.
    """
    return BoardOut(**services.board(db, user))


@router.get("/staff", response_model=list[StaffOut])
def staff_list(
    workspace: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[StaffOut]:
    """인력 목록 — 부서를 안 주면 전부.

    **가명이 기본이다.** 실명은 그 부서를 고칠 수 있는 사람과 시스템 관리자에게만 온다 —
    사람을 세는 자리이지 사람을 평가하는 자리가 아니다.
    """
    chosen = permissions.workspace_by_slug(db, workspace) if workspace else None
    return [StaffOut(**one) for one in services.staff(db, user, workspace=chosen)]


@router.get("/staff/summary", response_model=StaffSummaryOut)
def staff_summary(
    workspace: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StaffSummaryOut:
    """사람 수 · FTE 합 · 해석별 FTE · 종류별 사람 수. **파생값은 저장하지 않는다.**"""
    chosen = permissions.workspace_by_slug(db, workspace) if workspace else None
    return StaffSummaryOut(**services.staff_summary(db, user, workspace=chosen))


@router.post("/staff", response_model=StaffOut, status_code=201)
def staff_create(
    payload: StaffIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> StaffOut:
    workspace = permissions.workspace_by_slug(db, payload.workspace_slug)
    made = services.save_staff(
        db, user, staff_id=None, workspace=workspace, payload=payload.model_dump()
    )
    found = [one for one in services.staff(db, user, workspace=workspace) if one["id"] == made]
    return StaffOut(**found[0])


@router.put("/staff/{staff_id}", response_model=StaffOut)
def staff_update(
    staff_id: uuid.UUID,
    payload: StaffIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StaffOut:
    workspace = permissions.workspace_by_slug(db, payload.workspace_slug)
    services.save_staff(
        db, user, staff_id=staff_id, workspace=workspace, payload=payload.model_dump()
    )
    found = [
        one for one in services.staff(db, user, workspace=workspace) if one["id"] == staff_id
    ]
    return StaffOut(**found[0])


@router.delete("/staff/{staff_id}", status_code=204)
def staff_delete(
    staff_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> None:
    services.delete_staff(db, user, staff_id=staff_id)


@router.get("/capacity", response_model=CapacityOut)
def capacity_get(
    workspace: str = Query(...),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CapacityOut:
    """그 부서의 인프라 — 없으면 빈 줄이 온다(만들지는 않는다)."""
    chosen = permissions.workspace_by_slug(db, workspace)
    return CapacityOut(**services.capacity(db, workspace=chosen))


@router.put("/capacity", response_model=CapacityOut)
def capacity_save(
    payload: CapacityIn,
    workspace: str = Query(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CapacityOut:
    """인프라를 적는다 — **그 부서 멤버만.**"""
    chosen = permissions.workspace_by_slug(db, workspace)
    return CapacityOut(
        **services.save_capacity(db, user, workspace=chosen, payload=payload.model_dump())
    )


@router.get("/capacity/summary", response_model=CapacitySummaryOut)
def capacity_summary(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> CapacitySummaryOut:
    """전사 합계 — **공유 자원은 한 번만 센다.**

    부서마다 적힌 공유 라이선스를 그대로 더하면 전사 합이 실제보다 커지고, 그 숫자로
    투자를 판단하면 이미 있는 것을 또 산다.
    """
    return CapacitySummaryOut(**services.capacity_summary(db))
