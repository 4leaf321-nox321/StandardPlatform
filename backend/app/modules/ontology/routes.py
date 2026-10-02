"""메타모델 라우터 — **읽기는 누구나, 고치기는 시스템 관리자만.**

타입을 부서마다 열지 않는 이유는 [ADR 0005](../../../../docs/adr/0005-온톨로지-메타모델.md)
에 있다: 같은 개념이 부서마다 갈리면 **연결하려고 만든 것이 칸막이가 된다.**
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.coreapi.schemas import CoreStatusOut
from app.modules.datasources.models import DataSource
from app.modules.jobs import routes as jobs_routes
from app.modules.jobs import services as job_services
from app.modules.jobs.schemas import JobOut
from app.modules.objects import bulk, purge, refedges
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.ontology import (
    codebook,
    importer,
    inference,
    interfaces,
    linking,
    managed,
    reset,
    retype,
    views,
)
from app.modules.ontology import (
    export as schema_export,
)
from app.modules.ontology.models import (
    CARDINALITIES,
    DATA_TYPES,
    ENTRY_POLICIES,
    KEY_POLICIES,
    KEY_SCOPES,
    KIND_CLASSES,
    NAV_AUDIENCES,
    TEMPORAL_KINDS,
    USAGES,
    NavGroup,
    ObjectInterface,
    ObjectType,
    OntologySnapshot,
    PropertyDef,
    RelationType,
)
from app.modules.ontology.schemas import (
    ChangeOut,
    DeleteBlockOut,
    DeleteKind,
    DeletePlanOut,
    ImplementItemOut,
    ImplementPlanOut,
    ImplementPlanRequest,
    ImportPlanOut,
    InferBuildOut,
    InferBuildRequest,
    InferColumnOut,
    InferOut,
    InterfaceUsageOut,
    NavGroupNode,
    NavGroupOut,
    NavGroupPatchRequest,
    NavGroupWriteRequest,
    ObjectInterfaceOut,
    ObjectInterfacePatchRequest,
    ObjectInterfaceSchema,
    ObjectInterfaceWriteRequest,
    ObjectTypeOut,
    ObjectTypePatchRequest,
    ObjectTypeSchema,
    ObjectTypeWriteRequest,
    OntologySchemaOut,
    PromoteOptionOut,
    PromoteOut,
    PromoteRequest,
    PropertyDefOut,
    PropertyDefWriteRequest,
    PropertyUsage,
    ReferenceEdgeOut,
    RelationTypeOut,
    RelationTypePatchRequest,
    RelationTypeWriteRequest,
    RenameOptionOut,
    RenameOptionRequest,
    ResetItemOut,
    ResetPlanOut,
    ResetRequest,
    RetypeOut,
    RetypeRequest,
    RetypeSampleOut,
    RetypeTypeOut,
    RetypeValueOut,
    SnapshotOut,
    SystemSourceOut,
)
from app.modules.ontology.services import (
    InvalidValue,
    group_color,
    group_parent_error,
    require_choice,
    require_key,
    require_slug,
    require_system_source,
)
from app.shared import sheets, system_sources
from app.shared.audit import record as record_audit
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import AppError, Conflict, NotFound, code

router = APIRouter(prefix="/ontology", tags=["ontology"])


def _audit(
    db: Session, user: User, *, action: str, table: str, row_id: uuid.UUID, label: str
) -> None:
    """**메타모델 변경도 감사 대상이다.** 속성 하나가 화면의 모양을 바꾸고, 값이
    안 보이게 만든다 — 그것이 언제 누구 손에 바뀌었는지 물을 자리가 있어야 한다."""
    record_audit(
        db,
        action=action,
        actor=user,
        target_table=table,
        target_id=row_id,
        target_label=label,
    )


@dataclasses.dataclass
class _Deletion:
    """지우기 전에 셀 것 — **삭제 경로와 미리 보기(`GET /delete-plan`)가 같은 것을 읽는다.**

    막는 것은 그 경로가 낼 오류 그대로 담는다. 둘이 따로 세면 「미리 보기는 된다는데 지우면
    거절」 이 되고, 그때 어느 쪽이 맞는지 알 방법이 없다.

    지우는 경로는 **직전 정의를 스냅샷으로 남긴다** — 지운 정의를 되살릴 자리다(속성 정의를
    되살리면 남아 있던 저장값도 다시 보인다). 기계가 지울 수 있게 된 뒤로는 더 그렇다.
    """

    label: str
    blocking: list[AppError] = dataclasses.field(default_factory=list)
    removes: list[str] = dataclasses.field(default_factory=list)
    keeps: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    core_consumers: list[str] = dataclasses.field(default_factory=list)
    doomed: purge.Purge = dataclasses.field(default_factory=purge.Purge)
    """타입 삭제가 함께 영구 삭제할 지운 객체(ADR 0008) — 다른 정의는 늘 비어 있다."""

    def check(self, guard: Callable[[], None]) -> None:
        """막는 검사 하나 — 거절하면 그 오류를 담는다. 순서가 곧 삭제 경로의 순서다."""
        try:
            guard()
        except AppError as caught:
            self.blocking.append(caught)

    def require(self) -> None:
        """삭제 경로 — 막는 것이 있으면 **첫째를 그대로** 낸다."""
        if self.blocking:
            raise self.blocking[0]


# --- 조회 헬퍼 --------------------------------------------------------------


def _group(db: Session, slug: str) -> NavGroup:
    row = db.scalar(select(NavGroup).where(NavGroup.slug == slug))
    if row is None:
        raise NotFound(code("ONTOLOGY", 30), f"그룹을 찾을 수 없습니다: {slug}")
    return row


def _type(db: Session, slug: str) -> ObjectType:
    row = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if row is None:
        raise NotFound(code("ONTOLOGY", 31), f"타입을 찾을 수 없습니다: {slug}")
    return row


def _counts(db: Session) -> dict[uuid.UUID, int]:
    """타입별 객체 수. **한 번에 센다** — 타입마다 세면 목록 하나에 질의가
    타입 수만큼 붙고, 타입은 늘어나기만 한다."""
    rows = db.execute(
        select(ObjectInstance.type_id, func.count())
        .where(ObjectInstance.deleted_at.is_(None))
        .group_by(ObjectInstance.type_id)
    )
    return {type_id: count for type_id, count in rows}


def _type_out(row: ObjectType, group_slug: str | None, count: int) -> ObjectTypeOut:
    return ObjectTypeOut(
        id=row.id,
        slug=row.slug,
        label=row.label,
        icon=row.icon,
        description=row.description,
        sort_order=row.sort_order,
        nav_group_id=row.nav_group_id,
        nav_group_slug=group_slug,
        kind_class=row.kind_class,
        system_source=row.system_source,
        entry_policy=row.entry_policy,
        core=row.core,
        managed_by=row.managed_by,
        interface_slugs=list(row.interface_slugs or []),
        key_policy=row.key_policy,
        key_scope=row.key_scope,
        temporal_kind=row.temporal_kind,
        usage=row.usage,
        list_view=row.list_view or {},
        form_view=row.form_view or {},
        detail_view=row.detail_view or {},
        title_template=row.title_template,
        is_active=row.is_active,
        object_count=count,
    )


PARENT_GONE = (
    "상위 타입(parent_slug)은 없어졌습니다 — 「A 는 B 의 일종」 은 인터페이스로 적습니다. "
    "관리 › 온톨로지 › 인터페이스에서 만들고, 타입의 「구현 인터페이스」 에 적으세요."
)


def _refuse_parent(sent: set[str]) -> None:
    """**없어진 칸을 조용히 무시하지 않는다** — 무시하면 보낸 쪽은 계층이 적용된 줄 안다."""
    if "parent_slug" in sent:
        raise InvalidValue(code("ONTOLOGY", 28), PARENT_GONE)


def _group_slugs(db: Session) -> dict[uuid.UUID, str]:
    return {row.id: row.slug for row in db.scalars(select(NavGroup))}


# --- 인터페이스 도우미 (ADR 0006) --------------------------------------------


def _require_no_conflicts(bindings: list[interfaces.Binding]) -> None:
    """모양이 안 맞는 곳이 하나라도 있으면 **아무것도 안 바꾼다** — 무엇이 다른지 전부
    싣는다."""
    found = interfaces.conflicts(bindings)
    if not found:
        return
    head = found[0] if len(found) == 1 else f"{found[0]} (외 {len(found) - 1}건)"
    raise Conflict(code("ONTOLOGY", 7), head, details={"conflicts": found})


def _implementation(
    catalog: interfaces.Catalog,
    *,
    slug: str,
    label: str,
    kind_class: str,
    wanted: list[str],
) -> list[interfaces.Binding]:
    """이 타입이 `wanted` 를 구현하면 할 일. 없는 인터페이스 · 투영 타입은 먼저 말한다."""
    unknown = sorted(set(wanted) - set(catalog.interfaces))
    if unknown:
        raise NotFound(code("ONTOLOGY", 4), f"없는 인터페이스입니다: {', '.join(unknown)}")
    if wanted and kind_class == "system":
        raise Conflict(
            code("ONTOLOGY", 25),
            "다른 표를 비추는 타입(system)은 인터페이스를 구현하지 않습니다 — 속성도 행도 "
            "없는 타입이라 공통 속성을 가질 자리가 없습니다.",
        )
    after = catalog.clone()
    entry = after.types.get(slug)
    if entry is None:
        entry = interfaces.TypeDef(slug=slug, label=label)
        after.types[slug] = entry
    entry.kind_class = kind_class
    entry.interfaces = list(wanted)
    return interfaces.plan_bindings(catalog, after, only={slug})


def _contract_of(db: Session, slug: str) -> dict[str, tuple[interfaces.Prop, str]]:
    """이 타입이 가져야 할 공통 속성 — 키 → (속성, 인터페이스)."""
    catalog = interfaces.load(db)
    one = catalog.types.get(slug)
    if one is None or not one.interfaces:
        return {}
    return interfaces.contract(catalog, one.interfaces)[0]


def _refuse_bound(db: Session, slug: str, key: str, *, what: str) -> None:
    """공통 속성은 **타입에서 못 바꾼다** — 한 타입만 바뀌면 같은 속성이 타입마다 갈린다."""
    wanted = _contract_of(db, slug)
    if key in wanted:
        iface = wanted[key][1]
        raise Conflict(
            code("ONTOLOGY", 8),
            f"{slug}.{key} 는 인터페이스 {iface} 의 공통 속성이라 {what} — 인터페이스에서 "
            "수정하세요. 이 타입에서만 바꾸려면 먼저 구현을 해제합니다.",
            details={"interface": iface},
        )


def _interface_slugs(db: Session) -> set[str]:
    return set(db.scalars(select(ObjectInterface.slug)))


def _check_ref_target(db: Session, slug: str | None) -> None:
    """참조 대상이 **실재하는 타입 · 인터페이스인가.** 인터페이스면 그것을 구현한 타입의
    객체를 가리킨다(ADR 0006). 없는 것을 대상으로 두면 그 칸은 아무것도 못 고르는데, 화면은
    「고를 것이 없습니다」 라고만 말한다 — 오타인지 데이터가 없는 것인지 구별되지 않는다."""
    if not slug:
        return
    if db.scalar(select(ObjectType.id).where(ObjectType.slug == slug)) is not None:
        return
    if slug in _interface_slugs(db):
        return
    raise NotFound(code("ONTOLOGY", 41), f"참조 대상에 없는 타입 · 인터페이스입니다: {slug}")


# --- 그룹 -------------------------------------------------------------------


@router.get("/groups", response_model=list[NavGroupOut])
def list_groups(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[NavGroup]:
    return list(db.scalars(select(NavGroup).order_by(NavGroup.sort_order, NavGroup.label)))


def _parent_id(db: Session, slug: str, parent: str) -> uuid.UUID:
    """상위 묶음의 id — **화면과 파일이 같은 규칙으로 막는다**(`group_parent_error`)."""
    rows = list(db.scalars(select(NavGroup)))
    parents_of = {one.slug: one.parent_slug for one in rows}
    children_of: dict[str, list[str]] = {}
    for child, up in parents_of.items():
        if up:
            children_of.setdefault(up, []).append(child)
    wrong = group_parent_error(
        slug=slug,
        parent=parent,
        known={one.slug for one in rows},
        parents_of=parents_of,
        children_of=children_of,
    )
    if wrong:
        raise Conflict(code("ONTOLOGY", 33), wrong)
    found = next(one for one in rows if one.slug == parent)
    return found.id


@router.post("/groups", response_model=NavGroupOut, status_code=201)
def create_group(
    payload: NavGroupWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> NavGroup:
    slug = require_slug(payload.slug, what="그룹 slug")
    require_choice(payload.audience, NAV_AUDIENCES, what="보이는 대상")
    if db.scalar(select(NavGroup).where(NavGroup.slug == slug)) is not None:
        raise Conflict(code("ONTOLOGY", 32), f"이미 있는 그룹입니다: {slug}")

    row = NavGroup(
        slug=slug,
        label=payload.label,
        icon=payload.icon,
        audience=payload.audience,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    if payload.parent_slug:
        row.parent_id = _parent_id(db, slug, payload.parent_slug)
    row.color = group_color(slug, payload.color)
    db.add(row)
    db.flush()
    _audit(
        db, user, action="ontology.group.create", table="nav_groups", row_id=row.id, label=slug
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/groups/{slug}", response_model=NavGroupOut)
def update_group(
    slug: str,
    payload: NavGroupPatchRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> NavGroup:
    """**보낸 것만 바꾼다.** slug 는 안 바꾼다 — 타입은 id 로 가리키지만 사람과
    문서는 slug 로 가리키고, 바꾸면 그 말들이 조용히 틀린 것이 된다."""
    row = _group(db, slug)
    sent = payload.model_fields_set

    if "audience" in sent and payload.audience is not None:
        row.audience = require_choice(payload.audience, NAV_AUDIENCES, what="보이는 대상")
    if "label" in sent and payload.label is not None:
        row.label = payload.label
    if "icon" in sent and payload.icon is not None:
        row.icon = payload.icon
    if "color" in sent and payload.color is not None:
        row.color = group_color(slug, payload.color)
    if "parent_slug" in sent:
        # 빈 문자열이 「맨 위로」 다 — null 은 「안 보냄」 과 구별되지 않는다.
        row.parent_id = (
            _parent_id(db, slug, payload.parent_slug) if payload.parent_slug else None
        )
    if "sort_order" in sent and payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if "is_active" in sent and payload.is_active is not None:
        row.is_active = payload.is_active

    _audit(
        db, user, action="ontology.group.update", table="nav_groups", row_id=row.id, label=slug
    )
    db.commit()
    db.refresh(row)
    return row


# --- 타입 -------------------------------------------------------------------


@router.get("/types", response_model=list[ObjectTypeOut])
def list_types(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[ObjectTypeOut]:
    counts = _counts(db)
    groups = _group_slugs(db)
    rows = db.scalars(select(ObjectType).order_by(ObjectType.sort_order, ObjectType.label))
    return [
        _type_out(
            row,
            groups.get(row.nav_group_id) if row.nav_group_id else None,
            counts.get(row.id, 0),
        )
        for row in rows
    ]


@router.post("/types", response_model=ObjectTypeOut, status_code=201)
def create_type(
    payload: ObjectTypeWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ObjectTypeOut:
    _refuse_parent(payload.model_fields_set)
    slug = require_slug(payload.slug, what="타입 slug")
    _check_type_choices(payload)
    if db.scalar(select(ObjectType).where(ObjectType.slug == slug)) is not None:
        raise Conflict(code("ONTOLOGY", 33), f"이미 있는 타입입니다: {slug}")
    catalog = interfaces.load(db)
    clash = interfaces.namespace_error(
        slug, as_kind="type", types=(), interfaces=catalog.interfaces
    )
    if clash:
        raise Conflict(code("ONTOLOGY", 5), clash)
    wanted = interfaces.normalized_slugs(payload.interface_slugs)
    bindings = _implementation(
        catalog, slug=slug, label=payload.label, kind_class=payload.kind_class, wanted=wanted
    )
    _require_no_conflicts(bindings)

    group = _group(db, payload.nav_group_slug) if payload.nav_group_slug else None
    row = ObjectType(
        slug=slug,
        label=payload.label,
        icon=payload.icon,
        description=payload.description,
        sort_order=payload.sort_order,
        nav_group_id=group.id if group else None,
        interface_slugs=wanted,
        kind_class=payload.kind_class,
        system_source=payload.system_source,
        entry_policy=payload.entry_policy,
        key_policy=payload.key_policy,
        key_scope=payload.key_scope,
        temporal_kind=payload.temporal_kind,
        usage=payload.usage,
        title_template=payload.title_template,
        is_active=payload.is_active,
        core=payload.core,
    )
    db.add(row)
    db.flush()
    # 구현한 인터페이스의 공통 속성이 **먼저 서고** 뷰를 검증한다 — 뷰가 그 속성을 가리킬 수
    # 있다.
    interfaces.apply_bindings(db, bindings)
    defs = _properties_of(db, row.id)
    row.list_view = views.validate_list_view(payload.list_view, defs)
    row.form_view = views.validate_form_view(payload.form_view, defs, what="폼 화면")
    row.detail_view = views.validate_form_view(payload.detail_view, defs, what="상세 화면")
    _audit(
        db,
        user,
        action="ontology.type.create",
        table="object_types",
        row_id=row.id,
        label=slug,
    )
    db.commit()
    db.refresh(row)
    return _type_out(row, group.slug if group else None, 0)


@router.patch("/types/{slug}", response_model=ObjectTypeOut)
def update_type(
    slug: str,
    payload: ObjectTypePatchRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ObjectTypeOut:
    """**보낸 것만 바꾼다.**

    전체 교체로 두면 화면이 `list_view` 를 안 실어 보낸 날 그 설정이 통째로
    날아가고, **그 손실은 저장한 사람 눈에 안 보인다.**

    **slug 는 안 바꾼다** — URL(`/o/<slug>`)·관계의 허용 타입·MCP 도구 이름이
    여기 물려 있어서, 바꾸면 그 셋이 조용히 어긋난다.
    """
    row = _type(db, slug)
    managed.require_definition_editable(row)
    sent = payload.model_fields_set
    _refuse_parent(sent)

    choices = (
        ("kind_class", payload.kind_class, KIND_CLASSES, "객체 분류"),
        ("entry_policy", payload.entry_policy, ENTRY_POLICIES, "입력 정책"),
        ("key_policy", payload.key_policy, KEY_POLICIES, "식별자 정책"),
        ("key_scope", payload.key_scope, KEY_SCOPES, "식별자 범위"),
        ("temporal_kind", payload.temporal_kind, TEMPORAL_KINDS, "시간 정책"),
        ("usage", payload.usage, USAGES, "축 · 기록"),
    )
    for field, value, allowed, what in choices:
        if field in sent and value is not None:
            setattr(row, field, require_choice(value, allowed, what=what))
    if "system_source" in sent and payload.system_source is not None:
        row.system_source = payload.system_source.strip()
    if "kind_class" in sent or "system_source" in sent:
        require_system_source(row.kind_class, row.system_source)
        keeps = (
            interfaces.normalized_slugs(payload.interface_slugs)
            if "interface_slugs" in sent
            else list(row.interface_slugs or [])
        )
        if row.kind_class == "system" and keeps:
            raise Conflict(
                code("ONTOLOGY", 25),
                f"{row.label}은(는) 인터페이스를 구현하고 있어 투영(system)으로 바꿀 수 "
                "없습니다 — 먼저 구현을 해제하세요.",
            )
        if row.kind_class == "system" and _counts(db).get(row.id, 0):
            # 행이 있는 타입을 투영으로 돌리면 그 행이 **화면에서 사라진다** —
            # 지워진 것이 아닌데 안 보이고, 그 사실은 아무 데도 안 적힌다.
            raise Conflict(
                code("ONTOLOGY", 34),
                f"{row.label}에는 객체가 {_counts(db)[row.id]}개 있어 투영으로 바꿀 수 "
                "없습니다. 전용 표로 옮기는 절차(docs/승격-경로.md)를 따르세요.",
            )

    if "label" in sent and payload.label is not None:
        row.label = payload.label
    if "icon" in sent and payload.icon is not None:
        row.icon = payload.icon
    if "description" in sent and payload.description is not None:
        row.description = payload.description
    if "sort_order" in sent and payload.sort_order is not None:
        row.sort_order = payload.sort_order
    # **구현 인터페이스** — 없는 공통 속성은 만들고, 같은 모양이면 채택하고, 다르면 거절한다.
    # 뷰 검증보다 먼저다: 뷰가 구현으로 생기는 속성을 가리킬 수 있다.
    implemented: dict[str, list[str]] | None = None
    if "interface_slugs" in sent:
        wanted = interfaces.normalized_slugs(payload.interface_slugs)
        bindings = _implementation(
            interfaces.load(db),
            slug=row.slug,
            label=row.label,
            kind_class=row.kind_class,
            wanted=wanted,
        )
        _require_no_conflicts(bindings)
        was = list(row.interface_slugs or [])
        row.interface_slugs = wanted
        db.flush()
        interfaces.apply_bindings(db, bindings)
        implemented = {
            "added": sorted(set(wanted) - set(was)),
            "removed": sorted(set(was) - set(wanted)),
            "created": sorted(one.key for one in bindings if one.action == "create"),
            "adopted": sorted(
                one.key for one in bindings if one.fresh and one.action in ("adopt", "sync")
            ),
        }

    # **모르는 키가 오면 거절한다 — 조용히 무시하지 않는다.** 무시하면 「스펙에는
    # 있는데 안 그려지는 필드」 가 쌓이고, 적은 쪽은 적용된 줄 안다(ADR 0005).
    defs = _properties_of(db, row.id)
    if "list_view" in sent and payload.list_view is not None:
        row.list_view = views.validate_list_view(payload.list_view, defs)
    if "form_view" in sent and payload.form_view is not None:
        row.form_view = views.validate_form_view(payload.form_view, defs, what="폼 화면")
    if "detail_view" in sent and payload.detail_view is not None:
        row.detail_view = views.validate_form_view(payload.detail_view, defs, what="상세 화면")
    if "title_template" in sent and payload.title_template is not None:
        row.title_template = payload.title_template
    if "is_active" in sent and payload.is_active is not None:
        row.is_active = payload.is_active
    # **투영 타입은 못 연다.** 행이 원 표(부서 · 계정)에 있어 사람 정보가 그대로 나가고,
    # 그것을 바깥에 여는 일은 온톨로지 공개가 아니라 다른 판단이다.
    core_flip: tuple[bool, bool] | None = None
    if "core" in sent and payload.core is not None:
        if payload.core and row.kind_class == "system":
            raise Conflict(
                code("ONTOLOGY", 45),
                f"{row.label}은(는) 다른 표를 비추는 타입이라 외부에 공개할 수 없습니다.",
            )
        if payload.core != row.core:
            core_flip = (row.core, payload.core)
        row.core = payload.core

    # **`null` 을 명시하면 사이드바에서 뺀다.** 안 보내면 그대로 둔다 — 그 둘을
    # 안 가르면 다른 칸 하나 고칠 때마다 메뉴에서 사라진다.
    if "nav_group_slug" in sent:
        group = _group(db, payload.nav_group_slug) if payload.nav_group_slug else None
        row.nav_group_id = group.id if group else None

    _audit(
        db,
        user,
        action="ontology.type.update",
        table="object_types",
        row_id=row.id,
        label=slug,
    )
    if core_flip is not None:
        # **공개를 켜고 끈 일은 따로 남긴다.** 타입 수정 기록에 섞으면 「지금 무엇이 나가고
        # 있나」 는 코어 현황으로 알아도 「언제 누가 열었나」 를 물을 자리가 없다 — 실제로
        # 개발 설치에서 코어로 켜진 타입 하나의 사연을 아무도 댈 수 없었다.
        record_audit(
            db,
            action="ontology.type.core",
            actor=user,
            target_table="object_types",
            target_id=row.id,
            target_label=slug,
            changes={"core": core_flip[1], "was": core_flip[0]},
        )
    if implemented and (implemented["added"] or implemented["removed"]):
        # **구현을 더하고 뺀 일은 따로 남긴다** — 속성이 생기거나 묶이는 일이라, 「이 속성은 왜
        # 여기 있나」 를 물을 자리가 있어야 한다.
        record_audit(
            db,
            action="ontology.type.implement",
            actor=user,
            target_table="object_types",
            target_id=row.id,
            target_label=slug,
            changes=implemented,
        )
    db.commit()
    db.refresh(row)
    return _type_out(
        row,
        _group_slugs(db).get(row.nav_group_id) if row.nav_group_id else None,
        _counts(db).get(row.id, 0),
    )


@router.post("/types/{slug}/interfaces/plan", response_model=ImplementPlanOut)
def implement_plan(
    slug: str,
    payload: ImplementPlanRequest,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ImplementPlanOut:
    """구현하면 무엇이 되는가 — **아무것도 안 바꾼다.** 화면이 저장 전에 이것을 보여 준다:
    만들 속성 · 채택할 속성 · 모양이 달라 구현할 수 없는 곳(무엇이 다른지)."""
    row = _type(db, slug)
    wanted = interfaces.normalized_slugs(payload.interface_slugs)
    bindings = _implementation(
        interfaces.load(db),
        slug=row.slug,
        label=row.label,
        kind_class=row.kind_class,
        wanted=wanted,
    )

    def item(one: interfaces.Binding) -> ImplementItemOut:
        return ImplementItemOut(key=one.key, interface=one.interface, changed=one.changed)

    return ImplementPlanOut(
        creates=[item(one) for one in bindings if one.action == "create"],
        adopts=[
            item(one) for one in bindings if one.fresh and one.action in ("adopt", "sync")
        ],
        syncs=[item(one) for one in bindings if not one.fresh and one.action == "sync"],
        conflicts=interfaces.conflicts(bindings),
        warnings=[
            *interfaces.risks(db, bindings),
            *interfaces.unimplement_risks(db, row.slug, row.interface_slugs or [], wanted),
        ],
    )


def _check_type_choices(payload: ObjectTypeWriteRequest) -> None:
    """만들 때의 고른 값 검사. **고치기는 보낸 것만 보므로 따로 본다**(update_type)."""
    require_choice(payload.kind_class, KIND_CLASSES, what="객체 분류")
    require_system_source(payload.kind_class, payload.system_source)
    require_choice(payload.entry_policy, ENTRY_POLICIES, what="입력 정책")
    require_choice(payload.key_policy, KEY_POLICIES, what="식별자 정책")
    require_choice(payload.key_scope, KEY_SCOPES, what="식별자 범위")
    require_choice(payload.temporal_kind, TEMPORAL_KINDS, what="시간 정책")
    require_choice(payload.usage, USAGES, what="축 · 기록")


def _type_deletion(db: Session, row: ObjectType) -> _Deletion:
    found = _Deletion(label=row.label)
    found.check(lambda: managed.require_definition_editable(row))
    if row.core:
        # **끄는 것 자체가 알리는 행동이다.** 열린 채로 지우면 남의 동기화가 404 를 받고,
        # 그쪽은 그것이 「잠깐 장애」 인지 「없어진 것」 인지 구별할 수 없다.
        found.blocking.append(
            Conflict(
                code("ONTOLOGY", 47),
                f"{row.label}은(는) 외부에 공개 중이라 삭제할 수 없습니다. 먼저 「코어」 를 "
                "해제하십시오 — 해제 즉시 수신 시스템이 인지합니다.",
                details={"core_consumers": _core_consumers(db, row)},
            )
        )
    live = db.scalar(
        select(func.count())
        .select_from(ObjectInstance)
        .where(ObjectInstance.type_id == row.id, ObjectInstance.deleted_at.is_(None))
    )
    if live:
        found.blocking.append(
            Conflict(
                code("ONTOLOGY", 38),
                f"{row.label}에 {live}개가 들어 있어 지울 수 없습니다. "
                "그만 쓰려는 것이면 「사용 안 함」 으로 두세요 — "
                "자료는 남고 화면에서만 빠집니다.",
                details={"object_count": int(live)},
            )
        )
    sources = list(db.scalars(select(DataSource.name).where(DataSource.type_id == row.id)))
    if sources:
        # FK 가 RESTRICT 다 — 안 막으면 지우는 자리에서 500 이 난다.
        found.blocking.append(
            Conflict(
                code("ONTOLOGY", 86),
                f"데이터 소스 {', '.join(sources)} 이(가) {row.label}에 넣고 있습니다. "
                "데이터 소스를 먼저 삭제하거나 넣을 타입을 바꾸세요.",
                details={"data_sources": sources},
            )
        )
    if not live:
        # **지운 객체만 남았으면 함께 영구 삭제한다**(ADR 0008) — 행이 타입을 RESTRICT 로
        # 붙들어, 안 그러면 객체를 한 번이라도 넣어 본 타입은 영영 못 지운다. 확인은 삭제
        # 경로가 따로 받는다(`purge_deleted`).
        found.doomed = purge.plan(db, row)
        if found.doomed.pointing:
            found.blocking.append(
                Conflict(
                    code("ONTOLOGY", 85),
                    f"살아 있는 객체가 {row.label}의 지운 객체를 가리킵니다 — "
                    f"{', '.join(found.doomed.pointing)}. 품질 화면의 "
                    "「지워진 것을 가리키는 칸」 에서 먼저 비우세요 — 영구 삭제하면 그 칸은 "
                    "무엇을 가리켰는지조차 모르게 됩니다.",
                    details={"pointing": found.doomed.pointing},
                )
            )
        found.removes.extend(found.doomed.removes())
        if found.doomed.objects:
            found.keeps.append("감사 기록 — 누가 언제 무엇을 했는지(이름과 함께)")
    owned = _owned_properties(db, "type", row.id)
    if owned:
        found.removes.append(f"속성 정의 {owned}개")
    # **가리키는 정의는 막지 않는다** — 객체가 없으니 끊길 값은 없다. 그러나 그 칸은 고를 곳을
    # 잃고, 그 정의를 다음에 고칠 때 「참조 대상이 없다」 로 거절된다. 누르기 전에 안다.
    catalog = interfaces.load(db)
    owners: list[tuple[str, dict[str, interfaces.Prop]]] = [
        *((slug, one.props) for slug, one in catalog.types.items()),
        *((slug, one.props) for slug, one in catalog.interfaces.items()),
    ]
    for owner, props in owners:
        for key, prop in props.items():
            if prop.shape.ref_type_slug == row.slug and owner != row.slug:
                found.warnings.append(
                    f"속성 {owner}.{key} 가 이 타입을 참조 대상으로 적고 있습니다 — "
                    "지우면 그 칸은 가리킬 곳이 없어집니다(참조 대상을 먼저 바꾸세요)."
                )
    for kind in _relation_types(db):
        if row.slug in (kind.src_type_slugs or []) or row.slug in (kind.dst_type_slugs or []):
            found.warnings.append(
                f"관계 종류 {kind.slug} 의 끝에 이 타입이 있습니다 — "
                "지우면 그 끝에서 빠집니다."
            )
    return found


def _owned_properties(db: Session, owner_kind: str, owner_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(PropertyDef)
            .where(PropertyDef.owner_kind == owner_kind, PropertyDef.owner_id == owner_id)
        )
        or 0
    )


@router.delete("/types/{slug}", status_code=204)
def delete_type(
    slug: str,
    purge_deleted: bool = Query(
        default=False,
        description="지운 객체만 남은 타입이면 그것까지 영구 삭제함을 확인했는지(ADR 0008)",
    ),
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """타입을 지운다 — **살아 있는 객체가 하나도 없을 때만.**

    행이 있는데 지우면 그 데이터가 통째로 고아가 된다. 그런데 그것은 화면의 실수
    한 번으로 일어날 일이 아니다. 그래서 몇 개가 걸려 있는지 말하며 막고,
    **그만 쓰려는 것이면 비활성으로 두라고** 알려 준다 — 이 저장소는 지우지 않는다.

    비어 있으면 진짜로 지운다. 잘못 만든 타입이 목록에 영원히 남으면, 그 목록은
    곧 아무도 안 읽는다. **지운 객체만 남았으면** 그것까지 영구 삭제해야 지워진다 — 행이
    타입을 RESTRICT 로 붙든다. 되돌릴 수 없으니 `purge_deleted` 로 확인을 받는다(ADR 0008).
    """
    row = _type(db, slug)
    found = _type_deletion(db, row)
    found.require()
    doomed = found.doomed
    if doomed.objects and not purge_deleted:
        raise Conflict(
            code("ONTOLOGY", 84),
            f"{row.label}에 지운 객체 {doomed.objects}개가 기록으로 남아 있습니다 — 함께 영구 "
            "삭제해야 타입을 지울 수 있고, 되돌릴 수 없습니다. 무엇이 사라지는지 확인한 뒤 "
            "「영구 삭제」 로 다시 실행하십시오.",
            details={"deleted_objects": doomed.objects, "removes": doomed.removes()},
        )
    _snapshot(db, user, reason=f"삭제 직전: 타입 {slug}")
    purge.apply(db, doomed)

    record_audit(
        db,
        action="ontology.type.delete",
        actor=user,
        target_table="object_types",
        target_id=row.id,
        target_label=slug,
        # 영구 삭제한 것은 **수로** 남긴다 — 무엇이었는지는 그 객체들의 감사 기록이 말한다.
        changes=(
            {
                "purged_objects": doomed.objects,
                "purged_relations": doomed.relations,
                "purged_attachments": doomed.attachments,
            }
            if doomed.objects
            else None
        ),
        reason=f"지운 객체 {doomed.objects}개 영구 삭제" if doomed.objects else None,
    )
    # 속성 정의는 FK 가 없다(가리키는 표가 둘이라 걸 수 없다) — 여기서 함께 지운다.
    # 안 지우면 같은 slug 로 타입을 다시 만들 때 **옛 속성이 되살아난다.**
    db.query(PropertyDef).filter(
        PropertyDef.owner_kind == "type", PropertyDef.owner_id == row.id
    ).delete(synchronize_session=False)
    db.delete(row)
    db.commit()


def _group_deletion(db: Session, row: NavGroup) -> _Deletion:
    found = _Deletion(label=row.label)
    attached = list(
        db.scalars(select(ObjectType.label).where(ObjectType.nav_group_id == row.id))
    )
    if attached:
        found.blocking.append(
            Conflict(
                code("ONTOLOGY", 39),
                f"{row.label}에 {', '.join(attached)} 이(가) 걸려 있습니다. "
                "그 타입들의 묶음을 먼저 바꾸세요 — 안 그러면 사이드바에서 조용히 사라집니다.",
                details={"types": attached},
            )
        )
    # 하위 묶음은 FK 가 SET NULL 이라 **최상위로 올라온다** — 사라지지는 않으니 막지 않는다.
    children = list(db.scalars(select(NavGroup.label).where(NavGroup.parent_id == row.id)))
    if children:
        found.warnings.append(f"하위 묶음 {', '.join(children)} 은(는) 최상위 묶음이 됩니다.")
    return found


@router.delete("/groups/{slug}", status_code=204)
def delete_group(
    slug: str,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """묶음을 지운다 — **걸린 타입이 없을 때만.**

    FK 가 SET NULL 이라 DB 는 지우게 두지만, 그러면 그 타입들이 **조용히
    사이드바에서 사라진다.** 없어진 이유를 물을 자리가 없으므로 여기서 막는다.
    """
    row = _group(db, slug)
    _group_deletion(db, row).require()
    _snapshot(db, user, reason=f"삭제 직전: 묶음 {slug}")

    _audit(
        db, user, action="ontology.group.delete", table="nav_groups", row_id=row.id, label=slug
    )
    db.delete(row)
    db.commit()


# --- 관계 종류 --------------------------------------------------------------


def _relation_types(db: Session) -> list[RelationType]:
    return list(
        db.scalars(select(RelationType).order_by(RelationType.sort_order, RelationType.label))
    )


def _relation_type(db: Session, slug: str) -> RelationType:
    row = db.scalar(select(RelationType).where(RelationType.slug == slug))
    if row is None:
        raise NotFound(code("ONTOLOGY", 40), f"관계 종류를 찾을 수 없습니다: {slug}")
    return row


def _check_type_slugs(db: Session, slugs: list[str] | None, *, what: str) -> None:
    """허용 타입이 **실재하는 타입 · 인터페이스인가.** 인터페이스면 그것을 구현한 타입이 된다.

    없는 slug 를 넣어 두면 그 관계는 아무것도 못 맺는데, 화면은 「고를 것이
    없습니다」 라고만 말한다 — 오타인지 데이터가 없는 것인지 구별되지 않는다.
    """
    if not slugs:
        return
    known = {row.slug for row in db.scalars(select(ObjectType))} | _interface_slugs(db)
    missing = sorted(set(slugs) - known)
    if missing:
        raise NotFound(
            code("ONTOLOGY", 41),
            f"{what}에 없는 타입 · 인터페이스가 있습니다: {', '.join(missing)}",
        )


def _check_relation_shape(payload: RelationTypeWriteRequest) -> None:
    require_choice(payload.cardinality, CARDINALITIES, what="개수 제약")
    # **이행적인데 순환을 허용하면 트리 렌더가 무한히 돈다.** 재귀 펼침이 자기
    # 자신으로 돌아오기 때문이다 — 그 상태는 화면이 멈추는 것으로만 드러난다.
    if payload.transitive and not payload.acyclic:
        raise Conflict(
            code("ONTOLOGY", 42),
            "재귀로 펼치는 관계는 순환을 막아야 합니다. "
            "안 그러면 자기 조상을 자식으로 넣는 순간 트리가 무한히 돕니다.",
        )


@router.get("/relation-types", response_model=list[RelationTypeOut])
def list_relation_types(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[RelationType]:
    return _relation_types(db)


@router.post("/relation-types", response_model=RelationTypeOut, status_code=201)
def create_relation_type(
    payload: RelationTypeWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RelationType:
    slug = require_slug(payload.slug, what="관계 slug")
    _check_relation_shape(payload)
    _check_type_slugs(db, payload.src_type_slugs, what="출발 타입")
    _check_type_slugs(db, payload.dst_type_slugs, what="도착 타입")
    if db.scalar(select(RelationType).where(RelationType.slug == slug)) is not None:
        raise Conflict(code("ONTOLOGY", 43), f"이미 있는 관계 종류입니다: {slug}")

    row = RelationType(
        slug=slug,
        label=payload.label,
        inverse_label=payload.inverse_label,
        description=payload.description,
        directed=payload.directed,
        transitive=payload.transitive,
        acyclic=payload.acyclic,
        cardinality=payload.cardinality,
        src_type_slugs=payload.src_type_slugs,
        dst_type_slugs=payload.dst_type_slugs,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        user,
        action="ontology.relation_type.create",
        table="relation_types",
        row_id=row.id,
        label=slug,
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/relation-types/{slug}", response_model=RelationTypeOut)
def update_relation_type(
    slug: str,
    payload: RelationTypePatchRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RelationType:
    """**보낸 것만 바꾼다.** slug 는 안 바꾼다 — 이미 맺힌 관계가 그 값을
    문자열로 들고 있어서, 바꾸면 그 관계들이 통째로 고아가 된다."""
    row = _relation_type(db, slug)
    managed.require_definition_editable(row)
    sent = payload.model_fields_set

    if "cardinality" in sent and payload.cardinality is not None:
        row.cardinality = require_choice(payload.cardinality, CARDINALITIES, what="개수 제약")
    for field in ("label", "inverse_label", "description", "sort_order"):
        value = getattr(payload, field)
        if field in sent and value is not None:
            setattr(row, field, value)
    for flag in ("directed", "transitive", "acyclic", "is_active"):
        value = getattr(payload, flag)
        if flag in sent and value is not None:
            setattr(row, flag, value)

    # **`null` 을 명시하면 제약을 푼다.** 안 보내면 그대로 둔다.
    for field, what in (("src_type_slugs", "출발 타입"), ("dst_type_slugs", "도착 타입")):
        if field in sent:
            value = getattr(payload, field)
            _check_type_slugs(db, value, what=what)
            setattr(row, field, value)

    if row.transitive and not row.acyclic:
        raise Conflict(
            code("ONTOLOGY", 42),
            "재귀로 펼치는 관계는 순환을 막아야 합니다. "
            "안 그러면 자기 조상을 자식으로 넣는 순간 트리가 무한히 돕니다.",
        )

    _audit(
        db,
        user,
        action="ontology.relation_type.update",
        table="relation_types",
        row_id=row.id,
        label=slug,
    )
    db.commit()
    db.refresh(row)
    return row


def _relation_type_deletion(db: Session, row: RelationType) -> _Deletion:
    found = _Deletion(label=row.label)
    found.check(lambda: managed.require_definition_editable(row))
    edges = db.scalar(
        select(func.count())
        .select_from(ObjectRelation)
        .where(ObjectRelation.relation == row.slug)
    )
    if edges:
        found.blocking.append(
            Conflict(
                code("ONTOLOGY", 44),
                f"{row.label}으로 맺힌 관계가 {edges}개 있어 지울 수 없습니다. "
                "그만 쓰려는 것이면 「사용함」 을 끄세요 — "
                "맺힌 것은 남고 새로 맺지만 못합니다.",
                details={"relation_count": int(edges)},
            )
        )
    owned = _owned_properties(db, "relation", row.id)
    if owned:
        found.removes.append(f"관계 속성 정의 {owned}개")
    return found


@router.delete("/relation-types/{slug}", status_code=204)
def delete_relation_type(
    slug: str,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """관계 종류를 지운다 — **맺힌 관계가 하나도 없을 때만.**

    엣지는 이 slug 를 문자열로 들고 있다(FK 가 없다 — 종류의 추가·삭제가
    설정 변경이어야 하기 때문이다). 종류를 지우면 그 관계들은 **이름 없는 엣지**
    로 남고, 화면은 그 줄에 slug 를 그대로 보여 줄 수밖에 없다.

    비어 있으면 지운다. **속성 정의도 함께** — 안 지우면 같은 slug 로 다시 만들 때
    옛 속성이 되살아난다.
    """
    row = _relation_type(db, slug)
    _relation_type_deletion(db, row).require()
    _snapshot(db, user, reason=f"삭제 직전: 관계 종류 {slug}")
    _audit(
        db,
        user,
        action="ontology.relation_type.delete",
        table="relation_types",
        row_id=row.id,
        label=slug,
    )
    db.query(PropertyDef).filter(
        PropertyDef.owner_kind == "relation", PropertyDef.owner_id == row.id
    ).delete(synchronize_session=False)
    db.delete(row)
    db.commit()


# --- 인터페이스 (ADR 0006) ----------------------------------------------------


def _interface(db: Session, slug: str) -> ObjectInterface:
    row = db.scalar(select(ObjectInterface).where(ObjectInterface.slug == slug))
    if row is None:
        raise NotFound(code("ONTOLOGY", 4), f"인터페이스를 찾을 수 없습니다: {slug}")
    return row


def _interface_properties(db: Session, owner_id: uuid.UUID) -> list[PropertyDef]:
    return list(
        db.scalars(
            select(PropertyDef)
            .where(PropertyDef.owner_kind == "interface", PropertyDef.owner_id == owner_id)
            .order_by(PropertyDef.sort_order, PropertyDef.label)
        )
    )


def _interface_out(
    row: ObjectInterface,
    catalog: interfaces.Catalog,
    counts: dict[uuid.UUID, int],
    type_ids: dict[str, uuid.UUID],
) -> ObjectInterfaceOut:
    implementers = interfaces.implementers(catalog, row.slug)
    return ObjectInterfaceOut(
        id=row.id,
        slug=row.slug,
        label=row.label,
        icon=row.icon,
        description=row.description,
        sort_order=row.sort_order,
        extends_slugs=list(row.extends_slugs or []),
        list_view=row.list_view or {},
        managed_by=row.managed_by,
        implementers=implementers,
        object_count=sum(
            counts.get(type_ids[one], 0) for one in implementers if one in type_ids
        ),
    )


def _type_ids(db: Session) -> dict[str, uuid.UUID]:
    return {row.slug: row.id for row in db.scalars(select(ObjectType))}


def _touch(row: ObjectInterface) -> None:
    """공통 속성만 바뀌어도 인터페이스가 바뀐 것이다 — RDF 캐시가 이 시각으로 안다."""
    row.updated_at = datetime.now(UTC)


def _check_list_view_of(
    catalog: interfaces.Catalog, slug: str, spec: dict[str, Any]
) -> dict[str, Any]:
    return views.validate_list_view(
        spec,
        interfaces.interface_fields(catalog, slug),
        allowed=views.INTERFACE_LIST_KEYS,
        extra_fields=views.INTERFACE_FIELDS,
    )


@router.get("/interfaces", response_model=list[ObjectInterfaceOut])
def list_interfaces(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[ObjectInterfaceOut]:
    catalog = interfaces.load(db)
    counts = _counts(db)
    type_ids = _type_ids(db)
    rows = db.scalars(
        select(ObjectInterface).order_by(ObjectInterface.sort_order, ObjectInterface.label)
    )
    return [_interface_out(row, catalog, counts, type_ids) for row in rows]


@router.post("/interfaces", response_model=ObjectInterfaceOut, status_code=201)
def create_interface(
    payload: ObjectInterfaceWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ObjectInterfaceOut:
    slug = require_slug(payload.slug, what="인터페이스 slug")
    catalog = interfaces.load(db)
    if slug in catalog.interfaces:
        raise Conflict(code("ONTOLOGY", 5), f"이미 있는 인터페이스입니다: {slug}")
    clash = interfaces.namespace_error(
        slug, as_kind="interface", types=catalog.types, interfaces=()
    )
    if clash:
        raise Conflict(code("ONTOLOGY", 5), clash)
    extends = interfaces.normalized_slugs(payload.extends_slugs)
    wrong = interfaces.extends_error(
        slug, extends, catalog.extends_of(), set(catalog.interfaces)
    )
    if wrong:
        raise Conflict(code("ONTOLOGY", 6), wrong)
    after = catalog.clone()
    after.interfaces[slug] = interfaces.Iface(slug=slug, label=payload.label, extends=extends)
    row = ObjectInterface(
        slug=slug,
        label=payload.label,
        icon=payload.icon,
        description=payload.description,
        sort_order=payload.sort_order,
        extends_slugs=extends,
        list_view=_check_list_view_of(after, slug, payload.list_view),
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        user,
        action="ontology.interface.create",
        table="object_interfaces",
        row_id=row.id,
        label=slug,
    )
    db.commit()
    db.refresh(row)
    return _interface_out(row, interfaces.load(db), _counts(db), _type_ids(db))


@router.patch("/interfaces/{slug}", response_model=ObjectInterfaceOut)
def update_interface(
    slug: str,
    payload: ObjectInterfacePatchRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ObjectInterfaceOut:
    """**보낸 것만 바꾼다.** 상위 인터페이스를 바꾸면 공통 속성이 늘거나 줄어 구현 타입 전부가
    걸린다 — 모양이 안 맞는 곳이 하나라도 있으면 아무것도 안 바꾼다."""
    row = _interface(db, slug)
    managed.require_definition_editable(row)
    sent = payload.model_fields_set
    for name in ("label", "icon", "description", "sort_order"):
        value = getattr(payload, name)
        if name in sent and value is not None:
            setattr(row, name, value)

    catalog = interfaces.load(db)
    after = catalog.clone()
    if "extends_slugs" in sent:
        extends = interfaces.normalized_slugs(payload.extends_slugs)
        wrong = interfaces.extends_error(
            slug, extends, catalog.extends_of(), set(catalog.interfaces)
        )
        if wrong:
            raise Conflict(code("ONTOLOGY", 6), wrong)
        after.interfaces[slug].extends = extends
        bindings = interfaces.plan_bindings(catalog, after)
        _require_no_conflicts(bindings)
        row.extends_slugs = extends
        db.flush()
        interfaces.apply_bindings(db, bindings)
    if "list_view" in sent and payload.list_view is not None:
        row.list_view = _check_list_view_of(after, slug, payload.list_view)
    _touch(row)
    _audit(
        db,
        user,
        action="ontology.interface.update",
        table="object_interfaces",
        row_id=row.id,
        label=slug,
    )
    db.commit()
    db.refresh(row)
    return _interface_out(row, interfaces.load(db), _counts(db), _type_ids(db))


def _interface_usage(db: Session, slug: str) -> InterfaceUsageOut:
    catalog = interfaces.load(db)
    return InterfaceUsageOut(
        slug=slug,
        implementers=sorted(
            one.slug for one in catalog.types.values() if slug in one.interfaces
        ),
        sub_interfaces=sorted(
            one.slug for one in catalog.interfaces.values() if slug in one.extends
        ),
        referenced_by=sorted(
            f"{owner}.{key}"
            for owner, one in catalog.types.items()
            for key, prop in one.props.items()
            if prop.shape.ref_type_slug == slug
        ),
        relation_types=sorted(
            kind.slug
            for kind in _relation_types(db)
            if slug in (kind.src_type_slugs or []) or slug in (kind.dst_type_slugs or [])
        ),
    )


@router.get("/interfaces/{slug}/usage", response_model=InterfaceUsageOut)
def interface_usage(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> InterfaceUsageOut:
    """**지우기 전에 무엇이 가리키는지.** 화면의 확인 창이 이것을 읽어 말한다."""
    _interface(db, slug)
    return _interface_usage(db, slug)


def _interface_deletion(db: Session, row: ObjectInterface) -> _Deletion:
    found = _Deletion(label=row.label)
    found.check(lambda: managed.require_definition_editable(row))
    usage = _interface_usage(db, row.slug)
    blocking = [
        *(f"구현 타입 {one}" for one in usage.implementers),
        *(f"상위로 이어받는 인터페이스 {one}" for one in usage.sub_interfaces),
        *(f"참조 대상으로 적은 속성 {one}" for one in usage.referenced_by),
        *(f"관계 끝에 적은 관계 종류 {one}" for one in usage.relation_types),
    ]
    if blocking:
        found.blocking.append(
            Conflict(
                code("ONTOLOGY", 9),
                f"{row.label}을(를) 가리키는 것이 {len(blocking)}개 있어 지울 수 없습니다 — "
                f"{', '.join(blocking[:5])}{' …' if len(blocking) > 5 else ''}. "
                "먼저 해제하세요.",
                details=usage.model_dump(),
            )
        )
    owned = _owned_properties(db, "interface", row.id)
    if owned:
        found.removes.append(f"공통 속성 정의 {owned}개")
    return found


@router.delete("/interfaces/{slug}", status_code=204)
def delete_interface(
    slug: str,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """인터페이스를 지운다 — **가리키는 것이 없을 때만.**

    구현한 타입이 남아 있는데 지우면 그 타입은 없는 인터페이스를 구현하게 되고, 그 사실은 그
    타입을 고치는 날에야 오류로 드러난다. 무엇이 걸렸는지 말하며 막는다. 지우면 공통 속성
    정의도 함께 지운다 — **구현 타입의 속성은 남는다**(그 타입의 것이다).
    """
    row = _interface(db, slug)
    _interface_deletion(db, row).require()
    _snapshot(db, user, reason=f"삭제 직전: 인터페이스 {slug}")
    _audit(
        db,
        user,
        action="ontology.interface.delete",
        table="object_interfaces",
        row_id=row.id,
        label=slug,
    )
    db.query(PropertyDef).filter(
        PropertyDef.owner_kind == "interface", PropertyDef.owner_id == row.id
    ).delete(synchronize_session=False)
    db.delete(row)
    db.commit()


# --- 공통 속성 ---------------------------------------------------------------


def _interface_property(db: Session, slug: str, key: str) -> PropertyDef:
    owner = _interface(db, slug)
    row = db.scalar(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "interface",
            PropertyDef.owner_id == owner.id,
            PropertyDef.key == key,
        )
    )
    if row is None:
        raise NotFound(code("ONTOLOGY", 36), f"공통 속성을 찾을 수 없습니다: {slug}.{key}")
    return row


def _check_interface_property(db: Session, key: str, payload: PropertyDefWriteRequest) -> None:
    require_choice(payload.data_type, DATA_TYPES, what="속성 종류")
    _check_property_shape(payload)
    wrong = interfaces.interface_property_error(key, payload.model_dump())
    if wrong:
        raise InvalidValue(code("ONTOLOGY", 24), wrong)
    _check_ref_target(db, payload.ref_type_slug)


def _with_property(
    catalog: interfaces.Catalog, slug: str, key: str, payload: PropertyDefWriteRequest
) -> interfaces.Catalog:
    after = catalog.clone()
    after.interfaces[slug].props[key] = interfaces.Prop(
        key=key,
        shape=interfaces.shape_of(payload.model_dump()),
        label=payload.label,
        help=payload.help,
        section=payload.section,
        sort_order=payload.sort_order,
    )
    return after


@router.get("/interfaces/{slug}/properties", response_model=list[PropertyDefOut])
def list_interface_properties(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[PropertyDef]:
    return _interface_properties(db, _interface(db, slug).id)


@router.post("/interfaces/{slug}/properties", response_model=PropertyDefOut, status_code=201)
def create_interface_property(
    slug: str,
    payload: PropertyDefWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PropertyDef:
    """공통 속성을 더한다 — **구현 타입 전부에** 같은 키의 속성이 선다(있으면 모양이 같아야
    한다)."""
    owner = _interface(db, slug)
    managed.require_definition_editable(owner)
    key = require_key(payload.key)
    _check_interface_property(db, key, payload)
    catalog = interfaces.load(db)
    if key in catalog.interfaces[slug].props:
        raise Conflict(code("ONTOLOGY", 34), f"이미 있는 공통 속성입니다: {key}")
    bindings = interfaces.plan_bindings(catalog, _with_property(catalog, slug, key, payload))
    _require_no_conflicts(bindings)

    row = PropertyDef(owner_kind="interface", owner_id=owner.id, key=key, data_type="text")
    _write_interface_property(row, payload)
    db.add(row)
    db.flush()
    interfaces.apply_bindings(db, bindings)
    _touch(owner)
    _audit(
        db,
        user,
        action="ontology.interface_property.create",
        table="property_defs",
        row_id=row.id,
        label=f"{slug}.{key}",
    )
    db.commit()
    db.refresh(row)
    return row


def _write_interface_property(row: PropertyDef, payload: PropertyDefWriteRequest) -> None:
    """공통 속성에 적는 것 — 타입마다 정하는 칸(유일 · 기본값 · 역방향 이름)은 비워 둔다."""
    row.label = payload.label
    row.data_type = payload.data_type
    row.unit = payload.unit
    row.help = payload.help
    row.required = payload.required
    row.multi = payload.multi
    row.enum_options = payload.enum_options
    row.ref_type_slug = payload.ref_type_slug
    row.min_value = payload.min_value
    row.max_value = payload.max_value
    row.decimals = payload.decimals
    row.pattern = payload.pattern
    row.section = payload.section
    row.sort_order = payload.sort_order


@router.patch("/interfaces/{slug}/properties/{key}", response_model=PropertyDefOut)
def update_interface_property(
    slug: str,
    key: str,
    payload: PropertyDefWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PropertyDef:
    """공통 속성을 고친다 — **구현 타입들의 속성도 같은 트랜잭션에서** 바뀐다(모양만. 이름 ·
    묶음 · 순서는 타입마다 그대로다)."""
    owner = _interface(db, slug)
    managed.require_definition_editable(owner)
    row = _interface_property(db, slug, key)
    _check_interface_property(db, key, payload)
    if payload.data_type != row.data_type:
        raise Conflict(
            code("ONTOLOGY", 35),
            f"속성 종류는 수정으로 바꾸지 않습니다({row.data_type} → {payload.data_type}) — "
            "「종류 변경」(POST …/retype)이 구현 타입마다 저장된 값을 변환하는 계획을 먼저 "
            "보여 줍니다.",
        )
    catalog = interfaces.load(db)
    bindings = interfaces.plan_bindings(catalog, _with_property(catalog, slug, key, payload))
    _require_no_conflicts(bindings)
    _write_interface_property(row, payload)
    db.flush()
    interfaces.apply_bindings(db, bindings)
    _touch(owner)
    _audit(
        db,
        user,
        action="ontology.interface_property.update",
        table="property_defs",
        row_id=row.id,
        label=f"{slug}.{key}",
    )
    db.commit()
    db.refresh(row)
    return row


@router.get("/interfaces/{slug}/properties/{key}/usage", response_model=PropertyUsage)
def interface_property_usage(
    slug: str, key: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> PropertyUsage:
    """구현 타입 전부에서 이 값을 가진 객체 수 — 모양을 바꾸기 전에 무엇이 걸리는지."""
    row = _interface_property(db, slug, key)
    implementers = interfaces.implementers(interfaces.load(db), slug)
    ids = [one for name, one in _type_ids(db).items() if name in implementers]
    return PropertyUsage(
        key=row.key, label=row.label, objects_with_value=_objects_with_value(db, ids, key)
    )


def _interface_property_deletion(
    db: Session, owner: ObjectInterface, row: PropertyDef
) -> _Deletion:
    found = _Deletion(label=f"{owner.label} · {row.label}")
    found.check(lambda: managed.require_definition_editable(owner))
    found.removes.append("공통 속성 정의")
    if views.prune_field(owner.list_view or {}, row.key) != (owner.list_view or {}):
        found.removes.append("인터페이스 목록의 열")
    implementers = interfaces.implementers(interfaces.load(db), owner.slug)
    if implementers:
        found.keeps.append(
            f"구현 타입의 속성 — {', '.join(implementers)} 의 것이 되어 그 뒤로는 타입에서 "
            "수정합니다(저장값은 그대로)"
        )
    return found


@router.delete("/interfaces/{slug}/properties/{key}", status_code=204)
def delete_interface_property(
    slug: str,
    key: str,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """공통 속성을 뺀다. **구현 타입의 속성은 남는다** — 그 타입의 것이 되고, 그 뒤로는
    타입에서 고칠 수 있다."""
    owner = _interface(db, slug)
    row = _interface_property(db, slug, key)
    _interface_property_deletion(db, owner, row).require()
    _snapshot(db, user, reason=f"삭제 직전: 공통 속성 {slug}.{key}")
    owner.list_view = views.prune_field(owner.list_view or {}, key)
    _touch(owner)
    _audit(
        db,
        user,
        action="ontology.interface_property.delete",
        table="property_defs",
        row_id=row.id,
        label=f"{slug}.{key}",
    )
    db.delete(row)
    db.commit()


@router.post(
    "/interfaces/{slug}/properties/{key}/rename-option", response_model=RenameOptionOut
)
def rename_interface_option(
    slug: str,
    key: str,
    payload: RenameOptionRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RenameOptionOut:
    """고를 값의 이름을 **구현 타입 전부에서 한 번에** 바꾼다 — 정의와 저장된 값까지.

    한 타입만 바뀌고 멈추면 같은 공통 속성이 타입마다 다른 이름을 갖는다. 그래서 한
    트랜잭션이다.
    `apply=false` 면 몇 개가 함께 바뀔지만 말한다.
    """
    owner = _interface(db, slug)
    managed.require_definition_editable(owner)
    definition = _interface_property(db, slug, key)
    options = definition.enum_options or []
    errors: list[str] = []
    if definition.data_type != "enum":
        errors.append("고를 값이 있는 속성(enum)에만 씁니다.")
    elif payload.from_value not in options:
        errors.append(f"「{payload.from_value}」 은 고를 값에 없습니다.")
    elif payload.to_value in options and payload.to_value != payload.from_value:
        errors.append(
            f"「{payload.to_value}」 은 이미 있는 값입니다. 둘을 합치려면 먼저 저장된 값을 "
            "옮기세요."
        )
    implementers = [
        one
        for one in db.scalars(select(ObjectType))
        if one.slug in interfaces.implementers(interfaces.load(db), slug)
    ]
    total = 0
    for one in implementers:
        prop = db.scalar(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.owner_id == one.id,
                PropertyDef.key == key,
            )
        )
        if prop is None:
            continue
        if errors or not payload.apply:
            found = codebook.plan_rename(db, one, prop, payload.from_value, payload.to_value)
        else:
            found = codebook.rename_values(
                db, user, one, prop, payload.from_value, payload.to_value
            )
        errors.extend(f"{one.slug}: {message}" for message in found.errors)
        total += found.objects_with_value
    applied = bool(payload.apply and not errors)
    if applied:
        definition.enum_options = [
            payload.to_value if one == payload.from_value else one for one in options
        ]
        _touch(owner)
        _audit(
            db,
            user,
            action="ontology.interface_property.update",
            table="property_defs",
            row_id=definition.id,
            label=f"{slug}.{key}",
        )
        db.commit()
    else:
        db.rollback()
    return RenameOptionOut(
        applied=applied,
        from_value=payload.from_value,
        to_value=payload.to_value,
        objects_with_value=total,
        errors=errors,
    )


# --- 속성 정의 --------------------------------------------------------------


@router.get("/types/{slug}/properties", response_model=list[PropertyDefOut])
def list_properties(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[PropertyDefOut]:
    row = _type(db, slug)
    return _marked(_properties_of(db, row.id), _contract_of(db, slug))


def _marked(
    rows: list[PropertyDef], wanted: dict[str, tuple[interfaces.Prop, str]]
) -> list[PropertyDefOut]:
    """공통 속성에 **어느 인터페이스의 것인지** 적는다 — 화면이 모양 칸을 잠그고 이유를
    말한다."""
    return [
        PropertyDefOut.model_validate(one).model_copy(
            update={"interface_slug": wanted[one.key][1] if one.key in wanted else None}
        )
        for one in rows
    ]


def _properties_of(db: Session, owner_id: uuid.UUID) -> list[PropertyDef]:
    return list(
        db.scalars(
            select(PropertyDef)
            .where(PropertyDef.owner_kind == "type", PropertyDef.owner_id == owner_id)
            .order_by(PropertyDef.sort_order, PropertyDef.label)
        )
    )


@router.post("/types/{slug}/properties", response_model=PropertyDefOut, status_code=201)
def create_property(
    slug: str,
    payload: PropertyDefWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PropertyDef:
    owner = _type(db, slug)
    managed.require_definition_editable(owner)
    key = require_key(payload.key)
    require_choice(payload.data_type, DATA_TYPES, what="속성 종류")
    _check_property_shape(payload)
    _check_ref_target(db, payload.ref_type_slug)

    exists = db.scalar(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type",
            PropertyDef.owner_id == owner.id,
            PropertyDef.key == key,
        )
    )
    if exists is not None:
        raise Conflict(code("ONTOLOGY", 34), f"이미 있는 속성입니다: {key}")

    row = PropertyDef(
        owner_kind="type",
        owner_id=owner.id,
        key=key,
        label=payload.label,
        data_type=payload.data_type,
        unit=payload.unit,
        help=payload.help,
        required=payload.required,
        multi=payload.multi,
        enum_options=payload.enum_options,
        ref_type_slug=payload.ref_type_slug,
        inverse_label=payload.inverse_label.strip(),
        min_value=payload.min_value,
        max_value=payload.max_value,
        decimals=payload.decimals,
        pattern=payload.pattern,
        default_value=payload.default_value,
        unique=payload.unique,
        section=payload.section,
        sort_order=payload.sort_order,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        user,
        action="ontology.property.create",
        table="property_defs",
        row_id=row.id,
        label=f"{slug}.{key}",
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/types/{slug}/properties/{key}", response_model=PropertyDefOut)
def update_property(
    slug: str,
    key: str,
    payload: PropertyDefWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PropertyDef:
    row = _property(db, slug, key)
    managed.require_definition_editable(_type(db, slug))
    require_choice(payload.data_type, DATA_TYPES, what="속성 종류")
    _check_property_shape(payload)
    _check_ref_target(db, payload.ref_type_slug)
    wanted = _contract_of(db, slug)
    if key in wanted and payload.data_type == row.data_type:
        # **공통 속성의 모양은 인터페이스에서만 바뀐다.** 이름 · 도움말 · 묶음 · 순서 ·
        # 기본값 · 유일은 타입마다 정하므로 그대로 받는다.
        iprop, iface = wanted[key]
        sent = interfaces.shape_of(payload.model_dump())
        now = interfaces.shape_of(row)
        moved = [
            name
            for name in interfaces.SHAPE_FIELDS
            if getattr(sent, name) != getattr(now, name)
        ]
        if iprop.shape.required and not payload.required:
            moved.append("required")
        if moved:
            said = ", ".join(interfaces.FIELD_LABELS[name] for name in moved)
            raise Conflict(
                code("ONTOLOGY", 8),
                f"{slug}.{key} 는 인터페이스 {iface} 의 공통 속성이라 모양({said})은 "
                "인터페이스에서 수정합니다.",
                details={"interface": iface, "fields": moved},
            )

    # **키와 종류는 수정으로 안 바꾼다.** 키를 바꾸면 이미 저장된 값이 전부 고아가 되고,
    # 종류만 바꾸면 그 값들이 새 종류에 안 맞는데 **화면은 아무 말도 안 한다.** 종류는
    # 「종류 변경」(`retype_property`)이 저장값을 변환하는 계획을 먼저 보여 주고 바꾼다.
    if payload.data_type != row.data_type:
        raise Conflict(
            code("ONTOLOGY", 35),
            f"속성 종류는 수정으로 바꾸지 않습니다({row.data_type} → {payload.data_type}) — "
            "「종류 변경」(POST …/retype)이 저장된 값을 변환하는 계획을 먼저 보여 줍니다.",
        )
    row.label = payload.label
    row.unit = payload.unit
    row.help = payload.help
    row.required = payload.required
    row.multi = payload.multi
    row.enum_options = payload.enum_options
    row.ref_type_slug = payload.ref_type_slug
    row.inverse_label = payload.inverse_label.strip()
    row.min_value = payload.min_value
    row.max_value = payload.max_value
    row.decimals = payload.decimals
    row.pattern = payload.pattern
    row.default_value = payload.default_value
    row.unique = payload.unique
    row.section = payload.section
    row.sort_order = payload.sort_order
    _audit(
        db,
        user,
        action="ontology.property.update",
        table="property_defs",
        row_id=row.id,
        label=f"{slug}.{key}",
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/types/{slug}/properties/{key}/rename-option", response_model=RenameOptionOut)
def rename_option(
    slug: str,
    key: str,
    payload: RenameOptionRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RenameOptionOut:
    """고를 값의 이름을 바꾸면서 **저장된 값도 함께** 바꾼다.

    이름만 바꾸면 이미 저장된 값이 옛 이름으로 남아 거르기에서 빠지고, 그 사실은
    아무 데도 안 뜬다. `apply=false` 면 몇 개가 함께 바뀔지만 말한다.
    """
    owner = _type(db, slug)
    managed.require_definition_editable(owner)
    definition = _property(db, slug, key)
    _refuse_bound(db, slug, key, what="고를 값 이름을 여기서 바꾸지 않습니다")
    if payload.apply:
        plan = codebook.apply_rename(
            db, user, owner, definition, payload.from_value, payload.to_value
        )
        return RenameOptionOut(applied=not plan.errors, **vars(plan))
    plan = codebook.plan_rename(db, owner, definition, payload.from_value, payload.to_value)
    return RenameOptionOut(applied=False, **vars(plan))


@router.post("/types/{slug}/properties/{key}/promote", response_model=PromoteOut)
def promote_property(
    slug: str,
    key: str,
    payload: PromoteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PromoteOut:
    """enum 속성을 **코드표(참조 타입)** 로 승격한다.

    옵션마다 객체가 생기고(또는 있는 것에 붙고), 저장된 문자열이 그 객체를 가리키게
    바뀐다. 정의가 바뀌므로 **되돌릴 자리(스냅샷)** 를 먼저 남긴다 — 값 이전은
    스냅샷이 못 되돌리지만, 정의만이라도 되돌릴 수 있어야 다음 수를 둘 수 있다.
    """
    owner = _type(db, slug)
    managed.require_definition_editable(owner)
    definition = _property(db, slug, key)
    _refuse_bound(db, slug, key, what="코드표로 승격하지 않습니다")
    args = {
        "existing_slug": payload.target_type_slug,
        "new_slug": payload.new_slug,
        "new_label": payload.new_label,
    }
    if not payload.apply:
        plan = codebook.plan_promote(db, owner, definition, **args)
        return _promote_out(plan, applied=False, snapshot_id=None)
    snapshot = _snapshot(db, user, reason=f"승격 직전: {slug}.{key}")
    snapshot_id = snapshot.id
    plan = codebook.apply_promote(
        db, user, owner, definition, nav_group_slug=payload.nav_group_slug, **args
    )
    if plan.errors:
        db.rollback()
        return _promote_out(plan, applied=False, snapshot_id=None)
    return _promote_out(plan, applied=True, snapshot_id=snapshot_id)


# --- 종류 변경 -------------------------------------------------------------------

#: 대체 값 상한 — 계획이 보여 주는 변환할 수 없는 값(300종)보다 넉넉히.
MAPPING_MAX = 1000
MAPPING_TEXT_MAX = 500


def _retype_wanted(payload: RetypeRequest, *, with_unique: bool) -> dict[str, Any]:
    """종류 변경이 덮을 칸 — 보낸 것만(`None` 이면 지금 그대로)."""
    wanted: dict[str, Any] = {
        "data_type": payload.data_type,
        "enum_options": payload.enum_options,
        "min_value": payload.min_value,
        "max_value": payload.max_value,
        "decimals": payload.decimals,
        "pattern": payload.pattern,
    }
    if payload.unit is not None:
        wanted["unit"] = payload.unit.strip()
    if with_unique and payload.unique is not None:
        wanted["unique"] = payload.unique
    if payload.data_type == "object_ref":
        wanted["ref_type_slug"] = payload.ref_type_slug
        if payload.inverse_label is not None:
            wanted["inverse_label"] = payload.inverse_label.strip()
    return wanted


def _check_retype(db: Session, before: str, payload: RetypeRequest, *, name: str) -> None:
    require_choice(payload.data_type, DATA_TYPES, what="속성 종류")
    wrong = retype.unsupported(before, payload.data_type)
    if wrong:
        raise Conflict(code("ONTOLOGY", 64), f"{name}: {wrong}")
    if payload.data_type == "object_ref":
        # 이름을 풀 곳 — 「아무 타입이나」 로는 값마다 어디서 찾을지 정해지지 않는다.
        if not payload.ref_type_slug:
            raise InvalidValue(
                code("ONTOLOGY", 66),
                f"{name}: 객체 참조로 바꾸려면 가리킬 타입(ref_type_slug)을 정하세요.",
            )
        _check_ref_target(db, payload.ref_type_slug)
    _check_shape_fields(
        payload.data_type, payload.enum_options, payload.min_value, payload.max_value
    )
    too_long = [
        key
        for key, value in payload.mapping.items()
        if len(key) > MAPPING_TEXT_MAX or (value is not None and len(value) > MAPPING_TEXT_MAX)
    ]
    if len(payload.mapping) > MAPPING_MAX or too_long:
        raise InvalidValue(
            code("ONTOLOGY", 65),
            f"대체 값은 {MAPPING_MAX}개까지, 값마다 {MAPPING_TEXT_MAX}자까지입니다 — 그보다 "
            "많으면 객체를 먼저 고치세요.",
        )


def _retype_out(
    planned: retype.RetypePlan,
    *,
    before: str,
    after: str,
    applied: bool,
    core_consumers: list[str],
    snapshot_id: uuid.UUID | None,
) -> RetypeOut:
    def value(row: retype.ValueRow) -> RetypeValueOut:
        return RetypeValueOut(
            value=row.value,
            count=row.count,
            reason=row.reason,
            to=row.to,
            samples=[
                RetypeSampleOut(
                    type_slug=one.type_slug, object_id=one.object_id, label=one.label
                )
                for one in row.samples
            ],
        )

    return RetypeOut(
        applied=applied,
        data_type_before=before,
        data_type_after=after,
        types=[
            RetypeTypeOut(
                type_slug=one.type_slug,
                type_label=one.type_label,
                key=one.key,
                via=one.via,
                with_value=one.with_value,
                converted=one.converted,
                unchanged=one.unchanged,
                cleared=one.cleared,
            )
            for one in planned.counts
        ],
        failures=[value(one) for one in planned.failures],
        failures_total=planned.failures_total,
        mapped=[value(one) for one in planned.mapped],
        errors=planned.errors,
        warnings=planned.warnings,
        core_consumers=core_consumers,
        snapshot_id=snapshot_id,
    )


@router.post("/types/{slug}/properties/{key}/retype", response_model=RetypeOut)
def retype_property(
    slug: str,
    key: str,
    payload: RetypeRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RetypeOut:
    """**종류 변경** — 속성 종류를 바꾸면서 저장된 값도 새 종류로 변환한다(ADR 0007).

    `apply=false` 면 계획만: 타입의 건수 · 변환할 수 없는 값(값마다 건수 · 견본 · 까닭) · 경고.
    변환할 수 없는 값이 하나라도 남아 있으면 적용하지 않는다 — `mapping` 으로 값마다 대체 값을
    적거나 값 삭제(`null`)를 고른다. 적용 직전 정의를 스냅샷으로 남기고, 객체마다 이력이
    남는다.

    값이 있는 객체가 2만 건을 넘으면 ONTOLOGY-67 — `…/retype/job` 으로 작업을 만든다(기록 200만
    건의 변환은 요청 안에서 끝나지 않는다).
    """
    return _retype_type(db, user, slug, key, payload)


@router.post(
    "/types/{slug}/properties/{key}/retype/job", response_model=JobOut, status_code=202
)
def retype_property_job(
    slug: str,
    key: str,
    payload: RetypeRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> JobOut:
    """종류 변경을 **작업으로** — 계획 작업을 만든다. 사람이 결과를 보고 `POST
    /api/jobs/{id}/apply` 로 적용한다(그 사이 값이 바뀌었으면 지문이 막는다)."""
    _type(db, slug)
    row = _property(db, slug, key)
    _check_retype(db, row.data_type, payload, name=f"{slug}.{key}")
    return _retype_job(db, user, owner="type", slug=slug, key=key, payload=payload)


def _retype_type(
    db: Session,
    user: User,
    slug: str,
    key: str,
    payload: RetypeRequest,
    *,
    inline: bool = True,
    before_apply: Callable[[retype.RetypePlan], None] | None = None,
    on_progress: retype.Progress | None = None,
) -> RetypeOut:
    owner = _type(db, slug)
    managed.require_definition_editable(owner)
    row = _property(db, slug, key)
    _refuse_bound(db, slug, key, what="종류를 여기서 변경하지 않습니다")
    _check_retype(db, row.data_type, payload, name=f"{slug}.{key}")
    target, notes = retype.target_for(owner, row, _retype_wanted(payload, with_unique=True))
    if inline:
        _refuse_large(db, [target], path=f"/api/ontology/types/{slug}/properties/{key}")
    before, after = row.data_type, payload.data_type
    consumers = _core_consumers(db, owner)

    def out(planned: retype.RetypePlan, **more: Any) -> RetypeOut:
        return _retype_out(
            planned, before=before, after=after, core_consumers=consumers, **more
        )

    downstream = retype.downstream(db, [target])
    if not (payload.apply and not inline):
        planned = retype.plan(db, [target], payload.mapping, on_progress=on_progress)
        planned.warnings[:0] = notes
        planned.warnings.extend(downstream)
        if not payload.apply or planned.errors:
            db.rollback()
            return out(planned, applied=False, snapshot_id=None)

    # 작업의 적용은 계획을 다시 따로 세우지 않는다 — 사람이 본 계획은 지문이 지킨다
    # (`before_apply`). 200만 건을 세 번 읽지 않게.
    _require_core_accepted(db, owner, accepted=payload.accept_core, what="속성 종류 변경")
    snapshot = _snapshot(db, user, reason=f"종류 변경 직전: {slug}.{key}")
    done = retype.apply(
        db,
        user,
        [target],
        payload.mapping,
        before_apply=before_apply,
        on_progress=on_progress,
    )
    done.warnings[:0] = notes
    done.warnings.extend(downstream)
    if done.errors:
        db.rollback()
        return out(done, applied=False, snapshot_id=None)
    db.commit()
    return out(done, applied=True, snapshot_id=snapshot.id)


@router.post("/interfaces/{slug}/properties/{key}/retype", response_model=RetypeOut)
def retype_interface_property(
    slug: str,
    key: str,
    payload: RetypeRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RetypeOut:
    """공통 속성의 **종류 변경** — 구현 타입 전부의 저장값을 **한 트랜잭션에서** 변환한다.

    한 타입이라도 변환할 수 없는 값이 남아 있거나 모양이 안 맞으면 아무것도 안 바뀐다 — 한
    타입만 바뀌면 같은 공통 속성이 타입마다 다른 종류를 갖는다. 대체 값은 구현 타입 전부에
    걸린다. 값이 있는 객체가 구현 타입을 통틀어 2만 건을 넘으면 ONTOLOGY-67(`…/retype/job`).
    """
    return _retype_interface(db, user, slug, key, payload)


@router.post(
    "/interfaces/{slug}/properties/{key}/retype/job", response_model=JobOut, status_code=202
)
def retype_interface_property_job(
    slug: str,
    key: str,
    payload: RetypeRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> JobOut:
    """공통 속성의 종류 변경을 **작업으로** — 계획 작업을 만든다."""
    _interface(db, slug)
    row = _interface_property(db, slug, key)
    _check_retype(db, row.data_type, payload, name=f"{slug}.{key}")
    return _retype_job(db, user, owner="interface", slug=slug, key=key, payload=payload)


def _retype_interface(
    db: Session,
    user: User,
    slug: str,
    key: str,
    payload: RetypeRequest,
    *,
    inline: bool = True,
    before_apply: Callable[[retype.RetypePlan], None] | None = None,
    on_progress: retype.Progress | None = None,
) -> RetypeOut:
    owner = _interface(db, slug)
    managed.require_definition_editable(owner)
    row = _interface_property(db, slug, key)
    _check_retype(db, row.data_type, payload, name=f"{slug}.{key}")
    wanted = _retype_wanted(payload, with_unique=False)
    shape = interfaces.shape_of(wanted, interfaces.shape_of(row))
    wrong = interfaces.interface_property_error(key, {**shape.written(), "unique": False})
    if wrong:
        raise InvalidValue(code("ONTOLOGY", 24), wrong)

    catalog = interfaces.load(db)
    after_catalog = catalog.clone()
    have = after_catalog.interfaces[slug].props[key]
    after_catalog.interfaces[slug].props[key] = interfaces.Prop(
        key=key,
        shape=shape,
        label=have.label,
        help=have.help,
        section=have.section,
        sort_order=have.sort_order,
    )
    bindings = interfaces.plan_bindings(catalog, after_catalog)
    targets, notes = retype.targets_from_bindings(db, bindings)
    if inline:
        _refuse_large(db, targets, path=f"/api/ontology/interfaces/{slug}/properties/{key}")
    before, after = row.data_type, payload.data_type
    implementers = list(
        db.scalars(
            select(ObjectType).where(ObjectType.id.in_({one.type_id for one in targets}))
        )
    )
    consumers = sorted({one for kind in implementers for one in _core_consumers(db, kind)})

    def out(planned: retype.RetypePlan, **more: Any) -> RetypeOut:
        return _retype_out(
            planned, before=before, after=after, core_consumers=consumers, **more
        )

    conflicts = interfaces.conflicts(bindings)
    downstream = retype.downstream(db, targets)
    if not (payload.apply and not inline) or conflicts:
        planned = retype.plan(db, targets, payload.mapping, on_progress=on_progress)
        planned.errors[:0] = conflicts
        planned.warnings[:0] = notes
        planned.warnings.extend(downstream)
        if not payload.apply or planned.errors:
            db.rollback()
            return out(planned, applied=False, snapshot_id=None)

    for kind in implementers:
        _require_core_accepted(db, kind, accepted=payload.accept_core, what="속성 종류 변경")
    snapshot = _snapshot(db, user, reason=f"종류 변경 직전: 인터페이스 {slug}.{key}")
    for name, value in shape.written().items():
        setattr(row, name, value)
    db.flush()
    interfaces.apply_bindings(db, bindings)
    done = retype.apply(
        db,
        user,
        targets,
        payload.mapping,
        before_apply=before_apply,
        on_progress=on_progress,
    )
    done.warnings[:0] = notes
    done.warnings.extend(downstream)
    if done.errors:
        db.rollback()
        return out(done, applied=False, snapshot_id=None)
    _touch(owner)
    record_audit(
        db,
        action="ontology.interface_property.retype",
        actor=user,
        target_table="property_defs",
        target_id=row.id,
        target_label=f"{slug}.{key}",
        changes={"data_type": {"before": before, "after": after}},
        reason=f"구현 타입 {len(targets)}개의 저장값도 함께",
    )
    db.commit()
    return out(done, applied=True, snapshot_id=snapshot.id)


def _refuse_large(db: Session, targets: list[retype.Target], *, path: str) -> None:
    """값이 있는 객체가 많으면 요청 안에서 하지 않는다 — 작업으로 가라고 말한다."""
    if retype.count_rows(db, targets, cap=retype.RETYPE_INLINE) <= retype.RETYPE_INLINE:
        return
    raise Conflict(
        code("ONTOLOGY", 67),
        f"값이 있는 객체가 {retype.RETYPE_INLINE:,}건을 넘습니다 — 종류 변경을 작업으로 "
        f"돌립니다(POST {path}/retype/job). 계획을 본 뒤 적용합니다.",
        details={"limit": retype.RETYPE_INLINE, "job_path": f"{path}/retype/job"},
    )


def _retype_job(
    db: Session, user: User, *, owner: str, slug: str, key: str, payload: RetypeRequest
) -> JobOut:
    job = job_services.enqueue(
        db,
        kind="ontology_retype",
        params={
            "owner": owner,
            "slug": slug,
            "key": key,
            "request": payload.model_dump(mode="json", exclude={"apply"}),
            # 적용 작업을 만드는 토큰이 다를 수 있다 — 그때 다시 묻도록 범위를 적어 둔다.
            "needs_scope": "ontology:write",
        },
        user=user,
        workspace_id=None,
        input_file=None,
    )
    db.commit()
    db.refresh(job)
    return jobs_routes._out(db, job)


def run_retype_job(
    db: Session, user: User, params: dict[str, Any], progress: retype.Progress
) -> dict[str, Any]:
    """작업(`ontology_retype`)의 몸 — 화면과 **같은 함수**를 부른다. 적용이면 미리 본 계획의
    지문이 지금 계획과 같아야 쓴다."""
    payload = RetypeRequest(**params.get("request", {}), apply=bool(params.get("apply")))
    wanted = params.get("fingerprint")

    def guard(planned: retype.RetypePlan) -> None:
        if wanted and retype.fingerprint(planned) != wanted:
            raise Conflict(
                code("JOBS", 20),
                "미리 본 것과 달라졌습니다 — 그 사이에 누군가 바꿨습니다. 아무것도 바꾸지 "
                "않았으니 다시 계획을 보고 적용하세요.",
            )

    runner = _retype_type if params.get("owner") == "type" else _retype_interface
    result = runner(
        db,
        user,
        str(params.get("slug") or ""),
        str(params.get("key") or ""),
        payload,
        inline=False,
        before_apply=guard,
        on_progress=progress,
    )
    body = result.model_dump(mode="json")
    # 적용 작업이 이 지문을 들고 간다(`make_apply`) — 화면의 계획과 같은 값으로 센 것이다.
    body["fingerprint"] = retype.fingerprint_of(
        [
            (one.type_slug, one.key, one.with_value, one.converted, one.unchanged, one.cleared)
            for one in result.types
        ],
        [(one.value, one.count) for one in result.failures],
        result.failures_total,
        [(one.value, one.count, one.to) for one in result.mapped],
    )
    return body


def _promote_out(
    plan: codebook.PromotePlan, *, applied: bool, snapshot_id: uuid.UUID | None
) -> PromoteOut:
    return PromoteOut(
        applied=applied,
        target_slug=plan.target_slug,
        target_label=plan.target_label,
        target_new=plan.target_new,
        options=[PromoteOptionOut(**vars(one)) for one in plan.options],
        errors=plan.errors,
        warnings=plan.warnings,
        snapshot_id=snapshot_id,
    )


def _core_consumers(db: Session, object_type: ObjectType) -> list[str]:
    """이 타입이 바깥에 열려 있으면 **누가 읽을 수 있는지** 한 줄씩.

    닫혀 있으면 빈 목록이다 — 열지 않은 타입에까지 경고를 세우면 그 경고는 곧 안 읽힌다.
    """
    if not object_type.core:
        return []
    from app.modules.coreapi import services as core_services

    return [
        f"{one.name} ({one.last_used_at:%Y-%m-%d} 최종 사용)"
        if one.last_used_at
        else f"{one.name} (미사용)"
        for one in core_services.consumers(db)
    ]


def _require_core_accepted(
    db: Session, object_type: ObjectType, *, accepted: bool, what: str
) -> None:
    """**바깥에 연 타입의 약속을 말없이 깨지 않는다.**

    이 칸(또는 값)의 이름은 남의 시스템 코드에 박혀 있다. 지우면 그쪽에서 조용히 사라지고,
    그 사실은 이쪽 화면 어디에도 안 뜬다 — 그래서 한 번 더 묻는다. 막지는 않는다: 정말
    지워야 할 때가 있고, 그때 「코어를 껐다 켜기」 를 강요하면 그 사이 동기화가 실패한다.
    """
    if not object_type.core or accepted:
        return
    who = _core_consumers(db, object_type)
    raise Conflict(
        code("ONTOLOGY", 46),
        f"{object_type.label}은(는) 외부에 공개 중이라 {what} 전에 확인이 필요합니다 — "
        f"조회 가능한 토큰 {len(who)}개. 수신 시스템에 통보한 뒤 다시 실행하십시오.",
        details={"core_consumers": who},
    )


@router.get("/types/{slug}/properties/{key}/usage", response_model=PropertyUsage)
def property_usage(
    slug: str, key: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> PropertyUsage:
    """**지우기 전에 무엇이 사라지는지.** 화면의 확인 창이 이것을 읽어 말한다."""
    row = _property(db, slug, key)
    owner = _type(db, slug)
    return PropertyUsage(
        key=row.key,
        label=row.label,
        objects_with_value=_objects_with_value(db, [owner.id], key),
        core_open=owner.core,
        core_consumers=_core_consumers(db, owner),
    )


def _property_deletion(db: Session, owner: ObjectType, row: PropertyDef) -> _Deletion:
    found = _Deletion(label=f"{owner.label} · {row.label}")
    found.check(lambda: managed.require_definition_editable(owner))
    found.check(lambda: _refuse_bound(db, owner.slug, row.key, what="삭제하지 않습니다"))
    found.removes.append("속성 정의")
    if views.prune_field(owner.list_view or {}, row.key) != (owner.list_view or {}):
        found.removes.append("목록의 열")
    count = _objects_with_value(db, [owner.id], row.key)
    if count:
        found.keeps.append(
            f"저장값 {count}개 — 화면에서는 안 보이고, 같은 키로 정의를 되살리면 돌아옵니다"
        )
    # 막지는 않는다 — 확인을 받는다(`accept_core`). 그 확인은 삭제 경로가 따로 묻는다.
    found.core_consumers = _core_consumers(db, owner)
    if owner.core:
        found.warnings.append(
            f"{owner.label}은(는) 외부에 공개 중입니다 — 이 칸의 이름은 "
            "수신 시스템 코드에 박혀 있으니 통보한 뒤 지웁니다."
        )
    return found


def _objects_with_value(db: Session, type_ids: list[uuid.UUID], key: str) -> int:
    """이 키에 값을 가진 살아 있는 객체 수 — 지우거나 모양을 바꾸기 전에 무엇이 걸리는지."""
    if not type_ids:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.type_id.in_(type_ids),
                ObjectInstance.deleted_at.is_(None),
                ObjectInstance.properties.has_key(key),
            )
        )
        or 0
    )


@router.delete("/types/{slug}/properties/{key}", status_code=204)
def delete_property(
    slug: str,
    key: str,
    accept_core: bool = Query(
        default=False, description="외부 공개 타입의 속성 삭제를 확인했는지 여부"
    ),
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> None:
    """정의를 지운다. **값은 JSONB 에 그대로 남는다.**

    값까지 훑어 지우면 되돌릴 방법이 없다. 남겨 두면 정의를 되살렸을 때 값이
    돌아온다 — 실수로 지우는 일이 실제로 있기 때문이다. 대신 화면이 몇 개가
    안 보이게 되는지(`/usage`) 먼저 말한다.
    """
    row = _property(db, slug, key)
    owner = _type(db, slug)
    _property_deletion(db, owner, row).require()
    _require_core_accepted(db, owner, accepted=accept_core, what="이 속성 삭제")
    _snapshot(db, user, reason=f"삭제 직전: 속성 {slug}.{key}")
    # **안 걷어내면 그 뒤로 타입을 고칠 때마다 「없는 속성」 이라고 거절당한다** —
    # 그리고 사람은 자기가 방금 고친 것과 상관없는 그 오류를 이해할 수 없다.
    owner.list_view = views.prune_field(owner.list_view or {}, key)
    _audit(
        db,
        user,
        action="ontology.property.delete",
        table="property_defs",
        row_id=row.id,
        label=f"{slug}.{key}",
    )
    db.delete(row)
    db.commit()


def _property(db: Session, slug: str, key: str) -> PropertyDef:
    owner = _type(db, slug)
    row = db.scalar(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type",
            PropertyDef.owner_id == owner.id,
            PropertyDef.key == key,
        )
    )
    if row is None:
        raise NotFound(code("ONTOLOGY", 36), f"속성을 찾을 수 없습니다: {slug}.{key}")
    return row


def _check_property_shape(payload: PropertyDefWriteRequest) -> None:
    _check_shape_fields(
        payload.data_type, payload.enum_options, payload.min_value, payload.max_value
    )


def _check_shape_fields(
    data_type: str,
    enum_options: list[str] | None,
    min_value: float | None,
    max_value: float | None,
) -> None:
    """속성 정의 · 종류 변경이 함께 쓰는 모양 검사."""
    if min_value is not None and max_value is not None and min_value > max_value:
        # **뒤집힌 범위는 아무 값도 안 받는다.** 그런데 화면에는 「값이 틀렸다」
        # 로만 뜨므로, 정의가 잘못된 것을 아무도 못 찾는다.
        raise Conflict(
            code("ONTOLOGY", 38),
            f"아래 끝({min_value})이 위 끝({max_value})보다 큽니다. "
            "이러면 어떤 값도 못 넣습니다.",
        )
    if data_type == "enum" and not enum_options:
        raise Conflict(
            code("ONTOLOGY", 37),
            "고를 값 목록이 비어 있습니다. 선택 속성은 고를 것이 있어야 합니다.",
        )


# --- 스키마와 사이드바 ------------------------------------------------------


@router.get("/core-status", response_model=CoreStatusOut)
def core_status(
    request: Request,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> CoreStatusOut:
    """**무엇이 열려 있고, 누가 읽을 수 있고, 누가 받아 갔나** — 한 화면.

    셋이 흩어져 있으면(타입 목록 · 토큰 목록 · 감사 기록) 「지금 바깥으로 뭐가 나가고 있지」
    를 한눈에 답할 수 없고, 그러면 열어 둔 것을 잊는다.

    **`/api/core` 아래에 두지 않는 이유**: 그 아래는 좁은 토큰(`core:read`)이 읽을 수 있어서,
    거기 두면 받아 가는 쪽이 다른 연동의 이름까지 보게 된다. 여기는 시스템 관리자만.
    """
    from app.modules.coreapi import services as core_services

    # 주소는 **요청이 안다** — 설정에서 읽으면 역방향 프록시 뒤에서 틀린 주소를 준다.
    root = str(request.url).split("/ontology/core-status")[0].rstrip("/")
    return core_services.status(db, _, base=f"{root}/core")


@router.get("/core-kit")
def core_kit(
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> Response:
    """연동 키트 — 수신 측에 그대로 전달하는 한 벌(zip).

    안내서 · 설정 견본 · 수신 스크립트 · 연결 점검 스크립트가 들어 있고, **이 설치의
    주소와 공개 타입이 이미 채워져 있다.** 수신 측이 수정하는 것은 토큰 한 줄이다.

    코드를 작성할 수 있는 상대에게는 안내서만 전달해도 된다. 그렇지 않은 상대에게
    「개발해 주십시오」 라고 하면 대화가 몇 달 늘어나지만, 「이것을 실행하십시오」 는
    그날 끝난다.
    """
    from app.modules.coreapi import services as core_services

    root = str(request.url).split("/ontology/core-kit")[0].rstrip("/")
    body = core_services.kit_zip(db, user, base=f"{root}/core")
    return sheets.attachment(body, media="application/zip", stem="sp-core-client", ext="zip")


@router.get("/export")
def export_structure(
    format: str = Query(default="xlsx", pattern="^(xlsx|json)$"),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """**지금 온톨로지 구조 전부**를 파일 하나로 — 엑셀(기본) 또는 JSON.

    엑셀은 시트가 여럿이다: 개요 · 묶음 · 타입 · 속성 · 관계 종류 · 관계 속성(있으면) ·
    참조 칸, 그리고 **타입마다 그 타입의 속성 표 하나.** 정의 검토는 회의에서 하고,
    회의에 들고 가는 것은 표다 — 화면을 스무 번 눌러 옮겨 적는 일을 없앤다.

    JSON 은 `POST /ontology/import` 가 받는 모양 **그대로**다. 내보낸 파일을 고쳐
    다시 넣거나, 쌍둥이에 그대로 심을 수 있다 — 그래서 봉투를 씌우지 않는다.
    """
    if format == "json":
        body = json.dumps(
            schema_export.structure(db), ensure_ascii=False, indent=2, default=str
        )
        return sheets.attachment(
            body.encode("utf-8"),
            media="application/json; charset=utf-8",
            stem="ontology",
            ext="json",
        )
    return sheets.workbook_response(schema_export.pages(db), stem="ontology")


@router.post("/export", response_model=JobOut, status_code=202)
def export_everything(
    format: str = Query(default="xlsx", pattern="^(xlsx|json)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    """구조와 **그 안에 채워진 객체까지** 한 파일로 — 작업이 된다(202).

    결과는 `GET /api/jobs/{id}/download`. 구조만 필요하면 같은 경로의 `GET` 이 그 자리에서
    준다 — 데이터가 붙으면 타입 수만큼 행을 읽으므로 요청 안에서 만들면 큰 설치에서 끊긴다.

    엑셀은 구조 시트에 **타입마다 그 타입의 객체 행**이 붙는다(열은 일괄 입력이 받는 그대로).
    JSON 은 `POST /bundles/import` 가 받는 묶음이고, 객체가 **소유 부서마다** 나뉘어 담겨
    받는 쪽에서 부서까지 선다.

    **볼 수 있는 것만 나간다** — 내보내기라고 남의 부서 것이 따라 나가면 화면에서 막은 것을
    파일이 여는 셈이다.
    """
    job = jobs_routes.submit(
        db,
        user,
        kind="ontology_export",
        params={"format": format},
        upload=None,
        workspace_slug=None,
    )
    return jobs_routes._out(db, job)


@router.get("/schema", response_model=OntologySchemaOut)
def ontology_schema(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> OntologySchemaOut:
    """**자기 설명적 스키마 — MCP 의 입력.**

    이 하나를 읽으면 도구를 동적으로 만들 수 있다. 도메인마다 MCP 서버를 새로
    짜지 않아도 되는 이유가 이것이다.
    """
    counts = _counts(db)
    groups = list(db.scalars(select(NavGroup).order_by(NavGroup.sort_order, NavGroup.label)))
    group_slugs = {g.id: g.slug for g in groups}
    catalog = interfaces.load(db)
    type_ids = _type_ids(db)

    types: list[ObjectTypeSchema] = []
    for row in db.scalars(
        select(ObjectType).order_by(ObjectType.sort_order, ObjectType.label)
    ):
        base = _type_out(
            row,
            group_slugs.get(row.nav_group_id) if row.nav_group_id else None,
            counts.get(row.id, 0),
        )
        wanted = (
            interfaces.contract(catalog, row.interface_slugs)[0] if row.interface_slugs else {}
        )
        types.append(
            ObjectTypeSchema(
                **base.model_dump(),
                properties=_marked(_properties_of(db, row.id), wanted),
            )
        )

    ifaces = [
        ObjectInterfaceSchema(
            **_interface_out(row, catalog, counts, type_ids).model_dump(),
            properties=[
                PropertyDefOut.model_validate(one) for one in _interface_properties(db, row.id)
            ],
        )
        for row in db.scalars(
            select(ObjectInterface).order_by(ObjectInterface.sort_order, ObjectInterface.label)
        )
    ]

    return OntologySchemaOut(
        groups=[NavGroupOut.model_validate(g) for g in groups],
        interfaces=ifaces,
        types=types,
        relation_types=[RelationTypeOut.model_validate(r) for r in _relation_types(db)],
        reference_edges=[
            ReferenceEdgeOut(
                slug=kind.slug,
                label=kind.label,
                inverse_label=kind.inverse_label,
                src_type_slug=kind.src_type.slug,
                dst_type_slug=kind.target_slug,
                field_key=kind.key,
                multi=kind.multi,
            )
            for kind in refedges.kinds(db).values()
        ],
        data_types=list(DATA_TYPES),
        system_sources=[
            SystemSourceOut(key=one.key, label=one.label)
            for one in system_sources.system_sources()
        ],
        generated_at=datetime.now(UTC),
    )


@router.get("/nav", response_model=list[NavGroupNode])
def dynamic_nav(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[NavGroupNode]:
    """동적 사이드바. **정적 메뉴를 대체하지 않고 그 아래 합류한다.**

    빈 묶음은 내보내지 않는다 — 제목은 「여기 여럿이 있다」 는 신호라서, 하나도
    없는데 달면 거짓말이 된다.
    """
    groups = list(
        db.scalars(
            select(NavGroup)
            .where(NavGroup.is_active.is_(True))
            .order_by(NavGroup.sort_order, NavGroup.label)
        )
    )
    types = list(
        db.scalars(
            select(ObjectType)
            .where(ObjectType.is_active.is_(True), ObjectType.nav_group_id.is_not(None))
            .order_by(ObjectType.sort_order, ObjectType.label)
        )
    )

    # 묶음 안에서 **축이 먼저, 기록이 뒤**다(ADR 0011) — 기록은 「기록」 표를 달고 선다.
    types.sort(key=lambda t: t.usage == "log")
    items_of = {
        group.id: [
            {
                "label": t.label,
                "icon": t.icon,
                "to": f"/o/{t.slug}",
                "slug": t.slug,
                "usage": t.usage,
            }
            for t in types
            if t.nav_group_id == group.id
        ]
        for group in groups
    }
    # **아래에 항목이 있으면 상위 묶음도 보낸다.** 상위는 보통 제 타입이 없다(자식만 있다) —
    # 빈 묶음 규칙에 그대로 걸리면 자식들이 부모 없이 떠서 두 단계가 무너진다.
    alive = {group.id for group in groups if items_of[group.id]}
    for group in groups:
        if group.parent_id and group.id in alive:
            alive.add(group.parent_id)

    out: list[NavGroupNode] = []
    by_id = {group.id: group for group in groups}
    for group in groups:
        if group.id not in alive:
            continue
        parent = by_id.get(group.parent_id) if group.parent_id else None
        out.append(
            NavGroupNode(
                slug=group.slug,
                label=group.label,
                icon=group.icon,
                audience=group.audience,
                # 상위가 안 보이는 대상이면(또는 껐으면) 붙이지 않는다 — 없는 부모를 가리키면
                # 화면에서 그 묶음이 사라진다.
                parent=parent.slug if parent is not None and parent.id in alive else None,
                items=items_of[group.id],
            )
        )
    return out


# --- 가져오기와 되돌리기 -----------------------------------------------------


def _snapshot(db: Session, user: User, *, reason: str) -> OntologySnapshot:
    """지금 정의를 통째로 남긴다. **부르는 쪽이 커밋한다.** 묶음 가져오기와 한 벌이다."""
    return importer.take_snapshot(db, user, reason=reason)


def _plan_out(
    prepared: importer.Plan, *, applied: bool, snapshot_id: uuid.UUID | None
) -> ImportPlanOut:
    return ImportPlanOut(
        applied=applied,
        changes=[
            ChangeOut(kind=c.kind, slug=c.slug, action=c.action, fields=c.fields, via=c.via)
            for c in prepared.changes
        ],
        warnings=prepared.warnings,
        errors=prepared.errors,
        snapshot_id=snapshot_id,
    )


@router.post("/infer", response_model=InferOut)
def infer_from_file(
    upload: UploadFile = File(alias="file"),
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> InferOut:
    """CSV·JSON **데이터** 파일에서 타입 정의를 추론한다 — 열마다 역할과 종류를 제안.

    아무것도 안 바꾼다. 사람이 제안을 고쳐 `infer/build` → `import` → `objects/import-rows`
    로 이어 간다. 추론은 보수적이다 — 애매하면 글자로 둔다.

    열마다 **참조 후보**도 단다 — 값이 어느 있는 타입의 객체로 풀리나(`ref_candidates`).
    일괄 입력과 같은 이름 풀이로 세고, 확실할 때만 종류를 참조로 제안한다(ADR 0009)."""
    raw = upload.file.read()
    try:
        rows = bulk.parse_file(upload.filename or "rows.csv", raw)
    except InvalidValue as caught:
        raise Conflict(code("ONTOLOGY", 71), caught.message) from None
    if not rows:
        raise Conflict(code("ONTOLOGY", 71), "파일에 행이 없습니다.")
    if len(rows) > bulk.MAX_ROWS:
        raise Conflict(
            code("ONTOLOGY", 71),
            f"한 번에 {bulk.MAX_ROWS}행까지입니다 (보낸 행 {len(rows)}). 나눠 업로드하세요.",
        )
    inferred = inference.infer(rows)
    linking.attach(db, user, inferred, rows)
    return InferOut(
        rows=inferred.rows,
        columns=[InferColumnOut(**dataclasses.asdict(one)) for one in inferred.columns],
        raw_rows=rows,
    )


@router.post("/infer/build", response_model=InferBuildOut, response_model_by_alias=True)
def build_from_inferred(
    payload: InferBuildRequest,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> InferBuildOut:
    """사람이 고친 열 정의를 **정의 스키마**와 **가져올 행**으로 — 둘 다 기존 길로 넣는다."""
    slug = require_slug(payload.slug, what="타입 slug")
    require_choice(payload.key_policy, KEY_POLICIES, what="식별자 정책")
    require_choice(payload.usage, USAGES, what="축 · 기록")
    columns = [
        inference.ColumnGuess(**one.model_dump(exclude={"ref_candidates"}))
        for one in payload.columns
    ]
    taken: set[str] = set()
    for column in columns:
        if column.role == "property":
            column.key = require_key(column.key)
            if column.key in taken:
                raise Conflict(code("ONTOLOGY", 72), f"속성 키가 겹칩니다: {column.key}")
            taken.add(column.key)
            require_choice(column.data_type, DATA_TYPES, what="속성 종류")
            if column.data_type == "object_ref":
                if not column.ref_type_slug:
                    raise Conflict(
                        code("ONTOLOGY", 72),
                        f"참조 열 「{column.label}」 이 가리킬 타입을 고르세요.",
                    )
                _check_ref_target(db, column.ref_type_slug)
    if sum(one.role == "label" for one in columns) != 1:
        raise Conflict(code("ONTOLOGY", 72), "이름(label) 역할의 열이 정확히 하나여야 합니다.")
    inferred = inference.Inferred(rows=len(payload.raw_rows), columns=columns)
    return InferBuildOut(
        schema=inference.schema_of(
            inferred,
            slug=slug,
            label=payload.label,
            nav_group_slug=payload.nav_group_slug,
            key_policy=payload.key_policy,
            usage=payload.usage,
        ),
        import_rows=inference.rows_of(inferred, payload.raw_rows),
    )


@router.post("/import", response_model=ImportPlanOut)
def import_schema(
    payload: dict[str, Any],
    dry_run: bool = Query(default=True, description="적용하지 않고 계획만 본다"),
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ImportPlanOut:
    """정의를 통째로 받아 **한 트랜잭션으로** 적용한다.

    **기본이 `dry_run=true` 인 이유**: 기계가 부르는 자리이고, 실수가 기계 속도로
    반영되면 그 아래 쌓인 것이 전부 흔들린다. 적용은 **의도를 적어야** 일어난다.

    **더하고 고치기만 한다.** 「스키마에 없으니 지운다」 로 만들면 부분 스키마를
    한 번 보낸 날 그 타입의 객체가 통째로 갈 곳을 잃는다.
    """
    # **적용 직전의 모습을 남긴다.** 감사 로그는 누가 뭘 했는지는 알려 주지만
    # 되돌려 주지는 않는다. 적용은 **한 번만** 한다 — 예전에는 한 번 적용해 오류를 보고
    # 되돌린 뒤 다시 적용했는데, 종류 변경이 저장값까지 바꾸게 되면서 그 두 번이 값 변환
    # 두 번이 됐다.
    snapshot = None if dry_run else _snapshot(db, user, reason="가져오기")
    try:
        prepared = (
            importer.plan(db, payload) if dry_run else importer.apply(db, payload, actor=user)
        )
    except ValueError as caught:
        # **모르는 항목은 거절한다.** 조용히 무시하면 보낸 쪽은 적용된 줄 안다.
        db.rollback()
        raise Conflict(code("ONTOLOGY", 70), str(caught)) from None

    if snapshot is None or prepared.errors:
        # 오류가 하나라도 있으면 **아무것도 안 바꾼다**(남긴 스냅샷도 함께 되돌린다).
        db.rollback()
        return _plan_out(prepared, applied=False, snapshot_id=None)

    _audit(
        db,
        user,
        action="ontology.import",
        table="ontology_snapshots",
        row_id=snapshot.id,
        label=f"{len([c for c in prepared.changes if c.action != 'unchanged'])}건",
    )
    db.commit()
    return _plan_out(prepared, applied=True, snapshot_id=snapshot.id)


@router.get("/delete-plan", response_model=DeletePlanOut)
def delete_plan(
    kind: DeleteKind,
    slug: str,
    key: str | None = None,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DeletePlanOut:
    """**지우기 전에** — 무엇이 막고, 무엇이 함께 사라지고, 무엇이 남는지. 아무것도 안 바꾼다.

    삭제 경로와 **같은 함수**가 센다(`_Deletion`). 기계(MCP)가 지우기 전에 사람에게 보일
    자리다 — 「정말 삭제하시겠습니까」 만 묻는 창은 아무도 안 읽는다. `property` ·
    `interface_property` 는 `key` 가 있어야 한다.
    """
    found: _Deletion
    if kind in ("property", "interface_property") and not key:
        raise Conflict(
            code("ONTOLOGY", 83), f"{kind} 를 지우려면 속성 키(key)가 있어야 합니다."
        )
    if kind == "group":
        found = _group_deletion(db, _group(db, slug))
    elif kind == "type":
        found = _type_deletion(db, _type(db, slug))
    elif kind == "relation_type":
        found = _relation_type_deletion(db, _relation_type(db, slug))
    elif kind == "interface":
        found = _interface_deletion(db, _interface(db, slug))
    elif kind == "property":
        assert key is not None
        found = _property_deletion(db, _type(db, slug), _property(db, slug, key))
    else:
        assert key is not None
        found = _interface_property_deletion(
            db, _interface(db, slug), _interface_property(db, slug, key)
        )
    return DeletePlanOut(
        kind=kind,
        slug=slug,
        key=key,
        label=found.label,
        allowed=not found.blocking,
        blocking=[
            DeleteBlockOut(code=one.code, message=one.message) for one in found.blocking
        ],
        removes=found.removes,
        keeps=found.keeps,
        warnings=found.warnings,
        core_consumers=found.core_consumers,
        purge_deleted=found.doomed.objects,
    )


@router.post("/reset", response_model=ResetPlanOut)
def reset_ontology(
    payload: ResetRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ResetPlanOut:
    """정의를 통째로 비운다 — **되돌릴 수 없는 일.**

    `apply=false`(기본)면 **계획만**: 무엇이 몇 건 사라지는지 센다. 적용하려면 계획에
    실린 문구(`confirm_phrase`)를 그대로 보내야 한다.

    비우기 직전의 **정의는 스냅샷으로 남는다**(이력에서 되돌릴 수 있다). 객체와 관계는
    **안 돌아온다** — 그 비대칭을 화면이 분명히 말해야 한다.
    """
    found = reset.plan(db) if not payload.apply else None
    snapshot_id: uuid.UUID | None = None
    if payload.apply:
        # **지우기 전에 남긴다.** 순서가 바뀌면 남길 것이 이미 없다.
        snapshot = _snapshot(db, user, reason="온톨로지 초기화 직전")
        snapshot_id = snapshot.id
        found = reset.apply(db, confirm=payload.confirm)
        record_audit(
            db,
            action="ontology.reset",
            actor=user,
            target_table="object_types",
            target_id=None,
            target_label="온톨로지 초기화",
            changes={one.table: one.count for one in found.items if one.count},
            reason=f"스냅샷 {snapshot.id}",
        )
        db.commit()
    assert found is not None
    return ResetPlanOut(
        applied=found.applied,
        items=[
            ResetItemOut(table=one.table, label=one.label, count=one.count)
            for one in found.items
        ],
        total=found.total,
        confirm_phrase=found.confirm_phrase,
        snapshot_id=snapshot_id,
    )


@router.get("/snapshots", response_model=list[SnapshotOut])
def list_snapshots(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[SnapshotOut]:
    rows = db.scalars(
        # **id 까지 보고 세운다.** 시각이 같은 둘(한 요청이 남기는 「복원 직전」 과 복원)은
        # 순서가 질의마다 달라지고, 그러면 목록의 첫 줄이 매번 다른 것을 가리킨다.
        select(OntologySnapshot)
        .order_by(OntologySnapshot.taken_at.desc(), OntologySnapshot.id.desc())
        .limit(50)
    )
    return [
        SnapshotOut(
            id=row.id,
            taken_at=row.taken_at,
            actor_label=row.actor_label,
            reason=row.reason,
            type_count=len((row.schema or {}).get("types") or []),
            relation_count=len((row.schema or {}).get("relation_types") or []),
            interface_count=len((row.schema or {}).get("interfaces") or []),
        )
        for row in rows
    ]


@router.post("/snapshots/{snapshot_id}/restore", response_model=ImportPlanOut)
def restore_snapshot(
    snapshot_id: uuid.UUID,
    dry_run: bool = Query(
        default=False, description="되돌리지 않고 계획만 본다 — 무엇이 바뀌고 무엇을 잃는지"
    ),
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ImportPlanOut:
    """그때의 정의를 다시 덮어씌운다.

    **그 뒤에 새로 만든 것은 안 지운다.** 지우면 그 사이에 쌓인 객체가 통째로 갈
    곳을 잃는다 — 되돌리기가 그것까지 하면 되돌리기 자체가 위험해진다.

    `dry_run` 은 가져오기의 미리 보기와 같은 계획이다(기계가 되돌리기 전에 사람에게 보일
    자리). 기본이 적용인 것은 화면이 확인 창을 거친 뒤에 부르기 때문이다.
    """
    row = db.get(OntologySnapshot, snapshot_id)
    if row is None:
        raise NotFound(code("ONTOLOGY", 71), "스냅샷을 찾을 수 없습니다.")

    # **옛 스냅샷도 되돌려진다** — 인터페이스 전의 것은 타입에 `parent_slug` 를 담고 있다.
    schema, notes = importer.upgrade_snapshot(row.schema or {})
    if dry_run:
        try:
            planned = importer.plan(db, schema)
        except ValueError as caught:
            db.rollback()
            raise Conflict(code("ONTOLOGY", 70), str(caught)) from None
        planned.warnings.extend(notes)
        db.rollback()
        return _plan_out(planned, applied=False, snapshot_id=None)

    # 되돌리기 **직전**도 남긴다 — 되돌린 것을 되돌릴 수 있어야 한다.
    before = _snapshot(db, user, reason=f"복원 직전 ({row.taken_at:%Y-%m-%d %H:%M})")
    try:
        # 누가 되돌렸는지가 값 변환의 이력에도 남는다(종류가 바뀐 뒤의 복원은 값도 변환한다).
        prepared = importer.apply(db, schema, actor=user, reason="스냅샷 복원")
    except ValueError as caught:
        db.rollback()
        raise Conflict(code("ONTOLOGY", 70), str(caught)) from None
    prepared.warnings.extend(notes)
    if prepared.errors:
        db.rollback()
        return _plan_out(prepared, applied=False, snapshot_id=None)

    _audit(
        db,
        user,
        action="ontology.restore",
        table="ontology_snapshots",
        row_id=row.id,
        label=f"{row.taken_at:%Y-%m-%d %H:%M} 로 되돌림",
    )
    db.commit()
    return _plan_out(prepared, applied=True, snapshot_id=before.id)
