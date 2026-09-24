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
    CaeDtPair,
    CaeDtSetting,
)
from app.modules.accounts.models import User
from app.modules.objects import system
from app.modules.objects.models import ObjectInstance
from app.modules.ontology import importer
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.workspaces.models import Workspace
from app.shared import audit, permissions
from app.shared.errors import Conflict, NotFound, code

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
        "evidence_tier": row.evidence_tier,
        "evidence_ref": row.evidence_ref,
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


def _check_evidence(axis: dict[str, Any], payload: dict[str, Any]) -> None:
    """근거 규칙 — 축 종류와 무관하게 같다."""
    note = str(payload.get("note") or "").strip()
    if not note:
        # **근거 없는 평가는 다음 사람이 확인할 수 없다.** 수준만 남으면 그 숫자는
        # 「누가 언젠가 그렇게 봤다」 는 말과 같아진다.
        raise Conflict(code("CAEGROUP", 9), f"{axis['label']}: 근거를 적어야 저장됩니다.")
    tier = str(payload.get("evidence_tier") or "")
    if tier not in D.TIER_KEYS:
        raise Conflict(
            code("CAEGROUP", 10), f"{axis['label']}: 근거 등급을 고르세요(진술 · 확인 · 검증)."
        )
    if tier in D.TIERS_NEEDING_REF and not str(payload.get("evidence_ref") or "").strip():
        named = {one["key"]: one["label"] for one in D.EVIDENCE_TIERS}[tier]
        raise Conflict(
            code("CAEGROUP", 11),
            f"{axis['label']}: 「{named}」 은 근거 자료가 필요합니다"
            "(문서번호 · 파일명 · 화면 경로).",
        )


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
    _check_evidence(axis, payload)

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
    row.note = str(payload["note"]).strip()
    row.evidence = dict(payload.get("evidence") or {})
    row.evidence_tier = str(payload["evidence_tier"])
    row.evidence_ref = str(payload.get("evidence_ref") or "").strip()
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
        changes={"axis": axis_key, "new": fresh, "tier": row.evidence_tier},
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
        "evidence_tier": row.evidence_tier,
        "evidence_ref": row.evidence_ref,
    }


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
