"""객체 라우터 — **보이는 것과 고칠 수 있는 것은 다른 축이다.**

보기는 `visible_owner_clause`(전역 + 내 부서), 고치기는 `require_owner_edit`
(소유 부서의 관리자 또는 시스템 관리자).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import false, or_, select, true
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.files.models import Attachment
from app.modules.objects import (
    aliases,
    bulk,
    conditions,
    graph,
    history,
    lifecycle,
    links,
    quality,
    rollup,
    system,
)
from app.modules.objects import relations as rel
from app.modules.objects import summary as summary_service
from app.modules.objects.models import (
    OBJECT_STATUSES,
    ObjectAlias,
    ObjectInstance,
    ObjectLink,
    ObjectRelation,
    ObjectYear,
    SavedView,
)
from app.modules.objects.schemas import (
    AliasesRequest,
    AttachmentBrief,
    BucketOut,
    GroupOptionOut,
    HistoryEntryOut,
    ImportPlanOut,
    ImportRowOut,
    ImportRowsRequest,
    MergeRequest,
    MergeResultOut,
    ObjectCreateRequest,
    ObjectOut,
    ObjectPatchRequest,
    ObjectProfileOut,
    QualityFindingOut,
    QualityHitOut,
    QualityReportOut,
    ReferencesOut,
    RefHitOut,
    RelatedObjectOut,
    RelationCreateRequest,
    RelationHitOut,
    RelationPatchRequest,
    RestoreRequest,
    RollupOut,
    SavedViewOut,
    SavedViewPatchRequest,
    SavedViewQuery,
    SavedViewWriteRequest,
    SnapshotOut,
    SummaryOut,
    TreeNodeOut,
    TreeOut,
)
from app.modules.objects.services import (
    apply_property_filters,
    apply_search,
    apply_sort,
    apply_year,
    count_of,
    normalize_key,
    properties_of,
    require_key_free,
    require_refs_exist,
    require_unique_properties,
)
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.ontology.schemas import PropertyDefOut
from app.modules.ontology.services import (
    merge_properties,
    require_choice,
    validate_properties,
)
from app.modules.workspaces.models import Workspace
from app.shared import audit
from app.shared.auth import current_user
from app.shared.errors import Conflict, Forbidden, NotFound, code
from app.shared.pagination import Page, clamp_limit
from app.shared.permissions import (
    my_workspace_ids,
    require_owner_edit,
    resolve_owner_workspace,
    visible_owner_clause,
)

router = APIRouter(prefix="/objects", tags=["objects"])


# --- 공통 -------------------------------------------------------------------


def _type(db: Session, slug: str) -> ObjectType:
    row = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if row is None:
        raise NotFound(code("OBJECTS", 10), f"타입을 찾을 수 없습니다: {slug}")
    return row


def _not_system(object_type: ObjectType, what: str) -> None:
    """system 축은 **원 표를 투영**한다. 여기에 행을 만들면 두 벌이 되고, 두 벌은
    반드시 갈린다. 원 표의 화면(부서 관리·계정 관리)에서 한다."""
    if system.is_system(object_type):
        raise Forbidden(
            code("OBJECTS", 14),
            f"{object_type.label}은 다른 표를 비추는 타입이라 여기서 {what} 않습니다.",
        )


def _workspace_slugs(db: Session) -> dict[uuid.UUID, str]:
    return {row.id: row.slug for row in db.scalars(select(Workspace))}


def _ref_labels(
    db: Session, defs: list[PropertyDef], rows: list[ObjectInstance]
) -> dict[uuid.UUID, str]:
    """이 목록이 가리키는 객체들의 이름을 **한 번에** 읽는다.

    행마다 물으면 목록 한 쪽에 질의가 수십 개 붙는다. 어느 키가 참조인지는
    속성 정의가 알므로, 아무 문자열이나 uuid 로 넘겨짚지 않는다. 상대가 system
    타입이면 원 표에서 읽는다 — `system.ref_labels` 가 가른다.
    """
    return system.ref_labels(db, defs, [row.properties or {} for row in rows])


def _out(
    row: ObjectInstance,
    type_slug: str,
    workspaces: dict[uuid.UUID, str],
    ref_labels: dict[uuid.UUID, str] | None = None,
    alias_rows: list[ObjectAlias] | None = None,
) -> ObjectOut:
    names = [one.value for one in (alias_rows or []) if one.kind == aliases.HUMAN]
    external = {
        one.kind.split(":", 1)[1]: one.value
        for one in (alias_rows or [])
        if one.kind.startswith("source:")
    }
    return ObjectOut(
        id=row.id,
        type_slug=type_slug,
        key=row.key,
        label=row.label,
        description=row.description,
        properties=row.properties or {},
        ref_labels={str(k): v for k, v in (ref_labels or {}).items()},
        aliases=names,
        external_ids=external,
        status=row.status,
        owner_workspace_slug=(
            workspaces.get(row.owner_workspace_id) if row.owner_workspace_id else None
        ),
        valid_from_year=row.valid_from_year,
        valid_to_year=row.valid_to_year,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _visible(
    db: Session, user: User, object_type: ObjectType, object_id: uuid.UUID
) -> ObjectInstance:
    row = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == object_id,
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if row is None:
        # **없는 것과 안 보이는 것을 같은 말로 답한다.** 구별해 주면 남의 부서에
        # 무엇이 있는지 id 를 바꿔 가며 알아낼 수 있다.
        raise NotFound(code("OBJECTS", 11), "객체를 찾을 수 없습니다.")
    return row


def _can_edit(db: Session, user: User, row: ObjectInstance) -> bool:
    try:
        require_owner_edit(
            db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 12)
        )
    except Exception:
        return False
    return True


def _tree_spec(object_type: ObjectType) -> tuple[str | None, graph.ParentEnd]:
    """`list_view.tree` 가 정한 트리 관계와 부모 쪽 끝.

    안 정했으면 트리를 안 그린다 — **`transitive` 인 관계가 둘 이상일 수 있어서**
    아무거나 골라 그리면 그 트리는 무엇을 보여 주는지 말할 수 없다.
    """
    spec = (object_type.list_view or {}).get("tree") or {}
    relation = spec.get("relation")
    parent_end: graph.ParentEnd = "src" if spec.get("parent") == "src" else "dst"
    return (relation if isinstance(relation, str) and relation else None, parent_end)


# --- 품질 -------------------------------------------------------------------
#
# `/{type_slug}` 보다 **앞에** 선다 — 뒤에 두면 `quality` 를 타입 slug 로 읽고 404 를 낸다.


@router.get("/quality/report", response_model=QualityReportOut)
def quality_report(
    kind: str | None = Query(default=None, description="한 종류만"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> QualityReportOut:
    """필수값 빈 것 · 고아 · 깨진 참조 · 이름 같은 것. **볼 수 있는 것만 센다.**"""
    kinds = (kind,) if kind in quality.KINDS else quality.KINDS
    return QualityReportOut(
        findings=[
            QualityFindingOut(
                kind=one.kind,
                kind_label=quality.LABELS[one.kind],
                type_slug=one.type_slug,
                type_label=one.type_label,
                count=one.count,
                hits=[QualityHitOut(**vars(hit)) for hit in one.hits],
            )
            for one in quality.report(db, user, kinds=kinds)
        ],
        sample_limit=quality.SAMPLE,
    )


# --- 트리 -------------------------------------------------------------------


@router.get("/{type_slug}/summary", response_model=SummaryOut)
def summary(
    type_slug: str,
    request: Request,
    group_by: str = Query(
        default="status", description="묶을 축 — status·workspace·created_year·properties.<칸>"
    ),
    metric: str = Query(default="count", description="count·sum·avg·min·max"),
    metric_field: str | None = Query(default=None, description="합·평균을 낼 숫자 칸"),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    under: uuid.UUID | None = Query(default=None),
    deep: bool = Query(default=True),
    year: int | None = Query(default=None, ge=1900, le=2999),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SummaryOut:
    """묶어 보기 — **목록과 같은 거르기 위에서 센다.**

    거르기는 목록과 똑같이 온다(`?p.<칸>=`, `?f.<칸>.<연산>=`, q·status·year·under).
    따로 세면 「목록에는 12건인데 묶어 보면 15건」 이 되고, 그때 어느 쪽이 맞는지
    아무도 모른다.

    투영(system) 타입은 행이 없어 못 센다 — 원 표에 물어야 하는 일이고, 그 표의 축을
    이 틀은 모른다.
    """
    object_type = _type(db, type_slug)
    if system.is_system(object_type):
        raise Conflict(
            code("OBJECTS", 47),
            f"{object_type.label}은(는) 다른 표를 비추는 타입이라 여기서 세지 않습니다.",
        )
    stmt = _filtered(
        db, user, object_type, request, q=q, status=status, year=year, under=under, deep=deep
    )
    found = summary_service.summarize(
        db, object_type, stmt, group_by=group_by, metric=metric, metric_field=metric_field
    )
    defs = properties_of(db, object_type.id)
    return SummaryOut(
        group_field=found.group_field,
        group_label=found.group_label,
        metric=found.metric,
        metric_field=found.metric_field,
        metric_label=found.metric_label,
        total=found.total,
        buckets=[
            BucketOut(key=one.key, label=one.label, count=one.count, value=one.value)
            for one in found.buckets
        ],
        other_groups=found.other_groups,
        other_count=found.other_count,
        group_options=[
            GroupOptionOut(field=one.field, label=one.label, kind=one.kind)
            for one in summary_service.group_options(defs)
        ],
        metric_options=[
            GroupOptionOut(field=one.field, label=one.label, kind=one.kind)
            for one in summary_service.metric_options(defs)
        ],
    )


@router.get("/{type_slug}/tree", response_model=TreeOut)
def object_tree(
    type_slug: str,
    parent: uuid.UUID | None = Query(default=None, description="펼칠 노드. 없으면 뿌리"),
    orphans: bool = Query(default=False, description="어디에도 안 걸린 것만"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TreeOut:
    """트리 한 단계.

    **통째로 안 불러온다.** 펼칠 때 그 단계만 읽는다 — 안 그러면 부품 5천 개짜리
    트리에서 첫 화면이 안 뜬다.
    """
    object_type = _type(db, type_slug)
    relation, parent_end = _tree_spec(object_type)
    if relation is None:
        return TreeOut(nodes=[], orphan_count=0)

    if parent is not None:
        ids = graph.child_ids(db, relation=relation, parent_id=parent, parent_end=parent_end)
        orphan_count = 0
    else:
        parentless = graph.parentless_ids(
            db, relation=relation, type_id=object_type.id, parent_end=parent_end
        )
        counts = graph.child_counts(
            db, relation=relation, parent_ids=parentless, parent_end=parent_end
        )
        roots = [one for one in parentless if counts.get(one, 0) > 0]
        orphan_ids = [one for one in parentless if counts.get(one, 0) == 0]
        ids = orphan_ids if orphans else roots
        orphan_count = len(orphan_ids)

    if not ids:
        return TreeOut(nodes=[], orphan_count=orphan_count)

    # **볼 수 있는 것만 준다.** 남의 부서 것은 트리에도 안 나온다.
    rows = list(
        db.scalars(
            select(ObjectInstance)
            .where(
                ObjectInstance.id.in_(ids),
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
            .order_by(ObjectInstance.label)
        )
    )
    counts = graph.child_counts(
        db, relation=relation, parent_ids=[row.id for row in rows], parent_end=parent_end
    )
    return TreeOut(
        nodes=[
            TreeNodeOut(
                id=row.id,
                label=row.label,
                key=row.key,
                status=row.status,
                child_count=counts.get(row.id, 0),
            )
            for row in rows
        ],
        orphan_count=orphan_count,
    )


def _filtered(
    db: Session,
    user: User,
    object_type: ObjectType,
    request: Request,
    *,
    q: str | None,
    status: str | None,
    year: int | None,
    under: uuid.UUID | None = None,
    deep: bool = True,
) -> Any:
    """목록과 내보내기가 **같은 거르기**를 쓴다. 따로 적으면 「화면에는 있는데 파일에는
    없는」 줄이 생기고, 어느 쪽이 맞는지 아무도 모른다."""
    stmt = select(ObjectInstance).where(
        ObjectInstance.type_id == object_type.id,
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )
    if status:
        stmt = stmt.where(ObjectInstance.status == status)
    if q:
        stmt = apply_search(stmt, object_type, q)
    if year is not None:
        stmt = apply_year(db, stmt, object_type, year)
    if under is not None:
        # **기본은 「아래 것까지 포함」 이다.** 안 그러면 상위 노드를 눌렀을 때
        # 목록이 비고, 그 빈 목록은 「없다」 로 읽힌다.
        relation, parent_end = _tree_spec(object_type)
        if relation is None:
            raise Conflict(
                code("OBJECTS", 33),
                f"{object_type.label}에는 트리가 정의돼 있지 않습니다. "
                "타입의 「목록 화면」 에서 트리로 쓸 관계를 고르세요.",
            )
        wanted = [under]
        if deep:
            wanted += graph.descendant_ids(
                db, relation=relation, root_id=under, parent_end=parent_end
            )
        stmt = stmt.where(ObjectInstance.id.in_(wanted))
    filters = {
        key[2:]: value for key, value in request.query_params.items() if key.startswith("p.")
    }
    if filters:
        stmt = apply_property_filters(stmt, filters)
    # 조건 거르기 — `f.<칸>.<연산>=<값>`. 칸 안 OR, 칸끼리 AND.
    asked = conditions.parse(request.query_params)
    if asked:
        stmt = conditions.apply(stmt, properties_of(db, object_type.id), asked)
    return stmt


# --- 저장된 뷰 ----------------------------------------------------------------


def _view_out(db: Session, user: User, row: SavedView, type_slug: str) -> SavedViewOut:
    owner = db.get(User, row.owner_user_id)
    workspaces = _workspace_slugs(db)
    return SavedViewOut(
        id=row.id,
        type_slug=type_slug,
        name=row.name,
        query=SavedViewQuery.model_validate(row.query or {}),
        owner_user_id=row.owner_user_id,
        owner_label=owner.display_name if owner else "",
        workspace_slug=workspaces.get(row.workspace_id) if row.workspace_id else None,
        can_edit=_can_edit_view(db, user, row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _can_edit_view(db: Session, user: User, row: SavedView) -> bool:
    """내 것은 내가, 부서 것은 그 부서 관리자(또는 시스템 관리자)가."""
    if row.owner_user_id == user.id or user.is_system_admin:
        return True
    if row.workspace_id is None:
        return False
    try:
        require_owner_edit(
            db, user, row.workspace_id, what="뷰", code_value=code("OBJECTS", 80)
        )
    except Exception:
        return False
    return True


def _view(db: Session, user: User, object_type: ObjectType, view_id: uuid.UUID) -> SavedView:
    row = db.get(SavedView, view_id)
    if row is None or row.type_id != object_type.id:
        raise NotFound(code("OBJECTS", 81), "뷰를 찾을 수 없습니다.")
    mine = row.owner_user_id == user.id
    shared_with_me = row.workspace_id is not None and (
        user.is_system_admin or row.workspace_id in set(my_workspace_ids(db, user))
    )
    if not (mine or shared_with_me):
        raise NotFound(code("OBJECTS", 81), "뷰를 찾을 수 없습니다.")
    return row


@router.get("/{type_slug}/views", response_model=list[SavedViewOut])
def list_views(
    type_slug: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[SavedViewOut]:
    """내 뷰 + 내 부서와 함께 쓰는 뷰. 부서 것이 먼저, 그 안에서는 이름순."""
    object_type = _type(db, type_slug)
    mine = my_workspace_ids(db, user)
    stmt = select(SavedView).where(
        SavedView.type_id == object_type.id,
        or_(
            SavedView.owner_user_id == user.id,
            SavedView.workspace_id.in_(mine) if mine else false(),
            true() if user.is_system_admin else false(),
        ),
    )
    rows = sorted(
        db.scalars(stmt),
        key=lambda one: (one.workspace_id is None, one.name),
    )
    return [_view_out(db, user, one, type_slug) for one in rows]


@router.post("/{type_slug}/views", response_model=SavedViewOut, status_code=201)
def create_view(
    type_slug: str,
    payload: SavedViewWriteRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SavedViewOut:
    """뷰를 저장한다. 부서와 함께 쓰려면 그 부서의 관리자여야 한다 — 아무나 부서
    뷰를 만들면 목록이 곧 개인 취향으로 가득 찬다."""
    object_type = _type(db, type_slug)
    workspace_id: uuid.UUID | None = None
    if payload.workspace_slug:
        workspace_id = resolve_owner_workspace(
            db, user, payload.workspace_slug, what="뷰", code_value=code("OBJECTS", 82)
        )
    # 조건이 실제로 걸리는지 지금 검사한다 — 저장은 됐는데 열면 422 인 뷰는 아무도 못 고친다.
    conditions.apply(
        select(ObjectInstance),
        properties_of(db, object_type.id),
        [conditions.Condition(c.field, c.op, c.value) for c in payload.query.conditions],
    )
    row = SavedView(
        type_id=object_type.id,
        name=payload.name.strip(),
        owner_user_id=user.id,
        workspace_id=workspace_id,
        query=payload.query.model_dump(),
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="object.view.create",
        actor=user,
        target_table="saved_views",
        target_id=row.id,
        target_label=f"{type_slug}:{row.name}",
        workspace_id=workspace_id,
    )
    db.commit()
    db.refresh(row)
    return _view_out(db, user, row, type_slug)


@router.patch("/{type_slug}/views/{view_id}", response_model=SavedViewOut)
def update_view(
    type_slug: str,
    view_id: uuid.UUID,
    payload: SavedViewPatchRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> SavedViewOut:
    object_type = _type(db, type_slug)
    row = _view(db, user, object_type, view_id)
    if not _can_edit_view(db, user, row):
        raise Forbidden(code("OBJECTS", 83), "이 뷰를 고칠 수 없습니다.")
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.query is not None:
        conditions.apply(
            select(ObjectInstance),
            properties_of(db, object_type.id),
            [conditions.Condition(c.field, c.op, c.value) for c in payload.query.conditions],
        )
        row.query = payload.query.model_dump()
    audit.record(
        db,
        action="object.view.update",
        actor=user,
        target_table="saved_views",
        target_id=row.id,
        target_label=f"{type_slug}:{row.name}",
        workspace_id=row.workspace_id,
    )
    db.commit()
    db.refresh(row)
    return _view_out(db, user, row, type_slug)


@router.delete("/{type_slug}/views/{view_id}", status_code=204)
def delete_view(
    type_slug: str,
    view_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """뷰는 진짜로 지운다 — 데이터가 아니라 거르기 조건이라, 남겨 둘 이유가 없다."""
    object_type = _type(db, type_slug)
    row = _view(db, user, object_type, view_id)
    if not _can_edit_view(db, user, row):
        raise Forbidden(code("OBJECTS", 83), "이 뷰를 지울 수 없습니다.")
    audit.record(
        db,
        action="object.view.delete",
        actor=user,
        target_table="saved_views",
        target_id=row.id,
        target_label=f"{type_slug}:{row.name}",
        workspace_id=row.workspace_id,
    )
    db.delete(row)
    db.commit()


# --- 일괄 -------------------------------------------------------------------


def _plan_out(plan: bulk.Plan, applied: bool) -> ImportPlanOut:
    return ImportPlanOut(
        applied=applied,
        rows=[
            ImportRowOut(
                row=one.row,
                action=one.action,
                label=one.label,
                key=one.key,
                object_id=one.object_id,
                changes=one.changes,
                message=one.message,
            )
            for one in plan.rows
        ],
        errors=plan.errors,
        counts=plan.counts,
    )


def _file_rows(upload: UploadFile) -> list[dict[str, Any]]:
    name = upload.filename or "rows.csv"
    if not name.lower().endswith((".csv", ".json")):
        raise Conflict(code("OBJECTS", 48), "CSV 나 JSON 파일만 받습니다.")
    return bulk.parse_file(name, upload.file.read())


def _import_objects(
    db: Session,
    user: User,
    object_type: ObjectType,
    rows: list[dict[str, Any]],
    *,
    workspace_slug: str | None,
    apply: bool,
) -> ImportPlanOut:
    owner_workspace_id = resolve_owner_workspace(
        db, user, workspace_slug, what="객체", code_value=code("OBJECTS", 15)
    )
    if apply:
        plan = bulk.apply_objects(
            db, user, object_type, rows, owner_workspace_id=owner_workspace_id
        )
        return _plan_out(plan, applied=plan.ok)
    plan = bulk.plan_objects(
        db, user, object_type, rows, owner_workspace_id=owner_workspace_id
    )
    return _plan_out(plan, applied=False)


@router.get("/{type_slug}/template")
def object_template(
    type_slug: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """빈 CSV — 헤더가 「무엇을 채워야 하는지」 를 말한다."""
    object_type = _type(db, type_slug)
    _not_system(object_type, "파일로 넣지")
    defs = properties_of(db, object_type.id)
    body = bulk.to_csv(bulk.export_columns(defs), [])
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{type_slug}-template.csv"'},
    )


@router.get("/{type_slug}/export")
def export_objects(
    type_slug: str,
    request: Request,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    year: int | None = Query(default=None, ge=1900, le=2999),
    under: uuid.UUID | None = Query(default=None),
    deep: bool = Query(default=True),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """지금 거른 목록 **그대로** 파일로. 쪽 상한 없이 전부 — 목록의 상한은 화면을
    위한 것이고, 파일은 그 상한을 넘어서기 위해 있다."""
    object_type = _type(db, type_slug)
    _not_system(object_type, "내보내지")
    defs = properties_of(db, object_type.id)
    stmt = _filtered(
        db, user, object_type, request, q=q, status=status, year=year, under=under, deep=deep
    )
    rows = list(db.scalars(apply_sort(stmt, object_type)))
    records = bulk.export_rows(db, defs, rows)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    if format == "json":
        payload = json.dumps({"rows": records}, ensure_ascii=False, indent=1)
        return Response(
            payload.encode("utf-8"),
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{type_slug}-{stamp}.json"'
            },
        )
    body = bulk.to_csv(bulk.export_columns(defs), records)
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{type_slug}-{stamp}.csv"'},
    )


@router.post("/{type_slug}/import", response_model=ImportPlanOut)
def import_objects(
    type_slug: str,
    upload: UploadFile = File(alias="file"),
    workspace_slug: str | None = Form(default=None),
    apply: bool = Form(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ImportPlanOut:
    """파일로 넣기 — `apply=false` 면 **계획만**. 사람이 읽고 판단한 뒤 다시 부른다."""
    object_type = _type(db, type_slug)
    rows = _file_rows(upload)
    return _import_objects(
        db, user, object_type, rows, workspace_slug=workspace_slug, apply=apply
    )


@router.post("/{type_slug}/import-rows", response_model=ImportPlanOut)
def import_object_rows(
    type_slug: str,
    payload: ImportRowsRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ImportPlanOut:
    """JSON 행으로 넣기 — 파일과 같은 규칙. MCP 가 쓴다."""
    object_type = _type(db, type_slug)
    return _import_objects(
        db,
        user,
        object_type,
        payload.rows,
        workspace_slug=payload.workspace_slug,
        apply=payload.apply,
    )


@router.get("/{type_slug}/relations/export")
def export_relations(
    type_slug: str,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """이 타입에서 **출발하는** 관계 전부 — `src, relation, dst, evidence_note`."""
    object_type = _type(db, type_slug)
    records = bulk.export_relations(db, user, object_type)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    if format == "json":
        payload = json.dumps({"rows": records}, ensure_ascii=False, indent=1)
        return Response(
            payload.encode("utf-8"),
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{type_slug}-relations-{stamp}.json"'
                )
            },
        )
    return Response(
        bulk.to_csv(list(bulk.RELATION_COLUMNS), records),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{type_slug}-relations-{stamp}.csv"'
        },
    )


@router.post("/{type_slug}/relations/import", response_model=ImportPlanOut)
def import_relations(
    type_slug: str,
    upload: UploadFile = File(alias="file"),
    apply: bool = Form(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ImportPlanOut:
    object_type = _type(db, type_slug)
    rows = _file_rows(upload)
    if apply:
        plan = bulk.apply_relations(db, user, object_type, rows)
        return _plan_out(plan, applied=plan.ok)
    return _plan_out(bulk.plan_relations(db, user, object_type, rows), applied=False)


@router.post("/{type_slug}/relations/import-rows", response_model=ImportPlanOut)
def import_relation_rows(
    type_slug: str,
    payload: ImportRowsRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ImportPlanOut:
    object_type = _type(db, type_slug)
    if payload.apply:
        plan = bulk.apply_relations(db, user, object_type, payload.rows)
        return _plan_out(plan, applied=plan.ok)
    return _plan_out(bulk.plan_relations(db, user, object_type, payload.rows), applied=False)


# --- 목록 -------------------------------------------------------------------


@router.get("/{type_slug}", response_model=Page[ObjectOut])
def list_objects(
    type_slug: str,
    request: Request,
    q: str | None = Query(default=None, description="이름·식별자·검색 속성"),
    status: str | None = Query(default=None),
    under: uuid.UUID | None = Query(default=None, description="트리에서 고른 노드"),
    deep: bool = Query(default=True, description="아래 것까지 포함"),
    year: int | None = Query(
        default=None, ge=1900, le=2999, description="그 해에 해당하는 것만"
    ),
    limit: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Page[ObjectOut]:
    """이 타입의 객체 목록.

    거르기는 `?p.<속성키>=<값>` 으로 온다. `list_view` 가 열·정렬·검색 자리를
    정하고, 안 정해 뒀으면 기본형으로 떨어진다 — **빈 화면이 되지는 않는다.**
    """
    object_type = _type(db, type_slug)
    capped = clamp_limit(limit)

    if system.is_system(object_type):
        # 행이 없다 — 원 표를 그대로 투영한다. 거르기는 검색어뿐이다(속성이 없으므로).
        refs, total = system.source_of(object_type).search(db, user, q, capped, offset)
        now = datetime.now(UTC)
        return Page(
            items=[system.projected(ref, object_type.slug, now) for ref in refs],
            total=total,
            limit=capped,
            offset=offset,
        )

    stmt = _filtered(
        db, user, object_type, request, q=q, status=status, year=year, under=under, deep=deep
    )

    total = count_of(db, stmt)
    rows = db.scalars(apply_sort(stmt, object_type).limit(capped).offset(offset))

    found = list(rows)
    workspaces = _workspace_slugs(db)
    labels = _ref_labels(db, properties_of(db, object_type.id), found)
    names = aliases.of(db, [row.id for row in found])
    return Page(
        items=[
            _out(row, object_type.slug, workspaces, labels, names.get(row.id)) for row in found
        ],
        total=total,
        limit=capped,
        offset=offset,
    )


# --- 하나 -------------------------------------------------------------------


@router.get("/{type_slug}/{object_id}", response_model=ObjectProfileOut)
def object_profile(
    type_slug: str,
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ObjectProfileOut:
    object_type = _type(db, type_slug)
    if system.is_system(object_type):
        ref = system.find(db, object_type, object_id)
        if ref is None:
            raise NotFound(code("OBJECTS", 11), "객체를 찾을 수 없습니다.")
        return ObjectProfileOut(
            object=system.projected(ref, object_type.slug, datetime.now(UTC)),
            type_label=object_type.label,
            properties_schema=[],
            attachments=[],
            related=_related_links(db, user, object_id),
            # 원 표의 화면에서 고친다. 관계도 객체 쪽 끝에서 맺는다.
            can_edit=False,
        )
    try:
        row = _visible(db, user, object_type, object_id)
    except NotFound:
        # 합쳐져서 지워진 것이면 **어디로 갔는지 말한다** — 옛 링크가 새 것으로 간다.
        merged = db.scalar(
            select(ObjectInstance).where(
                ObjectInstance.id == object_id,
                ObjectInstance.type_id == object_type.id,
                ObjectInstance.merged_into_id.is_not(None),
            )
        )
        if merged is not None and merged.merged_into_id is not None:
            target = db.scalar(
                select(ObjectInstance).where(
                    ObjectInstance.id == merged.merged_into_id,
                    ObjectInstance.deleted_at.is_(None),
                    visible_owner_clause(user, ObjectInstance.owner_workspace_id),
                )
            )
            if target is not None:
                raise NotFound(
                    code("OBJECTS", 54),
                    f"{merged.label}은 {target.label}에 합쳐졌습니다.",
                    details={"merged_into": str(target.id), "type_slug": object_type.slug},
                ) from None
        raise

    attachments = db.scalars(
        select(Attachment)
        .where(
            Attachment.owner_table == "objects",
            Attachment.owner_id == row.id,
            Attachment.deleted_at.is_(None),
        )
        .order_by(Attachment.created_at.desc())
    )

    defs = properties_of(db, object_type.id)
    return ObjectProfileOut(
        object=_out(
            row,
            object_type.slug,
            _workspace_slugs(db),
            _ref_labels(db, defs, [row]),
            aliases.of(db, [row.id]).get(row.id),
        ),
        type_label=object_type.label,
        properties_schema=[PropertyDefOut.model_validate(p) for p in defs],
        attachments=[AttachmentBrief.model_validate(a) for a in attachments],
        related=_related(db, user, row),
        can_edit=_can_edit(db, user, row),
    )


@router.post("/{type_slug}", response_model=ObjectOut, status_code=201)
def create_object(
    type_slug: str,
    payload: ObjectCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ObjectOut:
    object_type = _type(db, type_slug)
    if not object_type.is_active:
        raise Forbidden(
            code("OBJECTS", 13), f"{object_type.label}은 지금 쓰지 않는 타입입니다."
        )
    _not_system(object_type, "만들지")

    owner_workspace_id = resolve_owner_workspace(
        db, user, payload.workspace_slug, what="객체", code_value=code("OBJECTS", 15)
    )
    key = normalize_key(object_type, payload.key)
    require_key_free(db, object_type, key, owner_workspace_id=owner_workspace_id)

    defs = properties_of(db, object_type.id)
    properties = validate_properties(defs, payload.properties, apply_defaults=True)
    require_refs_exist(db, defs, properties)
    require_unique_properties(
        db, object_type, defs, properties, owner_workspace_id=owner_workspace_id
    )

    row = ObjectInstance(
        type_id=object_type.id,
        key=key,
        label=payload.label,
        description=payload.description,
        properties=properties,
        owner_workspace_id=owner_workspace_id,
        valid_from_year=payload.valid_from_year,
        valid_to_year=payload.valid_to_year,
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="object.create",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=owner_workspace_id,
    )
    db.commit()
    db.refresh(row)
    return _out(row, object_type.slug, _workspace_slugs(db))


@router.patch("/{type_slug}/{object_id}", response_model=ObjectOut)
def update_object(
    type_slug: str,
    object_id: uuid.UUID,
    payload: ObjectPatchRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ObjectOut:
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 16)
    )

    before: dict[str, Any] = {
        "key": row.key,
        "label": row.label,
        "status": row.status,
        "properties": dict(row.properties or {}),
    }

    if payload.key is not None:
        key = normalize_key(object_type, payload.key)
        require_key_free(
            db, object_type, key, owner_workspace_id=row.owner_workspace_id, exclude_id=row.id
        )
        row.key = key
    if payload.label is not None:
        row.label = payload.label
    if payload.description is not None:
        row.description = payload.description
    if payload.status is not None:
        row.status = require_choice(payload.status, OBJECT_STATUSES, what="상태")
    if payload.valid_from_year is not None:
        row.valid_from_year = payload.valid_from_year
    if payload.valid_to_year is not None:
        row.valid_to_year = payload.valid_to_year

    if payload.properties is not None:
        defs = properties_of(db, object_type.id)
        merged = merge_properties(row.properties or {}, payload.properties)
        cleaned = validate_properties(defs, merged)
        require_refs_exist(db, defs, cleaned)
        require_unique_properties(
            db,
            object_type,
            defs,
            cleaned,
            owner_workspace_id=row.owner_workspace_id,
            exclude_id=row.id,
        )
        row.properties = cleaned

    after: dict[str, Any] = {
        "key": row.key,
        "label": row.label,
        "status": row.status,
        "properties": dict(row.properties or {}),
    }
    audit.record(
        db,
        action="object.update",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=row.owner_workspace_id,
        changes=audit.diff(before, after),
    )
    db.commit()
    db.refresh(row)
    return _out(row, object_type.slug, _workspace_slugs(db))


@router.put("/{type_slug}/{object_id}/aliases", response_model=ObjectOut)
def set_aliases(
    type_slug: str,
    object_id: uuid.UUID,
    payload: AliasesRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ObjectOut:
    """사람이 붙인 다른 이름을 통째로 바꾼다. **같은 타입의 다른 객체가 쓰는 별칭이면
    거절한다** — 어느 객체인지 말하며."""
    object_type = _type(db, type_slug)
    _not_system(object_type, "별칭을 붙이지")
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 12)
    )
    before, after = aliases.set_human(db, row, object_type, payload.aliases)
    if before != after:
        audit.record(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes={"aliases": {"before": before, "after": after}},
        )
    db.commit()
    return _out(
        row,
        object_type.slug,
        _workspace_slugs(db),
        _ref_labels(db, properties_of(db, object_type.id), [row]),
        aliases.of(db, [row.id]).get(row.id),
    )


@router.get("/{type_slug}/{object_id}/rollup", response_model=list[RollupOut])
def object_rollup(
    type_slug: str,
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[RollupOut]:
    """이 객체 「아래 전부」 의 숫자를 모은 것 — `list_view.rollups` 가 정한 대로.

    저장하지 않고 볼 때마다 센다. 값이 빈 것이 몇 개인지 함께 준다 — 안 주면 합계가
    「전부의 합」 으로 읽히고, 그것은 틀린 수다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    relation, parent_end = _tree_spec(object_type)
    if relation is None:
        return []
    defs = properties_of(db, object_type.id)
    return [
        RollupOut(**one.__dict__)
        for one in rollup.compute(
            db, user, object_type, row, defs, relation=relation, parent_end=parent_end
        )
    ]


