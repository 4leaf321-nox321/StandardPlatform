"""뜻으로 찾기(ADR 0052) — 켠 타입과 객체마다의 뜻의 자리.

- `semantic_targets` — 관리자가 켠 타입 하나에 한 줄. **설치의 운영 선택이라 온톨로지 정의에
  두지 않는다** — 같은 정의를 쓰는 다른 설치(형제 SP)는 서버 · 용량이 달라 다르게 고른다.
  정의에 두면 묶음 내보내기 · 가져오기가 그 선택까지 옮긴다.
- `object_embeddings` — 객체의 조각마다 자리 하나(기본 글 하나 + 문서의 절마다 하나). 글의
  해시로 바뀐 것만 다시 바꾼다. 객체를 영구 삭제하면 함께 사라진다(CASCADE). 지운(휴지통) 객체
  · 끈 타입의 자리는 워커가 지운다.
- `semantic_marks` — 객체를 마지막으로 맞춰 본 때(ADR 0054).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.shared import vectors


class SemanticTarget(Base):
    """뜻으로 찾을 타입 — 켠 것만 줄이 있다."""

    __tablename__ = "semantic_targets"

    type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("object_types.id", ondelete="CASCADE"),
        primary_key=True,
    )
    fields: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    """뜻으로 바꿀 칸 키 — **None 이면 자동**(축만: 이름 · 다른 이름 · 설명 · 글 · 선택 · 참조
    칸 전부와 문서 전부). 기록은 고른다(ADR 0054) — `label` 은 이름, 문서 속성 키는 그 문서의
    절들."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ObjectEmbedding(Base):
    """뜻의 자리 **한 조각**(ADR 0052 · 0054) — 객체 하나에 여럿일 수 있다.

    - `part = ""` — 그 객체의 「기본 글」(축은 이름 · 글 · 선택 · 참조 칸 전부, 기록은 고른
      칸만).
    - `part = "doc:<칸>:<번호>"` — 문서 속성의 **절**(제목 h1 ~ h3 로 나눈 것, 길면 더 나눈다).
      `heading` 이 절의 경로(「3. 결과 › 3.1 모서리 찍힘」), `anchor` 가 그 제목의 id(화면의
      `MarkdownView` 와 같은 규칙) — 찾은 줄이 그 절로 바로 간다.
    """

    __tablename__ = "object_embeddings"
    __table_args__ = (
        # 가까운 것을 빨리 찾는 그래프(HNSW · 코사인). 1,024차원 자리 하나가 4KB 라 색인 한
        # 쪽에 하나씩 든다 — 조각 하나에 약 8KB(ADR 0050 실측).
        Index(
            "ix_object_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": f"{vectors.SCHEMA}.vector_cosine_ops"},
        ),
        # 같은 글은 다시 바꾸지 않고 자리를 옮겨 쓴다 — 기록은 같은 증상 글이 되풀이된다.
        Index("ix_object_embeddings_text_hash", "text_hash"),
    )

    object_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="CASCADE"), primary_key=True
    )
    part: Mapped[str] = mapped_column(String(200), primary_key=True, default="")
    type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("object_types.id", ondelete="CASCADE"), index=True
    )
    heading: Mapped[str] = mapped_column(String(300), default="", server_default="")
    anchor: Mapped[str] = mapped_column(String(300), default="", server_default="")
    text_hash: Mapped[str] = mapped_column(String(64))
    """모델 이름 + 조각 글의 sha256 — 같으면 다시 바꾸지 않는다. 모델을 바꾸면 전부 다르다."""
    model: Mapped[str] = mapped_column(String(120))
    embedded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    embedding: Mapped[list[float]] = mapped_column(vectors.Vector())


class SemanticMark(Base):
    """객체를 **마지막으로 맞춰 본 때**(ADR 0054) — 조각이 하나도 없는 객체(고른 칸이 다 빈
    기록)도 「맞췄다」 를 알아야 5분마다 같은 것을 다시 보지 않는다. 객체의 `updated_at` 이
    이보다 나중이면
    다시 볼 차례다. 대상의 칸을 바꾸면 그 타입의 표시를 지운다(전부 다시)."""

    __tablename__ = "semantic_marks"

    object_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("objects.id", ondelete="CASCADE"), primary_key=True
    )
    type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("object_types.id", ondelete="CASCADE"), index=True
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    model: Mapped[str] = mapped_column(String(120))
