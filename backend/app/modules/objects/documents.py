"""문서 속성(ADR 0053) — 객체에 붙는 **마크다운 문서**(백서 · 위키 페이지)와 그 판.

    GET /api/objects/{type}/{id}/documents/{key}                   지금 본문 · 판
    PUT /api/objects/{type}/{id}/documents/{key}                   고치기(판 하나 오름)
    GET /api/objects/{type}/{id}/documents/{key}/revisions         판 목록
    GET /api/objects/{type}/{id}/documents/{key}/revisions/{n}     옛 판 본문
    GET /api/objects/{type}/{id}/mentions                          이 객체를 언급한 문서

## 판 — 사람과 AI 가 같은 문서를 고친다

고칠 때 **보고 고친 판**(`base_revision`)을 함께 보낸다. 그사이 누가 먼저 고쳤으면 늦게 온 쪽은
「먼저 바뀌었습니다」(409)를 받는다 — 말없이 덮으면 AI(MCP)가 백서를 다시 넣는 동안 사람이 고친
문장이 사라진다. 판마다 **누가 · 어느 토큰으로**(MCP 면 그 토큰 이름) 고쳤는지 남는다 — AI 가
쓴 판을 사람이 검토하는 흐름의 근거다. 본문이 그대로면 판을 올리지 않는다.

## 언급 — 문서가 온톨로지와 이어진다

본문의 링크 `[N12 카메라](/o/part/<id>)` 를 저장할 때 뽑아 둔다. 가리켜진 객체의 상세가 「이
객체를 언급한 문서」 로 모은다. MCP 로 쓰는 AI 는 객체 id 를 알므로 이 모양으로 쓴다(안내문
「문서」).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.objects import access
from app.modules.objects.models import (
    ObjectDocument,
    ObjectDocumentMention,
    ObjectDocumentRevision,
    ObjectInstance,
)
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import audit
from app.shared.auth import current_user
from app.shared.errors import Conflict, NotFound, code
from app.shared.permissions import visible_owner_clause
from app.shared.request_context import get_actor_token

router = APIRouter(prefix="/objects", tags=["objects"])

#: 본문의 상한 — 백서 한 편(100쪽)이 20만 자 남짓이다. 한글 100만 자는 약 3MB.
MAX_CHARS = 1_000_000
#: 본문 속 객체 링크 — `/o/<타입 slug>/<객체 id>`(마크다운 링크 · 맨 주소 모두).
MENTION = re.compile(
    r"/o/([a-z][a-z0-9_]*)/([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})"
)


class DocumentOut(BaseModel):
    key: str
    label: str
    body: str
    revision: int
    """0 이면 아직 없다(빈 문서)."""
    chars: int
    updated_at: datetime | None
    updated_by_name: str | None
    updated_via: str
    """토큰으로 고쳤으면 그 이름(MCP · AI) — 화면에서 고쳤으면 빈 글자."""
    can_edit: bool


class DocumentPutIn(BaseModel):
    body: str = Field(max_length=MAX_CHARS)
    base_revision: int = Field(ge=0)
    """보고 고친 판 — 그사이 바뀌었으면 409. 처음 쓰면 0."""
    note: str = Field(default="", max_length=200)
    """고친 까닭 한 줄 — 「백서 v2 반영」."""


class DocumentRevisionOut(BaseModel):
    revision: int
    chars: int
    created_at: datetime
    created_by_name: str | None
    via: str
    note: str


class DocumentRevisionBodyOut(DocumentRevisionOut):
    body: str


class MentionOut(BaseModel):
    """이 객체를 언급한 문서 하나."""

    object_id: uuid.UUID
    type_slug: str
    type_label: str
    label: str
    key: str
    """문서 속성의 키."""
    property_label: str


def _type(db: Session, slug: str) -> ObjectType:
    row = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if row is None:
        raise NotFound(code("OBJECTS", 10), f"타입을 찾을 수 없습니다: {slug}")
    return row


def _visible(
    db: Session, user: User, object_type: ObjectType, object_id: uuid.UUID
) -> ObjectInstance:
    row = db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == object_id,
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )
    if row is None:
        # 없는 것과 안 보이는 것을 같은 말로 — 상세(`routes._visible`)와 같다.
        raise NotFound(code("OBJECTS", 11), "객체를 찾을 수 없습니다.")
    return row


def _definition(db: Session, object_type: ObjectType, key: str) -> PropertyDef:
    found = db.scalar(
        select(PropertyDef).where(
            PropertyDef.owner_kind == "type",
            PropertyDef.owner_id == object_type.id,
            PropertyDef.key == key,
        )
    )
    if found is None or found.data_type != "document":
        raise NotFound(
            code("OBJECTS", 148), f"{object_type.label}에 문서 속성 {key} 가 없습니다."
        )
    return found


def _names(db: Session, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = [one for one in ids if one is not None]
    if not wanted:
        return {}
    return {
        row.id: row.display_name for row in db.scalars(select(User).where(User.id.in_(wanted)))
    }


def _out(
    db: Session, definition: PropertyDef, found: ObjectDocument | None, can_edit: bool
) -> DocumentOut:
    if found is None:
        return DocumentOut(
            key=definition.key,
            label=definition.label,
            body="",
            revision=0,
            chars=0,
            updated_at=None,
            updated_by_name=None,
            updated_via="",
            can_edit=can_edit,
        )
    names = _names(db, {found.updated_by_id})
    return DocumentOut(
        key=definition.key,
        label=definition.label,
        body=found.body,
        revision=found.revision,
        chars=found.chars,
        updated_at=found.updated_at,
        updated_by_name=names.get(found.updated_by_id) if found.updated_by_id else None,
        updated_via=found.updated_via,
        can_edit=can_edit,
    )


def mentions_in(db: Session, body: str, *, owner_id: uuid.UUID) -> set[uuid.UUID]:
    """본문이 가리키는 **있는** 객체들 — 자기 자신 · 지운 것은 뺀다."""
    pairs: dict[uuid.UUID, str] = {}
    for slug, raw in MENTION.findall(body):
        pairs[uuid.UUID(raw)] = slug
    pairs.pop(owner_id, None)
    if not pairs:
        return set()
    # 주소의 타입이 실제 타입과 다르면(인터페이스 주소) 그래도 같은 객체다 — id 로만 본다.
    return set(
        db.scalars(
            select(ObjectInstance.id).where(
                ObjectInstance.id.in_(list(pairs)), ObjectInstance.deleted_at.is_(None)
            )
        )
    )


@router.get("/{type_slug}/{object_id}/documents/{key}", response_model=DocumentOut)
def get_document(
    type_slug: str,
    object_id: uuid.UUID,
    key: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DocumentOut:
    """지금 본문 — 아직 안 썼으면 빈 문서(판 0)."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    definition = _definition(db, object_type, key)
    found = db.get(ObjectDocument, (row.id, key))
    return _out(db, definition, found, access.can_edit(db, user, object_type, row))


