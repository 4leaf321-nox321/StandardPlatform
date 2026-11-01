"""자주검증 — 주고받는 모양."""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["review", "postmortem", "briefing"]
"""회의 종류 — 심사회 · 포스트모텀 · 설명회."""

PresenceField = Literal["topic", "problem", "action", "remark", "schedule", "title"]
"""지금 수정하는 칸 — 줄의 글 칸 넷과 일정, 묶음 · 회의의 이름(`title`)."""

#: 받는 본문은 **모르는 칸을 거절한다** — 옛 칸(`due_on`)이나 오타를 말없이 버리면, 보낸 쪽은
#: 저장된 줄 알고 그 값은 어디에도 없다.
STRICT = ConfigDict(extra="forbid")

#: 칸 글 하나의 상한 — 한 장 보고서의 칸이라 넉넉히, 그래도 끝은 있게.
CELL_MAX = 20_000


class MeetingOut(BaseModel):
    id: uuid.UUID
    kind: Kind
    kind_label: str
    title: str
    held_on: date | None
    workspace_slug: str | None
    workspace_name: str | None
    author_name: str | None
    created_at: datetime
    updated_at: datetime
    revision: int
    """판 번호 — 이 회의의 쓰기마다 하나씩 오른다. 화면의 자동 반영이 이것만
    묻는다(`pulse`)."""
    confirmed_at: datetime | None
    """회의 확정 — 채워져 있으면 확정을 해제할 때까지 이 회의의 쓰기가 전부 409 다."""
    confirmed_by_name: str | None


class MeetingBriefOut(MeetingOut):
    """회의 목록의 한 줄."""

    row_count: int
    done_count: int
    """작성 완료한 줄의 수."""


class MeetingPageOut(BaseModel):
    items: list[MeetingBriefOut]
    total: int
    limit: int
    offset: int


class MeetingIn(BaseModel):
    """회의 만들기 — 종류 · 제목 · 일자 · 부서만 묻는다(짧은 창)."""

    kind: Kind
    title: str = Field(min_length=1, max_length=200)
    held_on: date | None = None
    workspace_slug: str = Field(min_length=1, max_length=64)
    """어느 부서의 회의인가 — 그 부서의 구성원이어야 한다."""


class MeetingExpected(BaseModel):
    """고치기 시작할 때 본 값 — **보낸 칸만** 지금 값과 견준다."""

    model_config = STRICT

    title: str | None = Field(default=None, max_length=200)
    held_on: date | None = None


class MeetingPatch(BaseModel):
    """**보낸 칸만** 바꾼다 — 일자를 비우려면 `held_on: null`(안 보낸 것과 다르다).

    **고친 칸마다 `expected`(고치기 전에 읽은 값)가 있어야 한다**(없으면 422) — 그 칸의 지금
    값이 그것과 다르면(그사이 남이 고쳤으면) 409 로 거절한다(`details` 에 지금 값 · 누가 ·
    언제)."""

    model_config = STRICT

    title: str | None = Field(default=None, min_length=1, max_length=200)
    held_on: date | None = None
    expected: MeetingExpected | None = None


class PhotoOut(BaseModel):
    id: uuid.UUID
    name: str
    is_image: bool
    width: int | None
    height: int | None
    size_bytes: int
    thumbnail_path: str | None
    """미리보기(긴 변 320px) — `/api` 아래의 경로. 인증이 필요해 화면은 받아서 띄운다."""


class RowPhotosOut(BaseModel):
    topic: list[PhotoOut]
    problem: list[PhotoOut]
    action: list[PhotoOut]
    remark: list[PhotoOut]


class ScheduleItem(BaseModel):
    """일정의 한 단계 — 날짜와 짧은 이름(「설계 변경」 · 비워도 된다)."""

    model_config = STRICT

    # 칸 이름이 형의 이름(`date`)을 가리지 않게 — 형은 모듈 이름으로 적는다.
    date: dt.date
    label: str = Field(default="", max_length=200)


class RowOut(BaseModel):
    """표의 한 줄 — 글 칸 넷(사진의 자리 · 폭은 칸 글 안의 표시 줄) · 일정 · 칸마다 사진 · 작성
    완료."""

    id: uuid.UUID
    group_id: uuid.UUID | None
    """어느 묶음의 줄인가 — 비면 묶음 없음(표 맨 위)."""
    position: int
    """묶음 안의 차례."""
    topic: str
    """항목."""
    problem: str
    """문제점."""
    action: str
    """개선대책."""
    remark: str
    """비고."""
    schedule: list[ScheduleItem]
    """일정 — 날짜 차례로."""
    due_on: date | None
    """완료 예정일 — 일정의 가장 늦은 날(파생값, 직접 쓰지 않는다)."""
    photos: RowPhotosOut
    """칸마다 붙은 사진 — **정본**. 칸 글 안의 `![](photo:<id>?w=50)` 줄이 자리 · 폭을 말하고,
    표시가 없는 사진은 칸 끝에 선다."""
    done: bool
    """작성 완료."""
    done_by_name: str | None
    done_at: datetime | None
    author_name: str | None
    updated_by_name: str | None
    """마지막으로 수정한 사람."""
    created_at: datetime
    updated_at: datetime
    revision: int
    """이 줄을 마지막으로 바꾼 쓰기의 판 번호(회의의 것) — 그 쓰기의 응답이면 곧 그 쓰기의
    번호다."""


