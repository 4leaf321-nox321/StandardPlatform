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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

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
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    """지우지 않는다. 이 객체를 가리키는 관계와 첨부가 밖에 남아 있다."""