@router.get("/{type_slug}/{object_id}/references", response_model=ReferencesOut)
def object_references(
    type_slug: str,
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ReferencesOut:
    """이 객체를 가리키는 것 — **누르기 전에 아는 자리.** 지우기 확인 창이 쓴다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    refs = lifecycle.references_of(db, user, row, object_type)
    return ReferencesOut(
        property_refs=[RefHitOut(**vars(one)) for one in refs.property_refs],
        relations=[RelationHitOut(**vars(one)) for one in refs.relations],
        hidden_property_refs=refs.hidden_property_refs,
        hidden_relations=refs.hidden_relations,
        total=refs.total,
    )


@router.get("/{type_slug}/{object_id}/history", response_model=list[HistoryEntryOut])
def object_history(
    type_slug: str,
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[HistoryEntryOut]:
    """이 객체의 이력 — 최근 것이 앞. **볼 수 있는 사람은 누구나** — 「이 값이 어디서
    왔나」 는 그 값을 쓰는 사람의 물음이지 관리자의 물음이 아니다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    return [
        HistoryEntryOut(
            id=one.id,
            at=one.at,
            actor_label=one.actor_label,
            action=one.action,
            reason=one.reason,
            kind=one.kind,
            changes=one.changes,
            relation=one.relation,
            snapshot=SnapshotOut(**vars(one.snapshot)) if one.snapshot else None,
        )
        for one in history.history_of(db, row)
    ]


@router.post("/{type_slug}/{object_id}/restore", response_model=ObjectOut)
def restore_object(
    type_slug: str,
    object_id: uuid.UUID,
    payload: RestoreRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ObjectOut:
    """그 시점의 값으로 **고친다** — 저장과 같은 검증을 거쳐서. 그때 가리키던 것이
    지워졌거나 규칙이 바뀌었으면 막고 이유를 말한다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 16)
    )
    history.restore(db, user, row, object_type, payload.entry_id)
    db.refresh(row)
    defs = properties_of(db, object_type.id)
    return _out(row, object_type.slug, _workspace_slugs(db), _ref_labels(db, defs, [row]))


@router.delete("/{type_slug}/{object_id}", status_code=204)
def delete_object(
    type_slug: str,
    object_id: uuid.UUID,
    mode: str = Query(default="block", pattern="^(block|detach)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """**지우지 않는다.** `deleted_at` 만 채운다 — 첨부가 밖에 남아 있고, 몇 년 뒤에도
    그것이 무엇이었는지는 물어질 수 있다.

    가리키는 것이 있으면 `block`(기본)은 **막고 무엇이 걸렸는지 말한다.** `detach` 는
    참조를 비우고 관계를 끊고 지운다 — 사람이 확인 창에서 고른 뒤에만 온다. 합치기는
    `POST .../merge` 다.
    """
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 17)
    )
    if mode == "detach":
        lifecycle.delete_detaching(db, user, row, object_type)
    else:
        lifecycle.delete_blocking(db, user, row, object_type)


@router.post("/{type_slug}/{object_id}/merge", response_model=MergeResultOut)
def merge_object(
    type_slug: str,
    object_id: uuid.UUID,
    payload: MergeRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MergeResultOut:
    """이 객체를 다른 객체에 **합치고** 지운다 — 참조·관계가 이긴 쪽으로 옮겨 가고,
    지는 쪽은 `merged_into` 로 남아 옛 링크가 새 것으로 간다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 17)
    )
    target = _visible(db, user, object_type, payload.into)
    result = lifecycle.merge_into(db, user, row, object_type, target)
    return MergeResultOut(into=target.id, **result)


