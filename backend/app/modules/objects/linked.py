"""이어진 칸 — 목록의 줄마다 **이어진 것의 값**을 싣는다(ADR 0043).

「제품 목록에 기본 모델 이름도 보여 줘」 를 지금까지는 줄마다 상세를 다시 읽어 풀었다 —
화면은 열 하나에 요청 50번, AI 는 `object_get` 50번이다. 그래서 목록이 주소(`columns`)를 받아
줄마다 그 주소의 값을 함께 싣는다.

    GET /api/objects/plm_model?columns=out.sku_base.label&columns=in.svc_case:model

## 주소는 조건과 같다

`ref.` · `out.` · `in.` 로 시작하는 주소, 넷까지 잇는 걸음, 선의 칸(`out.bom~qty` — 그 선의
수량), 기준일(`as_of` — 그날 살아 있는 선만). **해석기도 몸통도 조건과 같다**
(`paths.parse_chain` · `conditions.walk`) — 둘이 따로 걸으면 「조건에는 걸리는데 열에는 없는」
줄이 생긴다.

## 이어진 것마다 값 하나 — 상한까지만

값은 **이어진 것마다 하나**다(같은 값이 여러 번 나올 수 있다 — 증상 S07 인 기록 셋이면 S07
셋). 몇 종류 · 몇 건인지는 통계(`group_by`)의 몫이다. 축에서 기록으로 가는 주소
(`in.svc_case:model`)는 한 줄에 10만 값이 붙을 수 있어 **칸마다 `CAP` 까지만** 싣고 그보다
많으면 `more` 다 — 줄마다 `LATERAL (… LIMIT CAP+1)` 이라 **다 세지 않는다**(정확한 수는 말하지
않는다). 값은 쪽(최대 200줄) x 열 수만큼 읽는다. 어느 `CAP` 개인지는 정하지 않는다(정렬하면 다
읽어야 한다) — 받은 것은 이름 순으로 세운다.

## 가시성

걸음은 조건과 같은 규칙이다 — **볼 수 없는 객체는 걷지 않는다**(`Resolver.seen` · 선의 저쪽
끝). 그래서 볼 수 없는 상대의 값은 실리지 않는다. 끝의 값이 객체를 가리키면(관계로 이어진 것
자체 · 참조 칸) 이름은 목록의 참조 칸과 같은 규칙으로 — 볼 수 없으면 `system.HIDDEN_LABEL`,
지웠으면 「(지워짐)」.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, cast, select, true
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.objects import axes, conditions, paths, system
from app.modules.objects.models import ObjectInstance
from app.shared.errors import AppError, code

#: 칸 하나 · 줄 하나에 싣는 값의 상한 — 넘으면 `more`. 목록의 한 칸에 스물이면 이미 사람이 다
#: 못 읽는다.
CAP = 20
#: 한 요청의 이어진 칸 수 — 칸마다 쪽 크기만큼의 질의 하나다.
MAX_COLUMNS = 10


@dataclass(frozen=True)
class Column:
    """이어진 칸 하나 — 주소와 그 주소를 푼 것."""

    address: str
    chain: paths.Chain
    label: str
    data_type: str
    """끝 칸의 종류 — 관계로 이어진 것 자체면 `object`."""
    multi: bool
    """한 줄에 값이 여럿일 수 있나(여럿과 이어진 걸음 · 여러 값 칸)."""


@dataclass(frozen=True)
class Value:
    value: Any
    """원래 값 — 숫자는 숫자, 객체면 그 id(글자)."""
    label: str
    """사람이 읽는 말 — 객체면 이름(볼 수 없으면 `HIDDEN_LABEL`)."""


@dataclass(frozen=True)
class Cell:
    values: list[Value]
    more: bool
    """`CAP` 보다 많이 이어져 있다 — 실린 것이 전부가 아니다(몇 개인지는 세지 않았다)."""


def _refuse(message: str) -> AppError:
    return AppError(code("OBJECTS", 147), message, status=422)


def parse(resolver: paths.Resolver, addresses: list[str]) -> list[Column]:
    """받은 주소 → 이어진 칸들. 같은 주소는 한 번, 모르는 주소는 422(무엇이 틀렸는지)."""
    wanted = list(dict.fromkeys(one.strip() for one in addresses if one.strip()))
    if len(wanted) > MAX_COLUMNS:
        raise _refuse(
            f"이어진 칸은 한 번에 {MAX_COLUMNS}개까지입니다({len(wanted)}개) — 칸마다 쪽 "
            "크기만큼 읽습니다."
        )
    out: list[Column] = []
    for address in wanted:
        if not paths.is_path(address):
            raise _refuse(
                f"이어진 칸의 주소는 ref.<참조 칸> · out.<관계> · in.<관계> 로 시작합니다: "
                f"{address} — 이 타입 자신의 칸은 줄에 이미 있습니다."
            )
        try:
            chain = resolver.parse_chain(address)
        except AppError as caught:
            raise _refuse(caught.message) from caught
        if chain.definition is not None and chain.definition.data_type == "file":
            raise _refuse(f"{chain.label}: 파일 칸은 이어진 칸으로 싣지 않습니다.")
        if chain.edge:
            conditions.require_edge_usable(chain)
        whole = chain.field is None
        out.append(
            Column(
                address,
                chain,
                chain.label,
                "object" if whole else chain.data_type,
                chain.multi,
            )
        )
    return out


def cells(
    db: Session,
    viewer: User | None,
    columns: list[Column],
    ids: list[uuid.UUID],
    *,
    cap: int = CAP,
    hidden_label: str | None = system.HIDDEN_LABEL,
) -> dict[uuid.UUID, dict[str, Cell]]:
    """줄(`ids`)마다 칸마다 값 — 이어진 것이 없는 칸은 빠진다(빈 칸).

    `hidden_label` 이 None 이면(내보내기) 볼 수 없는 객체를 가리키는 값을 비운다 — 파일이
    화면에서 가린 것을 열지 않게(`bulk.export_rows` 와 같은 규칙)."""
    out: dict[uuid.UUID, dict[str, Cell]] = {}
    if not ids:
        return out
    for index, column in enumerate(columns):
        raw = _fetch(db, column, ids, index, cap)
        names = _names(db, viewer, column, raw, hidden_label)
        for row_id, found in raw.items():
            values: list[Value] = []
            for one in found[:cap]:
                values.extend(_values(column, one, names))
            values = [one for one in values if one.label != ""]
            more = len(found) > cap or len(values) > cap
            if not values and not more:
                continue
            values.sort(key=_order)
            out.setdefault(row_id, {})[column.address] = Cell(values[:cap], more)
    return out


def _fetch(
    db: Session, column: Column, ids: list[uuid.UUID], index: int, cap: int
) -> dict[uuid.UUID, list[Any]]:
    """줄마다 `cap + 1` 개까지 — `LATERAL` 안의 `LIMIT` 이라 이어진 것을 다 읽지 않는다."""
    chain = column.chain
    whole = chain.field is None
    row = aliased(ObjectInstance, name=f"lk{index}_row")
    walked = conditions.walk(
        chain.hops,
        chain.owners,
        row,
        f"lk{index}",
        stop_at_edge=(whole and not chain.hop.is_back) or chain.edge,
        edge=chain.edge,
    )
    if whole and chain.hop.is_back:
        expr: Any = cast(walked.target.id, String)
    elif whole:
        expr = cast(walked.edges.c.other, String)
    elif chain.edge:
        assert chain.field is not None
        expr = (
            cast(walked.edges.c[chain.field], String)
            if chain.definition is None
            else walked.edges.c.props[chain.field]
        )
    elif chain.definition is None:
        expr = {"label": walked.target.label, "key": walked.target.key}.get(
            chain.field or "", walked.target.status
        )
    else:
        expr = walked.target.properties[chain.definition.key]
    inner = walked.linked.with_only_columns(expr.label("v")).limit(cap + 1)
    lateral = inner.lateral(f"lk{index}_v")
    stmt = (
        select(row.id, lateral.c.v)
        .select_from(row)
        .join(lateral, true())
        .where(row.id.in_(ids))
    )
    found: dict[uuid.UUID, list[Any]] = {}
    for row_id, value in db.execute(stmt):
        if value is None:
            continue
        found.setdefault(row_id, []).append(value)
    return found


def _values(column: Column, raw: Any, names: dict[str, str]) -> list[Value]:
    """이어진 것 하나의 값 → 실을 값들(여러 값 칸은 원소마다)."""
    items = raw if isinstance(raw, list) else [raw]
    out: list[Value] = []
    for item in items:
        if item is None or item == "":
            continue
        if _is_object(column):
            key = str(item)
            out.append(Value(key, names.get(key, "")))
            continue
        out.append(Value(item, _label(column, item)))
    return out


def _order(one: Value) -> tuple[int, float, str]:
    """받은 값의 차례 — 숫자는 크기대로(글자로 세우면 10 이 2 앞에 선다), 나머지는 이름 순."""
    if isinstance(one.value, int | float) and not isinstance(one.value, bool):
        return (0, float(one.value), one.label)
    return (1, 0.0, one.label)


def _is_object(column: Column) -> bool:
    """끝 값이 객체를 가리키나 — 관계로 이어진 것 자체 · 참조 칸."""
    chain = column.chain
    if chain.field is None:
        return True
    return (
        not chain.edge
        and chain.definition is not None
        and chain.definition.data_type == "object_ref"
    )


def _label(column: Column, item: Any) -> str:
    if column.chain.field == "status" and column.chain.definition is None:
        return axes.STATUS_LABELS.get(str(item), str(item))
    if column.data_type == "bool":
        return axes.BOOL_LABELS.get(str(item).lower(), str(item))
    if isinstance(item, bool):
        return axes.BOOL_LABELS["true" if item else "false"]
    return str(item)


def _names(
    db: Session,
    viewer: User | None,
    column: Column,
    raw: dict[uuid.UUID, list[Any]],
    hidden_label: str | None,
) -> dict[str, str]:
    """객체를 가리키는 값의 이름 — 목록의 참조 칸과 같은 규칙(볼 수 없으면 가린다)."""
    if not _is_object(column):
        return {}
    keys: set[str] = set()
    for found in raw.values():
        for one in found:
            for item in one if isinstance(one, list) else [one]:
                if isinstance(item, str) and item:
                    keys.add(item)
    if not keys:
        return {}
    chain = column.chain
    if chain.field is None:
        owner = chain.owners[-1]
        named = owner.names(chain.hop, sorted(keys))
    else:
        definition = chain.definition
        assert definition is not None
        by_id = system.ref_labels(
            db, [definition], [{definition.key: one} for one in keys], viewer=viewer
        )
        named = {str(key): label for key, label in by_id.items()}
    if hidden_label is None:
        # 내보내기 — 가린 이름조차 싣지 않는다(빈 값은 `cells` 가 뺀다).
        return {key: label for key, label in named.items() if label != system.HIDDEN_LABEL}
    return named
