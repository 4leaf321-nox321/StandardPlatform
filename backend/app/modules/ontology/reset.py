"""온톨로지 통째로 비우기 — **되돌릴 수 없는 일.**

정의를 시험 삼아 몇 번 세워 보는 동안에는 타입이 금세 열댓 개가 되고, 그것을 하나씩
지우는 일은 순서까지 맞춰야 해서(객체 → 관계 → 타입) 사람이 포기한다. 그래서 한 번에
비우는 길을 낸다.

## 그래서 무섭다 — 세 겹으로 막는다

1. **계획 먼저.** 무엇이 몇 건 사라지는지 세어 보여 준다. 「정말 삭제하시겠습니까」 만
   묻는 창은 아무도 안 읽고 예를 누른다 — 읽을 것이 없기 때문이다.
2. **적어서 확인.** 계획에 뜬 문구를 손으로 쳐야 넘어간다. 실수로 누르는 것과 작정하고
   하는 것 사이에 글자 몇 개를 둔다.
3. **정의는 스냅샷으로 남는다.** 비우기 직전의 정의가 이력에 남아 되돌릴 수 있다.
   **데이터는 안 돌아온다** — 그 비대칭을 화면이 분명히 말해야 한다.

## 무엇을 지우나

정의(묶음·타입·속성·관계 종류)와 **그 정의에 매달린 것 전부**(객체·관계·링크·별칭·
연도·저장된 뷰·데이터 소스). 부서·계정·공지·웹훅은 안 건드린다 — 그것들은 온톨로지가
아니라 이 설치 자체의 것이다.

첨부는 **행만 지운다**(`files` 모듈의 규칙 그대로). 같은 내용을 다른 행이 가리킬 수
있어서, 파일까지 지우면 그쪽이 가리킬 곳을 잃는다.

## 도메인이 자기 표를 더했다면

`object_types` 를 RESTRICT 로 가리키는 표가 새로 생기면 여기서 막힌다. **조용히 넘어가지
않고** 어느 표가 막았는지 말한다 — 그때 이 파일에 한 줄을 더하면 된다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.datasources.models import DataSource
from app.modules.files.models import Attachment
from app.modules.objects.models import (
    ObjectAlias,
    ObjectInstance,
    ObjectLink,
    ObjectRelation,
    ObjectYear,
    SavedView,
)
from app.modules.ontology.models import NavGroup, ObjectType, PropertyDef, RelationType
from app.shared.errors import AppError, Conflict, code

#: 확인 문구 — **계획에 뜬 그대로 쳐야 한다.** 실수로 누르는 것과 작정하고 하는 것
#: 사이에 글자 몇 개를 둔다.
CONFIRM_PHRASE = "온톨로지 초기화"


@dataclass
class ResetItem:
    table: str
    label: str
    count: int


@dataclass
class ResetPlan:
    items: list[ResetItem] = field(default_factory=list)
    applied: bool = False
    snapshot_id: uuid.UUID | None = None
    confirm_phrase: str = CONFIRM_PHRASE

    @property
    def total(self) -> int:
        return sum(one.count for one in self.items)


def _count(db: Session, model: type, *where: object) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)  # type: ignore[arg-type]
    return int(db.scalar(stmt) or 0)


def plan(db: Session) -> ResetPlan:
    """무엇이 몇 건 사라지나. **0 건도 내보낸다** — 여기서는 「없다」 는 답도 정보다."""
    return ResetPlan(
        items=[
            ResetItem("object_types", "타입", _count(db, ObjectType)),
            ResetItem("property_defs", "속성 정의", _count(db, PropertyDef)),
            ResetItem("relation_types", "관계 종류", _count(db, RelationType)),
            ResetItem("nav_groups", "사이드바 묶음", _count(db, NavGroup)),
            # 지운 객체(`deleted_at`)도 센다 — 행이 남아 있으면 FK 는 그대로 붙든다.
            ResetItem("objects", "객체(지운 것 포함)", _count(db, ObjectInstance)),
            ResetItem("object_relations", "관계", _count(db, ObjectRelation)),
            ResetItem("object_links", "원 표와 이은 선", _count(db, ObjectLink)),
            ResetItem("object_aliases", "별칭", _count(db, ObjectAlias)),
            ResetItem("object_years", "연도 배정", _count(db, ObjectYear)),
            ResetItem("saved_views", "저장된 뷰", _count(db, SavedView)),
            ResetItem("data_sources", "데이터 소스", _count(db, DataSource)),
            ResetItem(
                "attachments",
                "객체에 붙은 첨부",
                _count(db, Attachment, Attachment.owner_table == "objects"),
            ),
        ]
    )


def apply(db: Session, *, confirm: str) -> ResetPlan:
    """비운다. **한 트랜잭션** — 절반만 지워진 온톨로지는 어느 절반인지 알 수 없다.

    스냅샷은 부르는 쪽이 먼저 남긴다(라우터가 한다). 여기서는 지우기만 한다.
    """
    if confirm.strip() != CONFIRM_PHRASE:
        raise Conflict(
            code("ONTOLOGY", 80),
            f"확인 문구가 다릅니다. 「{CONFIRM_PHRASE}」 를 그대로 적어야 합니다.",
        )
    found = plan(db)

    # 순서가 곧 안전이다 — 가리키는 쪽부터 지운다. CASCADE 가 대신 해 줄 것도 여기서
    # 명시적으로 지운다: 어느 표가 얼마나 지워졌는지 계획과 맞아야 사람이 믿는다.
    try:
        db.execute(delete(Attachment).where(Attachment.owner_table == "objects"))
        db.execute(delete(DataSource))
        db.execute(delete(SavedView))
        db.execute(delete(ObjectYear))
        db.execute(delete(ObjectAlias))
        db.execute(delete(ObjectLink))
        db.execute(delete(ObjectRelation))
        # merged_into_id 가 objects 를 가리킨다(SET NULL) — 통째로 지우므로 순서 문제는
        # 없지만, 남은 참조가 있으면 여기서 걸린다.
        db.execute(delete(ObjectInstance))
        db.execute(delete(PropertyDef))
        db.execute(delete(RelationType))
        db.execute(delete(ObjectType))
        db.execute(delete(NavGroup))
        db.flush()
    except IntegrityError as caught:
        db.rollback()
        # **어느 표가 막았는지 말한다.** 도메인이 `object_types` 를 RESTRICT 로 가리키는
        # 표를 더했으면 여기서 걸리고, 그때 이 파일에 한 줄을 더하면 된다.
        raise AppError(
            code("ONTOLOGY", 81),
            "가리키는 것이 남아 있어 비우지 못했습니다. 새로 더한 표가 타입이나 객체를 "
            f"가리키고 있는지 확인하세요: {str(caught.orig)[:300]}",
            status=409,
        ) from None

    found.applied = True
    return found
