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

이력은 이 변경의 감사 기록(`ontology.type.move`)을 보고, 옮기기 전 기록(감사 기록의 이름표가
`<원 타입>:…`)의 키를 대상의 키로 읽는다.

## 바뀐 때(`updated_at`)는 옮긴 때로 올린다

「지난번 이후 바뀐 것」 을 읽는 쪽은 모두 `type_id + updated_at` 으로 묻는다 — 코어 API 의
`since` · 형제 SP 의 코어 구독 · 지표 증분 · 지도의 점. 처음에는 바뀐 때를 그대로 뒀는데(값이
안 바뀌었다고 봤다), 그러면 옮긴 객체가 그 누구에게도 안 보였다: 대상의 수신 시스템은 옮겨 온
객체를 영영 못 받고, 대상의 지표는 그것을 안 더했다(2026-11-28 점검). 객체의 타입 · 칸의 키가
바뀐 것은 받는 쪽에는 바뀐 것이다 — 그래서 같은 문장(`_MOVE`)에서 올린다.

원 타입 쪽은 객체가 떠나 「바뀐 것」 으로 물을 행이 없다. 그래서 **떠난 일을 따로 말한다** —
`Trail` 이 감사 기록으로 「이 타입의 객체는 지금 어디 있나」 를 답하고, 코어 API 는 원 타입에
`moved_to` · `moved_at` 을 싣고, 지표 증분은 그 타입을 읽는 지표를 전량으로 다시 센다.

계보 선(ADR 0041)의 낡음은 대상 타입에 개정 칸이 없으면 「원천의 바뀐 때 > 고정한 때」 다. 바뀐
때만 올리면 옮긴 객체를 원천으로 둔 계보 선이 전부 낡음이 된다 — 그래서 옮기기 전에 낡지 않던
선은 고정한 때도 옮긴 때로 함께 올린다(`_KEEP_PINS`, 판정을 옮기기 전 그대로 지킨다).
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.audit.models import AuditEntry
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
from app.modules.ontology.services import require_new_key
from app.modules.ontology.similar import norm
from app.modules.semantic.models import ObjectEmbedding, SemanticMark, SemanticTarget
from app.modules.slides.models import SlideTemplate
from app.modules.webhooks.models import Webhook
from app.shared.errors import AppError

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
        if one.action == "create" and one.target:
            # 대상에 **새로 만드는** 칸 — 화면 · 가져오기의 새 속성과 같은 규칙(모양 · 길이 ·
            # 플랫폼 칸과 겹치는 키). 전에는 그대로 만들어 `status` 가 생기거나 49자에서 DB
            # 오류(500)가 났다.
            problem = _new_key_problem(one.target)
            if problem:
                one.note, one.blocked = problem, True
                out.fields.append(one)
                continue
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
    # **정의 없이 남은 옛 값**(예전에 지운 속성의 저장값) — 그대로 옮기면 대상에 같은 키의 칸이
    # 있을 때 그 칸의 값으로 살아난다(키 변경은 이것을 막는다 — 옮기기도 같게).
    landing = set(theirs) | {
        one.target for one in out.fields if one.target and one.action != "drop"
    }
    orphans = {key: count for key, count in counts.items() if key not in mine}
    for key, count in sorted(orphans.items()):
        if key in mapping:
            if mapping[key] is None:
                out.fields.append(
                    FieldMap(
                        key,
                        f"(정의 없는 옛 값) {key}",
                        None,
                        "drop",
                        count,
                        note=f"정의 없이 남은 옛 값 {count:,}건을 버립니다",
                    )
                )
            else:
                out.errors.append(
                    f"정의 없이 남은 옛 값 「{key}」 은 버리기(null)만 적을 수 있습니다 — "
                    "살릴 "
                    "값이면 원 타입에 그 속성을 먼저 다시 만드십시오."
                )
        elif key in landing:
            out.errors.append(
                f"원 타입에 정의 없이 남은 옛 값 「{key}」({count:,}건 — 예전에 지운 속성의 "
                f"저장값)이 대상의 칸 「{key}」 과 겹칩니다 — 옮기면 그 칸의 값으로 "
                "살아납니다. "
                f'버리려면 mapping 에 {{"{key}": null}} 을 적으십시오.'
            )
    for one in out.fields:
        if one.blocked:
            out.errors.append(f"속성 「{one.label}」({one.source}): {one.note}")
    unknown = sorted(set(mapping) - set(mine) - set(orphans))
    if unknown:
        out.errors.append(f"원 타입에 없는 키를 적었습니다: {', '.join(unknown)}")


