"""동기화 — **바깥 행을 파일의 행으로 바꾸고, 나머지는 일괄 입력가 한다.**

여기서 새로 하는 것은 셋뿐이다:

    칸 대응   바깥 열 → key · label · description · alias · properties.<키>. 값 대응표
              (`values`)가 있으면 코드를 우리 고를 값으로(`"C" → "상용"`). 표에 없는 값은
              **오류 행**이다(`values_strict`, 기본 참) — 조용히 통과시키면 고를 값이 오염된다.
    다시 찾기 이 소스의 외부 식별자(별칭 `source:<slug>`) → 우리 식별자 → 별칭·이름. 겹치면
              오류 행. 찾은 것은 행에 `id` 를 박아 일괄 입력가 그 객체를 고치게 한다.
    뒷정리    적용 뒤 외부 식별자를 별칭으로 남기고, 켜 두었으면 이번에 안 온 것을 「사용
              중지」 로.

검증·권한·upsert·감사·이력은 `objects/bulk.py` 그대로다 — 규칙을 두 벌로 두지 않는다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.datasources import fetchers, odata
from app.modules.datasources.models import DataSource, DataSourceRun
from app.modules.datasources.schemas import RunOut, SyncOut
from app.modules.notifications import services as notifications
from app.modules.objects import aliases, bulk
from app.modules.objects.models import ObjectAlias, ObjectInstance
from app.modules.objects.schemas import ImportRowOut
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import audit, extensions
from app.shared.errors import AppError, code
from app.shared.text import compare_key

#: 화면·기록에 싣는 오류 행의 상한.
ERROR_SAMPLE = 50
#: 칸 대응의 target 으로 쓸 수 있는 고정 자리.
FIXED_TARGETS = ("key", "label", "description", "alias")

#: 시험이 갈아 끼우는 자리 — 진짜 OData 대신 가짜 응답.
transport: httpx.BaseTransport | None = None


@dataclass
class Column:
    source: str
    target: str
    values: dict[str, Any] | None = None
    values_strict: bool = True


@dataclass
class Mapping:
    external_key: str
    columns: list[Column]

    @classmethod
    def parse(cls, raw: dict[str, Any], defs: list[PropertyDef]) -> Mapping:
        """정의를 읽고 **틀린 곳을 말한다** — 없는 속성, 모르는 자리, 빈 외부 식별자."""
        external_key = str(raw.get("external_key") or "").strip()
        if not external_key:
            raise AppError(
                code("DATASOURCES", 20),
                "칸 대응에 external_key(바깥 식별자 열)가 없습니다 — 같은 객체를 다음에 다시 "
                "찾을 근거입니다.",
                status=422,
            )
        keys = {d.key for d in defs if d.data_type != "file"}
        columns: list[Column] = []
        for one in raw.get("columns") or []:
            if not isinstance(one, dict):
                raise AppError(code("DATASOURCES", 20), "칸 대응은 표여야 합니다.", status=422)
            source = str(one.get("source") or "").strip()
            target = str(one.get("target") or "").strip()
            if not source or not target:
                raise AppError(
                    code("DATASOURCES", 20),
                    "칸 대응에 source 와 target 이 있어야 합니다.",
                    status=422,
                )
            if target.startswith("properties."):
                if target.split(".", 1)[1] not in keys:
                    raise AppError(
                        code("DATASOURCES", 20),
                        f"칸 대응이 정의되지 않은 속성을 가리킵니다: {target}",
                        status=422,
                    )
            elif target not in FIXED_TARGETS:
                raise AppError(
                    code("DATASOURCES", 20),
                    f"칸 대응의 자리를 모릅니다: {target}. "
                    f"{', '.join(FIXED_TARGETS)} 또는 properties.<키>.",
                    status=422,
                )
            values = one.get("values")
            columns.append(
                Column(
                    source=source,
                    target=target,
                    values=dict(values) if isinstance(values, dict) else None,
                    values_strict=bool(one.get("values_strict", True)),
                )
            )
        if not any(c.target == "label" for c in columns):
            raise AppError(
                code("DATASOURCES", 20),
                "칸 대응에 label(이름)로 가는 열이 있어야 합니다.",
                status=422,
            )
        return cls(external_key=external_key, columns=columns)

    def select_clause(self) -> str:
        """`$select` 를 안 적었으면 대응에 쓰인 열만 청한다 — 표 전체를 끌어오지 않게."""
        wanted: list[str] = []
        for name in [self.external_key, *(c.source for c in self.columns)]:
            top = name.replace(".", "/").split("/")[0]
            if top not in wanted:
                wanted.append(top)
        return ",".join(wanted)


@dataclass
class Mapped:
    """바깥 행 하나를 파일의 행으로 바꾼 것 — 또는 왜 못 바꿨는지."""

    external_id: str
    row: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return str(value)


def map_row(mapping: Mapping, raw: dict[str, Any], slug: str) -> Mapped:
    external = _text(odata.pick(raw, mapping.external_key)).strip()
    if not external:
        return Mapped(
            external_id="", error=f"바깥 식별자({mapping.external_key})가 비어 있습니다."
        )
    out: dict[str, Any] = {}
    alias_values: list[str] = []
    for column in mapping.columns:
        value = odata.pick(raw, column.source)
        if column.values is not None and value is not None and value != "":
            key = _text(value)
            if key in column.values:
                value = column.values[key]
            elif column.values_strict:
                return Mapped(
                    external_id=external,
                    error=f"{column.source}: 값 대응표에 없는 값입니다: {key}",
                )
        if column.target == "alias":
            items = value if isinstance(value, list) else [value]
            alias_values.extend(_text(one).strip() for one in items if _text(one).strip())
            continue
        if isinstance(value, list):
            value = bulk.MULTI_SEP.join(_text(one) for one in value)
        elif isinstance(value, bool):
            value = "예" if value else "아니오"
        name = (
            column.target.split(".", 1)[1]
            if column.target.startswith("properties.")
            else column.target
        )
        # 빈 값은 「안 건드림」 — 파일과 같다. 바깥이 비워 보낸 칸으로 우리 값을 지우지 않는다.
        if value is None or value == "":
            continue
        out[name] = value
    if alias_values:
        out["aliases"] = bulk.MULTI_SEP.join(dict.fromkeys(alias_values))
    return Mapped(external_id=external, row=out)


def _match(
    db: Session,
    object_type: ObjectType,
    slug: str,
    mapped: list[Mapped],
) -> None:
    """같은 객체를 다시 찾아 행에 `id` 를 박는다 — 외부 식별자 → (식별자는 bulk 가) →
    별칭·이름."""
    kind = aliases.source_kind(slug)
    by_external = {
        norm: object_id
        for norm, object_id in db.execute(
            select(ObjectAlias.norm, ObjectAlias.object_id)
            .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
            .where(
                ObjectAlias.type_id == object_type.id,
                ObjectAlias.kind == kind,
                ObjectInstance.deleted_at.is_(None),
            )
        )
    }
    names: dict[str, list[uuid.UUID]] = {}
    for object_id, label in db.execute(
        select(ObjectInstance.id, ObjectInstance.label).where(
            ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None)
        )
    ):
        names.setdefault(compare_key(label), []).append(object_id)
    for norm, hits in aliases.index_of(db, object_type).items():
        for hit in hits:
            if hit not in names.setdefault(norm, []):
                names[norm].append(hit)

    for one in mapped:
        if one.error:
            continue
        found = by_external.get(compare_key(one.external_id))
        if found is not None:
            one.row["id"] = str(found)
            continue
        # 처음 만나는 행 — 별칭·이름으로 우리 것을 찾는다(사람이 먼저 만들어 둔 것과 합류).
        # 못 찾으면 bulk 가 식별자로 찾거나 새로 만든다.
        label = str(one.row.get("label") or "")
        hits = names.get(compare_key(label), []) if label else []
        if len(hits) == 1:
            one.row["id"] = str(hits[0])
        elif len(hits) > 1:
            one.error = (
                f"「{label}」 이 {len(hits)}개에 맞습니다 — "
                "바깥 식별자가 없어 고를 수 없습니다."
            )


@dataclass
class SyncResult:
    run: DataSourceRun
    plan_rows: list[bulk.RowPlan]
    truncated: bool


def _auth(source: DataSource) -> odata.Auth:
    raw = source.auth or {}
    return odata.Auth(
        kind=str(raw.get("kind") or "none"),
        user=str(raw.get("user") or ""),
        secret=str(raw.get("secret") or ""),
    )


def preview(db: Session, source: DataSource, *, limit: int = 5) -> dict[str, Any]:
    """앞의 몇 행을 **그대로**와 **대응한 뒤** 로 — 칸 대응을 맞출 때 본다."""
    object_type = db.get(ObjectType, source.type_id)
    assert object_type is not None
    defs = properties_of(db, object_type.id)
    fetched = fetchers.fetch(
        source,
        auth=_auth(source),
        select=source.select,
        page_size=limit,
        max_rows=limit,
        transport=transport,
    )
    columns: list[str] = []
    for row in fetched.rows:
        for name in row:
            if name not in columns and not name.startswith("@") and name != "__metadata":
                columns.append(name)
    mapped: list[dict[str, Any]] = []
    try:
        mapping = Mapping.parse(source.mapping or {}, defs)
    except AppError as caught:
        return {
            "columns": columns,
            "rows": fetched.rows,
            "mapped": [],
            "mapping_error": caught.message,
        }
    for raw in fetched.rows:
        one = map_row(mapping, raw, source.slug)
        mapped.append({"external_id": one.external_id, "row": one.row, "error": one.error})
    return {"columns": columns, "rows": fetched.rows, "mapped": mapped, "mapping_error": None}


def sync(db: Session, user: User | None, source: DataSource, *, apply: bool) -> SyncResult:
    """계획을 세우고(그리고 `apply` 면 넣고) 기록을 남긴다. 상태가 바뀌면 알린다.

    **부르는 쪽이 커밋하지 않는다** — 여기서 커밋한다(bulk 가 그렇고, 기록은 실패해도
    남아야 한다).
    """
    before = source.last_status
    result = _sync(db, user, source, apply=apply)
    _announce(db, source, before=before)
    return result


def _announce(db: Session, source: DataSource, *, before: str | None) -> None:
    """**상태가 바뀔 때만 알린다.**

    타이머가 5분마다 도니까 실패할 때마다 알리면 하루에 288개가 쌓이고, 그러면 사람은
    이 종류를 통째로 안 읽게 된다 — 그때 이 알림은 없는 것과 같다. 복구도 알린다:
    안 알리면 실패 알림 하나를 들고 「아직도 안 되나」 를 손으로 확인하러 간다.

    계획만 본 것(`apply=false`)은 `last_status` 를 안 건드리므로 여기 안 걸린다.
    """
    after = source.last_status
    if after == before:
        return
    if after == "failed":
        notifications.notify_system_admins(
            db,
            kind=notifications.DATASOURCE_FAILED,
            title=f"데이터 소스 「{source.name}」 동기화가 실패했습니다",
            body="자동 동기화가 멈춘 것은 아닙니다 — 다음 차례에 다시 시도합니다. "
            "무엇이 막았는지는 소스 화면의 기록에 있습니다.",
            link="/admin/datasources",
        )
    elif after == "ok" and before == "failed":
        notifications.notify_system_admins(
            db,
            kind=notifications.DATASOURCE_RECOVERED,
            title=f"데이터 소스 「{source.name}」 동기화가 다시 됩니다",
            link="/admin/datasources",
        )
    db.commit()


def _sync(db: Session, user: User | None, source: DataSource, *, apply: bool) -> SyncResult:
    object_type = db.get(ObjectType, source.type_id)
    if object_type is None:
        raise AppError(code("DATASOURCES", 21), "소스가 가리키는 타입이 없습니다.", status=409)
    defs = properties_of(db, object_type.id)
    mapping = Mapping.parse(source.mapping or {}, defs)
    run = DataSourceRun(
        source_id=source.id,
        actor_label=(user.display_name or user.email) if user else "타이머",
    )
    db.add(run)
    db.flush()

    try:
        fetched = fetchers.fetch(
            source,
            auth=_auth(source),
            select=source.select or mapping.select_clause(),
            transport=transport,
        )
    except AppError as caught:
        run.status = "failed"
        run.errors = [caught.message]
        run.finished_at = datetime.now(UTC)
        source.last_run_at = run.finished_at
        source.last_status = "failed"
        db.commit()
        return SyncResult(run=run, plan_rows=[], truncated=False)

    mapped = [map_row(mapping, raw, source.slug) for raw in fetched.rows]
    run.rows_seen = len(mapped)
    _match(db, object_type, source.slug, mapped)

    # 대응에서 걸린 행은 계획에 오류로 싣고, 나머지를 bulk 에 넘긴다.
    rows = [one.row for one in mapped if not one.error]
    plan_rows: list[bulk.RowPlan] = []
    errors: list[str] = []
    if fetched.truncated:
        errors.append(
            f"행이 {odata.MAX_ROWS}개를 넘어 끊었습니다 — `$filter` 로 나눠 동기화하세요."
        )
    for index, one in enumerate(mapped, start=1):
        if one.error:
            plan_rows.append(
                bulk.RowPlan(
                    row=index, action="error", label=one.external_id, message=one.error
                )
            )
    # bulk 의 상한(한 번에 5,000행)은 조각으로 넘는다 — 계획은 전부 세운 뒤에 적용한다.
    chunks = [rows[i : i + bulk.MAX_ROWS] for i in range(0, len(rows), bulk.MAX_ROWS)]
    plans = [
        bulk.plan_objects(
            db, _actor(db, user), object_type, chunk, owner_workspace_id=source.workspace_id
        )
        for chunk in chunks
    ]
    offset = 0
    for plan in plans:
        errors.extend(plan.errors)
        for row_plan in plan.rows:
            plan_rows.append(
                bulk.RowPlan(
                    row=row_plan.row + offset,
                    action=row_plan.action,
                    label=row_plan.label,
                    key=row_plan.key,
                    object_id=row_plan.object_id,
                    changes=row_plan.changes,
                    message=row_plan.message,
                )
            )
        offset += len(plan.rows)
    ok = not errors and all(one.action != "error" for one in plan_rows)
    counts = _counts(plan_rows)

    if not apply or not ok:
        run.status = "planned" if ok else "failed"
        run.counts = counts
        run.errors = (
            errors
            + [
                f"{one.row}행 {one.label}: {one.message}"
                for one in plan_rows
                if one.action == "error"
            ][:ERROR_SAMPLE]
        )
        run.finished_at = datetime.now(UTC)
        if not ok:
            source.last_run_at = run.finished_at
            source.last_status = "failed"
        db.commit()
        return SyncResult(run=run, plan_rows=plan_rows, truncated=fetched.truncated)

    # 적용 — 조각마다 bulk 가 검증을 다시 하고 커밋한다.
    actor = _actor(db, user)
    applied_rows: list[bulk.RowPlan] = []
    for chunk in chunks:
        done = bulk.apply_objects(
            db, actor, object_type, chunk, owner_workspace_id=source.workspace_id
        )
        if not done.ok:
            run.status = "failed"
            run.errors = (
                done.errors
                + [
                    f"{one.row}행 {one.label}: {one.message}"
                    for one in done.rows
                    if one.action == "error"
                ][:ERROR_SAMPLE]
            )
            run.finished_at = datetime.now(UTC)
            source.last_run_at = run.finished_at
            source.last_status = "failed"
            db.commit()
            return SyncResult(run=run, plan_rows=plan_rows, truncated=fetched.truncated)
        applied_rows.extend(done.rows)

    # 외부 식별자를 별칭으로 남긴다 — 다음 동기화가 이것으로 같은 객체를 다시 찾는다.
    seen: set[str] = set()
    good = [one for one in mapped if not one.error]
    for mapped_row, row_plan in zip(good, applied_rows, strict=True):
        seen.add(compare_key(mapped_row.external_id))
        if row_plan.object_id is None:
            continue
        target = db.get(ObjectInstance, row_plan.object_id)
        if target is not None:
            aliases.set_external(db, target, object_type, source.slug, mapped_row.external_id)
    deprecated = 0
    if source.deprecate_missing:
        deprecated = _deprecate_missing(db, actor, object_type, source, seen)

    run.status = "ok"
    run.applied = True
    run.counts = {**counts, "deprecated": deprecated}
    run.finished_at = datetime.now(UTC)
    source.last_run_at = run.finished_at
    source.last_status = "ok"
    audit.record(
        db,
        action="datasource.sync",
        actor=user,
        target_table="data_sources",
        target_id=source.id,
        target_label=source.slug,
        workspace_id=source.workspace_id,
        changes={"rows": len(mapped), **run.counts},
    )
    db.commit()
    return SyncResult(run=run, plan_rows=applied_rows, truncated=fetched.truncated)


def _counts(rows: list[bulk.RowPlan]) -> dict[str, int]:
    out = {"create": 0, "update": 0, "unchanged": 0, "error": 0}
    for one in rows:
        out[one.action] = out.get(one.action, 0) + 1
    return out


def _actor(db: Session, user: User | None) -> User:
    """타이머가 돌릴 때는 사람이 없다 — 시스템 관리자 하나를 대신 세운다(권한 판정용).
    감사 기록은 `actor=None` 으로 남겨 「타이머」 로 보이게 한다."""
    if user is not None:
        return user
    found = db.scalar(
        select(User)
        .where(User.is_system_admin.is_(True), User.deleted_at.is_(None))
        .order_by(User.created_at)
    )
    if found is None:
        raise AppError(
            code("DATASOURCES", 22),
            "동기화를 대신 돌릴 시스템 관리자 계정이 없습니다.",
            status=409,
        )
    return found


def _deprecate_missing(
    db: Session, actor: User, object_type: ObjectType, source: DataSource, seen: set[str]
) -> int:
    """이 소스가 남긴 객체 중 이번에 안 온 것을 「사용 중지」 로. 사람이 만든 것은 안
    건드린다."""
    kind = aliases.source_kind(source.slug)
    rows = db.execute(
        select(ObjectInstance, ObjectAlias.norm)
        .join(ObjectAlias, ObjectAlias.object_id == ObjectInstance.id)
        .where(
            ObjectAlias.kind == kind,
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            ObjectInstance.status == "active",
        )
    )
    count = 0
    for row, norm in rows:
        if norm in seen:
            continue
        row.status = "deprecated"
        count += 1
        audit.record(
            db,
            action="object.update",
            actor=actor,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes={"status": {"before": "active", "after": "deprecated"}},
            reason=f"{source.name} 에서 사라져 사용 중지로 표시",
        )
    return count


def due(db: Session, now: datetime | None = None) -> list[DataSource]:
    """타이머가 돌릴 차례인 소스 — 간격이 0 이 아니고 마지막 실행에서 그만큼 지난 것."""
    now = now or datetime.now(UTC)
    out: list[DataSource] = []
    for source in db.scalars(
        select(DataSource).where(
            DataSource.is_active.is_(True), DataSource.interval_minutes > 0
        )
    ):
        if (
            source.last_run_at is None
            or source.last_run_at + timedelta(minutes=source.interval_minutes) <= now
        ):
            out.append(source)
    return out


def _move_sources(db: Session, source_id: uuid.UUID, target_id: uuid.UUID) -> int:
    done = db.execute(
        update(DataSource)
        .where(DataSource.workspace_id == source_id)
        .values(workspace_id=target_id)
    )
    return extensions.rows_changed(done)


def workspace_content(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceContent]:
    """부서 통폐합 때 옮길 데이터 소스. **새로 들어오는 객체의 소유 부서가 이것으로
    정해진다** — 안 옮기면 통폐합 뒤에도 동기화가 없어진 부서로 계속 넣는다."""
    count = (
        db.scalar(
            select(func.count())
            .select_from(DataSource)
            .where(DataSource.workspace_id == workspace_id)
        )
        or 0
    )
    return [
        extensions.WorkspaceContent(
            kind="datasources", label="데이터 소스", count=int(count), move=_move_sources
        )
    ]


# --- 홈 「남은 일」 ------------------------------------------------------------

#: 간격의 이 배를 넘도록 안 돌았으면 「멎었다」 로 본다. 딱 한 배로 보면 5분 간격에서
#: 타이머가 한 번만 늦어도 경고가 뜨고, 그런 경고는 곧 무시된다.
STALE_FACTOR = 3


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """**조용히 멎은 것을 홈이 말한다.**

    동기화가 실패하면 지금까지는 `last_status` 에 failed 만 적혔다. 소스 화면을 열어
    보는 사람만 그것을 안다 — 그리고 잘 도는 동안에는 아무도 그 화면을 안 연다.
    그것이 정기 작업의 기본 실패 방식이고, 그 사실은 데이터가 몇 주 낡은 뒤에야
    드러난다.

    시스템 관리자에게만. 데이터 소스 화면을 그들만 열 수 있어서, 다른 사람에게 띄우면
    그 줄은 못 지우는 숫자가 된다.
    """
    if not viewer.is_system_admin:
        return []
    failed = list(
        db.scalars(
            select(DataSource).where(
                DataSource.is_active.is_(True), DataSource.last_status == "failed"
            )
        )
    )
    now = datetime.now(UTC)
    stale = [
        one
        for one in db.scalars(
            select(DataSource).where(
                DataSource.is_active.is_(True), DataSource.interval_minutes > 0
            )
        )
        if one.last_status != "failed"
        and one.last_run_at is not None
        and one.last_run_at + timedelta(minutes=one.interval_minutes * STALE_FACTOR) < now
    ]
    return [
        extensions.MaintenanceItem(
            key="datasource_failed",
            label="동기화가 실패한 데이터 소스",
            count=len(failed),
            link="/admin/datasources",
            # **경고다.** 여기가 실패하면 화면의 값이 조용히 낡아 가고, 그 사실은
            # 값 자체로는 드러나지 않는다 — 틀린 값이 아니라 옛 값이기 때문이다.
            severity="warning",
        ),
        extensions.MaintenanceItem(
            key="datasource_stale",
            label="정해진 간격보다 오래 안 돈 데이터 소스",
            count=len(stale),
            link="/admin/datasources",
            severity="warning",
        ),
    ]


def stats(db: Session) -> list[extensions.StatItem]:
    total = db.scalar(select(func.count()).select_from(DataSource)) or 0
    return [extensions.StatItem(label="데이터 소스", count=int(total))]


def sync_out(result: SyncResult) -> SyncOut:
    """결과를 API 모양으로 — 라우터와 워커가 같은 것을 돌려준다."""
    return SyncOut(
        run=RunOut.model_validate(result.run),
        applied=result.run.applied,
        counts=dict(result.run.counts or {}),
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
            for one in result.plan_rows
        ],
        errors=list(result.run.errors or []),
        truncated=result.truncated,
    )
