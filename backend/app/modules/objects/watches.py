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

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.modules.notifications import services as notifications
from app.modules.objects.models import ObjectInstance, ObjectWatch
from app.modules.ontology.models import ObjectType
from app.shared import events

logger = logging.getLogger(__name__)

#: 알릴 만한 것 — 객체 자신이 바뀌는 일. 만들기는 뺀다(막 만든 사람만 지켜보고 있고,
#: 그 사람은 방금 자기가 만들었다).
WATCHED_ACTIONS = {
    "object.update",
    "object.delete",
    "object.restore",
    "object.merge",
    "object.relation.add",
    "object.relation.update",
    "object.relation.remove",
    "object.years.set",
}

#: 무엇이 바뀌었는지 한 줄로. 사람이 알림만 보고 열지 말지를 정한다.
LABELS = {
    "object.update": "값이 바뀌었습니다",
    "object.delete": "지워졌습니다",
    "object.restore": "되살아났습니다",
    "object.merge": "다른 것과 합쳐졌습니다",
    "object.relation.add": "관계가 생겼습니다",
    "object.relation.update": "관계가 바뀌었습니다",
    "object.relation.remove": "관계가 끊어졌습니다",
    "object.years.set": "연도가 바뀌었습니다",
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
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    if not object_ids:
        return out
    for row in db.scalars(select(ObjectWatch).where(ObjectWatch.object_id.in_(object_ids))):
        out.setdefault(row.object_id, []).append(row.user_id)
    return out


def counts(db: Session, object_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    return {key: len(value) for key, value in watchers(db, object_ids).items()}


def _changed_fields(one: events.ChangeEvent) -> str:
    """무엇이 바뀌었나 — 칸 이름 몇 개. **값은 안 싣는다**: 알림은 못 보는 사람에게도
    남고, 남의 부서 값이 거기 적히면 그것은 샌 것이다."""
    keys = [key for key in (one.changes or {}) if not key.startswith("_")]
    if not keys:
        return ""
    shown = ", ".join(keys[:3])
    return f"{shown} 외 {len(keys) - 3}개" if len(keys) > 3 else shown


def on_events(staged: list[events.ChangeEvent]) -> None:
    """커밋된 변경을 지켜보는 사람들에게. **던지지 않는다** — 알림이 실패했다고 방금
    성공한 저장이 실패로 보이면 안 된다(`shared/events.py` 가 삼키지만 여기서도 조심한다).
    """
    wanted = [
        one
        for one in staged
        if one.target_table == "objects" and one.action in WATCHED_ACTIONS and one.target_id
    ]
    if not wanted:
        return
    try:
        with SessionLocal() as db:
            ids = [one.target_id for one in wanted if one.target_id]
            by_object = watchers(db, ids)
            if not by_object:
                return
            types = {row.id: row.slug for row in db.scalars(select(ObjectType))}
            objects = {
                row.id: row
                for row in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(ids)))
            }
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
                    row = objects.get(target)
                    slug = types.get(row.type_id) if row else None
                    fields = _changed_fields(one)
                    notifications.notify(
                        db,
                        user_id=user_id,
                        kind=notifications.OBJECT_CHANGED,
                        title=f"{one.target_label} — {LABELS.get(one.action, '바뀌었습니다')}",
                        body=f"{one.actor_label}{f' · {fields}' if fields else ''}",
                        # 지워진 것은 열 수 없다 — 그때는 목록으로 보낸다.
                        link=(
                            f"/o/{slug}/{target}"
                            if slug and one.action != "object.delete"
                            else (f"/o/{slug}" if slug else None)
                        ),
                    )
                    sent += 1
            if sent:
                db.commit()
    except Exception:  # pragma: no cover - 알림 실패가 저장을 되돌리면 안 된다
        logger.exception("지켜보기 알림을 보내지 못했습니다")
