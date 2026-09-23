"""데이터 소스 관리 — 시스템 관리자만. 정의·미리 보기·동기화(계획 → 적용)·기록."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.datasources import services
from app.modules.datasources.models import (
    AUTH_KINDS,
    SOURCE_KINDS,
    DataSource,
    DataSourceRun,
)
from app.modules.datasources.schemas import (
    CoreSuggestOut,
    DataSourceOut,
    DataSourcePatchRequest,
    DataSourceWriteRequest,
    PreviewOut,
    RunOut,
)
from app.modules.jobs import routes as jobs_routes
from app.modules.jobs import services as job_services
from app.modules.jobs.schemas import JobOut
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType
from app.modules.ontology.services import require_choice, require_slug
from app.modules.workspaces.models import Workspace
from app.shared import audit
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import AppError, Conflict, NotFound, code

router = APIRouter(prefix="/datasources", tags=["datasources"])

RECENT_RUNS = 30


def _out(db: Session, row: DataSource) -> DataSourceOut:
    object_type = db.get(ObjectType, row.type_id)
    workspace = db.get(Workspace, row.workspace_id) if row.workspace_id else None
    auth = row.auth or {}
    return DataSourceOut(
        id=row.id,
        slug=row.slug,
        name=row.name,
        kind=row.kind,
        base_url=row.base_url,
        entity_set=row.entity_set,
        options=row.options or {},
        filter=row.filter,
        select=row.select,
        auth_kind=str(auth.get("kind") or "none"),
        auth_user=str(auth.get("user") or ""),
        has_secret=bool(auth.get("secret")),
        page_size=row.page_size,
        type_slug=object_type.slug if object_type else "",
        workspace_slug=workspace.slug if workspace else None,
        mapping=row.mapping or {},
        deprecate_missing=row.deprecate_missing,
        since_mark=row.since_mark,
        interval_minutes=row.interval_minutes,
        is_active=row.is_active,
        last_run_at=row.last_run_at,
        last_status=row.last_status,
        created_at=row.created_at,
    )


def _source(db: Session, slug: str) -> DataSource:
    row = db.scalar(select(DataSource).where(DataSource.slug == slug))
    if row is None:
        raise NotFound(code("DATASOURCES", 1), f"데이터 소스를 찾을 수 없습니다: {slug}")
    return row


def _type(db: Session, slug: str) -> ObjectType:
    row = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if row is None:
        raise NotFound(code("DATASOURCES", 2), f"타입을 찾을 수 없습니다: {slug}")
    if row.kind_class == "system":
        raise Conflict(
            code("DATASOURCES", 3),
            f"{row.label}은 다른 표를 비추는 타입이라 동기화로 채우지 않습니다.",
        )
    return row


def _workspace(db: Session, slug: str | None) -> Workspace | None:
    if not slug:
        return None
    row = db.scalar(select(Workspace).where(Workspace.slug == slug))
    if row is None:
        raise NotFound(code("DATASOURCES", 4), f"부서를 찾을 수 없습니다: {slug}")
    return row


def _require_url(url: str, *, kind: str) -> str:
    """OData·REST·코어는 루트 주소가 있어야 한다. 파일은 없어도 된다(위치가 entity_set 에)."""
    if kind == "file":
        return url.strip().rstrip("/")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError(
            code("DATASOURCES", 5),
            "주소는 http:// 또는 https:// 로 시작해야 합니다.",
            status=422,
        )
    return url.strip().rstrip("/")


def _check_deprecate(kind: str, wanted: bool) -> bool:
    """**증분 소스에서는 「이번에 안 온 것을 중지」 를 켤 수 없다.**

    코어 창구는 지난번 이후 바뀐 것만 준다 — 안 온 것이 대부분이다. 그 규칙을 켜면 첫
    동기화 다음 날 **멀쩡한 객체 전부가 사용 중지가 된다.** 사라진 것은 무덤(`deleted`)으로
    오므로 그 규칙 자체가 필요 없다.
    """
    if wanted and kind == "sp_core":
        raise AppError(
            code("DATASOURCES", 40),
            "코어 소스는 지난번 이후 바뀐 것만 받으므로 「이번에 안 온 것을 사용 중지」 를 "
            "켤 수 없습니다 — 사라진 것은 상대가 무덤으로 알려 줍니다.",
            status=422,
        )
    return wanted


def _check_mapping(db: Session, object_type: ObjectType, mapping: dict[str, object]) -> None:
    """저장할 때 칸 대응을 본다 — 동기화를 눌러서야 틀린 걸 아는 것보다 낫다. 비어 있으면
    아직 만드는 중이니 넘어간다."""
    if not mapping:
        return
    services.Mapping.parse(dict(mapping), properties_of(db, object_type.id))


@router.get("", response_model=list[DataSourceOut])
def list_sources(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[DataSourceOut]:
    return [_out(db, row) for row in db.scalars(select(DataSource).order_by(DataSource.name))]


@router.post("", response_model=DataSourceOut, status_code=201)
def create_source(
    payload: DataSourceWriteRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DataSourceOut:
    slug = require_slug(payload.slug, what="데이터 소스 slug")
    if db.scalar(select(DataSource).where(DataSource.slug == slug)) is not None:
        raise Conflict(code("DATASOURCES", 6), f"이미 있는 데이터 소스입니다: {slug}")
    object_type = _type(db, payload.type_slug)
    workspace = _workspace(db, payload.workspace_slug)
    require_choice(payload.kind, SOURCE_KINDS, what="소스 종류")
    require_choice(payload.auth_kind, AUTH_KINDS, what="인증 방식")
    _check_mapping(db, object_type, payload.mapping)
    row = DataSource(
        slug=slug,
        name=payload.name.strip(),
        kind=payload.kind,
        base_url=_require_url(payload.base_url, kind=payload.kind),
        entity_set=payload.entity_set.strip(),
        options=payload.options,
        filter=payload.filter.strip(),
        select=payload.select.strip(),
        auth={
            "kind": payload.auth_kind,
            "user": payload.auth_user.strip(),
            "secret": payload.auth_secret,
        },
        page_size=payload.page_size,
        type_id=object_type.id,
        workspace_id=workspace.id if workspace else None,
        mapping=payload.mapping,
        deprecate_missing=_check_deprecate(payload.kind, payload.deprecate_missing),
        interval_minutes=payload.interval_minutes,
        is_active=payload.is_active,
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="datasource.create",
        actor=user,
        target_table="data_sources",
        target_id=row.id,
        target_label=slug,
        changes={
            "base_url": row.base_url,
            "entity_set": row.entity_set,
            "type": object_type.slug,
        },
    )
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.get("/{slug}", response_model=DataSourceOut)
def get_source(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> DataSourceOut:
    return _out(db, _source(db, slug))


@router.patch("/{slug}", response_model=DataSourceOut)
def update_source(
    slug: str,
    payload: DataSourcePatchRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DataSourceOut:
    row = _source(db, slug)
    sent = payload.model_fields_set
    before = {
        "base_url": row.base_url,
        "entity_set": row.entity_set,
        "filter": row.filter,
        "mapping": row.mapping,
        "is_active": row.is_active,
    }
    if "name" in sent and payload.name is not None:
        row.name = payload.name.strip()
    if "kind" in sent and payload.kind is not None:
        row.kind = require_choice(payload.kind, SOURCE_KINDS, what="소스 종류")
    if "base_url" in sent and payload.base_url is not None:
        row.base_url = _require_url(payload.base_url, kind=row.kind)
    if "entity_set" in sent and payload.entity_set is not None:
        row.entity_set = payload.entity_set.strip()
    if "options" in sent and payload.options is not None:
        row.options = payload.options
    if "filter" in sent and payload.filter is not None:
        row.filter = payload.filter.strip()
    if "select" in sent and payload.select is not None:
        row.select = payload.select.strip()
    auth = dict(row.auth or {})
    if "auth_kind" in sent and payload.auth_kind is not None:
        auth["kind"] = require_choice(payload.auth_kind, AUTH_KINDS, what="인증 방식")
    if "auth_user" in sent and payload.auth_user is not None:
        auth["user"] = payload.auth_user.strip()
    if "auth_secret" in sent and payload.auth_secret is not None:
        auth["secret"] = payload.auth_secret
    row.auth = auth
    if "page_size" in sent and payload.page_size is not None:
        row.page_size = payload.page_size
    if "type_slug" in sent and payload.type_slug is not None:
        row.type_id = _type(db, payload.type_slug).id
    if "workspace_slug" in sent:
        workspace = _workspace(db, payload.workspace_slug)
        row.workspace_id = workspace.id if workspace else None
    if "mapping" in sent and payload.mapping is not None:
        object_type = db.get(ObjectType, row.type_id)
        assert object_type is not None
        _check_mapping(db, object_type, payload.mapping)
        row.mapping = payload.mapping
    if "deprecate_missing" in sent and payload.deprecate_missing is not None:
        row.deprecate_missing = _check_deprecate(row.kind, payload.deprecate_missing)
    # **비우는 것만 받는다** — 시계를 손으로 앞당기면 그 사이 것을 영영 안 받는다.
    if "since_mark" in sent and payload.since_mark is not None:
        if payload.since_mark.strip():
            raise AppError(
                code("DATASOURCES", 41),
                "받은 자리(since)는 손으로 정하지 않습니다 — 비우면 처음부터 다시 받습니다.",
                status=422,
            )
        row.since_mark = ""
    if "interval_minutes" in sent and payload.interval_minutes is not None:
        row.interval_minutes = payload.interval_minutes
    if "is_active" in sent and payload.is_active is not None:
        row.is_active = payload.is_active
    after = {
        "base_url": row.base_url,
        "entity_set": row.entity_set,
        "filter": row.filter,
        "mapping": row.mapping,
        "is_active": row.is_active,
    }
    audit.record(
        db,
        action="datasource.update",
        actor=user,
        target_table="data_sources",
        target_id=row.id,
        target_label=row.slug,
        changes=audit.diff(before, after),
    )
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.delete("/{slug}", status_code=204)
def delete_source(
    slug: str, user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> None:
    """소스를 지운다. **객체는 남는다** — 그 소스가 남긴 외부 식별자(별칭)도 남아서, 같은
    slug 로 다시 만들면 이어서 찾는다."""
    row = _source(db, slug)
    audit.record(
        db,
        action="datasource.delete",
        actor=user,
        target_table="data_sources",
        target_id=row.id,
        target_label=row.slug,
    )
    db.delete(row)
    db.commit()


@router.post("/{slug}/preview", response_model=PreviewOut)
def preview_source(
    slug: str,
    limit: int = Query(default=5, ge=1, le=50),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PreviewOut:
    """앞의 몇 행을 그대로 + 대응한 뒤로 — 칸 대응을 맞출 때 본다. 아무것도 안 바꾼다."""
    row = _source(db, slug)
    return PreviewOut(**services.preview(db, row, limit=limit))


@router.post("/{slug}/core-suggest", response_model=CoreSuggestOut)
def suggest_core_mapping(
    slug: str,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> CoreSuggestOut:
    """상대의 카탈로그를 읽어 **칸 대응 초안**을 만든다 — 저장은 사람이 한다.

    옮겨 적게 하면 상대가 칸을 하나 더하는 날 그 대응이 조용히 뒤처진다. 여기서는 상대의
    `GET /api/core` 를 그대로 읽어 같은 이름(키 → 키, 없으면 이름 → 이름)끼리 잇고,
    **못 이은 것은 까닭과 함께 돌려준다** — 조용히 빼면 사람은 그 칸이 온 줄 안다.
    """
    row = _source(db, slug)
    if row.kind != "sp_core":
        raise AppError(
            code("DATASOURCES", 42),
            "형제 설치의 코어 소스에서만 됩니다.",
            status=422,
        )
    object_type = db.get(ObjectType, row.type_id)
    assert object_type is not None
    return services.suggest_core_mapping(db, row, object_type)


@router.post("/{slug}/sync", response_model=JobOut, status_code=202)
def sync_source(
    slug: str,
    apply: bool = Query(default=False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    """계획(`apply=false`) 또는 적용 — **작업이 된다**(202). 바깥 표를 읽는 시간은 그쪽이
    정하므로 요청 안에서 기다리지 않는다. **한 행이라도 오류면 아무것도 안 넣는다** — 파일과
    같다. 시스템 관리자만."""
    if not user.is_system_admin:
        raise AppError(
            code("DATASOURCES", 7), "동기화는 시스템 관리자만 돌립니다.", status=403
        )
    row = _source(db, slug)
    job = job_services.enqueue(
        db,
        kind="datasource_sync",
        params={"slug": row.slug, "apply": apply},
        user=user,
        workspace_id=None,
    )
    db.commit()
    db.refresh(job)
    return jobs_routes._out(db, job)


@router.get("/{slug}/runs", response_model=list[RunOut])
def list_runs(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[RunOut]:
    row = _source(db, slug)
    runs = db.scalars(
        select(DataSourceRun)
        .where(DataSourceRun.source_id == row.id)
        .order_by(DataSourceRun.started_at.desc())
        .limit(RECENT_RUNS)
    )
    return [RunOut.model_validate(one) for one in runs]
