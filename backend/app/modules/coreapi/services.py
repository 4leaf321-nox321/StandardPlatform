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

쪽을 넘기는 동안의 `as_of` 는 **첫 쪽의 시각**이다(커서에 실어 다닌다). 마지막 쪽의 시각을
주면 그 사이 커밋된 긴 적재(시각은 시작 때 — 커서보다 앞)를 이번에도 다음에도 못 받는다.

## 더 이상 안 보이는 것

보이던 부서에서 안 보이는 부서로 옮긴 객체는 그 자격에게 **사라진 것**이다 — 무덤(`deleted`
· `hidden`)으로 알린다. 안 그러면 받는 쪽은 그 행을 영영 살아 있는 것으로 든다.
"""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, exists, func, literal, or_, select, text
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.audit.models import AuditEntry
from app.modules.coreapi.schemas import (
    CoreCatalogOut,
    CoreConsumerOut,
    CorePageOut,
    CorePropertyOut,
    CorePullOut,
    CoreRelationEndOut,
    CoreRelationKindOut,
    CoreRelationOut,
    CoreRelationPageOut,
    CoreRowOut,
    CoreStatusOut,
    CoreTypeOut,
)
from app.modules.objects import aliases
from app.modules.objects import keys as key_history
from app.modules.objects.models import (
    ObjectInstance,
    ObjectRelation,
    ObjectRelationTombstone,
)
from app.modules.objects.services import properties_of
from app.modules.ontology.interfaces import Ends, load_ends
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.workspaces.models import WorkspaceMember
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


def watermark(db: Session) -> datetime:
    """밖에 주는 「여기까지 봤다」(`as_of`) — **아직 안 끝난 적재보다 앞서지 않는다.**

    ⚠️ 왜 그냥 지금이 아닌가: `objects.updated_at` 은 `now()`, 곧 **트랜잭션이 시작한**
       시각이다. 2만 줄 적재가 T0 에 시작해 T2 에 끝나면 그 행들의 시각은 전부 T0 다.
       그 사이(T1)에 받아 간 쪽은 **그 적재를 못 보고** `as_of=T1` 을 적어 둔다 — 다음
       호출은 `updated_at > T1` 이라, **그 적재분을 영영 못 받는다**(0.4.16 부터 있던 구멍).

    그래서 지금 도는 트랜잭션 중 **가장 먼저 시작한 것보다 한 틱 앞**을 준다. 받는 쪽은
    같은 행을 한 번 더 받을 뿐이고(덮어쓰기라 해가 없다), 잃지는 않는다.

    ⚠️ **도는 것과 붙잡힌 것을 가른다.** 트랜잭션이 얼마나 **오래됐나**로 자르면(예: 10분)
       한 시간 도는 백필이 셈에서 빠져 **그 구멍이 그대로 돌아온다.** 그래서 자를 「시작한
       때」 가 아니라 **「마지막으로 무엇이라도 한 때」**(`state_change`)에 건다 — 백필은
       문장 사이가 밀리초이므로 늘 센다. 열어 두고 잊은 세션만 빠진다.

       (`state='active'` 하나로 가를 수는 없다 — 문장이 도는 순간에만 그렇고, 백필은 문장
       사이에 `idle in transaction` 이 된다. 그 순간에 물으면 빠진다.)

    그래서 **긴 백필 동안 받기를 멈출 필요가 없다.** `as_of` 가 그 자리에 머물러 있으므로
    받는 쪽은 같은 지점을 되풀이해 물을 뿐이고, 백필이 끝나면 그때부터 전부 따라온다.
    얼마나 뒤처져 있는지는 **코어 현황**(`watermark_lag_seconds`)이 말한다.
    """
    floor = get_settings().core_watermark_floor_seconds
    found = db.execute(
        text(
            """
            SELECT least(
              clock_timestamp(),
              coalesce((
                SELECT min(xact_start) - interval '1 microsecond'
                  FROM pg_stat_activity
                 WHERE datname = current_database()
                   AND xact_start IS NOT NULL
                   AND (
                     state = 'active'
                     OR coalesce(state_change, xact_start)
                        > clock_timestamp() - make_interval(secs => :floor)
                   )
              ), clock_timestamp())
            )
            """
        ),
        {"floor": float(floor)},
    ).scalar()
    return found if isinstance(found, datetime) else datetime.now(UTC)


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

    **정지 · 삭제된 계정의 토큰은 세지 않는다** — 인증(`resolve_pat`)이 이미 막으므로 읽을 수
    없는 자격이다. 세면 「이 이름을 쓰는 연동이 있다」 로 읽혀 아무도 못 지운다(2026-10-08).
    """
    from app.modules.auth.models import PersonalAccessToken

    now = datetime.now(UTC)
    rows = db.execute(
        select(PersonalAccessToken, User)
        .join(User, User.id == PersonalAccessToken.user_id)
        .where(
            PersonalAccessToken.revoked_at.is_(None),
            User.deleted_at.is_(None),
            User.status == "active",
        )
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
                id=pat.id,
                name=pat.name,
                owner=owner.display_name or owner.email,
                owner_id=owner.id,
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


def _mark(object_type: ObjectType, defs: list[PropertyDef], kinds: list[str]) -> str:
    """타입 하나의 **받는 쪽 코드가 기대는 모양** — 판(`revision`)의 재료.

    칸 이름과 자료형만으로는 모자란다. 한 값이 배열이 되거나(`multi`), 참조가 다른 타입을
    가리키거나, 고를 값이 늘거나, 선이 하나 더 열리면 받는 쪽은 해석을 바꿔야 한다 — 그런데
    판이 그대로면 「구조가 그대로다」 로 읽는다. 이름 · 설명 같은 표시 문구는 넣지 않는다:
    그것이 바뀔 때마다 판이 달라지면 받는 쪽은 곧 이 값을 안 본다.

    ⚠️ **칸의 차례는 키로 정한다** — 받은 차례(`sort_order` · 이름순)를 쓰면 표시 문구가
       차례를 통해 새어 든다. 이름만 바꿔도 차례가 바뀌어 판이 바뀌고, 이름의 정렬은 DB 의
       정렬 규칙(`C.UTF-8` · `en_US.utf8`)마다 달라 같은 정의가 설치마다 다른 판이 된다 —
       CI 에서 「이름만 바꿨는데 판이 바뀌었다」 로 깨졌다(로컬에서는 통과). 화면에서 칸
       순서를 바꾼 것도 판을 바꿨다.
    """
    columns = ",".join(
        f"{one.key}:{one.data_type}"
        + ("[]" if one.multi else "")
        + (f">{one.ref_type_slug}" if one.ref_type_slug else "")
        + ("=" + "/".join(one.enum_options) if one.enum_options else "")
        for one in sorted(defs, key=lambda one: one.key)
    )
    return f"{object_type.slug}:{columns};{','.join(sorted(kinds))}"


def revision_of(marks: list[str]) -> str:
    """열린 정의의 판 — **같은 구조면 어느 프로세스에서 물어도 같은 값.**

    ⚠️ 내장 `hash()` 를 쓰지 않는다. 문자열 해시는 프로세스마다 씨가 달라서(PYTHONHASHSEED)
       허브를 다시 띄울 때마다, 워커가 여럿이면 요청마다 판이 바뀌었다 — 받는 쪽은 구조가
       그대로인데도 「구조 변경」 을 통지받았다(0.4.40 까지, 같은 입력이 두 프로세스에서
       6120845454 · 8701402863 으로 실측).
    """
    # 타입의 차례도 slug 로 — 받은 차례는 사이드바 순서(`sort_order`)라 표시다.
    digest = hashlib.sha256("|".join(sorted(marks)).encode("utf-8")).hexdigest()
    return f"{len(marks)}-{int(digest, 16) % 10**10:010d}"


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

    open_slugs = {one.slug for one in types}
    ends = load_ends(db)
    for object_type in types:
        defs = _shown_defs(db, object_type)
        count = counts.get(object_type.id, 0)
        latest = latest_of.get(object_type.id)
        opened = open_relation_kinds(db, object_type, open_slugs, ends)
        kinds = [one.slug for one in opened]
        out.append(
            CoreTypeOut(
                slug=object_type.slug,
                label=object_type.label,
                description=object_type.description,
                count=count,
                updated_at=_stamp(latest),
                properties=[_property_out(one) for one in defs],
                endpoint=f"{base}/{object_type.slug}",
                relations_endpoint=(f"{base}/{object_type.slug}/relations" if kinds else None),
                relations=kinds,
                relation_kinds=opened,
            )
        )
        marks.append(_mark(object_type, defs, [_kind_mark(one) for one in opened]))

    return CoreCatalogOut(
        system=get_settings().app_slug,
        # **구조가 바뀌면 값이 바뀐다.** 날짜로 두면 칸이 안 바뀐 날에도 달라져서, 받는 쪽은
        # 곧 그것을 안 보게 된다.
        revision=revision_of(marks),
        as_of=_stamp(datetime.now(UTC)) or "",
        types=out,
    )


def _ref_keys(
    db: Session, user: User, defs: list[PropertyDef], rows: list[ObjectInstance]
) -> dict[str, str | None]:
    """참조 값(id) → 상대의 `key`. **id 로 주면 받는 쪽에서 아무것도 못 가리킨다.**

    **토큰 주인이 못 보는 상대는 None** — 그 값은 내보내지 않는다(`_properties_out`). 상대의
    부서를 보지 않고 풀었더니 안 보이는 부서 객체의 식별자 · 이름이 참조 칸으로 샜다
    (2026-10-08)."""
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
    seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    found = db.execute(
        select(
            ObjectInstance.id, ObjectInstance.key, ObjectInstance.label, seen.label("seen")
        ).where(ObjectInstance.id.in_(wanted))
    )
    return {str(one.id): (one.key or one.label) if one.seen else None for one in found}


def _properties_out(
    row: ObjectInstance, defs: list[PropertyDef], ref_keys: dict[str, str | None]
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
            # 못 보는 상대(None)는 뺀다 — 다 빠지면 빈 값처럼 키를 뺀다.
            resolved = [ref_keys.get(str(one), str(one)) for one in items]
            items = [one for one in resolved if one is not None]
            if not items:
                continue
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
    """한 쪽 — 바뀐 것과 **사라진 것**(지운 것 · 더 이상 안 보이는 것)을 함께."""
    # **시계는 DB 것을 쓰고, 도는 적재보다 앞서지 않는다**(`watermark` 의 설명). 쪽을 넘기는
    # 중이면 첫 쪽의 시각이 커서에 실려 온다.
    started = _cursor(cursor)[2] if cursor else None
    now = watermark(db)
    as_of = min(started, now) if started is not None else now
    defs = _shown_defs(db, object_type)
    seen = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    shown: ColumnElement[bool] = seen
    if since is not None and not user.is_system_admin:
        shown = or_(seen, _moved_away(user, since))
    stmt = select(ObjectInstance, seen.label("seen")).where(
        ObjectInstance.type_id == object_type.id, shown
    )
    if since is not None:
        stmt = stmt.where(ObjectInstance.updated_at > since)
    else:
        # 처음 받아 가는 쪽에는 **지워진 것을 보내지 않는다** — 없던 것을 지우라고 할 이유가
        # 없고, 오래된 무덤이 첫 적재를 채운다.
        stmt = stmt.where(ObjectInstance.deleted_at.is_(None))
    if cursor:
        mark, last, _ = _cursor(cursor)
        stmt = stmt.where(
            (ObjectInstance.updated_at > mark)
            | ((ObjectInstance.updated_at == mark) & (ObjectInstance.id > last))
        )

    # **정렬을 고정한다** — 쪽을 넘기는 사이 순서가 바뀌면 행이 빠지거나 두 번 온다.
    picked = db.execute(
        stmt.order_by(ObjectInstance.updated_at, ObjectInstance.id).limit(limit + 1)
    ).all()
    more = len(picked) > limit
    picked = picked[:limit]
    found = [one for one, _ in picked]
    hidden = {one.id for one, visible in picked if not visible}
    shown_rows = [one for one in found if one.id not in hidden]

    ref_keys = _ref_keys(db, user, defs, shown_rows)
    human = aliases.human_of(db, [one.id for one in shown_rows])
    merged_keys = _merged_keys(db, user, shown_rows)
    items = [
        _gone_out(one)
        if one.id in hidden
        else CoreRowOut(
            key=one.key or one.label,
            label=one.label,
            status=one.status,
            updated_at=_stamp(one.updated_at) or "",
            deleted=one.deleted_at is not None,
            merged_into=merged_keys.get(one.merged_into_id) if one.merged_into_id else None,
            renamed_from=key_history.latest(one),
            previous_keys=[x for x in (one.previous_keys or []) if x and x != one.key],
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
        # 남은 쪽을 영영 안 받는다 — 그래서 남았으면 아예 안 준다(null). 주는 시계는 **첫 쪽의
        # 것**이다(`_cursor`).
        as_of=None if more else _stamp(as_of),
        since=_stamp(since),
        next=(
            _next_cursor(last_row.updated_at, last_row.id, as_of)
            if more and last_row
            else None
        ),
        items=items,
    )


def _cursor(raw: str) -> tuple[datetime, uuid.UUID, datetime | None]:
    """커서 → (마지막 행의 시각, 그 id, 첫 쪽의 `as_of`).

    **첫 쪽의 시계를 커서에 실어 다닌다.** 마지막 쪽을 부른 때의 시계를 주면 이런 일이 났다:
    긴 적재 A 가 T0 에 시작해 도는 동안 뒤에 시작한 짧은 적재들의 행이 쪽을 넘어 커서가 T0 를
    지나고, 그 사이 A 가 커밋하면 A 의 행(시각 T0 < 커서)은 이번 쪽 넘김에서 건너뛰어지는데
    마지막 쪽의 시계는 T0 보다 뒤라 다음 주기의 `since` 로도 못 받는다 — **영영 못 받는다**
    (2026-10-08). 첫 쪽의 시계는 그때 도는 A 보다 앞이므로 다음 주기가 A 를 받는다.

    옛 커서(`시각|id`, 시계 없음)도 받는다 — 그때는 지금 시계를 쓴다(예전과 같다)."""
    at, _, tail = raw.partition("|")
    last, _, started = tail.partition("|")
    try:
        return (
            datetime.fromisoformat(at),
            uuid.UUID(last),
            datetime.fromisoformat(started) if started else None,
        )
    except ValueError:
        raise NotFound(
            code("CORE", 2), "커서를 읽을 수 없습니다. 처음부터 받으세요."
        ) from None


def _next_cursor(at: datetime, last: uuid.UUID, as_of: datetime) -> str:
    return f"{at.isoformat()}|{last}|{as_of.isoformat()}"


def _moved_away(user: User, since: datetime) -> ColumnElement[bool]:
    """`since` 뒤에 **보이던 부서에서 옮겨 간** 객체 — 감사 기록에 소유 부서가 보이는 쪽(전역 ·
    내 부서)에서 바뀐 것이 있다. 지금 안 보이면 그 자격에게는 사라진 것이다(무덤으로 보낸다).

    「보이던 쪽」 은 **토큰 주인의 지금 소속**으로 가른다 — 옮겨 오기 전 부서를 못 보던
    자격에게는 그 객체가 처음 보이는 것이고(보통 행으로 온다), 둘 다 못 보면 아무것도 안
    간다(식별자도 안 샌다).

    부서 통폐합(`objects/services._move_objects`)도 옮긴 객체마다 기록을 남긴다 — 예전에는
    통폐합 한 줄뿐이라 여기 안 걸렸고, 원본 부서만 보던 수신 측은 옮겨 간 객체를 영영 살아
    있는 것으로 들었다(2026-10-08). 객체마다의 기록이라 이 물음은 객체 id 하나로 감사 기록의
    색인(`target_id`)을 탄다 — 통폐합 한 줄의 「언제 · 어디서 어디로」 를 풀어 그때 옮긴
    객체를 되짚는 길은 없다(옮긴 뒤에는 원래 그 부서에 있던 것과 옮겨 온 것이 안 갈린다)."""
    mine = select(cast(WorkspaceMember.workspace_id, String)).where(
        WorkspaceMember.user_id == user.id
    )
    change = AuditEntry.changes["owner_workspace_id"]
    before = change["before"].astext
    return exists().where(
        AuditEntry.target_table == "objects",
        AuditEntry.target_id == ObjectInstance.id,
        AuditEntry.created_at > since,
        AuditEntry.changes.has_key("owner_workspace_id"),
        or_(before.is_(None), before.in_(mine)),
    )


def _gone_out(row: ObjectInstance) -> CoreRowOut:
    """더 이상 안 보이는 객체의 무덤 — 받는 쪽이 제 행을 찾을 식별자만. 옮겨 간 뒤의 이름 ·
    칸은 그 자격이 볼 수 없는 것이라 싣지 않는다."""
    key = row.key or row.label
    return CoreRowOut(
        key=key,
        label=key,
        status=row.status,
        updated_at=_stamp(row.updated_at) or "",
        deleted=True,
        hidden=True,
        renamed_from=key_history.latest(row),
        previous_keys=[x for x in (row.previous_keys or []) if x and x != row.key],
        properties={},
    )


def _open_end(
    slugs: list[str] | None, open_slugs: set[str], ends: Ends
) -> list[CoreRelationEndOut] | None:
    """관계 종류의 한쪽 끝 → 그 끝의 **약속**(적힌 것과 거기 설 수 있는 열린 타입). 못 열면
    None.

    - 끝을 안 적었으면(NULL = 제약 없음) 못 연다 — 무엇이 올지 우리도 모르는 선을 밖으로
      내보낼 수는 없다.
    - 적힌 **타입**이 하나라도 안 열렸으면 못 연다(예전 그대로). 타입 목록은 종류를 고쳐야만
      바뀌는 약속이라, 일부만 연 채로 내보내면 그 약속의 반쪽만 나간다.
    - **인터페이스**는 구현한 타입 중 **열린 것만** 그 끝에 선다(선마다 끝 객체의 타입을 본다
      — `relations`). 구현 타입이 **전부** 열려야 열리게 하면, 누가 새 타입에 그 인터페이스를
      구현하는 순간 그 종류가 바깥에서 통째로 닫히고, 그 일은 화면 어디에도 안 적힌다
      (2026-10-08 — 그 전에는 끝이 인터페이스면 아예 안 열어, 허브 → 쌍둥이에서 그 선이
      통째로 빠졌다).
    - 그 끝에 설 열린 타입이 하나도 없으면 못 연다 — 나갈 선이 없는 약속은 헛말이다.
    """
    if not slugs:
        return None
    out: list[CoreRelationEndOut] = []
    for one in slugs:
        if one in ends.interfaces:
            opened = sorted(slug for slug in ends.types_of([one]) if slug in open_slugs)
            out.append(CoreRelationEndOut(slug=one, interface=True, types=opened))
        elif one in open_slugs:
            out.append(CoreRelationEndOut(slug=one, types=[one]))
        else:
            return None
    if not any(end.types for end in out):
        return None
    return out


def open_relation_kinds(
    db: Session, object_type: ObjectType, open_slugs: set[str], ends: Ends | None = None
) -> list[CoreRelationKindOut]:
    """이 타입에서 **출발하는, 양끝을 열 수 있는** 관계 종류와 그 약속(`_open_end`).

    한쪽 끝을 못 열면 보내지 않는다 — 받는 쪽은 찾을 수 없는 끝점을 쥐게 되고, 그것은
    「우리 쪽 데이터가 이상하다」 로 읽힌다. 끝에 **인터페이스**를 적은 종류는 구현한 타입 중
    열린 것이 있으면 열고, 선마다 끝 객체의 타입이 열렸는지 본다(`relations`).

    `ends` 는 타입 · 인터페이스를 펴는 표(`interfaces.load_ends`) — 카탈로그는 타입마다 이것을
    부르므로 한 번 읽어 넘긴다.
    """
    ends = ends if ends is not None else load_ends(db)
    out: list[CoreRelationKindOut] = []
    for kind in db.scalars(select(RelationType).order_by(RelationType.slug)):
        if not kind.is_active:
            continue
        src = _open_end(kind.src_type_slugs, open_slugs, ends)
        dst = _open_end(kind.dst_type_slugs, open_slugs, ends)
        if src is None or dst is None:
            continue
        if not any(object_type.slug in end.types for end in src):
            continue
        out.append(CoreRelationKindOut(slug=kind.slug, label=kind.label, src=src, dst=dst))
    return out


def _kind_mark(kind: CoreRelationKindOut) -> str:
    """판(`revision`)에 싣는 관계 종류 하나 — 끝이 타입뿐이면 slug 그대로(예전 판과 같은 값),
    **끝에 인터페이스가 있으면 도착에 설 수 있는 열린 타입까지.** 구현한 타입이 하나 더
    열리거나 구현을 해제하면 `dst_type` 에 올 수 있는 것이 달라지는데, 판이 그대로면 받는 쪽은
    「구조가 그대로다」 로 읽는다."""
    if not any(end.interface for end in kind.src + kind.dst):
        return kind.slug
    dst = sorted({slug for end in kind.dst for slug in end.types})
    return f"{kind.slug}>{'/'.join(dst)}"


def relations(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    since: datetime | None,
    cursor: str | None,
    limit: int,
) -> CoreRelationPageOut:
    """이 타입에서 출발하는 선 — **바뀐 것과 끊긴 것**을 한 흐름으로.

    객체 쪽과 같은 규칙이다(`since` · `next` · `as_of`, 시계는 DB 것). 끊긴 선은 무덤
    (`object_relation_tombstones`)에서 온다 — 선은 행을 정말 지우기 때문이다. 둘을 시각으로
    한 줄에 세워 보내므로, 받는 쪽은 **온 차례대로 적용하면** 마지막 상태가 맞는다.

    **양 끝이 모두 보이는 선만** 나간다 — 출발점만 보고 도착점의 부서를 안 봤더니 못 보는
    부서 객체의 식별자가 `dst` 로 샜다(2026-10-08).

    **도착 객체의 타입이 열린 선만** 나간다 — 끝이 인터페이스인 종류는 구현한 타입 중 안 열린
    것의 객체도 잇는다. 그 선을 보내면 받는 쪽이 못 찾는 끝점을 쥔다(무덤도 같은 규칙 — 받은
    적 없는 선을 끊으라고 할 이유가 없다).
    """
    started = _cursor(cursor)[2] if cursor else None
    now = watermark(db)
    as_of = min(started, now) if started is not None else now
    opened_types = core_types(db)
    open_slugs = {one.slug for one in opened_types}
    open_ids = [one.id for one in opened_types]
    kinds = open_relation_kinds(db, object_type, open_slugs)
    if not kinds:
        return CoreRelationPageOut(
            type_slug=object_type.slug, as_of=_stamp(as_of), since=_stamp(since), items=[]
        )
    horizon = now - timedelta(days=get_settings().tombstone_ttl_days)
    if since is not None and since < horizon:
        # **모른다고 말한다.** 그 시각 이후에 끊긴 선 중 일부는 이미 정리됐다(무덤은 한동안만
        # 들고 있다). 빈 쪽을 주면 받는 쪽은 「바뀐 것 없음」 으로 읽고 끊긴 선을 영영 들고
        # 있는다 — 그것이 가장 나쁜 답이다.
        return CoreRelationPageOut(
            type_slug=object_type.slug,
            as_of=None,
            since=_stamp(since),
            items=[],
            reset=True,
            reset_reason=(
                f"{get_settings().tombstone_ttl_days}일보다 오래된 시점부터는 "
                "끊긴 선을 알려 줄 수 없습니다 — `since` 를 비우고 처음부터 받으세요."
            ),
        )
    slugs = [one.slug for one in kinds]
    visible = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    src = ObjectInstance
    dst = aliased(ObjectInstance)
    reachable = visible_owner_clause(user, dst.owner_workspace_id)
    # 출발점이 이 타입인 것만. 양 끝이 보이는 부서의 것만(객체 쪽과 같은 규칙).
    live = (
        select(
            ObjectRelation.id.label("id"),
            ObjectRelation.updated_at.label("at"),
            literal(False).label("gone"),
        )
        .join(src, src.id == ObjectRelation.src_object_id)
        .join(dst, dst.id == ObjectRelation.dst_object_id)
        .where(
            ObjectRelation.relation.in_(slugs),
            src.type_id == object_type.id,
            src.deleted_at.is_(None),
            dst.type_id.in_(open_ids),
            visible,
            reachable,
        )
    )
    if since is not None:
        live = live.where(ObjectRelation.updated_at > since)
    # **무덤도 출발 타입으로 거른다.** 안 그러면 같은 관계 종류를 쓰는 다른 타입의 끊긴 선이
    # 섞여 오고, 받는 쪽은 제 것이 아닌 선을 끊으려다 「없다」 만 본다.
    graves = (
        select(
            ObjectRelationTombstone.id.label("id"),
            ObjectRelationTombstone.removed_at.label("at"),
            literal(True).label("gone"),
        )
        .join(src, src.id == ObjectRelationTombstone.src_object_id)
        .join(dst, dst.id == ObjectRelationTombstone.dst_object_id)
        .where(
            ObjectRelationTombstone.relation.in_(slugs),
            src.type_id == object_type.id,
            dst.type_id.in_(open_ids),
            visible,
            reachable,
        )
    )
    if since is not None:
        graves = graves.where(ObjectRelationTombstone.removed_at > since)
    else:
        # 처음 받는 쪽에 무덤을 보내지 않는다 — 없던 선을 끊으라고 할 이유가 없다.
        graves = graves.where(sa_false())

    # **쪽 넘김은 SQL 이 한다.** 예전에는 둘을 전부 읽어 파이썬에서 세웠다 — 쪽을 넘길
    # 때마다 처음부터 다시 읽는 셈이라, 선이 몇만이면 마지막 쪽이 가장 느렸다.
    stream = live.union_all(graves).subquery()
    ordered = select(stream).order_by(stream.c.at, stream.c.id)
    if cursor:
        mark, last, _ = _cursor(cursor)
        ordered = ordered.where(
            (stream.c.at > mark) | ((stream.c.at == mark) & (stream.c.id > last))
        )
    picked = db.execute(ordered.limit(limit + 1)).all()
    more = len(picked) > limit
    picked = picked[:limit]

    rows_by_id: dict[uuid.UUID, Any] = {}
    live_ids = [one.id for one in picked if not one.gone]
    grave_ids = [one.id for one in picked if one.gone]
    if live_ids:
        rows_by_id.update(
            {
                one.id: one
                for one in db.scalars(
                    select(ObjectRelation).where(ObjectRelation.id.in_(live_ids))
                )
            }
        )
    if grave_ids:
        rows_by_id.update(
            {
                one.id: one
                for one in db.scalars(
                    select(ObjectRelationTombstone).where(
                        ObjectRelationTombstone.id.in_(grave_ids)
                    )
                )
            }
        )
    marks: list[tuple[datetime, uuid.UUID, bool, Any]] = [
        (one.at, one.id, bool(one.gone), rows_by_id[one.id])
        for one in picked
        if one.id in rows_by_id
    ]

    wanted: set[uuid.UUID] = set()
    for _, _, _, row in marks:
        wanted.add(row.src_object_id)
        wanted.add(row.dst_object_id)
    ends = {
        one.id: one
        for one in db.scalars(
            select(ObjectInstance).where(ObjectInstance.id.in_(wanted or {uuid.uuid4()}))
        )
    }
    type_slugs = {
        one.id: one.slug
        for one in db.scalars(
            select(ObjectType).where(
                ObjectType.id.in_({one.type_id for one in ends.values()} or {uuid.uuid4()})
            )
        )
    }
    items: list[CoreRelationOut] = []
    for when, _, gone, row in marks:
        start, end = ends.get(row.src_object_id), ends.get(row.dst_object_id)
        if start is None or end is None:
            # 끝점까지 정말 사라진 줄 — 그 객체의 무덤이 이미 그것을 말한다.
            continue
        items.append(
            CoreRelationOut(
                src=start.key or start.label,
                relation=row.relation,
                dst=end.key or end.label,
                dst_type=type_slugs.get(end.type_id, ""),
                evidence_note="" if gone else (row.evidence_note or ""),
                properties={} if gone else {k: v for k, v in (row.properties or {}).items()},
                updated_at=_stamp(when) or "",
                deleted=gone,
            )
        )
    last_mark = marks[-1] if marks else None
    return CoreRelationPageOut(
        type_slug=object_type.slug,
        # 객체 쪽과 같이 **첫 쪽의 시계**(`_cursor`).
        as_of=None if more else _stamp(as_of),
        since=_stamp(since),
        next=(_next_cursor(last_mark[0], last_mark[1], as_of) if more and last_mark else None),
        items=items,
    )


def _aliases_of(human: dict[uuid.UUID, list[str]], row: ObjectInstance) -> dict[str, Any]:
    """다른 이름들 — 받는 쪽이 제 데이터와 맞춰 볼 때 쓴다. 없으면 키를 안 만든다."""
    names = human.get(row.id) or []
    return {"aliases": names} if names else {}


def _merged_keys(db: Session, user: User, rows: list[ObjectInstance]) -> dict[uuid.UUID, str]:
    """합쳐진 것의 이긴 쪽 `key` — **보이는 것만**(참조 칸과 같은 규칙, `_ref_keys`)."""
    wanted = {one.merged_into_id for one in rows if one.merged_into_id}
    if not wanted:
        return {}
    return {
        one.id: one.key or one.label
        for one in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_(wanted),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
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
        # **받아 가는 쪽이 지금 어디까지 볼 수 있나.** 긴 적재가 도는 동안 커지고 끝나면
        # 0 으로 돌아온다 — 줄지 않으면 열어 두고 잊은 세션이 있다는 뜻이다.
        watermark_lag_seconds=max(
            0,
            int(
                (
                    (db.scalar(select(func.clock_timestamp())) or datetime.now(UTC))
                    - watermark(db)
                ).total_seconds()
            ),
        ),
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
