"""온톨로지 메타모델 — **도메인을 코드가 아니라 데이터로 정의한다.**

    그룹을 정의하면      -> 사이드바에 묶음이 생기고
    타입을 정의하면      -> 그 묶음 안에 목록·프로필 화면이 생기고
    속성을 정의하면      -> 그 화면의 폼이 생긴다

배경과 결정은 [ADR 0005](../../../../docs/adr/0005-온톨로지-메타모델.md), 절차는
[설계 문서](../../../../docs/온톨로지-메타모델-설계.md) 가 정본이다.

## 여기 있는 셋

    NavGroup      표시 — 사이드바 묶음
    ObjectType    의미 — 인스턴스의 종류(= 축)
    PropertyDef   속성 정의 — 타입에도 붙고 **관계 종류에도** 붙는다

**표시와 의미를 나눈 이유**: 한 표로 두면 「이 타입을 두 그룹에」 「그룹을 부서마다
다르게」 가 반드시 오고, 그때는 데이터가 이미 쌓여 있다.

## 타입은 전역이다

`owner_workspace_id` 를 두지 않는다. 타입까지 부서별로 열면 같은 개념이 부서마다
갈리고, **연결하려고 만든 것이 칸막이가 된다.** 인스턴스만 부서가 소유한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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

#: 사이드바 항목이 누구에게 보이는가. **표시일 뿐 권한이 아니다** — 권한은 서버가
#: 판정한다. 사이드바를 고쳐 우회할 수 있으면 그건 애초에 보안이 아니다.
NAV_AUDIENCES = ("everyone", "manager", "system_admin")

#: 타입의 **객체 분류.**
#:
#:   reference  어휘/enum. 속성 거의 없음, picker 선택용 (불량 종류·개발 단계)
#:   record     속성을 가진 인스턴스 객체 (부품·과제·공급사)
#:   system     이미 있는 1급 표의 **투영** (부서·계정·공지)
#:
#: **system 이 핵심이다.** 부서를 온톨로지에 넣겠다고 `objects` 에 행을 복제하면
#: 두 벌이 되고 **반드시 갈린다** — 한쪽에서 이름을 바꾸면 다른 쪽은 옛 이름으로
#: 남고, 그 어긋남은 아무 데도 안 뜬다. 행을 만들지 않고 원 표에서 라벨을 읽는다.
KIND_CLASSES = ("reference", "record", "system")

#: 값을 누가 더할 수 있는가.
#:   open    picker 에서 즉석 추가 — 어휘 축이 자라는 자연스러운 길
#:   closed  관리자가 등록한 값만. 정형 마스터(부품번호·BOM)를 잠근다
ENTRY_POLICIES = ("open", "closed")

#: 인스턴스 식별자(`key`) 정책. 사람이 정하는 값(부품번호·과제코드)이다.
#:   none      쓰지 않는다. label 만으로 산다 (어휘 축)
#:   optional  있으면 유니크, 없어도 된다
#:   required  반드시 있고 유니크
KEY_POLICIES = ("none", "optional", "required")

#: `key` 의 유니크 범위.
#:
#: **외부 시스템과 맞물리는 축은 global 이어야 한다.** 부서마다 같은 부품번호가
#: 다른 것을 가리키면, 그 데이터로는 아무 질문에도 답할 수 없다.
KEY_SCOPES = ("global", "workspace")

#: 축의 **시간 차원** 정책. 연도 필터(`year=Y`)가 걸렸을 때 무엇이 보이는가.
#:
#:   evergreen  연도 무관, 항상 보인다. 필터를 무시한다 (기본값)
#:   lifecycle  valid_from_year ~ valid_to_year 에 Y 가 들면. NULL 끝 = 진행 중
#:   yearly     object_years 에 Y 가 배정돼 있으면 (불연속 가능)
#:   derived    **도메인이 답한다** — extensions.register_temporal_source
#:
#: **칼럼만 잡고 미루지 않는다.** 미루면 나중에 모든 타입을 건드리게 되고, 그때는
#: 데이터가 이미 쌓여 있다.
TEMPORAL_KINDS = ("evergreen", "lifecycle", "yearly", "derived")

#: 속성의 값 종류 일곱.
#:
#: `file` 은 첨부 모듈을 재사용한다(`attachments.owner_field`). JSONB 에 첨부 id 를
#: 넣지 않는 이유는, 첨부를 지우면 **유령 id 가 남고 그것은 깨진 링크로만**
#: 드러나기 때문이다.
DATA_TYPES = ("text", "number", "date", "bool", "enum", "object_ref", "file")

#: 속성 정의가 무엇에 붙는가. **타입의 폼과 관계의 폼이 같은 컴포넌트여야 한다** —
#: 두 벌로 만들면 위젯이 갈리고, 갈린 것은 한쪽만 고쳐진다.
PROPERTY_OWNER_KINDS = ("type", "relation")

#: slug 는 **바뀌면 안 되는 식별자**다. 관계·URL·MCP 도구 이름이 여기 물린다.
SLUG_MAX = 32


class NavGroup(Base):
    """사이드바 묶음 — **표시**.

    정적 메뉴(`navigation.ts`)를 대체하지 않는다. 그 아래 합류한다 — 정적 화면의
    정본은 여전히 파일이고, 동적 화면의 정본이 이 표다.
    """

    __tablename__ = "nav_groups"
    __table_args__ = (UniqueConstraint("slug", name="uq_nav_groups_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(SLUG_MAX))
    label: Mapped[str] = mapped_column(String(64))
    icon: Mapped[str] = mapped_column(String(40), default="", server_default="")
    """lucide 아이콘 이름. 모르는 이름이면 화면이 기본 아이콘으로 떨어진다 —
    **메뉴가 안 뜨는 것보다 낫다.**"""

    audience: Mapped[str] = mapped_column(
        String(20), default="everyone", server_default="everyone"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    """지우지 않는다. 이 그룹에 걸린 타입들이 밖에 남아 있다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ObjectType(Base):
    """인스턴스의 종류(= 축) — **의미**."""

    __tablename__ = "object_types"
    __table_args__ = (UniqueConstraint("slug", name="uq_object_types_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(SLUG_MAX))
    """**바뀌면 안 된다.** URL(`/o/<slug>`)·관계의 허용 타입·MCP 도구 이름이 이
    값에 물린다. 바꾸면 그 셋이 조용히 어긋난다."""

    label: Mapped[str] = mapped_column(String(64))
    icon: Mapped[str] = mapped_column(String(40), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    nav_group_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("nav_groups.id", ondelete="SET NULL"), nullable=True
    )
    """NULL 이면 사이드바에 안 선다. **어휘 축은 대개 그렇다** — 그 자체로 여는
    화면이 아니라 다른 타입의 picker 에서 쓰인다."""

    kind_class: Mapped[str] = mapped_column(
        String(20), default="record", server_default="record"
    )
    entry_policy: Mapped[str] = mapped_column(
        String(20), default="open", server_default="open"
    )
    key_policy: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    key_scope: Mapped[str] = mapped_column(
        String(20), default="global", server_default="global"
    )
    temporal_kind: Mapped[str] = mapped_column(
        String(20), default="evergreen", server_default="evergreen"
    )

    list_view: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """목록 화면의 모양 — 열·정렬·검색·필터.

    **없으면 모든 목록이 똑같아지고, 똑같으면 아무도 안 쓴다.** 부품 목록과 과제
    목록은 보여야 할 열이 다르다. 비어 있으면 화면이 기본형(key·label·수정시각)
    으로 떨어진다 — 빈 화면이 되지는 않는다.
    """

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PropertyDef(Base):
    """속성 정의 — **폴리모픽**. 타입에도 붙고 관계 종류에도 붙는다.

    `owner_kind='relation'` 은 2단계에서 쓰인다. 칸을 지금 두는 이유는, 나중에
    더하면 **이미 쌓인 정의를 옮겨야** 하기 때문이다.
    """

    __tablename__ = "property_defs"
    __table_args__ = (
        UniqueConstraint("owner_kind", "owner_id", "key", name="uq_property_defs_owner_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_kind: Mapped[str] = mapped_column(String(20), default="type", server_default="type")
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    """FK 를 걸지 않는다 — 가리키는 표가 둘(object_types · relation_types)이라
    걸 수 없다. 정합은 서비스 레이어가 지킨다."""

    key: Mapped[str] = mapped_column(String(48))
    """`properties` JSONB 의 키. **바뀌면 안 된다** — 바꾸면 이미 저장된 값이
    전부 고아가 되고, 화면은 그 값을 안 보여 줄 뿐 아무 말도 안 한다."""

    label: Mapped[str] = mapped_column(String(64))
    data_type: Mapped[str] = mapped_column(String(20))
    unit: Mapped[str] = mapped_column(String(24), default="", server_default="")
    help: Mapped[str] = mapped_column(String(200), default="", server_default="")

    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    multi: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    enum_options: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    """`data_type='enum'` 일 때의 고를 것들."""

    ref_type_slug: Mapped[str | None] = mapped_column(String(SLUG_MAX), nullable=True)
    """`data_type='object_ref'` 일 때 가리키는 타입. NULL 이면 아무 타입이나."""

    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
