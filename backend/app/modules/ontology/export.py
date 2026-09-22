"""온톨로지 **구조**를 파일로 — JSON 하나, 또는 표 여럿이 든 엑셀 하나.

## 왜 둘인가

    JSON    기계가 읽는 것. **그대로 다시 넣을 수 있다** — `POST /ontology/import` 가
            받는 모양 그대로다. 옮겨 심기·백업·쌍둥이에 심기가 이것 하나로 된다.
    엑셀    사람이 읽는 것. 정의 검토는 회의에서 하고, 회의에 들고 가는 것은 표다.
            화면을 스무 번 눌러 옮겨 적는 일을 없앤다.

**JSON 에 봉투를 씌우지 않는다.** `{"format": …, "exported_at": …, "groups": …}` 처럼
한 겹 두르면 보기에는 친절하지만 가져오기가 모르는 열쇠라며 거절한다(`importer.TOP_KEYS`).
「내보낸 파일을 그대로 다시 넣을 수 있다」 는 성질이 봉투보다 훨씬 쓸모 있다.

## 엑셀의 시트 구성

    개요        몇 개씩 있나 · 언제 뽑았나
    묶음 · 타입 · 속성 · 관계 종류 · 관계 속성 · 참조 칸    각각 한 표
    타입마다    그 타입의 속성만 담은 표 — **개별 테이블**

전체 속성 표와 타입별 표를 **둘 다** 둔다. 하나로 줄이면 한쪽 일이 불편해진다:
「이 타입이 어떻게 생겼나」 는 타입별 표로 보고, 「단위를 안 적은 칸이 어디 있나」 는
전체 표를 걸러서 본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.objects import refedges
from app.modules.objects.bulk import MULTI_SEP
from app.modules.objects.models import ObjectInstance
from app.modules.ontology import importer
from app.modules.ontology.models import NavGroup, ObjectType, PropertyDef, RelationType
from app.shared.sheets import Page

#: 타입마다 시트를 두되 여기까지. 넘으면 개요에 **잘렸다고 적는다** — 말 안 하면 그 파일은
#: 「이게 전부」 로 읽힌다.
#:
#: 실제 설치는 타입이 수십 개다. 이 수는 그 자리를 위한 것이 아니라, 누가 기계로 수천 개를
#: 만들어 둔 설치에서 엑셀 파일이 열리지 않게 되는 것을 막는 빗장이다 — 속성 시트에는
#: **전부** 있으므로 잘려도 잃는 정보는 없다.
MAX_TYPE_SHEETS = 1000


def structure(db: Session) -> dict[str, Any]:
    """정의 전부 — **가져오기에 그대로 다시 넣을 수 있는 모양.**"""
    return importer.capture(db)


def _yes(value: Any) -> str:
    """참은 「예」, 거짓은 빈 칸. 열을 훑을 때 예만 눈에 걸리게 — 「아니오」 를 줄줄이
    적으면 정작 참인 칸이 안 보인다."""
    return "예" if value else ""


def _joined(value: Any) -> str:
    if isinstance(value, list):
        return MULTI_SEP.join(str(one) for one in value)
    return "" if value is None else str(value)


PROPERTY_HEADER = [
    "key",
    "이름",
    "자료형",
    "필수",
    "여럿",
    "단위",
    "고를 값",
    "참조 타입",
    "유일",
    "구획",
    "순서",
    "최소",
    "최대",
    "소수",
    "정규식",
    "기본값",
    "도움말",
]


def _property_row(one: PropertyDef) -> list[Any]:
    return [
        one.key,
        one.label,
        one.data_type,
        _yes(one.required),
        _yes(one.multi),
        one.unit,
        _joined(one.enum_options),
        one.ref_type_slug or "",
        _yes(one.unique),
        one.section,
        one.sort_order,
        one.min_value,
        one.max_value,
        one.decimals,
        one.pattern or "",
        _joined(one.default_value),
        one.help,
    ]


def pages(db: Session) -> list[Page]:
    """엑셀에 담을 표 전부."""
    groups = list(db.scalars(select(NavGroup).order_by(NavGroup.sort_order, NavGroup.label)))
    types = list(
        db.scalars(select(ObjectType).order_by(ObjectType.sort_order, ObjectType.label))
    )
    relation_types = list(
        db.scalars(select(RelationType).order_by(RelationType.sort_order, RelationType.label))
    )
    group_slug = {one.id: one.slug for one in groups}

    props: dict[uuid.UUID, list[PropertyDef]] = {}
    for one in db.scalars(
        select(PropertyDef).order_by(PropertyDef.sort_order, PropertyDef.key)
    ):
        props.setdefault(one.owner_id, []).append(one)

    counts = {
        type_id: count
        for type_id, count in db.execute(
            select(ObjectInstance.type_id, func.count())
            .where(ObjectInstance.deleted_at.is_(None))
            .group_by(ObjectInstance.type_id)
        )
    }
    edges = refedges.kinds(db)
    type_props = {one.id: props.get(one.id, []) for one in types}
    relation_props = {one.id: props.get(one.id, []) for one in relation_types}
    shown = types[:MAX_TYPE_SHEETS]

    overview: list[list[Any]] = [
        ["내보낸 때", datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")],
        ["사이드바 묶음", len(groups)],
        ["타입", len(types)],
        ["속성", sum(len(one) for one in type_props.values())],
        ["관계 종류", len(relation_types)],
        ["관계 속성", sum(len(one) for one in relation_props.values())],
        ["참조 칸", len(edges)],
        ["객체", sum(counts.values())],
    ]
    if len(types) > len(shown):
        overview.append(
            ["타입별 시트", f"{len(shown)}개만 — 타입이 {len(types)}개라 뒤가 잘렸습니다"]
        )

    out = [
        Page("개요", ["항목", "값"], overview),
        Page(
            "묶음",
            ["slug", "이름", "아이콘", "대상", "순서", "쓰임", "타입 수"],
            [
                [
                    one.slug,
                    one.label,
                    one.icon,
                    one.audience,
                    one.sort_order,
                    _yes(one.is_active),
                    sum(1 for t in types if t.nav_group_id == one.id),
                ]
                for one in groups
            ],
        ),
        Page(
            "타입",
            [
                "slug",
                "이름",
                "묶음",
                "상위 타입",
                "분류",
                "비추는 원 표",
                "식별자 정책",
                "식별자 범위",
                "시간 축",
                "속성 수",
                "객체 수",
                "관리 주체",
                "쓰임",
                "설명",
            ],
            [
                [
                    one.slug,
                    one.label,
                    group_slug.get(one.nav_group_id, "") if one.nav_group_id else "",
                    one.parent_slug or "",
                    one.kind_class,
                    one.system_source,
                    one.key_policy,
                    one.key_scope,
                    one.temporal_kind,
                    len(type_props[one.id]),
                    counts.get(one.id, 0),
                    one.managed_by,
                    _yes(one.is_active),
                    one.description,
                ]
                for one in types
            ],
        ),
        Page(
            "속성",
            ["타입", "타입 이름", *PROPERTY_HEADER],
            [
                [one.slug, one.label, *_property_row(definition)]
                for one in types
                for definition in type_props[one.id]
            ],
        ),
        Page(
            "관계 종류",
            [
                "slug",
                "이름",
                "역방향 이름",
                "출발 타입",
                "도착 타입",
                "방향 있음",
                "이행",
                "비순환",
                "개수 제한",
                "관리 주체",
                "쓰임",
                "설명",
            ],
            [
                [
                    one.slug,
                    one.label,
                    one.inverse_label,
                    _joined(one.src_type_slugs),
                    _joined(one.dst_type_slugs),
                    _yes(one.directed),
                    _yes(one.transitive),
                    _yes(one.acyclic),
                    one.cardinality,
                    one.managed_by,
                    _yes(one.is_active),
                    one.description,
                ]
                for one in relation_types
            ],
        ),
    ]

    # 관계에 붙은 속성은 없는 설치가 많다 — 없으면 빈 시트를 두지 않는다.
    relation_rows = [
        [one.slug, one.label, *_property_row(definition)]
        for one in relation_types
        for definition in relation_props[one.id]
    ]
    if relation_rows:
        out.append(Page("관계 속성", ["관계", "관계 이름", *PROPERTY_HEADER], relation_rows))

    # **참조 칸도 타입과 타입을 잇는 길이다.** 관계 종류만 보면 그림의 절반을 놓친다.
    out.append(
        Page(
            "참조 칸",
            ["출발 타입", "칸(key)", "칸 이름", "도착 타입", "여럿", "역방향 이름"],
            [
                [
                    kind.src_type.slug,
                    kind.key,
                    kind.label,
                    kind.dst_type.slug,
                    _yes(kind.multi),
                    kind.inverse_label,
                ]
                for kind in edges.values()
            ],
        )
    )

    # **타입마다 하나.** 시트 이름은 사람이 찾는 이름으로 — 겹치면 sheet_name 이 번호를 붙인다.
    for object_type in shown:
        out.append(
            Page(
                object_type.label or object_type.slug,
                PROPERTY_HEADER,
                [_property_row(d) for d in type_props[object_type.id]],
            )
        )
    return out
