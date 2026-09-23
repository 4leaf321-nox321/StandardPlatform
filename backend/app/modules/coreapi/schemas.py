"""코어 API 의 모양 — **봉투는 고정, 알맹이는 온톨로지가 정한다.**

바깥 시스템이 코드를 짜는 자리라, 여기서 정한 이름은 그쪽 코드에 박힌다. 그래서 두 층으로
가른다:

    봉투    `as_of` · `next` · `items`, 행의 `key` · `label` · `status` · `updated_at` ·
            `deleted` · `merged_into`. **어느 타입이든 같다.** 받는 쪽의 반복문은 이것만 안다.
    알맹이  `properties` 안. 타입이 정한 칸이라 설치마다 다르고, 온톨로지가 자라면 늘어난다.
            무엇이 있는지는 카탈로그(`/api/core`)가 스스로 설명한다.

**속성을 행에 펼치지 않는 이유**가 여기 있다. 펼치면(`{key, label, density}`) 누가 속성 키를
`label` 이나 `deleted` 로 만드는 날 봉투와 부딪힌다. 한 겹 넣어 두면 온톨로지가 무엇을 하든
봉투는 안전하다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CorePropertyOut(BaseModel):
    """칸 하나의 설명 — 받는 쪽이 **하드코딩하지 않게** 하는 자리."""

    key: str
    label: str
    data_type: str
    """text · text_long · number · date · datetime · bool · enum · url · object_ref."""
    unit: str = ""
    required: bool = False
    multi: bool = False
    """참이면 `properties` 의 그 칸은 **배열**이다."""
    enum_options: list[str] = Field(default_factory=list)
    ref_type_slug: str | None = None
    """`object_ref` 면 가리키는 코어 타입. 값은 그 타입의 **`key`** 로 나간다 — id 로 주면
    받는 쪽에서 아무것도 못 가리킨다."""
    help: str = ""


class CoreTypeOut(BaseModel):
    slug: str
    label: str
    description: str = ""
    count: int
    """지금 볼 수 있는 건수 — 처음 붙는 쪽이 규모를 가늠하는 자리."""
    updated_at: str | None = None
    """이 타입에서 **가장 최근에 바뀐 때.** 이 값이 그대로면 받아 갈 것이 없다."""
    properties: list[CorePropertyOut]
    endpoint: str
    """이 타입의 행을 가져가는 자리. 주소를 짐작하지 않게 **우리가 적어 준다.**"""


class CoreCatalogOut(BaseModel):
    """**무엇이 열려 있나.** 처음 붙는 쪽은 이것 하나만 읽으면 된다."""

    system: str
    """이 설치의 이름(`app_slug`) — 받는 쪽이 어디서 온 것인지 적어 두게."""
    revision: str
    """열린 정의의 판. **구조가 바뀌면 이 값이 바뀐다** — 받는 쪽이 「칸이 달라졌네」 를
    코드로 알아챌 수 있다."""
    as_of: str
    types: list[CoreTypeOut]


class CoreConsumerOut(BaseModel):
    """이 창구를 쓰는 자격 하나 — **누가 무엇을 가져가는지 사람이 볼 자리.**

    바깥에 연 이름은 남의 코드에 박힌다. 그것을 지우거나 바꾸려 할 때 「누가 쓰는가」 가
    화면에 없으면, 사람은 아무도 안 쓴다고 여기고 누른다.
    """

    name: str
    """토큰 이름 — 발급할 때 적은 용도(「MatNexus 야간 동기화」)."""
    owner: str
    """토큰이 붙은 계정."""
    last_used_at: datetime | None = None
    """**한 번도 안 쓴 토큰은 여기가 비어 있다** — 아직 안 붙은 연동이라는 뜻이다."""
    expires_at: datetime | None = None
    """없으면 만료가 없다 — 연동이 끝난 뒤에도 살아 있는 자격이 가장 오래 남는 구멍이다."""
    narrow: bool = False
    """`core:read` 만 가진 좁은 자격인가. 거짓이면 `read` 라 **코어 밖도 읽는다.**"""
    created_at: datetime


class CoreRowOut(BaseModel):
    """행 하나 — **봉투는 고정.**"""

    key: str
    """이 시스템에서 이 객체를 가리키는 값. **받는 쪽은 이것을 저장해 둔다** — 그래야 다음
    동기화가 「고침」 이 되지 「새로 만들기」 가 안 된다. 식별자가 없는 객체는 이름이 온다."""
    label: str
    status: str = "active"
    """active · deprecated. **지운 것이 아니라 그만 쓰는 것**은 이 값으로 온다."""
    updated_at: str
    deleted: bool = False
    """참이면 이 시스템에서 사라졌다 — 받는 쪽은 자기 것을 비활성으로 둔다."""
    merged_into: str | None = None
    """다른 것에 합쳐져서 사라졌으면 이긴 쪽의 `key`. **받는 쪽이 제 참조를 옮길 수 있다.**"""
    properties: dict[str, Any] = Field(default_factory=dict)
    """타입이 정한 칸. 참조는 상대의 `key`, 여러 값은 배열, 날짜는 `YYYY-MM-DD`,
    일시는 `...Z`. **빈 값은 키를 뺀다.**"""


class CorePageOut(BaseModel):
    """한 쪽 — `next` 가 빌 때까지 부른다."""

    type_slug: str
    as_of: str | None = None
    """**다음 호출에 이 값을 `since` 로 그대로 넣는다.** 받는 쪽 시계를 쓰면 시계가 몇 초만
    달라도 그 사이 행이 샌다.

    **쪽이 남아 있으면(`next` 가 있으면) `null` 이다** — 끝까지 받은 뒤에만 시계를 옮긴다.
    안 그러면 중간에서 멈춘 쪽이 남은 쪽을 영영 안 받는다."""
    since: str | None = None
    next: str | None = None
    """다음 쪽의 커서. 없으면 끝이다."""
    items: list[CoreRowOut]


class CorePullOut(BaseModel):
    """누가 언제 무엇을 받아 갔나 — 감사 기록의 `core.pull` 한 줄."""

    at: datetime
    actor: str
    token: str | None = None
    """통로(토큰 이름). 사람이 화면에서 본 것과 기계가 받아 간 것을 가른다."""
    type_slug: str
    rows: int
    since: str = ""


class CoreStatusOut(BaseModel):
    """**무엇이 열려 있고, 누가 읽을 수 있고, 누가 받아 갔나** — 한 화면.

    셋이 흩어져 있으면(타입 목록 · 토큰 목록 · 감사 기록) 「지금 바깥에 뭐가 나가고 있지」 를
    아무도 한눈에 답할 수 없고, 그러면 열어 둔 것을 잊는다.
    """

    types: list[CoreTypeOut]
    consumers: list[CoreConsumerOut]
    recent: list[CorePullOut]
    base: str
    """이 설치의 코어 창구 주소 — 상대에게 그대로 건넨다."""