# --- 관련 객체 --------------------------------------------------------------


def _related(db: Session, user: User, row: ObjectInstance) -> list[RelatedObjectOut]:
    """이 객체에 걸린 관계들 — **양방향 다.**

    「이것이 가리키는 것」 만 주면 「이것을 가리키는 것」 을 물을 자리가 없어진다.
    부품에서 그 부품을 쓰는 어셈블리를 못 보면, 그 부품을 지워도 되는지 알 수 없다.

    **볼 수 있는 것만 준다.** 저쪽 끝이 남의 부서 것이면 그 줄은 안 나온다 —
    없는 것과 안 보이는 것을 같은 말로 답하는 이 틀의 규칙 그대로다.
    """
    edges = list(
        db.scalars(
            select(ObjectRelation)
            .where(
                (ObjectRelation.src_object_id == row.id)
                | (ObjectRelation.dst_object_id == row.id)
            )
            .order_by(ObjectRelation.created_at)
        )
    )
    if not edges:
        return _related_links(db, user, row.id)

    other_ids = {
        (edge.dst_object_id if edge.src_object_id == row.id else edge.src_object_id)
        for edge in edges
    }
    others = {
        one.id: one
        for one in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_(other_ids),
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
    }
    kinds = {one.slug: one for one in db.scalars(select(RelationType))}
    types = {one.id: one for one in db.scalars(select(ObjectType))}

    out: list[RelatedObjectOut] = []
    for edge in edges:
        outgoing = edge.src_object_id == row.id
        other = others.get(edge.dst_object_id if outgoing else edge.src_object_id)
        if other is None:
            continue
        kind = kinds.get(edge.relation)
        object_type = types.get(other.type_id)

        # **방향에 맞는 말을 고른다.** 없으면 slug 를 보여 준다 — 빈 칸으로 두면
        # 그 줄이 무슨 관계인지 알 방법이 없다.
        if kind is None:
            label = edge.relation
        elif outgoing or not kind.directed:
            label = kind.label
        else:
            label = kind.inverse_label or f"{kind.label}의 반대"

        out.append(
            RelatedObjectOut(
                relation_id=edge.id,
                relation=edge.relation,
                label=label,
                outgoing=outgoing,
                object_id=other.id,
                object_label=other.label,
                object_key=other.key,
                object_type_slug=object_type.slug if object_type else "",
                object_type_label=object_type.label if object_type else "알 수 없음",
                properties=edge.properties or {},
                evidence_note=edge.evidence_note,
                created_at=edge.created_at,
            )
        )
    return out + _related_links(db, user, row.id)


