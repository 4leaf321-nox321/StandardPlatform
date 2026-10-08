"""종류 변경 — 속성 종류를 바꾸면서 **저장된 값도 새 종류로** 변환한다(ADR 0007).

규칙(값 하나를 어떻게 읽나)은 `conversion.py` 가 쥐고, 여기는 그 규칙을 **타입의 객체들에**
건다: 무엇이 바뀌고 무엇이 안 되는지 세고(`plan`), 안 되는 것이 없으면 값 · 정의를 함께
고친다(`apply`). 화면 · 정의 가져오기 · 허브 묶음 · 스냅샷 복원이 **이 두 함수를** 부른다 —
두 벌이면 한쪽만 고쳐진다.

    Target    한 타입의 한 속성 — 바뀌기 전 종류와 바뀐 뒤의 정의(저장하지 않은 사본)
    plan      건수 · 변환할 수 없는 값(값마다 건수 · 견본) · 경고 · 오류 — 아무것도 안 바꾼다
    apply     계획을 다시 세워 오류가 없으면 값 · 객체 이력 · 정의 · 롤업 · 판(updated_at) ·
              정의 이력

**변환할 수 없는 값이 하나라도 있으면 적용하지 않는다** — 대체 값(`mapping`)으로 값마다 정해야
한다. 예외는 허브가 관리하는 타입(`clear_failures`): 그 묶음으로 바뀔 때만 비우고 경고한다 —
쌍둥이는 그 객체를 못 고치고, 허브가 변환한 값은 같은 묶음의 객체 단계가 다시 보낸다.

**글 ↔ 참조**(ADR 0009) — 이미 넣은 기록을 축에 잇는 길이다. 글 → 참조는 값마다 일괄 입력과
같은 이름 풀이로 바꾸고(못 푼 값은 대체 값), 참조 → 글은 상대의 식별자(없으면 이름)로 읽는다.
객체마다의 이력에는 **바뀐 칸만** 남긴다 — 기록 200만 건의 속성 전체를 두 번 담으면 이력 표가
기록보다 커진다.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.objects import bulk, rewrite
from app.modules.objects.models import ObjectInstance
from app.modules.ontology import conversion, interfaces, views
from app.modules.ontology.models import ObjectType, PropertyDef
from app.shared import audit, system_sources
from app.shared.batches import chunks

#: 화면과 같은 이름 — 문구가 화면의 종류 이름과 같아야 사람이 같은 것으로 읽는다.
KIND_LABELS = {
    "text": "글 (한 줄)",
    "text_long": "글 (여러 줄)",
    "number": "숫자",
    "date": "날짜",
    "datetime": "날짜와 시각",
    "bool": "예/아니오",
    "enum": "선택",
    "url": "주소",
    "object_ref": "객체 참조",
    "file": "파일",
}

#: 「유일」 을 둘 수 있는 종류 — 화면의 속성 창과 같다.
UNIQUEABLE = ("text", "number", "url", "date", "datetime")

#: 변환할 수 없는 값은 이만큼까지(건수가 많은 것부터) — 더 있으면 수만 말한다.
FAILURE_CAP = 300
#: 계획이 값마다 세는 변환할 수 없는 값의 종류 — 넘으면 건수만 센다. 엉뚱한 열을 참조로 바꾸면
#: 200만 건이 전부 실패하고, 값마다 견본을 들면 워커의 메모리가 먼저 바닥난다.
FAILURE_TRACK = 10_000
#: 값마다 견본 객체 수.
SAMPLES = 3
#: 계획이 한 번에 읽는 행 수 — 칸 하나만 읽으니 넉넉히. 200만 건을 한 목록으로 싣지 않는다.
READ_CHUNK = 5_000
#: 값이 있는 객체가 이보다 많으면 요청 안에서 하지 않고 **작업**으로 돈다 — 기록 200만 건의
#: 변환은 분 단위라 클라이언트가 먼저 끊는다(ADR 0009).
RETYPE_INLINE = 20_000

Progress = Callable[[str, int, int], None]
"""(단계, 처리한 수, 전체) — 작업이 진행률을 적고, 취소 요청이면 여기서 멈춘다."""
#: 정의 이력에 남기는 대체 값 수.
MAPPED_IN_AUDIT = 50

_LOSSY = {
    "time": "시각을 버리고 날짜만 남기는 값",
    "subsecond": "초 아래를 버리는 값",
    "precision": "화면에서 끝자리가 깎일 수 있는 큰 정수",
    "duplicate": "변환으로 겹쳐 하나로 친 원소",
}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class RefLinker:
    """종류 변경의 이름 풀이(`conversion.Linker`).

    글 → 참조는 일괄 입력과 **같은 판정**(`bulk.Refs.classify`) — 넣을 때 풀리는 글자가
    여기서도 풀린다. 참조 → 글은 상대의 식별자(없으면 이름) — 그 글자를 다시 참조로 바꾸면
    같은 객체로 풀린다. 보이는 범위를 안 건다: 종류 변경은 시스템 관리자 · 허브의 일이다.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.refs = bulk.Refs(db, None)
        self.names: dict[str, str | None] = {}
        self._system: dict[str, str] | None = None

    def resolve(self, target: str, text: str) -> str:
        match = self.refs.classify(target, text)
        if match.kind == "one" and match.id is not None:
            return match.id
        if match.kind == "many" and match.how == "key":
            raise conversion.Unconvertible(
                f"식별자 「{text.strip()}」 이 구현 타입 여럿에 있습니다 — id 로 적으세요"
            )
        if match.kind == "many":
            raise conversion.Unconvertible(
                f"이름 「{text.strip()}」 이 {match.count}개에 맞습니다 — 식별자로 적으세요"
            )
        raise conversion.Unconvertible(
            f"{target} 에서 「{text.strip()}」 을(를) 찾지 못했습니다"
        )

    def prefetch(self, ids: Iterable[str]) -> None:
        """id 들의 글자를 한 번에 — 행마다 묻지 않게. 원 표(부서 · 계정)의 id 도 찾는다."""
        wanted = sorted({one for one in ids if one not in self.names and _UUID.match(one)})
        for batch in chunks(wanted):
            for found, key, label in self.db.execute(
                select(ObjectInstance.id, ObjectInstance.key, ObjectInstance.label).where(
                    ObjectInstance.id.in_([uuid.UUID(one) for one in batch])
                )
            ):
                self.names[str(found)] = key or label
        missing = [one for one in wanted if one not in self.names]
        if missing:
            if self._system is None:
                self._system = {
                    str(ref.id): ref.key or ref.label
                    for source in system_sources.system_sources()
                    for ref in source.list_all(self.db)
                }
            for one in missing:
                self.names[one] = self._system.get(one)

    def ready(self, target: Target, values: list[Any]) -> None:
        """덩어리 하나의 값을 미리 물어 둔다 — 참조였으면 상대의 글자를, 참조가 되면 이름
        풀이를(큰 대상은 통째로 안 읽으므로 덩어리째 묻는다, `bulk.Refs.prefetch`)."""
        if target.before == "object_ref":
            self.prefetch(_elements(values))
        if target.after.data_type == "object_ref":
            self.refs.prefetch(target.after.ref_type_slug or "", _elements(values))

    def text_of(self, object_id: str) -> str:
        if object_id not in self.names:
            self.prefetch([object_id])
        found = self.names.get(object_id)
        if not found:
            raise conversion.Unconvertible("가리키던 객체가 없습니다(지웠거나 없는 id)")
        return found


