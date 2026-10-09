"""묶음 가져오기의 모양 — **새 형식을 만들지 않는다.**

정의는 `ontology/import` 의 스키마, 객체 행은 `objects/{slug}/import-rows`, 관계 행은
`objects/{slug}/relations/import-rows` 와 같다. 따로 만들면 변환기가 하나 더 생기고, 둘은
언젠가 갈린다.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.objects.schemas import ImportPlanOut as RowsPlanOut
from app.modules.ontology.schemas import ImportPlanOut as SchemaPlanOut


class ObjectBatchIn(BaseModel):
    type_slug: str = Field(min_length=1)
    workspace_slug: str | None = None
    """소유 부서. 비우면 전역이고, 전역은 시스템 관리자만 넣는다."""
    rows: list[dict[str, Any]]
    aliases_mode: Literal["add", "replace"] = "add"
    """별칭을 **더할지 맞출지.** 기본은 더하기(사람이 화면에서 붙인 것을 지키려고).
    허브 내보내기는 `replace` 를 적는다 — 허브에서 뺀 별칭이 받는 쪽에 남으면 둘이 갈린다."""


class RelationBatchIn(BaseModel):
    type_slug: str = Field(min_length=1)
    """출발 타입. 행은 `src · relation · dst · evidence_note` 와 그 종류의 속성."""
    rows: list[dict[str, Any]]
    mode: Literal["add", "replace", "replace_type"] = "add"
    """`replace` 면 이 묶음에 나온 (출발 객체 · 관계 종류) 범위에서 **오지 않은 선을 끊는다.**
    허브 내보내기가 이것을 적는다 — 허브에서 끊은 선이 받는 쪽에 남으면 둘이 갈린다."""


class ObjectTombstoneIn(BaseModel):
    """허브에서 **사라진 객체** 하나."""

    type_slug: str = Field(min_length=1)
    key: str = Field(min_length=1)
    """허브에서 그것을 가리키던 값(식별자, 없으면 이름)."""
    merged_into: str | None = None
    """다른 것에 **합쳐져** 사라졌으면 이긴 쪽의 식별자. 받는 쪽은 합치기를 계획에 올린다 —
    그래야 이쪽에서 그것을 가리키던 참조 · 관계가 이긴 쪽으로 옮겨진다."""


class RelationTombstoneIn(BaseModel):
    """허브에서 **끊긴 선** 하나 — 세 끝으로 찾는다."""

    type_slug: str = Field(min_length=1)
    src: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    dst: str = Field(min_length=1)
    dst_type: str = ""
    """도착점의 타입 slug — 있으면 그 타입의 것만 끊는다. 끝이 인터페이스인 종류는 도착이
    여러 타입이고 식별자는 타입 안에서만 하나라, 이것 없이 찾으면 같은 식별자를 가진 다른
    타입의 선을 끊을 수 있다. 이 설치에 없는 타입이면 버린다(예전처럼 세 끝으로 찾는다)."""


class TombstonesIn(BaseModel):
    objects: list[ObjectTombstoneIn] = Field(default_factory=list)
    relations: list[RelationTombstoneIn] = Field(default_factory=list)


class BundleIn(BaseModel):
    ontology: dict[str, Any] | None = None
    """`ontology/import` 와 같은 스키마. 없으면 정의는 안 건드린다."""
    objects: list[ObjectBatchIn] = Field(default_factory=list)
    """**적는 차례대로 넣는다** — 다른 타입을 참조하는 타입은 그 뒤에 둔다."""
    relations: list[RelationBatchIn] = Field(default_factory=list)
    tombstones: TombstonesIn | None = None
    """허브에서 **사라진 것** — 지운 객체와 끊긴 선. 받는 쪽은 지우지 않는다: 객체는
    **사용 중지**, 합쳐진 것은 **합치기**, 선은 **끊기**로 계획에 올린다."""
    source: str = Field(default="", pattern=r"^([a-z][a-z0-9_-]{0,39})?$")
    """**어디서 받은 묶음인가** — 허브에서 받으면 `hub`. 적으면 그 허브가 관리하는 정의 ·
    객체를 고칠 수 있고, 이 묶음이 들이는 타입 · 관계 종류는 그 허브의 관리가 된다(그 뒤로
    이 설치의 화면 · 파일 · MCP 로는 못 고친다). 시스템 관리자만.

    적지 않으면(기본) 허브가 관리하는 것은 **막힌다** — 받는 쪽에서 고친 값은 다음 받기가
    덮어쓰고, 그 사실은 고친 사람에게 안 보이기 때문이다."""
    apply: bool = False
    """거짓(기본)이면 아무것도 저장하지 않고 한 번에 미리 본다."""
    preview: Literal["full", "plan"] = "full"
    """미리 보기를 **어떻게** 하나.

        full   기본 — 적용을 끝까지 돌린 뒤 되돌린다. 뒤 묶음이 앞 묶음의 객체를 찾을 수
               있어 판정이 적용과 **똑같다**. 대신 대량 백필은 **시간이 두 배**다.
        plan   계획만 본다. 이 묶음이 **만들** 객체를 가리키는 칸 · 끝점은 오류가 아니라
               「적용할 때 풀린다」 로 줄에 적는다. 빠른 대신, 그 칸의 값까지 맞춰 보지는
               않는다(적용에서 다시 본다).
    """
    events: Literal["each", "summary"] = "each"
    """바깥에 알리는 것(웹훅 · 지켜보기)을 **줄마다 보낼지 한 번에 보낼지.**

    백필은 수만 줄이다. 줄마다 보내면 웹훅 수신 측이 수만 번 두드려 맞고, 이쪽도 그만큼
    조회한다 — 받는 쪽은 그때 「무슨 일이 났나」 를 알 수 없다. `summary` 는 **묶음 한 건**을
    보낸다."""
    audit: Literal["each", "summary"] = "each"
    """감사 기록을 **줄마다 남길지 묶음 한 줄로 남길지.**

    백필 수만 줄이 감사 로그를 뒤덮으면, 사람이 한 일을 그 사이에서 찾을 수 없다.
    `summary` 는 묶음 한 줄만 남긴다(무엇이 몇 건인지는 그 줄에 적힌다)."""
    human_edits: Literal["keep", "overwrite"] = "keep"
    """**사람이 화면에서 고친 칸**을 어떻게 할지.

    기본 `keep` — 비켜 가고 줄에 적는다. 파일로 넣기는 같은 식별자면 덮으므로, 원천을 다시
    정제해 넣을 때마다 사람의 수정이 사라지면 **사람은 고치기를 그만둔다.**

    `overwrite` 는 덮고 표시를 지운다 — 원천이 정본이라고 사람이 정했을 때만."""
    missing_refs: Literal["error", "blank"] = "error"
    """가리키는 객체를 못 찾았을 때 — **그 줄을 오류로 볼지, 그 칸만 비우고 적을지.**

    기본은 오류다(전부 아니면 무). 그런데 백필에서는 **한 줄이 묶음 전체를 막는다** —
    PLM 에 없는 모델을 가리키는 행 하나 때문에 수만 줄이 안 들어간다. `blank` 는 그 칸만
    비우고 줄에 적는다(별칭이 겹칠 때와 같은 규칙이다)."""


class BatchOut(BaseModel):
    type_slug: str
    plan: RowsPlanOut | None
    """행마다 무엇이 되나. 묶음 자체가 막혔으면(모르는 타입 · 권한) None 이고
    `error` 가 말한다."""
    error: str = ""


class BundleOut(BaseModel):
    """묶음이 무엇을 할 것인가(또는 했나) — **전부 아니면 무.**"""

    applied: bool
    ok: bool
    """하나라도 오류가 있으면 거짓 — 그때 `apply` 여도 아무것도 안 들어간다."""
    ontology: SchemaPlanOut | None
    objects: list[BatchOut]
    relations: list[BatchOut]
    tombstones: RowsPlanOut | None = None
    """사라진 것을 어떻게 할지 — 줄마다 `merge` · `deprecate` · `unlink` · `unchanged`."""
    errors: list[str]
    """묶음 전체에 걸린 오류(권한 등)."""
    snapshot_id: uuid.UUID | None
    """정의를 적용했으면 그 직전의 스냅샷 — 되돌릴 자리."""
    run_id: uuid.UUID | None = None
    """적용한 판의 번호 — **이 판을 통째로 되돌리는 자리**다
    (`POST /bundles/runs/{run_id}/undo`). 미리 보기에는 없다(아무것도 안 바뀐다)."""
    counts: dict[str, int]


class BundleExportOut(BaseModel):
    """허브가 내려주는 묶음 — **가져오기와 같은 모양.** 받는 쪽은 `ontology` · `objects` ·
    `relations` · `tombstones` 를 그대로 `POST /bundles/import` 에 `source` 를 붙여 보낸다."""

    format: str
    group: str
    exported_at: str
    ontology: dict[str, Any]
    objects: list[ObjectBatchIn]
    relations: list[RelationBatchIn]
    tombstones: TombstonesIn = Field(default_factory=TombstonesIn)
    """허브에서 **사라진 것** — 지운 객체(합쳐진 것은 이긴 쪽과 함께)와 끊긴 선."""
    counts: dict[str, int]
    warnings: list[str]
    """내보냈지만 받는 쪽에서 잃는 것 — 식별자 없는 객체 등."""