def _new_key_problem(key: str) -> str:
    """새로 만드는 속성 키의 문제 — 없으면 빈 글자."""
    try:
        require_new_key(key)
    except AppError as refused:
        return refused.message
    return ""


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
    if source.key_scope == "workspace" and target.key_scope != "workspace":
        # 범위가 **부서 → 전사**로 넓어진다 — 옮길 것끼리(부서가 다른) 같은 식별자가 있으면
        # 대상에서 같은 식별자가 둘 산다(유일은 서비스가 지킨다 — DB 제약이 아니다). 대상에
        # 이미 그 식별자의 객체가 있으면 둘 다 그 객체로 병합되어 겹치지 않는다(병합하지 않으면
        # 위의 「같은 식별자의 객체」 가 막는다). 범위가 부서인 대상은 부서마다 따로라 겹치지
        # 않는다.
        there = aliased(ObjectInstance)
        clashing = db.execute(
            select(ObjectInstance.key, func.count())
            .where(
                ObjectInstance.type_id == source.id,
                ObjectInstance.deleted_at.is_(None),
                ObjectInstance.key.is_not(None),
                ~select(there.id)
                .where(
                    there.type_id == target.id,
                    there.deleted_at.is_(None),
                    there.key == ObjectInstance.key,
                )
                .exists(),
            )
            .group_by(ObjectInstance.key)
            .having(func.count() > 1)
            .limit(6)
        ).all()
        if clashing:
            shown = " · ".join(f"{key}({count})" for key, count in clashing[:5])
            out.errors.append(
                f"옮길 객체끼리 같은 식별자가 부서마다 있습니다 — {shown}. 대상은 식별자가 "
                "전사에 하나인 타입이라 겹칩니다 — 식별자를 먼저 정리하십시오."
            )


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


#: 빈 값 — 유일 검사가 보지 않는다(`require_unique_properties`).
_EMPTY = "('null'::jsonb, '\"\"'::jsonb, '[]'::jsonb)"
#: 옮긴 뒤 대상의 그 칸에 **살아서 남는 값**마다 한 줄 — (그 값을 들 객체, 부서, 값).
#: 병합되는 객체(`pairs`)는 짝의 칸이 비었을 때만 값이 짝으로 간다(`merge_into` 는 빈 칸만
#: 채운다) — 그때 그 값을 드는 것은 짝이다. 짝에 값이 있으면 옮길 쪽의 값은 사라진다.
_UNIQUE_MOVED = (
    "WITH pairs AS (SELECT * FROM unnest(CAST(:moved AS uuid[]), CAST(:into AS uuid[])) "
    "AS p(moved, into_id)), mine AS (SELECT coalesce(p.into_id, m.id) AS who, "
    "m.owner_workspace_id AS ws, m.properties -> CAST(:src AS text) AS v FROM objects m "
    "LEFT JOIN pairs p ON p.moved = m.id LEFT JOIN objects t ON t.id = p.into_id "
    "WHERE m.type_id = :source AND m.deleted_at IS NULL AND m.properties ? CAST(:src AS text) "
    f"AND m.properties -> CAST(:src AS text) NOT IN {_EMPTY} AND (p.into_id IS NULL OR "
    f"coalesce(t.properties -> CAST(:dst AS text), 'null'::jsonb) IN {_EMPTY})) "
)


