"""전수 집계(ADR 0079) — **묶음을 접지도 자르지도 않고 전부, 그리고 합이 맞는지까지.**

통계(`summary`)는 그림이다 — 막대 30개에서 「그 밖에」 로 접고, 쪽으로 넘겨도 한 쪽이 500 이다.
「시장 165곳의 건수 전부」 · 「모델 3천 개 각각」 처럼 **표가 답인 물음**에서 AI 는 쪽을 몇 번
넘기다 끝을 놓치거나, 원본 기록을 받아 직접 센다(2,000만 건이면 그것도 못 한다). 그리고 받은
막대를 더해도 전체와 같은지 모른다 — 참조가 빈 기록 · 이어진 것이 없는 기록이 어느 막대에도 안
들면 말없이 빠진다.

전수 집계는:

- 묶음을 **전부** 돌려준다(`INLINE_MAX` 개까지는 그 자리에서, 넘으면 작업으로 — 작업이 결과를
  파일에 쓰고 커서로 나눠 받는다).
- **검증값**을 함께 준다 — 묶음 건수의 합(`group_sum`)이 거르기를 통과한 전체(`total`)와
  같은가. 여러 값 기준(한 기록이 여러 묶음에 드는 것)이면 합이 더 클 수 있다고 따로 말한다.
- 묶음 수와 합은 창 함수로 **같은 질의 한 번**에 센다(`summary` 와 같다).

거르기 · 기준의 주소 · 셈(객체마다 한 번, ADR 0017)은 통계와 같다(`summary._grouped`)."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, func, nulls_last, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import axes
from app.modules.objects import edges as edge_rule
from app.modules.objects.axes import EMPTY_LABEL
from app.modules.objects.scope import Scope, as_scope
from app.modules.objects.services import count_of
from app.modules.objects.summary import (
    METRIC_LABELS,
    METRICS,
    ORDERS,
    _float,
    _grouped,
)
from app.modules.ontology.models import ObjectType
from app.shared.errors import AppError, code

#: 그 자리에서 돌려주는 묶음 수의 상한 — 넘으면 작업으로 돌린다. 줄 하나가 JSON 으로 150바이트
#: 남짓이라 5천이면 1MB 안쪽이다.
INLINE_MAX = 5_000
#: 작업의 결과를 커서로 받을 때 한 쪽의 기본 · 상한.
PAGE_DEFAULT = 1_000
PAGE_MAX = 5_000
#: 작업이 결과를 읽고 쓰는 덩어리.
CHUNK = 2_000


@dataclass
class Row:
    key: str | None
    label: str
    count: int
    value: float | None = None
    range: tuple[str, str] | None = None
    drill: tuple[str, str] | None = None

    def out(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "count": self.count,
            "value": self.value,
            "range": {"gte": self.range[0], "lt": self.range[1]} if self.range else None,
            "drill": {"field": self.drill[0], "op": self.drill[1]} if self.drill else None,
        }


@dataclass
class Check:
    """묶음의 합이 전체와 맞는가."""

    total: int
    """거르기를 통과한 기록 수."""
    group_sum: int
    """묶음 건수의 합."""
    multi: bool
    """여러 값 기준인가 — 한 기록이 여러 묶음에 들어 합이 전체보다 클 수 있다."""

    @property
    def difference(self) -> int:
        return self.group_sum - self.total

    @property
    def matches(self) -> bool:
        return self.group_sum == self.total

    def message(self, groups: int) -> str:
        head = f"묶음 {groups:,}개의 건수 합 {self.group_sum:,} · 전체 {self.total:,}"
        if self.matches:
            return f"{head} — 같습니다. 빠지거나 겹친 기록이 없습니다."
        if self.multi:
            return (
                f"{head} — 합이 {self.difference:,} 큽니다. 여러 값 기준이라 한 기록이 여러 "
                "묶음에 듭니다(그만큼 겹쳐 셌다). 기록 수는 `total` 입니다."
            )
        if self.difference < 0:
            return (
                f"{head} — 합이 {-self.difference:,} 작습니다. 어느 묶음에도 들지 않은 기록이 "
                "있습니다(기준이 이어진 것 너머이면 이어진 것이 없는 기록일 수 있다). 기록 "
                "수는 "
                "`total` 입니다."
            )
        return (
            f"{head} — 합이 {self.difference:,} 큽니다. 세는 사이에 기록이 들어왔을 수 "
            "있습니다 "
            "— 다시 부릅니다."
        )

    def out(self, groups: int) -> dict[str, Any]:
        return {
            "total": self.total,
            "group_sum": self.group_sum,
            "difference": self.difference,
            "matches": self.matches,
            "multi": self.multi,
            "message": self.message(groups),
        }


@dataclass
class Census:
    group_field: str
    group_label: str
    metric: str
    metric_field: str | None
    metric_label: str
    order: str
    grain: str | None
    total: int
    groups: int
    check: Check
    rows: list[Row] = field(default_factory=list)
    complete: bool = True
    """`rows` 가 묶음 전부인가 — 아니면(`INLINE_MAX` 를 넘음) 작업이 전부를 만든다."""


class Plan:
    """전수 집계 한 번의 질의 — 기준 · 셈 · 차례를 풀어 둔 것. 그 자리에서도 작업에서도
    같다."""

    def __init__(
        self,
        db: Session,
        target: ObjectType | Scope,
        filtered: Select[Any],
        *,
        group_by: str,
        metric: str = "count",
        metric_field: str | None = None,
        order: str = "desc",
        grain: str | None = None,
        viewer: User | None = None,
        at: edge_rule.AtArg = "today",
    ) -> None:
        if metric not in METRICS:
            raise AppError(
                code("OBJECTS", 46),
                f"집계 방식은 {', '.join(METRICS)} 중 하나여야 합니다: {metric}",
                status=422,
            )
        if order not in ORDERS:
            raise AppError(
                code("OBJECTS", 52),
                f"차례는 {', '.join(ORDERS)} 중 하나여야 합니다: {order}",
                status=422,
            )
        self.db = db
        scope = as_scope(db, target)
        plan = axes.JoinPlan(db, scope, prefix="g", viewer=viewer, at=at)
        self.group = plan.axis(group_by, grain=grain)
        self.group_by = group_by
        self.metric = metric
        self.metric_field = metric_field
        self.order = order
        value_expr = axes.metric_expr(scope.defs, metric, metric_field, METRIC_LABELS)
        self.counting = value_expr is None
        self.filtered = filtered
        every = _grouped(filtered, [self.group], metric, value_expr).order_by(None).subquery()
        self.whole = select(
            every,
            func.count().over().label("groups"),
            func.sum(every.c.n).over().label("group_sum"),
        ).subquery()
        measured = self.whole.c.n if self.counting else self.whole.c.v
        if order == "key":
            ordering = self.whole.c.k.asc()
        else:
            ordering = measured.asc() if order == "asc" else measured.desc()
        self.ordered = select(self.whole).order_by(nulls_last(ordering), self.whole.c.k)

    def rows_of(self, raw: list[Any]) -> list[Row]:
        keys = [str(one.k) for one in raw if one.k is not None]
        names = axes.labels(self.db, self.group, keys)
        grain = self.group.grain
        tree = self.group.tree
        return [
            Row(
                key=None if one.k is None else str(one.k),
                label=(
                    EMPTY_LABEL
                    if one.k is None or str(one.k) == ""
                    else names.get(str(one.k), str(one.k))
                ),
                count=int(one.n),
                value=None if self.counting else _float(one.v),
                range=(
                    axes.period_range(str(one.k), grain)
                    if grain is not None and one.k is not None
                    else None
                ),
                drill=(
                    (tree.field, tree.drill_op(str(one.k)))
                    if tree is not None and one.k is not None
                    else None
                ),
            )
            for one in raw
        ]

    def head(self, total: int, groups: int, group_sum: int) -> Census:
        return Census(
            group_field=self.group_by,
            group_label=self.group.label,
            metric=self.metric,
            metric_field=self.metric_field,
            metric_label=METRIC_LABELS[self.metric],
            order=self.order,
            grain=self.group.grain,
            total=total,
            groups=groups,
            check=Check(total=total, group_sum=group_sum, multi=self.group.multi),
        )

    def inline(self) -> Census:
        """그 자리에서 — 묶음이 `INLINE_MAX` 개 이하면 전부, 넘으면 수 · 검증값만(`complete`
        거짓 — 부르는 쪽이 작업을 넣는다)."""
        total = count_of(self.db, self.filtered)
        raw = list(self.db.execute(self.ordered.limit(INLINE_MAX + 1)))
        if not raw:
            return self.head(total, 0, 0)
        found = self.head(total, int(raw[0].groups), int(raw[0].group_sum or 0))
        if len(raw) > INLINE_MAX:
            found.complete = False
            return found
        found.rows = self.rows_of(raw)
        return found

    def stream(self) -> Iterator[list[Row]]:
        """전부 — 덩어리마다(작업). 첫 덩어리 앞에 `stats` 가 찬다."""
        self.stats: tuple[int, int] = (0, 0)
        result = self.db.execute(self.ordered.execution_options(yield_per=CHUNK))
        for part in result.partitions():
            raw = list(part)
            if raw and self.stats == (0, 0):
                self.stats = (int(raw[0].groups), int(raw[0].group_sum or 0))
            yield self.rows_of(raw)


# --- 작업 결과를 커서로 -------------------------------------------------------------


def encode_cursor(job_id: uuid.UUID, at: int) -> str:
    raw = json.dumps({"j": str(job_id), "n": at}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(raw: str) -> tuple[uuid.UUID, int]:
    try:
        padded = raw.strip() + "=" * (-len(raw.strip()) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return uuid.UUID(str(data["j"])), max(0, int(data["n"]))
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as caught:
        raise AppError(
            code("OBJECTS", 157),
            "전수 집계의 커서를 읽을 수 없습니다 — 받은 `next_cursor` 를 그대로 넘기세요.",
            status=422,
        ) from caught


def page(
    db: Session, file_id: uuid.UUID, at: int, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """작업의 결과 파일(줄마다 JSON 하나)에서 `at` 번째부터 `limit` 줄 — (줄들, 더 있나).
    파일을 흘려 읽는다(앞 줄은 건너뛰기만)."""
    from app.modules.jobs import files
    from app.modules.jobs.models import JobFile

    row = db.get(JobFile, file_id)
    if row is None:
        raise AppError(
            code("OBJECTS", 159),
            "전수 집계의 결과 파일이 없습니다 — 보관 기간이 지나 지워졌을 수 있습니다. 다시 "
            "세어 주세요.",
            status=410,
        )
    out: list[dict[str, Any]] = []
    more = False
    with files.open_file(row) as handle:
        for number, line in enumerate(handle):
            if number < at:
                continue
            if len(out) >= limit:
                more = True
                break
            text = line.strip()
            if text:
                out.append(json.loads(text))
    return out, more
