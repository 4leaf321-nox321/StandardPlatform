"""디지털 트윈 역량 — 기준 정보 설정과 연계.

**기준 정보는 온톨로지가 들고 있다.** 이 확장이 하는 일은 「어느 타입을 시험 항목 ·
시뮬레이션으로 쓸지」 를 기억하고, 없으면 만들어 주고, 그 객체들의 **연계**를 등록하는 것이다.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.extensions.caegroup import definitions as D
from app.extensions.caegroup.models import (
    CaeDtAssessment,
    CaeDtAssessmentHistory,
    CaeDtCapacity,
    CaeDtPair,
    CaeDtSetting,
    CaeDtStaff,
)
from app.modules.accounts.models import User
from app.modules.objects import system
from app.modules.objects.models import ObjectInstance
from app.modules.ontology import importer
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.workspaces.models import Workspace
from app.shared import audit, permissions
from app.shared import extensions as extension_points
from app.shared.errors import AppError, Conflict, NotFound, code

SUBJECT_SLUG = "sim_test_item"
AGENT_SLUG = "sim_analysis"
#: 도구 카탈로그(소프트웨어 제품)가 있는 설치에서는 해석이 그것을 가리킨다.
TOOL_SLUG = "sim_tool"
#: 부서를 비추는 타입이 있는 설치에서는 해석이 담당 부서를 가리킨다.
DEPT_SLUG = "dept"

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


#: 담당 부서 — **누가 이 해석을 들고 있나.** 없으면 낮은 수준이 「누구의 일인지」 조차
#: 안 보이고, 그때 그 숫자는 아무도 자기 것으로 읽지 않는다.
DEPT_PROPERTY: dict[str, Any] = {
    "key": "owner_dept",
    "label": "담당 부서",
    "data_type": "object_ref",
    "ref_type_slug": DEPT_SLUG,
    "inverse_label": "이 부서가 담당하는 해석",
}


def setup_schema(db: Session) -> dict[str, Any]:
    """이 설치에 맞춘 정의.

    도구 카탈로그가 없는 설치에서는 참조 속성을 빼고 만든다 — 없는 타입을 가리키는
    속성은 가져오기가 거절하고, 그러면 기준 정보 생성이 통째로 막힌다.
    """
    schema = deepcopy(SETUP_SCHEMA)
    agent = next(one for one in schema["types"] if one["slug"] == AGENT_SLUG)
    for slug, extra in ((TOOL_SLUG, TOOL_PROPERTY), (DEPT_SLUG, DEPT_PROPERTY)):
        if db.scalar(select(ObjectType).where(ObjectType.slug == slug)) is not None:
            agent["properties"].append(deepcopy(extra))
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


def _as_list(raw: Any) -> list[Any]:
    """참조 속성은 하나일 수도 여럿일 수도 있다 — 읽는 쪽을 한 모양으로 만든다."""
    if raw is None or raw == "":
        return []
    return list(raw) if isinstance(raw, list) else [raw]


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
    # 해석이 가리키는 도구 · 담당 부서의 **이름**까지 한 번에 낸다. 화면이 참조마다 다시
    # 물으면 줄 수만큼 왕복이 생기고, 그 느림은 목록이 길어진 뒤에야 드러난다.
    #
    # **이름은 플랫폼의 해석기가 찾는다**(`objects/system.ref_labels`). 부서처럼 다른 표를
    # 비추는 타입은 객체 표에 행이 없다 — 직접 찾으면 이름이 영영 비어 있고, 그 사실은
    # 화면에서 「—」 로만 보인다(실측 2026-09-24).
    agent_type = _type_or_none(db, setting(db).agent_type_slug)
    agent_defs = (
        list(
            db.scalars(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "type", PropertyDef.owner_id == agent_type.id
                )
            )
        )
        if agent_type is not None
        else []
    )
    # `ref_labels` 는 **속성 사전 자체**를 받는다(`values.get(key)`).
    agent_rows = [
        dict(one.properties or {})
        for one in names.values()
        if agent_type is not None and one.type_id == agent_type.id
    ]
    ref_names = {
        str(key): value for key, value in system.ref_labels(db, agent_defs, agent_rows).items()
    }
    workspaces = {
        one.id: one.name
        for one in db.scalars(
            select(Workspace).where(Workspace.id.in_({one.workspace_id for one in rows}))
        )
    }
    counts = assessed_counts(db, pair_ids=[one.id for one in rows])
    out: list[dict[str, Any]] = []
    for one in rows:
        subject = names.get(one.subject_id)
        agent = names.get(one.agent_id)
        props = (agent.properties or {}) if agent else {}
        out.append(
            {
                "id": one.id,
                "workspace_id": one.workspace_id,
                "workspace_name": workspaces.get(one.workspace_id, ""),
                "subject_id": one.subject_id,
                "subject_label": subject.label if subject else "(지워짐)",
                "agent_id": one.agent_id,
                "agent_label": agent.label if agent else "(지워짐)",
                "agent_tools": [
                    ref_names.get(str(value), "") for value in _as_list(props.get("tools"))
                ],
                "agent_dept": next(
                    (ref_names.get(str(value)) for value in _as_list(props.get("owner_dept"))),
                    None,
                ),
                "assessed": counts.get(one.id, 0),
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


def move(db: Session, user: User, *, pair_id: uuid.UUID, workspace: Workspace) -> CaeDtPair:
    """연계의 **소속 부서를 옮긴다.**

    연계에서 고칠 수 있는 것은 이것뿐이다. 시험 항목이나 해석을 바꾸는 것은 **다른 연계**를
    뜻하는데, 그때 이미 매긴 평가가 엉뚱한 대상의 평가로 남는다 — 그 손실은 숫자에서만
    드러난다. 대상 · 수단을 바꾸려면 해제하고 다시 등록한다(평가가 함께 사라진다는 것을
    확인 문구가 말한다).

    **양쪽 부서 멤버여야 한다.** 남의 부서로 밀어 넣거나 남의 자료를 가져오는 일이 한쪽
    권한으로 되면, 그 부서는 자기 숫자를 설명할 수 없게 된다.
    """
    row = _pair_or_404(db, pair_id)
    if row.workspace_id == workspace.id:
        return row
    before = db.get(Workspace, row.workspace_id)
    if before is None:
        raise NotFound(code("CAEGROUP", 7), "부서를 찾을 수 없습니다.")
    permissions.require_member(db, workspace=before, user=user)
    permissions.require_member(db, workspace=workspace, user=user)
    audit.record(
        db,
        action="caegroup.dt.pair.move",
        actor=user,
        target_table="cae_dt_pairs",
        target_id=row.id,
        target_label=f"{row.subject_id} - {row.agent_id}",
        workspace_id=workspace.id,
        changes={"from": before.slug, "to": workspace.slug},
    )
    row.workspace_id = workspace.id
    db.commit()
    db.refresh(row)
    return row


def bulk_move(
    db: Session, user: User, *, pair_ids: list[uuid.UUID], workspace: Workspace
) -> int:
    """고른 연계를 한 부서로 옮긴다 — **한 건씩 같은 규칙으로.**

    목록에서 서른 건을 고른 사람에게 서른 번을 누르게 하지 않는다. 다만 권한은 건마다
    본다 — 「여럿이라서 한 번에 통과」 가 되면 그 예외가 곧 규칙이 된다.
    """
    moved = 0
    for one in pair_ids:
        before = db.get(CaeDtPair, one)
        if before is None or before.workspace_id == workspace.id:
            continue
        move(db, user, pair_id=one, workspace=workspace)
        moved += 1
    return moved


def bulk_unlink(db: Session, user: User, *, pair_ids: list[uuid.UUID]) -> int:
    """고른 연계를 해제한다.

    **평가와 이력도 함께 간다** — 화면이 그 수를 확인 문구에 넣는다.
    """
    gone = 0
    for one in pair_ids:
        if db.get(CaeDtPair, one) is None:
            continue
        unlink(db, user, pair_id=one)
        gone += 1
    return gone


def assessed_counts(db: Session, *, pair_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """연계마다 **몇 개 축을 매겼나** — 목록이 「어디까지 채웠나」 를 바로 보여 준다."""
    if not pair_ids:
        return {}
    rows = db.execute(
        select(CaeDtAssessment.pair_id, func.count())
        .where(CaeDtAssessment.pair_id.in_(pair_ids))
        .group_by(CaeDtAssessment.pair_id)
    )
    return {one: int(count) for one, count in rows}


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


# ── 평가 ────────────────────────────────────────────────────────────────


def _pair_or_404(db: Session, pair_id: uuid.UUID) -> CaeDtPair:
    row = db.get(CaeDtPair, pair_id)
    if row is None:
        raise NotFound(code("CAEGROUP", 6), "연계를 찾을 수 없습니다.")
    return row


def _axis_or_404(axis: str) -> dict[str, Any]:
    found = D.AXIS_BY_KEY.get(axis)
    if found is None:
        raise NotFound(code("CAEGROUP", 8), f"이 부문에 없는 축입니다: {axis}")
    return found


def _out(row: CaeDtAssessment) -> dict[str, Any]:
    return {
        "axis": row.axis,
        "value": row.value,
        "rung": row.rung,
        "rungs": list(row.rungs or []),
        "defects": dict(row.defects or {}),
        "note": row.note,
        "evidence": dict(row.evidence or {}),
        "assessed_at": row.assessed_at,
        "assessed_by_label": row.assessed_by_label,
    }


def assessments(db: Session, *, pair_id: uuid.UUID) -> list[dict[str, Any]]:
    """그 연계의 평가 — **축 순서대로.** 아직 안 매긴 축은 목록에 없다.

    빈 줄을 만들어 내려 주지 않는다 — 「안 매긴 것」 과 「0 으로 매긴 것」 을 화면이 구별할
    수 있어야 한다.
    """
    _pair_or_404(db, pair_id)
    rows = {
        one.axis: one
        for one in db.scalars(
            select(CaeDtAssessment).where(CaeDtAssessment.pair_id == pair_id)
        )
    }
    return [_out(rows[key]) for key in D.AXIS_KEYS if key in rows]


def history(db: Session, *, pair_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
    """평가가 바뀐 기록 — 최근 것부터."""
    _pair_or_404(db, pair_id)
    rows = db.scalars(
        select(CaeDtAssessmentHistory)
        .where(CaeDtAssessmentHistory.pair_id == pair_id)
        .order_by(CaeDtAssessmentHistory.changed_at.desc())
        .limit(limit)
    )
    return [
        {
            "axis": one.axis,
            "axis_label": D.AXIS_BY_KEY.get(one.axis, {}).get("label", one.axis),
            "snapshot": dict(one.snapshot or {}),
            "changed_at": one.changed_at,
            "changed_by_label": one.changed_by_label,
        }
        for one in rows
    ]


def _check_note(axis: dict[str, Any], payload: dict[str, Any]) -> str:
    """근거 — **비우면 저장하지 않는다.**

    수준만 남은 평가는 다음 사람이 확인할 방법이 없다. 「누가 언젠가 그렇게 봤다」 는 말과
    같아지고, 그 숫자는 다음 회차에 아무도 못 고친다.

    등급 · 자료 칸은 두었다가 걷었다(2026-09-24) — 칸이 늘수록 채우는 사람이 줄고, 안 채운
    칸은 「모름」 과 구별되지 않는다. 무엇을 보고 매겼는지는 이 글에 적는다.
    """
    note = str(payload.get("note") or "").strip()
    if not note:
        raise Conflict(code("CAEGROUP", 9), f"{axis['label']}: 근거를 적어야 저장됩니다.")
    return note


def _apply_axis(
    axis: dict[str, Any],
    row: CaeDtAssessment,
    payload: dict[str, Any],
    *,
    defect_types: list[str],
) -> None:
    """축 종류마다 채우는 칸이 다르다 — 그 갈림을 **한 곳**에서 한다."""
    allowed = set(D.rung_keys(axis["key"]))
    kind = axis["kind"]
    row.value, row.rung, row.rungs, row.defects = None, None, [], {}

    if kind == "value":
        raw = payload.get("value")
        if raw is None:
            raise Conflict(code("CAEGROUP", 12), f"{axis['label']}: 값을 적어야 합니다.")
        value = float(raw)
        if not 0 <= value <= 100:
            raise Conflict(code("CAEGROUP", 13), f"{axis['label']}: 0 ~ 100 사이여야 합니다.")
        row.value = value
        # **수준은 문턱이 정한다.** 화면이 보내는 수준은 받지 않는다 — 값과 수준을 따로
        # 받으면 값을 고쳐도 수준이 안 따라오고, 그때 가상검증률이 둘이 된다.
        row.rung = D.rung_for_value(value)
    elif kind == "rung":
        chosen = str(payload.get("rung") or "")
        if chosen not in allowed:
            raise Conflict(code("CAEGROUP", 14), f"{axis['label']}: 수준을 고르세요.")
        row.rung = chosen
    elif kind == "set":
        picked = [one for one in (payload.get("rungs") or []) if one in allowed]
        if not picked:
            raise Conflict(code("CAEGROUP", 15), f"{axis['label']}: 하나 이상 고르세요.")
        # 정의에 적힌 순서로 담는다 — 고른 순서대로 두면 같은 평가가 화면마다 다르게 보인다.
        row.rungs = [one for one in D.rung_keys(axis["key"]) if one in picked]
    else:  # matrix — 바탕 토글 + 불량 유형별 재현. **수준은 셈으로 접는다.**
        base = {one["key"] for one in axis.get("base", [])}
        row.rungs = [one for one in (payload.get("rungs") or []) if one in base]
        columns = {one["key"] for one in axis.get("columns", [])}
        defects = {
            str(name): {
                key: value for key, value in (marks or {}).items() if key in columns and value
            }
            for name, marks in (payload.get("defects") or {}).items()
        }
        row.defects = {name: marks for name, marks in defects.items() if marks}
        if not row.rungs and not row.defects:
            raise Conflict(
                code("CAEGROUP", 16),
                f"{axis['label']}: 바탕(형상 · 거동)을 켜거나 불량 유형의 재현을 표시하세요.",
            )
        row.rung = D.modeling_level(row.rungs, row.defects, defect_types)


def _defect_types(db: Session, pair: CaeDtPair) -> list[str]:
    """모델링 수준의 셈 기준 — **시험 항목이 든 불량 유형 목록.**

    시뮬레이션이 아니라 시험에 붙는다. 수단에 두면 같은 시험인데 도구마다 목록이 갈려
    「이 시험의 불량 중 아직 아무 데서도 재현 안 되는 것」 을 셀 수 없다.
    """
    subject = db.get(ObjectInstance, pair.subject_id)
    raw = (subject.properties or {}).get("defect_types") if subject else None
    if isinstance(raw, list):
        return [str(one) for one in raw]
    return [str(raw)] if isinstance(raw, str) and raw else []


def save_assessment(
    db: Session, user: User, *, pair_id: uuid.UUID, axis_key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """평가를 적는다 — **그 부서 멤버만.** 바뀌면 이력에 한 줄 남는다."""
    pair = _pair_or_404(db, pair_id)
    workspace = db.get(Workspace, pair.workspace_id)
    if workspace is None:
        raise NotFound(code("CAEGROUP", 7), "부서를 찾을 수 없습니다.")
    permissions.require_member(db, workspace=workspace, user=user)
    axis = _axis_or_404(axis_key)
    note = _check_note(axis, payload)

    row = db.scalar(
        select(CaeDtAssessment).where(
            CaeDtAssessment.pair_id == pair_id, CaeDtAssessment.axis == axis_key
        )
    )
    fresh = row is None
    if row is None:
        row = CaeDtAssessment(pair_id=pair_id, axis=axis_key)
        db.add(row)
    _apply_axis(axis, row, payload, defect_types=_defect_types(db, pair))
    row.note = note
    row.evidence = dict(payload.get("evidence") or {})
    row.assessed_by_id = user.id
    row.assessed_by_label = user.display_name or user.email
    db.flush()

    db.add(
        CaeDtAssessmentHistory(
            pair_id=pair_id,
            axis=axis_key,
            snapshot=_history_snapshot(row),
            changed_by_id=user.id,
            changed_by_label=row.assessed_by_label,
        )
    )
    audit.record(
        db,
        action="caegroup.dt.assessment.save",
        actor=user,
        target_table="cae_dt_assessments",
        target_id=row.id,
        target_label=f"{pair_id} · {axis_key}",
        workspace_id=pair.workspace_id,
        changes={"axis": axis_key, "new": fresh},
    )
    db.commit()
    db.refresh(row)
    return _out(row)


def _history_snapshot(row: CaeDtAssessment) -> dict[str, Any]:
    """이력에 남길 한 벌 — **값과 근거만.** 사람 이름은 줄 자신이 들고 있다."""
    return {
        "value": row.value,
        "rung": row.rung,
        "rungs": list(row.rungs or []),
        "defects": dict(row.defects or {}),
        "note": row.note,
        "evidence": dict(row.evidence or {}),
    }


def board(db: Session, user: User, *, limit_recent: int = 12) -> dict[str, Any]:
    """대시보드가 그리는 한 벌 — **연계마다 축의 수준**과 최근 변경.

    **분포와 타일은 같은 자료에서 나온다.** 서버가 분포를 따로 세어 내려 주면 타일과
    분포가 갈릴 수 있고, 그때 어느 쪽이 맞는지 아무도 모른다 — 화면이 이 목록 하나로
    둘을 그린다.
    """
    rows = pairs(db, user, workspace=None)
    by_pair: dict[uuid.UUID, dict[str, Any]] = {}
    ids = [one["id"] for one in rows]
    if ids:
        for one in db.scalars(select(CaeDtAssessment).where(CaeDtAssessment.pair_id.in_(ids))):
            by_pair.setdefault(one.pair_id, {})[one.axis] = {
                "rung": one.rung,
                "rungs": list(one.rungs or []),
                "value": one.value,
                "note": one.note,
            }
    tiles = [
        {
            **one,
            # 묶음은 **담당 부서**다 — 없으면 소속 부서. 「누가 들고 있나」 로 묶어야
            # 벽이 조직의 그림이 된다.
            "group": one["agent_dept"] or one["workspace_name"] or "(부서 없음)",
            "levels": by_pair.get(one["id"], {}),
        }
        for one in rows
    ]
    recent = (
        [
            {
                "pair_id": one.pair_id,
                "axis": one.axis,
                "axis_label": D.AXIS_BY_KEY.get(one.axis, {}).get("label", one.axis),
                "label": next(
                    (
                        f"{row['subject_label']} · {row['agent_label']}"
                        for row in rows
                        if row["id"] == one.pair_id
                    ),
                    "(지워짐)",
                ),
                "note": (one.snapshot or {}).get("note", ""),
                "changed_at": one.changed_at,
                "changed_by_label": one.changed_by_label,
            }
            for one in db.scalars(
                select(CaeDtAssessmentHistory)
                .where(CaeDtAssessmentHistory.pair_id.in_(ids))
                .order_by(CaeDtAssessmentHistory.changed_at.desc())
                .limit(limit_recent)
            )
        ]
        if ids
        else []
    )
    return {"tiles": tiles, "recent": recent}


def coverage(db: Session, *, workspace: Workspace | None = None) -> dict[str, Any]:
    """축마다 **평가 완료율** — 평가된 연계 ÷ 전체 연계.

    3단계 대시보드가 이것으로 그린다. 여기 두는 이유는 화면 둘이 같은 셈을 두 번 하지
    않게 하려는 것이다 — 두 번 하면 둘이 갈리고, 그때 어느 쪽이 맞는지 아무도 모른다.
    """
    pair_stmt = select(CaeDtPair.id)
    if workspace is not None:
        pair_stmt = pair_stmt.where(CaeDtPair.workspace_id == workspace.id)
    ids = set(db.scalars(pair_stmt))
    total = len(ids)
    done: dict[str, int] = {key: 0 for key in D.AXIS_KEYS}
    if ids:
        for axis_key, count in db.execute(
            select(CaeDtAssessment.axis, func.count())
            .where(CaeDtAssessment.pair_id.in_(ids))
            .group_by(CaeDtAssessment.axis)
        ):
            if axis_key in done:
                done[axis_key] = int(count)
    return {
        "pairs": total,
        "axes": [
            {
                "axis": key,
                "label": D.AXIS_BY_KEY[key]["label"],
                "assessed": done[key],
                "ratio": round(done[key] / total, 3) if total else 0.0,
            }
            for key in D.AXIS_KEYS
        ],
    }


# ── 인력 ────────────────────────────────────────────────────────────────

#: 가명 — **표에 서는 이름.** 실명은 고칠 수 있는 사람에게만 보인다.
ALIAS_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _alias(index: int) -> str:
    """담당 A · B · … Z · AA. 부서 안에서 만든 순서대로."""
    if index < len(ALIAS_LETTERS):
        return f"담당 {ALIAS_LETTERS[index]}"
    first, second = divmod(index, len(ALIAS_LETTERS))
    return f"담당 {ALIAS_LETTERS[first - 1]}{ALIAS_LETTERS[second]}"


def _may_see_names(db: Session, user: User, workspace_id: uuid.UUID) -> bool:
    """실명을 볼 수 있나 — **그 부서를 고칠 수 있는 사람과 시스템 관리자만.**

    사람을 세는 자리이지 사람을 평가하는 자리가 아니다. 전사에 실명을 열면 이 표는
    「누가 느린가」 로 읽히고, 그때 부서는 자료를 방어적으로 적는다.
    """
    if user.is_system_admin:
        return True
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        return False
    return (
        permissions.membership_of(db, workspace_id=workspace.id, user_id=user.id) is not None
    )


def _staff_rows(db: Session, *, workspace: Workspace | None) -> list[CaeDtStaff]:
    stmt = select(CaeDtStaff).order_by(CaeDtStaff.workspace_id, CaeDtStaff.created_at)
    if workspace is not None:
        stmt = stmt.where(CaeDtStaff.workspace_id == workspace.id)
    return list(db.scalars(stmt))


def _share_of(row: CaeDtStaff) -> float:
    """한 사람이 담당 해석 **하나**에 주는 몫 — 반올림하지 않은 값.

    **한 사람은 1.0 이다.** 담당이 n 개면 각 1/n 이고, 이 조사 밖 업무가 있으면 n+1 로
    나눈다 — 없는 일까지 이 조사에 넣지 않는다. 담당이 없으면(지원 조직) 0 이다:
    그 사람의 역량은 종류로 세고 FTE 로는 세지 않는다.

    ⚠️ **반올림은 내보낼 때 한 번만 한다.** 몫을 먼저 반올림해 더하면 담당 셋짜리 한
       사람이 0.9999 가 되고, 그 숫자를 본 사람은 「왜 1 이 아니지」 를 묻는다(실측).
    """
    parts = len(row.agents or []) + (1 if row.outside else 0)
    if not row.agents or not parts:
        return 0.0
    return 1 / parts


def staff(db: Session, user: User, *, workspace: Workspace | None) -> list[dict[str, Any]]:
    """인력 목록 — **가명이 기본, 실명은 권한이 있을 때만.**

    `_share` 는 반올림하지 않은 몫이다(집계가 쓴다). 응답 스키마에 없으므로 바깥으로
    나가지 않는다 — 나가면 화면이 그 값을 다시 반올림해 표와 합계가 갈린다.
    """
    rows = _staff_rows(db, workspace=workspace)
    agent_ids = {
        uuid.UUID(str(one)) for row in rows for one in (row.agents or []) if str(one).strip()
    }
    labels = (
        {
            str(one.id): one.label
            for one in db.scalars(
                select(ObjectInstance).where(ObjectInstance.id.in_(agent_ids))
            )
        }
        if agent_ids
        else {}
    )
    workspaces = {
        one.id: one.name
        for one in db.scalars(
            select(Workspace).where(Workspace.id.in_({row.workspace_id for row in rows}))
        )
    }
    seen: dict[uuid.UUID, int] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        index = seen.get(row.workspace_id, 0)
        seen[row.workspace_id] = index + 1
        share = _share_of(row)
        out.append(
            {
                "id": row.id,
                "workspace_id": row.workspace_id,
                "workspace_name": workspaces.get(row.workspace_id, ""),
                "alias": _alias(index),
                "name": row.name if _may_see_names(db, user, row.workspace_id) else None,
                "agents": [
                    {"id": str(one), "label": labels.get(str(one), "(지워짐)")}
                    for one in (row.agents or [])
                ],
                "skill_kinds": list(row.skill_kinds or []),
                "outside": row.outside,
                "note": row.note,
                "share": round(share, 4),
                # 사람의 몫은 **나눈 값을 그대로 곱한 뒤** 한 번만 반올림한다.
                "fte": round(share * len(row.agents or []), 4),
                "_share": share,
            }
        )
    return out


def staff_summary(db: Session, user: User, *, workspace: Workspace | None) -> dict[str, Any]:
    """사람 수 · FTE 합 · 해석별 FTE · 종류별 사람 수.

    **파생값은 저장하지 않는다** — 물을 때마다 인력 줄에서 다시 센다. 표를 하나 더 두면
    줄과 어긋나는 순간 어느 쪽이 참인지 말할 수 없다.
    """
    rows = staff(db, user, workspace=workspace)
    by_agent: dict[str, dict[str, Any]] = {}
    by_kind: dict[str, int] = {}
    for one in rows:
        for agent in one["agents"]:
            bucket = by_agent.setdefault(
                agent["id"],
                {"id": agent["id"], "label": agent["label"], "fte": 0.0, "people": 0},
            )
            bucket["fte"] += float(one["_share"])
            bucket["people"] += 1
        for kind in one["skill_kinds"]:
            by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "head_count": len(rows),
        # **합이 사람 수를 넘지 않는다.** 한 사람의 몫은 담당에 갈려 들어가고, 조사 밖
        # 업무가 있는 사람은 그만큼 덜 잡힌다.
        "fte": round(sum(float(one["_share"]) * len(one["agents"]) for one in rows), 4),
        "by_agent": sorted(
            ({**one, "fte": round(one["fte"], 4)} for one in by_agent.values()),
            key=lambda one: -one["fte"],
        ),
        "by_kind": [{"kind": key, "people": value} for key, value in sorted(by_kind.items())],
    }


def save_staff(
    db: Session,
    user: User,
    *,
    staff_id: uuid.UUID | None,
    workspace: Workspace,
    payload: dict[str, Any],
) -> uuid.UUID:
    """인력 줄을 넣거나 고친다 — **그 부서 멤버만.**"""
    permissions.require_member(db, workspace=workspace, user=user)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise Conflict(code("CAEGROUP", 18), "이름을 적어야 합니다(화면에는 가명으로 섭니다).")
    row = db.get(CaeDtStaff, staff_id) if staff_id else None
    if staff_id and row is None:
        raise NotFound(code("CAEGROUP", 19), "인력 줄을 찾을 수 없습니다.")
    if row is None:
        row = CaeDtStaff(workspace_id=workspace.id)
        db.add(row)
    row.name = name
    row.agents = [str(one) for one in (payload.get("agents") or [])]
    row.skill_kinds = [str(one) for one in (payload.get("skill_kinds") or [])]
    row.outside = bool(payload.get("outside"))
    row.note = str(payload.get("note") or "").strip()
    db.flush()
    audit.record(
        db,
        action="caegroup.dt.staff.save",
        actor=user,
        target_table="cae_dt_staff",
        target_id=row.id,
        # **감사에도 실명을 안 남긴다.** 세는 자리이지 평가하는 자리가 아니다.
        target_label=f"인력 {len(row.agents)}담당",
        workspace_id=workspace.id,
        changes={"agents": len(row.agents), "outside": row.outside},
    )
    db.commit()
    return row.id


def delete_staff(db: Session, user: User, *, staff_id: uuid.UUID) -> None:
    row = db.get(CaeDtStaff, staff_id)
    if row is None:
        raise NotFound(code("CAEGROUP", 19), "인력 줄을 찾을 수 없습니다.")
    workspace = db.get(Workspace, row.workspace_id)
    if workspace is None:
        raise NotFound(code("CAEGROUP", 7), "부서를 찾을 수 없습니다.")
    permissions.require_member(db, workspace=workspace, user=user)
    audit.record(
        db,
        action="caegroup.dt.staff.delete",
        actor=user,
        target_table="cae_dt_staff",
        target_id=row.id,
        target_label=f"인력 {len(row.agents or [])}담당",
        workspace_id=workspace.id,
    )
    db.delete(row)
    db.commit()


# ── 인프라 ──────────────────────────────────────────────────────────────


def capacity(db: Session, *, workspace: Workspace) -> dict[str, Any]:
    """그 부서의 인프라 한 줄 — 없으면 빈 줄을 그려 준다(만들지는 않는다)."""
    row = db.scalar(select(CaeDtCapacity).where(CaeDtCapacity.workspace_id == workspace.id))
    return {
        "workspace_id": workspace.id,
        "workspace_name": workspace.name,
        "sw": list(row.sw or []) if row else [],
        "hw": list(row.hw or []) if row else [],
        "material_types": row.material_types if row else None,
        "has_process_std": row.has_process_std if row else False,
        "note": row.note if row else "",
    }


def save_capacity(
    db: Session, user: User, *, workspace: Workspace, payload: dict[str, Any]
) -> dict[str, Any]:
    """인프라를 적는다 — **그 부서 멤버만.**"""
    permissions.require_member(db, workspace=workspace, user=user)
    row = db.scalar(select(CaeDtCapacity).where(CaeDtCapacity.workspace_id == workspace.id))
    if row is None:
        row = CaeDtCapacity(workspace_id=workspace.id)
        db.add(row)
    row.sw = [dict(one) for one in (payload.get("sw") or [])]
    row.hw = [dict(one) for one in (payload.get("hw") or [])]
    row.material_types = payload.get("material_types")
    row.has_process_std = bool(payload.get("has_process_std"))
    row.note = str(payload.get("note") or "").strip()
    db.flush()
    audit.record(
        db,
        action="caegroup.dt.capacity.save",
        actor=user,
        target_table="cae_dt_capacity",
        target_id=row.id,
        target_label=workspace.name,
        workspace_id=workspace.id,
        changes={"sw": len(row.sw), "hw": len(row.hw)},
    )
    db.commit()
    return capacity(db, workspace=workspace)


def capacity_summary(db: Session) -> dict[str, Any]:
    """전사 합계 — **공유 자원은 한 번만 센다.**

    부서마다 적힌 공유 라이선스 · 공유 계산 자원을 그대로 더하면 전사 합이 실제보다
    커지고, 그 숫자로 투자를 판단하면 이미 있는 것을 또 산다.
    """
    rows = list(db.scalars(select(CaeDtCapacity)))
    sw_total: dict[str, float] = {}
    shared_seen: set[str] = set()
    hw_cores = 0
    hw_ram = 0
    hw_gpu = 0
    hw_shared_seen: set[str] = set()
    materials = 0

    def as_number(raw: Any) -> float:
        """숫자로 못 읽는 값은 0 으로 본다 — **합계 때문에 화면이 죽지 않게.**

        사람이 적는 칸이라 「256(예정)」 처럼 들어온다. 그것을 숫자로 강제하면 합계가
        500 을 내고, 그때 사람은 자기가 적은 칸 때문인 줄 모른다 — 실측으로 GPU 칸의
        「A100 4장」 가 그렇게 터졌다(2026-09-25).
        """
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return 0.0

    for row in rows:
        for one in row.sw or []:
            name = str(one.get("name") or "")
            amount = float(one.get("quantity") or 0)
            if one.get("shared"):
                if name in shared_seen:
                    continue
                shared_seen.add(name)
            sw_total[name] = sw_total.get(name, 0.0) + amount
        for one in row.hw or []:
            name = str(one.get("name") or "")
            if one.get("shared"):
                if name in hw_shared_seen:
                    continue
                hw_shared_seen.add(name)
            hw_cores += int(as_number(one.get("cpu_cores")))
            hw_ram += int(as_number(one.get("ram_gb")))
            # **GPU 는 글이다**(「A100 4장」). 원본도 사양을 글로 적는다 — 숫자로 강제하면
            # 사람이 적을 수 있는 것을 못 적게 만든다. 그래서 개수를 더하지 않고
            # **GPU 를 가진 자원이 몇인지** 센다.
            if str(one.get("gpu") or "").strip():
                hw_gpu += 1
        materials += int(as_number(row.material_types))
    return {
        "departments": len(rows),
        "sw": [
            {"name": key, "quantity": round(value, 2)}
            for key, value in sorted(sw_total.items(), key=lambda one: -one[1])
        ],
        "hw": {"cpu_cores": hw_cores, "ram_gb": hw_ram, "gpu_units": hw_gpu},
        "material_types": materials,
        "process_std": sum(1 for row in rows if row.has_process_std),
    }


# ── 일괄 입력 ───────────────────────────────────────────────────────────
#
# **현재값을 내려 주고, 고쳐서 한 번에 되돌려 받는다.** 한 줄씩 창을 열어 고치는 길만
# 있으면 스무 건이 넘는 순간 아무도 최신으로 유지하지 않는다 — 그리고 안 채운 자료는
# 「모름」 과 구별되지 않는다.


def _rung_by_name(axis: dict[str, Any]) -> dict[str, str]:
    """사람이 읽는 이름 → 수준 key. **엑셀에는 이름이 적힌다** — key 를 적게 하면 안 된다."""
    out: dict[str, str] = {}
    for one in axis["rungs"]:
        out[str(one["label"]).strip()] = one["key"]
        out[str(one["key"]).strip()] = one["key"]
        if one.get("short"):
            out[str(one["short"]).strip()] = one["key"]
    return out


def sheet(db: Session, user: User, *, axis_key: str) -> dict[str, Any]:
    """축 하나의 **현재값 표** — 연계마다 한 줄.

    화면은 이것을 그대로 표에 채우고(현재값 불러오기), 사람이 고친 뒤 한 번에 되돌려 준다.
    """
    axis = _axis_or_404(axis_key)
    rows = pairs(db, user, workspace=None)
    ids = [one["id"] for one in rows]
    saved: dict[uuid.UUID, CaeDtAssessment] = {}
    if ids:
        saved = {
            one.pair_id: one
            for one in db.scalars(
                select(CaeDtAssessment).where(
                    CaeDtAssessment.pair_id.in_(ids), CaeDtAssessment.axis == axis_key
                )
            )
        }
    names = {one["key"]: one["label"] for one in axis["rungs"]}
    out: list[dict[str, Any]] = []
    for one in rows:
        row = saved.get(one["id"])
        out.append(
            {
                "pair_id": one["id"],
                "subject_label": one["subject_label"],
                "agent_label": one["agent_label"],
                "agent_dept": one["agent_dept"],
                "workspace_name": one["workspace_name"],
                "value": row.value if row else None,
                # **수준은 이름으로 준다.** 엑셀에서 사람이 읽고 고치는 값이다.
                "rung": names.get(row.rung or "", "") if row else "",
                "rungs": [names.get(key, key) for key in (row.rungs or [])] if row else [],
                "note": row.note if row else "",
            }
        )
    return {"axis": axis_key, "axis_label": axis["label"], "kind": axis["kind"], "rows": out}


def bulk_save(
    db: Session, user: User, *, axis_key: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """표를 한 번에 저장한다 — **줄마다 같은 규칙, 줄마다 결과.**

    ⚠️ **빈 값은 건너뛴다**(지우기가 아니다). 엑셀에서 일부만 채워 보내는 일이 흔한데,
       빈 칸을 「지움」 으로 읽으면 한 번의 붙여넣기가 남의 평가를 지운다.
    ⚠️ 한 줄이 틀려도 나머지는 저장한다. 통째로 되돌리면 스무 줄 중 하나의 오타가 열아홉
       줄의 일을 없앤다 — 대신 **어느 줄이 왜 막혔는지** 돌려준다.
    """
    axis = _axis_or_404(axis_key)
    known = pairs(db, user, workspace=None)
    by_id = {str(one["id"]): one for one in known}
    by_label = {(one["subject_label"], one["agent_label"]): one for one in known}
    by_name = _rung_by_name(axis)
    # 매트릭스는 이 표에서 **바탕(형상 · 거동)만** 고친다. 저장은 축 한 줄을 통째로 다시
    # 쓰기 때문에, 지금 든 불량 유형별 재현을 같이 넘기지 않으면 바탕을 고치는 일이
    # 「역량」 화면에서 채운 재현 표시를 지운다.
    held: dict[uuid.UUID, dict[str, Any]] = {}
    if axis["kind"] == "matrix" and known:
        held = {
            one.pair_id: dict(one.defects or {})
            for one in db.scalars(
                select(CaeDtAssessment).where(
                    CaeDtAssessment.pair_id.in_([one["id"] for one in known]),
                    CaeDtAssessment.axis == axis_key,
                )
            )
        }

    results: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        found = by_id.get(str(raw.get("pair_id") or "")) or by_label.get(
            (
                str(raw.get("subject_label") or "").strip(),
                str(raw.get("agent_label") or "").strip(),
            )
        )
        if found is None:
            results.append(
                {
                    "line": index + 1,
                    "status": "error",
                    "message": "연계를 찾을 수 없습니다 — 시험 항목과 해석 이름을 확인하세요.",
                }
            )
            continue

        payload: dict[str, Any] = {"note": str(raw.get("note") or "").strip()}
        empty = True
        if axis["kind"] == "value":
            text = str(raw.get("value") or "").strip()
            if text:
                try:
                    payload["value"] = float(text)
                except ValueError:
                    results.append(
                        {
                            "line": index + 1,
                            "status": "error",
                            "message": f"값이 숫자가 아닙니다: {text}",
                        }
                    )
                    continue
                empty = False
        elif axis["kind"] == "rung":
            text = str(raw.get("rung") or "").strip()
            if text:
                key = by_name.get(text)
                if key is None:
                    results.append(
                        {
                            "line": index + 1,
                            "status": "error",
                            "message": f"모르는 수준입니다: {text}",
                        }
                    )
                    continue
                payload["rung"] = key
                empty = False
        else:  # set · matrix 의 바탕
            picked = raw.get("rungs")
            names = (
                [one.strip() for one in str(picked).split("·")]
                if isinstance(picked, str)
                else [str(one).strip() for one in (picked or [])]
            )
            keys = [by_name[one] for one in names if one and one in by_name]
            unknown = [one for one in names if one and one not in by_name]
            if unknown:
                results.append(
                    {
                        "line": index + 1,
                        "status": "error",
                        "message": f"모르는 항목입니다: {', '.join(unknown)}",
                    }
                )
                continue
            if keys:
                payload["rungs"] = keys
                empty = False
                marks = held.get(found["id"])
                if marks:
                    payload["defects"] = marks

        if empty:
            # **빈 줄은 건너뛴다.** 엑셀에서 일부만 채워 보내는 일이 흔하다.
            results.append(
                {"line": index + 1, "status": "skipped", "message": "값이 비어 있습니다."}
            )
            continue
        try:
            save_assessment(db, user, pair_id=found["id"], axis_key=axis_key, payload=payload)
        except AppError as failed:
            results.append({"line": index + 1, "status": "error", "message": failed.message})
            continue
        results.append(
            {
                "line": index + 1,
                "status": "ok",
                "message": f"{found['subject_label']} · {found['agent_label']}",
            }
        )
    return results


def staff_sheet(db: Session, user: User) -> list[dict[str, Any]]:
    """인력의 **현재값 표** — 이름 · 부서 · 담당 해석 · 조사 밖 업무 · 역량 분야 · 메모.

    실명이 안 보이는 사람에게는 빈 칸으로 온다 — 그 줄은 고칠 수도 없다(부서 멤버가 아니다).
    """
    rows = staff(db, user, workspace=None)
    return [
        {
            "staff_id": one["id"],
            "name": one["name"] or "",
            "alias": one["alias"],
            "workspace_name": one["workspace_name"],
            "agents": " · ".join(agent["label"] for agent in one["agents"]),
            "outside": "예" if one["outside"] else "",
            "skill_kinds": " · ".join(one["skill_kinds"]),
            "note": one["note"],
        }
        for one in rows
    ]


def staff_bulk(db: Session, user: User, *, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """인력 표를 한 번에 저장한다 — **이름과 부서로 그 줄을 찾는다.**

    같은 부서에 같은 이름이 둘이면 첫 줄을 고친다. 이름이 사람의 식별자인 자리라, 동명이인은
    메모로 가른다 — 계정을 붙이는 길은 다음 층이다(겹침 탐지가 그때 정확해진다).
    """
    workspaces = {one.name: one for one in db.scalars(select(Workspace))}
    setting_row = setting(db)
    agent_ids: dict[str, str] = {}
    if setting_row.agent_type_slug:
        kind = _type_or_none(db, setting_row.agent_type_slug)
        if kind is not None:
            agent_ids = {
                one.label: str(one.id)
                for one in db.scalars(
                    select(ObjectInstance).where(
                        ObjectInstance.type_id == kind.id, ObjectInstance.deleted_at.is_(None)
                    )
                )
            }
    existing = {(one.name, one.workspace_id): one for one in db.scalars(select(CaeDtStaff))}

    results: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        name = str(raw.get("name") or "").strip()
        if not name:
            results.append(
                {"line": index + 1, "status": "skipped", "message": "이름이 비어 있습니다."}
            )
            continue
        wanted = str(raw.get("workspace_name") or "").strip()
        workspace = workspaces.get(wanted)
        if workspace is None:
            results.append(
                {
                    "line": index + 1,
                    "status": "error",
                    "message": f"부서를 찾을 수 없습니다: {wanted or '(비어 있음)'}",
                }
            )
            continue
        names = [one.strip() for one in str(raw.get("agents") or "").split("·") if one.strip()]
        unknown = [one for one in names if one not in agent_ids]
        if unknown:
            results.append(
                {
                    "line": index + 1,
                    "status": "error",
                    "message": f"모르는 해석입니다: {', '.join(unknown)}",
                }
            )
            continue
        picked_agents = [agent_ids[one] for one in names]
        payload: dict[str, Any] = {
            "name": name,
            "agents": picked_agents,
            "skill_kinds": [
                one.strip()
                for one in str(raw.get("skill_kinds") or "").split("·")
                if one.strip()
            ],
            "outside": str(raw.get("outside") or "").strip() in ("예", "y", "Y", "true", "1"),
            "note": str(raw.get("note") or "").strip(),
        }
        found = existing.get((name, workspace.id))
        try:
            save_staff(
                db,
                user,
                staff_id=found.id if found else None,
                workspace=workspace,
                payload=payload,
            )
        except AppError as failed:
            results.append({"line": index + 1, "status": "error", "message": failed.message})
            continue
        results.append(
            {
                "line": index + 1,
                "status": "ok",
                "message": f"{workspace.name} · {len(picked_agents)}담당",
            }
        )
    return results


def maintenance(db: Session, user: User) -> list[extension_points.MaintenanceItem]:
    """홈의 「남은 일」 — **대시보드를 열어야만 보이는 자료는 안 채워진다.**

    0 건인 항목은 안 낸다(레지스트리가 거른다) — 다 0 인 목록을 매일 보면 사람은 그 자리를
    아예 안 읽게 되고, 그때 진짜 하나가 떠도 눈에 안 들어온다.
    """
    del user  # 이 목록은 사람에 따라 다르지 않다 — 전사 자료다.
    ready = status(db)
    if not ready["ready"]:
        return []
    out: list[extension_points.MaintenanceItem] = []
    spread = coverage(db)
    for one in spread["axes"]:
        missing = spread["pairs"] - one["assessed"]
        if missing > 0:
            out.append(
                extension_points.MaintenanceItem(
                    key=f"caegroup.dt.axis.{one['axis']}",
                    label=f"디지털 트윈 — {one['label']} 미평가",
                    count=missing,
                    link="/ext/caegroup/dt/bulk",
                )
            )
    # 인프라를 아직 안 적은 부서 — 인력이 있는 부서만 센다(아무 부서나 다 세면 목록이 곧
    # 무의미해진다).
    with_staff = {row.workspace_id for row in db.scalars(select(CaeDtStaff))}
    with_capacity = {row.workspace_id for row in db.scalars(select(CaeDtCapacity))}
    blank = len(with_staff - with_capacity)
    if blank:
        out.append(
            extension_points.MaintenanceItem(
                key="caegroup.dt.capacity",
                label="디지털 트윈 — 인프라 미입력 부서",
                count=blank,
                link="/ext/caegroup/dt/infra",
            )
        )
    return out
