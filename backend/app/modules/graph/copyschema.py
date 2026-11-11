"""그래프 사본의 모양 — Cypher 를 쓰는 쪽(AI)이 **추측하지 않게** 라벨 · 관계 종류 · 속성을
SP 의 정의에서 그대로 알려 준다(`copyexport` 와 같은 이름 규칙).

사본에 실제로 실린 수는 판 표시(`_SPCopy`)에 있고, 타입마다의 수는 SP 의 계수표(`typecounts`)로
— 사본에 묻지 않는다(사본이 꺼져 있어도 모양은 보인다).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.modules.graph import copyexport
from app.modules.objects import typecounts
from app.modules.objects.scope import as_scope


@dataclass
class PropShape:
    name: str
    """사본의 속성 이름(`p_<키>` 일 수 있다)."""
    label: str
    kind: str
    """`string` · `double` · `boolean`, 여러 값이면 `[]`. 날짜는 ISO 글자."""


@dataclass
class LabelShape:
    label: str
    """사본의 라벨 = 타입 slug."""
    name: str
    count: int
    props: list[PropShape] = field(default_factory=list)


@dataclass
class RelShape:
    type: str
    """사본의 관계 종류 — 관계 slug, 참조 칸은 `ref_<칸>`."""
    name: str
    start: list[str]
    """출발 쪽 라벨들(비면 정하지 않은 관계)."""
    end: list[str]
    kind: str
    """`relation`(관계 — 오늘 유효한 선만) · `ref`(참조 칸)."""
    props: list[PropShape] = field(default_factory=list)
    transitive: bool = False
    """이어지는 관계(BOM 처럼 몇 단계든 따라간다 — `*1..20`)."""


@dataclass
class Shape:
    labels: list[LabelShape]
    relationships: list[RelShape]
    rules: list[str]


RULES = [
    "모든 노드에 라벨 `Obj` 와 타입 slug 라벨이 있다 — `MATCH (p:gb_part)`.",
    "노드 속성 `id`(SP 의 uuid) · `key` · `label`(이름) · `status` · `ws`(소유 부서) + 그 "
    "타입의 칸.",
    "관계 종류는 관계 slug(방향은 SP 의 출발 → 도착), 참조 칸은 `ref_<칸>`(가리키는 쪽 → "
    "가리켜지는 쪽) — 「모델 M 을 가리키는 시험」 은 `(t:test)-[:ref_model]->(m:model)`.",
    "관계는 **오늘 유효한 선만** 실려 있다(기간이 지난 선 · 앞으로의 선은 없다 — 그 날의 "
    "구성은 SP 의 `as_of`). 선의 칸(`qty` …)과 `valid_from` · `valid_to` 가 선의 속성이다.",
    "날짜 · 시각은 ISO 글자다 — `e.date >= '2026-05-05'` 처럼 글자로 견준다.",
    "이어지는 관계를 끝까지 따라가면 `-[:bom*1..20]->` — 걸음 수 상한을 꼭 둔다.",
    "값은 `$이름` 매개변수로 넘긴다(`params`). 한 번에 한 문장, 읽기만.",
]


def shape(db: Session) -> Shape:
    counted = typecounts.counts(db)
    labels: list[LabelShape] = []
    refs: list[RelShape] = []
    for row in copyexport.object_types(db):
        defs = as_scope(db, row).defs
        props = [
            PropShape(copyexport.prop_name(one.key), one.label, copyexport.value_kind(one))
            for one in defs
            if copyexport.copied(one)
        ]
        labels.append(LabelShape(row.slug, row.label, int(counted.get(row.id, 0)), props))
        for one in defs:
            if one.data_type == "object_ref" and one.ref_type_slug:
                refs.append(
                    RelShape(
                        copyexport.ref_type(one.key),
                        f"{row.label} › {one.label}",
                        [row.slug],
                        [one.ref_type_slug],
                        "ref",
                    )
                )
    owned = copyexport.edge_defs(db)
    relations = [
        RelShape(
            relation.slug,
            relation.label,
            list(relation.src_type_slugs or []),
            list(relation.dst_type_slugs or []),
            "relation",
            [
                PropShape(one.key, one.label, copyexport.value_kind(one))
                for one in owned.get(relation.id, [])
                if copyexport.copied(one) and one.key not in copyexport.FIXED
            ],
            transitive=bool(relation.transitive),
        )
        for relation in copyexport.relation_types(db)
    ]
    return Shape(labels, relations + refs, RULES)
