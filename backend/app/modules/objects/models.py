"""객체 — 타입이 정의한 모양대로 쌓이는 실제 데이터.

메타모델(`ontology`)이 「무엇이 있을 수 있나」 를 정하고, 여기가 「실제로 무엇이
있나」 를 담는다. 배경은 [ADR 0005](../../../../docs/adr/0005-온톨로지-메타모델.md).

## 객체는 부서가 소유한다

타입은 전역이지만 객체는 `owner_workspace_id` 를 갖는다(NULL = 전역).
그러면 `visible_owner_clause` · `resolve_owner_workspace` · `require_owner_edit`
가 그대로 붙는다 — 이 틀이 그렇게 만들어져 있다.

## 트리는 관계로 만든다

`parent_id` 칸이 여기 **없는 것이 의도다.** 트리는 `transitive` + `acyclic` 인
관계(`part_of` 같은)로 만든다. 칸을 두면 트리 관계와 일반 관계가 두 벌이 되고,
트래버설 코드도 두 벌이 된다 — 그리고 두 벌은 반드시 갈린다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.modules.ontology.models import SLUG_MAX

#: 객체의 상태.
#:   active      picker 와 목록에 나온다
#:   deprecated  picker 에서 숨되 **이미 걸린 관계와 값은 그대로 남는다**
#:
#: 지우지 않는 이유는 이 틀의 다른 표와 같다 — 그것으로 만든 자료가 밖에 남아
#: 있고, 몇 년 뒤에도 그것이 무엇이었는지는 물어질 수 있다.
OBJECT_STATUSES = ("active", "deprecated")


class ObjectInstance(Base):
    """타입 하나에 속하는 객체 하나."""

    __tablename__ = "objects"
    __table_args__ = (
        # 목록은 거의 항상 「이 타입의 것」 으로 시작한다.
        Index("ix_objects_type_status", "type_id", "status"),
        # **JSONB 를 거르는 목록이 이 인덱스를 탄다.** 없으면 행이 몇만을 넘는
        # 순간 목록이 느려지고, 느려진 이유는 화면 어디에도 안 적힌다.
        Index("ix_objects_properties", "properties", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("object_types.id", ondelete="RESTRICT"),
        index=True,
    )
    """**RESTRICT 다.** 타입을 지우면 그 객체가 통째로 사라지는데, 그것은
    화면의 실수 한 번으로 일어날 일이 아니다. 지우기 전에 무엇이 걸렸는지
    보여 주는 자리가 먼저 있어야 한다."""

    key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    """사람이 정하는 식별자(부품번호·과제코드). 타입의 `key_policy` 가 필수인지
    정하고 `key_scope` 가 유니크 범위를 정한다.

    **유니크를 DB 제약으로 걸지 않는다** — 범위가 타입마다 다르기(전사/부서)
    때문이다. 서비스 레이어가 지키고, 아래 부분 인덱스가 전사 범위를 돕는다."""

    label: Mapped[str] = mapped_column(String(200), index=True)
    """화면에 보이는 이름. `key` 가 없는 축은 이것만으로 산다."""

    description: Mapped[str] = mapped_column(Text, default="", server_default="")

    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    """타입이 정의한 속성의 값들.

    **유연함을 이 한 칸에 가둔다.** 나머지는 정규화된 칼럼이다 — 순수 EAV
    (`entity, attribute, value` 세 칸)로 가면 값의 타입이 행마다 달라져 정렬·집계·
    조인이 전부 캐스팅이 되고, 목록 하나에 self-join 이 속성 수만큼 붙는다.

    대가는 분명하다: 여기에는 유니크·FK·CHECK 를 못 건다. 그래서 검증이
    `services.validate_properties()` 한 곳에 있고, 그 대가를 감당 못 할 타입은
    전용 표로 **승격**한다(설계 문서 §9 4단계)."""

    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")

    owner_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    """어느 부서의 것인가. **NULL 은 전역**이고, 여러 부서가 함께 쓰므로 고치는
    것은 시스템 관리자뿐이다."""

    valid_from_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_to_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """`temporal_kind='lifecycle'` 인 축의 유효 구간. NULL 끝 = 진행 중.

    **동작은 2단계에서 붙지만 칸은 지금 둔다.** 나중에 더하면 그때는 모든 타입에
    데이터가 쌓여 있고, 그 상태에서 뒤로 채우는 일은 아무도 정확히 못 한다."""

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="SET NULL"), nullable=True
    )
    """다른 객체에 **합쳐져서** 지워졌으면 그 객체. 옛 링크가 이것을 따라 새 것으로 간다 —
    안 남기면 「ACME」 와 「ACME Inc.」 를 합친 뒤 옛 주소가 전부 404 가 된다."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    """지우지 않는다. 이 객체를 가리키는 관계와 첨부가 밖에 남아 있다."""


class ObjectRelation(Base):
    """객체와 객체를 잇는 엣지 하나.

    **관계 종류(`relation_types`)에 FK 를 걸지 않는다.** 종류의 추가·삭제가
    마이그레이션이 아니라 설정 변경이어야 하기 때문이다 — 도메인을 데이터로
    정의한다는 이 설계의 전제가 그것이다. 정합은 서비스 레이어가 지킨다.
    """

    __tablename__ = "object_relations"
    __table_args__ = (
        # 같은 것을 두 번 맺지 않는다. **막지 않으면 「관련 객체」 에 같은 줄이
        # 둘 서고, 사람은 그것을 데이터가 이상한 것으로 읽는다.**
        UniqueConstraint(
            "src_object_id", "dst_object_id", "relation", name="uq_object_relations_edge"
        ),
        # 양방향으로 훑는다 — 「이것이 가리키는 것」 과 「이것을 가리키는 것」.
        Index("ix_object_relations_src", "src_object_id", "relation"),
        Index("ix_object_relations_dst", "dst_object_id", "relation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    src_object_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="CASCADE"), index=True
    )
    dst_object_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="CASCADE"), index=True
    )
    """**CASCADE 다.** 객체는 `deleted_at` 으로만 지우므로 이 길로 사라지는 일은
    거의 없다. 정말 행을 지우는 날(초기화·정리)에는 엣지가 남아 **가리키는 것이
    없는 관계**가 되는데, 그것은 화면에서 빈 줄로만 드러난다."""

    relation: Mapped[str] = mapped_column(String(SLUG_MAX), index=True)
    """관계 종류의 slug. 그 종류가 지워져도 이 값은 남는다 — 그래서 종류를 지울 때
    맺힌 관계가 있으면 막는다."""

    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    """관계 자체의 속성. `property_defs.owner_kind='relation'` 이 모양을 정한다 —
    **타입의 속성 폼과 같은 컴포넌트를 쓴다.**"""

    evidence_note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    """**왜 이렇게 이었는가.** 근거 없는 연결은 시간이 지나면 아무도 못 믿는다 —
    맞는지 확인하려면 처음부터 다시 조사해야 하기 때문이다."""

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ObjectYear(Base):
    """`temporal_kind='yearly'` 인 축의 **연도 배정.**

    모델명처럼 **연도가 불연속인** 값을 위한 표다 — 2024·2026 에는 쓰고 2025 에는
    안 쓰는 일이 실제로 있다. 구간(`valid_from_year`~`valid_to_year`)으로는 그것을
    표현할 수 없다.
    """

    __tablename__ = "object_years"
    __table_args__ = (
        UniqueConstraint("object_id", "year", name="uq_object_years_object_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    object_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="CASCADE"), index=True
    )
    year: Mapped[int] = mapped_column(Integer, index=True)
