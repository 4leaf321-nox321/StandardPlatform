"""비슷한 정의 — 새로 만들 정의를 **이미 있는 것과 견준다**(ADR 0091).

바깥 AI 가 문서마다 정의를 뽑아 넣으면, 이미 있는 `공급사` 옆에 `협력사` 가 새로 선다.
미리 보기는 그것을 「타입 생성」 한 줄로만 보여서, 사람은 같은 것이 둘이 된 줄 모르고
적용한다 — 나중에 객체가 두 타입에 갈려 쌓이면 치우기가 훨씬 비싸다. 그래서 정의 가져오기 ·
묶음 가져오기의
**미리 보기**가 새 정의마다 가까운 것을 함께 말한다. **막지 않는다** — 같은 이름의 다른 개념도
있다. 판단은 사람이(AI 는 사람에게 묻는다).

## 무엇을 견주나

- **타입 · 관계 종류** — 이름이 같은 것(글자), 그리고 뜻이 가까운 것(서버 안의 bge-m3,
  ADR 0050). 글은 「이름 (slug)」 — 설명 · 칸까지 넣으면 칸 이름이 겹치는 다른 타입끼리
  가까워져 오히려 흐려졌다(합성 짝 18개로 잰 것, ADR 0091). 같은 파일이 함께 만드는 것끼리도
  본다.
- **속성** — 같은 키나 같은 이름인데 **종류 · 단위가 다른** 칸만(글자). 「무게」 가 부품은
  숫자(kg)인데 이번 것은 글이면, 둘을 함께 세거나 인터페이스로 묶을 수 없다. 이름만 같고
  모양도 같은 칸은 말하지 않는다 — 그것은 정의 점검(`checkup`)의 「인터페이스 후보」 다.

## 뜻 비교의 비용

정의는 많아야 수백 개라 그 자리에서 계산한다. 계산한 것은 프로세스 안에 둔다(`_cache` — 같은
모델 · 같은 글이면 다시 묻지 않는다). 처음 한 번은 모델을 메모리에 올리는 몇 초가 든다. 못
계산하면(꺼짐 · 닿지 않음 · 시간 초과) 글자로 같은 것만 말하고 그 사실을 적는다(`meaning`).
"""

from __future__ import annotations

import math
import threading
import unicodedata
from array import array
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.ontology.retype import KIND_LABELS
from app.shared import embedding

#: 뜻이 이만큼 가까우면 「비슷한 정의」 다 — 타입. 합성 짝으로 잰 값(ADR 0091): 같은 것 0.68 ~
#: 0.89, 다른 것 0.54 ~ 0.63.
TYPE_MIN = 0.68
#: 관계 종류 — 이름이 짧은 동사라 갈림이 약하다(같은 것 0.62 ~ 0.88, 다른 것 0.49 ~ 0.67).
#: 분명한 것만 말하도록 높게 둔다.
RELATION_MIN = 0.74
#: 새 정의 하나마다 가까운 것을 몇 개까지.
NEAR_MAX = 3
#: 뜻 계산을 기다리는 시간 — 처음 한 번은 모델을 올리는 몇 초가 든다. 넘으면 글자로만.
EMBED_SECONDS = 10.0
#: 한 번에 묻는 글의 수.
EMBED_BATCH = 64
#: 프로세스 안에 두는 뜻의 자리 수 — 하나에 4KB(float32 1,024개).
CACHE_MAX = 4096
#: 속성 경고 줄의 상한 — 넘으면 수만 말한다.
PROPERTY_LINES = 20

_cache: OrderedDict[tuple[str, str], array[float]] = OrderedDict()
_lock = threading.Lock()


def forget() -> None:
    """뜻의 자리를 비운다 — 시험이 가짜 모델을 바꿀 때."""
    with _lock:
        _cache.clear()