@router.put("/{type_slug}/{object_id}/documents/{key}", response_model=DocumentOut)
def put_document(
    type_slug: str,
    object_id: uuid.UUID,
    key: str,
    payload: DocumentPutIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DocumentOut:
    """본문을 통째로 바꾼다 — 판이 하나 오르고 그 판이 남는다. 그사이 누가 먼저 고쳤으면 409
    (`details.revision` 이 지금 판). 본문이 그대로면 판을 올리지 않는다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    access.require_edit(db, user, object_type, row, code_value=code("OBJECTS", 16))
    definition = _definition(db, object_type, key)
    found = db.scalar(
        select(ObjectDocument)
        .where(ObjectDocument.object_id == row.id, ObjectDocument.key == key)
        .with_for_update()
    )
    now_revision = found.revision if found is not None else 0
    if payload.base_revision != now_revision:
        raise Conflict(
            code("OBJECTS", 149),
            f"{definition.label}: 다른 곳에서 먼저 수정했습니다(지금 판 {now_revision}) — "
            "새 판을 불러와 다시 적용하세요.",
            details={"revision": now_revision},
        )
    # 상한은 요청 모양(`max_length`)이 본다 — 줄바꿈을 맞추면 짧아지기만 한다.
    body = payload.body.replace("\r\n", "\n")
    if found is not None and found.body == body:
        return _out(db, definition, found, True)

    now = datetime.now(UTC)
    via = get_actor_token() or ""
    if found is None:
        found = ObjectDocument(object_id=row.id, key=key)
        db.add(found)
    found.body = body
    found.revision = now_revision + 1
    found.chars = len(body)
    found.updated_at = now
    found.updated_by_id = user.id
    found.updated_via = via
    db.add(
        ObjectDocumentRevision(
            object_id=row.id,
            key=key,
            revision=found.revision,
            body=body,
            chars=len(body),
            created_at=now,
            created_by_id=user.id,
            via=via,
            note=payload.note.strip(),
        )
    )
    db.execute(
        delete(ObjectDocumentMention).where(
            ObjectDocumentMention.object_id == row.id, ObjectDocumentMention.key == key
        )
    )
    for target in mentions_in(db, body, owner_id=row.id):
        db.add(ObjectDocumentMention(object_id=row.id, key=key, target_id=target))
    # 객체가 바뀐 것으로 친다 — 「지난번 이후 바뀐 것」 · 뜻으로 찾기의 차례가 이것을 본다.
    row.updated_at = now
    audit.record(
        db,
        action="object.document",
        actor=user,
        target_table="objects",
        target_id=row.id,
        target_label=row.label,
        workspace_id=row.owner_workspace_id,
        changes={
            "key": key,
            "revision": found.revision,
            "chars": found.chars,
            "via": via,
            "note": payload.note.strip(),
        },
    )
    db.commit()
    db.refresh(found)
    return _out(db, definition, found, True)


@router.get(
    "/{type_slug}/{object_id}/documents/{key}/revisions",
    response_model=list[DocumentRevisionOut],
)
def list_revisions(
    type_slug: str,
    object_id: uuid.UUID,
    key: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[DocumentRevisionOut]:
    """판 목록 — 새 판부터. 본문은 싣지 않는다(판 하나를 열 때 받는다)."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    _definition(db, object_type, key)
    rows = list(
        db.scalars(
            select(ObjectDocumentRevision)
            .where(
                ObjectDocumentRevision.object_id == row.id, ObjectDocumentRevision.key == key
            )
            .order_by(ObjectDocumentRevision.revision.desc())
        )
    )
    names = _names(db, {one.created_by_id for one in rows})
    return [
        DocumentRevisionOut(
            revision=one.revision,
            chars=one.chars,
            created_at=one.created_at,
            created_by_name=names.get(one.created_by_id) if one.created_by_id else None,
            via=one.via,
            note=one.note,
        )
        for one in rows
    ]


@router.get(
    "/{type_slug}/{object_id}/documents/{key}/revisions/{revision}",
    response_model=DocumentRevisionBodyOut,
)
def get_revision(
    type_slug: str,
    object_id: uuid.UUID,
    key: str,
    revision: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> DocumentRevisionBodyOut:
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    _definition(db, object_type, key)
    found = db.scalar(
        select(ObjectDocumentRevision).where(
            ObjectDocumentRevision.object_id == row.id,
            ObjectDocumentRevision.key == key,
            ObjectDocumentRevision.revision == revision,
        )
    )
    if found is None:
        raise NotFound(code("OBJECTS", 151), f"판 {revision} 이 없습니다.")
    names = _names(db, {found.created_by_id})
    return DocumentRevisionBodyOut(
        revision=found.revision,
        chars=found.chars,
        created_at=found.created_at,
        created_by_name=names.get(found.created_by_id) if found.created_by_id else None,
        via=found.via,
        note=found.note,
        body=found.body,
    )


@router.get("/{type_slug}/{object_id}/mentions", response_model=list[MentionOut])
def mentions(
    type_slug: str,
    object_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[MentionOut]:
    """이 객체를 언급한 문서 — **볼 수 있는 것만**, 이름 순. 원 표(부서 · 계정) 객체는 행이
    없어 링크가 있어도 여기 안 모인다."""
    object_type = _type(db, type_slug)
    row = _visible(db, user, object_type, object_id)
    found = db.execute(
        select(ObjectDocumentMention.key, ObjectInstance, ObjectType)
        .join(ObjectInstance, ObjectInstance.id == ObjectDocumentMention.object_id)
        .join(ObjectType, ObjectType.id == ObjectInstance.type_id)
        .where(
            ObjectDocumentMention.target_id == row.id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
        .order_by(ObjectInstance.label, ObjectDocumentMention.key)
    ).tuples()
    out: list[MentionOut] = []
    labels: dict[tuple[uuid.UUID, str], str] = {}
    for key, source, source_type in found:
        if (source_type.id, key) not in labels:
            definition = db.scalar(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "type",
                    PropertyDef.owner_id == source_type.id,
                    PropertyDef.key == key,
                )
            )
            labels[(source_type.id, key)] = definition.label if definition else key
        out.append(
            MentionOut(
                object_id=source.id,
                type_slug=source_type.slug,
                type_label=source_type.label,
                label=source.label,
                key=key,
                property_label=labels[(source_type.id, key)],
            )
        )
    return out