def _elements(values: Iterable[Any]) -> Iterator[str]:
    for value in values:
        for one in value if isinstance(value, list) else [value]:
            if one is not None:
                yield str(one)


def _touches_refs(targets: Iterable[Target]) -> bool:
    return any(
        target.before == "object_ref" or target.after.data_type == "object_ref"
        for target in targets
    )


@dataclass
class Target:
    """한 타입의 한 속성 — **바뀌기 전 종류**와 **바뀐 뒤의 정의**(저장하지 않은 사본).

    가져오기는 계획을 세운 뒤 정의 행을 먼저 고치고 값을 바꾼다 — 그때 행은 이미 새 종류라,
    바뀌기 전 종류를 여기 들고 있어야 한다.
    """

    type_id: uuid.UUID
    type_slug: str
    type_label: str
    key_scope: str
    key: str
    label: str
    before: str
    after: PropertyDef
    via: str = ""
    """인터페이스를 따라 바뀌면 그 인터페이스."""
    clear_failures: bool = False
    """허브가 관리하는 타입이 그 허브의 묶음으로 바뀔 때 — 변환할 수 없는 값을 비운다."""

    @property
    def name(self) -> str:
        return f"{self.type_slug}.{self.key}"


@dataclass
class Sample:
    type_slug: str
    object_id: uuid.UUID | None
    label: str


@dataclass
class ValueRow:
    value: str
    count: int
    reason: str = ""
    to: str | None = None
    """대체 값 — `None` 이면 값 삭제(대체 값 목록에서만 뜻이 있다)."""
    samples: list[Sample] = field(default_factory=list)


@dataclass
class TypeCount:
    type_slug: str
    type_label: str
    key: str
    via: str
    with_value: int = 0
    converted: int = 0
    unchanged: int = 0
    cleared: int = 0


