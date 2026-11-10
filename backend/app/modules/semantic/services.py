"""뜻으로 찾기(ADR 0052 · 0054) — 대상 타입의 객체를 뜻의 자리로 바꿔 두고, 가까운 것을 찾는다.

## 무엇을 뜻으로 바꾸나 — 조각

객체 하나가 조각 여럿이 된다(`object_embeddings` 의 `part`).

- **기본 글**(`part = ""`) — 객체를 한 줄로. 축의 「자동」 은 타입 이름 · 이름 · 다른 이름 ·
  설명 · 글 · 선택 칸 · 참조 칸이 가리키는 것의 이름 전부(「부품: N12 카메라 · 부품군: 카메라 ·
  모델: N12」). 기록은 **고른 칸만**(ADR 0054 — 「고객 증상: 모서리가 찍혔어요 · 수리 내역:
  …」). 숫자 · 날짜 · 주소 · 파일은 뜻이 없는 값이라 넣지 않는다.
- **문서의 절**(`part = "doc:<칸>:<번호>"`) — 문서 속성(ADR 0053)의 본문을 제목(h1 ~ h3)으로
  나눈 것. 길면 문단 단위로 더 나눈다. 절의 경로(`heading`)와 제목 id(`anchor`)를 함께 둔다 —
  찾은 줄이 그 절로 바로 간다.

## 어느 타입 · 어느 칸 — 대상

축은 일괄로 켜고(칸은 「자동」), 기록은 **칸을 골라** 켠다 — 시장 서비스 13.6만 건을 전부
바꾸면 GPU 없는 서버에서 끝나지 않는다. 고르는 칸은 글 · 여러 줄 글 · 선택 · 참조 · 문서와
이름(`label`).

## 언제 바꾸나 — 워커

대상을 바꾸면 곧장, 그 뒤로는 5분마다 「새것 · 고친 것」 이 있으면, 하루 한 번은 전부를 해시로
맞춘다(다른 이름 · 가리키는 것의 이름이 바뀌어도 객체의 `updated_at` 은 안 바뀐다). 객체마다
맞춰 본 때(`semantic_marks`)를 남긴다 — 조각이 없는 객체도 다시 보지 않게. **같은 글은 다시
바꾸지 않는다** — 이미 있는 조각의 자리를 옮겨 쓴다(기록은 같은 증상 글이 되풀이된다).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Float,
    bindparam,
    delete,
    exists,
    func,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.files import photomarks
from app.modules.objects import system, typecounts
from app.modules.objects.models import ObjectAlias, ObjectDocument, ObjectInstance
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType, PropertyDef
from app.modules.semantic.models import ObjectEmbedding, SemanticMark, SemanticTarget
from app.shared import audit, embedding, vectors
from app.shared.errors import AppError, code
from app.shared.permissions import visible_owner_clause

log = logging.getLogger(__name__)

SYNC_KIND = "semantic_sync"
#: 이만큼 가까워야(코사인 유사도) 「뜻이 가까운 것」 으로 낸다. 개발 DB 의 축 274개로 잰 값
#: (2026-11-06) — 맞는 짝: 「뒷면 커버」 → 후면 커버 0.66 · 「폴더블」 → 폴드 0.64 ·
#: 「battery」 → 배터리 0.55 · 「물에 빠짐」 → 침수 0.47. 상관없는 말(「날씨」 「회의록」
#: 「점심 메뉴」)의 맨 위가 0.41 ~ 0.43 이었다.
MIN_SIMILARITY = 0.45
#: 검색 화면에 내는 수(객체 수) — 글자로 찾은 줄의 보탬이지 그것을 덮는 목록이 아니다.
NEAR_LIMIT = 8
#: HNSW 가 훑는 후보 수(기본 40). pgvector 0.6 은 걸러 찾을 때 색인을 이어 훑지 못해, 부서 ·
#: 타입으로 걸러지면 덜 나온다 — 이 질의에서만 키운다(ADR 0050 「다음」).
EF_SEARCH = 400
#: 고른 범위의 조각이 이만큼 이하면 색인을 안 쓰고 정확히 잰다 — 작은 타입으로 좁혀도 덜 나오지
#: 않는다. 개발 PC 실측(1024차원 · 캐시에 든 표, 2026-11-07): 2,000개 15ms · 5,200개 27ms ·
#: 1만 개 130ms. 뜻으로 바꾸는 데 드는 Ollama 한 번(CPU 수백 ms)보다 작다.
EXACT_MAX = 10_000
#: 기본 글의 상한 — bge-m3 은 8,192 토큰까지 받지만 긴 글은 뜻이 흐려진다.
TEXT_MAX = 4_000
#: 문서의 절 하나가 이보다 길면 문단 단위로 더 나눈다 — 백서의 한 절이 몇 쪽이면 그 안의 한
#: 가지 이야기가 뜻의 자리에서 묻힌다.
PIECE_MAX = 1_500
#: Ollama 에 한 번에 보내는 글 수 — 개발 PC 실측 128개씩 초당 86개, 32개씩 62개(ADR 0050). GPU
#: 없는 서버에서 한 묶음이 너무 오래 걸리지 않게 작게.
BATCH = 32
#: 한 번에 읽는 객체 수 — 이만큼마다 커밋하고 진행을 알린다(멈추면 거기까지는 남는다).
CHUNK = 500
#: 조각 하나에 드는 자리 — ADR 0050 실측(표 5.7KB + 색인 8.2KB).
KB_PER_OBJECT = 14
#: 하루 한 번은 전부를 해시로 맞춘다.
FULL_EVERY = timedelta(hours=24)
#: 지난 차례가 실패했으면(Ollama 가 꺼졌다 …) 이만큼은 다시 넣지 않는다 — 5분마다 실패를 쌓지
#: 않게.
RETRY_AFTER = timedelta(hours=1)
#: 기본 글에 넣는 칸의 종류.
TEXT_KINDS = frozenset({"text", "text_long", "enum"})
#: 고를 수 있는 칸의 종류 — 기본 글(글 · 선택 · 참조)과 문서의 절.
CHOOSABLE = frozenset({"text", "text_long", "enum", "object_ref", "document"})
#: 고르는 칸 가운데 「이름」.
LABEL_FIELD = "label"
#: 속도를 믿을 만한 계산의 크기 — 이보다 적게 바꾼 계산으로는 초당 몇 개인지 어림하지 않는다.
RATE_MIN_PIECES = 100


# --- 어느 타입 · 어느 칸
# --------------------------------------------------------------------------


def why_not(object_type: ObjectType) -> str | None:
    """대상으로 지정할 수 없는 까닭 — 지정할 수 있으면 None."""
    if system.is_system(object_type):
        return "원 표(부서 · 계정)를 비추는 타입입니다 — 객체 행이 없습니다"
    return None


def choosable(defs: Iterable[PropertyDef]) -> list[PropertyDef]:
    """고를 수 있는 칸 — 정의 차례대로."""
    return [one for one in defs if one.data_type in CHOOSABLE]


def targets(db: Session) -> dict[uuid.UUID, list[str] | None]:
    """대상 타입 → 고른 칸(None 이면 자동)."""
    return {row.type_id: row.fields for row in db.scalars(select(SemanticTarget))}


def target_ids(db: Session) -> set[uuid.UUID]:
    return set(db.scalars(select(SemanticTarget.type_id)))


def set_targets(db: Session, user: User, wanted: Mapping[str, list[str] | None]) -> bool:
    """대상을 통째로 정한다 — 타입 slug → 고른 칸(None 이면 자동, 축만).

    바뀌었으면 기록을 남기고 True(부르는 쪽이 계산을 넣는다). 칸이 바뀐 타입은 맞춰 본 때를
    지운다 — 전부 다시."""
    by_slug = {row.slug: row for row in db.scalars(select(ObjectType))}
    unknown = sorted(set(wanted) - set(by_slug))
    if unknown:
        raise AppError(
            code("SEMANTIC", 1), f"없는 타입입니다: {', '.join(unknown)}", status=422
        )
    refused = {slug: why for slug in wanted if (why := why_not(by_slug[slug]))}
    if refused:
        raise AppError(
            code("SEMANTIC", 2),
            "대상으로 지정할 수 없는 타입이 있습니다 — "
            + " · ".join(
                f"{by_slug[slug].label}: {why}" for slug, why in sorted(refused.items())
            ),
            status=422,
            details={"refused": sorted(refused)},
        )
    chosen: dict[uuid.UUID, list[str] | None] = {}
    for slug, fields in wanted.items():
        object_type = by_slug[slug]
        chosen[object_type.id] = _check_fields(db, object_type, fields)

    now = targets(db)
    if chosen == now:
        return False
    db.execute(delete(SemanticTarget).where(SemanticTarget.type_id.not_in(list(chosen))))
    changed = [
        type_id for type_id in chosen if type_id in now and now[type_id] != chosen[type_id]
    ]
    for type_id, fields in chosen.items():
        if type_id not in now:
            db.add(SemanticTarget(type_id=type_id, fields=fields, created_by_id=user.id))
        elif now[type_id] != fields:
            row = db.get(SemanticTarget, type_id)
            assert row is not None
            row.fields = fields
    if changed:
        # 칸이 바뀌면 그 타입의 객체는 전부 다시 — 글이 같은 조각은 해시로 그대로 남는다.
        db.execute(delete(SemanticMark).where(SemanticMark.type_id.in_(changed)))
    slugs = {row.id: row.slug for row in by_slug.values()}
    audit.record(
        db,
        action="semantic.targets",
        actor=user,
        target_table="semantic_targets",
        target_id=None,
        target_label="뜻으로 찾기",
        changes={
            "on": sorted(slugs[one] for one in set(chosen) - set(now)),
            "off": sorted(slugs[one] for one in set(now) - set(chosen)),
            "fields": {
                slugs[one]: chosen[one] for one in sorted(chosen, key=slugs.__getitem__)
            },
        },
    )
    return True


def _check_fields(
    db: Session, object_type: ObjectType, fields: list[str] | None
) -> list[str] | None:
    """고른 칸이 맞는지 — 기록은 반드시 고르고, 없는 칸 · 고를 수 없는 종류는 거절한다."""
    if fields is None:
        if object_type.usage == "log":
            raise AppError(
                code("SEMANTIC", 4),
                f"{object_type.label}: 기록은 뜻으로 찾을 칸을 선택합니다 — "
                "건수가 많아 전부를 계산하면 서버가 감당하지 못합니다.",
                status=422,
            )
        return None
    picked = list(dict.fromkeys(one.strip() for one in fields if one.strip()))
    if not picked:
        raise AppError(
            code("SEMANTIC", 4), f"{object_type.label}: 칸을 하나 이상 선택합니다.", status=422
        )
    defs = {one.key: one for one in choosable(properties_of(db, object_type.id))}
    wrong = [one for one in picked if one != LABEL_FIELD and one not in defs]
    if wrong:
        raise AppError(
            code("SEMANTIC", 5),
            f"{object_type.label}: 선택할 수 없는 칸입니다 — {', '.join(wrong)}"
            "(글 · 여러 줄 글 · "
            "선택 · 참조 · 문서 칸과 이름만 됩니다)",
            status=422,
            details={"fields": wrong},
        )
    return picked


# --- 조각
# -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    part: str
    text: str
    heading: str = ""
    anchor: str = ""


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _values(
    definition: PropertyDef, value: Any, ref_labels: Mapping[uuid.UUID, str]
) -> list[str]:
    items = value if isinstance(value, list) else [value]
    if definition.data_type in TEXT_KINDS:
        return [str(item).strip() for item in items if item is not None and str(item).strip()]
    if definition.data_type == "object_ref":
        ids = [found for item in items if (found := _uuid(item)) is not None]
        return [label for one_id in ids if (label := ref_labels.get(one_id, ""))]
    return []


def basic_text(
    object_type: ObjectType,
    row: ObjectInstance,
    defs: list[PropertyDef],
    ref_labels: Mapping[uuid.UUID, str],
    aliases: list[str],
    fields: list[str] | None,
) -> str:
    """객체 하나의 기본 글 — 자동이면 「부품: N12 카메라 · 부품군: 카메라 · 모델: N12」, 칸을
    골랐으면 그 칸만(「시장 서비스 · 고객 증상: 모서리가 찍혔어요」). 고른 칸이 다 비면 빈
    글자."""
    values = row.properties or {}
    if fields is None:
        parts = [f"{object_type.label}: {row.label}"]
        if aliases:
            parts.append("다른 이름: " + ", ".join(aliases))
        if row.description and row.description.strip():
            parts.append(row.description.strip())
        for one in defs:
            shown = _values(one, values.get(one.key), ref_labels)
            if shown:
                parts.append(f"{one.label}: {', '.join(shown)}")
        return " · ".join(parts)[:TEXT_MAX]

    by_key = {one.key: one for one in defs}
    parts = []
    for key in fields:
        if key == LABEL_FIELD:
            parts.append(row.label)
            if aliases:
                parts.append("다른 이름: " + ", ".join(aliases))
            continue
        definition = by_key.get(key)
        if definition is None or definition.data_type == "document":
            continue
        shown = _values(definition, values.get(key), ref_labels)
        if shown:
            parts.append(f"{definition.label}: {', '.join(shown)}")
    if not parts:
        return ""
    return f"{object_type.label} · " + " · ".join(parts)[:TEXT_MAX]


_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def plain_heading(raw: str) -> str:
    """제목의 글자 — 화면(`MarkdownView`)의 제목 `textContent` 와 같게: 링크 · 그림은 글자만,
    굵게 · 기울임 · 코드 · 지움 표시는 뺀다."""
    shown = _LINK.sub(r"\1", raw)
    shown = re.sub(r"[*`~]", "", shown)
    return re.sub(r"\s+", " ", shown).strip()


def slug_of(text_value: str) -> str:
    """제목 id — 화면의 `slugOf` 와 **같은 규칙**(글자 · 숫자 말고는 「-」, 앞뒤 「-」
    빼기)."""
    return re.sub(r"[\W_]+", "-", text_value.strip().lower()).strip("-") or "section"


def _chunks(body: str) -> list[str]:
    """절의 글을 `PIECE_MAX` 안으로 — 문단(빈 줄)마다 모으고, 문단 하나가 넘으면 그것을
    자른다."""
    if len(body) <= PIECE_MAX:
        return [body]
    out: list[str] = []
    current = ""
    for paragraph in re.split(r"\n[ \t]*\n", body):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > PIECE_MAX:
            if current:
                out.append(current)
                current = ""
            out.append(paragraph[:PIECE_MAX])
            paragraph = paragraph[PIECE_MAX:]
        if current and len(current) + 2 + len(paragraph) > PIECE_MAX:
            out.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        out.append(current)
    return out


@dataclass
class Section:
    heading: str
    """절의 경로 — 「3. 결과 › 3.1 모서리 찍힘」. 첫 제목 앞의 글은 빈 글자."""
    anchor: str
    text: str


def sections(body: str) -> list[Section]:
    """마크다운을 제목(h1 ~ h3)으로 나눈다 — 코드 울타리 안의 「#」 은 제목이 아니다.

    제목 id 는 화면과 같이 h1 ~ h3 전부를 차례로 세어 같은 것이 또 나오면 「-2」 를 붙인다."""
    used: dict[str, int] = {}
    path: list[tuple[int, str]] = []
    out: list[Section] = []
    lines: list[str] = []
    heading, anchor = "", ""
    fenced = False

    def flush() -> None:
        content = "\n".join(lines).strip()
        if content:
            out.append(Section(heading=heading, anchor=anchor, text=content))

    for line in body.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            lines.append(line)
            continue
        found = None if fenced else _HEADING.match(line)
        if found is None or len(found.group(1)) > 3:
            lines.append(line)
            continue
        flush()
        lines = []
        level = len(found.group(1))
        title = plain_heading(found.group(2))
        slug = slug_of(title)
        used[slug] = used.get(slug, 0) + 1
        anchor = slug if used[slug] == 1 else f"{slug}-{used[slug]}"
        path = [one for one in path if one[0] < level] + [(level, title)]
        heading = " › ".join(one[1] for one in path)
    flush()
    return out


def document_pieces(row: ObjectInstance, key: str, body: str) -> list[Piece]:
    """문서 하나의 조각 — 절마다(길면 더 나눠서), 글 앞에 「객체 이름 › 절의 경로」."""
    out: list[Piece] = []
    for section in sections(body):
        head = f"{row.label} › {section.heading}" if section.heading else row.label
        # 그림 표시(ADR 0056)는 캡션만 남긴다 — 첨부 id 36자가 조각의 뜻을 흐린다. 그림만 있는
        # 절은 조각이 없다(찾을 글이 없다).
        text_value = photomarks.readable(section.text).strip()
        if not text_value:
            continue
        for chunk in _chunks(text_value):
            out.append(
                Piece(
                    part=f"doc:{key}:{len(out)}",
                    text=f"{head}\n{chunk}",
                    heading=section.heading,
                    anchor=section.anchor,
                )
            )
    return out


def pieces_of(
    object_type: ObjectType,
    row: ObjectInstance,
    defs: list[PropertyDef],
    ref_labels: Mapping[uuid.UUID, str],
    aliases: list[str],
    fields: list[str] | None,
    documents: Mapping[str, str],
) -> list[Piece]:
    """객체 하나의 조각 전부 — 기본 글(비면 없음) + 대상인 문서의 절들."""
    out: list[Piece] = []
    basic = basic_text(object_type, row, defs, ref_labels, aliases, fields)
    if basic:
        out.append(Piece(part="", text=basic))
    document_keys = [
        one.key
        for one in defs
        if one.data_type == "document" and (fields is None or one.key in fields)
    ]
    for key in document_keys:
        body = documents.get(key, "")
        if body.strip():
            out.extend(document_pieces(row, key, body))
    return out


def text_hash(model: str, body: str) -> str:
    return hashlib.sha256(f"{model}\n{body}".encode()).hexdigest()


# --- 계산(워커)
# ----------------------------------------------------------------------------------


def _stale_clause(model: str) -> Any:
    """맞춰 본 적이 없거나, 그 뒤에 객체가 고쳐졌거나, 모델이 바뀌었다."""
    return or_(
        SemanticMark.object_id.is_(None),
        ObjectInstance.updated_at > SemanticMark.synced_at,
        SemanticMark.model != model,
    )


def _orphans(table: Any) -> Any:
    """대상에서 뺀 타입 · 지운 객체의 줄."""
    return or_(
        table.type_id.not_in(select(SemanticTarget.type_id)),
        table.object_id.in_(
            select(ObjectInstance.id).where(ObjectInstance.deleted_at.is_not(None))
        ),
    )


def run_sync(
    db: Session,
    *,
    full: bool,
    progress: Callable[[str, int, int], None],
    commit: Callable[[], None],
) -> dict[str, Any]:
    """대상 타입의 조각을 맞춘다 — `full` 이면 전부를 해시로, 아니면 새것 · 고친 것만.

    **묶음마다 커밋한다** — 멈추면(취소 · 워커 재시작) 거기까지는 남고 다음 차례가 이어 간다.
    """
    model = get_settings().embedding_model
    gone = db.execute(delete(ObjectEmbedding).where(_orphans(ObjectEmbedding)))
    db.execute(delete(SemanticMark).where(_orphans(SemanticMark)))
    removed = int(getattr(gone, "rowcount", 0) or 0)
    chosen = targets(db)
    types = {
        row.id: row
        for row in db.scalars(select(ObjectType).where(ObjectType.id.in_(list(chosen))))
    }
    counted = {
        "types": len(types),
        "seen": 0,
        "embedded": 0,
        "reused": 0,
        "unchanged": 0,
        "removed": removed,
    }
    commit()
    if not types:
        return counted

    def candidates(type_id: uuid.UUID) -> Any:
        query = (
            select(ObjectInstance)
            .outerjoin(SemanticMark, SemanticMark.object_id == ObjectInstance.id)
            .where(ObjectInstance.type_id == type_id, ObjectInstance.deleted_at.is_(None))
        )
        if not full:
            query = query.where(_stale_clause(model))
        return query

    total = sum(
        db.scalar(select(func.count()).select_from(candidates(one).subquery())) or 0
        for one in types
    )
    progress("뜻 계산", 0, total)
    for type_id, object_type in types.items():
        defs = properties_of(db, type_id)
        fields = chosen[type_id]
        after: uuid.UUID | None = None
        while True:
            query = candidates(type_id).order_by(ObjectInstance.id).limit(CHUNK)
            if after is not None:
                query = query.where(ObjectInstance.id > after)
            rows = list(db.scalars(query))
            if not rows:
                break
            after = rows[-1].id
            done = _sync_chunk(db, object_type, defs, fields, rows, model)
            counted["seen"] += len(rows)
            for name in ("embedded", "reused", "unchanged", "removed"):
                counted[name] += done[name]
            commit()
            progress("뜻 계산", counted["seen"], total)
    return counted


def _sync_chunk(
    db: Session,
    object_type: ObjectType,
    defs: list[PropertyDef],
    fields: list[str] | None,
    rows: list[ObjectInstance],
    model: str,
) -> dict[str, int]:
    """한 묶음 — 글이 바뀐 조각만 Ollama 에 보낸다(같은 글이 이미 있으면 그 자리를 옮겨
    쓴다)."""
    ids = [row.id for row in rows]
    labels = system.ref_labels(db, defs, [row.properties or {} for row in rows])
    names: dict[uuid.UUID, list[str]] = {}
    if fields is None or LABEL_FIELD in fields:
        for alias in db.scalars(
            select(ObjectAlias)
            .where(ObjectAlias.object_id.in_(ids), ObjectAlias.kind == "alias")
            .order_by(ObjectAlias.value)
        ):
            names.setdefault(alias.object_id, []).append(alias.value)
    document_keys = [
        one.key
        for one in defs
        if one.data_type == "document" and (fields is None or one.key in fields)
    ]
    bodies: dict[uuid.UUID, dict[str, str]] = {}
    if document_keys:
        for doc in db.scalars(
            select(ObjectDocument).where(
                ObjectDocument.object_id.in_(ids), ObjectDocument.key.in_(document_keys)
            )
        ):
            bodies.setdefault(doc.object_id, {})[doc.key] = doc.body

    known: dict[tuple[uuid.UUID, str], str] = {
        (one_id, part): digest
        for one_id, part, digest in db.execute(
            select(
                ObjectEmbedding.object_id, ObjectEmbedding.part, ObjectEmbedding.text_hash
            ).where(ObjectEmbedding.object_id.in_(ids))
        ).tuples()
    }
    wanted: dict[tuple[uuid.UUID, str], tuple[ObjectInstance, Piece, str]] = {}
    for row in rows:
        for piece in pieces_of(
            object_type,
            row,
            defs,
            labels,
            names.get(row.id, []),
            fields,
            bodies.get(row.id, {}),
        ):
            wanted[(row.id, piece.part)] = (row, piece, text_hash(model, piece.text))

    stale = [one for one in known if one not in wanted]
    if stale:
        db.execute(
            delete(ObjectEmbedding).where(
                tuple_(ObjectEmbedding.object_id, ObjectEmbedding.part).in_(stale)
            )
        )
    changed = {key: value for key, value in wanted.items() if known.get(key) != value[2]}
    counted = {
        "embedded": 0,
        "reused": 0,
        "unchanged": len(wanted) - len(changed),
        "removed": len(stale),
    }

    # 같은 글의 자리가 이미 있으면 옮겨 쓴다.
    digests = sorted({digest for _row, _piece, digest in changed.values()})
    reuse: dict[str, list[float]] = {}
    if digests:
        for digest, at in db.execute(
            select(ObjectEmbedding.text_hash, ObjectEmbedding.embedding)
            .where(ObjectEmbedding.text_hash.in_(digests))
            .distinct(ObjectEmbedding.text_hash)
        ).tuples():
            reuse[digest] = at
    todo = [key for key, (_row, _piece, digest) in changed.items() if digest not in reuse]
    found: dict[str, list[float]] = dict(reuse)
    for start in range(0, len(todo), BATCH):
        part = todo[start : start + BATCH]
        texts: dict[str, str] = {}
        for key in part:
            _row, piece, digest = changed[key]
            texts.setdefault(digest, piece.text)
        missing = [digest for digest in texts if digest not in found]
        for digest, at in zip(
            missing, embedding.embed([texts[one] for one in missing]), strict=True
        ):
            found[digest] = at
        counted["embedded"] += len(missing)
    # 새로 바꾼 것은 서로 다른 글의 수다 — 나머지(이미 있던 글 · 이 묶음 안의 같은 글)는 옮겨
    # 썼다.
    counted["reused"] = len(changed) - counted["embedded"]

    now = datetime.now(UTC)
    values = [
        {
            "object_id": row.id,
            "part": piece.part,
            "type_id": row.type_id,
            "heading": piece.heading[:300],
            "anchor": piece.anchor[:300],
            "text_hash": digest,
            "model": model,
            "embedded_at": now,
            "embedding": found[digest],
        }
        for (row, piece, digest) in changed.values()
    ]
    for start in range(0, len(values), BATCH):
        statement = insert(ObjectEmbedding).values(values[start : start + BATCH])
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[ObjectEmbedding.object_id, ObjectEmbedding.part],
                set_={
                    name: getattr(statement.excluded, name)
                    for name in (
                        "type_id",
                        "heading",
                        "anchor",
                        "text_hash",
                        "model",
                        "embedded_at",
                        "embedding",
                    )
                },
            )
        )
    marks = insert(SemanticMark).values(
        [
            {"object_id": row.id, "type_id": row.type_id, "synced_at": now, "model": model}
            for row in rows
        ]
    )
    db.execute(
        marks.on_conflict_do_update(
            index_elements=[SemanticMark.object_id],
            set_={
                "type_id": marks.excluded.type_id,
                "synced_at": marks.excluded.synced_at,
                "model": marks.excluded.model,
            },
        )
    )
    return counted


# --- 차례(타이머)
# ---------------------------------------------------------------------------------


def _busy(db: Session) -> bool:
    from app.modules.jobs.models import Job

    return bool(
        db.scalar(
            select(Job.id).where(Job.kind == SYNC_KIND, Job.status.in_(("queued", "running")))
        )
    )


def _last(db: Session, statuses: tuple[str, ...] = ("done", "failed", "cancelled")) -> Any:
    """가장 최근에 끝난 계산 — `statuses` 로 좁힌다(「done」 이면 마지막으로 성공한 것)."""
    from app.modules.jobs.models import Job

    return db.scalar(
        select(Job)
        .where(Job.kind == SYNC_KIND, Job.status.in_(statuses))
        .order_by(Job.created_at.desc())
        .limit(1)
    )


def _when(job: Any) -> datetime | None:
    return (job.finished_at or job.created_at) if job is not None else None


def needs_work(db: Session) -> bool:
    """새것 · 고친 것 · 모델이 바뀐 것 · 대상에서 뺀 타입과 지운 객체의 줄 — 하나라도 있나."""
    model = get_settings().embedding_model
    stale = (
        select(ObjectInstance.id)
        .join(SemanticTarget, SemanticTarget.type_id == ObjectInstance.type_id)
        .outerjoin(SemanticMark, SemanticMark.object_id == ObjectInstance.id)
        .where(ObjectInstance.deleted_at.is_(None), _stale_clause(model))
    )
    return bool(
        db.scalar(select(exists(stale)))
        or db.scalar(select(exists().where(_orphans(ObjectEmbedding))))
        or db.scalar(select(exists().where(_orphans(SemanticMark))))
    )


def enqueue(db: Session, *, full: bool, reason: str, user: User | None) -> Any:
    """계산 한 차례를 넣는다 — 이미 기다리거나 돌면 넣지 않고 None. 부르는 쪽이 커밋한다."""
    from app.modules.jobs import services as job_services

    if _busy(db):
        return None
    return job_services.enqueue(
        db,
        kind=SYNC_KIND,
        params={"full": full, "reason": reason},
        user=user,
        workspace_id=None,
    )


def schedule(db: Session, now: datetime | None = None) -> str | None:
    """**워커가 5분마다 부른다** — 차례면 계산을 넣는다(넣었으면 「full」 · 「changed」).

    두 워커가 같은 때 봐도 하나만 넣는다(자문 잠금). 끔(`EMBEDDING_BACKEND=off`)이면 아무것도
    안 넣고, 지난 차례가 실패했으면 한 시간은 기다린다 — Ollama 가 꺼진 서버에서 5분마다 실패를
    쌓지 않게.
    """
    if not embedding.enabled():
        return None
    if not db.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": "semantic:schedule"}
    ):
        return None
    if _busy(db):
        return None
    now = now or datetime.now(UTC)
    last = _last(db)
    failed_at = _when(last) if last is not None and last.status != "done" else None
    if failed_at is not None and now - failed_at < RETRY_AFTER:
        return None
    # 「전부」 의 차례는 **마지막으로 성공한 때**로 센다 — 처음 차례가 실패했으면 다시 넣을
    # 때도 전부다(실패한 때로 세면 「바뀐 것만」 이 되어 하루를 기다린다).
    succeeded = _when(_last(db, ("done",)))
    chosen = target_ids(db)
    kind: str | None = None
    if chosen and (succeeded is None or now - succeeded >= FULL_EVERY):
        kind = "full"
    elif needs_work(db):
        kind = "changed"
    if kind is None:
        return None
    enqueue(db, full=kind == "full", reason="timer", user=None)
    db.commit()
    return kind


# --- 관리 화면
# ----------------------------------------------------------------------------------


@dataclass
class TypeState:
    object_type: ObjectType
    objects: int
    synced: int
    """맞춰 본 객체 수."""
    pieces: int
    """조각 수(기본 글 + 문서의 절)."""
    pending: int | None
    """대상 타입에서 아직 안 맞춘 수(새것 · 고친 것). 대상이 아니면 None."""
    enabled: bool
    fields: list[str] | None
    options: list[PropertyDef] = field(default_factory=list)
    estimate_pieces: int = 0
    """대상으로 하면(지금 고른 칸으로 — 아니면 자동 · 기록은 칸 하나) 생길 조각 수의 어림."""
    why_not: str | None = None


def _document_chars(db: Session) -> dict[tuple[uuid.UUID, str], int]:
    """(타입, 문서 칸)마다 본문 글자 수의 합 — 절 조각 수를 어림하려고."""
    return {
        (type_id, key): int(total or 0)
        for type_id, key, total in db.execute(
            select(ObjectInstance.type_id, ObjectDocument.key, func.sum(ObjectDocument.chars))
            .join(ObjectInstance, ObjectInstance.id == ObjectDocument.object_id)
            .where(ObjectInstance.deleted_at.is_(None))
            .group_by(ObjectInstance.type_id, ObjectDocument.key)
        ).tuples()
    }


def overview(db: Session) -> list[TypeState]:
    """타입마다 — 객체 수 · 맞춘 수 · 조각 수 · 남은 수 · 대상인가 · 고른 칸 · 어림.

    대상 · 축 · 이름 순."""
    model = get_settings().embedding_model
    chosen = targets(db)
    counted = typecounts.counts(db)
    synced: dict[uuid.UUID, int] = {
        type_id: int(count)
        for type_id, count in db.execute(
            select(SemanticMark.type_id, func.count())
            .where(SemanticMark.model == model)
            .group_by(SemanticMark.type_id)
        ).tuples()
    }
    pieces: dict[uuid.UUID, int] = {
        type_id: int(count)
        for type_id, count in db.execute(
            select(ObjectEmbedding.type_id, func.count()).group_by(ObjectEmbedding.type_id)
        ).tuples()
    }
    pending: dict[uuid.UUID, int] = {
        type_id: int(count)
        for type_id, count in db.execute(
            select(ObjectInstance.type_id, func.count())
            .outerjoin(SemanticMark, SemanticMark.object_id == ObjectInstance.id)
            .where(
                ObjectInstance.type_id.in_(list(chosen)),
                ObjectInstance.deleted_at.is_(None),
                _stale_clause(model),
            )
            .group_by(ObjectInstance.type_id)
        ).tuples()
    }
    chars = _document_chars(db)
    out: list[TypeState] = []
    for row in db.scalars(select(ObjectType).where(ObjectType.is_active.is_(True))):
        if system.is_system(row):
            continue
        options = choosable(properties_of(db, row.id))
        fields = chosen.get(row.id)
        objects = counted.get(row.id, 0)
        out.append(
            TypeState(
                object_type=row,
                objects=objects,
                synced=synced.get(row.id, 0),
                pieces=pieces.get(row.id, 0),
                pending=pending.get(row.id, 0) if row.id in chosen else None,
                enabled=row.id in chosen,
                fields=fields,
                options=options,
                estimate_pieces=_estimate(row, objects, options, fields, chars),
                why_not=why_not(row),
            )
        )
    out.sort(
        key=lambda one: (
            not one.enabled,
            one.object_type.usage == "log",
            one.object_type.label,
        )
    )
    return out


def _estimate(
    object_type: ObjectType,
    objects: int,
    options: list[PropertyDef],
    fields: list[str] | None,
    chars: Mapping[tuple[uuid.UUID, str], int],
) -> int:
    """조각 수의 어림 — 기본 글이 있으면 객체마다 하나, 문서는 글자 수 / 절 크기."""
    if fields is None and object_type.usage == "log":
        fields = [LABEL_FIELD]  # 기록은 칸 하나를 고른 것으로 어림한다
    kinds = {one.key: one.data_type for one in options}
    wanted = [*kinds, LABEL_FIELD] if fields is None else fields
    basic = any(
        one == LABEL_FIELD or kinds.get(one) not in (None, "document") for one in wanted
    )
    documents = sum(
        math.ceil(chars.get((object_type.id, key), 0) / PIECE_MAX)
        for key in wanted
        if kinds.get(key) == "document"
    )
    return (objects if basic else 0) + documents


def last_job(db: Session) -> Any:
    """가장 최근의 계산(돌거나 기다리는 것 포함)."""
    from app.modules.jobs.models import Job

    return db.scalar(
        select(Job).where(Job.kind == SYNC_KIND).order_by(Job.created_at.desc()).limit(1)
    )


def rate(db: Session) -> float | None:
    """지난 계산이 초당 몇 조각을 바꿨나 — 이 서버의 속도(GPU 유무)를 어림한다. 충분히 큰
    계산(`RATE_MIN_PIECES` 조각 이상)을 마친 적이 없으면 None."""
    from app.modules.jobs.models import Job

    for job in db.scalars(
        select(Job)
        .where(Job.kind == SYNC_KIND, Job.status == "done")
        .order_by(Job.created_at.desc())
        .limit(20)
    ):
        made = int((job.result or {}).get("embedded") or 0)
        if made >= RATE_MIN_PIECES and job.started_at and job.finished_at:
            seconds = (job.finished_at - job.started_at).total_seconds()
            if seconds > 0:
                return round(made / seconds, 1)
    return None


# --- 찾기
# ---------------------------------------------------------------------------------------


@dataclass
class Near:
    row: ObjectInstance
    object_type: ObjectType
    similarity: float
    heading: str = ""
    """문서의 절에서 걸렸으면 그 절의 경로 — 기본 글이면 빈 글자."""
    anchor: str = ""
    same: int = 0
    """**글이 똑같은** 다른 객체의 수(볼 수 있는 것) — 기록은 같은 증상 글이 되풀이되어, 그대로
    늘어놓으면 같은 줄이 첫 쪽을 다 차지한다. 하나만 세우고 수로 말한다."""


def nearest(
    db: Session,
    user: User,
    q: str,
    *,
    among: set[uuid.UUID] | None = None,
    exclude: Iterable[uuid.UUID] = (),
    limit: int = NEAR_LIMIT,
) -> tuple[str, list[Near], bool]:
    """찾을 말과 뜻이 가까운 것 — (상태, 줄, 덜 나왔을 수 있나). 객체마다 가장 가까운 조각
    하나.

    상태: ready · off(대상 타입이 없거나 끔) · unavailable. **볼 수 있는 것만**
    (`visible_owner_clause`), 지운 것 · 이미 글자로 찾은 것(`exclude`)은 뺀다. 뜻으로 못
    바꾸면(Ollama 가 꺼졌다 …) 빈 줄과 `unavailable` — 찾기 전체는 멈추지 않는다.

    고른 범위(대상 타입 ∩ `among`)의 조각이 `EXACT_MAX` 이하면 정확히 잰다. 넘으면 HNSW
    색인으로 받는데, pgvector 0.6 은 가까운 조각 `EF_SEARCH` 개를 먼저 받고 그 뒤에 타입 ·
    부서 · 지운 것을 거른다.

    **셋째 값(narrowed)** — 낼 수(`limit`)를 못 채웠는데, 받은 줄이 끝까지 기준 안이고 범위를
    다 봤다고 할 수 없을 때 참이다. 그러면 **안 본 것 중에 가까운 것이 있을 수 있다** — 빈 줄을
    「뜻으로도 없다」 로 읽지 않게 알린다(ADR 0052 「추가」).
    """
    chosen = target_ids(db)
    if among is not None:
        chosen &= among
    if not embedding.enabled() or not chosen:
        return "off", [], False
    try:
        [at] = embedding.embed([q.strip()], seconds=embedding.QUERY_SECONDS)
    except embedding.Unavailable as caught:
        log.warning("뜻으로 찾기를 건너뜁니다: %s", caught)
        return "unavailable", [], False
    distance = ObjectEmbedding.embedding.op(
        f"OPERATOR({vectors.SCHEMA}.<=>)", return_type=Float()
    )(bindparam("at", at, type_=vectors.Vector()))
    skip = list(set(exclude))
    scope: list[Any] = [
        ObjectEmbedding.type_id.in_(list(chosen)),
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    ]
    if skip:
        scope.append(ObjectInstance.id.not_in(skip))
    # **범위가 작으면 정확히 잰다** — 고른 타입의 조각을 `EXACT_MAX` + 1 까지만 센다(`type_id`
    # 색인). 계획을 Postgres 에 맡기면 거리 조건 하나로도 색인 · 정확 계산이 갈려, 같은 좁힌
    # 물음이 어느 날은 다 나오고 어느 날은 덜 나왔다(2026-11-07 검토).
    in_range = db.scalar(
        select(func.count()).select_from(
            select(ObjectEmbedding.object_id)
            .where(ObjectEmbedding.type_id.in_(list(chosen)))
            .limit(EXACT_MAX + 1)
            .subquery()
        )
    )
    exact = int(in_range or 0) <= EXACT_MAX
    if not exact:
        db.execute(text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"))
    # 기준(MIN_SIMILARITY)은 질의에 안 건다 — 받은 줄의 수와 끝 줄의 거리로 「덜 나왔나」 를
    # 알아야 한다.
    found = list(
        db.execute(
            select(
                ObjectEmbedding.object_id,
                ObjectEmbedding.heading,
                ObjectEmbedding.anchor,
                ObjectEmbedding.text_hash,
                distance,
            )
            .join(ObjectInstance, ObjectInstance.id == ObjectEmbedding.object_id)
            .where(*scope)
            # 정확히 잴 때는 색인 차례를 못 쓰게 한다(`+ 0`) — 쓰면 HNSW 가 가까운 것을 받은
            # 뒤에 걸러 덜 나온다.
            .order_by(distance + 0 if exact else distance)
            # 한 객체의 조각이 여럿 · 같은 글의 기록이 수백 걸릴 수 있다 — 넉넉히 받아 객체마다
            # 가장 가까운 조각 하나, 같은 글은 하나만 남긴다.
            .limit(EF_SEARCH)
        ).tuples()
    )
    best: dict[uuid.UUID, tuple[str, str, str, float]] = {}
    seen_text: set[str] = set()
    for object_id, heading, anchor, digest, gap in found:
        similarity = 1 - float(gap)
        if similarity < MIN_SIMILARITY:
            break  # 가까운 것부터 왔다 — 뒤는 모두 기준 밖이다
        if object_id in best or digest in seen_text:
            continue
        seen_text.add(digest)
        best[object_id] = (heading, anchor, digest, similarity)
        if len(best) >= limit:
            break
    # **덜 나왔을 수 있나.** 아닌 셋: 낼 수만큼 다 찼다(안 본 것은 받은 어느 것보다 멀다) · 끝
    # 줄이 기준 밖이다(기준 안은 다 봤다) · 정확히 쟀고 받는 수보다 적게 왔다(범위를 다 봤다).
    # 나머지 — 받은 줄이 같은 글로 찼거나, 색인이 가까운 것을 받은 뒤 걸러 모자랐다 — 는 안 본
    # 가까운 것이 있을 수 있다.
    narrowed = (
        len(best) < limit
        and (not found or 1 - float(found[-1][4]) >= MIN_SIMILARITY)
        and not (exact and len(found) < EF_SEARCH)
    )
    if not best:
        return "ready", [], narrowed
    # 같은 글의 다른 객체 — 볼 수 있는 것만 센다(남의 부서 것이 수로 새지 않게).
    same: dict[str, int] = {
        digest: int(count) - 1
        for digest, count in db.execute(
            select(
                ObjectEmbedding.text_hash, func.count(func.distinct(ObjectEmbedding.object_id))
            )
            .join(ObjectInstance, ObjectInstance.id == ObjectEmbedding.object_id)
            .where(
                ObjectEmbedding.text_hash.in_([one[2] for one in best.values()]),
                ObjectEmbedding.type_id.in_(list(chosen)),
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
            .group_by(ObjectEmbedding.text_hash)
        ).tuples()
    }
    rows = {
        row.id: row
        for row in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(list(best))))
    }
    types = {
        row.id: row
        for row in db.scalars(select(ObjectType).where(ObjectType.id.in_(list(chosen))))
    }
    return (
        "ready",
        [
            Near(
                row=rows[object_id],
                object_type=types[rows[object_id].type_id],
                similarity=round(similarity, 3),
                heading=heading,
                anchor=anchor,
                same=max(same.get(digest, 1), 0),
            )
            for object_id, (heading, anchor, digest, similarity) in best.items()
            if object_id in rows and rows[object_id].type_id in types
        ],
        narrowed,
    )