def _related_links(db: Session, user: User, mine: uuid.UUID) -> list[RelatedObjectOut]:
    """한쪽 끝이 원 표(system)인 선들 — 「관련 객체」 에 같은 줄로 선다."""
    found = system.links_of(db, mine)
    if not found:
        return []
    kinds = {one.slug: one for one in db.scalars(select(RelationType))}
    types = system.types_by_slug(db)
    out: list[RelatedObjectOut] = []
    for link in found:
        other = system.other_end(db, user, link, mine, types)
        if other is None:
            continue
        outgoing = link.src_id == mine
        kind = kinds.get(link.relation)
        if kind is None:
            label = link.relation
        elif outgoing or not kind.directed:
            label = kind.label
        else:
            label = kind.inverse_label or f"{kind.label}의 반대"
        other_type = types.get(other.type_slug)
        out.append(
            RelatedObjectOut(
                relation_id=link.id,
                relation=link.relation,
                label=label,
                outgoing=outgoing,
                object_id=other.id,
                object_label=other.label,
                object_key=None,
                object_type_slug=other.type_slug,
                object_type_label=other_type.label if other_type else "알 수 없음",
                properties=link.properties or {},
                evidence_note=link.evidence_note,
                created_at=link.created_at,
            )
        )
    return out


@router.post(
    "/{type_slug}/{object_id}/relations", response_model=RelatedObjectOut, status_code=201
)
def add_relation(
    type_slug: str,
    object_id: uuid.UUID,
    payload: RelationCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RelatedObjectOut:
    """관계를 맺는다 — **출발점을 고칠 수 있는 사람만.**

    도착점은 볼 수만 있으면 된다. 양쪽 다 고칠 수 있어야 한다고 하면 부서를
    가로지르는 연결을 아무도 못 만들고, 그러면 **연결하려고 만든 것이 칸막이가
    된다.** 대신 맺은 사람과 근거가 남는다.
    """
    object_type = _type(db, type_slug)
    src = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, src.owner_workspace_id, what="객체", code_value=code("OBJECTS", 27)
    )

    kind = rel.relation_type(db, payload.relation)
    dst_end = system.find_end(db, user, payload.dst_object_id, allowed=kind.dst_type_slugs)
    if dst_end is None:
        raise NotFound(code("OBJECTS", 28), "이을 객체를 찾을 수 없습니다.")
    if dst_end.is_system:
        # 도착점이 원 표(부서·계정)면 선은 `object_links` 에 — 규칙은 같다.
        defs = list(
            db.scalars(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "relation", PropertyDef.owner_id == kind.id
                )
            )
        )
        link = links.add(
            db,
            user,
            kind,
            system.end_of(src, object_type),
            dst_end,
            properties=validate_properties(defs, payload.properties, apply_defaults=True),
            evidence_note=payload.evidence_note,
        )
        db.commit()
        return _link_out(db, user, src.id, link.id)

    dst = db.get(ObjectInstance, dst_end.id)
    assert dst is not None
    rel.require_endpoints_allowed(db, kind, src, dst)
    rel.require_cardinality(db, kind, src.id, dst.id)
    rel.require_no_cycle(db, kind, src.id, dst.id)

    existing = db.scalar(
        select(ObjectRelation).where(
            ObjectRelation.src_object_id == src.id,
            ObjectRelation.dst_object_id == dst.id,
            ObjectRelation.relation == kind.slug,
        )
    )
    if existing is not None:
        raise Conflict(code("OBJECTS", 29), "이미 이어져 있습니다.")

    # 관계 종류가 정한 속성 모양대로 검증한다 — **타입의 속성과 같은 규칙**이다.
    defs = list(
        db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "relation", PropertyDef.owner_id == kind.id
            )
        )
    )
    properties = validate_properties(defs, payload.properties, apply_defaults=True)

    edge = ObjectRelation(
        src_object_id=src.id,
        dst_object_id=dst.id,
        relation=kind.slug,
        properties=properties,
        evidence_note=payload.evidence_note,
        created_by_id=user.id,
    )
    db.add(edge)
    db.flush()
    audit.record(
        db,
        action="object.relation.add",
        actor=user,
        target_table="object_relations",
        target_id=edge.id,
        target_label=f"{src.label} -{kind.slug}-> {dst.label}",
        workspace_id=src.owner_workspace_id,
        # 양 끝을 id 로 남긴다 — 객체의 이력이 「이 관계가 나에게 걸린 것」 을 찾는 근거.
        changes=audit.relation_endpoints(edge, src.label, dst.label),
    )
    db.commit()

    found = [one for one in _related(db, user, src) if one.relation_id == edge.id]
    return found[0]