@dataclass
class RetypePlan:
    counts: list[TypeCount] = field(default_factory=list)
    failures: list[ValueRow] = field(default_factory=list)
    failures_total: int = 0
    """변환할 수 없는 값의 **종류 수**(목록은 `FAILURE_CAP` 까지)."""
    mapped: list[ValueRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    defaults: dict[tuple[uuid.UUID, str], Any] = field(default_factory=dict)
    """(타입, 키) → 바뀐 기본값. 적용이 정의에 적는다."""


# --- 대상 -----------------------------------------------------------------------


def unsupported(before: str, after: str) -> str | None:
    """종류 변경이 안 되는 쌍이면 그 까닭 — 되면 None."""
    if before == after:
        return f"이미 {kind_label(after)} 입니다."
    if "file" in (before, after):
        return "파일 속성은 값이 첨부라 종류를 바꾸지 않습니다."
    linkable = " · ".join(kind_label(one) for one in conversion.LINKABLE)
    if after == "object_ref":
        # 있는 축에 잇는다(ADR 0009) — 선택을 **새** 코드표로 만드는 것은 코드표 승격이다.
        if before not in conversion.LINKABLE:
            return f"객체 참조로는 {linkable} 에서만 바꿉니다."
        return None
    if before == "object_ref":
        if after not in conversion.LINKABLE:
            return (
                f"객체 참조는 {linkable} (으)로만 바꿉니다 — 상대의 식별자(없으면 이름)가 "
                "됩니다."
            )
        return None
    if after not in conversion.SUPPORTED or before not in conversion.SUPPORTED:
        names = " · ".join(kind_label(one) for one in conversion.SUPPORTED)
        return f"종류 변경은 {names} 사이에서만 합니다."
    return None


def target_def(found: PropertyDef, wanted: Mapping[str, Any]) -> tuple[PropertyDef, list[str]]:
    """바뀐 뒤의 정의 — 지금 정의에 **보낸 칸만** 덮고 모양을 정규화한 사본(저장하지 않는다).

    다른 종류에서 남은 칸(패턴 · 최소 · 고를 값)은 지운다 — 남기면 되돌아올 때 조용히 살아난다.
    """
    shape = interfaces.shape_of(wanted, interfaces.shape_of(found))
    notes: list[str] = []
    unique = bool(wanted.get("unique", found.unique))
    if unique and (shape.data_type not in UNIQUEABLE or shape.multi):
        notes.append(
            f"속성 {found.key}: {kind_label(shape.data_type)} 은(는) 유일을 두지 않아 "
            "유일을 끕니다."
        )
        unique = False
    after = PropertyDef(
        owner_kind=found.owner_kind,
        owner_id=found.owner_id,
        key=found.key,
        label=str(wanted.get("label") or found.label),
        unique=unique,
        default_value=wanted.get("default_value", found.default_value),
        inverse_label=(
            str(wanted.get("inverse_label") or found.inverse_label or "")
            if shape.data_type == "object_ref"
            else ""
        ),
        **shape.written(),
    )
    return after, notes


def target_for(
    owner: ObjectType,
    found: PropertyDef,
    wanted: Mapping[str, Any],
    *,
    via: str = "",
    clear_failures: bool = False,
) -> tuple[Target, list[str]]:
    after, notes = target_def(found, wanted)
    return (
        Target(
            type_id=owner.id,
            type_slug=owner.slug,
            type_label=owner.label,
            key_scope=owner.key_scope,
            key=found.key,
            label=after.label,
            before=found.data_type,
            after=after,
            via=via,
            clear_failures=clear_failures,
        ),
        notes,
    )


def targets_from_bindings(
    db: Session,
    bindings: Iterable[interfaces.Binding],
    *,
    skip: set[str] | None = None,
    source: str = "",
) -> tuple[list[Target], list[str]]:
    """인터페이스를 따라 **종류가 바뀌는** 구현 타입의 속성 — 파일에 없던 타입도 여기서
    걸린다."""
    out: list[Target] = []
    notes: list[str] = []
    for one in bindings:
        if one.action != "sync" or "data_type" not in one.changed or one.shape is None:
            continue
        name = f"{one.type_slug}.{one.key}"
        if skip and name in skip:
            continue
        owner = db.scalar(select(ObjectType).where(ObjectType.slug == one.type_slug))
        if owner is None:
            continue
        found = db.scalar(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type",
                PropertyDef.owner_id == owner.id,
                PropertyDef.key == one.key,
            )
        )
        if found is None:
            continue
        target, more = target_for(
            owner,
            found,
            one.shape.written(),
            via=one.interface,
            clear_failures=bool(source) and owner.managed_by == source,
        )
        out.append(target)
        notes.extend(more)
    return out, notes


# --- 계획 -----------------------------------------------------------------------


def _with_value(target: Target) -> Any:
    return select(ObjectInstance.id).where(
        ObjectInstance.type_id == target.type_id,
        ObjectInstance.deleted_at.is_(None),
        ObjectInstance.properties.has_key(target.key),
    )


def count_rows(db: Session, targets: Iterable[Target], *, cap: int | None = None) -> int:
    """값이 있는 객체 수. `cap` 을 주면 거기서 멈추고 센다(넘었는지만 알면 될 때)."""
    total = 0
    for target in targets:
        stmt = _with_value(target)
        if cap is not None:
            stmt = stmt.limit(cap + 1 - total)
        total += int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        if cap is not None and total > cap:
            break
    return total


def fingerprint(planned: RetypePlan) -> str:
    """계획의 지문 — 미리 본 계획과 적용할 때의 계획이 같은가. 그 사이 누가 값을 바꿨으면
    다르다(작업의 적용이 본다)."""
    return fingerprint_of(
        [
            (one.type_slug, one.key, one.with_value, one.converted, one.unchanged, one.cleared)
            for one in planned.counts
        ],
        [(one.value, one.count) for one in planned.failures],
        planned.failures_total,
        [(one.value, one.count, one.to) for one in planned.mapped],
    )


