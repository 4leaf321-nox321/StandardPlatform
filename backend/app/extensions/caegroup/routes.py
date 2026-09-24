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
from app.extensions.caegroup.schemas import DefsOut, PairIn, PairOut, SetupStatusOut
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