@router.patch(
    "/{type_slug}/{object_id}/relations/{relation_id}", response_model=RelatedObjectOut
)
def update_relation(
    type_slug: str,
    object_id: uuid.UUID,
    relation_id: uuid.UUID,
    payload: RelationPatchRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RelatedObjectOut:
    """관계의 속성과 근거를 고친다. **양끝과 종류는 못 바꾼다** — 그건 다른 관계다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 30)
    )
    link = _link(db, relation_id, row)
    if link is not None:
        if payload.evidence_note is not None:
            link.evidence_note = payload.evidence_note
        if payload.properties is not None:
            kind = rel.relation_type(db, link.relation)
            defs = list(
                db.scalars(
                    select(PropertyDef).where(
                        PropertyDef.owner_kind == "relation", PropertyDef.owner_id == kind.id
                    )
                )
            )
            link.properties = validate_properties(
                defs, merge_properties(link.properties or {}, payload.properties)
            )
        audit.record(
            db,
            action="object.relation.update",
            actor=user,
            target_table=links.TABLE,
            target_id=link.id,
            target_label=f"{row.label} · {link.relation}",
            workspace_id=row.owner_workspace_id,
            changes=links.endpoints(link, *_link_labels(db, user, link)),
        )
        db.commit()
        return _link_out(db, user, row.id, link.id)
    edge = _edge(db, relation_id, row)

    if payload.evidence_note is not None:
        edge.evidence_note = payload.evidence_note
    if payload.properties is not None:
        kind = rel.relation_type(db, edge.relation)
        defs = list(
            db.scalars(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "relation", PropertyDef.owner_id == kind.id
                )
            )
        )
        merged = merge_properties(edge.properties or {}, payload.properties)
        edge.properties = validate_properties(defs, merged)

    audit.record(
        db,
        action="object.relation.update",
        actor=user,
        target_table="object_relations",
        target_id=edge.id,
        target_label=f"{row.label} · {edge.relation}",
        workspace_id=row.owner_workspace_id,
        changes=audit.relation_endpoints(edge, *_edge_labels(db, edge)),
    )
    db.commit()
    found = [one for one in _related(db, user, row) if one.relation_id == edge.id]
    return found[0]


@router.delete("/{type_slug}/{object_id}/relations/{relation_id}", status_code=204)
def remove_relation(
    type_slug: str,
    object_id: uuid.UUID,
    relation_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """관계를 끊는다.

    **엣지는 진짜로 지운다.** 객체와 달리 관계는 그 자체로 기록이 아니라 두 기록
    사이의 말이고, 끊긴 관계를 남겨 두면 「지금 이어져 있나」 를 묻는 모든 질의가
    그 상태를 걸러야 한다. 대신 **감사 로그에 남는다.**
    """
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 31)
    )
    link = _link(db, relation_id, row)
    if link is not None:
        src_label, dst_label = _link_labels(db, user, link)
        links.remove(
            db,
            user,
            link,
            src_label=src_label,
            dst_label=dst_label,
            workspace_id=row.owner_workspace_id,
        )
        db.commit()
        return
    edge = _edge(db, relation_id, row)

    audit.record(
        db,
        action="object.relation.remove",
        actor=user,
        target_table="object_relations",
        target_id=edge.id,
        target_label=f"{row.label} · {edge.relation}",
        workspace_id=row.owner_workspace_id,
        changes=audit.relation_endpoints(edge, *_edge_labels(db, edge)),
    )
    db.delete(edge)
    db.commit()


def _edge_labels(db: Session, edge: ObjectRelation) -> tuple[str, str]:
    src = db.get(ObjectInstance, edge.src_object_id)
    dst = db.get(ObjectInstance, edge.dst_object_id)
    return (src.label if src else "", dst.label if dst else "")


def _link_out(
    db: Session, user: User, mine: uuid.UUID, link_id: uuid.UUID
) -> RelatedObjectOut:
    return next(one for one in _related_links(db, user, mine) if one.relation_id == link_id)


def _link(db: Session, relation_id: uuid.UUID, row: ObjectInstance) -> ObjectLink | None:
    """그 id 가 **이 객체에 걸린 링크**인가. 관계 id 와 링크 id 는 한 자리(`relation_id`)로
    온다 — 화면은 둘을 구별할 이유가 없고, 구별하게 하면 지우는 단추가 둘이 된다."""
    link = db.get(ObjectLink, relation_id)
    if link is None or row.id not in (link.src_id, link.dst_id):
        return None
    return link


def _link_labels(db: Session, user: User, link: ObjectLink) -> tuple[str, str]:
    types = system.types_by_slug(db)
    src = system.other_end(db, user, link, link.dst_id, types)
    dst = system.other_end(db, user, link, link.src_id, types)
    return (src.label if src else "", dst.label if dst else "")


def _edge(db: Session, relation_id: uuid.UUID, row: ObjectInstance) -> ObjectRelation:
    """그 관계가 **이 객체에 걸린 것인가.** 아니면 남의 관계를 남의 화면에서
    끊을 수 있게 된다."""
    edge = db.get(ObjectRelation, relation_id)
    if edge is None or row.id not in (edge.src_object_id, edge.dst_object_id):
        raise NotFound(code("OBJECTS", 32), "관계를 찾을 수 없습니다.")
    return edge


# --- 연도 배정 --------------------------------------------------------------


@router.get("/{type_slug}/{object_id}/years", response_model=list[int])
def list_years(
    type_slug: str,
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[int]:
    """이 객체가 **명시적으로 배정된** 연도들(`temporal_kind='yearly'`)."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    return sorted(db.scalars(select(ObjectYear.year).where(ObjectYear.object_id == row.id)))


@router.put("/{type_slug}/{object_id}/years", response_model=list[int])
def set_years(
    type_slug: str,
    object_id: uuid.UUID,
    years: list[int],
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[int]:
    """연도 배정을 **통째로** 정한다.

    부분 수정이 아닌 이유: 배정은 「이 해들」 이라는 하나의 답이고, 한 해를 빼는
    일이 한 해를 더하는 일만큼 흔하다. 통째로 받으면 **화면이 보여 준 것과
    저장되는 것이 같다.**
    """
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    require_owner_edit(
        db, user, row.owner_workspace_id, what="객체", code_value=code("OBJECTS", 34)
    )
    if object_type.temporal_kind != "yearly":
        raise Conflict(
            code("OBJECTS", 35),
            f"{object_type.label}은 연도를 배정하는 축이 아닙니다"
            f"(지금 정책: {object_type.temporal_kind}).",
        )

    wanted = sorted({one for one in years if 1900 <= one <= 2999})
    db.query(ObjectYear).filter(ObjectYear.object_id == row.id).delete(
        synchronize_session=False
    )
    for one in wanted:
        db.add(ObjectYear(object_id=row.id, year=one))

    audit.record(
        db,
        action="object.years.set",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=f"{object_type.slug}:{row.label}",
        workspace_id=row.owner_workspace_id,
        changes={"years": wanted},
    )
    db.commit()
    return wanted
