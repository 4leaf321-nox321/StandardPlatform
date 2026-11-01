"""제안된 관계 — 「이 둘은 이어져야 할 것 같다」 의 **규칙 한 곳**(ADR 0042).

AI(MCP) · 규칙 · 데이터 소스 · 권한 없는 사람이 선을 바로 만들지 않고 **제안**으로 남기고, 출발
객체를 고칠 수 있는 사람이 **확정** · **거절**한다. 제안은 따로 둔 표(`relation_proposals`)라
선을 고르는 판정(`edges.alive`)에 들지 않는다 — 트리 · 롤업 · 지표 · 코어 API · RDF · 묶음은
제안을 볼 길이 없다.

## 권한

    제안                양 끝을 **볼 수만 있으면** — 권한 없는 사람의 길이 이것이다
    확정 · 거절 · 다시 열기   출발 객체를 고칠 수 있는 사람(「관계 추가」 와 같다 — `access`)
    철회                제안한 사람(시스템 관리자도)

## 만들 때는 모양만, 나머지는 확정할 때

만들 때: 종류가 있고 쓰는가 · 허브가 관리하는 종류가 아닌가 · 양 끝 타입이 종류에 맞나 ·
속성 · 기간. **개수 제약 · 순환 · 기간 겹침은 확정할 때** 본다 — 제안과 확정 사이에 선이
바뀐다. 확정은 `relations.add_edge` 로 화면의 「관계 추가」 와 **같은 길**을 탄다 — 같은 검사 ·
계보 고정 트리거 · 감사(`object.relation.add`). 그 길이 거절하면 까닭을 그대로 실어
OBJECTS-0143 으로 말한다.

## 같은 것이 이미 있으면 — 만들지 않고 돌려준다

    connected        같은 선이 이미 있다 — 기간이 겹치는 같은 선(「관계 추가」 의 「이미 이어져
                     있다」 와 같은 판정, `edges.find_overlap`)
    open             같은 열린 제안이 있다 — 새 확신도가 더 높으면 근거 · 확신도 · 출처를
                     새 것으로
    rejected_before  같은 제안이 거절된 적 있다 — 언제 누가 왜. `again` 일 때만 다시 만든다

## 감사

`relation.propose` · `relation.proposal.update` · `relation.proposal.accept` ·
`relation.proposal.reject` · `relation.proposal.reopen` · `relation.proposal.withdraw` —
바뀐 것에 양 끝 id(`audit.relation_ends`)를 실어 **양 끝 객체의 이력**에 선다.
`object.relation.*` 밖에 둔 까닭: 그 이름을 듣는 웹훅은 「선이 바뀌었다」 로 읽는다 — 제안은
선이 아니다. 확정으로 생긴 선은 `object.relation.add` 를 따로 남긴다(같은 길이라 그대로).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.objects import access, edges, system
from app.modules.objects import relations as rel
from app.modules.objects.models import (
    PROPOSAL_ORIGINS,
    ObjectInstance,
    ObjectRelation,
    RelationProposal,
)
from app.modules.ontology import interfaces, managed
from app.modules.ontology.models import ObjectType, RelationType
from app.modules.ontology.services import InvalidValue, validate_properties
from app.modules.workspaces.models import WorkspaceMember
from app.shared import audit, extensions
from app.shared.errors import AppError, Conflict, Forbidden, NotFound, code
from app.shared.permissions import is_any_manager, visible_owner_clause

TABLE = "relation_proposals"
#: 여러 건 제안 한 번의 상한 — 줄마다 검사(종류 · 끝 · 같은 선)를 돌린다. 넘으면 나눠 보낸다.
MAX_ROWS = 500

Outcome = Literal["created", "open", "connected", "rejected_before", "error"]

ORIGIN_LABELS = {
    "user": "사람",
    "mcp": "AI(MCP)",
    "rule": "규칙",
    "datasource": "데이터 소스",
    "import": "파일 가져오기",
}
STATUS_LABELS = {"proposed": "제안", "accepted": "확정", "rejected": "거절"}


@dataclass
class Spec:
    """제안 하나 — 선 만들기와 같은 칸에 확신도 · 출처를 더한 것."""

    relation: str
    src_id: uuid.UUID
    dst_id: uuid.UUID
    properties: dict[str, Any] = field(default_factory=dict)
    evidence_note: str = ""
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: float | None = None
    origin: str = "user"
    origin_ref: str = ""


@dataclass
class Result:
    """제안 하나의 결과 — 만들었나, 무엇이 이미 있었나."""

    status: Outcome
    message: str
    proposal: RelationProposal | None = None
    relation_id: uuid.UUID | None = None
    """`connected` 면 이미 있는 그 선."""
    updated: bool = False
    """`open` 이고 더 높은 확신도로 근거 · 확신도를 바꿨으면 참."""
    error_code: str | None = None


# --- 읽기 도우미 -------------------------------------------------------------


def _visible(db: Session, user: User, object_id: uuid.UUID) -> ObjectInstance | None:
    """보이고 지우지 않은 객체 — 제안의 끝이 될 수 있는 것."""
    return db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == object_id,
            ObjectInstance.deleted_at.is_(None),
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )


def _seen(db: Session, user: User, object_id: uuid.UUID) -> ObjectInstance | None:
    """보이는 객체 — **지운 것도**(확정을 「삭제됨」 으로 거절하고, 거절은 받으려고)."""
    return db.scalar(
        select(ObjectInstance).where(
            ObjectInstance.id == object_id,
            visible_owner_clause(user, ObjectInstance.owner_workspace_id),
        )
    )


def _open_same(
    db: Session,
    relation: str,
    src_id: uuid.UUID,
    dst_id: uuid.UUID,
    *,
    skip: uuid.UUID | None = None,
) -> RelationProposal | None:
    stmt = select(RelationProposal).where(
        RelationProposal.relation == relation,
        RelationProposal.src_object_id == src_id,
        RelationProposal.dst_object_id == dst_id,
        RelationProposal.status == "proposed",
    )
    if skip is not None:
        stmt = stmt.where(RelationProposal.id != skip)
    return db.scalar(stmt.limit(1))


def _user_label(db: Session, user_id: uuid.UUID | None) -> str:
    if user_id is None:
        return ""
    found = db.get(User, user_id)
    return (found.display_name or found.email) if found else ""


def _when(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "(때 모름)"


def rejected_message(db: Session, row: RelationProposal) -> str:
    """「거절된 적 있음」 의 사람 말 — 언제 누가 왜."""
    who = _user_label(db, row.decided_by_id) or "(알 수 없는 사람)"
    why = row.decision_note.strip() or "까닭을 적지 않음"
    return (
        f"같은 제안이 {_when(row.decided_at)} 에 {who} 이(가) 거절했습니다 — 까닭: {why}. "
        "그래도 다시 제안하려면 again 을 켜서 보내세요."
    )


def _ends(row: RelationProposal, src_label: str, dst_label: str) -> dict[str, Any]:
    """감사의 양 끝 — 객체 이력이 이 키(`src` · `dst`)로 찾는다."""
    return audit.relation_ends(
        row.relation,
        row.src_object_id,
        row.dst_object_id,
        src_label,
        dst_label,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
    )


def _labels(db: Session, row: RelationProposal) -> tuple[str, str]:
    src = db.get(ObjectInstance, row.src_object_id)
    dst = db.get(ObjectInstance, row.dst_object_id)
    return (src.label if src else "", dst.label if dst else "")


def _origin(raw: str) -> str:
    value = (raw or "user").strip()
    if value not in PROPOSAL_ORIGINS:
        raise InvalidValue(
            code("OBJECTS", 139),
            f"제안의 출처는 {', '.join(PROPOSAL_ORIGINS)} 중 하나입니다 — 받은 값: {raw!r}.",
        )
    return value


def _confidence(value: float | None) -> float | None:
    if value is None:
        return None
    if not 0 <= value <= 1:
        raise InvalidValue(
            code("OBJECTS", 139), f"확신도는 0 ~ 1 사이의 수입니다 — 받은 값: {value}."
        )
    return round(float(value), 4)


# --- 제안 ---------------------------------------------------------------------


class _Definitions:
    """한 번의 요청 안에서 정의(관계 종류 · 그 속성 · 타입 · 끝의 인터페이스)를 **한 번만**
    읽는다 — 여러 건 제안 500줄이 줄마다 타입 표 · 인터페이스를 읽으면 줄당 10ms 였다(규모
    실측, ADR 0042). 판정은 단건과 같은 함수(`require_end_types_allowed` ·
    `validate_properties`)다."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._kinds: dict[str, RelationType] = {}
        self._defs: dict[uuid.UUID, list[Any]] = {}
        self._types: dict[uuid.UUID, ObjectType] | None = None
        self._ends: interfaces.Ends | None = None

    def kind(self, slug: str) -> RelationType:
        if slug not in self._kinds:
            self._kinds[slug] = rel.relation_type(self.db, slug)
        return self._kinds[slug]

    def defs(self, kind: RelationType) -> list[Any]:
        if kind.id not in self._defs:
            self._defs[kind.id] = rel.relation_defs(self.db, kind)
        return self._defs[kind.id]

    def type_slug(self, row: ObjectInstance) -> str:
        if self._types is None:
            self._types = {one.id: one for one in self.db.scalars(select(ObjectType))}
        found = self._types.get(row.type_id)
        return found.slug if found else ""

    def ends(self) -> interfaces.Ends:
        if self._ends is None:
            self._ends = interfaces.load_ends(self.db)
        return self._ends


