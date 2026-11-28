"""지켜보기 — **내가 보던 그것이 바뀌면 알려 준다.**

데이터를 함께 쓰는 플랫폼에서 가장 자주 나오는 물음은 「내가 보던 그게 아직 그대로인가」
다. 그것을 아는 방법이 목록을 다시 여는 것뿐이면 사람은 안 열고, **옛 값을 들고 회의에
들어간다.** 그 순간 플랫폼은 진실의 자리를 잃는다.

## 감사 기록이 곧 「알릴 만한 변경」 이다

새로 심지 않는다. `audit.record` 가 이미 이 틀에서 「되돌릴 수 없거나 권한이 실린 변경」
의 깔때기이고, 그것이 커밋 뒤 이벤트로 흐른다(`shared/events.py`). 웹훅이 듣는 그 줄기에
하나를 더 붙일 뿐이다 — 따로 심으면 두 벌이 되고, 두 벌은 반드시 갈린다.

## 내가 한 일은 나에게 안 알린다

자기 행동을 알림으로 돌려받으면 그 종은 곧 잡음이 되고, **잡음이 된 종은 진짜 하나가
울려도 안 읽힌다.** 그래서 이벤트의 `actor_id` 를 보고 그 사람만 뺀다.

## 만든 사람은 자동으로 지켜본다

스스로 켜야만 하는 기능은 켜는 법을 아는 사람만 쓰고, 그 사람은 대개 이미 알고 있는
사람이다. 만든 것은 그 사람이 책임지는 것이라, 거기서 시작하는 것이 맞다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.notifications import services as notifications
from app.modules.objects.models import ObjectInstance, ObjectRelation, ObjectWatch
from app.modules.objects.system import HIDDEN_LABEL
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.shared import events
from app.shared.batches import chunks
from app.shared.permissions import visible_owner_clause

logger = logging.getLogger(__name__)

#: 선의 일 — 선 하나에 끝이 둘이라 **두 끝을 지켜보는 사람 모두**에게 간다(「관계가
#: 추가됐습니다」 는 어느 끝에서 읽어도 맞는 말이고, 객체의 이력도 두 끝 모두에 그 줄을
#: 싣는다).
#: 기록은 객체가 아니라 선의 표(`object_relations` · 원 표와 이은 `object_links`)에 남는다 —
#: 객체의 일만 보던 때는 이 셋이 목록에 있는데도 한 번도 안 갔다(2026-11-28).
RELATION_ACTIONS = frozenset(
    {"object.relation.add", "object.relation.update", "object.relation.remove"}
)
RELATION_TABLES = frozenset({"object_relations", "object_links"})

#: 알릴 만한 것 — 객체 자신이 바뀌는 일과 그것에 걸린 선의 일. 만들기는 뺀다(막 만든 사람만
#: 지켜보고 있고, 그 사람은 방금 자기가 만들었다). 「이력의 그 값으로」(`object_restore`)는
#: 고치기와 같은 `object.update` 로 남는다 — 지운 객체를 되살리는 길은 없다(AGENTS.md).
WATCHED_ACTIONS = {
    "object.update",
    "object.delete",
    "object.merge",
    *RELATION_ACTIONS,
    "object.years.set",
    "object.attachment.add",
    "object.attachment.remove",
}

#: 이 일이 같은 커밋에 있으면 그 객체의 선 알림은 안 보낸다 — 객체를 지우면 선도 함께
#: 끊기는데(`lifecycle.delete_detaching`), 「삭제됐습니다」 한 통 뒤에 선마다 한 통씩 더 가면
#: 그것은 잡음이다.
_SWALLOWS_RELATIONS = frozenset({"object.delete", "object.merge"})

#: 무엇이 바뀌었는지 한 줄로. 사람이 알림만 보고 열지 말지를 정한다. 말은 용어집의 공식
#: 동사(수정 · 삭제 · 병합 · 추가 · 해제)다 — 「지워졌습니다 · 값이 바뀌었습니다」 는 구어였다.
LABELS = {
    "object.update": "값이 수정됐습니다",
    "object.delete": "삭제됐습니다",
    "object.merge": "다른 객체에 병합됐습니다",
    # 선의 말은 용어집대로(추가 · 수정 · 해제) — 처음으로 화면에 나가는 문구다.
    "object.relation.add": "관계가 추가됐습니다",
    "object.relation.update": "관계가 수정됐습니다",
    "object.relation.remove": "관계가 해제됐습니다",
    "object.years.set": "연도가 수정됐습니다",
    "object.attachment.add": "첨부가 추가됐습니다",
    "object.attachment.remove": "첨부가 삭제됐습니다",
}


def watching(db: Session, *, object_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(ObjectWatch.id).where(
                ObjectWatch.object_id == object_id, ObjectWatch.user_id == user_id
            )
        )
        is not None
    )


def set_watching(db: Session, *, object_id: uuid.UUID, user_id: uuid.UUID, on: bool) -> bool:
    """켜거나 끈다. **여러 번 눌러도 같은 결과다** — 두 번 누른 사람이 두 통을 받지 않는다."""
    if not on:
        db.execute(
            delete(ObjectWatch).where(
                ObjectWatch.object_id == object_id, ObjectWatch.user_id == user_id
            )
        )
        return False
    if not watching(db, object_id=object_id, user_id=user_id):
        db.add(ObjectWatch(object_id=object_id, user_id=user_id))
    return True


def watch_own(db: Session, *, object_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """만든 사람이 지켜본다. **커밋은 부르는 쪽이** — 만들기와 같은 트랜잭션에 있어야
    「객체는 생겼는데 지켜보기는 안 걸린」 상태가 안 생긴다."""
    set_watching(db, object_id=object_id, user_id=user_id, on=True)


def watchers(db: Session, object_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    """객체 → 지켜보는 사람. **나눠 묻는다** — 10만 행 일괄 입력의 커밋 뒤에 한 `IN` 으로
    물으면 바인드 한도(65,535)에 걸려 알림이 통째로 빠졌다(실측)."""
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for batch in chunks(dict.fromkeys(object_ids)):
        for row in db.scalars(select(ObjectWatch).where(ObjectWatch.object_id.in_(batch))):
            out.setdefault(row.object_id, []).append(row.user_id)
    return out


def counts(db: Session, object_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    return {key: len(value) for key, value in watchers(db, object_ids).items()}


#: 객체 자신이 가진 칸의 이름. 속성은 정의에서 가져온다.
FIXED_LABELS = {
    "label": "이름",
    "key": "식별자",
    "description": "설명",
    "status": "상태",
    "owner_workspace_id": "소유 부서",
    "valid_from_year": "시작 연도",
    "valid_to_year": "끝 연도",
}


def _changed_fields(one: events.ChangeEvent, labels: dict[str, str]) -> str:
    """무엇이 바뀌었나 — **사람이 읽는 칸 이름으로.**

    `properties.weight` 같은 내부 키를 그대로 내보내면 알림을 받은 사람은 그것이
    화면의 어느 칸인지 모른다 — 그러면 열어 보는 수밖에 없고, 알림이 하려던 일
    (열지 말지 정하기)이 안 된다.

    **값은 안 싣는다**: 알림은 못 보는 사람에게도 남고, 남의 부서 값이 거기 적히면
    그것은 샌 것이다.
    """
    shown: list[str] = []
    for key, change in (one.changes or {}).items():
        if key.startswith("_"):
            continue
        if key == "properties":
            # 속성은 통째로 한 칸에 담겨 온다(JSONB) — **무엇이 바뀌었는지는 그 안에
            # 있다.** 「속성이 바뀌었습니다」 로 뭉뚱그리면 열어 보는 수밖에 없다.
            shown.extend(_changed_properties(change, labels))
            continue
        shown.append(labels.get(key, FIXED_LABELS.get(key, key.split(".")[-1])))
    if not shown:
        return ""
    return ", ".join(shown[:3]) + (f" 외 {len(shown) - 3}개" if len(shown) > 3 else "")


def _changed_properties(change: object, labels: dict[str, str]) -> list[str]:
    """`{"before": {...}, "after": {...}}` 에서 **실제로 달라진 속성**의 이름만."""
    if not isinstance(change, dict):
        return []
    before = change.get("before") or {}
    after = change.get("after") or {}
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    keys = sorted(key for key in {*before, *after} if before.get(key) != after.get(key))
    return [labels.get(f"properties.{key}", key) for key in keys]


def _property_labels(db: Session, type_ids: set[uuid.UUID]) -> dict[uuid.UUID, dict[str, str]]:
    """타입마다 {`properties.<키>`: 사람이 읽는 이름}. 한 질의로 모은다."""
    out: dict[uuid.UUID, dict[str, str]] = {}
    if not type_ids:
        return out
    rows = db.scalars(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type", PropertyDef.owner_id.in_(type_ids)
        )
    )
    for row in rows:
        out.setdefault(row.owner_id, {})[f"properties.{row.key}"] = row.label
    return out


def _visible_to(
    db: Session, by_object: dict[uuid.UUID, list[uuid.UUID]]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """지켜보는 사람 → 그 사람이 **지금** 볼 수 있는 객체(이 알림에 걸린 것 중). 판정은 목록과
    같은 규칙(`visible_owner_clause`) — 사람마다 한 번 묻는다(지켜보는 사람은 몇 명이다)."""
    wanted: dict[uuid.UUID, set[uuid.UUID]] = {}
    for object_id, user_ids in by_object.items():
        for user_id in user_ids:
            wanted.setdefault(user_id, set()).add(object_id)
    people = {
        row.id: row
        for batch in chunks(list(wanted))
        for row in db.scalars(select(User).where(User.id.in_(batch)))
    }
    out: dict[uuid.UUID, set[uuid.UUID]] = {}
    for user_id, object_ids in wanted.items():
        viewer = people.get(user_id)
        if viewer is None:
            continue
        out[user_id] = {
            found
            for batch in chunks(sorted(object_ids))
            for found in db.scalars(
                select(ObjectInstance.id).where(
                    ObjectInstance.id.in_(batch),
                    visible_owner_clause(viewer, ObjectInstance.owner_workspace_id),
                )
            )
        }
    return out


@dataclass(frozen=True)
class _End:
    """선의 기록 하나를 **지켜볼 수 있는 한 끝**에서 본 것."""

    event: events.ChangeEvent
    watched: uuid.UUID
    other: uuid.UUID | None
    relation: str
    outgoing: bool
    """지켜보는 끝이 출발 쪽인가 — 도착 쪽이면 관계를 역방향의 말로 읽는다."""


def _uuid(raw: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


def _relation_ends(db: Session, linked: list[events.ChangeEvent]) -> list[_End]:
    """선의 기록마다 양 끝 — 기록이 실은 `src` · `dst`(`audit.relation_ends` 의 모양)에서.

    끝을 안 실은 기록(일괄 가져오기의 선 고침)은 선에서 읽는다. 끊은 선은 이미 없어 못 찾는다 —
    그런 기록은 알리지 않는다(끝을 짐작해 엉뚱한 사람에게 보내지 않는다)."""
    bare = [
        one.target_id
        for one in linked
        if one.target_table == "object_relations"
        and one.target_id is not None
        and not (_uuid(one.changes.get("src")) and _uuid(one.changes.get("dst")))
    ]
    edges = {
        row.id: row
        for batch in chunks(bare)
        for row in db.execute(
            select(
                ObjectRelation.id,
                ObjectRelation.src_object_id,
                ObjectRelation.dst_object_id,
                ObjectRelation.relation,
            ).where(ObjectRelation.id.in_(batch))
        )
    }
    out: list[_End] = []
    for one in linked:
        src, dst = _uuid(one.changes.get("src")), _uuid(one.changes.get("dst"))
        relation = str(one.changes.get("relation") or "")
        edge = edges.get(one.target_id) if one.target_id is not None else None
        if (src is None or dst is None) and edge is not None:
            src, dst, relation = edge.src_object_id, edge.dst_object_id, edge.relation
        if src is not None:
            out.append(_End(one, src, dst, relation, outgoing=True))
        if dst is not None and dst != src:
            out.append(_End(one, dst, src, relation, outgoing=False))
    return out


def _relation_detail(
    end: _End,
    kinds: dict[str, RelationType],
    objects: dict[uuid.UUID, ObjectInstance],
    visible: set[uuid.UUID],
) -> str:
    """「관계의 말: 다른 끝」 — **다른 끝은 받는 사람이 지금 볼 수 있을 때만 이름으로.**
    못 보면 「(볼 수 없는 객체)」. 원 표의 끝(부서 · 사람)은 여기서 볼 수 있는지 모르므로
    이름을 안 싣는다."""
    # 방향에 맞는 말 — 「관련 객체」 와 같은 규칙(`objects/routes.py`).
    kind = kinds.get(end.relation)
    if kind is None:
        word = end.relation
    elif end.outgoing or not kind.directed:
        word = kind.label
    else:
        word = kind.inverse_label or f"{kind.label}의 반대"
    other = objects.get(end.other) if end.other is not None else None
    if other is None:
        return word
    name = other.label if other.id in visible else HIDDEN_LABEL
    return f"{word}: {name}" if word else name


def _notify_relations(
    db: Session,
    ends: list[_End],
    by_object: dict[uuid.UUID, list[uuid.UUID]],
    objects: dict[uuid.UUID, ObjectInstance],
    types: dict[uuid.UUID, ObjectType],
    seen: dict[uuid.UUID, set[uuid.UUID]],
    gone: set[uuid.UUID],
) -> int:
    """선의 일을 끝마다 지켜보는 사람에게 — **(사람 · 객체 · 일)마다 한 통으로 묶는다.** 일괄
    입력 한 번이 허브 모델 하나에 선 수천 개를 이으면 그 모델을 지켜보는 사람에게 수천 통이
    간다 — 「관계가 추가됐습니다(3,000건)」 한 통이 같은 말을 한다."""
    groups: dict[tuple[uuid.UUID, uuid.UUID, str], list[_End]] = {}
    for end in ends:
        if end.watched in gone:
            continue
        for user_id in by_object.get(end.watched, []):
            # 내가 한 일은 나에게 안 알린다 · 지금 볼 수 있는 사람에게만(객체의 일과 같다).
            if end.event.actor_id and user_id == end.event.actor_id:
                continue
            if end.watched not in seen.get(user_id, set()):
                continue
            groups.setdefault((user_id, end.watched, end.event.action), []).append(end)
    if not groups:
        return 0
    kinds = {row.slug: row for row in db.scalars(select(RelationType))}
    sent = 0
    for (user_id, watched, action), found in groups.items():
        row = objects.get(watched)
        if row is None:  # pragma: no cover - 지켜보는 것은 객체뿐이다
            continue
        object_type = types.get(row.type_id)
        first = found[0]
        detail = _relation_detail(first, kinds, objects, seen.get(user_id, set()))
        many = len(found) > 1
        if many:
            detail = f"{detail} 외 {len(found) - 1:,}건"
        notifications.notify(
            db,
            user_id=user_id,
            kind=notifications.OBJECT_CHANGED,
            title=f"{row.label} — {LABELS[action]}" + (f"({len(found):,}건)" if many else ""),
            body=" · ".join(
                part
                for part in (
                    object_type.label if object_type else "",
                    first.event.actor_label,
                    detail,
                )
                if part
            ),
            link=f"/o/{object_type.slug}/{watched}" if object_type else None,
        )
        sent += 1
    return sent


def on_events(staged: list[events.ChangeEvent]) -> None:
    """커밋된 변경을 지켜보는 사람들에게. **던지지 않는다** — 알림이 실패했다고 방금
    성공한 저장이 실패로 보이면 안 된다(`shared/events.py` 가 삼키지만 여기서도 조심한다).
    """
    wanted = [
        one
        for one in staged
        if one.target_table == "objects" and one.action in WATCHED_ACTIONS and one.target_id
    ]
    linked = [
        one
        for one in staged
        if one.target_table in RELATION_TABLES and one.action in RELATION_ACTIONS
    ]
    if not wanted and not linked:
        return
    try:
        with SessionLocal() as db:
            ends = _relation_ends(db, linked) if linked else []
            ids = [one.target_id for one in wanted if one.target_id]
            by_object = watchers(db, [*ids, *(end.watched for end in ends)])
            if not by_object:
                return
            ends = [end for end in ends if end.watched in by_object]
            types = {row.id: row for row in db.scalars(select(ObjectType))}
            # 지켜보는 사람이 있는 것만 읽는다 — 변경이 수만 건이어도 그런 객체는 몇 개다. 선의
            # 다른 끝도(이름을 실을지 정하려고).
            others = {end.other for end in ends if end.other is not None}
            objects = {
                row.id: row
                for batch in chunks(list({*by_object, *others}))
                for row in db.scalars(
                    select(ObjectInstance).where(ObjectInstance.id.in_(batch))
                )
            }
            labels = _property_labels(
                db, {objects[one].type_id for one in by_object if one in objects}
            )
            # 다른 끝은 그 선을 지켜보는 사람의 눈으로 본다.
            looked = {key: list(value) for key, value in by_object.items()}
            for end in ends:
                if end.other is not None:
                    looked.setdefault(end.other, []).extend(by_object[end.watched])
            seen = _visible_to(db, looked)
            sent = 0
            for one in wanted:
                target = one.target_id
                if target is None:
                    continue
                for user_id in by_object.get(target, []):
                    # **내가 한 일은 나에게 안 알린다.** 자기 행동을 돌려받으면 그 종은
                    # 곧 잡음이 되고, 잡음이 된 종은 진짜 하나가 울려도 안 읽힌다.
                    if one.actor_id and user_id == one.actor_id:
                        continue
                    # **지금 볼 수 있는 사람에게만.** 지켜보기를 켠 뒤 부서가 바뀌어 못 보게
                    # 됐을 수 있다 — `/watching` 목록은 거르는데 알림은 객체 이름과 바뀐 칸을
                    # 그대로 실어 보냈다(2026-10-08).
                    if target not in seen.get(user_id, set()):
                        continue
                    row = objects.get(target)
                    object_type = types.get(row.type_id) if row else None
                    slug = object_type.slug if object_type else None
                    fields = _changed_fields(one, labels.get(row.type_id, {}) if row else {})
                    # **감사 기록의 이름표를 그대로 쓰지 않는다.** 그것은 `slug:이름`
                    # 이라 타입 슬러그가 알림 제목으로 새어 나온다 — 사람이 화면에서
                    # 본 적 없는 글자다.
                    name = row.label if row else one.target_label.split(":", 1)[-1]
                    where = object_type.label if object_type else ""
                    notifications.notify(
                        db,
                        user_id=user_id,
                        kind=notifications.OBJECT_CHANGED,
                        title=f"{name} — {LABELS.get(one.action, '변경됐습니다')}",
                        # 타입 · 누가 · 무엇이. 빈 조각은 뺀다 — 「부품 ·  · 」 는
                        # 정보가 아니라 잡음이다.
                        body=" · ".join(
                            part for part in (where, one.actor_label, fields) if part
                        ),
                        # 지워진 것은 열 수 없다 — 그때는 목록으로 보낸다.
                        link=(
                            f"/o/{slug}/{target}"
                            if slug and one.action != "object.delete"
                            else (f"/o/{slug}" if slug else None)
                        ),
                    )
                    sent += 1
            gone = {
                one.target_id
                for one in wanted
                if one.action in _SWALLOWS_RELATIONS and one.target_id is not None
            }
            sent += _notify_relations(db, ends, by_object, objects, types, seen, gone)
            if sent:
                db.commit()
    except Exception:  # pragma: no cover - 알림 실패가 저장을 되돌리면 안 된다
        logger.exception("지켜보기 알림을 보내지 못했습니다")
