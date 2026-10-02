"""표의 열이 **어느 있는 타입을 가리키나** — 참조 후보(ADR 0009).

표에서 타입을 만들 때 열마다 「이 값들이 어느 타입의 객체로 풀리나」 를 센다. 판정은 일괄
입력이 참조 칸을 풀 때 쓰는 것 그대로다(`bulk.Refs.classify` — 식별자 → 별칭 → 이름 → id,
이름이 여럿이면 「여럿에 맞음」). 후보가 97% 라면 넣을 때도 97% 가 풀린다.

**보수적으로 제안한다.** 하나로 풀린 값이 90% 이상이고, 그런 값이 3종 이상이고, 짧은 숫자가
아닐 때만 종류를 참조로 바꾼다. 나머지는 후보로 **보이기만** 한다 — 틀린 제안은 맞는 값처럼
들어가 아무도 안 고친다. 실측(개발 DB): 개발모델의 판(`01` · `02` …)이 Northwind 공급사
식별자와 65% 맞았다.

두 단계로 센다. 먼저 SQL 로 「이 값을 식별자 · 이름 · 별칭 · id 로 가진 타입」 을 넉넉히 거르고
(인덱스를 탄다), 걸린 타입만 이름 풀이를 세워 값마다 판정한다 — 타입마다 풀이를 다 세우면
기록 타입의 식별자 · 이름을 통째로 읽는다.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import system
from app.modules.objects.bulk import MULTI_SEP, NULL_MARK, Refs
from app.modules.objects.models import ObjectAlias, ObjectInstance
from app.modules.ontology import interfaces
from app.modules.ontology.inference import ColumnGuess, Inferred, RefCandidate
from app.modules.ontology.models import ObjectType
from app.shared.batches import chunks
from app.shared.permissions import visible_owner_clause
from app.shared.text import compare_key

HUGE = 200_000
"""이보다 큰 타입은 후보로 보지 않는다 — 이름 풀이가 그 타입의 식별자 · 이름을 통째로 읽는다.
축은 수천 ~ 수만 건이고, 이만큼 큰 것은 기록이다(ADR 0010)."""
DISTINCT_CAP = 2_000
"""열마다 많이 나온 값부터 이만큼만 본다."""
PROPOSE_SHARE = 0.9
PROPOSE_MIN_VALUES = 3
LIST_SHARE = 0.3
MAX_CANDIDATES = 3
PRECISE_MAX = 6
"""거르기에 걸린 타입 중 값마다 판정해 볼 수 — 많이 걸린 것부터."""
SAMPLES = 5

_SHORT_NUMBER = re.compile(r"^\d{1,4}$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_LINKABLE = {"text", "enum", "number"}


@dataclass
class _Target:
    slug: str
    label: str
    kind: str
    """`type` · `interface`."""
    members: set[str] = field(default_factory=set)
    """인터페이스면 구현 타입 slug, 타입이면 자기."""


def _linkable(column: ColumnGuess) -> bool:
    """가리킬 법한 열 — 글 · 고를 값 · 정수. 날짜 · 참/거짓 · 주소 · 긴 글은 아니다."""
    if column.role != "property" or column.data_type not in _LINKABLE:
        return False
    return not (column.data_type == "number" and column.decimals)


def _values(rows: list[dict[str, Any]], column: ColumnGuess) -> Counter[str]:
    """열의 값 → 나온 수. 여러 값 칸은 항목마다(넣을 때 그렇게 나눠 푼다)."""
    counts: Counter[str] = Counter()
    for row in rows:
        raw = row.get(column.header)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text or text == NULL_MARK:
            continue
        parts = text.split(MULTI_SEP) if column.multi else [text]
        counts.update(part.strip() for part in parts if part.strip())
    return counts


def _sizes(db: Session) -> dict[uuid.UUID, int]:
    """타입마다 살아 있는 객체 수 — **`HUGE` 를 넘으면 거기서 멈추고 센다**(200만 건을 다
    세지 않는다. 실측 0.09초)."""
    out: dict[uuid.UUID, int] = {}
    for type_id in db.scalars(select(ObjectType.id).where(ObjectType.kind_class != "system")):
        bounded = (
            select(ObjectInstance.id)
            .where(ObjectInstance.type_id == type_id, ObjectInstance.deleted_at.is_(None))
            .limit(HUGE + 1)
            .subquery()
        )
        out[type_id] = int(db.scalar(select(func.count()).select_from(bounded)) or 0)
    return out


def _hits(
    db: Session, user: User, values: set[str], small: list[uuid.UUID]
) -> dict[str, set[uuid.UUID]]:
    """값 → 그 값을 식별자 · 이름 · 별칭 · id 로 가진 타입들. **넉넉하게** 거른다(이름은
    대소문자 무시) — 정확한 판정은 뒤에서 이름 풀이가 한다.

    식별자는 모든 타입에서 찾는다(큰 타입이 걸렸다는 것도 알려 줄 말이다). 이름 · 별칭은 큰
    타입을 빼고 찾는다 — `(type_id, lower(label))` 인덱스가 타입을 앞에 두기 때문이다."""
    out: dict[str, set[uuid.UUID]] = defaultdict(set)
    visible = visible_owner_clause(user, ObjectInstance.owner_workspace_id)
    texts = sorted(values)
    for batch in chunks(texts):
        for type_id, key in db.execute(
            select(ObjectInstance.type_id, ObjectInstance.key).where(
                ObjectInstance.key.in_(batch), ObjectInstance.deleted_at.is_(None), visible
            )
        ):
            out[key].add(type_id)
    ids = [one for one in texts if _UUID.match(one)]
    for batch in chunks(ids):
        for type_id, found in db.execute(
            select(ObjectInstance.type_id, ObjectInstance.id).where(
                ObjectInstance.id.in_([uuid.UUID(one) for one in batch]),
                ObjectInstance.deleted_at.is_(None),
                visible,
            )
        ):
            out[str(found)].add(type_id)
    if not small:
        return out
    lowered: dict[str, list[str]] = defaultdict(list)
    for one in texts:
        lowered[one.lower()].append(one)
    lower_label = func.lower(ObjectInstance.label)
    for batch in chunks(list(lowered)):
        for type_id, label in db.execute(
            select(ObjectInstance.type_id, lower_label).where(
                ObjectInstance.type_id.in_(small),
                lower_label.in_(batch),
                ObjectInstance.deleted_at.is_(None),
                visible,
            )
        ):
            for original in lowered.get(label, []):
                out[original].add(type_id)
    normed: dict[str, list[str]] = defaultdict(list)
    for one in texts:
        normed[compare_key(one)].append(one)
    for batch in chunks(list(normed)):
        for type_id, norm in db.execute(
            select(ObjectAlias.type_id, ObjectAlias.norm).where(
                ObjectAlias.type_id.in_(small), ObjectAlias.norm.in_(batch)
            )
        ):
            for original in normed.get(norm, []):
                out[original].add(type_id)
    return out


def _measure(refs: Refs, target: _Target, counts: list[tuple[str, int]]) -> RefCandidate:
    """값마다 이름 풀이로 판정해 센다."""
    checked = one = many = none = 0
    one_values: list[str] = []
    many_samples: list[str] = []
    none_samples: list[str] = []
    for text, n in counts:
        checked += n
        match = refs.classify(target.slug, text)
        if match.kind == "one":
            one += n
            one_values.append(text)
        elif match.kind == "many":
            many += n
            if len(many_samples) < SAMPLES:
                many_samples.append(text)
        else:
            none += n
            if len(none_samples) < SAMPLES:
                none_samples.append(text)
    return RefCandidate(
        target_slug=target.slug,
        target_label=target.label,
        target_kind=target.kind,
        checked=checked,
        one=one,
        many=many,
        none=none,
        one_values=len(one_values),
        many_samples=many_samples,
        none_samples=none_samples,
        short=bool(one_values) and all(_SHORT_NUMBER.match(one) for one in one_values),
    )


def _related(a: RefCandidate, b: RefCandidate, targets: dict[str, _Target]) -> bool:
    """한쪽이 다른 쪽을 담는 인터페이스인가 — 그러면 둘이 다 맞아도 겨루는 것이 아니다."""
    first, second = targets[a.target_slug], targets[b.target_slug]
    return a.target_slug in second.members or b.target_slug in first.members


def _decide(
    column: ColumnGuess, found: list[RefCandidate], targets: dict[str, _Target]
) -> None:
    """제안할지 정하고, 안 하면 왜 안 하는지 적는다."""
    if not found:
        return
    best = found[0]
    share = best.one / best.checked if best.checked else 0.0
    if share < PROPOSE_SHARE:
        return
    rivals = [
        other
        for other in found[1:]
        if other.checked
        and other.one / other.checked >= PROPOSE_SHARE
        and not _related(best, other, targets)
    ]
    reasons: list[str] = []
    if best.short:
        reasons.append("맞은 값이 전부 짧은 숫자라 우연일 수 있어 제안하지 않습니다")
    elif best.one_values < PROPOSE_MIN_VALUES:
        reasons.append(f"맞은 값이 {best.one_values}종뿐이라 제안하지 않습니다")
    elif rivals:
        names = " · ".join(one.target_label for one in [best, *rivals])
        reasons.append(f"{names} 에 다 맞습니다 — 어느 것인지 고르세요")
    if reasons:
        column.ref_note = " · ".join([column.ref_note, *reasons]).strip(" ·")
        return
    column.data_type = "object_ref"
    column.ref_type_slug = best.target_slug
    column.enum_options = []
    column.decimals = None
    column.note = (
        f"{best.target_label} 의 식별자 · 이름으로 {round(share * 100)}% 가 풀립니다 — 참조로 "
        "둡니다"
    )


def attach(db: Session, user: User, inferred: Inferred, rows: list[dict[str, Any]]) -> None:
    """추론한 열마다 참조 후보를 단다 — 확실하면 종류를 참조로 바꾼다(`_decide`)."""
    columns = [one for one in inferred.columns if _linkable(one)]
    if not columns:
        return
    counted: dict[str, list[tuple[str, int]]] = {}
    capped: dict[str, int] = {}
    for column in columns:
        values = _values(rows, column)
        counted[column.header] = values.most_common(DISTINCT_CAP)
        if len(values) > DISTINCT_CAP:
            capped[column.header] = len(values)
    if not any(counted.values()):
        return

    kinds = {one.id: one for one in db.scalars(select(ObjectType))}
    sizes = _sizes(db)
    small = [type_id for type_id, size in sizes.items() if size <= HUGE]
    every = {text for pairs in counted.values() for text, _ in pairs}
    hits = _hits(db, user, every, small)
    ends = interfaces.load_ends(db)
    targets: dict[str, _Target] = {
        one.slug: _Target(one.slug, one.label, "type", {one.slug}) for one in kinds.values()
    }
    for slug in ends.interfaces:
        targets[slug] = _Target(
            slug, ends.labels.get(slug, slug), "interface", set(ends.expand([slug]) or ())
        )
    systems = [one.slug for one in kinds.values() if system.is_system(one)]
    refs = Refs(db, user)

    for column in columns:
        counts = counted[column.header]
        if not counts:
            continue
        weight: Counter[uuid.UUID] = Counter()
        for text, n in counts:
            for type_id in hits.get(text, ()):
                weight[type_id] += n
        checked = sum(n for _, n in counts)
        floor = LIST_SHARE * checked
        huge = [
            kinds[type_id]
            for type_id, w in weight.items()
            if w >= floor and sizes.get(type_id, 0) > HUGE
        ]

        chosen = [
            kinds[type_id].slug
            for type_id, w in weight.most_common()
            if w >= floor and sizes.get(type_id, 0) <= HUGE and type_id in kinds
        ][:PRECISE_MAX]
        # 인터페이스 — 구현 타입 **둘 이상**에 걸리면 그 인터페이스도 본다(값이 여러 타입에
        # 나뉘어 있으면 어느 한 타입으로는 90% 가 안 된다).
        hit_slugs = {kinds[type_id].slug for type_id in weight if type_id in kinds}
        shared = [
            slug
            for slug, target in targets.items()
            if target.kind == "interface" and len(target.members & hit_slugs) >= 2
        ]
        # 원 표(부서 · 계정)는 `objects` 에 없어 거르기에 안 걸린다 — 늘 본다(작다).
        candidates = [targets[slug] for slug in [*chosen, *shared, *systems]]
        measured = [_measure(refs, one, counts) for one in candidates]
        found = sorted(
            (one for one in measured if one.one and one.one >= floor),
            key=lambda one: (-one.one, one.target_kind == "interface", one.target_slug),
        )[:MAX_CANDIDATES]
        column.ref_candidates = found
        notes = [
            f"{one.label}({HUGE:,}건 넘음)은 너무 커서 후보로 보지 않았습니다" for one in huge
        ]
        if (found or huge) and column.header in capped:
            # 아무것도 안 가리키는 열(메모 · 일련번호)에는 안 붙인다 — 할 말이 없는데 붙으면
            # 정작 읽어야 할 말이 묻힌다.
            total = capped[column.header]
            notes.insert(0, f"많이 나온 값 {DISTINCT_CAP:,}종만 보았습니다(전체 {total:,}종)")
        column.ref_note = " · ".join(notes)
        _decide(column, found, targets)