class GroupOut(BaseModel):
    """묶음 — 표 중간의 머리줄."""

    id: uuid.UUID
    position: int
    title: str
    author_name: str | None
    created_at: datetime
    updated_at: datetime
    revision: int


class CellOut(BaseModel):
    key: str
    """저장 칸 — 글 열이면 사진의 `owner_field`(`topic` · `problem` · `action` · `remark`)."""
    label: str
    kind: Literal["text", "schedule"]
    """`text` — 글 + 사진(칸 글 안의 표시 줄), `schedule` — 일정 단계 목록."""


class SheetOut(BaseModel):
    """회의 한 장 — 화면이 한 번에 받는다."""

    meeting: MeetingOut
    cells: list[CellOut]
    """표의 열 — 차례대로."""
    groups: list[GroupOut]
    """묶음 — 차례대로. 줄은 `rows` 에 묶음 없는 것부터, 묶음의 차례대로 선다."""
    rows: list[RowOut]
    can_write: bool
    """줄 · 묶음 · 사진 · 작성 완료 · 배치를 수정할 수 있나 — 그 회의 부서의 구성원 · 시스템
    관리자, 그리고 **확정 전**이어야 한다."""
    write_refusal: str | None
    """수정할 수 없는 까닭 — 단추가 그냥 없으면 고장으로 읽힌다."""
    can_manage: bool
    """회의의 제목 · 일자 · 삭제 · 확정 · 확정 해제를 할 수 있나 — 만든 사람(아직 구성원) · 그
    부서 관리자 · 시스템 관리자. 확정된 동안 제목 · 일자 · 삭제는 확정 해제 뒤에 된다."""
    manage_refusal: str | None
    max_rows: int
    max_groups: int


class RowIn(BaseModel):
    """줄 추가 — 비워 두면 빈 줄. `after_id` 를 주면 그 줄 바로 아래(그 줄의 묶음), 아니면
    `group_id` 묶음의 맨 아래(비우면 묶음 없음의 맨 아래). 처음 값에는 `expected` 가 없다."""

    model_config = STRICT

    after_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    topic: str = Field(default="", max_length=CELL_MAX)
    problem: str = Field(default="", max_length=CELL_MAX)
    action: str = Field(default="", max_length=CELL_MAX)
    remark: str = Field(default="", max_length=CELL_MAX)
    schedule: list[ScheduleItem] = Field(default_factory=list, max_length=20)


class RowExpected(BaseModel):
    """고치기 시작할 때 본 값 — **보낸 칸만** 지금 값과 견준다(일정은 목록 통째로)."""

    model_config = STRICT

    topic: str | None = Field(default=None, max_length=CELL_MAX)
    problem: str | None = Field(default=None, max_length=CELL_MAX)
    action: str | None = Field(default=None, max_length=CELL_MAX)
    remark: str | None = Field(default=None, max_length=CELL_MAX)
    schedule: list[ScheduleItem] | None = Field(default=None, max_length=20)


class RowPatch(BaseModel):
    """**보낸 칸만** — 칸 글은 빈 글로 비우고, 일정은 `[]` 로 비운다. 사진의 자리 · 폭은 칸 글
    안의 표시 줄(`![](photo:<id>?w=50)`)을 옮기고 고친다.

    **고친 칸마다 `expected`(고치기 전에 읽은 값)가 있어야 한다**(없으면 422 — 먼저 한 장을
    읽는다). 그 칸의 지금 값이 다르면(그사이 남이 고쳤으면) 409 — **칸 단위**라 남이 다른 칸을
    고친 것과는 부딪치지 않는다."""

    model_config = STRICT

    topic: str | None = Field(default=None, max_length=CELL_MAX)
    problem: str | None = Field(default=None, max_length=CELL_MAX)
    action: str | None = Field(default=None, max_length=CELL_MAX)
    remark: str | None = Field(default=None, max_length=CELL_MAX)
    schedule: list[ScheduleItem] | None = Field(default=None, max_length=20)
    expected: RowExpected | None = None


