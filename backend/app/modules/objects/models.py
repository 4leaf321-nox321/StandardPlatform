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


class ObjectLink(Base):
    """한쪽 끝이 `objects` 밖인 관계 — **`system` 객체(부서·계정·승격한 표)와 잇는 선.**

    `object_relations` 는 양끝이 `objects` 행이라 FK 로 묶인다. system 객체는 행이
    없으므로(원 표를 투영한다) 그 선은 여기 담는다. 끝은 **타입 slug + id** 로
    적는다 — 어느 끝이 원 표인지는 그 타입의 `kind_class` 가 말한다.

    FK 가 없는 대가: 객체가 진짜로 지워지는 날 이 선이 남는다. 객체는 `deleted_at`
    으로만 지우고, 지우기 전 확인(`lifecycle.references_of`)이 이 선도 세므로
    그 날은 사실상 오지 않는다.
    """

    __tablename__ = "object_links"
    __table_args__ = (
        UniqueConstraint("src_id", "dst_id", "relation", name="uq_object_links_edge"),
        Index("ix_object_links_src", "src_id", "relation"),
        Index("ix_object_links_dst", "dst_id", "relation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    src_type: Mapped[str] = mapped_column(String(SLUG_MAX))
    src_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    dst_type: Mapped[str] = mapped_column(String(SLUG_MAX))
    dst_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    relation: Mapped[str] = mapped_column(String(SLUG_MAX), index=True)
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    evidence_note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ObjectAlias(Base):
    """객체의 **다른 이름** — 사람이 붙인 별칭과, 바깥 시스템이 부르는 식별자.

    같은 것을 다르게 부르는 일은 늘 있다(「Ansys」 「ANSYS Inc.」 「앤시스」). 이름 풀이가
    식별자와 이름만 보면 표기가 하나라도 다른 순간 못 찾고, **못 찾은 사람은 새로 만든다**
    — 그러면 같은 것이 둘이 된다. 별칭을 두면 찾기·참조 풀이·파일·동기화가 전부 그것을
    본다. 합치기는 지는 쪽 이름을 이긴 쪽 별칭으로 남겨 **같은 중복이 다시 안 생기게** 한다.

    `kind`:
        alias            사람이 붙인 다른 이름
        source:<slug>    그 데이터 소스의 외부 식별자 — 동기화가 남기고, 다음 동기화가 이것으로
                         같은 객체를 다시 찾는다(우리 쪽 이름·식별자를 고쳐도 안 끊긴다)

    **같은 타입 안에서 (kind, 값) 은 하나뿐이다.** 「ANSYS」 가 두 객체의 별칭이면 어느
    쪽인지 아무도 모른다. 비교는 `norm`(전각·대소문자·공백을 모은 것)으로 한다.
    """

    __tablename__ = "object_aliases"
    __table_args__ = (
        UniqueConstraint("type_id", "kind", "norm", name="uq_object_aliases_value"),
        Index("ix_object_aliases_object", "object_id"),
        Index("ix_object_aliases_lookup", "type_id", "norm"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    object_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="CASCADE")
    )
    type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("object_types.id", ondelete="CASCADE")
    )
    """객체의 타입을 여기도 둔다 — 유일성이 타입 안에서 서기 때문이다."""
    kind: Mapped[str] = mapped_column(String(80), default="alias", server_default="alias")
    value: Mapped[str] = mapped_column(String(200))
    norm: Mapped[str] = mapped_column(String(200))
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


class SavedView(Base):
    """저장된 뷰 — **조건·정렬을 이름 붙여 둔다.**

    「영남 공급사 중 심사 점수 80 미만」 을 매번 다시 거르면 사람은 곧 안 거른다.
    `workspace_id` 가 있으면 그 부서가 함께 쓰고, 없으면 만든 사람 것이다. 전사 뷰는
    따로 없다 — 타입 정의의 `list_view` 가 그 자리다.
    """

    __tablename__ = "saved_views"
    __table_args__ = (Index("ix_saved_views_type_owner", "type_id", "owner_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("object_types.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    """있으면 그 부서가 함께 쓴다. NULL 은 만든 사람 것."""
    query: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """`{"q": ..., "conditions": [{"field","op","value"}], "sort": {...}}`."""
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """묶어 보기 설정 — `{"group_by", "metric", "metric_field", "chart"}`. 비어 있으면
    이 뷰는 목록일 뿐이다.

    뷰에 함께 담는 이유: 조건과 축은 **같은 물음의 두 쪽**이다(「영남 공급사를 등급별로」).
    따로 두면 뷰를 불러올 때마다 축을 다시 고르게 되고, 그 수고가 몇 번 반복되면
    사람은 그 화면을 CSV 로 내려받아 엑셀에서 본다."""

    home_order: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    """부서 홈에 올린 자리(0부터). NULL 이면 홈에 없다.

    **부서 뷰만 올릴 수 있다.** 개인 뷰를 부서 홈에 붙이면 같은 화면을 보는 사람마다
    다른 것이 뜨고, 그때 「내 홈에는 왜 그게 없지」 를 아무도 설명하지 못한다."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ObjectWatch(Base):
    """**이 객체가 바뀌면 나에게 알려 달라.**

    데이터를 함께 쓰는 플랫폼에서 가장 자주 나오는 물음은 「내가 보던 그게 아직 그대로
    인가」 다. 그것을 알 방법이 목록을 다시 여는 것뿐이면, 사람은 안 열고 옛 값을 들고
    회의에 들어간다.

    **만든 사람은 자동으로 지켜본다.** 스스로 켜야만 하는 기능은 켜는 법을 아는 사람만
    쓰게 되고, 그 사람은 대개 이미 알고 있는 사람이다.

    관계는 이 표가 안 담는다 — 「무엇을」 지켜보는지는 객체 하나로 충분하고, 관계가
    바뀌면 양 끝 객체의 감사 기록에 남는다.
    """

    __tablename__ = "object_watches"
    __table_args__ = (UniqueConstraint("object_id", "user_id", name="uq_object_watches_pair"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    object_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
