"""디지털 트윈 역량 — 기준 정보 설정과 연계.

**기준 정보는 온톨로지가 들고 있다.** 이 확장이 하는 일은 「어느 타입을 시험 항목 ·
시뮬레이션으로 쓸지」 를 기억하고, 없으면 만들어 주고, 그 객체들의 **연계**를 등록하는 것이다.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
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
AGENT_SLUG = "sim_analysis"
#: 도구 카탈로그(소프트웨어 제품)가 있는 설치에서는 해석이 그것을 가리킨다.
TOOL_SLUG = "sim_tool"

#: 기준 정보가 들어갈 사이드바 묶음.
#:
#: **묶음을 안 주면 타입이 메뉴에 안 뜬다.** 그러면 시험 항목을 넣을 자리를 주소로만 찾을 수
#: 있고, 그 사실은 화면 어디에도 안 적힌다 — 실측으로 그렇게 됐다(2026-09-24).
MASTER_GROUP = "dt_master"

#: 첫 설정이 만드는 정의. **가져오기(importer)로 넣는다** — 더하고 고치기만 하므로
#: 이미 있는 타입·속성은 건드리지 않고, 한 트랜잭션이라 반쯤 만들어진 정의가 안 남는다.
SETUP_SCHEMA: dict[str, Any] = {
    "groups": [
        {
            "slug": MASTER_GROUP,
            "label": "디지털 트윈 기준정보",
            "icon": "Database",
            "sort_order": 50,
        }
    ],
    "types": [
        {
            "slug": SUBJECT_SLUG,
            "label": "시험 항목",
            "icon": "ClipboardCheck",
            "nav_group_slug": MASTER_GROUP,
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
            "label": "시뮬레이션 해석",
            "icon": "Wrench",
            "nav_group_slug": MASTER_GROUP,
            "description": "디지털 트윈 역량 평가의 수단 — 시험을 대신 확인하는 해석.",
            "key_policy": "optional",
            "properties": [
                {"key": "kind", "label": "해석 종류", "data_type": "text"},
                {
                    "key": "model_kind",
                    "label": "모델 종류",
                    "data_type": "enum",
                    "enum_options": ["물리 기반", "데이터 기반", "하이브리드"],
                    "help": "동일 기준 집계를 위해 속성으로 관리합니다.",
                },
            ],
        },
    ],
}

#: 도구 카탈로그가 있을 때만 붙이는 참조 속성.
#:
#: **해석과 소프트웨어 제품은 다른 것이다.** 제품 타입(`sim_tool`)은 해석 분야 · 수치 기법 ·
#: 라이선스를 받는 카탈로그이고, 여기서 재는 수단은 「낙하 구조 해석」 처럼 **시험을 보는
#: 행위**다. 하나로 합치면 해석 하나를 적을 때마다 라이선스를 입력해야 하고, 같은 제품을
#: 쓰는 해석 열 개가 한 줄로 뭉쳐 「이 시험을 무엇으로 보나」 를 답할 수 없다.
TOOL_PROPERTY: dict[str, Any] = {
    "key": "tools",
    "label": "사용 도구",
    "data_type": "object_ref",
    "ref_type_slug": TOOL_SLUG,
    "multi": True,
    "inverse_label": "이 도구를 쓰는 해석",
    "help": "이 해석이 사용하는 소프트웨어입니다.",
}


def setup_schema(db: Session) -> dict[str, Any]:
    """이 설치에 맞춘 정의.

    도구 카탈로그가 없는 설치에서는 참조 속성을 빼고 만든다 — 없는 타입을 가리키는
    속성은 가져오기가 거절하고, 그러면 기준 정보 생성이 통째로 막힌다.
    """
    schema = deepcopy(SETUP_SCHEMA)
    if db.scalar(select(ObjectType).where(ObjectType.slug == TOOL_SLUG)) is not None:
        agent = next(one for one in schema["types"] if one["slug"] == AGENT_SLUG)
        agent["properties"].append(deepcopy(TOOL_PROPERTY))
    return schema


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
    plan = importer.apply(db, setup_schema(db))
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
    """연계 목록 — 화면의 왼쪽 표. **이름은 객체에서 온다.**

    **조회는 부서로 가리지 않는다.** 이 화면이 답하는 물음은 「전사 역량이 지금 어디까지
    왔나」 이고, 그것은 조직을 가로지른다 — 자기 부서 것만 보이면 그 물음에 아무도 답할 수
    없다(`shared/permissions.open_owner_clause` 가 같은 까닭으로 있다). 홈 부서로 걸어
    두었을 때 다른 부서에 등록한 연계가 화면에서 사라졌고, 사람은 그것을 「저장이 안
    됐다」 로 읽었다(실측 2026-09-24).

    **고치는 것은 부서 멤버만**이다(`link` · `unlink`) — 보는 것과 고치는 것은 다른 물음이다.
    """
    stmt = select(CaeDtPair)
    if workspace is not None:
        # 부서를 주면 그 부서만 — 가리는 것이 아니라 좁혀 보는 것이다.
        stmt = stmt.where(CaeDtPair.workspace_id == workspace.id)
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
