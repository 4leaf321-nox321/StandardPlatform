"""묶음 가져오기의 모양 — **새 형식을 만들지 않는다.**

정의는 `ontology/import` 의 스키마, 객체 행은 `objects/{slug}/import-rows`, 관계 행은
`objects/{slug}/relations/import-rows` 와 같다. 따로 만들면 변환기가 하나 더 생기고, 둘은
언젠가 갈린다.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.modules.objects.schemas import ImportPlanOut as RowsPlanOut
from app.modules.ontology.schemas import ImportPlanOut as SchemaPlanOut


class ObjectBatchIn(BaseModel):
    type_slug: str = Field(min_length=1)
    workspace_slug: str | None = None
    """소유 부서. 비우면 전역이고, 전역은 시스템 관리자만 넣는다."""
    rows: list[dict[str, Any]]


class RelationBatchIn(BaseModel):
    type_slug: str = Field(min_length=1)
    """출발 타입. 행은 `src · relation · dst · evidence_note`."""
    rows: list[dict[str, Any]]


class BundleIn(BaseModel):
    ontology: dict[str, Any] | None = None
    """`ontology/import` 와 같은 스키마. 없으면 정의는 안 건드린다."""
    objects: list[ObjectBatchIn] = Field(default_factory=list)
    """**적는 차례대로 넣는다** — 다른 타입을 참조하는 타입은 그 뒤에 둔다."""
    relations: list[RelationBatchIn] = Field(default_factory=list)
    source: str = Field(default="", pattern=r"^([a-z][a-z0-9_-]{0,39})?$")
    """**어디서 받은 묶음인가** — 허브에서 받으면 `hub`. 적으면 그 허브가 관리하는 정의 ·
    객체를 고칠 수 있고, 이 묶음이 들이는 타입 · 관계 종류는 그 허브의 관리가 된다(그 뒤로
    이 설치의 화면 · 파일 · MCP 로는 못 고친다). 시스템 관리자만.

    적지 않으면(기본) 허브가 관리하는 것은 **막힌다** — 받는 쪽에서 고친 값은 다음 받기가
    덮어쓰고, 그 사실은 고친 사람에게 안 보이기 때문이다."""
    apply: bool = False
    """거짓(기본)이면 아무것도 저장하지 않고 한 번에 미리 본다."""


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
    errors: list[str]
    """묶음 전체에 걸린 오류(권한 등)."""
    snapshot_id: uuid.UUID | None
    """정의를 적용했으면 그 직전의 스냅샷 — 되돌릴 자리."""
    counts: dict[str, int]


class BundleExportOut(BaseModel):
    """허브가 내려주는 묶음 — **가져오기와 같은 모양.** 받는 쪽은 `ontology` · `objects` ·
    `relations` 를 그대로 `POST /bundles/import` 에 `source` 를 붙여 보낸다."""

    format: str
    group: str
    exported_at: str
    ontology: dict[str, Any]
    objects: list[ObjectBatchIn]
    relations: list[RelationBatchIn]
    counts: dict[str, int]
    warnings: list[str]
    """내보냈지만 받는 쪽에서 잃는 것 — 식별자 없는 객체, 관계에 붙은 속성 등."""
