"""**보여 줄 이름**(`display_label`, ADR 0088) — 타입의 앞머리를 이름 앞에 붙인다.

문서의 절(「측정 방법」, 절 번호 「3.2」)을 객체로 넣으면 번호는 칸
(`properties.section_no`)에 두고 이름에는 넣지 않는다(ADR 0086). 그러면 트리 밖에서는 번호가
사라져, 검색 결과에 「개요」 가 열 개 나오면 어느 장의 개요인지 알 수 없었다. 타입의
`label_prefix` 로 정한 칸의 값을 이름 앞에 붙인 「3.2 측정 방법」 을 목록 · 상세 제목 · 참조
링크 · 관계 목록 · 검색 · 검토함 · 트리가 함께 쓴다.

- **이름(`label`)은 그대로다** — 저장 · 수정 창 · 이름으로 찾기 · 별칭 · 식별자 맞추기 ·
  내보내기 · 코어 API 는 `label` 을 쓴다. 이것은 보이는 말만 바꾼다.
- **앞머리를 쓰는 타입이 없으면 질의 하나로 끝난다**(작은 타입 표) — 어느 응답에 붙여도 비용이
  거의 없다. 있으면 그 타입의 줄만 읽는다.
- 값을 글자로 만드는 규칙은 트리와 같다(`treeview.prefixes` — 참조면 상대의 이름, 볼 수 없는
  상대는 싣지 않는다).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import treeview
from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import ObjectType
from app.shared.batches import chunks


def shown(label: str, prefix: str | None) -> str:
    """앞머리 + 이름 — 앞머리가 없으면 이름 그대로."""
    return f"{prefix} {label}" if prefix else label


def prefixed_types(db: Session) -> dict[uuid.UUID, ObjectType]:
    """앞머리를 쓰는 타입 — 대개 비어 있다(그러면 아무것도 더 읽지 않는다)."""
    return {
        one.id: one
        for one in db.scalars(
            select(ObjectType).where(
                or_(
                    ObjectType.label_prefix != "",
                    # 옛 자리(0.4.95) — 옮기기 전의 값이 남은 곳에서도 같은 이름이 보이게.
                    ObjectType.list_view["tree"].has_key("prefix"),
                )
            )
        )
    }


def prefixes(
    db: Session,
    user: User,
    rows: Sequence[ObjectInstance],
    types: dict[uuid.UUID, ObjectType] | None = None,
) -> dict[uuid.UUID, str]:
    """줄마다 앞머리 — 앞머리가 있는 줄만 싣는다. 줄마다 제 타입의 칸을 본다(트리 · 인터페이스
    목록 · 검색은 여러 타입이 섞인다)."""
    if not rows:
        return {}
    found = prefixed_types(db) if types is None else types
    if not found:
        return {}
    by_type: dict[uuid.UUID, list[ObjectInstance]] = defaultdict(list)
    for row in rows:
        if row.type_id in found:
            by_type[row.type_id].append(row)
    out: dict[uuid.UUID, str] = {}
    for type_id, mine in by_type.items():
        object_type = found[type_id]
        values = treeview.prefixes(
            db, user, object_type, treeview.prefix_field(object_type), mine
        )
        out.update({key: value for key, value in values.items() if value})
    return out


def labels(db: Session, user: User, rows: Sequence[ObjectInstance]) -> dict[uuid.UUID, str]:
    """줄마다 보여 줄 이름 — **앞머리가 붙는 줄만** 싣는다. 쓰는 쪽은
    `labels.get(row.id, row.label)`."""
    values = prefixes(db, user, rows)
    return {row.id: shown(row.label, values[row.id]) for row in rows if row.id in values}


def labels_for_ids(db: Session, user: User, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """id 만 든 자리(참조 칸 · 검색 · 검토함)의 보여 줄 이름 — 앞머리를 쓰는 타입의 줄만
    읽는다.

    **그 객체를 보여도 되는지는 부르는 쪽이 이미 정했다** — 여기서는 이름만 만든다(지워진 것도
    빼지 않는다: 부르는 쪽이 「(지워짐)」 을 붙인다)."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    types = prefixed_types(db)
    if not types:
        return {}
    rows: list[ObjectInstance] = []
    for part in chunks(wanted):
        rows.extend(
            db.scalars(
                select(ObjectInstance).where(
                    ObjectInstance.id.in_(part), ObjectInstance.type_id.in_(list(types))
                )
            )
        )
    values = prefixes(db, user, rows, types)
    return {row.id: shown(row.label, values[row.id]) for row in rows if row.id in values}