def _ends_visible(
    db: Session, user: User, src_id: uuid.UUID, dst_id: uuid.UUID
) -> dict[uuid.UUID, ObjectInstance]:
    """양 끝 중 보이고 지우지 않은 것 — 한 질의로."""
    return {
        one.id: one
        for one in db.scalars(
            select(ObjectInstance).where(
                ObjectInstance.id.in_([src_id, dst_id]),
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
    }


def _same_proposals(
    db: Session, relation: str, src_id: uuid.UUID, dst_id: uuid.UUID
) -> tuple[RelationProposal | None, RelationProposal | None]:
    """같은 (종류, 출발, 도착)의 열린 제안과 가장 최근에 거절된 제안 — 한 질의로."""
    found = list(
        db.scalars(
            select(RelationProposal)
            .where(
                RelationProposal.relation == relation,
                RelationProposal.src_object_id == src_id,
                RelationProposal.dst_object_id == dst_id,
                RelationProposal.status.in_(("proposed", "rejected")),
            )
            .order_by(RelationProposal.decided_at.desc().nulls_last())
        )
    )
    opened = next((one for one in found if one.status == "proposed"), None)
    rejected = next((one for one in found if one.status == "rejected"), None)
    return opened, rejected


def propose(
    db: Session,
    user: User,
    spec: Spec,
    *,
    again: bool = False,
    definitions: _Definitions | None = None,
    guard: bool = True,
) -> Result:
    """제안 하나 — 모양만 보고 남긴다. **커밋하지 않는다.** 모양이 틀리면 `AppError`.

    권한은 **양 끝을 볼 수 있나** 뿐이다 — 고칠 수 없는 사람이 「이어야 한다」 를 남기는 길이
    이것이다. 같은 선 · 같은 열린 제안 · 거절된 적 있는 같은 제안은 만들지 않고 돌려준다."""
    known = definitions or _Definitions(db)
    seen = _ends_visible(db, user, spec.src_id, spec.dst_id)
    src = seen.get(spec.src_id)
    if src is None:
        raise NotFound(
            code("OBJECTS", 137),
            "제안할 출발 객체를 찾을 수 없습니다 — 볼 수 있고 삭제하지 않은 객체여야 합니다"
            "(부서 · 계정은 제안의 끝이 되지 않습니다).",
        )
    kind = known.kind(spec.relation)
    managed.require_relation_editable(kind)
    valid_from, valid_to = edges.checked_period(kind, spec.valid_from, spec.valid_to)
    origin = _origin(spec.origin)
    confidence = _confidence(spec.confidence)
    dst = seen.get(spec.dst_id)
    if dst is None:
        # 객체가 아니면 원 표(부서 · 계정)인가 — 그쪽 끝은 제안으로 받지 않는다.
        end = system.find_end(db, user, spec.dst_id, allowed=kind.dst_type_slugs)
        if end is not None and end.is_system:
            raise Conflict(
                code("OBJECTS", 138),
                "부서 · 계정과 잇는 관계는 제안으로 받지 않습니다 — 출발 객체를 수정할 수 "
                "있는 사람이 「연결」 로 바로 추가합니다.",
            )
        raise NotFound(code("OBJECTS", 28), "이을 객체를 찾을 수 없습니다.")
    # 끝 타입 — 「관계 추가」 와 같은 판정(`require_endpoints_allowed` 의 몸통)을 읽어 둔
    # 정의로.
    rel.require_end_types_allowed(
        db, kind, known.type_slug(src), known.type_slug(dst), known.ends()
    )
    # 속성은 모양만 — 기본값은 확정할 때 채운다(그때의 정의로).
    properties = validate_properties(known.defs(kind), spec.properties or {})

    same = edges.find_overlap(
        db,
        src_id=src.id,
        dst_id=dst.id,
        relation=kind.slug,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    if same is not None:
        period = (
            f"({edges.describe(same.valid_from, same.valid_to)})"
            if same.valid_from is not None or same.valid_to is not None
            else ""
        )
        return Result(
            status="connected",
            message=f"「{src.label}」 과(와) 「{dst.label}」 은(는) 이미 {kind.label}(으)로 "
            f"연결되어 있습니다{period} — 제안을 만들지 않았습니다.",
            relation_id=same.id,
        )

    found, before = _same_proposals(db, kind.slug, src.id, dst.id)
    if found is not None:
        updated = _raise_confidence(
            db, user, found, spec, origin=origin, confidence=confidence, labels=(src, dst)
        )
        return Result(
            status="open",
            message="같은 제안이 이미 열려 있습니다"
            + (
                " — 확신도가 더 높아 근거 · 확신도를 새 것으로 수정했습니다."
                if updated
                else "."
            ),
            proposal=found,
            updated=updated,
        )
    if not again and before is not None:
        return Result(
            status="rejected_before", message=rejected_message(db, before), proposal=before
        )

    row = RelationProposal(
        relation=kind.slug,
        src_object_id=src.id,
        dst_object_id=dst.id,
        properties=properties,
        valid_from=valid_from,
        valid_to=valid_to,
        evidence_note=spec.evidence_note.strip(),
        confidence=confidence,
        origin=origin,
        origin_ref=spec.origin_ref.strip()[:200],
        status="proposed",
        proposed_by_id=user.id,
    )
    if not guard:
        # 부르는 쪽(여러 건)이 줄마다 savepoint 를 두르고 같은 순간의 겹침을 받는다.
        db.add(row)
        db.flush()
    else:
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            raced = _raced(db, spec.relation, src.id, dst.id)
            if raced is None:
                raise
            return raced
    audit.record(
        db,
        action="relation.propose",
        actor=user,
        target_table=TABLE,
        target_id=row.id,
        target_label=f"{src.label} -{kind.slug}-> {dst.label}",
        workspace_id=src.owner_workspace_id,
        changes={
            **_ends(row, src.label, dst.label),
            "confidence": confidence,
            "origin": origin,
            "origin_ref": row.origin_ref,
            "evidence_note": row.evidence_note,
        },
    )
    return Result(
        status="created",
        message=f"제안했습니다 — 「{src.label}」 —{kind.label}→ 「{dst.label}」. 출발 객체를 "
        "수정할 수 있는 사람이 확정하면 관계가 됩니다.",
        proposal=row,
    )


def _raced(db: Session, relation: str, src_id: uuid.UUID, dst_id: uuid.UUID) -> Result | None:
    """같은 순간에 같은 제안이 들어와 열린 제안의 유일 색인에 걸렸다 — 먼저 들어온 것을
    돌려준다(열린 것은 하나)."""
    found = _open_same(db, relation, src_id, dst_id)
    if found is None:
        return None
    return Result(status="open", message="같은 제안이 이미 열려 있습니다.", proposal=found)


def _raise_confidence(
    db: Session,
    user: User,
    row: RelationProposal,
    spec: Spec,
    *,
    origin: str,
    confidence: float | None,
    labels: tuple[ObjectInstance, ObjectInstance],
) -> bool:
    """같은 열린 제안이 또 왔다 — **더 높은 확신도**면 근거 · 확신도 · 출처를 새 것으로.

    낮거나 같으면 그대로 둔다 — 같은 것을 여러 번 보낸 AI 가 낮은 값으로 덮어 검토함의 순서를
    흔들지 않게. 제안한 사람은 처음 사람 그대로다(확정 기록은 그 사람에게 간다)."""
    if confidence is None or (row.confidence is not None and confidence <= row.confidence):
        return False
    before = {
        "confidence": row.confidence,
        "evidence_note": row.evidence_note,
        "origin": row.origin,
        "origin_ref": row.origin_ref,
    }
    row.confidence = confidence
    if spec.evidence_note.strip():
        row.evidence_note = spec.evidence_note.strip()
    row.origin = origin
    if spec.origin_ref.strip():
        row.origin_ref = spec.origin_ref.strip()[:200]
    after = {
        "confidence": row.confidence,
        "evidence_note": row.evidence_note,
        "origin": row.origin,
        "origin_ref": row.origin_ref,
    }
    src, dst = labels
    audit.record(
        db,
        action="relation.proposal.update",
        actor=user,
        target_table=TABLE,
        target_id=row.id,
        target_label=f"{src.label} -{row.relation}-> {dst.label}",
        workspace_id=src.owner_workspace_id,
        changes={**_ends(row, src.label, dst.label), "changed": audit.diff(before, after)},
    )
    db.flush()
    return True


def propose_many(db: Session, user: User, specs: list[Spec], *, again: bool) -> list[Result]:
    """여러 건 — **줄마다 결과**. 한 줄이 틀려도 나머지는 간다(틀린 줄은 `error` 와 까닭).
    커밋하지 않는다. 줄마다 savepoint 라 틀린 줄의 반쯤 쓴 것이 남지 않는다."""
    if len(specs) > MAX_ROWS:
        raise InvalidValue(
            code("OBJECTS", 139),
            f"한 번에 {MAX_ROWS}줄까지 제안합니다 — {len(specs)}줄을 나눠 보내세요.",
        )
    out: list[Result] = []
    known = _Definitions(db)
    for spec in specs:
        try:
            with db.begin_nested():
                out.append(
                    propose(db, user, spec, again=again, definitions=known, guard=False)
                )
        except AppError as refused:
            out.append(
                Result(status="error", message=refused.message, error_code=refused.code)
            )
        except IntegrityError:
            raced = _raced(db, spec.relation, spec.src_id, spec.dst_id)
            if raced is None:
                raise
            out.append(raced)
    return out


# --- 확정 · 거절 ---------------------------------------------------------------


def _require_open(row: RelationProposal) -> None:
    if row.status != "proposed":
        raise Conflict(
            code("OBJECTS", 140),
            f"이미 {STATUS_LABELS.get(row.status, row.status)}된 제안입니다 — 열린 제안만 "
            "확정 · 거절 · 철회합니다.",
            details={"status": row.status},
        )


def _type_of(db: Session, row: ObjectInstance) -> ObjectType:
    found = db.get(ObjectType, row.type_id)
    if found is None:  # pragma: no cover - 타입은 객체를 RESTRICT 로 붙든다
        raise NotFound(code("OBJECTS", 137), "출발 객체의 타입을 찾을 수 없습니다.")
    return found


def _decider_src(db: Session, user: User, row: RelationProposal) -> ObjectInstance:
    """확정 · 거절 · 다시 열기를 할 수 있나 — **출발 객체를 고칠 수 있는 사람**(「관계 추가」
    와 같은 판정). 출발 객체를 돌려준다(지운 것도 — 거절은 받는다)."""
    src = _seen(db, user, row.src_object_id)
    if src is None:
        raise NotFound(code("OBJECTS", 137), "그 제안의 출발 객체를 찾을 수 없습니다.")
    access.require_edit(db, user, _type_of(db, src), src, code_value=code("OBJECTS", 142))
    return src


def can_decide(db: Session, user: User, row: RelationProposal) -> bool:
    try:
        _decider_src(db, user, row)
    except AppError:
        return False
    return True


@dataclass
class _Ready:
    kind: RelationType
    src: ObjectInstance
    dst: ObjectInstance
    valid_from: date | None
    valid_to: date | None


def _ready(db: Session, user: User, row: RelationProposal) -> _Ready:
    """확정 앞의 검사 — 「관계 추가」 가 `add_edge` 앞에서 보는 것과 같은 차례(권한 → 종류 →
    허브 관리 → 도착점)에, 그 사이 끝이 지워졌나를 더한다."""
    _require_open(row)
    src = _decider_src(db, user, row)
    if src.deleted_at is not None:
        raise Conflict(
            code("OBJECTS", 141),
            f"출발 객체 「{src.label}」 이(가) 삭제돼 확정할 수 없습니다 — 거절하거나, 객체를 "
            "되살린 뒤 확정하세요.",
        )
    kind = rel.relation_type(db, row.relation)
    managed.require_relation_editable(kind)
    dst = db.get(ObjectInstance, row.dst_object_id)
    if dst is not None and dst.deleted_at is not None:
        raise Conflict(
            code("OBJECTS", 141),
            f"도착 객체 「{dst.label}」 이(가) 삭제돼 확정할 수 없습니다 — 거절하거나, 객체를 "
            "되살린 뒤 확정하세요.",
        )
    if dst is None or _visible(db, user, dst.id) is None:
        raise NotFound(code("OBJECTS", 28), "이을 객체를 찾을 수 없습니다.")
    valid_from, valid_to = edges.checked_period(kind, row.valid_from, row.valid_to)
    return _Ready(kind=kind, src=src, dst=dst, valid_from=valid_from, valid_to=valid_to)


def _refused(caught: AppError) -> Conflict:
    """선 만들기가 거절한 까닭을 그대로 실어 — 제안은 열린 채로 남는다."""
    return Conflict(
        code("OBJECTS", 143),
        f"확정하지 못했습니다 — {caught.message} 제안은 열린 채로 남습니다.",
        details={"cause": caught.code, **caught.details},
    )


def blocker(db: Session, user: User, row: RelationProposal) -> AppError | None:
    """확정하면 막히나 — **아무것도 안 바꾼다**(검토함의 미리 보기). 확정과 같은 검사."""
    try:
        ready = _ready(db, user, row)
    except AppError as caught:
        return caught
    try:
        rel.require_can_add(
            db,
            ready.kind,
            ready.src,
            ready.dst,
            properties=dict(row.properties or {}),
            valid_from=ready.valid_from,
            valid_to=ready.valid_to,
        )
    except AppError as caught:
        return _refused(caught)
    return None


def reject_blocker(db: Session, user: User, row: RelationProposal) -> AppError | None:
    """거절하면 막히나 — **아무것도 안 바꾼다**(검토함의 미리 보기). 거절과 같은 검사."""
    try:
        _require_open(row)
        _decider_src(db, user, row)
    except AppError as caught:
        return caught
    return None


def _reason(row: RelationProposal) -> str:
    who = ORIGIN_LABELS.get(row.origin, row.origin)
    ref = f" 「{row.origin_ref}」" if row.origin_ref else ""
    score = f" · 확신도 {row.confidence:.2f}" if row.confidence is not None else ""
    return f"제안 확정 — {who}{ref}{score}"


def accept(
    db: Session, user: User, row: RelationProposal, *, note: str = ""
) -> ObjectRelation:
    """확정 — 「관계 추가」 와 **같은 길**(`relations.add_edge`)로 선을 만들고 제안에 그 선 ·
    결정한 사람 · 때를 적는다. 커밋하지 않는다. 막히면 `AppError`(선 만들기의 거절은
    OBJECTS-0143 에 까닭을 실어)."""
    ready = _ready(db, user, row)
    try:
        edge = rel.add_edge(
            db,
            user,
            ready.kind,
            ready.src,
            ready.dst,
            properties=dict(row.properties or {}),
            evidence_note=row.evidence_note,
            valid_from=ready.valid_from,
            valid_to=ready.valid_to,
            reason=_reason(row),
        )
    except AppError as caught:
        raise _refused(caught) from caught
    row.status = "accepted"
    row.decided_by_id = user.id
    # DB 의 now() — 선의 `updated_at`(넣을 때의 now())과 **같은 값**이다. 되돌리기가 이 둘이
    # 같은지로 「그 뒤 선이 고쳐졌나」 를 본다.
    row.decided_at = func.now()
    row.decision_note = note.strip()[:500]
    row.relation_id = edge.id
    db.flush()
    audit.record(
        db,
        action="relation.proposal.accept",
        actor=user,
        target_table=TABLE,
        target_id=row.id,
        target_label=f"{ready.src.label} -{row.relation}-> {ready.dst.label}",
        workspace_id=ready.src.owner_workspace_id,
        changes={
            **_ends(row, ready.src.label, ready.dst.label),
            "relation_id": str(edge.id),
            "confidence": row.confidence,
            "origin": row.origin,
            "origin_ref": row.origin_ref,
        },
        reason=row.decision_note or None,
    )
    return edge


def reject(db: Session, user: User, row: RelationProposal, *, note: str = "") -> None:
    """거절 — 상태 · 까닭 · 결정한 사람. 끝이 지워진 제안도 거절은 받는다(치우는 길).
    커밋하지 않는다."""
    _require_open(row)
    src = _decider_src(db, user, row)
    row.status = "rejected"
    row.decided_by_id = user.id
    row.decided_at = func.now()
    row.decision_note = note.strip()[:500]
    db.flush()
    _, dst_label = _labels(db, row)
    audit.record(
        db,
        action="relation.proposal.reject",
        actor=user,
        target_table=TABLE,
        target_id=row.id,
        target_label=f"{src.label} -{row.relation}-> {dst_label}",
        workspace_id=src.owner_workspace_id,
        changes={
            **_ends(row, src.label, dst_label),
            "confidence": row.confidence,
            "origin": row.origin,
            "origin_ref": row.origin_ref,
        },
        reason=row.decision_note or None,
    )


def reopen(
    db: Session, user: User, row: RelationProposal, *, apply: bool = True
) -> tuple[bool, str]:
    """확정 · 거절을 되돌려 **다시 연다** — (되나, 말). `apply=False` 면 아무것도 안 바꾸고
    무엇이 되나만. 커밋하지 않는다.

    - 거절 → 다시 연다.
    - 확정 → 그 선을 지우고(감사 `object.relation.remove`) 다시 연다. **확정 뒤 그 선이
      고쳐졌으면**(속성 · 근거 · 기간 · 합치기로 옮긴 끝 — 선의 `updated_at` 이 확정한 때와
      다르다) 선도 제안도 건드리지 않고 말한다 — 사람이 고친 선을 되돌리기가 지우면 안 된다.
      선이 이미 없으면 제안만 다시 연다.
    - 같은 제안이 그 사이 새로 열렸으면 건너뛴다(열린 것은 하나)."""
    if row.status == "proposed":
        return False, "이미 열려 있는 제안입니다."
    try:
        src = _decider_src(db, user, row)
    except AppError as denied:
        return False, denied.message
    if _open_same(db, row.relation, row.src_object_id, row.dst_object_id, skip=row.id):
        return False, "같은 제안이 그 사이 새로 열려 있어 다시 열지 않았습니다."
    edge = None
    if row.status == "accepted" and row.relation_id is not None:
        edge = db.get(ObjectRelation, row.relation_id)
        if edge is not None and edge.updated_at != row.decided_at:
            return False, (
                "확정한 뒤 그 관계가 수정됐습니다 — 관계도 제안도 그대로 둡니다. "
                "관련 객체에서 직접 해제하세요."
            )
        if edge is not None:
            kind = db.scalar(select(RelationType).where(RelationType.slug == edge.relation))
            if kind is not None and managed.owner_of(kind):
                return (
                    False,
                    f"{kind.label}은(는) 허브가 관리하는 관계 종류라 해제하지 않습니다.",
                )
    _, dst_label = _labels(db, row)
    pair = f"「{src.label}」 —{row.relation}→ 「{dst_label}」"
    message = (
        f"{pair} 관계를 해제하고 제안을 다시 엶"
        if edge is not None
        else f"{pair} 제안을 다시 엶"
    )
    if not apply:
        return True, message
    if edge is not None:
        audit.record(
            db,
            action="object.relation.remove",
            actor=user,
            target_table="object_relations",
            target_id=edge.id,
            target_label=f"{src.label} · {edge.relation}",
            workspace_id=src.owner_workspace_id,
            changes=audit.relation_endpoints(edge, src.label, dst_label),
            reason="제안 확정 복원",
        )
        db.delete(edge)
    before = row.status
    row.status = "proposed"
    row.decided_by_id = None
    row.decided_at = None
    row.decision_note = ""
    row.relation_id = None
    db.flush()
    audit.record(
        db,
        action="relation.proposal.reopen",
        actor=user,
        target_table=TABLE,
        target_id=row.id,
        target_label=f"{src.label} -{row.relation}-> {dst_label}",
        workspace_id=src.owner_workspace_id,
        changes={**_ends(row, src.label, dst_label), "status_before": before},
    )
    return True, message


def withdraw(db: Session, user: User, row: RelationProposal) -> None:
    """철회 — 제안한 사람이 열린 제안을 지운다(시스템 관리자도). 검토할 사람은 「거절」 한다
    (거절은 남아 같은 제안이 또 오는 것을 막는다). 커밋하지 않는다."""
    _require_open(row)
    if not (user.is_system_admin or row.proposed_by_id == user.id):
        raise Forbidden(
            code("OBJECTS", 144),
            "제안한 사람만 철회할 수 있습니다 — 확정할 수 있는 사람은 「거절」 합니다.",
        )
    src_label, dst_label = _labels(db, row)
    src = db.get(ObjectInstance, row.src_object_id)
    audit.record(
        db,
        action="relation.proposal.withdraw",
        actor=user,
        target_table=TABLE,
        target_id=row.id,
        target_label=f"{src_label} -{row.relation}-> {dst_label}",
        workspace_id=src.owner_workspace_id if src else None,
        changes={**_ends(row, src_label, dst_label), "evidence_note": row.evidence_note},
    )
    db.delete(row)
    db.flush()


def get(db: Session, user: User, proposal_id: uuid.UUID) -> RelationProposal:
    """제안 하나 — **양 끝을 볼 수 있어야** 있다고 답한다(없는 것과 안 보이는 것은 같은 말)."""
    row = db.get(RelationProposal, proposal_id)
    if row is None:
        raise NotFound(code("OBJECTS", 145), "제안을 찾을 수 없습니다.")
    seen = set(
        db.scalars(
            select(ObjectInstance.id).where(
                ObjectInstance.id.in_([row.src_object_id, row.dst_object_id]),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
        )
    )
    if row.src_object_id not in seen or row.dst_object_id not in seen:
        raise NotFound(code("OBJECTS", 145), "제안을 찾을 수 없습니다.")
    return row


# --- 보여 주기 -----------------------------------------------------------------


@dataclass
class View:
    """제안 한 줄 — 화면 · API · MCP 가 같은 모양을 받는다."""

    id: uuid.UUID
    relation: str
    relation_label: str
    label: str
    """보는 객체 쪽에서 읽는 말(정방향이면 종류 이름, 역방향이면 반대 이름)."""
    outgoing: bool
    status: str
    src_object_id: uuid.UUID
    src_label: str
    src_key: str | None
    src_type_slug: str
    src_type_label: str
    dst_object_id: uuid.UUID
    dst_label: str
    dst_key: str | None
    dst_type_slug: str
    dst_type_label: str
    properties: dict[str, Any]
    valid_from: date | None
    valid_to: date | None
    evidence_note: str
    confidence: float | None
    origin: str
    origin_ref: str
    proposed_by_label: str
    created_at: datetime
    decided_by_label: str
    decided_at: datetime | None
    decision_note: str
    relation_id: uuid.UUID | None
    can_decide: bool
    can_withdraw: bool


def views(
    db: Session,
    user: User,
    rows: Iterable[RelationProposal],
    *,
    mine: uuid.UUID | None = None,
) -> list[View]:
    """제안들을 줄로 — 끝 이름 · 타입 · 제안한 사람 · 이 사람이 확정할 수 있나를 **한
    번에** 읽는다. 보는 사람이 못 보는 끝은 「(볼 수 없는 객체)」. `mine` 은 보는 객체(방향의
    기준)."""
    found = list(rows)
    if not found:
        return []
    ids = {one.src_object_id for one in found} | {one.dst_object_id for one in found}
    objects = {
        one.id: one
        for one in db.scalars(select(ObjectInstance).where(ObjectInstance.id.in_(ids)))
    }
    hidden = system.hidden_objects(db, user, ids)
    types = {one.id: one for one in db.scalars(select(ObjectType))}
    kinds = {one.slug: one for one in db.scalars(select(RelationType))}
    people_ids = {one.proposed_by_id for one in found} | {one.decided_by_id for one in found}
    people = {
        one.id: (one.display_name or one.email)
        for one in db.scalars(select(User).where(User.id.in_([p for p in people_ids if p])))
    }
    editor = access.Editor(db, user)

    def end(object_id: uuid.UUID) -> tuple[str, str | None, str, str]:
        row = objects.get(object_id)
        kind = types.get(row.type_id) if row is not None else None
        if row is None or object_id in hidden:
            return (system.HIDDEN_LABEL, None, kind.slug if kind else "", "")
        return (
            row.label,
            row.key,
            kind.slug if kind else "",
            kind.label if kind else "알 수 없음",
        )

    out: list[View] = []
    for one in found:
        kind = kinds.get(one.relation)
        outgoing = mine is None or one.src_object_id == mine
        if kind is None:
            label = one.relation
        elif outgoing or not kind.directed:
            label = kind.label
        else:
            label = kind.inverse_label or f"{kind.label}의 반대"
        src = objects.get(one.src_object_id)
        src_type = types.get(src.type_id) if src is not None else None
        decider = (
            one.status == "proposed"
            and src is not None
            and src_type is not None
            and one.src_object_id not in hidden
            and editor(src_type, src)
        )
        s_label, s_key, s_type, s_type_label = end(one.src_object_id)
        d_label, d_key, d_type, d_type_label = end(one.dst_object_id)
        out.append(
            View(
                id=one.id,
                relation=one.relation,
                relation_label=kind.label if kind else one.relation,
                label=label,
                outgoing=outgoing,
                status=one.status,
                src_object_id=one.src_object_id,
                src_label=s_label,
                src_key=s_key,
                src_type_slug=s_type,
                src_type_label=s_type_label,
                dst_object_id=one.dst_object_id,
                dst_label=d_label,
                dst_key=d_key,
                dst_type_slug=d_type,
                dst_type_label=d_type_label,
                properties=dict(one.properties or {}),
                valid_from=one.valid_from,
                valid_to=one.valid_to,
                evidence_note=one.evidence_note,
                confidence=one.confidence,
                origin=one.origin,
                origin_ref=one.origin_ref,
                proposed_by_label=people.get(one.proposed_by_id, "")
                if one.proposed_by_id
                else "",
                created_at=one.created_at,
                decided_by_label=people.get(one.decided_by_id, "")
                if one.decided_by_id
                else "",
                decided_at=one.decided_at,
                decision_note=one.decision_note,
                relation_id=one.relation_id,
                can_decide=bool(decider),
                can_withdraw=one.status == "proposed"
                and (user.is_system_admin or one.proposed_by_id == user.id),
            )
        )
    return out


def _alive_ends() -> tuple[Any, Any, list[ColumnElement[bool]]]:
    src = aliased(ObjectInstance)
    dst = aliased(ObjectInstance)
    return (
        src,
        dst,
        [
            src.id == RelationProposal.src_object_id,
            dst.id == RelationProposal.dst_object_id,
            src.deleted_at.is_(None),
            dst.deleted_at.is_(None),
        ],
    )


def of_object(db: Session, user: User, row: ObjectInstance) -> list[View]:
    """이 객체에 걸린 **열린** 제안 — 양방향, 새것부터. 저쪽 끝이 지워졌거나 안 보이면 뺀다
    (「관련 객체」 와 같은 규칙 — 없는 것과 안 보이는 것을 같은 말로)."""
    src, dst, alive = _alive_ends()
    found = db.scalars(
        select(RelationProposal)
        .join(src, src.id == RelationProposal.src_object_id)
        .join(dst, dst.id == RelationProposal.dst_object_id)
        .where(
            RelationProposal.status == "proposed",
            or_(
                RelationProposal.src_object_id == row.id,
                RelationProposal.dst_object_id == row.id,
            ),
            *alive[2:],
            visible_owner_clause(user, src.owner_workspace_id),
            visible_owner_clause(user, dst.owner_workspace_id),
        )
        .order_by(RelationProposal.created_at.desc())
        .limit(500)
    )
    return views(db, user, found, mine=row.id)


@dataclass(frozen=True)
class PageQuery:
    status: str = "proposed"
    relation: str | None = None
    type_slug: str | None = None
    origin: str | None = None
    mine: bool = False
    """내가 제안한 것만."""
    decidable: bool = False
    """내가 확정할 수 있는 것만(열린 것)."""


def _page_stmt(db: Session, user: User, query: PageQuery) -> Select[Any]:
    src, dst, alive = _alive_ends()
    stmt = (
        select(RelationProposal)
        .join(src, alive[0])
        .join(dst, alive[1])
        .where(
            *alive[2:],
            visible_owner_clause(user, src.owner_workspace_id),
            visible_owner_clause(user, dst.owner_workspace_id),
        )
    )
    if query.status != "all":
        stmt = stmt.where(RelationProposal.status == query.status)
    if query.relation:
        stmt = stmt.where(RelationProposal.relation == query.relation)
    if query.origin:
        stmt = stmt.where(RelationProposal.origin == query.origin)
    if query.mine:
        stmt = stmt.where(RelationProposal.proposed_by_id == user.id)
    if query.type_slug:
        type_id = db.scalar(select(ObjectType.id).where(ObjectType.slug == query.type_slug))
        stmt = stmt.where(or_(src.type_id == type_id, dst.type_id == type_id))
    if query.decidable and not user.is_system_admin:
        stmt = stmt.where(
            RelationProposal.status == "proposed",
            or_(
                src.owner_workspace_id.in_(_managed_ids(user)),
                and_(
                    src.created_by_id == user.id,
                    src.owner_workspace_id.in_(_member_ids(user)),
                    src.type_id.in_(select(ObjectType.id).where(ObjectType.usage == "log")),
                ),
            ),
        )
    return stmt


def _managed_ids(user: User) -> Select[Any]:
    return select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == user.id, WorkspaceMember.role == "manager"
    )


def _member_ids(user: User) -> Select[Any]:
    return select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)


def page(
    db: Session, user: User, query: PageQuery, *, limit: int, offset: int
) -> tuple[list[View], int]:
    """제안 목록 — 양 끝이 보이고 지우지 않은 것만, 새것부터. (줄, 전체 수)."""
    stmt = _page_stmt(db, user, query)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    found = db.scalars(
        stmt.order_by(RelationProposal.created_at.desc(), RelationProposal.id)
        .limit(limit)
        .offset(offset)
    )
    return views(db, user, found), total


# --- 다른 기능과의 관계 ----------------------------------------------------------


def move_on_merge(db: Session, row: ObjectInstance, target: ObjectInstance) -> tuple[int, int]:
    """합치기 — 진 쪽(row)에 걸린 제안의 끝을 이긴 쪽(target)으로. (옮김, 버림).

    열린 제안은 옮긴 뒤 **같은 열린 제안이 이긴 쪽에 이미 있거나**(열린 것은 하나), 자기
    자신으로 가는 제안이 되면 버린다 — 둘을 남기면 검토자가 같은 것을 두 번 판단한다. 이긴
    쪽에 같은 선이 이미 있는 열린 제안은 남긴다(확정이 「이미 이어져 있다」 로 거절하고,
    사람이 거절해 치운다). 확정 · 거절된 제안은 이력이라 그대로 옮긴다(자기 자신으로 가는
    것만 버린다)."""
    found = list(
        db.scalars(
            select(RelationProposal).where(
                or_(
                    RelationProposal.src_object_id == row.id,
                    RelationProposal.dst_object_id == row.id,
                )
            )
        )
    )
    moved = dropped = 0
    for one in found:
        src = target.id if one.src_object_id == row.id else one.src_object_id
        dst = target.id if one.dst_object_id == row.id else one.dst_object_id
        if src == dst or (
            one.status == "proposed"
            and _open_same(db, one.relation, src, dst, skip=one.id) is not None
        ):
            db.delete(one)
            dropped += 1
            continue
        one.src_object_id, one.dst_object_id = src, dst
        moved += 1
        # 한 줄씩 쓴다 — 진 쪽의 두 제안이 이긴 쪽에서 같은 제안이 되면 둘째가 첫째를 본다.
        db.flush()
    return moved, dropped


def count_of_kind(db: Session, slug: str) -> int:
    """그 관계 종류의 제안 수(상태와 상관없이) — 종류 지우기의 미리 보기가 센다."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(RelationProposal)
            .where(RelationProposal.relation == slug)
        )
        or 0
    )


def delete_of_kind(db: Session, slug: str) -> int:
    """그 관계 종류의 제안을 지운다 — 종류를 지울 때 함께(제안은 자료가 아니라 작업 거리다)."""
    done = (
        db.query(RelationProposal)
        .filter(RelationProposal.relation == slug)
        .delete(synchronize_session=False)
    )
    return int(done or 0)


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """홈 「남은 일」 — **확정할 수 있는 열린 제안**의 수(부서 관리자 이상). 누르면 검토함의
    그 종류로 간다. 처리할 수 없는 사람에게 띄우면 못 지우는 숫자가 된다."""
    if not is_any_manager(db, viewer):
        return []
    src = aliased(ObjectInstance)
    stmt = (
        select(func.count())
        .select_from(RelationProposal)
        .join(src, src.id == RelationProposal.src_object_id)
        .where(RelationProposal.status == "proposed", src.deleted_at.is_(None))
    )
    if not viewer.is_system_admin:
        stmt = stmt.where(src.owner_workspace_id.in_(_managed_ids(viewer)))
    count = int(db.scalar(stmt) or 0)
    if not count:
        return []
    return [
        extensions.MaintenanceItem(
            key="relation_proposals",
            label="제안된 관계",
            count=count,
            link="/review?kind=proposed_relation",
        )
    ]
