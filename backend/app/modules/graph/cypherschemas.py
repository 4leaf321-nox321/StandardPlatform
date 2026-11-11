"""그래프 사본 경로의 요청 · 응답 모양(4단계)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CypherCopyOut(BaseModel):
    copied_at: str | None = None
    """사본을 실은 때(UTC ISO) — SP 의 지금과 다를 수 있다. 답에 함께 말한다."""
    sp_version: str | None = None
    nodes: int | None = None
    relationships: int | None = None


class CypherStatusOut(BaseModel):
    enabled: bool
    """이 설치에 사본이 있나(`GRAPHDB_URL`)."""
    reachable: bool
    copy_: CypherCopyOut | None = Field(default=None, alias="copy")
    error: str | None = None
    query_seconds: int
    max_rows: int

    model_config = {"populate_by_name": True}


class CypherPropOut(BaseModel):
    name: str
    label: str
    kind: str


class CypherLabelOut(BaseModel):
    label: str
    name: str
    count: int
    props: list[CypherPropOut]


class CypherRelOut(BaseModel):
    type: str
    name: str
    start: list[str]
    end: list[str]
    kind: str
    props: list[CypherPropOut]
    transitive: bool = False


class CypherShapeOut(BaseModel):
    labels: list[CypherLabelOut]
    relationships: list[CypherRelOut]
    rules: list[str]


class CypherAsk(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    """Cypher 읽기 질의 한 문장."""
    params: dict[str, Any] = Field(default_factory=dict)
    """`$이름` 매개변수 — 값은 글 안에 박지 않고 여기로."""
    limit: int | None = Field(default=None, ge=1, le=5000)
    """돌려줄 줄 상한(설정 상한 안). 비우면 설정의 상한."""


class CypherAnswerOut(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    """줄 상한에서 잘렸다 — 전부가 아니다. 집계(`count`)로 다시 묻는다."""
    seconds: float
    copy_: CypherCopyOut = Field(alias="copy")
    notes: list[str] = Field(default_factory=list)
    """Neo4j 의 경고(예: 데카르트 곱 · 없는 라벨)."""

    model_config = {"populate_by_name": True}
