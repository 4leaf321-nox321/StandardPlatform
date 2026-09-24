"""코어 — **바깥에 연 타입만** 골라, 지난번 이후 바뀐 것을 준다.

## 왜 목록 API(`/api/objects/...`)를 쓰라고 하지 않나

세 가지가 다르다.

    무엇이 열렸나   목록은 그 계정이 볼 수 있는 **전부**를 연다. 여기는 `core` 를 켠 타입만.
    증분            목록에는 「지난번 이후」 가 없다. 주기 동기화는 그것이 핵심이다.
    사라진 것       목록은 살아 있는 것만 준다. 그러면 받는 쪽은 **삭제를 영영 모른다.**

## 시각은 우리가 준다

응답의 `as_of` 를 다음 호출에 그대로 넣게 한다. 받는 쪽 시계를 쓰면 몇 초 차이로 그 사이
행이 샌다 — 그리고 샌 줄을 아무도 모른다.

경계는 `updated_at > since` 로 **연다**(같은 값은 뺀다). 같은 밀리초에 둘이 바뀌면 한 번 더
받을 뿐이고, 받는 쪽은 같은 값을 덮어쓰므로 해가 없다 — 반대로 닫으면 잃는다.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.coreapi.schemas import (
    CoreCatalogOut,
    CoreConsumerOut,
    CorePageOut,
    CorePropertyOut,
    CorePullOut,
    CoreRowOut,
    CoreStatusOut,
    CoreTypeOut,
)
from app.modules.objects import aliases
from app.modules.objects.models import ObjectInstance
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import audit
from app.shared.errors import NotFound, code
from app.shared.permissions import visible_owner_clause

#: 한 번에 줄 행의 기본·상한. 상한을 서버가 강제하는 이유는 목록과 같다 — 받는 쪽이
#: `limit=1000000` 을 넣는 날 이쪽이 죽는다.
DEFAULT_LIMIT = 500
MAX_LIMIT = 2_000

#: 이 창구를 읽는 범위. 좁은 것과 넓은 것 둘 다 읽을 수 있으므로 둘 다 센다.
CORE_SCOPE = "core:read"
READ_SCOPE = "read"


def _stamp(when: datetime | None) -> str | None:
    if when is None:
        return None
    # **초로 자르지 않는다.** 자르면 그 초 안에 바뀐 행이 다음 호출에 다시 오고(해는 없지만
    # 「받아 갈 것 없음」 을 말할 수 없다), 경계가 흐려진다.
    return when.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def core_types(db: Session) -> list[ObjectType]:
    """열린 타입 — 켜 둔 것 중 **쓰는 것만.** 사용 중지한 타입까지 내보내면 받는 쪽은
    그것이 아직 쓰이는 줄 안다."""
    return list(
        db.scalars(
            select(ObjectType)
            .where(
                ObjectType.core.is_(True),
                ObjectType.is_active.is_(True),
                ObjectType.kind_class != "system",
            )
            .order_by(ObjectType.sort_order, ObjectType.slug)
        )
    )


def find_core_type(db: Session, slug: str) -> ObjectType:
    found = next((one for one in core_types(db) if one.slug == slug), None)
    if found is None:
        # **「없다」 와 「안 열었다」 를 같은 말로 답한다** — 어느 타입이 있는지가 바깥에
        # 새면 그것 자체가 정보다. 열린 것은 카탈로그가 말한다.
        raise NotFound(
            code("CORE", 1),
            f"열려 있는 코어 타입이 아닙니다: {slug}. 「GET /api/core」 로 목록을 보세요.",
        )
    return found


def consumers(db: Session) -> list[CoreConsumerOut]:
    """이 창구를 읽을 수 있는 **살아 있는 자격**들.

    「몇 개 시스템이 이 이름을 쓰는가」 를 말할 근거다. 정확히는 「읽을 수 있는 자격」 이지
    「지금 읽고 있는 시스템」 이 아니다 — 그래서 마지막 사용 시각을 함께 준다. 한 번도 안
    쓴 토큰과 어제도 받아 간 토큰은 다른 무게다.
    """
    from app.modules.auth.models import PersonalAccessToken

    now = datetime.now(UTC)
    rows = db.execute(
        select(PersonalAccessToken, User)
        .join(User, User.id == PersonalAccessToken.user_id)
        .where(PersonalAccessToken.revoked_at.is_(None))
        .order_by(PersonalAccessToken.created_at)
    ).all()
    out: list[CoreConsumerOut] = []
    for pat, owner in rows:
        scopes = list(pat.scopes or [])
        if CORE_SCOPE not in scopes and READ_SCOPE not in scopes:
            continue
        if pat.expires_at is not None and pat.expires_at <= now:
            continue
        out.append(
            CoreConsumerOut(
                name=pat.name,
                owner=owner.display_name or owner.email,
                last_used_at=pat.last_used_at,
                expires_at=pat.expires_at,
                narrow=CORE_SCOPE in scopes and READ_SCOPE not in scopes,
                created_at=pat.created_at,
            )
        )
    return out


def _property_out(definition: PropertyDef) -> CorePropertyOut:
    return CorePropertyOut(
        key=definition.key,
        label=definition.label,
        data_type=definition.data_type,
        unit=definition.unit,
        required=definition.required,
        multi=definition.multi,
        enum_options=list(definition.enum_options or []),
        ref_type_slug=definition.ref_type_slug,
        help=definition.help,
    )


def _shown_defs(db: Session, object_type: ObjectType) -> list[PropertyDef]:
    """나가는 칸 — 첨부(`file`)는 뺀다. 파일은 이 길로 옮길 수 없고, 목록에 이름만 남으면
    받는 쪽은 그 파일이 있는 줄 안다."""
    return [one for one in properties_of(db, object_type.id) if one.data_type != "file"]


def catalog(db: Session, user: User, *, base: str) -> CoreCatalogOut:
    """**무엇이 열려 있나.** 처음 붙는 쪽은 이것 하나만 읽으면 된다."""
    types = core_types(db)
    out: list[CoreTypeOut] = []
    marks: list[str] = []

    # **타입마다 세지 않는다 — 한 번에 센다.** 타입별로 질의를 돌리면 공개 타입이 늘어날수록
    # 카탈로그가 느려지고, 느려진 이유는 화면 어디에도 안 적힌다.
    #
    # 그리고 **집계를 서브쿼리로 감싸지 않는다.** `select(max(objects.updated_at))
    # .select_from(<objects 서브쿼리>)` 는 바깥 `objects` 와 조인 조건이 없어 **교차곱**이
    # 된다 — 6천 행이 3천7백만 행이 되어 2초가 걸렸다(실측). 조건은 `where` 로 적는다.
    visible = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    ids = [one.id for one in types]
    counts: dict[uuid.UUID, int] = {}
    latest_of: dict[uuid.UUID, datetime] = {}
    if ids:
        counts = {
            type_id: total
            for type_id, total in db.execute(
                select(ObjectInstance.type_id, func.count())
                .where(
                    ObjectInstance.type_id.in_(ids),
                    ObjectInstance.deleted_at.is_(None),
                    visible,
                )
                .group_by(ObjectInstance.type_id)
            )
        }
        latest_of = {
            type_id: when
            for type_id, when in db.execute(
                select(ObjectInstance.type_id, func.max(ObjectInstance.updated_at))
                .where(ObjectInstance.type_id.in_(ids), visible)
                .group_by(ObjectInstance.type_id)
            )
        }

    for object_type in types:
        defs = _shown_defs(db, object_type)
        count = counts.get(object_type.id, 0)
        latest = latest_of.get(object_type.id)
        out.append(
            CoreTypeOut(
                slug=object_type.slug,
                label=object_type.label,
                description=object_type.description,
                count=count,
                updated_at=_stamp(latest),
                properties=[_property_out(one) for one in defs],
                endpoint=f"{base}/{object_type.slug}",
            )
        )
        marks.append(f"{object_type.slug}:" + ",".join(f"{d.key}:{d.data_type}" for d in defs))

    return CoreCatalogOut(
        system=get_settings().app_slug,
        # **구조가 바뀌면 값이 바뀐다.** 날짜로 두면 칸이 안 바뀐 날에도 달라져서, 받는 쪽은
        # 곧 그것을 안 보게 된다.
        revision=f"{len(types)}-{abs(hash('|'.join(marks))) % 10**10:010d}",
        as_of=_stamp(datetime.now(UTC)) or "",
        types=out,
    )


def _ref_keys(
    db: Session, defs: list[PropertyDef], rows: list[ObjectInstance]
) -> dict[str, str]:
    """참조 값(id) → 상대의 `key`. **id 로 주면 받는 쪽에서 아무것도 못 가리킨다.**"""
    wanted: set[uuid.UUID] = set()
    for row in rows:
        values = row.properties or {}
        for definition in defs:
            if definition.data_type != "object_ref":
                continue
            raw = values.get(definition.key)
            for item in raw if isinstance(raw, list) else [raw]:
                if isinstance(item, str):
                    try:
                        wanted.add(uuid.UUID(item))
                    except ValueError:
                        continue
    if not wanted:
        return {}
    return {
        str(one.id): one.key or one.label
        for one in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(wanted)))
    }


def _properties_out(
    row: ObjectInstance, defs: list[PropertyDef], ref_keys: dict[str, str]
) -> dict[str, Any]:
    values = row.properties or {}
    out: dict[str, Any] = {}
    for definition in defs:
        raw = values.get(definition.key)
        if raw is None or raw == "" or raw == []:
            # **빈 값은 키를 뺀다.** 「비었다」 와 「그 칸이 없다」 를 가르는 일은 받는 쪽의
            # 몫이 아니다 — 카탈로그에 칸 목록이 있다.
            continue
        items = raw if isinstance(raw, list) else [raw]
        if definition.data_type == "object_ref":
            items = [ref_keys.get(str(one), str(one)) for one in items]
        out[definition.key] = items if definition.multi else items[0]
    return out


def page(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    since: datetime | None,
    cursor: str | None,
    limit: int,
) -> CorePageOut:
    """한 쪽 — 바뀐 것과 **사라진 것**을 함께."""
    now = datetime.now(UTC)
    defs = _shown_defs(db, object_type)
    stmt = select(ObjectInstance).where(
        ObjectInstance.type_id == object_type.id,
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )
    if since is not None:
        stmt = stmt.where(ObjectInstance.updated_at > since)
    else:
        # 처음 받아 가는 쪽에는 **지워진 것을 보내지 않는다** — 없던 것을 지우라고 할 이유가
        # 없고, 오래된 무덤이 첫 적재를 채운다.
        stmt = stmt.where(ObjectInstance.deleted_at.is_(None))
    if cursor:
        at, _, tail = cursor.partition("|")
        try:
            mark = datetime.fromisoformat(at)
            last = uuid.UUID(tail)
        except ValueError:
            raise NotFound(
                code("CORE", 2), "커서를 읽을 수 없습니다. 처음부터 받으세요."
            ) from None
        stmt = stmt.where(
            (ObjectInstance.updated_at > mark)
            | ((ObjectInstance.updated_at == mark) & (ObjectInstance.id > last))
        )

    # **정렬을 고정한다** — 쪽을 넘기는 사이 순서가 바뀌면 행이 빠지거나 두 번 온다.
    found = list(
        db.scalars(
            stmt.order_by(ObjectInstance.updated_at, ObjectInstance.id).limit(limit + 1)
        )
    )
    more = len(found) > limit
    found = found[:limit]

    ref_keys = _ref_keys(db, defs, found)
    human = aliases.human_of(db, [one.id for one in found])
    merged_keys = _merged_keys(db, found)
    items = [
        CoreRowOut(
            key=one.key or one.label,
            label=one.label,
            status=one.status,
            updated_at=_stamp(one.updated_at) or "",
            deleted=one.deleted_at is not None,
            merged_into=merged_keys.get(one.merged_into_id) if one.merged_into_id else None,
            properties=(
                {}
                if one.deleted_at is not None
                else _properties_out(one, defs, ref_keys) | _aliases_of(human, one)
            ),
        )
        for one in found
    ]
    if items:
        # **누가 언제 무엇을 가져갔나.** 토큰의 「마지막 사용」 만으로는 어느 타입을 받아
        # 갔는지 알 수 없고, 바깥에 연 창구에서 그것을 모르면 무엇을 고쳐도 되는지 판단할
        # 근거가 없다. 감사 기록이 통로(토큰 이름)를 이미 남기므로 거기에 붙인다.
        #
        # **빈 응답은 안 남긴다.** 새벽마다 「받아 갈 것 없음」 이 쌓이면 그 목록은 곧 아무도
        # 안 읽는다 — 그때 이 기록은 없는 것과 같다.
        audit.record(
            db,
            action="core.pull",
            actor=user,
            target_table="object_types",
            target_id=object_type.id,
            target_label=object_type.slug,
            changes={
                "rows": len(items),
                "deleted": sum(1 for one in items if one.deleted),
                "since": _stamp(since) or "(처음부터)",
            },
        )
        db.commit()

    last_row = found[-1] if found else None
    return CorePageOut(
        type_slug=object_type.slug,
        # **끝까지 받았을 때만 시계가 온다.** 쪽이 남았는데 `as_of` 를 주면, 거기서 멈춘 쪽은
        # 남은 쪽을 영영 안 받는다 — 그래서 남았으면 아예 안 준다(null).
        as_of=None if more else _stamp(now),
        since=_stamp(since),
        next=(
            f"{last_row.updated_at.isoformat()}|{last_row.id}" if more and last_row else None
        ),
        items=items,
    )


def _aliases_of(human: dict[uuid.UUID, list[str]], row: ObjectInstance) -> dict[str, Any]:
    """다른 이름들 — 받는 쪽이 제 데이터와 맞춰 볼 때 쓴다. 없으면 키를 안 만든다."""
    names = human.get(row.id) or []
    return {"aliases": names} if names else {}


def _merged_keys(db: Session, rows: list[ObjectInstance]) -> dict[uuid.UUID, str]:
    wanted = {one.merged_into_id for one in rows if one.merged_into_id}
    if not wanted:
        return {}
    return {
        one.id: one.key or one.label
        for one in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(wanted)))
    }


def status(db: Session, user: User, *, base: str, limit: int = 20) -> CoreStatusOut:
    """관리 화면이 읽는 한 판 — 연 것 · 읽을 수 있는 자격 · 최근에 받아 간 기록.

    **이 응답은 `/api/core` 아래에 두지 않는다.** 그 아래는 좁은 토큰(`core:read`)이 읽을 수
    있어서, 여기에 두면 받아 가는 쪽이 **다른 연동의 이름까지** 보게 된다.
    """
    from app.modules.audit.models import AuditEntry

    # `seq` 로 거꾸로 — `created_at` 은 트랜잭션 시작 시각이라 한 번에 남은 여러 줄이 같다.
    rows = db.scalars(
        select(AuditEntry)
        .where(AuditEntry.action == "core.pull")
        .order_by(AuditEntry.seq.desc())
        .limit(limit)
    )
    return CoreStatusOut(
        types=catalog(db, user, base=base).types,
        consumers=consumers(db),
        recent=[
            CorePullOut(
                at=one.created_at,
                actor=one.actor_label,
                token=one.actor_token,
                type_slug=one.target_label,
                rows=int((one.changes or {}).get("rows") or 0),
                since=str((one.changes or {}).get("since") or ""),
            )
            for one in rows
        ],
        base=base,
    )


# --- 연동 키트 --------------------------------------------------------------------
#
# 수신 측이 코드를 작성하지 못하는 경우가 많다. 그때 「개발해 주십시오」 는 대화를 몇 달
# 늘리지만, 「이것을 실행하십시오」 는 그날 끝난다. 그래서 **주소와 공개 타입을 채워 넣은**
# 한 벌을 우리가 만들어 준다 — 수신 측이 고치는 것은 토큰 한 줄이다.

#: 키트 파일이 있는 자리. 패키지 안에 두어 배포본에 항상 포함된다.
KIT_DIR = Path(__file__).resolve().parent / "kit"

#: zip 에 담을 파일과 그 안에서의 이름.
KIT_FILES = ("README.md", "config.example.ini", "sp_core_pull.py", "check.sh")


def kit_zip(db: Session, user: User, *, base: str) -> bytes:
    """연동 키트 한 벌 — **주소와 공개 타입이 이미 채워진 상태로.**

    비워 두고 「여기에 주소를 적으십시오」 라고 하면, 수신 측은 그 주소를 메일에서 찾아
    옮겨 적다가 오타를 낸다. 우리가 아는 값은 우리가 채운다.
    """
    catalog_out = catalog(db, user, base=base)
    slugs = [one.slug for one in catalog_out.types]
    # **키트가 쓰는 것은 API 루트다**(`…/api`). 클라이언트가 `{base}/core/<타입>` 으로
    # 조립하므로 여기에 창구 주소(`…/api/core`)를 넣으면 `/core/core/…` 가 된다 — 실측으로
    # 그렇게 나왔다.
    api_root = base[: -len("/core")] if base.endswith("/core") else base
    fill = {
        "{BASE}": api_root,
        "{SYSTEM}": catalog_out.system,
        "{SYSTEM_NAME}": get_settings().app_name,
        "{REVISION}": catalog_out.revision,
        "{AT}": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "{TYPES}": ", ".join(slugs),
        "{TYPES_LONG}": (
            ", ".join(
                f"{one.label}(`{one.slug}`, {one.count:,}건)" for one in catalog_out.types
            )
            or "없음 — 공개된 타입이 없습니다"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in KIT_FILES:
            text = (KIT_DIR / name).read_text(encoding="utf-8")
            for token, value in fill.items():
                text = text.replace(token, value)
            info = zipfile.ZipInfo(f"sp-core-client/{name}")
            # 실행 권한을 살려 둔다 — 풀자마자 `./check.sh` 가 되게.
            info.external_attr = (0o755 if name.endswith(".sh") else 0o644) << 16
            bundle.writestr(info, text)
    return buffer.getvalue()
