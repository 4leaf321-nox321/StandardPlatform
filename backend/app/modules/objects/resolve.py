"""이름 → 객체 **해소**, 그리고 **빈 결과의 이유**.

두 가지를 여기 둔다. 둘 다 「사람이 아니라 기계(AI)가 읽는 답」 을 만드는 일이고,
둘 다 **틀린 답보다 「모른다」 가 낫다** 는 같은 원칙 위에 서 있다.

## 해소 — 짐작하지 않는다

이름으로 무언가를 가리키는 자리(참조 칸·관계 잇기·찾기)에서, 모델은 목록의 **첫
줄을 집는다 — 틀린 줄도 첫 줄이면 집는다.** 그래서 목록을 주지 않고 판정을 준다:

    exact        하나로 정해졌다. 이 id 로 써라.
    candidates   여럿이다. **쓰지 말고** 사람에게 어느 것인지 물어라.
    none         없다. 새로 만들지, 오타인지 사람에게 물어라.

맞춰 보는 차례는 좁은 것부터다 — 식별자 → 별칭·외부 식별자 → 이름 → 포함.
앞 단계에서 정해지면 뒤는 안 본다(「Ansys」 라는 식별자가 있는데 이름에 「Ansys」 가
들어간 것 열둘 때문에 흐려지면 안 된다). **포함으로 하나만 걸려도 `exact` 가 아니다**
— 포함은 짐작이고, 짐작을 `exact` 로 부르면 그 짐작이 그대로 저장된다.

## 진단 — 「없다」 와 「안 보인다」 와 「조건이 좁다」 는 다른 일이다

목록이 0건이면 모델은 그것을 「그런 것은 없다」 로 읽고, 없는 것을 새로 만든다.
셋을 갈라 말한다:

    empty_type   이 타입에 객체가 하나도 없다 — 채울 때다
    not_visible  있지만 내 부서 밖이라 안 보인다 — 권한 문제지 데이터 문제가 아니다
    filters      조건이 좁다 — **어느 조건을 빼면 몇 건인지**까지 말한다

조건마다 「이것만 빼면 n 건」 과 함께 **「그 칸이 비어 있어 빠진 것 k 건」** 을 센다.
조건에 안 맞아 빠진 것과 값이 없어서 빠진 것은 다른 일이고, 그 둘을 한 덩어리로
0 이라 말하면 모델은 「조건에 맞는 것이 없다」 고 단정한다 — **모르는 것은 모른다고
말해야 다음 물음이 나온다.**
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import aliases, system
from app.modules.objects.models import ObjectAlias, ObjectInstance
from app.modules.ontology.models import ObjectType
from app.shared.permissions import visible_owner_clause
from app.shared.text import compare_key

CANDIDATE_CAP = 20
"""후보를 몇 개까지 보여 줄지. 더 많으면 고르라는 말 자체가 무의미하다 —
그때는 조건을 더 달라고 한다."""


@dataclass
class Hit:
    id: uuid.UUID
    key: str | None
    label: str
    aliases: list[str] = field(default_factory=list)
    status: str = "active"
    matched_by: str = "label"
    hint: str = ""


@dataclass
class Resolution:
    match: str
    """`exact` · `candidates` · `none`."""
    object: Hit | None = None
    candidates: list[Hit] = field(default_factory=list)
    hint: str = ""
    truncated: bool = False


def _hit(row: ObjectInstance, how: str, names: list[str]) -> Hit:
    return Hit(
        id=row.id,
        key=row.key,
        label=row.label,
        aliases=names,
        status=row.status,
        matched_by=how,
    )


def _hits(db: Session, rows: list[ObjectInstance], how: str) -> list[Hit]:
    names = aliases.human_of(db, [row.id for row in rows])
    return [_hit(row, how, names.get(row.id, [])) for row in rows]


def _visible(user: User, object_type: ObjectType) -> Any:
    return select(ObjectInstance).where(
        ObjectInstance.type_id == object_type.id,
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )


def _decide(db: Session, rows: list[ObjectInstance], how: str, *, sure: bool) -> Resolution:
    """한 단계의 결과를 판정으로. `sure=False` 면 하나뿐이어도 후보다(포함 단계)."""
    hits = _hits(db, rows[:CANDIDATE_CAP], how)
    if len(rows) == 1 and sure:
        return Resolution(match="exact", object=hits[0], hint="하나로 정해졌습니다.")
    return Resolution(
        match="candidates",
        candidates=hits,
        truncated=len(rows) > CANDIDATE_CAP,
        hint=(
            f"「{how}」 로 {len(rows)}개가 걸렸습니다. **어느 것인지 정하지 말고** "
            "사람에게 물어 id 를 받으세요."
            if len(rows) > 1
            else "이름의 일부가 겹쳐 하나 걸렸을 뿐입니다 — 맞는지 사람에게 확인하고 "
            "id 로 쓰세요."
        ),
    )


def _system(db: Session, user: User, object_type: ObjectType, text: str) -> Resolution:
    """원 표를 비추는 타입(부서·계정) — 식별자와 이름으로만 맞춘다."""
    source = system.source_of(object_type)
    rows, _total = source.search(db, user, text, CANDIDATE_CAP * 5, 0)
    norm = compare_key(text)

    def out(refs: list[Any], how: str, *, sure: bool) -> Resolution | None:
        if not refs:
            return None
        hits = [
            Hit(
                id=one.id,
                key=one.key,
                label=one.label,
                status="active" if one.active else "deprecated",
                matched_by=how,
                hint=one.hint,
            )
            for one in refs[:CANDIDATE_CAP]
        ]
        if len(refs) == 1 and sure:
            return Resolution(match="exact", object=hits[0], hint="하나로 정해졌습니다.")
        return Resolution(
            match="candidates",
            candidates=hits,
            truncated=len(refs) > CANDIDATE_CAP,
            hint=f"「{how}」 로 {len(refs)}개가 걸렸습니다. 사람에게 물어 id 를 받으세요.",
        )

    for refs, how, sure in (
        ([one for one in rows if compare_key(one.key) == norm], "key", True),
        ([one for one in rows if compare_key(one.label) == norm], "label", True),
        (rows, "contains", False),
    ):
        found = out(refs, how, sure=sure)
        if found is not None:
            return found
    return Resolution(
        match="none",
        hint=f"{object_type.label}에 「{text}」 이(가) 없습니다. 이 타입은 원 표를 "
        "비추므로 여기서 새로 만들 수 없습니다 — 그 표의 담당자에게 문의하세요.",
    )


def by_name(db: Session, user: User, object_type: ObjectType, text: str) -> Resolution:
    """이름 하나를 객체 하나로. **정해지지 않으면 정해지지 않았다고 말한다.**"""
    text = (text or "").strip()
    if not text:
        return Resolution(match="none", hint="찾을 이름이 비어 있습니다.")
    if system.is_system(object_type):
        return _system(db, user, object_type, text)

    base = _visible(user, object_type)
    norm = compare_key(text)

    # 0) 이미 id 로 왔다 — 있는 것인지만 확인한다(모양만 보고 받으면 없는 것을 가리킨다).
    try:
        as_id = uuid.UUID(text)
    except ValueError:
        as_id = None
    if as_id is not None:
        row = db.scalar(base.where(ObjectInstance.id == as_id))
        if row is not None:
            return _decide(db, [row], "id", sure=True)
        return Resolution(
            match="none", hint=f"그 id 의 {object_type.label}이(가) 없습니다: {text}"
        )

    # 1) 식별자 — 가장 좁다.
    rows = list(db.scalars(base.where(ObjectInstance.key == text)))
    if not rows:
        rows = [
            one
            for one in db.scalars(base.where(ObjectInstance.key.isnot(None)))
            if compare_key(one.key or "") == norm
        ]
    if rows:
        return _decide(db, rows, "key", sure=True)

    # 2) 별칭·외부 식별자 — 「앤시스」 로 적어도 「Ansys」 로 풀린다.
    found = aliases.lookup(db, object_type, text)
    if found:
        rows = list(db.scalars(base.where(ObjectInstance.id.in_(found))))
        if rows:
            return _decide(db, rows, "alias", sure=True)

    # 3) 이름이 그대로 같은 것.
    rows = [
        one
        for one in db.scalars(base.where(ObjectInstance.label.ilike(text)))
        if compare_key(one.label) == norm
    ]
    if rows:
        return _decide(db, rows, "label", sure=True)

    # 4) 포함 — 여기서 나온 것은 하나여도 짐작이다.
    pattern = f"%{text}%"
    rows = list(
        db.scalars(
            base.where(
                or_(
                    ObjectInstance.label.ilike(pattern),
                    ObjectInstance.key.ilike(pattern),
                    ObjectInstance.id.in_(
                        select(ObjectAlias.object_id).where(
                            ObjectAlias.type_id == object_type.id,
                            ObjectAlias.value.ilike(pattern),
                        )
                    ),
                )
            ).order_by(ObjectInstance.label)
        )
    )
    if rows:
        return _decide(db, rows, "contains", sure=False)

    return Resolution(
        match="none",
        hint=f"{object_type.label}에 「{text}」 이(가) 없습니다. 오타인지, 아직 안 만든 "
        "것인지 사람에게 확인하세요 — **짐작해서 다른 것을 쓰지 마세요.**",
    )


# --- 빈 결과 진단 -------------------------------------------------------------


@dataclass
class FilterEffect:
    name: str
    """주소에 적힌 그대로 — `f.power.gte=10` · `p.grade=A` · `q=ansys`."""
    label: str
    remaining: int
    """**이 조건 하나만 빼면** 몇 건인가."""
    unknown: int | None = None
    """그 칸에 **값이 없어서** 빠진 것. 조건에 안 맞아 빠진 것과 다른 일이다.
    다른 타입의 칸이면 **이어진 것이 아예 없는 것**도 여기 든다.
    셀 수 없는 조건(검색어·트리)은 None — **0 이 아니라 모른다는 뜻이다.**"""


@dataclass
class Diagnosis:
    total: int
    reason: str
    """`has_rows` · `empty_type` · `not_visible` · `filters`."""
    message: str
    type_total: int
    """조건을 다 뺐을 때, 내게 보이는 이 타입의 객체 수."""
    hidden: int
    """있지만 내 부서 밖이라 안 보이는 수."""
    filters: list[FilterEffect] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


def diagnose(
    db: Session,
    user: User,
    object_type: ObjectType,
    *,
    total: int,
    parts: list[tuple[str, str]],
    without: Any,
    unknown: Any,
    count: Any,
) -> Diagnosis:
    """0건이 **왜** 0건인지. `without(name)` 은 그 조건만 뺀 질의, `unknown(name)` 은
    그 칸이 비어 있어 빠진 수, `count(stmt)` 는 세기다 — 목록과 **같은 거르기**를
    쓰려고 부르는 쪽이 넘긴다(따로 적으면 진단이 목록과 어긋난다)."""
    if system.is_system(object_type):
        return Diagnosis(
            total=total,
            reason="has_rows" if total else "empty_type",
            message=(
                f"{object_type.label}은(는) 원 표를 그대로 비춥니다 — 조건은 검색어뿐입니다."
            ),
            type_total=total,
            hidden=0,
        )

    visible_total = count(_visible(user, object_type))
    everything = count(
        select(ObjectInstance).where(
            ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None)
        )
    )
    hidden = max(everything - visible_total, 0)

    if total > 0:
        return Diagnosis(
            total=total,
            reason="has_rows",
            message=f"{total}건입니다.",
            type_total=visible_total,
            hidden=hidden,
        )

    if visible_total == 0 and hidden == 0:
        return Diagnosis(
            total=0,
            reason="empty_type",
            message=f"{object_type.label}에 객체가 **하나도 없습니다** — 조건 때문이 "
            "아닙니다. 아직 안 채운 타입입니다.",
            type_total=0,
            hidden=0,
            next_steps=[
                "사람에게 이 타입을 채울 자료가 있는지 묻는다.",
                "자료가 있으면 objects_import 로 넣는다(apply=false 로 먼저 계획을 본다).",
            ],
        )

    if visible_total == 0:
        return Diagnosis(
            total=0,
            reason="not_visible",
            message=f"{object_type.label}에 {hidden}건이 있지만 **내 부서 밖이라 "
            "안 보입니다** — 없는 것이 아니라 권한이 없는 것입니다.",
            type_total=0,
            hidden=hidden,
            next_steps=["그 부서의 관리자에게 열람 권한을 요청한다."],
        )

    effects = [
        FilterEffect(
            name=name, label=label, remaining=count(without(name)), unknown=unknown(name)
        )
        for name, label in parts
    ]
    effects.sort(key=lambda one: -one.remaining)
    if not effects:
        # 조건이 없는데 0건인데 보이는 것은 있다 — 그 사이에 바뀐 것이다.
        return Diagnosis(
            total=0,
            reason="filters",
            message="조건이 없는데 0건입니다 — 다시 불러 보세요.",
            type_total=visible_total,
            hidden=hidden,
        )

    top = effects[0]
    tail = ""
    if top.unknown:
        tail = (
            f" 그 중 {top.unknown}건은 **그 칸이 비어 있어서** 빠졌습니다 — "
            "조건에 안 맞는 것과 값이 없는 것은 다른 일입니다."
        )
    return Diagnosis(
        total=0,
        reason="filters",
        message=f"{object_type.label}에 보이는 것은 {visible_total}건인데 조건이 좁아 "
        f"0건이 됐습니다. 「{top.label}」 하나만 빼면 {top.remaining}건입니다.{tail}",
        type_total=visible_total,
        hidden=hidden,
        filters=effects,
        next_steps=[
            f"「{top.label}」 을(를) 빼고 다시 묻는다.",
            "칸 이름이 맞는지 object_fields 로 확인한다 — 값이 비어 있는 칸일 수 있다.",
        ],
    )
