"""묶음 라우터 — 로컬 정제 도구와 MCP 가 부른다.

가져오기도 내보내기도 **작업**이다(202). 묶음은 파일 가져오기보다 크다 — 리허설에서 1만
객체가 44초였다. 요청 안에서 돌리면 클라이언트가 먼저 끊는다. 규칙 검사(비었나 · 범위가
있나 · 관리자인가)는 **넣는 순간** 한다 — 워커가 돌 때 거절하면 사람은 몇 분 뒤에야 본다.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.bundles import undo
from app.modules.bundles.schemas import BundleIn
from app.modules.jobs import routes as jobs_routes
from app.modules.jobs import services as job_services
from app.modules.jobs.schemas import JobOut
from app.modules.ontology.models import NavGroup
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import Conflict, Forbidden, NotFound, code

router = APIRouter(prefix="/bundles", tags=["bundles"])

#: 정의가 든 묶음에 더 필요한 범위. 경로의 범위(objects:write)만으로는 부족하다.
ONTOLOGY_SCOPE = "ontology:write"


@router.post("/import", response_model=JobOut, status_code=202)
def import_bundle(
    payload: BundleIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    """정의 · 객체 · 관계를 **한 묶음으로** — 작업이 된다.

    `apply=false`(기본)면 워커가 **한 번에 미리 본다** — 정의를 먼저 적용하지 않아도 그
    정의로 객체와 관계를 맞춰 본다. 계획을 본 뒤 `POST /api/jobs/{id}/apply` 로 넣는다(같은
    파일 · 같은 지문). `apply=true` 로 곧장 보내도 된다 — 정제 도구가 제 지문으로 미리 본
    것과 견준 뒤 그렇게 한다. **적용은 전부 아니면 무.**
    """
    graves = payload.tombstones
    has_graves = graves is not None and bool(graves.objects or graves.relations)
    if (
        payload.ontology is None
        and not payload.objects
        and not payload.relations
        and not has_graves
    ):
        raise Conflict(
            code("BUNDLES", 2),
            "묶음이 비어 있습니다 — ontology · objects · relations · tombstones 중 "
            "하나는 있어야 합니다.",
        )
    granted: list[str] | None = getattr(request.state, "token_scopes", None)
    if payload.ontology is not None and granted is not None and ONTOLOGY_SCOPE not in granted:
        raise Forbidden(
            code("BUNDLES", 1),
            f"이 토큰에는 {ONTOLOGY_SCOPE} 범위가 없습니다 — 정의가 든 묶음은 정의와 객체 "
            "쓰기 범위가 모두 필요합니다.",
            details={"needed": ONTOLOGY_SCOPE, "granted": granted},
        )
    body = payload.model_dump(mode="json", exclude={"apply"})
    stored = job_services.store_file(
        db,
        name="bundle.json",
        content_type="application/json",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    )
    params: dict[str, Any] = {"apply": payload.apply, "source": payload.source}
    if payload.ontology is not None:
        # 적용 작업을 만드는 토큰이 다를 수 있다 — 그때 다시 묻도록 필요한 범위를 적어 둔다.
        params["needs_scope"] = ONTOLOGY_SCOPE
    job = job_services.enqueue(
        db,
        kind="bundle_import",
        params=params,
        user=user,
        workspace_id=None,
        input_file=stored,
    )
    db.commit()
    db.refresh(job)
    return jobs_routes._out(db, job)


class RunOut(BaseModel):
    """적용한 한 판 — **되돌릴 자리.**"""

    id: uuid.UUID
    at: datetime
    actor: str
    source: str = ""
    label: str = ""
    counts: dict[str, Any] = Field(default_factory=dict)
    undoable: bool = True
    """거짓이면 되돌릴 수 없다 — 이미 되돌렸거나 보관 기간이 지나 기록을 지웠다."""
    undone_at: datetime | None = None
    undo_note: str = ""


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    limit: int = 30,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[RunOut]:
    """**넣은 판들** — 최근 것부터. 시스템 관리자는 전부, 나머지는 자기가 넣은 것.

    되돌리려면 여기서 번호를 찾는다. 적용한 판만 있다(미리 보기는 아무것도 안 바꾼다).
    """
    rows = undo.recent(db, user, limit=max(1, min(limit, 100)))
    counted = undo.undo_counts(db, [one.id for one in rows])
    return [
        RunOut(
            id=one.id,
            at=one.at,
            actor=one.actor_label,
            source=one.source,
            label=one.label,
            counts=dict(one.counts or {}),
            undoable=one.undone_at is None and counted.get(one.id, 0) > 0,
            undone_at=one.undone_at,
            undo_note=one.undo_note,
        )
        for one in rows
    ]


@router.post("/runs/{run_id}/undo", response_model=JobOut, status_code=202)
def undo_run(
    run_id: uuid.UUID,
    apply: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    """그 판을 **통째로 되돌린다** — 작업이 된다.

    기본은 계획이다: 무엇이 지워지고 무엇이 어느 값으로 돌아가는지 줄마다 보여 준다. 사람이
    읽은 뒤 `POST /api/jobs/{id}/apply`(또는 여기에 `apply=true`) 로 되돌린다. **그 사이
    남이 고친 줄은 되돌리지 않고 이유를 적는다** — 남의 변경을 조용히 덮지 않는다.
    """
    run = undo.find(db, user, run_id)
    if run.undone_at is not None:
        raise Conflict(
            code("BUNDLES", 32),
            f"이미 되돌린 판입니다({run.undone_at:%Y-%m-%d %H:%M}). {run.undo_note}".strip(),
        )
    job = job_services.enqueue(
        db,
        kind="bundle_undo",
        params={"run_id": str(run_id), "apply": apply},
        user=user,
        workspace_id=None,
    )
    db.commit()
    db.refresh(job)
    return jobs_routes._out(db, job)


class ExportRequest(BaseModel):
    group: str | None = Field(
        default=None, description="사이드바 묶음 slug 하나 — 허브의 PLM 기준정보면 plm"
    )
    groups: list[str] = Field(
        default_factory=list,
        description='여럿을 한 봉투로 — `["plm", "core"]`. `group` 과 함께 적어도 된다',
    )
    since: str | None = Field(
        default=None,
        description="**사라진 것**을 이 시각 뒤의 것만 싣는다(지난 봉투의 `exported_at`). "
        "살아 있는 것은 늘 전량이다",
    )
    allow_outside_refs: bool = Field(
        default=False,
        description="묶음 **밖을 가리키는 참조**를 막지 않는다 — 받는 쪽이 그 타입을 이미 "
        "다른 묶음으로 받아 뒀을 때만 켠다",
    )

    def wanted(self) -> list[str]:
        """적은 차례대로, 겹치는 것은 한 번만."""
        out: list[str] = []
        for one in [self.group, *self.groups]:
            slug = (one or "").strip()
            if slug and slug not in out:
                out.append(slug)
        return out


@router.post("/export", response_model=JobOut, status_code=202)
def export_bundle(
    payload: ExportRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> JobOut:
    """허브가 쌍둥이에 내려주는 묶음 — 작업이 되고, 결과 파일을 `GET /api/jobs/{id}/download`
    로 받는다. 받는 쪽은 그것을 그대로 `POST /bundles/import` 에 `source` 를 붙여 보낸다.

    **묶음을 여럿 골라도 된다**(`groups`). 코어를 축별로 나눠 둔 설치에서 하나씩 내보내면
    축끼리 가리키는 참조 때문에 어느 쪽도 못 보낸다.

    시스템 관리자만 — 허브의 기준정보 전부를 부서 가리지 않고 내보내기 때문이다.
    """
    wanted = payload.wanted()
    if not wanted:
        raise Conflict(
            code("BUNDLES", 21), "내보낼 사이드바 묶음을 하나는 골라야 합니다(group · groups)."
        )
    for slug in wanted:
        if db.scalar(select(NavGroup).where(NavGroup.slug == slug)) is None:
            # 넣는 순간 말한다 — 워커가 돌아서야 「없는 묶음」 이라 하면 정제 도구는 몇 초를
            # 기다린 뒤에야 듣는다.
            raise NotFound(code("BUNDLES", 20), f"사이드바 묶음을 찾을 수 없습니다: {slug}")
    job = job_services.enqueue(
        db,
        kind="bundle_export",
        params={
            "group": wanted[0],
            "groups": wanted,
            "allow_outside_refs": payload.allow_outside_refs,
            "since": payload.since,
        },
        user=user,
        workspace_id=None,
    )
    db.commit()
    db.refresh(job)
    return jobs_routes._out(db, job)
