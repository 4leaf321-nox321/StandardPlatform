"""플랫폼 자기소개의 자동 요약 — **무엇이 담겼나**(ADR 0019).

타입별 건수를 읽을 때마다 센다. 사람이 쓴 소개는 쓴 날에 멈추지만 이것은 늘 지금이다 —
에이전트는 이것으로 「여기에 보고서가 있나」 를 가른다. 낡음 표시는 **타입이 생기고 없어지는
것**과 정본이 바깥인지(`managed_by`)뿐이다 — 건수는 늘 변해서 표시로 쓰면 소개가 매일 낡는다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.objects import typecounts
from app.modules.ontology.models import ObjectType
from app.shared.extensions import ProfileFact, count_text

#: 한 줄에 늘어놓는 타입 수 — 그 밖은 「그 밖에 N종」.
SHOWN = 12


def _counts(db: Session) -> dict[uuid.UUID, int]:
    """타입별 객체 수 — 계수표(ADR 0038). 1분씩 붙들어 두던 것(그래도 비면 운영에서 수십 초 —
    MCP 접속이 그만큼 늦었다)을 걷었다."""
    return typecounts.counts(db)


def _listed(rows: list[tuple[ObjectType, int]]) -> str:
    head = " · ".join(f"{row.label} {count_text(count)}" for row, count in rows[:SHOWN])
    rest = len(rows) - SHOWN
    return head + (f" · 그 밖에 {rest}종" if rest > 0 else "")


def facts(db: Session) -> list[ProfileFact]:
    counts = _counts(db)
    kinds = [
        row
        for row in db.scalars(select(ObjectType).order_by(ObjectType.sort_order))
        if row.is_active and row.kind_class != "system"
    ]
    ranked = sorted(
        ((row, int(counts.get(row.id, 0))) for row in kinds), key=lambda one: -one[1]
    )
    logs = [one for one in ranked if one[0].usage == "log"]
    axes = [one for one in ranked if one[0].usage != "log"]
    marks = {f"type:{row.slug}": f"타입 「{row.label}」" for row in kinds}
    lines: list[str] = []
    if logs:
        lines.append(f"기록 — {_listed(logs)}")
    if axes:
        lines.append(f"축 — {_listed(axes)}")
    outside = [row for row in kinds if row.managed_by]
    if outside:
        lines.append(
            "정본이 바깥인 타입(여기서 못 고친다) — "
            + " · ".join(f"{row.label}({row.managed_by})" for row in outside)
        )
        marks.update(
            {
                f"managed:{row.slug}={row.managed_by}": (
                    f"「{row.label}」 의 정본이 {row.managed_by}"
                )
                for row in outside
            }
        )
    opened = [row.label for row in kinds if row.core]
    if opened:
        lines.append("코어 공개(바깥 시스템이 받아 간다) — " + " · ".join(opened))
    return [ProfileFact(key="types", label="담긴 것", lines=lines, marks=marks)]