def _unique(db: Session, source: ObjectType, target: ObjectType, out: Plan) -> None:
    """대상의 유일 칸에 같은 값이 둘 서는가 — 대상에 이미 있는 값과, 옮길 것끼리.

    유일의 범위는 대상의 식별자 범위를 따른다(`require_unique_properties`) — 부서인 대상이면
    부서가 다른 같은 값은 겹치지 않는다. 같은 대상 객체로 병합되는 것끼리도 겹치지
    않는다(하나가 된다). 전에는 값만 묶어, 부서 A 와 B 의 「S1」 을 범위가 부서인 대상으로 옮길
    때 「옮길 객체끼리 같은 값」 으로 막았다(2026-11-27 점검)."""
    unique = {one.key for one in _defs(db, target).values() if one.unique}
    scoped = target.key_scope == "workspace"
    same_ws = " AND x.owner_workspace_id IS NOT DISTINCT FROM e.ws" if scoped else ""
    for one in out.fields:
        if not one.target or one.target not in unique or one.action == "drop":
            continue
        params = {
            "target": target.id,
            "source": source.id,
            "src": one.source,
            "dst": one.target,
            "moved": [moved for moved, _ in out.pairs],
            "into": [into for _, into in out.pairs],
        }
        count = int(
            db.scalar(
                text(
                    _UNIQUE_MOVED + "SELECT count(DISTINCT e.who) FROM mine e JOIN objects x "
                    "ON x.type_id = :target AND x.deleted_at IS NULL AND x.id <> e.who AND "
                    "x.properties -> CAST(:dst AS text) = e.v" + same_ws
                ),
                params,
            )
            or 0
        )
        if count:
            out.errors.append(
                f"대상의 유일 칸 「{one.target}」 에 같은 값이 이미 있는 객체가 {count:,}건 "
                "있습니다 — 같은 것이면 겹치는 객체로 병합하거나 값을 정리하십시오."
            )
        # 옮길 것**끼리** 같은 값 — 원 타입에서는 유일이 아니었을 수 있다.
        among = int(
            db.scalar(
                text(
                    _UNIQUE_MOVED
                    + "SELECT count(*) FROM (SELECT 1 FROM mine GROUP BY v"
                    + (", ws" if scoped else "")
                    + " HAVING count(DISTINCT who) > 1) d"
                ),
                params,
            )
            or 0
        )
        if among:
            where = " 같은 부서 안에서" if scoped else ""
            out.errors.append(
                f"옮길 객체끼리{where} 대상의 유일 칸 「{one.target}」 에 같은 값을 가진 "
                f"묶음이 {among:,}개 있습니다 — 값을 먼저 정리하십시오."
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
        # **수신 시스템에 실제로 일어나는 일**을 적는다 — 전에는 「사라집니다」 · 「새 객체를
        # 받습니다」 라고 했지만 옮긴 객체의 바뀐 때가 그대로라 어느 쪽도 일어나지 않았다
        # (2026-11-28 점검). 이제 원 타입에 `moved_to` 가 실리고 옮긴 객체는 대상의 「바뀐 것」
        # 으로 간다.
        out.core_consumers = consumers
        if target.core:
            told = (
                f"수신 시스템에는 이 객체들이 {target.label}({target.slug})(으)로 "
                "옮겨졌다고 알립니다(카탈로그 · 쪽의 `moved_to`). 연동 키트 2026-11-28 판 "
                f"이상과 코어 구독은 {source.label} 쪽을 비활성으로 두고 {target.label} "
                "로 받습니다 — 그 전 판의 키트는 아무것도 지우지 않고 「확인 필요」 로 "
                "멈춥니다(키트를 새로 받거나 --full --allow-shrink)"
            )
        else:
            told = (
                f"대상 {target.label}은(는) 공개하지 않은 타입이라 수신 시스템에서는 이 "
                "객체들이 사라집니다(옮겼다고만 알리고 어디로 갔는지는 밝히지 않습니다 — "
                "키트는 비활성으로 둡니다)"
            )
        out.warnings.append(
            f"{source.label}은(는) 외부에 공개 중입니다 — {told}. 통보를 확인하고 "
            "accept_core 로 적용하십시오."
        )
    if target.core:
        out.warnings.append(
            f"대상 {target.label}은(는) 외부에 공개 중입니다 — 옮긴 객체는 바뀐 때가 옮긴 "
            "때로 올라 수신 시스템이 "
            + ("「바뀐 것」 으로 다시 받습니다." if source.core else "새 객체로 받습니다.")
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

#: 옮기는 문장 — 타입 · 값의 키 · 사람이 수정한 칸 표시, 그리고 **바뀐 때**(`now()`, ORM 의
#: `onupdate` 와 같은 값). 바뀐 때를 그대로 두면 「지난번 이후」 로 묻는 쪽(코어 API · 코어
#: 구독 · 지표 증분 · 지도의 점)이 옮긴 객체를 못 본다(머리말).
_MOVE = text(
    "UPDATE objects o SET type_id = :target, updated_at = now(), "
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

#: 계보 선의 고정한 때를 옮긴 때로 — **`_MOVE` 앞에** 돈다(옛 바뀐 때를 읽는다). 대상
#: 타입에 개정 칸이 없으면 낡음은 「원천의 바뀐 때 > 고정한 때」 라, 바뀐 때만 올리면 옮긴
#: 객체를 원천으로 둔 계보 선이 전부 낡음이 된다. 옮기기 전에 그 식으로 낡지 않던 선만
#: 올린다 — 낡았던 선은 바뀐 때가 더 올라 낡은 채다. 선의 바뀐 때(`updated_at`)는
#: 그대로(`lineage._write_pin` 과 같다).
_KEEP_PINS = {
    end: text(
        "UPDATE object_relations r SET pinned_at = now() FROM objects o "
        f"WHERE o.id = ANY(:ids) AND r.{end}_object_id = o.id "
        "AND r.relation = ANY(CAST(:kinds AS text[])) AND r.pinned_at IS NOT NULL "
        "AND NOT coalesce(o.updated_at > r.pinned_at, false)"
    )
    for end in ("src", "dst")
}


def apply(
    db: Session,
    user: User,
    source: ObjectType,
    target: ObjectType,
    out: Plan,
    *,
    progress: Progress,
) -> dict[str, Any]:
    """계획대로 옮긴다 — 부르는 쪽이 커밋한다. 돌려주는 것: 옮긴 수 · 병합한 수 · 원 타입의
    id(`from_id` — 감사 기록에 실린다)."""
    for edit in out.edits:
        edit()
    db.flush()
    if out.dropped_aliases:
        db.execute(delete(ObjectAlias).where(ObjectAlias.id.in_(out.dropped_aliases)))
    keymap = json.dumps(out.keys, ensure_ascii=False)
    # 대상이 개정 칸으로 낡음을 보면 고정한 때는 판정에 안 든다 — 바뀐 때로 보는 때만 지킨다.
    lineage: dict[str, list[str]] = {"src": [], "dst": []}
    if not target.revision_key:
        for slug, end in db.execute(
            select(RelationType.slug, RelationType.lineage_source).where(
                RelationType.lineage_source.in_(("src", "dst"))
            )
        ):
            lineage[end].append(slug)
    done = 0
    for ids in _chunks(db, source.id, ObjectInstance.id.is_not(None)):
        for end, kinds in lineage.items():
            if kinds:
                db.execute(_KEEP_PINS[end], {"ids": ids, "kinds": kinds})
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
    # 원 타입의 id 를 함께 돌려준다 — 부르는 쪽이 감사 기록(`ontology.type.move`)에 그대로
    # 싣는다. 원 타입을 slug 로만 적으면, 지운 뒤 같은 slug 로 만든 새 타입이 그 옮기기를
    # 물려받는다(`Trail`).
    return {"moved": done, "merged": merged, "from_id": str(source.id)}


def fingerprint(out: Plan) -> str:
    import hashlib

    body: list[Any] = [out.source, out.target, out.objects, out.duplicates, out.errors]
    body.append([(f.source, f.target, f.action, f.values) for f in out.fields])
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest()


# --- 옮긴 자취 — 읽는 쪽 ------------------------------------------------------------
#
# 옮기기는 감사 기록 하나(`ontology.type.move` — 대상은 `target_id`, 원 타입은 그때의 slug 로
# `changes.from` · 이름표 「<원> → <대상>」, 이 판부터는 원 타입의 id 도 `changes.from_id`)만
# 남긴다. 「이 타입의 객체는 지금 어디 있나」 · 「이 타입에서 지난번 이후 옮겨 간 · 옮겨 온
# 일이 있나」 를 그 기록에서 읽는다 — 코어 API 의 `moved_to`, 지표 증분, 코어 구독이 같은 답을
# 쓴다.

#: 옮긴 길을 따라갈 단계 — A → B → C … (이력 `objects/history.MOVE_HOPS` 와 같은 값).
MOVE_HOPS = 6

#: (slug, 이 차례 뒤부터, 이 차례 앞까지) — 한 타입이 그 slug 를 쥐었던 동안.
Span = tuple[str, int | None, int | None]


@dataclass(frozen=True)
class Moved:
    """옮기기 한 번 — 감사 기록 하나."""

    seq: int
    """감사의 차례 — 때(`created_at`)는 트랜잭션 시작 시각이라 차례는 이것으로 가른다."""
    at: datetime
    source: str
    """그때의 원 타입 slug."""
    source_id: uuid.UUID | None
    """원 타입의 id — 이 판 전의 기록에는 없다(그때는 slug 로만 맞춘다)."""
    target_id: uuid.UUID | None
    target: str
    """그때의 대상 slug."""


@dataclass(frozen=True)
class Departure:
    """한 타입의 객체가 **지금 있는 곳** — 마지막으로 떠난 일을 그 뒤의 옮기기까지 따라간
    끝."""

    at: datetime
    """이 타입에서 마지막으로 옮겨 간 때(감사 기록의 때)."""
    to_id: uuid.UUID | None
    """끝 타입 — 지운 타입이면 None."""
    to_slug: str
    """끝 타입의 지금 slug(지운 타입이면 기록의 slug)."""


def _uuid(raw: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


class Trail:
    """옮기기 · 타입 slug 변경 · 타입 삭제의 감사 기록을 **한 번** 읽어 둔 것.

    **원 타입은 id 로 맞춘다** — 이 판부터 옮기기 기록에 원 타입의 id(`from_id`)를 남긴다.
    그 전의 기록은 그때의 slug 로만 적혀 있어, 타입마다 「언제부터 언제까지 어느 slug 였나」 를
    세워 맞춘다(slug 변경 기록, `objects/routes._slug_spans` 와 같은 규칙 — 차례로 가른다).
    그 동안의 **앞은 다른 타입이 그 slug 를 내놓은 때로 막는다**(지움 · slug 변경) — 열어 두면
    옮긴 원 타입을 지우고 같은 slug 로 새 타입을 만들었을 때 새 타입이 옛 옮기기를
    물려받았다(`moved_to` 가 실리고, 코어 구독이 새 타입의 객체를 사용 중지했다 — 2026-11-28
    검토). 기록은 옮기기 · slug 변경 · 삭제마다 한 줄이라 몇 줄 안 된다."""

    def __init__(self, db: Session) -> None:
        self.moves: list[Moved] = []
        for seq, at, target_id, label, origin, origin_id, landed in db.execute(
            select(
                AuditEntry.seq,
                AuditEntry.created_at,
                AuditEntry.target_id,
                AuditEntry.target_label,
                AuditEntry.changes["from"].astext,
                AuditEntry.changes["from_id"].astext,
                AuditEntry.changes["to"].astext,
            )
            .where(AuditEntry.action == "ontology.type.move")
            .order_by(AuditEntry.seq)
        ):
            head, _, tail = (label or "").partition(" → ")
            self.moves.append(
                Moved(
                    seq=int(seq),
                    at=at,
                    source=str(origin or head),
                    source_id=_uuid(origin_id),
                    target_id=target_id,
                    target=str(landed or tail),
                )
            )
        self.renames: dict[uuid.UUID, list[tuple[int, str]]] = {}
        #: slug → (내놓은 차례, 내놓은 타입) — 지움 · slug 변경. 동안의 앞을 막는다.
        self.released: dict[str, list[tuple[int, uuid.UUID]]] = {}
        #: 지운 타입 → 지운 차례 — 지운 타입의 동안의 뒤를 막는다.
        self.deleted: dict[uuid.UUID, int] = {}
        self.slugs: dict[uuid.UUID, str] = {}
        if not self.moves:
            return
        for action, seq, type_id, label, old in db.execute(
            select(
                AuditEntry.action,
                AuditEntry.seq,
                AuditEntry.target_id,
                AuditEntry.target_label,
                AuditEntry.changes["from"].astext,
            )
            .where(AuditEntry.action.in_(("ontology.type.rename", "ontology.type.delete")))
            .order_by(AuditEntry.seq)
        ):
            if type_id is None:
                continue
            if action == "ontology.type.rename":
                self.renames.setdefault(type_id, []).append((int(seq), str(old or "")))
                gone = str(old or "")
            else:
                self.deleted[type_id] = int(seq)
                gone = str(label or "")
            if gone:
                self.released.setdefault(gone, []).append((int(seq), type_id))
        self.slugs = dict(db.execute(select(ObjectType.id, ObjectType.slug)).tuples().all())

    def _spans(self, type_id: uuid.UUID | None, slug: str) -> list[Span]:
        """이 타입이 slug 를 쥐었던 동안들 — 지운 타입이면 기록의 slug 하나(지운 차례까지).
        동안의 앞은 다른 타입이 그 slug 를 마지막으로 내놓은 차례로 막는다."""
        raw: list[Span] = []
        if type_id is None or type_id not in self.slugs:
            raw.append((slug, None, self.deleted.get(type_id) if type_id else None))
        else:
            after: int | None = None
            for seq, old in self.renames.get(type_id, []):
                if old:
                    raw.append((old, after, seq))
                after = seq
            raw.append((self.slugs[type_id], after, None))
        out: list[Span] = []
        for name, after, before in raw:
            others = [
                seq
                for seq, who in self.released.get(name, [])
                if who != type_id and (before is None or seq < before)
            ]
            floor = max([one for one in (after, *others) if one is not None], default=None)
            out.append((name, floor, before))
        return out

    def _from(self, one: Moved, type_id: uuid.UUID | None, spans: list[Span]) -> bool:
        """그 옮기기의 원 타입이 이 타입인가 — id 가 적혀 있으면 id 로, 없으면 slug
        동안으로."""
        if one.source_id is not None:
            return one.source_id == type_id
        return _within(one, spans)

    def out_of(self, type_id: uuid.UUID | None, slug: str) -> list[Moved]:
        """이 타입에서 **옮겨 간** 일 — 차례대로."""
        spans = self._spans(type_id, slug)
        return [one for one in self.moves if self._from(one, type_id, spans)]

    def touching(self, type_ids: set[uuid.UUID], after: datetime) -> list[Moved]:
        """`after` 뒤에 이 타입들에서 옮겨 갔거나 이 타입들로 옮겨 온 일."""
        found: dict[int, Moved] = {}
        for type_id in type_ids:
            if type_id not in self.slugs:
                continue
            for one in self.out_of(type_id, self.slugs[type_id]):
                found[one.seq] = one
        for one in self.moves:
            if one.target_id in type_ids:
                found[one.seq] = one
        return [one for _seq, one in sorted(found.items()) if one.at > after]

    def left(self, object_type: ObjectType) -> Departure | None:
        """이 타입의 객체가 옮겨 간 곳 — 옮긴 적이 없거나 **다시 돌아왔으면** None.

        마지막으로 떠난 일에서 시작해, 그 객체들이 닿은 타입에서 **그 뒤에** 처음 떠난 일을
        따라간다(A → B 뒤에 B → C 면 A 의 객체는 C 에 있다 — 옮기기는 타입의 객체 전부를
        옮긴다). 닿은 타입에서 그 앞에 떠난 일은 그 객체들과 상관없다."""
        outs = self.out_of(object_type.id, object_type.slug)
        if not outs:
            return None
        first = outs[-1]
        at_id, at_slug, seq = first.target_id, first.target, first.seq
        for _hop in range(MOVE_HOPS):
            following = next(
                (one for one in self.out_of(at_id, at_slug) if one.seq > seq), None
            )
            if following is None:
                break
            at_id, at_slug, seq = following.target_id, following.target, following.seq
        if at_id == object_type.id:
            return None
        known = at_id is not None and at_id in self.slugs
        return Departure(
            at=first.at,
            to_id=at_id if known else None,
            to_slug=self.slugs[at_id] if known and at_id is not None else at_slug,
        )


def _within(one: Moved, spans: list[Span]) -> bool:
    """그 옮기기의 원 타입이 이 slug 동안의 것인가(원 타입 id 가 없는 옛 기록)."""
    return any(
        one.source == name
        and (after is None or one.seq > after)
        and (before is None or one.seq < before)
        for name, after, before in spans
    )