def norm(text: str) -> str:
    """이름 견주기 — 폭 · 대소문자 · 띄어쓰기 · 구분 기호를 맞춘다(「공급 사」 =
    「공급사」)."""
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def _text(label: str, slug: str) -> str:
    return f"{label} ({slug})"


@dataclass
class Near:
    kind: str
    """`type` · `relation_type` · `property`."""
    slug: str
    """속성이면 `<타입>.<키>`."""
    label: str
    why: str
    """「이름이 같음」 · 「뜻이 가까움」 · 그 칸의 모양(속성)."""
    score: float | None = None
    """뜻의 가까움(코사인) — 이름이 같아서 든 것이면 None."""
    pending: bool = False
    """같은 파일이 함께 만드는 것(아직 없다)."""


@dataclass
class Similar:
    kind: str
    """`type` · `relation_type` · `property`."""
    slug: str
    label: str
    near: list[Near] = field(default_factory=list)
    shape: str = ""
    """속성이면 이번 것의 모양(「글 (한 줄)」 · 「숫자 · kg」)."""


@dataclass
class Found:
    items: list[Similar] = field(default_factory=list)
    meaning: str = "none"
    """뜻으로 견주었나 — `ready` · `off`(꺼둔 설치) · `unavailable`(못 계산함, `reason`) ·
    `none`(새 타입 · 관계 종류가 없다)."""
    reason: str = ""

    def lines(self) -> list[str]:
        """사람이 읽는 경고 줄 — 미리 보기의 `warnings` 에 싣는다."""
        out: list[str] = []
        props = 0
        for one in self.items:
            if one.kind == "property":
                props += 1
                if props > PROPERTY_LINES:
                    continue
                others = " · ".join(
                    f"「{near.label}」({near.slug}) {near.why}" for near in one.near
                )
                out.append(
                    f"같은 칸 다른 모양 — 새 속성 「{one.label}」({one.slug}) {one.shape} · "
                    f"{others}. 같은 뜻이면 종류 · 단위를 맞추십시오(함께 세거나 인터페이스로 "
                    "묶을 수 있게)."
                )
                continue
            what = "타입" if one.kind == "type" else "관계 종류"
            others = " · ".join(_near_words(near) for near in one.near)
            out.append(
                f"비슷한 정의 — 새 {what} 「{one.label}」({one.slug}) ↔ {others}. 같은 것이면 "
                f"새로 생성하지 말고 그 {what}을(를) 사용하십시오."
            )
        if props > PROPERTY_LINES:
            out.append(f"같은 칸 다른 모양 — 외 {props - PROPERTY_LINES}건.")
        if self.meaning == "unavailable" and self.reason:
            out.append(
                "비슷한 정의를 이름으로만 비교했습니다 — 뜻으로 비교하지 못함: " + self.reason
            )
        return out


def _near_words(near: Near) -> str:
    where = "같은 파일의 " if near.pending else ""
    why = f"뜻이 가까움 {near.score:.2f}" if near.score is not None else near.why
    return f"{where}「{near.label}」({near.slug}, {why})"


# --- 뜻 ---------------------------------------------------------------------------


def _vectors(texts: list[str]) -> dict[str, array[float]]:
    """글 → 뜻의 자리. 프로세스 안에 둔 것은 다시 묻지 않는다. 못 바꾸면 `Unavailable`."""
    model = get_settings().embedding_model
    out: dict[str, array[float]] = {}
    missing: list[str] = []
    with _lock:
        for text in dict.fromkeys(texts):
            got = _cache.get((model, text))
            if got is None:
                missing.append(text)
            else:
                _cache.move_to_end((model, text))
                out[text] = got
    for at in range(0, len(missing), EMBED_BATCH):
        part = missing[at : at + EMBED_BATCH]
        found = embedding.embed(part, seconds=EMBED_SECONDS)
        with _lock:
            for text, vector in zip(part, found, strict=True):
                packed = array("f", vector)
                _cache[(model, text)] = packed
                out[text] = packed
            while len(_cache) > CACHE_MAX:
                _cache.popitem(last=False)
    return out


