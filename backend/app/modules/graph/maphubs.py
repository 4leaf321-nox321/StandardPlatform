"""지도의 허브 칸 — 기록 타입마다 **기록을 어느 기준정보 곁에 둘까**(ADR 0046).

기록(시장 서비스 · 판매 집계 …)은 저마다 자리를 갖지 않는다. 기록 타입마다 칸 하나(「주
참조」)를 골라, 그 칸이 가리키는 기준정보(허브) 곁 원반에 놓는다.

- **자동**: 값 하나 · 기준정보(기록 아닌 타입)를 가리키는 참조 칸 중 **필수인 칸을 먼저**, 그
  안에서 **가리킬 수 있는 기준정보가 가장 많은 칸** — 허브가 많을수록 기록이 고르게 퍼진다.
  필수 칸이 없으면 필수 아닌 칸 중에서 같은 규칙(빈 기록은 미지정 자리로). 수는 계수표(ADR
  0038)로 센다(즉시). 개발 DB 의 시장 서비스는 판매 모델, 2,000만 DB 는 개발모델이 된다.
- **관리자가 바꿈**: 값 하나 · 기준정보를 가리키는 칸이면 무엇이든(필수가 아니어도 — 빈 기록은
  「미지정」 자리로 간다), 또는 「미지정」(허브 없음).
- 알맞은 칸이 없는 타입 · 주 참조가 빈 기록은 타입별 「미지정」 자리.

**설정은 다음 전체 배치에 반영된다** — 판(`MapLayout.hub_keys`)이 그때의 칸을 들고, 시간마다의
집계 · 확대는 판의 칸을 쓴다. 칸을 바꿔도 DDL 이 필요 없다 — 확대 점 표(`graph_map_points`)는
다음 전체 배치가 새 칸으로 통째로 다시 채운다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.graph.models import MapHubKey
from app.modules.objects import typecounts
from app.modules.ontology import interfaces
from app.modules.ontology.models import ObjectType, PropertyDef


def is_record(row: ObjectType) -> bool:
    return row.usage == "log" and row.kind_class != "system"


def is_master(row: ObjectType) -> bool:
    """기준정보 — 기록이 아니고 원 표를 비추지 않는(객체 표에 사는) 타입."""
    return row.usage != "log" and row.kind_class != "system"


@dataclass(frozen=True)
class Candidate:
    """허브가 될 수 있는 칸 — 값 하나 · 기준정보를 가리키는 참조 칸."""

    key: str
    label: str
    target_label: str
    target_types: tuple[str, ...]
    """가리킬 수 있는 타입 slug(대상이 인터페이스면 그 구현 타입들)."""
    required: bool
    hubs: int
    """가리킬 수 있는 기준정보 수(계수표) — 자동 규칙이 이것으로 견준다."""


@dataclass
class HubChoice:
    type: ObjectType
    key: str | None
    """쓸 칸 — 비면 미지정."""
    mode: str
    """`auto` · `admin`(관리자가 고른 칸) · `none`(관리자가 미지정으로)."""
    auto_key: str | None
    candidates: list[Candidate] = field(default_factory=list)
    stale: bool = False
    """관리자가 고른 칸이 이제 알맞지 않다(지웠거나 여러 값이 됐다) — 자동으로 돌아갔다."""


def _types(db: Session) -> list[ObjectType]:
    return list(db.scalars(select(ObjectType).where(ObjectType.is_active.is_(True))))


def master_type_ids(db: Session) -> list[uuid.UUID]:
    return sorted((row.id for row in _types(db) if is_master(row)), key=str)


def record_types(db: Session) -> list[ObjectType]:
    return sorted((row for row in _types(db) if is_record(row)), key=lambda row: row.slug)


def choices(db: Session) -> dict[uuid.UUID, HubChoice]:
    """기록 타입마다 허브 칸 — 설정 · 자동 · 후보."""
    types = _types(db)
    by_slug = {row.slug: row for row in types}
    masters = {row.slug for row in types if is_master(row)}
    records = [row for row in types if is_record(row)]
    ends = interfaces.load_ends(db)
    counted = typecounts.counts(db)
    settings = {row.type_id: row for row in db.scalars(select(MapHubKey))}
    defs: dict[uuid.UUID, list[PropertyDef]] = {}
    for definition in db.scalars(
        select(PropertyDef)
        .where(
            PropertyDef.owner_kind == "type",
            PropertyDef.data_type == "object_ref",
            PropertyDef.owner_id.in_([row.id for row in records]),
        )
        .order_by(PropertyDef.key)
    ):
        defs.setdefault(definition.owner_id, []).append(definition)

    out: dict[uuid.UUID, HubChoice] = {}
    for record in sorted(records, key=lambda row: row.slug):
        candidates: list[Candidate] = []
        for definition in defs.get(record.id, []):
            target = definition.ref_type_slug or ""
            if definition.multi or not target:
                continue
            reach = ends.expand([target]) if target in ends.interfaces else {target}
            if not reach or not all(one in masters for one in reach):
                continue
            candidates.append(
                Candidate(
                    key=definition.key,
                    label=definition.label,
                    target_label=ends.labels.get(target, target),
                    target_types=tuple(sorted(reach)),
                    required=bool(definition.required),
                    hubs=sum(counted.get(by_slug[one].id, 0) for one in reach),
                )
            )
        # 필수 칸을 먼저 — 없으면 값 하나 · 기준정보 칸 중에서(2,000만 · 200만 DB 의 시장
        # 서비스 모델 칸은 필수가 아니다. 필수만 보면 자동이 아무것도 못 골라 기록 전부가
        # 미지정이 됐다).
        auto = sorted(candidates, key=lambda one: (not one.required, -one.hubs, one.key))
        auto_key = auto[0].key if auto else None
        setting = settings.get(record.id)
        if setting is None:
            out[record.id] = HubChoice(record, auto_key, "auto", auto_key, candidates)
        elif setting.key is None:
            out[record.id] = HubChoice(record, None, "none", auto_key, candidates)
        elif any(one.key == setting.key for one in candidates):
            out[record.id] = HubChoice(record, setting.key, "admin", auto_key, candidates)
        else:
            out[record.id] = HubChoice(
                record, auto_key, "auto", auto_key, candidates, stale=True
            )
    return out


def chosen_keys(db: Session) -> dict[str, str | None]:
    """판에 적을 허브 칸 — {기록 타입 id 글자: 칸 또는 None}."""
    return {str(type_id): choice.key for type_id, choice in choices(db).items()}


def set_key(
    db: Session, record: ObjectType, *, mode: str, key: str | None, user_id: uuid.UUID
) -> None:
    """허브 칸 설정 — `auto`(줄을 지운다) · `none`(미지정) · `key`(그 칸). 고를 수 있는
    칸인지는 부르는 쪽이 `choices` 로 본다."""
    db.execute(delete(MapHubKey).where(MapHubKey.type_id == record.id))
    if mode == "auto":
        return
    db.add(
        MapHubKey(type_id=record.id, key=key if mode == "key" else None, updated_by=user_id)
    )
