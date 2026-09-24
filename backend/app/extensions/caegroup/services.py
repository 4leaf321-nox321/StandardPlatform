"""디지털 트윈 역량 — 기준 정보 설정과 연계.

**기준 정보는 온톨로지가 들고 있다.** 이 확장이 하는 일은 「어느 타입을 시험 항목 ·
시뮬레이션으로 쓸지」 를 기억하고, 없으면 만들어 주고, 그 객체들의 **연계**를 등록하는 것이다.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extensions.caegroup import definitions as D
from app.extensions.caegroup.models import CaeDtPair, CaeDtSetting
from app.modules.accounts.models import User
from app.modules.objects.models import ObjectInstance
from app.modules.ontology import importer
from app.modules.ontology.models import ObjectType
from app.modules.workspaces.models import Workspace
from app.shared import audit, permissions
from app.shared.errors import Conflict, NotFound, code

SUBJECT_SLUG = "sim_test_item"
AGENT_SLUG = "sim_tool"

#: 첫 설정이 만드는 정의. **가져오기(importer)로 넣는다** — 더하고 고치기만 하므로
#: 이미 있는 타입·속성은 건드리지 않고, 한 트랜잭션이라 반쯤 만들어진 정의가 안 남는다.
SETUP_SCHEMA: dict[str, Any] = {
    "types": [
        {
            "slug": SUBJECT_SLUG,
            "label": "시험 항목",
            "icon": "ClipboardCheck",
            "description": "디지털 트윈 역량 평가의 대상 — 시뮬레이션이 대신 확인하는 시험.",
            "key_policy": "optional",
            "properties": [
                {"key": "detail", "label": "세부 내용", "data_type": "text"},
                {
                    "key": "product_families",
                    "label": "제품군",
                    "data_type": "text",
                    "multi": True,
                },
                # 불량 유형은 **시험에 붙는다** — 시뮬레이션에 두면 같은 시험인데 도구마다
                # 줄이 갈려, 「아직 아무 데서도 재현 안 되는 불량」 을 못 센다.
                {
                    "key": "defect_types",
                    "label": "불량 유형",
                    "data_type": "text",
                    "multi": True,
                },
                {
                    "key": "dev_stages",
                    "label": "개발 단계",
                    "data_type": "enum",
                    "multi": True,
                    "enum_options": ["선행 검토", "DV", "PV", "양산"],
                    "help": "사업부별로 명칭이 다릅니다 — 이 목록을 수정하여 사용합니다.",
                },
                {
                    "key": "accuracy_rule",
                    "label": "가상검증률 집계",
                    "data_type": "enum",
                    "enum_options": [one["key"] for one in D.ACCURACY_RULES],
                    "default_value": "auto",
                    "help": "여러 시뮬레이션의 값에서 항목 값을 어떻게 셈하나.",
                },
            ],
        },
        {
            "slug": AGENT_SLUG,
            "label": "시뮬레이션",
            "icon": "Wrench",
            "description": "디지털 트윈 역량 평가의 수단 — 시험을 대신하는 해석 · 도구.",
            "key_policy": "optional",
            "properties": [
                {"key": "kind", "label": "시뮬레이션 종류", "data_type": "text"},
                {
                    "key": "model_kind",
                    "label": "모델 종류",
                    "data_type": "enum",
                    "enum_options": ["물리 기반", "데이터 기반", "하이브리드"],
                    "help": "동일 기준 집계를 위해 속성으로 관리합니다.",
                },
            ],
        },
    ]
}


def setting(db: Session) -> CaeDtSetting:
    """설정 한 줄 — 없으면 만든다. **커밋은 부르는 쪽이 한다.**"""
    row = db.get(CaeDtSetting, 1)
    if row is None:
        row = CaeDtSetting(id=1)
        db.add(row)
        db.flush()
    return row


def _type_or_none(db: Session, slug: str | None) -> ObjectType | None:
    if not slug:
        return None
    return db.scalar(select(ObjectType).where(ObjectType.slug == slug))


def status(db: Session) -> dict[str, Any]:
    """화면이 「쓸 준비가 됐나」 를 묻는 자리."""
    row = setting(db)
    subject = _type_or_none(db, row.subject_type_slug)
    agent = _type_or_none(db, row.agent_type_slug)
    return {
        "subject_type_slug": row.subject_type_slug,
        "subject_type_label": subject.label if subject else None,
        "agent_type_slug": row.agent_type_slug,
        "agent_type_label": agent.label if agent else None,
        "ready": subject is not None and agent is not None,
    }


def setup(db: Session, user: User) -> dict[str, Any]:
    """기준 정보 타입·속성을 만들고 설정에 적는다.

    **사람이 손으로 정의를 짜 맞추게 두지 않는다.** 그러면 설치마다 칸 이름이 갈리고,
    화면은 어느 칸이 불량 유형인지 알 방법이 없다.
    """
    plan = importer.apply(db, SETUP_SCHEMA)
    if plan.errors:
        raise Conflict(
            code("CAEGROUP", 1), "기준 정보 생성에 실패했습니다: " + "; ".join(plan.errors)
        )
    row = setting(db)
    row.subject_type_slug = SUBJECT_SLUG
    row.agent_type_slug = AGENT_SLUG
    audit.record(
        db,
        action="caegroup.dt.setup",
        actor=user,
        target_table="cae_dt_settings",
        target_id=None,
        target_label="기준 정보",
        changes={"subject": SUBJECT_SLUG, "agent": AGENT_SLUG, "changed": len(plan.changes)},
    )
    db.commit()
    return status(db)


def _object_of(
    db: Session, object_id: uuid.UUID, *, type_slug: str, what: str
) -> ObjectInstance:
    row = db.get(ObjectInstance, object_id)
    if row is None or row.deleted_at is not None:
        raise NotFound(code("CAEGROUP", 2), f"{what}을 찾을 수 없습니다.")
    kind = db.get(ObjectType, row.type_id)
    if kind is None or kind.slug != type_slug:
        # **다른 타입의 객체를 이으면 화면이 그 줄을 못 그린다.** 이름도 속성도 다르다.
        raise Conflict(
            code("CAEGROUP", 3),
            f"{what}의 타입이 설정과 다릅니다: {kind.slug if kind else '?'}",
        )
    return row


def pairs(db: Session, user: User, *, workspace: Workspace | None) -> list[dict[str, Any]]:
    """연계 목록 — 화면의 왼쪽 표. **이름은 객체에서 온다.**"""
    stmt = select(CaeDtPair)
    if workspace is not None:
        stmt = stmt.where(CaeDtPair.workspace_id == workspace.id)
    else:
        # 부서를 안 고르면 **내가 속한 부서만.** 전사 조회는 다음 단계의 물음이다.
        mine = permissions.my_workspace_ids(db, user)
        if not mine:
            return []
        stmt = stmt.where(CaeDtPair.workspace_id.in_(mine))
    rows = list(db.scalars(stmt))
    wanted = {one.subject_id for one in rows} | {one.agent_id for one in rows}
    names = (
        {
            one.id: one
            for one in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(wanted)))
        }
        if wanted
        else {}
    )
    out: list[dict[str, Any]] = []
    for one in rows:
        subject = names.get(one.subject_id)
        agent = names.get(one.agent_id)
        out.append(
            {
                "id": one.id,
                "workspace_id": one.workspace_id,
                "subject_id": one.subject_id,
                "subject_label": subject.label if subject else "(지워짐)",
                "agent_id": one.agent_id,
                "agent_label": agent.label if agent else "(지워짐)",
                "created_at": one.created_at,
            }
        )
    out.sort(key=lambda one: (one["subject_label"], one["agent_label"]))
    return out


def link(
    db: Session,
    user: User,
    *,
    subject_id: uuid.UUID,
    agent_id: uuid.UUID,
    workspace: Workspace,
) -> CaeDtPair:
    """연계 등록 — **부서 멤버만.**"""
    permissions.require_member(db, workspace=workspace, user=user)
    ready = status(db)
    if not ready["ready"]:
        raise Conflict(code("CAEGROUP", 4), "기준 정보 설정이 필요합니다.")
    _object_of(db, subject_id, type_slug=str(ready["subject_type_slug"]), what="시험 항목")
    _object_of(db, agent_id, type_slug=str(ready["agent_type_slug"]), what="시뮬레이션")
    if db.scalar(
        select(CaeDtPair).where(
            CaeDtPair.subject_id == subject_id, CaeDtPair.agent_id == agent_id
        )
    ):
        raise Conflict(code("CAEGROUP", 5), "이미 등록된 연계입니다.")
    row = CaeDtPair(
        workspace_id=workspace.id,
        subject_id=subject_id,
        agent_id=agent_id,
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="caegroup.dt.pair.create",
        actor=user,
        target_table="cae_dt_pairs",
        target_id=row.id,
        target_label=f"{subject_id} - {agent_id}",
        workspace_id=workspace.id,
    )
    db.commit()
    db.refresh(row)
    return row


def unlink(db: Session, user: User, *, pair_id: uuid.UUID) -> None:
    """연계 해제. **평가도 함께 삭제된다** — 화면이 그 수를 확인 문구에 넣는다(2단계)."""
    row = db.get(CaeDtPair, pair_id)
    if row is None:
        raise NotFound(code("CAEGROUP", 6), "연계를 찾을 수 없습니다.")
    workspace = db.get(Workspace, row.workspace_id)
    if workspace is None:
        raise NotFound(code("CAEGROUP", 7), "부서를 찾을 수 없습니다.")
    permissions.require_member(db, workspace=workspace, user=user)
    audit.record(
        db,
        action="caegroup.dt.pair.delete",
        actor=user,
        target_table="cae_dt_pairs",
        target_id=row.id,
        target_label=f"{row.subject_id} - {row.agent_id}",
        workspace_id=row.workspace_id,
    )
    db.delete(row)
    db.commit()
