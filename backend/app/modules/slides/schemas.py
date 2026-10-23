"""장표의 입출력(ADR 0037)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.objects.schemas import ConditionOut

#: 격자의 한 변 — 조직의 정형 장표는 2 ~ 3행 · 2 ~ 3열 안이다(사용자, 2026-10-23).
GRID_MAX = 3
#: 영역 하나의 기록 수 — 기본과 상한. 장표는 한 장이다.
AREA_LIMIT = 10
AREA_LIMIT_MAX = 50
#: 영역이 보이는 칸 · 연결 항목 줄의 칸 수 상한.
FIELDS_MAX = 12


class AreaIn(BaseModel):
    """틀의 영역 하나 — 격자의 자리와 무엇을 받는가."""

    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    """틀 안에서 영역을 가리키는 이름 — 장표의 글 · 고른 기록이 이것으로 붙는다. 바꾸면 그
    영역에
    써 둔 글이 떨어진다."""
    title: str = Field(min_length=1, max_length=60)
    """머리글(「AS-IS」 · 「변경점」 · 「종합 의견」)."""
    row: int = Field(ge=0, lt=GRID_MAX)
    col: int = Field(ge=0, lt=GRID_MAX)
    row_span: int = Field(default=1, ge=1, le=GRID_MAX)
    col_span: int = Field(default=1, ge=1, le=GRID_MAX)
    kind: Literal["records", "text"] = "records"
    type_slug: str | None = None
    """기록 영역의 타입. 짝 영역은 비워 둔다(짝의 것을 쓴다)."""
    fields: list[str] = Field(default_factory=list, max_length=FIELDS_MAX)
    """보여 줄 칸 — 속성 키, 또는 `label`(이름) · `key`(식별자)."""
    fill: Literal["condition", "pick", "follow"] = "condition"
    """채우는 법 — 조건에 맞는 것 전부(목록), 장표마다 선택(후보는 조건으로 좁힌다), 다른
    영역을
    따라옴(그 영역에 표시된 기록과 이어진 것 전부)."""
    subject_field: str | None = Field(default=None, max_length=200)
    """주제와 이어지는 칸의 주소(`model` · `ref.meeting.model` · `in.<관계>.model` …). 비우면
    주제로
    거르지 않는다(고정 조건만)."""
    subject_op: Literal["eq", "under"] = "eq"
    """`under` 면 주제의 하위 전체(트리가 있는 타입)."""
    conditions: list[ConditionOut] = Field(default_factory=list, max_length=20)
    """고정 조건 — 목록의 거르기와 같은 말."""
    ref_line: list[str] = Field(default_factory=list, max_length=FIELDS_MAX)
    """연결 항목 줄에 실을 참조 칸."""
    follow_area: str | None = None
    """따라갈 영역 — `fill` 이 `follow` 일 때."""
    follow_field: str | None = Field(default=None, max_length=200)
    """이 기록의 어느 칸(주소)이 따라갈 영역의 기록을 가리키나 — `change` · `in.<관계>` …."""
    pair_of: str | None = None
    """짝 영역 — 이 영역은 그 영역의 기록을 같은 차례로 보이고 칸만 다르다(AS-IS / TO-BE)."""
    limit: int = Field(default=AREA_LIMIT, ge=1, le=AREA_LIMIT_MAX)
    """표시할 건수 — 선택 영역이면 선택할 수 있는 건수(1 이면 「기록 하나」)."""
    order: Literal["newest", "oldest", "label"] = "newest"


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    subject_type_slug: str = Field(min_length=1)
    rows: int = Field(ge=1, le=GRID_MAX)
    cols: int = Field(ge=1, le=GRID_MAX)
    areas: list[AreaIn] = Field(min_length=1, max_length=GRID_MAX * GRID_MAX)


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    subject_type_slug: str
    subject_type_label: str
    rows: int
    cols: int
    areas: list[AreaIn]
    slides: int
    """이 틀로 만든 장표 수(지운 것 빼고)."""
    updated_at: datetime


class SlideCreateIn(BaseModel):
    template_id: uuid.UUID
    subject_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)
    """비우면 「<주제 이름> <틀 이름>」."""


class SlidePatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    texts: dict[str, str] | None = None
    """글 영역의 글 — 영역 키마다. 보낸 영역만 바뀐다."""
    picks: dict[str, list[uuid.UUID]] | None = None
    """고르기 영역의 기록 — 영역 키마다 **통째로**. 보낸 영역만 바뀐다."""
    made: dict[str, uuid.UUID | None] | None = None
    """글 영역의 글로 만든 기록 — 영역 키마다. `null` 이면 그 표시를 지운다(기록은 남는다)."""


class PreviewIn(BaseModel):
    """틀 편집의 미리 보기 — 저장하지 않은 틀을 주제 하나로 세워 본다."""

    template: TemplateIn
    subject_id: uuid.UUID


class FieldOut(BaseModel):
    key: str
    label: str


class LinkItemOut(BaseModel):
    id: str
    label: str
    type_slug: str | None


class LinkOut(BaseModel):
    """연결 항목 줄의 한 칸 — 「모델: A」."""

    label: str
    items: list[LinkItemOut]


class TieOut(BaseModel):
    """따라오는 기록 하나와 따라갈 기록 하나를 잇는 것."""

    id: str
    """관계면 관계 id, 칸이면 따라갈 기록의 id."""
    source_id: str
    """잇는 따라갈 기록."""


class RecordOut(BaseModel):
    id: uuid.UUID
    label: str
    type_slug: str
    values: dict[str, str]
    """보이는 칸의 값을 글로 — 참조는 이름, 여러 값은 「, 」 로."""
    links: list[LinkOut]
    can_edit: bool = False
    """이 사람이 이 기록의 값을 수정할 수 있나 — 객체 상세의 `can_edit` 와 같은 판정(ADR
    0036)."""
    ties: list[TieOut] = Field(default_factory=list)
    """따라오는 영역에서 이 기록을 따라갈 영역의 기록과 잇는 것 — 「연결 해제」 가 이것을
    끊는다."""


class AreaLinkOut(BaseModel):
    """따라오는 영역이 따라갈 영역의 기록과 **어떻게** 이어지나 — 장표에서 연결 · 해제할 때
    객체
    API 로 무엇을 부를지. 걸음이 둘 이상인 주소면 장표에서 잇지 않는다(없음)."""

    kind: Literal["relation", "ref", "backref"]
    """`relation` — 관계 하나. `ref` — 이 기록의 참조 칸이 따라갈 기록을 가리킨다. `backref` —
    따라갈 기록의 참조 칸이 이 기록을 가리킨다."""
    relation: str | None = None
    direction: Literal["in", "out"] | None = None
    """관계의 방향 — `in` 이면 따라갈 기록이 출발점(관계를 거기서 맺는다), `out` 이면 이
    기록이."""
    property: str | None = None
    """`ref` 면 이 기록의 칸, `backref` 면 따라갈 기록의 칸."""
    multi: bool = False
    source_type_slug: str
    sources: list[LinkItemOut]
    """따라갈 영역에 표시된 기록 — 둘 이상이면 어느 것과 이을지 고른다."""
    can_link: bool
    """따라갈 기록 쪽을 고치는 연결(`in` 관계 · `backref`)이면 그 기록들을 고칠 수 있나. 이
    기록
    쪽을 고치는 연결이면 참 — 기록마다 서버가 다시 판정한다."""


class AreaOut(BaseModel):
    key: str
    title: str
    row: int
    col: int
    row_span: int
    col_span: int
    kind: str
    fill: str
    pair_of: str | None
    type_slug: str | None
    type_label: str | None
    fields: list[FieldOut]
    text: str = ""
    records: list[RecordOut] = Field(default_factory=list)
    total: int = 0
    """조건에 맞는 기록 수 — `records` 보다 많으면 「N건 더」."""
    list_params: dict[str, str] = Field(default_factory=dict)
    """「목록에서 보기」 — 같은 조건의 목록 주소(`f.<칸>.<연산>`). 고르기 · 글 영역은 비어
    있다."""
    follow_title: str | None = None
    """따라오는 영역이면 따라가는 영역의 머리글."""
    link: AreaLinkOut | None = None
    """따라오는 영역을 장표에서 연결 · 해제하는 법 — 걸음이 둘 이상이면 없다."""
    made: LinkItemOut | None = None
    """글 영역의 글로 만든 기록."""
    limit: int = AREA_LIMIT
    problem: str | None = None
    """이 영역만 못 그린 이유 — 칸이 지워졌거나 조건이 이제 안 맞을 때. 장표는 나머지를
    그린다."""


class SubjectOut(BaseModel):
    id: uuid.UUID
    label: str
    type_slug: str
    deleted: bool


class SlideSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    template_id: uuid.UUID
    template_name: str
    subject: SubjectOut | None
    author_name: str
    created_at: datetime
    edited_at: datetime | None


class SlideOut(SlideSummaryOut):
    rows: int
    cols: int
    areas: list[AreaOut]
    can_edit: bool
    rendered_at: datetime
    """값을 읽은 때 — 살아 있는 값이라 이때의 기록이다."""


class RenderOut(BaseModel):
    """미리 보기 — 저장하지 않은 틀을 세운 모습."""

    rows: int
    cols: int
    areas: list[AreaOut]


class CandidateOut(BaseModel):
    id: uuid.UUID
    label: str


class CandidatesOut(BaseModel):
    """선택 영역의 후보 — 틀의 조건에 맞는 것."""

    items: list[CandidateOut]
    total: int


class UsedInOut(BaseModel):
    """이 객체가 쓰인 장표 하나 — 주제이거나, 마지막으로 열었을 때 영역에 실렸거나."""

    slide: SlideSummaryOut
    role: Literal["subject", "record"]
    areas: list[str]
    """실린 영역의 머리글 — 주제면 비어 있다."""
    seen_at: datetime | None
    """영역에 실린 것을 본 때 — 장표는 살아 있는 값이라 그 뒤에 바뀌었을 수 있다."""