def fingerprint_of(
    counts: Iterable[tuple[Any, ...]],
    failures: Iterable[tuple[Any, ...]],
    failures_total: int,
    mapped: Iterable[tuple[Any, ...]],
) -> str:
    """지문의 몸 — 계획(`RetypePlan`)에서도 응답(`RetypeOut`)에서도 같은 값을 낸다."""
    shape = {
        "counts": [list(one) for one in counts],
        "failures": [list(one) for one in failures],
        "failures_total": failures_total,
        "mapped": [list(one) for one in mapped],
    }
    raw = json.dumps(shape, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _rows(
    db: Session, target: Target
) -> Iterator[list[tuple[uuid.UUID, str, uuid.UUID | None, Any]]]:
    """그 키에 값이 있는 살아 있는 객체를 **덩어리로** — 계획은 그 칸만 읽고(행 전체를 안
    싣는다), 200만 건을 한 목록으로 들지 않는다."""
    last: uuid.UUID | None = None
    while True:
        stmt = (
            select(
                ObjectInstance.id,
                ObjectInstance.label,
                ObjectInstance.owner_workspace_id,
                ObjectInstance.properties[target.key],
            )
            .where(
                ObjectInstance.type_id == target.type_id,
                ObjectInstance.deleted_at.is_(None),
                ObjectInstance.properties.has_key(target.key),
            )
            .order_by(ObjectInstance.id)
            .limit(READ_CHUNK)
        )
        if last is not None:
            stmt = stmt.where(ObjectInstance.id > last)
        rows = [(row[0], row[1], row[2], row[3]) for row in db.execute(stmt)]
        if not rows:
            return
        yield rows
        last = rows[-1][0]


def _convert(
    target: Target,
    raw: Any,
    mapping: Mapping[str, str | None] | None,
    linker: RefLinker | None,
) -> conversion.Converted:
    """값 하나를 새 정의로 — 참조였으면 상대의 식별자로 읽고, 참조가 되면 이름을 푼다."""
    return conversion.convert_stored(
        target.after, raw, mapping, linker=linker, from_ref=target.before == "object_ref"
    )


def _converted(
    target: Target,
    raw: Any,
    mapping: Mapping[str, str | None] | None,
    linker: RefLinker | None = None,
) -> conversion.Converted:
    """적용할 값 — 허브가 관리하는 타입이면 변환할 수 없는 원소를 지운다(경고는 계획이
    낸다)."""
    out = _convert(target, raw, mapping, linker)
    if out.failures and target.clear_failures:
        dropped = {**(mapping or {}), **{key: None for key, _ in out.failures}}
        out = _convert(target, raw, dropped, linker)
    return out


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def plan(
    db: Session,
    targets: list[Target],
    mapping: Mapping[str, str | None] | None = None,
    *,
    linker: RefLinker | None = None,
    on_progress: Progress | None = None,
) -> RetypePlan:
    """무엇이 변환되고 무엇이 안 되는지 — **아무것도 안 바꾼다.**"""
    out = RetypePlan()
    failures: dict[str, ValueRow] = {}
    mapped: dict[str, ValueRow] = {}
    used: set[str] = set()
    if linker is None and _touches_refs(targets):
        linker = RefLinker(db)
    total = count_rows(db, targets) if on_progress is not None else 0
    seen = 0

    for target in targets:
        counts = TypeCount(target.type_slug, target.type_label, target.key, target.via)
        lossy: dict[str, int] = {}
        mine: dict[str, ValueRow] = {}
        before_groups: dict[tuple[Any, str], list[str]] = {}
        after_groups: dict[tuple[Any, str], list[str]] = {}
        untracked = 0  # `FAILURE_TRACK` 종을 넘어 값마다 세지 않은 실패 건수
        name = f"속성 {target.name}"
        if target.after.data_type == "object_ref" and not target.after.ref_type_slug:
            # 「아무 타입이나」 로는 이름을 풀 곳이 없다 — 어디서 찾을지 사람이 정한다.
            out.errors.append(f"{name}: 객체 참조로 바꾸려면 가리킬 타입을 정해야 합니다.")
            out.counts.append(counts)
            continue
        for row_chunk in _rows(db, target):
            if on_progress is not None:
                on_progress("계획", seen, total)
                seen += len(row_chunk)
            if linker is not None:
                linker.ready(target, [raw for *_, raw in row_chunk])
            for object_id, label, workspace_id, raw in row_chunk:
                counts.with_value += 1
                result = _convert(target, raw, mapping, linker)
                for kind in result.lossy:
                    lossy[kind] = lossy.get(kind, 0) + 1
                for key in result.mapped:
                    used.add(key)
                    row = mapped.setdefault(key, ValueRow(key, 0, to=(mapping or {}).get(key)))
                    row.count += 1
                if result.failures:
                    for key, reason in result.failures:
                        if key not in mine and len(mine) >= FAILURE_TRACK:
                            untracked += 1
                            continue
                        row = mine.setdefault(key, ValueRow(key, 0, reason))
                        row.count += 1
                        if len(row.samples) < SAMPLES:
                            row.samples.append(Sample(target.type_slug, object_id, label))
                    if target.clear_failures:
                        counts.cleared += 1
                    continue
                if result.remove:
                    counts.cleared += 1
                elif result.changed:
                    counts.converted += 1
                else:
                    counts.unchanged += 1
                if target.after.unique and not target.after.multi and not result.remove:
                    scope = workspace_id if target.key_scope == "workspace" else None
                    before_groups.setdefault((scope, _json(raw)), []).append(label)
                    after_groups.setdefault((scope, _json(result.value)), []).append(label)

        # 기본값도 같은 길 — 안 되면 견본 「(기본값)」 으로 같은 표에 선다.
        default = target.after.default_value
        if default is not None:
            result = _convert(target, default, mapping, linker)
            if result.failures:
                for key, reason in result.failures:
                    row = mine.setdefault(key, ValueRow(key, 0, reason))
                    row.count += 1
                    row.samples.append(Sample(target.type_slug, None, "(기본값)"))
            else:
                for key in result.mapped:
                    used.add(key)
                out.defaults[(target.type_id, target.key)] = (
                    None if result.remove else result.value
                )

        if mine:
            listed = ", ".join(
                f"「{row.value}」({row.count})"
                for row in sorted(mine.values(), key=lambda one: -one.count)[:5]
            )
            total = sum(row.count for row in mine.values()) + untracked
            kinds = f"{len(mine):,}종 넘게" if untracked else f"{len(mine)}종"
            if target.clear_failures:
                out.warnings.append(
                    f"{name}: 허브가 관리하는 타입이라 "
                    f"{kind_label(target.after.data_type)}(으)로 변환할 수 없는 값 "
                    f"{total}건을 비웁니다 — {listed}. 허브의 값이 같은 묶음의 객체로 다시 "
                    "옵니다."
                )
            else:
                out.errors.append(
                    f"{name}: {kind_label(target.before)} → "
                    f"{kind_label(target.after.data_type)}(으)로 변환할 수 없는 값이 "
                    f"{kinds} {total}건 있습니다 — {listed}."
                )
            for key, row in mine.items():
                shared = failures.setdefault(key, ValueRow(key, 0, row.reason))
                shared.count += row.count
                for sample in row.samples:
                    if len(shared.samples) < SAMPLES:
                        shared.samples.append(sample)

        for kind, count in sorted(lossy.items()):
            out.warnings.append(f"{name}: {_LOSSY.get(kind, kind)} {count}개.")
        if counts.cleared and target.after.required and not target.clear_failures:
            out.warnings.append(
                f"{name}: 필수 속성인데 값을 비우는 객체가 {counts.cleared}개입니다 — "
                "그대로 두지만, 그 객체의 속성을 수정할 때 걸립니다."
            )
        if after_groups:
            fresh = [
                (value, labels)
                for (scope, value), labels in after_groups.items()
                if len(labels) > 1
                and not any(
                    len(old) > 1 and set(labels) <= set(old) for old in before_groups.values()
                )
            ]
            for value, labels in fresh[:5]:
                out.errors.append(
                    f"{name}: 변환하면 유일해야 하는 값 {value} 이(가) 겹칩니다 — "
                    f"{', '.join(labels[:5])}. 대체 값으로 갈라 주세요."
                )
            if any(len(labels) > 1 for labels in before_groups.values()):
                out.warnings.append(f"{name}: 이미 겹쳐 있던 유일 값은 그대로 둡니다.")
        if target.before == "number" and target.after.data_type != "number":
            owner = db.get(ObjectType, target.type_id)
            rollups = (owner.list_view or {}).get("rollups") if owner is not None else None
            if any(
                isinstance(one, dict) and one.get("property") == target.key
                for one in rollups or []
            ):
                out.warnings.append(
                    f"{name}: 목록의 롤업이 이 속성을 모읍니다 — 숫자가 아니게 되므로 "
                    "걷어냅니다."
                )
        out.counts.append(counts)

    ordered = sorted(failures.values(), key=lambda one: (-one.count, one.value))
    out.failures_total = len(ordered)
    out.failures = ordered[:FAILURE_CAP]
    out.mapped = sorted(mapped.values(), key=lambda one: (-one.count, one.value))
    unused = sorted(set(mapping or {}) - used - set(failures))
    if unused:
        shown = ", ".join(f"「{one}」" for one in unused[:10])
        out.warnings.append(f"쓰이지 않은 대체 값 {len(unused)}개 — {shown}.")
    return out


# --- 적용 -----------------------------------------------------------------------


def apply(
    db: Session,
    user: User | None,
    targets: list[Target],
    mapping: Mapping[str, str | None] | None = None,
    *,
    reason: str = "",
    before_apply: Callable[[RetypePlan], None] | None = None,
    on_progress: Progress | None = None,
) -> RetypePlan:
    """계획을 다시 세워 오류가 없으면 **값과 정의를 함께** 고친다. 커밋하지 않는다.

    객체마다 이력을 남긴다(`object.update`) — 이력 화면이 그것으로 그때 값을 되짚는다. **바뀐
    칸만** 담는다(`keys`): 속성 전체를 두 번 담으면 기록 200만 건의 종류 변경이 이력 표를
    기록보다 크게 만든다. 묶음 일지에는 안 적는다: 묶음 되돌리기는 정의를 안 되돌리므로, 옛
    종류의 값을 새 정의에 다시 넣게 된다.
    """
    linker = RefLinker(db) if _touches_refs(targets) else None
    planned = plan(db, targets, mapping, linker=linker, on_progress=on_progress)
    if planned.errors:
        return planned
    if before_apply is not None:
        # 쓰기 **전에** — 미리 본 계획과 다르면 여기서 멈춘다(작업의 지문).
        before_apply(planned)
    total = sum(one.with_value for one in planned.counts)
    seen = 0
    now = datetime.now(UTC)
    for target in targets:
        failed = _Failed()
        # **정의 행과 타입을 먼저 잠근다** — 긴 변환 중에 다른 관리자가 같은 속성 · 타입을
        # 고치면 끝에서 덮인다. 타입은 `NO KEY UPDATE` 다: 객체 만들기의 FK 검사(KEY SHARE)는
        # 막지 않는다 — 막으면 200만 건 변환 내내 그 타입에 객체를 못 넣는다.
        definition = db.scalar(
            select(PropertyDef)
            .where(
                PropertyDef.owner_kind == "type",
                PropertyDef.owner_id == target.type_id,
                PropertyDef.key == target.key,
            )
            .with_for_update()
        )
        owner = db.scalar(
            select(ObjectType)
            .where(ObjectType.id == target.type_id)
            .with_for_update(key_share=True)  # PostgreSQL 에서 `FOR NO KEY UPDATE`
        )
        if owner is None or definition is None:  # pragma: no cover - 계획과 같은 트랜잭션이다
            continue
        why = (
            f"속성 종류 변경: 「{target.label}」 {kind_label(target.before)} → "
            f"{kind_label(target.after.data_type)}"
        )
        if target.via:
            why += f" — 인터페이스 {target.via}"
        if reason:
            why += f" — {reason}"
        counts = [0, 0]
        for scope in ("all", "late"):
            # 첫 바퀴는 값이 있는 행 전부, 둘째 바퀴는 **커서가 지나간 뒤 바뀌거나 생긴 행** —
            # uuid 순으로 덩어리를 잠그므로, 이미 지나간 자리에 새로 생긴 행 · 그때 그 칸이
            # 비어 있다가 채워진 행은 첫 바퀴가 못 본다. 정의를 바꾸기 직전에 한 번 더 훑지
            # 않으면 그 값은 옛 종류로 남았다(2026-10-08).
            last: uuid.UUID | None = None
            while True:
                rows = _locked_chunk(db, target, last, late=scope == "late")
                if not rows:
                    break
                if on_progress is not None and scope == "all":
                    on_progress("적용", seen, total)
                    seen += len(rows)
                if linker is not None:
                    linker.ready(target, [old for *_, old in rows])
                done = _rewrite_rows(
                    db, user, target, owner.slug, rows, mapping, linker, why, failed
                )
                counts[0] += done[0]
                counts[1] += done[1]
                last = rows[-1][0]
        converted, cleared = counts
        if failed.values:
            # 계획 뒤에 **바뀐 값이 변환되지 않는다** — 예전에는 조용히 건너뛰어 그 값이 옛
            # 종류로 남았다. 하나라도 있으면 전부 되돌린다(부르는 쪽이 롤백한다).
            listed = ", ".join(
                f"「{value}」({count})"
                for value, count in sorted(failed.values.items(), key=lambda one: -one[1])[:5]
            )
            planned.errors.append(
                f"{target.name}: 계획을 세운 뒤 바뀐 값 가운데 "
                f"{kind_label(target.after.data_type)}(으)로 변환할 수 없는 것이 "
                f"{sum(failed.values.values())}건 있습니다 — {listed}. 다시 계획을 보세요."
            )
            return planned
        for name, value in interfaces.shape_of(target.after).written().items():
            setattr(definition, name, value)
        definition.unique = bool(target.after.unique)
        definition.inverse_label = target.after.inverse_label or ""
        definition.default_value = planned.defaults.get((target.type_id, target.key))
        if target.before == "number" and target.after.data_type != "number":
            owner.list_view = views.prune_rollups(owner.list_view or {}, target.key)
        # 값이 하나도 없어도 정의가 바뀌었다 — RDF 판이 타입의 시각으로 안다.
        owner.updated_at = now
        audit.record(
            db,
            action="ontology.property.retype",
            actor=user,
            target_table="property_defs",
            target_id=definition.id,
            target_label=target.name,
            changes={
                "data_type": {"before": target.before, "after": target.after.data_type},
                "converted": converted,
                "cleared": cleared,
                "mapped": dict(list((mapping or {}).items())[:MAPPED_IN_AUDIT]),
                **({"via": target.via} if target.via else {}),
            },
            reason=reason or None,
        )
    db.flush()
    return planned


#: 둘째 바퀴가 보는 시각의 폭 — 이 트랜잭션이 시작하기 이만큼 전에 시작한 쓰기도 커서가 지나간
#: 뒤에 커밋됐을 수 있다(요청은 초 단위, 묶음 가져오기는 분 단위다).
LATE_SLACK = timedelta(minutes=30)


@dataclass
class _Failed:
    """적용 중에 변환하지 못한 값 — 값마다 건수, 그리고 그 행(둘째 바퀴가 다시 세지 않게)."""

    values: dict[str, int] = field(default_factory=dict)
    ids: set[uuid.UUID] = field(default_factory=set)


Row = tuple[uuid.UUID, str, uuid.UUID | None, Any]


def _locked_chunk(
    db: Session, target: Target, last: uuid.UUID | None, *, late: bool
) -> list[Row]:
    """그 칸만 읽고 잠근 덩어리 하나 — 행 전체(속성 30여 칸)를 ORM 객체로 싣지 않는다.

    `late` 면 **이 트랜잭션 밖에서** 최근에 바뀐 행만 — 이 트랜잭션이 고친 행은 `updated_at`
    이 `now()` 와 같고, 그 행은 첫 바퀴가 이미 잠가 남이 못 고쳤다(두 범위로 나눠 색인을 탄다).
    """
    stmt = (
        select(
            ObjectInstance.id,
            ObjectInstance.label,
            ObjectInstance.owner_workspace_id,
            ObjectInstance.properties[target.key],
        )
        .where(
            ObjectInstance.type_id == target.type_id,
            ObjectInstance.deleted_at.is_(None),
            ObjectInstance.properties.has_key(target.key),
        )
        .order_by(ObjectInstance.id)
        .limit(rewrite.CHUNK)
        .with_for_update()
    )
    if late:
        stmt = stmt.where(
            or_(
                and_(
                    ObjectInstance.updated_at >= func.now() - LATE_SLACK,
                    ObjectInstance.updated_at < func.now(),
                ),
                ObjectInstance.updated_at > func.now(),
            )
        )
    if last is not None:
        stmt = stmt.where(ObjectInstance.id > last)
    return [(row[0], row[1], row[2], row[3]) for row in db.execute(stmt)]


def _rewrite_rows(
    db: Session,
    user: User | None,
    target: Target,
    owner_slug: str,
    rows: list[Row],
    mapping: Mapping[str, str | None] | None,
    linker: RefLinker | None,
    why: str,
    failed: _Failed,
) -> tuple[int, int]:
    """덩어리 하나를 새 종류로 — (변환한 수, 비운 수). 변환할 수 없는 값은 `failed` 에 센다."""
    writes: list[tuple[uuid.UUID, bool, Any]] = []
    history: list[tuple[uuid.UUID, str, uuid.UUID | None, dict[str, Any]]] = []
    converted = cleared = 0
    for object_id, label, workspace_id, old in rows:
        if object_id in failed.ids:
            continue
        result = _converted(target, old, mapping, linker)
        if result.failures:
            failed.ids.add(object_id)
            for key, _reason in result.failures:
                failed.values[key] = failed.values.get(key, 0) + 1
            continue
        if not result.changed:
            continue
        writes.append((object_id, result.remove, result.value))
        if result.remove:
            cleared += 1
            after: dict[str, Any] = {}
        else:
            converted += 1
            after = {target.key: result.value}
        history.append(
            (
                object_id,
                f"{owner_slug}:{label}",
                workspace_id,
                rewrite.changed_property(target.key, old, after),
            )
        )
    if writes:
        # **덩어리 하나에 문장 하나** — 행마다 보내면 참조 색인 트리거가 행마다 돌았다
        # (실측: 200만 건 38분).
        rewrite.write_key(db, target.key, writes)
        audit.record_rows(
            db,
            action="object.update",
            actor=user,
            target_table="objects",
            rows=history,
            reason=why,
        )
    return converted, cleared


# --- 딸린 것 --------------------------------------------------------------------


def downstream(db: Session, targets: list[Target]) -> list[str]:
    """이 변경에 **딸려 깨지는 것** — 저장된 뷰 · 홈 위젯, 데이터 소스, 수신 시스템. 고치지
    않고 말한다(뷰는 사람의 것이고, 데이터 소스 · 수신 시스템은 바깥의 일이다)."""
    out = _views_breaking(db, targets)
    out.extend(_sources_touching(db, targets))
    for target in targets:
        owner = db.get(ObjectType, target.type_id)
        if owner is not None and owner.core:
            out.append(
                f"속성 {target.name}: 공개 타입이라 수신 시스템이 이 속성의 종류를 다시 맞출 "
                "때까지 그쪽에서 행 오류가 날 수 있습니다."
            )
    return out


def _sources_touching(db: Session, targets: list[Target]) -> list[str]:
    from app.modules.datasources.models import DataSource

    by_type: dict[uuid.UUID, list[Target]] = {}
    for target in targets:
        by_type.setdefault(target.type_id, []).append(target)
    out: list[str] = []
    for source in db.scalars(select(DataSource).where(DataSource.type_id.in_(list(by_type)))):
        for column in (source.mapping or {}).get("columns") or []:
            if not isinstance(column, dict):
                continue
            for target in by_type[source.type_id]:
                if column.get("target") != f"properties.{target.key}":
                    continue
                values = (
                    " — 열 값 대응(values)도 새 종류에 맞는지 보세요"
                    if column.get("values")
                    else ""
                )
                out.append(
                    f"데이터 소스 「{source.name}」 이 {target.name} 에 넣습니다 — 다음 "
                    f"동기화부터 {kind_label(target.after.data_type)}(으)로 읽습니다{values}."
                )
    return out


def _view_error(db: Session, view: Any) -> str | None:
    """저장된 뷰를 지금 정의로 열면 나는 오류 — 없으면 None. 저장할 때의 검사와 같은 함수다."""
    from app.modules.objects import conditions, paths, summary
    from app.modules.objects.services import properties_of
    from app.shared.errors import AppError

    owner = db.get(ObjectType, view.type_id)
    if owner is None:
        return None
    defs = properties_of(db, owner.id)
    resolver = paths.Resolver(db, owner)
    asked = [
        conditions.Condition(
            str(one.get("field", "")), str(one.get("op", "")), str(one.get("value", ""))
        )
        for one in (view.query or {}).get("conditions") or []
        if isinstance(one, dict)
    ]
    shape = view.summary or {}
    try:
        conditions.apply(select(ObjectInstance), defs, asked, resolver)
        if shape.get("group_by"):
            summary.check_group(defs, str(shape["group_by"]), resolver)
            if shape.get("split_by"):
                summary.check_group(defs, str(shape["split_by"]), resolver)
            summary.check_metric(
                defs, str(shape.get("metric") or "count"), shape.get("metric_field")
            )
    except AppError as caught:
        return caught.message
    return None


def _metric_error(db: Session, metric: Any) -> str | None:
    """지표 정의를 지금 정의로 지으면 나는 오류 — 없으면 None. 계산이 부르는 것과 같은 함수다
    (`metrics.spec.build`)."""
    from pydantic import ValidationError

    from app.modules.metrics import spec as metric_spec
    from app.shared.errors import AppError

    source = db.get(ObjectType, metric.source_type_id)
    if source is None:  # pragma: no cover - RESTRICT 라 원천 타입이 먼저 못 지워진다
        return None
    try:
        parsed = metric_spec.MetricSpec.model_validate(metric.spec or {})
        metric_spec.build(db, source, parsed, self_slug=metric.slug)
    except AppError as caught:
        return caught.message
    except ValidationError:  # 원래 깨진 정의 — 이 변경 탓이 아니다
        return None
    return None


def breaking(db: Session, mutate: Callable[[], None]) -> list[str]:
    """저장된 뷰 · 홈 위젯 · **지표** 가운데 `mutate` 로 **새로** 깨지는 것.

    손으로 견주지 않고 **흉내 낸다** — 세이브포인트 안에서 바꾼 뒤, 저장할 때 · 계산할 때와
    같은 검사를 다시 돌리고 되돌린다. 칸은 이어진 칸 주소(`ref.개발사.국가`)일 수도 있어서,
    손으로 견주면 다른 타입의 뷰 · 지표를 놓친다. 원래 깨져 있던 것은 이 변경 탓이 아니라
    말하지 않는다. 속성 삭제(`ontology.routes`)와 종류 변경이 함께 쓴다 — 지표는 예전에 둘 다
    안 봤다(2026-10-08).
    """
    from app.modules.metrics.models import MetricDef
    from app.modules.objects.models import SavedView

    saved = list(db.scalars(select(SavedView)))
    metrics = list(db.scalars(select(MetricDef)))
    if not saved and not metrics:
        return []
    before = {one.id: _view_error(db, one) for one in saved}
    counted = {one.id: _metric_error(db, one) for one in metrics}
    savepoint = db.begin_nested()
    try:
        mutate()
        db.flush()
        after = {one.id: _view_error(db, one) for one in saved}
        recounted = {one.id: _metric_error(db, one) for one in metrics}
    finally:
        savepoint.rollback()
    broken = [one for one in saved if before[one.id] is None and after[one.id] is not None]
    out = [
        f"저장된 뷰 「{one.name}」{'(홈 게시)' if one.home_order is not None else ''} 은(는) "
        f"이 변경 뒤 열면 오류가 납니다 — {after[one.id]}"
        for one in broken[:10]
    ]
    if len(broken) > 10:
        out.append(f"그 밖에 저장된 뷰 {len(broken) - 10}개도 이 변경 뒤 열면 오류가 납니다.")
    failing = [
        one for one in metrics if counted[one.id] is None and recounted[one.id] is not None
    ]
    out.extend(
        f"지표 「{one.label}」 은(는) 이 변경 뒤 계산이 실패합니다 — {recounted[one.id]}"
        for one in failing[:10]
    )
    if len(failing) > 10:
        out.append(f"그 밖에 지표 {len(failing) - 10}개도 이 변경 뒤 계산이 실패합니다.")
    return out


def _views_breaking(db: Session, targets: list[Target]) -> list[str]:
    """종류 변경으로 새로 깨지는 뷰 · 지표 — 새 정의를 적어 흉내 낸다(`breaking`)."""

    def write() -> None:
        for target in targets:
            definition = db.scalar(
                select(PropertyDef).where(
                    PropertyDef.owner_kind == "type",
                    PropertyDef.owner_id == target.type_id,
                    PropertyDef.key == target.key,
                )
            )
            if definition is None:
                continue
            for name, value in interfaces.shape_of(target.after).written().items():
                setattr(definition, name, value)

    return breaking(db, write)
