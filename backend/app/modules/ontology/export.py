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
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import bulk, refedges
from app.modules.objects.bulk import MULTI_SEP
from app.modules.objects.models import ObjectInstance
from app.modules.objects.services import properties_of
from app.modules.ontology import importer
from app.modules.ontology.models import NavGroup, ObjectType, PropertyDef, RelationType
from app.modules.workspaces.models import Workspace
from app.shared.errors import AppError
from app.shared.permissions import visible_owner_clause
from app.shared.sheets import Page

#: 타입마다 시트를 두되 여기까지. 넘으면 개요에 **잘렸다고 적는다** — 말 안 하면 그 파일은
#: 「이게 전부」 로 읽힌다.
#:
#: 실제 설치는 타입이 수십 개다. 이 수는 그 자리를 위한 것이 아니라, 누가 기계로 수천 개를
#: 만들어 둔 설치에서 엑셀 파일이 열리지 않게 되는 것을 막는 빗장이다 — 속성 시트에는
#: **전부** 있으므로 잘려도 잃는 정보는 없다.
MAX_TYPE_SHEETS = 1000

#: 데이터를 함께 낼 때 **타입 하나에서 가져올 행의 상한.** 넘으면 개요에 적는다.
#: 엑셀 한 장은 백만 줄까지 들어가지만, 그 전에 파일이 열리지 않고 만드는 쪽도 메모리에
#: 다 올린다 — 그만한 양은 타입별 내보내기(작업)로 받는 편이 맞다.
MAX_DATA_ROWS = 50_000

#: 구조 시트의 이름. 데이터까지 낼 때는 이것만 남기고 **타입 시트를 객체 행으로 바꾼다.**
STRUCTURE_SHEETS = ("개요", "묶음", "타입", "속성", "관계 종류", "관계 속성", "참조 칸")

#: (단계, 처리한 수, 전체). 워커가 진행률을 표에 쓴다. 없으면 조용히.
Progress = Callable[[str, int, int], None] | None


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


# --- 채워진 것까지 ---------------------------------------------------------------
#
# 구조만 있는 파일은 「어떤 칸이 있나」 에 답하고, 데이터가 든 파일은 「무엇이 들어 있나」 에
# 답한다. 두 물음은 같은 회의에서 함께 나온다 — 그래서 한 파일에 담는다.
#
# **타입 시트에는 객체 행이 들어간다**(구조만 낼 때는 속성 정의였다). 정의는 「속성」 시트에
# 전부 있으므로 잃는 것이 없고, 열은 **일괄 입력이 받는 열 그대로**라 고쳐서 그대로 다시
# 붙여 넣을 수 있다. 그래서 소유 부서 열을 덧붙이지 않는다 — 모르는 열은 가져오기가 거절한다.
# 부서까지 옮기려면 JSON 묶음으로 받는다(부서마다 묶음이 나뉜다).


def _tick(progress: Progress, stage: str, done: int, total: int) -> None:
    if progress is not None:
        progress(stage, done, total)


def _data_types(db: Session) -> list[ObjectType]:
    """행을 가질 수 있는 타입만 — 투영(system)은 원 표를 비추므로 제 행이 없다."""
    return list(
        db.scalars(
            select(ObjectType)
            .where(ObjectType.kind_class != "system")
            .order_by(ObjectType.sort_order, ObjectType.label)
        )
    )


def _objects_of(db: Session, user: User, object_type: ObjectType) -> list[ObjectInstance]:
    """**볼 수 있는 것만.** 목록과 같은 가시성 규칙이다 — 내보내기라고 남의 부서 것이
    따라 나가면, 화면에서 막은 것을 파일이 연다."""
    return list(
        db.scalars(
            select(ObjectInstance)
            .where(
                ObjectInstance.type_id == object_type.id,
                ObjectInstance.deleted_at.is_(None),
                visible_owner_clause(user, ObjectInstance.owner_workspace_id),
            )
            .order_by(ObjectInstance.created_at, ObjectInstance.id)
            .limit(MAX_DATA_ROWS + 1)
        )
    )


def _relations_of(
    db: Session, user: User, object_type: ObjectType, skipped: list[str]
) -> list[dict[str, Any]]:
    """한 타입에서 출발하는 관계 — **못 읽으면 그 타입만 건너뛴다.**

    끝점이 투영(부서·계정) 타입인 관계는 그 원 표가 이 설치에 등록돼 있어야 이름을 푼다.
    확장을 끈 설치에서는 그것이 없을 수 있는데, 그때 통째 내보내기가 예외로 죽으면 **한
    타입 때문에 파일 전체를 못 받는다.** 건너뛴 것은 개요에 적는다 — 말 안 하면 그 파일은
    「관계가 없다」 로 읽힌다.
    """
    try:
        return bulk.export_relations(db, user, object_type)
    except AppError as caught:
        skipped.append(f"{object_type.label}: {caught.message}")
        return []