def _cosine(a: array[float], b: array[float]) -> float:
    """Ollama 는 길이 1 로 맞춘 값을 준다 — 내적이 곧 코사인이다. 혹시 몰라 나눈다."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / size if size else 0.0


# --- 견주기 -----------------------------------------------------------------------


@dataclass
class _Def:
    slug: str
    label: str
    pending: bool = False


def _compare(
    kind: str,
    incoming: list[_Def],
    existing: list[_Def],
    *,
    threshold: float,
    vectors: dict[str, array[float]] | None,
) -> list[Similar]:
    """새 것마다 — 이름이 같은 것 먼저, 그다음 뜻이 가까운 것. 같은 파일의 앞선 새 것도
    본다."""
    out: list[Similar] = []
    for at, new in enumerate(incoming):
        # 같은 파일의 새 것끼리는 **뒤의 것에** 한 번만 말한다.
        others = existing + [_Def(one.slug, one.label, pending=True) for one in incoming[:at]]
        others = [one for one in others if one.slug != new.slug]
        near: list[Near] = []
        named = set()
        for one in others:
            if norm(one.label) and norm(one.label) == norm(new.label):
                same = Near(kind, one.slug, one.label, "이름이 같음", pending=one.pending)
                near.append(same)
                named.add(one.slug)
        if vectors is not None:
            mine = vectors[_text(new.label, new.slug)]
            scored = [
                (_cosine(mine, vectors[_text(one.label, one.slug)]), one)
                for one in others
                if one.slug not in named
            ]
            scored.sort(key=lambda pair: (-pair[0], pair[1].slug))
            near += [
                Near(kind, one.slug, one.label, "뜻이 가까움", round(score, 3), one.pending)
                for score, one in scored
                if score >= threshold
            ]
        if near:
            out.append(Similar(kind, new.slug, new.label, near[:NEAR_MAX]))
    return out


def _created(changes: Iterable[Any], kind: str) -> set[str]:
    return {one.slug for one in changes if one.kind == kind and one.action == "create"}


def _shape(data_type: str, unit: str) -> str:
    kind = KIND_LABELS.get(data_type, data_type)
    return f"{kind} · {unit}" if data_type == "number" and unit else kind


@dataclass
class _Prop:
    owner: str
    owner_label: str
    key: str
    label: str
    data_type: str
    unit: str

    @property
    def name(self) -> str:
        return f"{self.owner}.{self.key}"

    @property
    def shape(self) -> str:
        return _shape(self.data_type, self.unit)

    def differs(self, other: _Prop) -> bool:
        if self.data_type != other.data_type:
            return True
        return self.data_type == "number" and (self.unit or "") != (other.unit or "")


def _same_field(a: _Prop, b: _Prop) -> bool:
    """같은 키이거나 같은 이름(맞춘 글자) — 사람이 같은 칸으로 읽을 것."""
    return a.key == b.key or (bool(norm(a.label)) and norm(a.label) == norm(b.label))


def _properties(
    db: Session, payload_types: list[dict[str, Any]], changes: list[Any]
) -> list[Similar]:
    """새 속성마다 — 다른 타입의 같은 키 · 같은 이름 칸 중 **모양이 다른** 것."""
    created = _created(changes, "property")
    type_labels = {row.id: (row.slug, row.label) for row in db.scalars(select(ObjectType))}
    existing: list[_Prop] = []
    for row in db.scalars(select(PropertyDef).where(PropertyDef.owner_kind == "type")):
        owner = type_labels.get(row.owner_id)
        if owner is not None:
            existing.append(
                _Prop(owner[0], owner[1], row.key, row.label, row.data_type, row.unit or "")
            )
    label_of = dict(type_labels.values())
    incoming: list[_Prop] = []
    for one in payload_types:
        slug = str(one.get("slug") or "")
        label = str(one.get("label") or label_of.get(slug, slug))
        for prop in one.get("properties") or []:
            if not isinstance(prop, dict) or not prop.get("key"):
                continue
            incoming.append(
                _Prop(
                    slug,
                    label,
                    str(prop["key"]),
                    str(prop.get("label") or prop["key"]),
                    str(prop.get("data_type") or "text"),
                    str(prop.get("unit") or ""),
                )
            )
    # 이 파일이 다시 적는 칸은 「있는 것」 의 자리를 이 파일의 모양으로 바꿔 본다.
    sent = {one.name for one in incoming}
    pool = [one for one in existing if one.name not in sent] + incoming
    out: list[Similar] = []
    for new in incoming:
        if new.name not in created:
            continue
        matched = [
            other
            for other in pool
            if other.owner != new.owner and _same_field(new, other) and new.differs(other)
        ]
        # **같은 키가 먼저** — 인터페이스로 묶거나 함께 셀 때 바로 부딪히는 것은 키다. 이름만
        # 같은 칸(「무게」 가 여러 타입에)은 그 뒤.
        matched.sort(key=lambda other: (other.key != new.key, other.name))
        near = [
            Near("property", other.name, f"{other.owner_label}.{other.label}", other.shape)
            for other in matched
        ]
        if near:
            out.append(
                Similar(
                    "property",
                    new.name,
                    f"{new.owner_label}.{new.label}",
                    near[:2],
                    shape=new.shape,
                )
            )
    return out


def check(db: Session, payload: dict[str, Any], changes: list[Any]) -> Found:
    """정의 가져오기의 계획(`changes`)이 **새로 만드는** 타입 · 관계 종류 · 속성을 이미 있는
    것과 견준다. 아무것도 안 바꾼다."""
    out = Found()
    payload_types = [one for one in payload.get("types") or [] if isinstance(one, dict)]
    payload_relations = [
        one for one in payload.get("relation_types") or [] if isinstance(one, dict)
    ]
    new_types = _created(changes, "type")
    new_relations = _created(changes, "relation_type")
    incoming_types = [
        _Def(str(one["slug"]), str(one.get("label") or one["slug"]))
        for one in payload_types
        if one.get("slug") in new_types
    ]
    incoming_relations = [
        _Def(str(one["slug"]), str(one.get("label") or one["slug"]))
        for one in payload_relations
        if one.get("slug") in new_relations
    ]
    # 묶음 미리 보기는 정의를 적용한 뒤(바깥이 롤백한다) 부른다 — 이번에 만든 것은 「있는
    # 것」 이 아니다.
    existing_types = [
        _Def(row.slug, row.label)
        for row in db.scalars(select(ObjectType))
        if row.slug not in new_types
    ]
    existing_relations = [
        _Def(row.slug, row.label)
        for row in db.scalars(select(RelationType))
        if row.slug not in new_relations
    ]

    vectors: dict[str, array[float]] | None = None
    if incoming_types or incoming_relations:
        if not embedding.enabled():
            out.meaning = "off"
        else:
            texts = [
                _text(one.label, one.slug)
                for one in [
                    *incoming_types,
                    *incoming_relations,
                    *(existing_types if incoming_types else []),
                    *(existing_relations if incoming_relations else []),
                ]
            ]
            try:
                vectors = _vectors(texts)
                out.meaning = "ready"
            except embedding.Unavailable as caught:
                out.meaning = "unavailable"
                out.reason = str(caught)

    out.items += _compare(
        "type", incoming_types, existing_types, threshold=TYPE_MIN, vectors=vectors
    )
    out.items += _compare(
        "relation_type",
        incoming_relations,
        existing_relations,
        threshold=RELATION_MIN,
        vectors=vectors,
    )
    out.items += _properties(db, payload_types, changes)
    return out
