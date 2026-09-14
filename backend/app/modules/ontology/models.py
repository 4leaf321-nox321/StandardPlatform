"""온톨로지 메타모델 — **도메인을 코드가 아니라 데이터로 정의한다.**

    그룹을 정의하면      -> 사이드바에 묶음이 생기고
    타입을 정의하면      -> 그 묶음 안에 목록·프로필 화면이 생기고
    속성을 정의하면      -> 그 화면의 폼이 생긴다

배경과 결정은 [ADR 0005](../../../../docs/adr/0005-온톨로지-메타모델.md), 절차는
[설계 문서](../../../../docs/온톨로지-메타모델-설계.md) 가 정본이다.

## 여기 있는 셋

    NavGroup      표시 — 사이드바 묶음
    ObjectType    의미 — 객체의 종류(= 축)
    PropertyDef   속성 정의 — 타입에도 붙고 **관계 종류에도** 붙는다

**표시와 의미를 나눈 이유**: 한 표로 두면 「이 타입을 두 그룹에」 「그룹을 부서마다
다르게」 가 반드시 오고, 그때는 데이터가 이미 쌓여 있다.

## 타입은 전역이다

`owner_workspace_id` 를 두지 않는다. 타입까지 부서별로 열면 같은 개념이 부서마다
갈리고, **연결하려고 만든 것이 칸막이가 된다.** 객체만 부서가 소유한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
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
#:   record     속성을 가진 객체 객체 (부품·과제·공급사)
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

#: 객체 식별자(`key`) 정책. 사람이 정하는 값(부품번호·과제코드)이다.
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
#:
#: `text_long` 과 `text` 를 가르는 이유는 **위젯이 다르기 때문**이다 — 여러 줄
#: 글을 한 줄 칸에 넣으면 사람은 자기가 쓴 것을 못 본다. `url` 은 링크로 뜬다.
#: `datetime` 은 날짜만으로 시각을 못 담는 자리(측정·기록 시각)에 쓴다.
DATA_TYPES = (
    "text",
    "text_long",
    "number",
    "date",
    "datetime",
    "bool",
    "enum",
    "url",
    "object_ref",
    "file",
)

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
    """객체의 종류(= 축) — **의미**."""

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
    system_source: Mapped[str] = mapped_column(String(40), default="", server_default="")
    """`kind_class='system'` 일 때 **어느 원 표를 비추는가** — `workspace` · `user`,
    또는 승격한 전용 표가 `shared/system_sources.py` 에 등록한 키. 행을 복제하지
    않으므로 이 타입에는 `objects` 행이 없다. system 이 아니면 빈 문자열."""
    entry_policy: Mapped[str] = mapped_column(
        String(20), default="open", server_default="open"
    )
    managed_by: Mapped[str] = mapped_column(String(40), default="", server_default="")
    """**누가 이 정의와 그 객체를 관리하나** — 빈 값이면 이 설치, `hub` 면 허브. 허브 것은
    묶음 가져오기에 같은 `source` 를 적어서만 바뀐다(`ontology/managed.py`). 가져오기가
    채우고 사람이 고치지 않는다 — 그래서 정의 가져오기의 칸이 아니다."""
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

    form_view: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """만들기·고치기 폼의 모양 — 섹션·순서·열 수·접힘.

    **동작은 3단계지만 칸은 지금 둔다.** 속성이 40개인 타입에서 폼이 일렬로 서면
    사람은 그 폼을 안 채운다. 비어 있으면 화면이 속성 순서대로 그린다."""

    detail_view: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    """객체 상세의 배치. **폼과 같은 `section` 을 쓴다** — 두 벌로 두면 갈리고,
    갈린 것은 한쪽만 고쳐진다."""

    title_template: Mapped[str] = mapped_column(String(200), default="", server_default="")
    """이름을 속성에서 조합한다 — `"{model} {size}"`.

    기계가 객체를 대량으로 만들 때 `label` 을 매번 정하는 것보다 **타입이 규칙을
    갖는 편이 낫다.** 비어 있으면 사람이 적은 `label` 을 그대로 쓴다."""

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

    inverse_label: Mapped[str] = mapped_column(String(64), default="", server_default="")
    """`object_ref` 의 **역방향 이름** — 「과제」 칸을 과제 쪽에서 읽으면 「개발모델」. 참조
    칸은 칸에 저장한 많대일 관계라, 관계 종류의 `inverse_label` 과 같은 자리다
    (`objects/refedges.py`). 비어 있으면 화면이 「<타입 이름>」 으로 대신 말한다."""

    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    """`number` 의 아래·위 끝. **없으면 두께가 -5mm 여도 통과한다** — 그 값은
    나중에 집계에 섞여 들어가 어디서 온 것인지 아무도 못 찾는다."""

    decimals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """소수 자릿수. 넘으면 거절한다 — **반올림해서 저장하지 않는다.** 조용히
    바꾸면 사람이 넣은 값과 저장된 값이 달라지고, 그 차이는 아무 데도 안 뜬다."""

    pattern: Mapped[str | None] = mapped_column(String(200), nullable=True)
    """`text` 계열의 정규식. 사번·도번처럼 **모양이 정해진 값**에 쓴다."""

    default_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    """안 채웠을 때 들어가는 값. **기계가 대량으로 만들 때** 특히 쓸모 있다."""

    unique: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    """이 값이 유일해야 하는가. 범위는 타입의 `key_scope` 를 따른다 —
    **두 벌의 규칙을 만들지 않는다.**

    DB 유니크로 못 거는 이유는 `key` 와 같다: 범위가 타입마다 다르고, 값이 JSONB
    안에 있다. 그래서 서비스 레이어가 지킨다."""

    section: Mapped[str] = mapped_column(String(48), default="", server_default="")
    """속성 묶음의 이름 — 「치수」·「재질」·「이력」. 폼과 상세가 **함께 쓴다.**

    **동작은 3단계지만 칸은 지금 둔다.** 나중에 넣으면 이미 정의된 속성 전부를
    다시 분류해야 하고, 그 일은 아무도 정확히 못 한다."""

    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


#: 관계의 **개수 제약.** 한쪽 끝에 몇 개가 붙을 수 있는가.
#:
#:   one_to_one    둘 다 하나씩
#:   one_to_many   출발 하나 -> 도착 여럿
#:   many_to_one   출발 여럿 -> 도착 하나 (부품 -> 공급사)
#:   many_to_many  제약 없음
#:
#: **없으면 「한 부품의 공급사는 하나」 를 표현할 방법이 없고**, 데이터가 조용히
#: 여러 개를 갖는다. 나중에 넣으면 이미 어긴 데이터가 쌓여 있어 **켤 수가 없다** —
#: 그래서 처음부터 둔다.
CARDINALITIES = ("one_to_one", "one_to_many", "many_to_one", "many_to_many")


class RelationType(Base):
    """관계의 종류 — **엣지의 의미를 데이터로 정의한다.**

    코드에 암묵이면 화면도 MCP 도 그 의미를 알 방법이 없다. ReportArchive 가
    `p55` 에서 뒤늦게 깨달은 자리다: *「관계 타입의 의미가 코드에 암묵 — AI 가
    스키마를 인지하거나 질의를 생성할 근거가 없음」*.
    """

    __tablename__ = "relation_types"
    __table_args__ = (UniqueConstraint("slug", name="uq_relation_types_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(SLUG_MAX))
    """**바뀌면 안 된다.** 이미 맺힌 관계가 이 값을 문자열로 들고 있다."""

    label: Mapped[str] = mapped_column(String(64))
    inverse_label: Mapped[str] = mapped_column(String(64), default="", server_default="")
    """역방향의 말. `part_of` <-> 「포함」. **화면이 양쪽에서 읽히게 하려면 필요하다** —
    없으면 도착 쪽 객체의 「관련 객체」 에 「part_of 의 반대」 라고 적힐 수밖에 없다."""

    description: Mapped[str] = mapped_column(Text, default="", server_default="")

    directed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    """방향이 있나. 없으면(`false`) 양쪽이 같은 말로 읽힌다(「비슷함」)."""

    transitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    """**재귀 펼침 대상인가.** 트리와 롤업이 이것으로 갈린다 — `part_of` 는 참이고
    「시험함」 은 거짓이다(시험의 시험은 시험이 아니다)."""

    acyclic: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    """순환 금지. 참이면 맺을 때 가드가 붙는다 — **자기 조상을 자식으로 넣는 순간
    트리 렌더가 무한히 돈다.**"""

    cardinality: Mapped[str] = mapped_column(
        String(20), default="many_to_many", server_default="many_to_many"
    )

    src_type_slugs: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    dst_type_slugs: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    """허용 출발/도착 타입. NULL 이면 제약 없음.

    **없으면 「공급사를 시험함」 같은 말이 안 되는 관계가 남고**, 그 뒤로 그
    데이터로는 아무것도 못 믿는다."""

    managed_by: Mapped[str] = mapped_column(String(40), default="", server_default="")
    """누가 이 관계 종류와 그 줄을 관리하나 — `ObjectType.managed_by` 와 같다."""

    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OntologySnapshot(Base):
    """정의 전체를 한 행에 담아 둔 것 — **되돌릴 자리.**

    **감사 로그는 「누가 뭘 했나」 는 알려 주지만 되돌려 주지는 않는다.** 사람이
    한 칸씩 고칠 때는 그것으로 충분했지만, 기계는 타입 20개·속성 200개를 5초에
    만든다 — 그 실수도 같은 속도로 반영된다.

    적용 **직전**의 스키마를 통째로 남긴다. 되돌리기는 그 스키마를 다시 덮어
    씌우는 일이다. **그 뒤에 새로 만든 것은 안 지운다** — 지우면 그 사이에 쌓인
    객체가 고아가 된다(그 사실은 설명 문구가 말한다).
    """

    __tablename__ = "ontology_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(120), default="", server_default="")
    """**그때의 이름을 박는다.** 계정이 지워지면 누가 했는지 모르게 되는데, 그건
    되돌릴 자리가 존재하는 이유와 정면으로 어긋난다."""

    reason: Mapped[str] = mapped_column(String(200), default="", server_default="")
    """무엇을 하기 직전인가 — 「가져오기」·「되돌리기」."""

    schema: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """그 시점의 그룹·타입·속성·관계 종류 전부."""