def data_pages(db: Session, user: User, *, progress: Progress = None) -> list[Page]:
    """구조 시트 + **타입마다 그 타입의 객체 행** + 관계 한 표."""
    out = [one for one in pages(db) if one.name in STRUCTURE_SHEETS]
    types = _data_types(db)
    total = len(types)
    cut: list[str] = []
    skipped: list[str] = []
    relations: list[list[Any]] = []
    rows_total = 0

    for done, object_type in enumerate(types, start=1):
        _tick(progress, "읽기", done, total)
        defs = properties_of(db, object_type.id)
        found = _objects_of(db, user, object_type)
        if len(found) > MAX_DATA_ROWS:
            cut.append(f"{object_type.label}: 앞 {MAX_DATA_ROWS:,}행만")
            found = found[:MAX_DATA_ROWS]
        rows_total += len(found)
        columns = bulk.export_columns(defs)
        records = bulk.export_rows(db, defs, found)
        out.append(
            Page(
                object_type.label or object_type.slug,
                columns,
                [[record.get(column, "") for column in columns] for record in records],
            )
        )
        relations.extend(
            [object_type.slug, row["src"], row["relation"], row["dst"], row["evidence_note"]]
            for row in _relations_of(db, user, object_type, skipped)
        )

    out.append(Page("관계", ["출발 타입", *bulk.RELATION_COLUMNS], relations))

    # **개요가 이 파일이 무엇인지 말한다** — 시트 이름만 보고는 「속성 정의」 인지 「객체 행」
    # 인지 알 수 없다.
    overview = next(one for one in out if one.name == "개요")
    overview.rows.append(
        ["타입 시트", "그 타입의 객체 행 — 일괄 입력에 그대로 붙일 수 있는 열"]
    )
    overview.rows.append(["내보낸 객체", rows_total])
    overview.rows.append(["내보낸 관계", len(relations)])
    overview.rows.append(
        ["소유 부서", "엑셀에는 없습니다 — 부서까지 옮기려면 JSON 으로 받으세요"]
    )
    if cut:
        overview.rows.append(["잘린 타입", "; ".join(cut)])
    if skipped:
        overview.rows.append(["관계를 못 읽은 타입", "; ".join(skipped)])
    return out


def bundle(
    db: Session, user: User, *, progress: Progress = None, skipped: list[str] | None = None
) -> dict[str, Any]:
    """정의 · 객체 · 관계를 **묶음 하나로** — `POST /bundles/import` 가 받는 모양 그대로다.

    객체는 **소유 부서마다 나눠 담는다.** 묶음의 한 덩이는 부서 하나를 뜻하므로, 그렇게
    나누면 받는 쪽에서 부서까지 그대로 선다 — 한 덩이로 뭉치면 그 정보가 사라진다.

    **같은 설치에 도로 넣는 것은 보장하지 않는다.** 허브가 관리하는 타입이 섞여 있으면 그것은
    이쪽에서 못 고치게 막혀 있고(그것이 「관리」 의 뜻이다), 그러면 통째로 다시 넣기는
    거절된다. 이 묶음의 쓰임새는 **새 설치에 심는 것**과 백업이다.
    """
    out: dict[str, Any] = {
        "ontology": importer.capture(db),
        "objects": [],
        "relations": [],
    }
    slugs = {one.id: one.slug for one in db.scalars(select(Workspace))}
    types = _data_types(db)
    total = len(types)
    for done, object_type in enumerate(types, start=1):
        _tick(progress, "읽기", done, total)
        defs = properties_of(db, object_type.id)
        found = _objects_of(db, user, object_type)[:MAX_DATA_ROWS]
        by_workspace: dict[str | None, list[ObjectInstance]] = {}
        for row in found:
            key = slugs.get(row.owner_workspace_id) if row.owner_workspace_id else None
            by_workspace.setdefault(key, []).append(row)
        for workspace_slug, group in by_workspace.items():
            records = bulk.export_rows(db, defs, group)
            for record in records:
                # id 는 이 설치의 것이다 — 다른 설치에 넣으면 아무것도 안 가리킨다.
                record.pop("id", None)
            out["objects"].append(
                {
                    "type_slug": object_type.slug,
                    "workspace_slug": workspace_slug,
                    "rows": records,
                }
            )
        edges = _relations_of(db, user, object_type, skipped if skipped is not None else [])
        if edges:
            out["relations"].append({"type_slug": object_type.slug, "rows": edges})
    return out