class DoneIn(BaseModel):
    """작성 완료 — 켜고 끈다."""

    done: bool


class GroupIn(BaseModel):
    """묶음 만들기 — 맨 아래에 선다. `row_ids` 의 줄을 (지금 표의 차례대로) 그 묶음으로
    옮긴다. 그사이 지워진 줄은 건너뛰고 응답의 `skipped` 에 적는다."""

    model_config = STRICT

    title: str = Field(min_length=1, max_length=200)
    row_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)


class GroupExpected(BaseModel):
    model_config = STRICT

    title: str | None = Field(default=None, max_length=200)


class GroupPatch(BaseModel):
    """묶음 이름 — `expected.title`(고치기 전에 읽은 이름)이 있어야 한다(줄의 칸과 같은
    규칙)."""

    model_config = STRICT

    title: str = Field(min_length=1, max_length=200)
    expected: GroupExpected | None = None


class MoveTarget(BaseModel):
    """줄을 놓을 자리 — `group`(null = 묶음 없음) 안에서 `before` · `after` 줄의 앞 · 뒤,
    아니면 `at`(맨 위 `start` · 맨 아래 `end`). 기준 줄이 살아 있으면 그 줄의 묶음이 이긴다.
    기준 줄이 그사이 사라졌으면 `group` 의 끝으로 간다(`anchor_missing`)."""

    model_config = STRICT

    group: uuid.UUID | None
    before: uuid.UUID | None = None
    after: uuid.UUID | None = None
    at: Literal["start", "end"] = "end"


class MoveIn(BaseModel):
    """옮기기 — **상대 동작**이다. 서버가 그때의 차례에 대고 적용하므로 차례 때문에 부딪치지
    않는다(그사이 남이 줄을 더하거나 지워도 409 가 아니다). 셋 중 하나:

    - 줄 한 칸: `{"rows": [줄], "step": "up" | "down"}` — 그 묶음 안의 이웃과 자리를
      바꾼다(묶음 경계는 넘지 않는다)
    - 묶음 한 칸: `{"group": 묶음, "step": "up" | "down"}`
    - 줄을 다른 자리로: `{"rows": [줄…], "to": {"group": 묶음 | null, "before": 줄?, "after":
      줄?, "at": "start" | "end"}}` — 여럿이면 지금 표의 차례대로 붙여 놓는다

    그사이 사라진 줄 · 묶음은 건너뛰고 응답의 `skipped` 에 적는다."""

    model_config = STRICT

    rows: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    group: uuid.UUID | None = None
    step: Literal["up", "down"] | None = None
    to: MoveTarget | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> MoveIn:
        row_step = bool(self.rows) and self.step is not None and self.to is None
        group_step = self.group is not None and self.step is not None and not self.rows
        place = bool(self.rows) and self.to is not None and self.step is None
        if [row_step, group_step, place].count(True) != 1 or (
            self.group is not None and self.to is not None
        ):
            raise ValueError(
                "옮기기는 셋 중 하나입니다 — rows + step · group + step · rows + to"
            )
        if row_step and len(self.rows) != 1:
            raise ValueError("한 칸 옮기기(step)는 줄 하나씩입니다")
        return self


class PresenceIn(BaseModel):
    """지금 수정하는 칸 — 줄의 칸(`row_id` + 칸), 묶음 이름(`group_id` + `title`), 회의 제목
    (둘 다 비우고 `title`)."""

    row_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    field: PresenceField | None = None


class PresenceOut(BaseModel):
    user_id: uuid.UUID
    name: str
    row_id: uuid.UUID | None
    group_id: uuid.UUID | None
    field: str | None


class ArrangeOut(SheetOut):
    """옮기기 · 묶음 만들기의 결과 — 바뀐 한 장과 건너뛴 것."""

    skipped: list[uuid.UUID] = Field(default_factory=list)
    """그사이 사라져서 건너뛴 줄 · 묶음(기준 줄 포함)."""
    anchor_missing: bool = False
    """기준 줄(`before` · `after`)이 사라져 그 묶음의 끝에 놓았다."""
    changed: bool = True
    """자리가 바뀌었나 — false 면 그대로다(끝에서 한 칸 더 · 이미 그 자리)."""
    group_id: uuid.UUID | None = None
    """묶음 만들기면 새 묶음."""


class PulseOut(BaseModel):
    """한 장이 바뀌었나 — 화면이 10~15초마다 묻는다(탭이 보일 때만). 판 번호가 바뀌었을 때만 한
    장을 다시 받는다."""

    revision: int
    confirmed: bool
    presence: list[PresenceOut]
    """**30초 안에** 칸을 수정하던 다른 사람 — 묻는 사람은 뺀다."""
