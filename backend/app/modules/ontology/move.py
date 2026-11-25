"""객체를 다른 타입으로 옮기기 · 타입 합치기(ADR 0094).

AI 가 문서마다 정의를 만들면 같은 개념이 두 타입에 갈린다 — `협력사` 300건과 `공급사`
1,200건. 객체 합치기는 같은 타입끼리만 되고(OBJECTS-52), 다른 타입으로 옮기는 길이 없어
내보내서 다시 넣고 지우면 관계 · 별칭 · 이력 · 참조가 끊겼다. 여기서는 **객체 id 를 그대로
둔 채** 타입을 바꾼다 — 참조 칸 · 관계 · 첨부 · 문서 · 이력은 id 로 이어져 그대로 따라온다.

## 칸 대응

원 타입의 속성마다: 대상의 **같은 키**(모양이 같을 때), 대상의 다른 키(`mapping`), 대상에
**새로 만들기**(정의를 베낀다), **버림**(`null` — 사람이 적을 때만). 적지 않으면 같은 키 →
같은 이름 → 새로 만들기 차례로 고른다. 모양이 다르면(종류 · 단위 · 값 하나/여러 값 ·
가리키는 타입 · 대상에 없는 선택 값) 막는다 — 값을 바꾸는 일은 종류 변경(ADR 0007)의 몫이다.
문서 · 파일 속성은 같은 키로만(첨부 · 판 표가 키를 쥔다).

## 겹치는 객체

대상에 같은 식별자의 객체가 있으면 막는다 — `merge_duplicates` 면 옮긴 뒤(이제 같은
타입이다) 대상의 객체로 **병합**한다(객체 병합과 같은 함수, 참조 · 관계 · 별칭이
따라간다). 서로 다른 객체가 같은 별칭을 쓰면 막는다(타입 안에서 별칭은 하나다).

## 바꾸는 것 · 알리는 것

바꾼다: 객체(타입 · 값의 키 · 사람이 수정한 칸 표시), 별칭, 원 표와 맺은 관계의 끝, 뜻으로
찾기(다시 계산), 대상의 새 속성, 이 타입을 가리키던 참조 속성의 대상, 관계 종류의 끝(대상을
더한다), 못 찾은 말. 알린다: 원 타입에 남는 뷰 · 지표 · 장표 틀 · 뜻으로 찾기 대상 · 웹훅,
대상의 필수 칸이 빈 객체, 개정 칸이 다른 계보, 공개 타입. 원 타입은 빈 채로 남는다 —
삭제는 삭제 미리 보기로.

객체의 `updated_at` 은 그대로다. 이력은 이 변경의 감사 기록(`ontology.type.move`)을 보고,
옮기기 전 기록(감사 기록의 이름표가 `<원 타입>:…`)의 키를 대상의 키로 읽는다.
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.datasources.models import DataSource
from app.modules.metrics.models import MetricDef
from app.modules.objects import lifecycle
from app.modules.objects.models import (
    ObjectAlias,
    ObjectInstance,
    ObjectLink,
    SavedView,
    SearchMiss,
)
from app.modules.ontology.models import (
    DETACHED_KINDS,
    ObjectType,
    PropertyDef,
    RelationType,
)
from app.modules.ontology.rename import (
    Place,
    Progress,
    _chunks,
    _common_refusals,
    _setter,
)
from app.modules.ontology.retype import KIND_LABELS
from app.modules.ontology.similar import norm
from app.modules.semantic.models import ObjectEmbedding, SemanticMark, SemanticTarget
from app.modules.slides.models import SlideTemplate
from app.modules.webhooks.models import Webhook

#: 객체가 이만큼 넘으면 요청 안에서 안 옮기고 작업으로 — 키 변경 · 종류 변경과 같은 문턱.
INLINE_OBJECTS = 20_000
#: 계획에 견본으로 적는 겹침 수.
SAMPLES = 5
#: 정의를 베낄 때 옮기지 않는 칸.
_NOT_COPIED = {"id", "owner_kind", "owner_id", "created_at", "updated_at"}


@dataclass
class FieldMap:
    """칸 대응 한 줄."""

    source: str
    label: str
    target: str | None
    action: str
    """`same`(대상의 같은 키) · `map`(대상의 다른 키) · `create`(대상에 새로) ·
    `drop`(버림)."""
    values: int
    note: str = ""
    """막는 까닭 · 알아 둘 것 — 비어 있으면 그대로 옮긴다."""
    blocked: bool = False


@dataclass
class Plan:
    source: str
    target: str
    label: str
    objects: int = 0
    """옮길 객체(지운 것 포함 — 원 타입이 비어야 삭제된다)."""
    live: int = 0
    duplicates: int = 0
    """대상에 같은 식별자가 있는 객체 — `merge_duplicates` 면 병합한다."""
    fields: list[FieldMap] = field(default_factory=list)
    places: list[Place] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    core_consumers: list[str] = field(default_factory=list)
    edits: list[Any] = field(default_factory=list, repr=False)
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list, repr=False)
    """(옮길 객체, 병합될 대상 객체)."""
    dropped_aliases: list[uuid.UUID] = field(default_factory=list, repr=False)

    def place(self, what: str, count: int) -> None:
        if count:
            self.places.append(Place(what, count))

    @property
    def keys(self) -> dict[str, str | None]:
        """값의 키 대응 — 버리면 None. 같은 키는 싣지 않는다."""
        return {
            one.source: one.target
            for one in self.fields
            if one.action == "drop" or (one.target and one.target != one.source)
        }


def _defs(db: Session, owner: ObjectType) -> dict[str, PropertyDef]:
    rows = db.scalars(
        select(PropertyDef)
        .where(PropertyDef.owner_kind == "type", PropertyDef.owner_id == owner.id)
        .order_by(PropertyDef.sort_order, PropertyDef.key)
    )
    return {one.key: one for one in rows}


def _shape(row: PropertyDef) -> str:
    kind = KIND_LABELS.get(row.data_type, row.data_type)
    return f"{kind} · {row.unit}" if row.data_type == "number" and row.unit else kind


def _mismatch(src: PropertyDef, dst: PropertyDef, source: str, target: str) -> str | None:
    """모양이 다르면 그 까닭 — 값을 바꾸지 않고 옮길 수 있으면 None."""
    if src.data_type != dst.data_type or (
        src.data_type == "number" and (src.unit or "") != (dst.unit or "")
    ):
        return (
            f"모양이 다릅니다({_shape(src)} → {_shape(dst)}) — 종류 변경으로 먼저 맞추십시오"
        )
    if bool(src.multi) != bool(dst.multi):
        return "값 하나 · 여러 값이 다릅니다"
    if src.data_type == "object_ref":
        mine = target if src.ref_type_slug == source else src.ref_type_slug
        if mine != dst.ref_type_slug:
            return f"가리키는 타입이 다릅니다({mine} → {dst.ref_type_slug})"
    return None


def _values(db: Session, type_id: uuid.UUID) -> dict[str, int]:
    """키마다 값이 있는 객체 수(지운 것 포함)."""
    rows = db.execute(
        text(
            "SELECT e.key, count(*) FROM objects o CROSS JOIN LATERAL jsonb_object_keys("
            "o.properties) e(key) WHERE o.type_id = :type_id GROUP BY 1"
        ),
        {"type_id": type_id},
    )
    return {str(key): int(count) for key, count in rows}


def _used_options(db: Session, type_id: uuid.UUID, key: str) -> set[str]:
    rows = db.execute(
        text(
            "SELECT DISTINCT CASE WHEN jsonb_typeof(o.properties -> CAST(:key AS text)) = "
            "'array' THEN a.v ELSE o.properties ->> CAST(:key AS text) END FROM objects o "
            "LEFT JOIN LATERAL jsonb_array_elements_text(CASE WHEN jsonb_typeof(o.properties "
            "-> CAST(:key AS text)) = 'array' THEN o.properties -> CAST(:key AS text) ELSE "
            "'[]'::jsonb END) a(v) ON true WHERE o.type_id = :type_id AND o.properties ? "
            "CAST(:key AS text)"
        ),
        {"type_id": type_id, "key": key},
    )
    return {str(one) for (one,) in rows if one is not None}


def _fields(
    db: Session,
    source: ObjectType,
    target: ObjectType,
    mapping: dict[str, str | None],
    out: Plan,
) -> None:
    mine, theirs = _defs(db, source), _defs(db, target)
    counts = _values(db, source.id)
    by_label = {norm(one.label): one for one in theirs.values()}
    taken: dict[str, str] = {}
    for key, src in mine.items():
        values = counts.get(key, 0)
        if key in mapping:
            wanted = mapping[key]
            if wanted is None:
                one = FieldMap(key, src.label, None, "drop", values)
                if values:
                    one.note = f"값 {values:,}건을 버립니다 — 되돌릴 수 없습니다"
                out.fields.append(one)
                continue
            dst = theirs.get(wanted)
            one = FieldMap(
                key, src.label, wanted, "same" if wanted == key and dst else "map", values
            )
            if dst is None:
                one.action = "create"
        elif key in theirs:
            one = FieldMap(key, src.label, key, "same", values)
        elif norm(src.label) in by_label and not _mismatch(
            src, by_label[norm(src.label)], source.slug, target.slug
        ):
            one = FieldMap(key, src.label, by_label[norm(src.label)].key, "map", values)
        else:
            one = FieldMap(key, src.label, key, "create", values)
        dst = theirs.get(one.target or "")
        if src.data_type in DETACHED_KINDS and one.target != key:
            one.note = "문서 · 파일 속성은 같은 키로만 옮깁니다(첨부 · 판 표가 키를 쥔다)"
            one.blocked = True
        elif dst is not None:
            reason = _mismatch(src, dst, source.slug, target.slug)
            if reason:
                one.note, one.blocked = reason, True
            elif src.data_type == "enum" and values:
                missing = sorted(
                    _used_options(db, source.id, key) - set(dst.enum_options or [])
                )
                if missing:
                    shown = " · ".join(f"「{one}」" for one in missing[:8])
                    one.note = f"대상의 선택 값에 없는 값이 있습니다 — {shown}"
                    one.blocked = True
        if one.target:
            if one.target in taken:
                one.note = (
                    f"「{taken[one.target]}」 와 같은 대상 칸으로 갑니다 — 값이 섞입니다"
                )
                one.blocked = True
            taken[one.target] = key
        out.fields.append(one)
    for one in out.fields:
        if one.blocked:
            out.errors.append(f"속성 「{one.label}」({one.source}): {one.note}")
    unknown = sorted(set(mapping) - set(mine))
    if unknown:
        out.errors.append(f"원 타입에 없는 키를 적었습니다: {', '.join(unknown)}")


def _scope_clause(target: ObjectType, alias: Any, other: Any) -> Any:
    clause = alias.key == other.key
    if target.key_scope == "workspace":
        clause = and_(
            clause, alias.owner_workspace_id.is_not_distinct_from(other.owner_workspace_id)
        )
    return clause


def _duplicates(db: Session, source: ObjectType, target: ObjectType, out: Plan) -> None:
    """대상에 같은 식별자가 있는 살아 있는 객체 — 쌍을 적는다."""
    from sqlalchemy.orm import aliased

    moved, there = aliased(ObjectInstance), aliased(ObjectInstance)
    rows = db.execute(
        select(moved.id, there.id, moved.label, there.label, moved.key)
        .join(there, _scope_clause(target, moved, there))
        .where(
            moved.type_id == source.id,
            there.type_id == target.id,
            moved.deleted_at.is_(None),
            there.deleted_at.is_(None),
            moved.key.is_not(None),
        )
    ).all()
    seen: set[uuid.UUID] = set()
    for mid, tid, _mlabel, _tlabel, _key in rows:
        if mid not in seen:
            seen.add(mid)
            out.pairs.append((mid, tid))
    out.duplicates = len(out.pairs)


def _aliases(db: Session, source: ObjectType, target: ObjectType, out: Plan) -> None:
    """같은 (kind, norm) 의 별칭이 대상에 있다 — 병합할 짝의 것이면 옮길 쪽을 버리고, 아니면
    막는다."""
    from sqlalchemy.orm import aliased

    mine, theirs = aliased(ObjectAlias), aliased(ObjectAlias)
    rows = db.execute(
        select(mine.id, mine.object_id, theirs.object_id, mine.value)
        .join(theirs, and_(mine.kind == theirs.kind, mine.norm == theirs.norm))
        .where(mine.type_id == source.id, theirs.type_id == target.id)
    ).all()
    partner = dict(out.pairs)
    clash: list[str] = []
    for alias_id, owner, other, value in rows:
        if partner.get(owner) == other:
            out.dropped_aliases.append(alias_id)
        else:
            clash.append(str(value))
    if clash:
        shown = ", ".join(f"「{one}」" for one in clash[:SAMPLES])
        out.errors.append(
            f"서로 다른 객체가 같은 별칭을 씁니다 {len(clash)}건 — {shown}. 타입 안에서 "
            "별칭은 "
            "하나라 옮기면 겹칩니다 — 한쪽 별칭을 정리하십시오."
        )


def _unique(db: Session, source: ObjectType, target: ObjectType, out: Plan) -> None:
    """대상의 유일 칸에 이미 있는 값 — 병합할 짝은 빼고 센다."""
    unique = {one.key for one in _defs(db, target).values() if one.unique}
    merging = {moved for moved, _ in out.pairs}
    for one in out.fields:
        if not one.target or one.target not in unique or one.action == "drop":
            continue
        clash = db.execute(
            text(
                "SELECT m.id FROM objects m JOIN objects t ON t.type_id = :target AND "
                "t.deleted_at IS NULL AND t.properties -> CAST(:dst AS text) = m.properties "
                "-> "
                "CAST(:src AS text) WHERE m.type_id = :source AND m.deleted_at IS NULL AND "
                "m.properties ? CAST(:src AS text)"
            ),
            {"target": target.id, "source": source.id, "src": one.source, "dst": one.target},
        ).all()
        count = sum(1 for (moved,) in clash if moved not in merging)
        if count:
            out.errors.append(
                f"대상의 유일 칸 「{one.target}」 에 같은 값이 이미 있는 객체가 {count:,}건 "
                "있습니다 — 같은 것이면 겹치는 객체로 병합하거나 값을 정리하십시오."
            )


def _key_policy(db: Session, source: ObjectType, target: ObjectType, out: Plan) -> None:
    base = select(func.count()).select_from(ObjectInstance)
    live = [ObjectInstance.type_id == source.id, ObjectInstance.deleted_at.is_(None)]
    if target.key_policy == "none":
        keyed = int(db.scalar(base.where(*live, ObjectInstance.key.is_not(None))) or 0)
        if keyed:
            out.errors.append(
                f"대상 {target.label}은(는) 식별자를 쓰지 않는 타입인데 옮길 객체 "
                f"{keyed:,}건에 "
                "식별자가 있습니다 — 대상의 식별자 정책을 먼저 바꾸십시오."
            )
    if target.key_policy == "required":
        keyless = int(db.scalar(base.where(*live, ObjectInstance.key.is_(None))) or 0)
        if keyless:
            out.errors.append(
                f"대상 {target.label}은(는) 식별자가 필수인데 옮길 객체 {keyless:,}건에 "
                "식별자가 "
                "없습니다 — 먼저 식별자를 채우십시오."
            )


def plan(
    db: Session,
    source: ObjectType,
    target: ObjectType,
    *,
    mapping: dict[str, str | None],
    merge_duplicates: bool,
    consumers: list[str],
    skip_job: uuid.UUID | None = None,
) -> Plan:
    """원 타입의 객체를 대상 타입으로 — 아무것도 안 바꾸고 센다."""
    out = Plan(source.slug, target.slug, f"{source.label} → {target.label}")
    if source.id == target.id:
        out.errors.append("같은 타입입니다.")
        return out
    _common_refusals(db, source, out.errors, skip_job)
    _common_refusals(db, target, out.errors, skip_job)
    feeding = list(db.scalars(select(DataSource.name).where(DataSource.type_id == source.id)))
    if feeding:
        out.errors.append(
            f"데이터 소스 {', '.join(feeding)} 이(가) {source.label}에 넣는 중입니다 — 옮겨도 "
            "다음 "
            "동기화가 다시 만듭니다. 넣을 타입을 먼저 바꾸십시오."
        )

    out.objects = int(
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(ObjectInstance.type_id == source.id)
        )
        or 0
    )
    out.live = int(
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(ObjectInstance.type_id == source.id, ObjectInstance.deleted_at.is_(None))
        )
        or 0
    )
    _fields(db, source, target, mapping, out)
    _key_policy(db, source, target, out)
    _duplicates(db, source, target, out)
    if out.duplicates and not merge_duplicates:
        out.errors.append(
            f"대상에 같은 식별자의 객체가 {out.duplicates:,}건 있습니다 — 같은 것이면 "
            "「겹치는 "
            "객체 병합」 으로 옮긴 뒤 대상의 객체로 병합합니다(참조 · 관계 · 별칭이 "
            "따라갑니다)."
        )
    _aliases(db, source, target, out)
    _unique(db, source, target, out)

    out.place("옮길 객체(지운 것 포함)", out.objects)
    out.place("병합할 겹친 객체", out.duplicates if merge_duplicates else 0)
    out.place("대상에 새로 만들 속성", sum(one.action == "create" for one in out.fields))
    out.place("버리는 속성 값", sum(one.values for one in out.fields if one.action == "drop"))
    _definitions(db, source, target, out)
    _leftovers(db, source, target, out)
    if source.core and consumers:
        out.core_consumers = consumers
        out.warnings.append(
            f"{source.label}은(는) 외부에 공개 중입니다 — 수신 시스템에서 이 객체들이 "
            "사라집니다. "
            "통보를 확인하고 accept_core 로 적용하십시오."
        )
    if target.core:
        out.warnings.append(
            f"대상 {target.label}은(는) 외부에 공개 중입니다 — 수신 시스템이 새 객체를 "
            "받습니다."
        )
    out.warnings.append(
        f"옮긴 뒤 「{source.label}」 타입은 빈 채로 남습니다 — 정리하려면 삭제 미리 보기로."
    )
    return out


def _definitions(db: Session, source: ObjectType, target: ObjectType, out: Plan) -> None:
    """대상의 새 속성 · 이 타입을 가리키던 참조 속성 · 관계 종류의 끝."""
    mine = _defs(db, source)
    created = [one for one in out.fields if one.action == "create" and not one.blocked]
    for one in created:
        src = mine[one.source]
        values = {
            column.key: copy.deepcopy(getattr(src, column.key))
            for column in PropertyDef.__table__.columns
            if column.key not in _NOT_COPIED
        }
        values["key"] = one.target
        if values.get("ref_type_slug") == source.slug:
            values["ref_type_slug"] = target.slug
        out.edits.append(lambda values=values: _create(db, target, values))

    refs = list(
        db.scalars(select(PropertyDef).where(PropertyDef.ref_type_slug == source.slug))
    )
    for row in refs:
        out.edits.append(_setter(row, "ref_type_slug", target.slug))
    out.place("이 타입을 가리키던 참조 속성(대상으로)", len(refs))

    ends = 0
    for kind in db.scalars(select(RelationType)):
        changed = False
        for name in ("src_type_slugs", "dst_type_slugs"):
            slugs = list(getattr(kind, name) or [])
            if source.slug in slugs and target.slug not in slugs:
                out.edits.append(_setter(kind, name, [*slugs, target.slug]))
                changed = True
        ends += changed
    out.place("관계 종류의 끝(대상을 더함)", ends)

    misses = int(
        db.scalar(
            select(func.count()).select_from(SearchMiss).where(SearchMiss.scope == source.slug)
        )
        or 0
    )
    out.place("못 찾은 말(별칭 후보)", misses)
    revision = source.revision_key
    if revision and out.keys.get(revision, revision) != target.revision_key:
        out.warnings.append(
            f"개정 칸이 다릅니다({source.label}.{source.revision_key} → 대상 "
            f"{target.revision_key or '없음'}) — 옮긴 객체를 원천으로 둔 계보 선의 낡음 "
            "판정이 "
            "대상의 개정 칸으로 바뀝니다."
        )


def _create(db: Session, target: ObjectType, values: dict[str, Any]) -> None:
    db.add(PropertyDef(owner_kind="type", owner_id=target.id, **values))


def _leftovers(db: Session, source: ObjectType, target: ObjectType, out: Plan) -> None:
    """원 타입에 남는 것 — 바꾸지 않고 알린다."""
    parts: list[str] = []
    for label, count in (
        (
            "저장된 뷰",
            db.scalar(
                select(func.count())
                .select_from(SavedView)
                .where(SavedView.type_id == source.id)
            ),
        ),
        (
            "지표",
            db.scalar(
                select(func.count())
                .select_from(MetricDef)
                .where(MetricDef.source_type_id == source.id)
            ),
        ),
        (
            "장표 틀",
            db.scalar(
                select(func.count())
                .select_from(SlideTemplate)
                .where(SlideTemplate.subject_type_id == source.id)
            ),
        ),
    ):
        if count:
            parts.append(f"{label} {count}개")
    if db.get(SemanticTarget, source.id) is not None:
        parts.append("뜻으로 찾기 대상")
    hooks = sum(source.slug in (one.type_slugs or []) for one in db.scalars(select(Webhook)))
    if hooks:
        parts.append(f"웹훅 {hooks}개")
    if parts:
        out.warnings.append(
            f"{source.label}에 걸린 {' · '.join(parts)}은(는) 원 타입에 남습니다 — 대상에서 "
            "다시 "
            "만들거나 정리하십시오."
        )
    required = [
        one
        for one in _defs(db, target).values()
        if one.required
        and not any(f.target == one.key and f.action != "drop" for f in out.fields)
    ]
    if required and out.live:
        out.warnings.append(
            f"대상의 필수 칸 {', '.join(one.label for one in required)} 이(가) 옮긴 객체 "
            f"{out.live:,}"
            "건에서 비게 됩니다 — 그 객체를 수정할 때 채우라고 거절됩니다."
        )


# --- 적용 ------------------------------------------------------------------------

_MOVE = text(
    "UPDATE objects o SET type_id = :target, "
    "properties = (SELECT coalesce(jsonb_object_agg(coalesce(m.value #>> '{}', e.key), "
    "e.value), '{}'::jsonb) FROM jsonb_each(o.properties) e LEFT JOIN jsonb_each(CAST(:map AS "
    "jsonb)) m ON m.key = e.key WHERE m.value IS NULL OR m.value <> 'null'::jsonb), "
    "human_edits = CASE WHEN o.human_edits IS NULL THEN NULL ELSE (SELECT coalesce("
    "jsonb_object_agg(CASE WHEN e.key LIKE 'properties.%' THEN 'properties.' || coalesce("
    "m.value #>> '{}', substr(e.key, 12)) ELSE e.key END, e.value), '{}'::jsonb) FROM "
    "jsonb_each(o.human_edits) e LEFT JOIN jsonb_each(CAST(:map AS jsonb)) m ON 'properties.' "
    "|| m.key = e.key WHERE m.value IS NULL OR m.value <> 'null'::jsonb) END "
    "WHERE o.id = ANY(:ids)"
)


def apply(
    db: Session,
    user: User,
    source: ObjectType,
    target: ObjectType,
    out: Plan,
    *,
    progress: Progress,
) -> dict[str, int]:
    """계획대로 옮긴다 — 부르는 쪽이 커밋한다."""
    for edit in out.edits:
        edit()
    db.flush()
    if out.dropped_aliases:
        db.execute(delete(ObjectAlias).where(ObjectAlias.id.in_(out.dropped_aliases)))
    keymap = json.dumps(out.keys, ensure_ascii=False)
    done = 0
    for ids in _chunks(db, source.id, ObjectInstance.id.is_not(None)):
        db.execute(_MOVE, {"target": target.id, "map": keymap, "ids": ids})
        db.execute(
            update(ObjectAlias).where(ObjectAlias.object_id.in_(ids)).values(type_id=target.id)
        )
        for model in (ObjectEmbedding, SemanticMark):
            db.execute(delete(model).where(model.object_id.in_(ids)))
        db.execute(
            update(ObjectLink)
            .where(ObjectLink.src_id.in_(ids), ObjectLink.src_type == source.slug)
            .values(src_type=target.slug)
        )
        db.execute(
            update(ObjectLink)
            .where(ObjectLink.dst_id.in_(ids), ObjectLink.dst_type == source.slug)
            .values(dst_type=target.slug)
        )
        done += len(ids)
        if progress is not None:
            progress("객체 옮기기", done, out.objects)
    db.execute(
        text(
            "DELETE FROM search_misses o WHERE o.scope = :old AND EXISTS (SELECT 1 FROM "
            "search_misses n WHERE n.scope = :new AND n.norm = o.norm)"
        ),
        {"old": source.slug, "new": target.slug},
    )
    db.execute(
        update(SearchMiss).where(SearchMiss.scope == source.slug).values(scope=target.slug)
    )
    db.expire_all()
    merged = 0
    for moved_id, into_id in out.pairs:
        moved, into = db.get(ObjectInstance, moved_id), db.get(ObjectInstance, into_id)
        if moved is None or into is None or moved.deleted_at is not None:
            continue
        lifecycle.merge_into(db, user, moved, target, into, inline=False)
        merged += 1
        if progress is not None:
            progress("겹친 객체 병합", merged, len(out.pairs))
    return {"moved": done, "merged": merged}


def fingerprint(out: Plan) -> str:
    import hashlib

    body: list[Any] = [out.source, out.target, out.objects, out.duplicates, out.errors]
    body.append([(f.source, f.target, f.action, f.values) for f in out.fields])
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest()
