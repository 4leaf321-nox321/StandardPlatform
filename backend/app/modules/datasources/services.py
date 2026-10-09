"""동기화 — **바깥 행을 파일의 행으로 바꾸고, 나머지는 일괄 입력가 한다.**

여기서 새로 하는 것은 셋뿐이다:

    칸 대응   바깥 열 → key · label · description · alias · properties.<키>. 값 대응표
              (`values`)가 있으면 코드를 우리 고를 값으로(`"C" → "상용"`). 표에 없는 값은
              **오류 행**이다(`values_strict`, 기본 참) — 조용히 통과시키면 고를 값이 오염된다.
    다시 찾기 이 소스의 외부 식별자(별칭 `source:<slug>`) → 우리 식별자 → 별칭·이름. 겹치면
              오류 행. 찾은 것은 행에 `id` 를 박아 일괄 입력가 그 객체를 고치게 한다.
    뒷정리    적용 뒤 외부 식별자를 별칭으로 남기고, 켜 두었으면 이번에 안 온 것을 「사용
              중지」 로.

검증·권한·upsert·감사·이력은 `objects/bulk.py` 그대로다 — 규칙을 두 벌로 두지 않는다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.audit.models import AuditEntry

# **무덤의 규칙을 그대로 쓴다** — 끊긴 선을 끊는 길이 둘이 되면 한쪽에만 권한 검사가 남는다.
from app.modules.bundles import tombstones as graves
from app.modules.bundles.schemas import RelationTombstoneIn, TombstonesIn
from app.modules.datasources import fetchers, odata, ra_reports
from app.modules.datasources.models import DataSource, DataSourceRun
from app.modules.datasources.schemas import (
    CoreSuggestOut,
    CoreSuggestProperty,
    RunOut,
    SyncOut,
)
from app.modules.jobs import services as job_services
from app.modules.jobs.models import Job
from app.modules.notifications import services as notifications
from app.modules.objects import aliases, bulk
from app.modules.objects.models import ObjectAlias, ObjectInstance, ObjectRelation
from app.modules.objects.schemas import ImportRowOut
from app.modules.objects.services import properties_of
from app.modules.ontology import managed
from app.modules.ontology.interfaces import load_ends
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.shared import audit, extensions, singleton
from app.shared.batches import chunks as id_batches
from app.shared.errors import AppError, Conflict, code
from app.shared.permissions import require_owner_edit
from app.shared.text import compare_key

log = logging.getLogger(__name__)

#: 화면·기록에 싣는 오류 행의 상한.
ERROR_SAMPLE = 50
#: 「다른 곳이 이 소스를 지금 넣고 있다」 — 차례(round)는 이 코드를 보고 실패가 아니라
#: 건너뜀으로 적는다(그 소스에는 아무 일도 안 했다).
BUSY = code("DATASOURCES", 45)
#: 적용이 시작되면 실행 기록에 먼저 적어 두는 말 — 끝나면 지운다. 도중에 프로세스가
#: 죽으면 이것이 남아, 기록이 「계획 · 미완료」 로 보이지 않는다(조각마다 커밋되므로 이
#: 줄도 함께 남는다).
INTERRUPTED = (
    "적용 중입니다 — 작업이 끝났는데 이 줄이 이대로면 도중에 멈춘 것입니다(그 전에 들어간 "
    "조각은 그대로 있고, 다음 동기화가 이어서 맞춥니다)."
)
#: 작업을 빼앗겨(박동이 멎어 되살려져 다른 워커가 다시 집었다) 멈춘 실행.
TAKEN_OVER = (
    "이 실행을 돌던 워커가 멎은 사이 작업이 되돌려져, 여기서 멈추고 더 넣지 않았습니다 — "
    "다시 돈 동기화는 따로 기록됩니다."
)
#: 앞 조각이 이미 들어간 뒤에 멈췄을 때 덧붙이는 말.
PARTIAL = (
    "그 전에 들어간 조각(객체와 외부 식별자)은 그대로 있습니다 — 사용 중지 · 선 · 받은 자리는 "
    "다음 동기화가 다시 맞춥니다."
)
#: 끊은 자리 — 소스의 `options` 안에 둔다(표를 늘리지 않으려고). `_` 로 시작하는 키는 화면에
#: 안 나가고(`public_options`) 정의를 고쳐도 지켜진다(라우터의 수정).
RESUME = "_resume"
RESUME_EDGES = "_resume_relations"
#: 「바깥에서 사라져 사용 중지」 를 감사 기록에 박는 표식 — 다시 올라왔을 때 **이 소스가
#: 중지시킨 것만** 되살리려고. `_` 로 시작하는 키는 이력 · 알림이 칸으로 안 읽는다
#: (`history.py`).
MARK = "_datasource"
#: 칸 대응의 target 으로 쓸 수 있는 고정 자리.
FIXED_TARGETS = ("key", "label", "description", "alias", "status")

#: 시험이 갈아 끼우는 자리 — 진짜 OData 대신 가짜 응답.
transport: httpx.BaseTransport | None = None


def _one_of(raw: Any, what: str) -> tuple[str, str]:
    """`{"column": "열"}` 또는 `{"value": "고정값"}` — `(열, 값)` 중 하나만 채워 돌려준다."""
    if not isinstance(raw, dict):
        raise AppError(
            code("DATASOURCES", 20),
            f'{what} 은 {{"column": "열"}} 또는 {{"value": "고정값"}} 으로 적습니다.',
            status=422,
        )
    column = str(raw.get("column") or "").strip()
    value = str(raw.get("value") or "").strip()
    if bool(column) == bool(value):
        raise AppError(
            code("DATASOURCES", 20),
            f"{what} 에는 column 과 value 중 **하나만** 적습니다.",
            status=422,
        )
    return column, value


def _parse_edges(raw: dict[str, Any]) -> EdgeMap:
    """관계 대응을 읽는다 — **한 행이 선 하나**인 원천."""
    out = EdgeMap()
    kind = raw.get("relation")
    if isinstance(kind, str) and kind.strip():
        out.relation = kind.strip()
    elif isinstance(kind, dict):
        out.relation_column, out.relation = _one_of(kind, "relation")
    if not out.relation and not out.relation_column:
        raise AppError(
            code("DATASOURCES", 20),
            "관계 대응에 relation(관계 종류 slug, 또는 그것이 든 열)이 있어야 합니다.",
            status=422,
        )
    for name in ("src", "dst"):
        column, value = _one_of(raw.get(name) or {}, name)
        if value:
            raise AppError(
                code("DATASOURCES", 20),
                f"{name} 은 열이어야 합니다 — 고정값으로 선을 긋지 않습니다.",
                status=422,
            )
        setattr(out, name, column)
    note = raw.get("evidence_note")
    if isinstance(note, dict) and note:
        out.evidence_note, out.evidence_value = _one_of(note, "evidence_note")
    props = raw.get("properties")
    if isinstance(props, dict):
        out.properties = {
            str(key): str(one.get("column") or "").strip()
            for key, one in props.items()
            if isinstance(one, dict) and str(one.get("column") or "").strip()
        }
    mode = str(raw.get("mode") or "add").strip()
    if mode not in ("add", "replace"):
        raise AppError(
            code("DATASOURCES", 20),
            f"관계 대응의 mode 는 add · replace 중 하나입니다 ({mode!r}).",
            status=422,
        )
    out.mode = mode
    return out


def edge_row(spec: EdgeMap, raw: dict[str, Any]) -> dict[str, Any]:
    """원천 한 행 → 관계 적재의 한 줄. **빈 끝점은 그 줄의 오류로 남긴다**(짐작하지 않는다)."""
    out: dict[str, Any] = {
        "src": _text(raw.get(spec.src)),
        "dst": _text(raw.get(spec.dst)),
        "relation": (
            _text(raw.get(spec.relation_column)) if spec.relation_column else spec.relation
        ),
    }
    note = _text(raw.get(spec.evidence_note)) if spec.evidence_note else spec.evidence_value
    if note:
        out["evidence_note"] = note
    for key, column in spec.properties.items():
        value = raw.get(column)
        if value not in (None, ""):
            out[key] = value
    return out


@dataclass
class Column:
    source: str
    target: str
    values: dict[str, Any] | None = None
    values_strict: bool = True


@dataclass
class EdgeMap:
    """**한 행이 선 하나**인 원천(BOM · 매핑 표)의 대응.

    객체 소스와 **갈라 둔다**: 한 소스가 객체도 만들고 선도 만들면 계획이 두 겹이 되고,
    「무엇이 몇 건인가」 를 한 표로 못 읽는다. 원천 하나가 둘 다 담고 있으면 소스를 둘로
    만든다(같은 주소 · 다른 대응) — 그러면 각자의 계획을 각자 읽는다.

    모양은 정제 도구의 관계 파일(`sp_table.py` 의 `relations`)과 **같은 말**을 쓴다.
    """

    relation: str = ""
    """관계 종류 slug. 비우고 `relation_column` 을 적으면 행마다 다르다."""
    relation_column: str = ""
    src: str = ""
    """출발점을 담은 열 — 값은 그 객체의 식별자(없으면 별칭 · 이름)다."""
    dst: str = ""
    evidence_note: str = ""
    """근거를 담은 열. 비어 있으면 `evidence_value` 를 쓴다."""
    evidence_value: str = ""
    properties: dict[str, str] = field(default_factory=dict)
    """관계에 붙는 속성 — {속성 키: 열 이름}."""
    mode: str = "add"
    """`add`(기본) — 온 선만 잇는다. `replace` — 온 목록에 나온 (출발 객체 · 관계 종류)
    범위에서 **안 온 선을 끊는다**. 그 표가 그 범위의 정본일 때만."""

    def columns(self) -> list[str]:
        """원천에서 청할 열 — `$select` 가 이것으로 좁힌다."""
        wanted = [self.src, self.dst, self.relation_column, self.evidence_note]
        return [one for one in [*wanted, *self.properties.values()] if one]


@dataclass
class Mapping:
    external_key: str
    columns: list[Column]
    relations: EdgeMap | None = None
    """있으면 이 소스는 **선을 가져온다**(객체가 아니라). 둘을 섞지 않는다."""

    @classmethod
    def parse(cls, raw: dict[str, Any], defs: list[PropertyDef]) -> Mapping:
        """정의를 읽고 **틀린 곳을 말한다** — 없는 속성, 모르는 자리, 빈 외부 식별자."""
        edges = raw.get("relations")
        if isinstance(edges, dict) and edges:
            return cls(external_key="", columns=[], relations=_parse_edges(edges))
        external_key = str(raw.get("external_key") or "").strip()
        if not external_key:
            raise AppError(
                code("DATASOURCES", 20),
                "칸 대응에 external_key(바깥 식별자 열)가 없습니다 — 같은 객체를 다음에 다시 "
                "찾을 근거입니다.",
                status=422,
            )
        keys = {d.key for d in defs if d.data_type != "file"}
        columns: list[Column] = []
        for one in raw.get("columns") or []:
            if not isinstance(one, dict):
                raise AppError(code("DATASOURCES", 20), "칸 대응은 표여야 합니다.", status=422)
            source = str(one.get("source") or "").strip()
            target = str(one.get("target") or "").strip()
            if not source or not target:
                raise AppError(
                    code("DATASOURCES", 20),
                    "칸 대응에 source 와 target 이 있어야 합니다.",
                    status=422,
                )
            if target.startswith("properties."):
                if target.split(".", 1)[1] not in keys:
                    raise AppError(
                        code("DATASOURCES", 20),
                        f"칸 대응이 정의되지 않은 속성을 가리킵니다: {target}",
                        status=422,
                    )
            elif target not in FIXED_TARGETS:
                raise AppError(
                    code("DATASOURCES", 20),
                    f"칸 대응의 자리를 모릅니다: {target}. "
                    f"{', '.join(FIXED_TARGETS)} 또는 properties.<키>.",
                    status=422,
                )
            values = one.get("values")
            columns.append(
                Column(
                    source=source,
                    target=target,
                    values=dict(values) if isinstance(values, dict) else None,
                    values_strict=bool(one.get("values_strict", True)),
                )
            )
        if not any(c.target == "label" for c in columns):
            raise AppError(
                code("DATASOURCES", 20),
                "칸 대응에 label(이름)로 가는 열이 있어야 합니다.",
                status=422,
            )
        return cls(external_key=external_key, columns=columns)

    def with_core_envelope(self) -> Mapping:
        """형제 코어의 봉투에서 **상태 · 별칭**도 받는다 — 대응에 안 적었어도.

        상대에서 사용 중지한 것 · 뺀 별칭이 이쪽에 남으면 둘이 갈린다. 자동 제안이 만든 대응
        (`suggest_core_mapping` — 이름과 속성만)으로 만든 소스에는 이 둘이 없었다.
        """
        have = {one.target for one in self.columns}
        extra: list[Column] = []
        if "status" not in have:
            extra.append(Column(source="status", target="status"))
        if "alias" not in have:
            extra.append(Column(source="aliases", target="alias"))
        return Mapping(external_key=self.external_key, columns=[*self.columns, *extra])

    def select_clause(self) -> str:
        """`$select` 를 안 적었으면 대응에 쓰인 열만 청한다 — 표 전체를 끌어오지 않게."""
        wanted: list[str] = []
        used = (
            self.relations.columns()
            if self.relations is not None
            else [self.external_key, *(c.source for c in self.columns)]
        )
        for name in used:
            top = name.replace(".", "/").split("/")[0]
            if top not in wanted:
                wanted.append(top)
        return ",".join(wanted)


@dataclass
class Mapped:
    """바깥 행 하나를 파일의 행으로 바꾼 것 — 또는 왜 못 바꿨는지."""

    external_id: str
    row: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    merged_into: str = ""
    """사람이 합친 객체로 바깥의 다른 행과 함께 모였다 — 값은 그 행(이 바깥 식별자)에서만 받고
    이 행은 건너뛴다(`_collapse_merged`). 오류가 아니다."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return str(value)


def mirrors(source: DataSource) -> bool:
    """**바깥을 정본으로 보나**(거울) — 바깥에서 비운 칸은 이쪽도 비우고, 뺀 별칭은 뺀다.

    형제 코어(`sp_core`)는 늘 그렇다 — 상대가 보내는 한 줄이 그 객체의 지금 모습 전부이고,
    빈 칸은 키째 빼고 보낸다(「비었다」 를 가르는 것은 받는 쪽 몫이다 — `coreapi`). 예전에는
    빠진 칸을 「안 건드림」 으로 읽어, 허브에서 지운 값 · 뺀 별칭 · 사용 중지가 쌍둥이에 영영
    남았다(동기화가 더하고 바꾸기만 했다). 그 밖의 소스는 `options.mirror` 를 켰을 때만 —
    바깥 표에는 우리가 안 채운 칸이 흔해, 기본은 그대로 「빈 칸은 안 건드림」 이다.
    """
    return source.kind == "sp_core" or bool((source.options or {}).get("mirror"))


def mapping_of(source: DataSource, defs: list[PropertyDef]) -> Mapping:
    """이 소스의 대응 — 형제 코어면 봉투의 상태 · 별칭까지(`Mapping.with_core_envelope`)."""
    mapping = Mapping.parse(source.mapping or {}, defs)
    if source.kind == "sp_core" and mapping.relations is None:
        mapping = mapping.with_core_envelope()
    return mapping


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def map_row(
    mapping: Mapping,
    raw: dict[str, Any],
    slug: str,
    *,
    mirror: bool = False,
    keep: frozenset[str] = frozenset(),
) -> Mapped:
    """바깥 행 하나 → 파일의 행. 빈 칸은 「안 건드림」 이다 — `mirror` 면 「비움」(`\\null`).

    거울이어도 **비우지 않는 것**: 식별자 · 이름 · 상태(빈 값에 뜻이 없다), 그리고 `keep`
    (필수 속성) — 비우면 그 줄이 「값이 필요합니다」 오류가 되고, 오류 한 줄이 묶음 전체를
    막는다. 별칭 열이 있는데 별칭이 하나도 없으면 별칭을 비운다(뺀 것을 뺀다).
    """
    external = _text(odata.pick(raw, mapping.external_key)).strip()
    if not external:
        return Mapped(
            external_id="", error=f"바깥 식별자({mapping.external_key})가 비어 있습니다."
        )
    out: dict[str, Any] = {}
    alias_values: list[str] = []
    alias_mapped = False
    for column in mapping.columns:
        value = odata.pick(raw, column.source)
        if column.values is not None and not _empty(value):
            key = _text(value)
            if key in column.values:
                value = column.values[key]
            elif column.values_strict:
                return Mapped(
                    external_id=external,
                    error=f"{column.source}: 값 대응표에 없는 값입니다: {key}",
                )
        if column.target == "alias":
            alias_mapped = True
            items = value if isinstance(value, list) else [value]
            alias_values.extend(_text(one).strip() for one in items if _text(one).strip())
            continue
        if isinstance(value, list):
            value = bulk.MULTI_SEP.join(_text(one) for one in value)
        elif isinstance(value, bool):
            value = "예" if value else "아니오"
        name = (
            column.target.split(".", 1)[1]
            if column.target.startswith("properties.")
            else column.target
        )
        # 빈 값은 「안 건드림」 — 파일과 같다. 바깥이 비워 보낸 칸으로 우리 값을 지우지 않는다.
        # **거울이면 「비움」** — 바깥에서 지운 값이 이쪽에 남으면 둘이 갈린다.
        if _empty(value):
            if mirror and _clears(column.target, name, keep):
                out[name] = bulk.NULL_MARK
            continue
        out[name] = value
    if alias_values:
        out["aliases"] = bulk.MULTI_SEP.join(dict.fromkeys(alias_values))
    elif mirror and alias_mapped:
        out["aliases"] = bulk.NULL_MARK
    return Mapped(external_id=external, row=out)


def _clears(target: str, name: str, keep: frozenset[str]) -> bool:
    """거울에서 빈 칸을 비우는 자리인가 — 설명과 (필수가 아닌) 속성만."""
    if target == "description":
        return True
    return target.startswith("properties.") and name not in keep


@dataclass
class Known:
    """이 소스의 외부 식별자 — `_match` 가 한 번 읽고, 적용이 고쳐 가며 쓴다."""

    by_external: dict[str, uuid.UUID] = field(default_factory=dict)
    """비교키 → 객체."""
    held: dict[uuid.UUID, list[str]] = field(default_factory=dict)
    """객체 → 그 객체가 쥔 이 소스의 비교키. **둘 이상일 수 있다** — 같은 소스에서 온 둘을
    사람이 합치면 이긴 쪽이 둘 다 갖는다(`aliases.move`)."""


def _match(
    db: Session,
    object_type: ObjectType,
    slug: str,
    mapped: list[Mapped],
    *,
    by_name: bool = True,
) -> Known:
    """같은 객체를 다시 찾아 행에 `id` 를 박는다 — 외부 식별자 → (식별자는 bulk 가) →
    별칭·이름. `by_name=False` 면 이름으로 붙이지 않는다 — RA 보고서처럼 **같은 이름이 흔한**
    것을 이름으로 붙이면 다른 보고서를 덮어쓴다.

    ⚠️ **이름으로 찾을 때는 이미 이 소스의 다른 항목과 이어진 객체를 후보에서 뺀다** — 이번
    실행에서 다른 행이 먼저 차지한 것도. 이름으로 붙이는 것은 「사람이 먼저 만들어 둔 것」 ·
    「다른 소스가 만든 것」 과 합류하려는 것이다. 이 소스의 다른 항목에 이미 묶인 객체에
    붙이면 바깥의 두 항목이 우리 객체 하나로 겹친다 — 옛 이름이 별칭으로 남은 객체에 같은
    이름의 새 항목이 붙으면, 그 뒤로 한 객체가 두 항목의 값을 번갈아 받는다.

    이름으로는 **사람이 붙인 별칭만** 본다 — 다른 소스의 외부 식별자(`source:<slug>`)는 이름이
    아니다. 예전에는 모든 별칭을 봐서, 바깥 행의 이름이 다른 소스의 식별자 글자와 같으면
    엉뚱한 객체에 붙었다(2026-10-08).
    """
    kind = aliases.source_kind(slug)
    known = Known()
    for norm, object_id in db.execute(
        select(ObjectAlias.norm, ObjectAlias.object_id)
        .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
        .where(
            ObjectAlias.type_id == object_type.id,
            ObjectAlias.kind == kind,
            ObjectInstance.deleted_at.is_(None),
        )
    ):
        known.by_external[norm] = object_id
        known.held.setdefault(object_id, []).append(norm)
    #: 이미 이 소스의 항목과 이어진 객체 — 이름 · 별칭으로는 붙이지 않는다.
    taken: set[uuid.UUID] = set(known.by_external.values())
    names: dict[str, list[uuid.UUID]] = {}
    if by_name:
        for object_id, label in db.execute(
            select(ObjectInstance.id, ObjectInstance.label).where(
                ObjectInstance.type_id == object_type.id, ObjectInstance.deleted_at.is_(None)
            )
        ):
            names.setdefault(compare_key(label), []).append(object_id)
        for norm, object_id in db.execute(
            select(ObjectAlias.norm, ObjectAlias.object_id)
            .join(ObjectInstance, ObjectInstance.id == ObjectAlias.object_id)
            .where(
                ObjectAlias.type_id == object_type.id,
                ObjectAlias.kind == aliases.HUMAN,
                ObjectInstance.deleted_at.is_(None),
            )
        ):
            hits = names.setdefault(norm, [])
            if object_id not in hits:
                hits.append(object_id)

    for one in mapped:
        if one.error:
            continue
        found = known.by_external.get(compare_key(one.external_id))
        if found is not None:
            one.row["id"] = str(found)
            continue
        if not by_name:
            continue
        # 처음 만나는 행 — 별칭·이름으로 우리 것을 찾는다(사람이 먼저 만들어 둔 것과 합류).
        # 못 찾으면 bulk 가 식별자로 찾거나 새로 만든다.
        label = str(one.row.get("label") or "")
        named = names.get(compare_key(label), []) if label else []
        hits = [hit for hit in named if hit not in taken]
        if len(hits) == 1:
            one.row["id"] = str(hits[0])
            taken.add(hits[0])
        elif len(hits) > 1:
            one.error = (
                f"「{label}」 이 {len(hits)}개에 맞습니다 — "
                "바깥 식별자가 없어 고를 수 없습니다."
            )
    _collapse_merged(mapped)
    return known


def _collapse_merged(mapped: list[Mapped]) -> None:
    """**사람이 합친 객체**로 바깥의 여러 행이 모이면 — 한 행만 넣고 나머지는 건너뛴다.

    같은 소스에서 온 둘을 사람이 합치면 이긴 쪽이 이 소스의 외부 식별자를 둘 갖는다(합치기가
    진 쪽 것을 옮긴다). 그러면 다음 동기화가 바깥 두 행에 같은 `id` 를 박고, 일괄 입력이
    「같은 id 가 N행에도 있습니다」 로 **전부** 거절해 그 소스가 영영 멈췄다(2026-10-08).

    **사람의 합치기가 이긴다.** 값은 바깥 식별자 차례로 첫 행에서만 받는다 — 매번 같은 행이
    이겨야 값이 두 행 사이를 오가지 않는다. 나머지는 계획에 「합친 객체」 로 적는다(오류가
    아니다). 외부 식별자는 둘 다 남아 다음에도 그 객체를 찾는다.
    """
    groups: dict[str, list[Mapped]] = {}
    for one in mapped:
        if not one.error and one.row.get("id"):
            groups.setdefault(str(one.row["id"]), []).append(one)
    for rows in groups.values():
        norms = sorted({compare_key(one.external_id) for one in rows})
        if len(norms) < 2:
            # 같은 바깥 식별자가 두 번 온 것은 합친 객체가 아니다 — 일괄 입력이 그 줄을
            # 「같은 id」 로 말하게 둔다(바깥 표의 문제다).
            continue
        first = min(
            (one for one in rows if compare_key(one.external_id) == norms[0]),
            key=lambda one: one.external_id,
        )
        for other in rows:
            if compare_key(other.external_id) != norms[0]:
                other.merged_into = first.external_id


def _merged_plan(index: int, one: Mapped) -> bulk.RowPlan:
    """합친 객체로 모여 건너뛰는 행 — 계획의 한 줄(그대로)."""
    return bulk.RowPlan(
        row=index,
        action="unchanged",
        label=one.external_id,
        object_id=uuid.UUID(str(one.row["id"])),
        message=(
            f"합친 객체 — 같은 객체로 모이는 「{one.merged_into}」 행의 값으로 넣고 이 행은 "
            "건너뜁니다."
        ),
    )


def _remember_externals(
    db: Session,
    object_type: ObjectType,
    slug: str,
    sent: list[Mapped],
    row_plans: list[bulk.RowPlan],
    known: Known,
) -> None:
    """외부 식별자를 별칭으로 남긴다 — 다음 동기화가 이것으로 같은 객체를 다시 찾는다.

    이미 그 값을 쥔 객체면 그대로. 이 소스의 다른 값을 **하나** 쥐었으면 갈아 끼운다(바깥이
    식별자를 바꿨고, 우리 식별자 · 이름으로 다시 찾았다). **둘 이상이면 더한다** — 사람이 합친
    객체다. 갈아 끼우면 합친 짝의 식별자가 사라져 그 행이 다음에 새 객체를 만든다.
    """
    kind = aliases.source_kind(slug)
    for one, row_plan in zip(sent, row_plans, strict=True):
        object_id = row_plan.object_id
        if object_id is None:
            continue
        norm = compare_key(one.external_id)
        holder = known.by_external.get(norm)
        if holder is not None:
            # 이미 이 객체의 것이거나, 다른 객체가 쥐었다(고르지 않는다 — 유일 제약에 걸린다).
            continue
        mine = known.held.get(object_id, [])
        if len(mine) == 1:
            old = db.scalar(
                select(ObjectAlias).where(
                    ObjectAlias.object_id == object_id,
                    ObjectAlias.kind == kind,
                    ObjectAlias.norm == mine[0],
                )
            )
            if old is not None:
                old.value = one.external_id[: aliases.MAX_VALUE]
                old.norm = norm
                known.by_external.pop(mine[0], None)
                known.by_external[norm] = object_id
                known.held[object_id] = [norm]
                continue
        db.add(
            ObjectAlias(
                object_id=object_id,
                type_id=object_type.id,
                kind=kind,
                value=one.external_id[: aliases.MAX_VALUE],
                norm=norm,
            )
        )
        known.by_external[norm] = object_id
        known.held.setdefault(object_id, []).append(norm)


def _apply_chunk(
    db: Session,
    actor: User,
    object_type: ObjectType,
    source: DataSource,
    run: DataSourceRun,
    sent: list[Mapped],
    known: Known,
    *,
    owner: uuid.UUID | None,
    aliases_mode: str = "add",
    blank_missing: bool = False,
) -> bulk.Plan:
    """조각 하나를 넣고 — **같은 커밋에** 외부 식별자를 남긴다.

    일괄 입력은 조각마다 스스로 커밋한다. 예전에는 외부 식별자를 조각을 다 넣은 **뒤에** 따로
    남겼다 — 그 사이(선 받기 · 마지막 커밋)에서 예외가 나면 객체는 들어가고 외부 식별자는
    롤백되어, 다음 동기화가 같은 것을 이름으로 다시 찾거나 새로 만들었다(2026-10-08). 그래서
    일괄 입력이 커밋하기 **직전**(`before_commit`)에 함께 싣는다 — 둘은 함께 들어가거나 함께
    안 들어간다. 실행 기록의 「적용함」 도 그 커밋에 탄다.
    """
    captured: list[bulk.Plan] = []

    def ride(_session: Session) -> None:
        if not captured:
            return
        plan = captured.pop()
        # 새로 만든 객체가 먼저 들어가야 별칭의 외래키가 선다(둘 사이에 ORM 관계가 없다).
        db.flush()
        run.applied = True
        _remember_externals(db, object_type, source.slug, sent, plan.rows, known)

    event.listen(db, "before_commit", ride)
    try:
        return bulk.apply_objects(
            db,
            actor,
            object_type,
            [one.row for one in sent],
            owner_workspace_id=owner,
            source=source_name(source),
            aliases_mode=aliases_mode,
            blank_missing=blank_missing,
            before_apply=captured.append,
        )
    finally:
        event.remove(db, "before_commit", ride)


@dataclass
class SyncResult:
    run: DataSourceRun
    plan_rows: list[bulk.RowPlan]
    truncated: bool


def _auth(source: DataSource) -> odata.Auth:
    raw = source.auth or {}
    return odata.Auth(
        kind=str(raw.get("kind") or "none"),
        user=str(raw.get("user") or ""),
        secret=str(raw.get("secret") or ""),
    )


#: 미리 보기에 싣는 본문의 길이 — 한 화면에 몇 MB 를 펴지 않는다.
PREVIEW_TEXT = 300


def _short(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str) and len(value) > PREVIEW_TEXT:
            value = f"{value[:PREVIEW_TEXT]}… ({len(value):,}자)"
        out[key] = value
    return out


def _preview_ra(
    db: Session, source: DataSource, defs: list[PropertyDef], limit: int
) -> dict[str, Any]:
    """RA 보고서 — 첫 쪽 몇 건을 본문까지(잘라서), 그리고 우리 칸으로 바꾼 뒤."""
    try:
        fetched = ra_reports.fetch(
            source,
            auth=_auth(source),
            mode="initial",
            page_size=limit,
            max_rows=limit,
            transport=transport,
        )
    except AppError as caught:
        return {"columns": [], "rows": [], "mapped": [], "mapping_error": caught.message}
    rows = [_short(raw) for raw in fetched.rows]
    columns: list[str] = []
    for row in rows:
        columns.extend(name for name in row if name not in columns)
    mapped = []
    for raw in fetched.rows:
        one = ra_reports.to_row(raw, defs)
        mapped.append(
            {"external_id": one.external_id, "row": _short(one.row), "error": one.error}
        )
    return {"columns": columns, "rows": rows, "mapped": mapped, "mapping_error": None}


def preview(db: Session, source: DataSource, *, limit: int = 5) -> dict[str, Any]:
    """앞의 몇 행을 **그대로**와 **대응한 뒤** 로 — 칸 대응을 맞출 때 본다."""
    object_type = db.get(ObjectType, source.type_id)
    assert object_type is not None
    defs = properties_of(db, object_type.id)
    if source.kind == ra_reports.KIND:
        return _preview_ra(db, source, defs, limit)
    fetched = fetchers.fetch(
        source,
        auth=_auth(source),
        select=source.select,
        page_size=limit,
        max_rows=limit,
        transport=transport,
    )
    columns: list[str] = []
    for row in fetched.rows:
        for name in row:
            if name not in columns and not name.startswith("@") and name != "__metadata":
                columns.append(name)
    mapped: list[dict[str, Any]] = []
    try:
        mapping = mapping_of(source, defs)
    except AppError as caught:
        return {
            "columns": columns,
            "rows": fetched.rows,
            "mapped": [],
            "mapping_error": caught.message,
        }
    keep = frozenset(one.key for one in defs if one.required)
    for raw in fetched.rows:
        one = map_row(mapping, raw, source.slug, mirror=mirrors(source), keep=keep)
        mapped.append({"external_id": one.external_id, "row": one.row, "error": one.error})
    return {"columns": columns, "rows": fetched.rows, "mapped": mapped, "mapping_error": None}


def sync(db: Session, user: User | None, source: DataSource, *, apply: bool) -> SyncResult:
    """계획을 세우고(그리고 `apply` 면 넣고) 기록을 남긴다. 상태가 바뀌면 알린다.

    **부르는 쪽이 커밋하지 않는다** — 여기서 커밋한다(bulk 가 그렇고, 기록은 실패해도
    남아야 한다). **실패는 늘 실행 기록과 `last_status` 에 남는다**(`_sync` 의 그물) —
    예외로 빠져나가는 것은 「다른 곳이 이 소스를 지금 넣고 있다」(`BUSY`) 하나뿐이고, 그때는
    이 소스에 아무 일도 안 했다.

    **적용은 소스마다 한 곳에서만 돈다** — DB 의 자문 잠금(`singleton.held`). 워커가 둘이면
    화면의 「동기화」 와 타이머의 차례가 같은 소스를 함께 돌 수 있었고, 둘 다 같은 새 행을
    만들어 늦은 쪽이 외부 식별자의 유일 제약에서 터졌다 — 그 사이에 같은 객체가 둘이
    됐다(2026-10-08). 계획은 아무것도 안 바꾸므로 잠그지 않는다.
    """
    if not apply:
        return _sync_announced(db, user, source, apply=False)
    with singleton.held(f"datasource:{source.slug}") as locked:
        if locked is None:
            raise Conflict(
                BUSY,
                f"「{source.name}」 은 다른 작업이 지금 동기화하고 있습니다 — 겹쳐 돌리면 "
                "같은 행이 두 번 만들어집니다. 그 작업이 끝난 뒤에 다시 하세요.",
            )
        return _sync_announced(db, user, source, apply=True)


def _sync_announced(
    db: Session, user: User | None, source: DataSource, *, apply: bool
) -> SyncResult:
    before = source.last_status
    result = _sync(db, user, source, apply=apply)
    _announce(db, source, before=before)
    return result


def _announce(db: Session, source: DataSource, *, before: str | None) -> None:
    """**상태가 바뀔 때만 알린다.**

    타이머가 5분마다 도니까 실패할 때마다 알리면 하루에 288개가 쌓이고, 그러면 사람은
    이 종류를 통째로 안 읽게 된다 — 그때 이 알림은 없는 것과 같다. 복구도 알린다:
    안 알리면 실패 알림 하나를 들고 「아직도 안 되나」 를 손으로 확인하러 간다.

    계획만 본 것(`apply=false`)은 `last_status` 를 안 건드리므로 여기 안 걸린다.
    """
    after = source.last_status
    if after == before:
        return
    if after == "failed":
        notifications.notify_system_admins(
            db,
            kind=notifications.DATASOURCE_FAILED,
            title=f"데이터 소스 「{source.name}」 동기화가 실패했습니다",
            body="자동 동기화가 멈춘 것은 아닙니다 — 다음 차례에 다시 시도합니다. "
            "무엇이 막았는지는 소스 화면의 기록에 있습니다.",
            link="/admin/datasources",
        )
    elif after == "ok" and before == "failed":
        notifications.notify_system_admins(
            db,
            kind=notifications.DATASOURCE_RECOVERED,
            title=f"데이터 소스 「{source.name}」 동기화가 다시 됩니다",
            link="/admin/datasources",
        )
    db.commit()


def _sync(db: Session, user: User | None, source: DataSource, *, apply: bool) -> SyncResult:
    """한 번의 동기화 — **실행 기록부터 남기고, 무슨 일이 나도 그 기록을 닫는다.**

    예전에는 대응 · 타입 · 대신 돌릴 관리자를 실행 기록보다 먼저 봤고, 적용 중의 예외(선
    받기의 연결 오류 · 마지막 커밋의 유일 제약)는 그대로 빠져나갔다. 그러면 기록은 아예
    없거나 「계획 · 미완료」 로 남고 `last_status` 도 알림도 그대로라, 실패한 줄 아무도
    몰랐다(2026-10-08).
    """
    run = DataSourceRun(
        id=uuid.uuid4(),
        source_id=source.id,
        actor_label=(user.display_name or user.email) if user else "타이머",
    )
    db.add(run)
    db.flush()
    run_id, source_id, actor_label, slug = run.id, source.id, run.actor_label, source.slug
    try:
        return _sync_body(db, user, source, run, apply=apply)
    except job_services.Lost:
        # **작업을 빼앗겼다** — 박동이 멎어 되살려진 작업을 다른 워커가 다시 집었고, 커밋
        # 앞 검사(`jobs/services.still_mine`)가 이 조각의 커밋을 막았다. 아래 그물에
        # 맡기면 「실패」 기록을 적으려는 커밋이 같은 검사에 또 막히고, `last_status` 를
        # 「실패」 로 적으면 이어받은 워커가 끝낼 때 「다시 됩니다」 알림이 헛나간다. 그래서
        # 소스는 건드리지 않고, 앞 조각이 이미 들어갔으면 그 실행 기록만 **다른 연결로**
        # 닫는다 — 「적용 중」 으로 영영 남지 않게.
        db.rollback()
        _taken_over(run_id)
        raise
    except Exception as caught:  # 무엇이 나도 기록은 닫는다 — 스택은 로그에
        log.exception("데이터 소스 동기화가 도중에 멈췄습니다: %s", slug)
        return _crashed(db, source_id, run_id, actor_label, caught)


def _taken_over(run_id: uuid.UUID) -> None:
    """빼앗긴 작업이 남긴 실행 기록을 닫는다 — 앞 조각이 커밋됐을 때만 행이 있다."""
    with SessionLocal() as other:
        run = other.get(DataSourceRun, run_id)
        if run is None:  # 조각 하나도 커밋되기 전 — 실행 행도 함께 롤백됐다
            return
        run.status = "failed"
        run.errors = [TAKEN_OVER] + ([PARTIAL] if run.applied else [])
        run.finished_at = datetime.now(UTC)
        other.commit()


def _crashed(
    db: Session,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    actor_label: str,
    caught: Exception,
) -> SyncResult:
    """도중에 난 예외 — 되돌리고, **기록과 상태만은 남긴다.**

    조각마다 커밋된 객체(와 같은 커밋에 실린 외부 식별자)는 그대로 있다. 사용 중지 · 선 · 받은
    자리처럼 마지막 커밋에 실리던 것만 사라지고, 그것은 다음 동기화가 다시 한다(시계를 안
    옮겼다)."""
    db.rollback()
    said = (
        caught.message
        if isinstance(caught, AppError)
        else f"{type(caught).__name__}: {caught}"
    )
    run = db.get(DataSourceRun, run_id)
    if run is None:  # 조각 하나도 커밋되기 전 — 실행 행도 함께 롤백됐다
        run = DataSourceRun(id=run_id, source_id=source_id, actor_label=actor_label)
        db.add(run)
    errors = [f"동기화가 도중에 멈췄습니다 — {said[:500]}"]
    if run.applied:
        errors.append(PARTIAL)
    now = datetime.now(UTC)
    run.status = "failed"
    run.errors = errors
    run.finished_at = now
    source = db.get(DataSource, source_id)
    if source is not None:
        source.last_run_at = now
        source.last_status = "failed"
    db.commit()
    return SyncResult(run=run, plan_rows=[], truncated=False)


def _sync_body(
    db: Session, user: User | None, source: DataSource, run: DataSourceRun, *, apply: bool
) -> SyncResult:
    object_type = db.get(ObjectType, source.type_id)
    if object_type is None:
        raise AppError(code("DATASOURCES", 21), "소스가 가리키는 타입이 없습니다.", status=409)
    defs = properties_of(db, object_type.id)
    ra = source.kind == ra_reports.KIND
    # RA 보고서는 칸 대응을 틀이 정한다 — 사람이 적은 대응은 없다.
    mapping = Mapping(external_key="id", columns=[]) if ra else mapping_of(source, defs)

    if mapping.relations is None:
        # **잠긴 타입이면 바깥 표를 부르기 전에 끝낸다** — 읽어 온 뒤에 거절하면 그쪽
        # 시스템을 헛되게 부르고, 기록에는 「행 1784개를 읽었고 아무것도 안 넣었다」 만
        # 남는다. 그리고 거절 문구는 **이 화면에서 무엇을 적어야 하는가**를 말해야 한다 —
        # 받는 것 자체는 막을 일이 아니다(잠근 뜻은 「아무나 고치지 마라」 다).
        name = source_name(source)
        refused = managed.objects_refusal(object_type, source=name, what="넣지")
        if refused:
            run.status = "failed"
            run.errors = [
                refused,
                f"이 소스가 말하는 출처 이름은 「{name}」 입니다 — "
                f"「{managed.owner_of(object_type)}」 로 적으면 이 소스의 적재만 통과합니다"
                "(소스 수정 › 출처 이름).",
            ]
            run.finished_at = datetime.now(UTC)
            source.last_run_at = run.finished_at
            source.last_status = "failed"
            db.commit()
            return SyncResult(run=run, plan_rows=[], truncated=False)

    if ra:
        return _sync_ra(db, user, source, object_type, defs, run, apply=apply)

    notes: list[str] = []
    # 형제 코어는 **지난 차례가 끊은 자리**에서 잇는다(`Fetched.next`).
    resume = (
        _resume_of(source, RESUME, source.since_mark) if source.kind == "sp_core" else None
    )
    fetch = partial(
        fetchers.fetch,
        source,
        auth=_auth(source),
        max_rows=odata.MAX_ROWS,
        select=source.select or mapping.select_clause(),
        transport=transport,
    )
    try:
        if resume is not None:
            try:
                fetched = fetch(
                    since=str(resume.get("since") or ""), cursor=str(resume["next"])
                )
            except AppError as caught:
                # 상대가 끊은 자리를 못 알아본다(상대를 다시 깔았다) — 받은 자리부터 다시.
                notes.append(
                    f"지난번에 끊은 자리에서 잇지 못해 받은 자리부터 다시 받았습니다 — "
                    f"{caught.message}"
                )
                resume = None
                fetched = fetch()
        else:
            fetched = fetch()
    except AppError as caught:
        return _fail(db, source, run, [caught.message])
    since_used = str(resume.get("since") or "") if resume else source.since_mark

    if mapping.relations is not None:
        # **선을 가져오는 소스다** — 한 행이 선 하나다(BOM · 매핑 표). 객체 쪽 길(대응 · 다시
        # 찾기 · 무덤)은 타지 않는다: 이 소스는 객체를 만들지 않는다.
        run.rows_seen = len(fetched.rows)
        edges = [edge_row(mapping.relations, raw) for raw in fetched.rows]
        # **「더하기」 면 끝점을 못 찾은 줄이 나머지를 막지 않는다** — 이 소스는 매번 전량을
        # 읽으니 다음 동기화가 저절로 다시 넣는다. 「파일대로 맞춤」 은 그대로 엄격하다 —
        # 건너뛴 줄이 「파일에 없는 선」 으로 읽혀 있는 선을 끊을 수 있다.
        only_counts, only_errors, waiting, edges_ok = _apply_edges(
            db,
            user,
            source,
            object_type,
            edges,
            [],
            mode=mapping.relations.mode,
            apply=apply,
            skip_missing=mapping.relations.mode == "add",
            max_rows=max(len(edges), bulk.MAX_ROWS),
        )
        if fetched.truncated:
            only_errors.append(
                f"행이 {odata.MAX_ROWS}개를 넘어 끊었습니다 — `$filter` 로 나눠 동기화하세요."
            )
        ok = (
            edges_ok
            and not only_errors
            and not any(
                name.endswith("_error") and value for name, value in only_counts.items()
            )
        )
        if waiting:
            only_errors.append(_waiting_note(waiting))
        run.counts = only_counts
        run.errors = only_errors[:ERROR_SAMPLE]
        run.status = "ok" if (apply and ok) else ("planned" if ok else "failed")
        run.applied = bool(apply and ok)
        run.finished_at = datetime.now(UTC)
        if apply or not ok:
            source.last_run_at = run.finished_at
            source.last_status = run.status
        if run.applied:
            audit.record(
                db,
                action="datasource.sync",
                actor=user,
                target_table="data_sources",
                target_id=source.id,
                target_label=source.slug,
                workspace_id=source.workspace_id,
                changes={"rows": run.rows_seen, **only_counts},
            )
        db.commit()
        return SyncResult(run=run, plan_rows=[], truncated=fetched.truncated)

    # **무덤은 대응을 타지 않는다** — 값이 비어 있어서 「이름이 없다」 로 거절될 뿐이다.
    # 가르고 나서 각자의 길로 보낸다.
    graves = [one for one in fetched.rows if one.get(fetchers.CORE_DELETED)]
    alive = [one for one in fetched.rows if not one.get(fetchers.CORE_DELETED)]
    # **같은 키를 지웠다 다시 만들면** 한 증분에 무덤과 산 행이 함께 온다 — 산 것이 이긴다.
    # 무덤을 그대로 두면 방금 산 행으로 고친 객체를 `_bury` 가 사용 중지했다(2026-10-08).
    living = {compare_key(_text(odata.pick(one, mapping.external_key))) for one in alive}
    graves = [
        one
        for one in graves
        if compare_key(_text(one.get(mapping.external_key))) not in living
    ]
    mirror = mirrors(source)
    keep = frozenset(one.key for one in defs if one.required)
    mapped = [map_row(mapping, raw, source.slug, mirror=mirror, keep=keep) for raw in alive]
    # 거울이면 별칭을 **맞춘다**(뺀 것을 뺀다) — 아니면 더하기만(사람이 붙인 것을 지우지 않게).
    aliases_mode = "replace" if mirror else "add"
    run.rows_seen = len(fetched.rows)
    known = _match(db, object_type, source.slug, mapped)
    # 형제 코어는 끊은 자리를 기억해 다음 차례가 잇는다 — 그쪽의 끊김은 오류가 아니다.
    resumable = source.kind == "sp_core" and fetched.truncated and bool(fetched.next)

    # 대응에서 걸린 행은 계획에 오류로, 합친 객체로 모인 행은 「그대로」 로 싣고, 나머지를
    # bulk 에 넘긴다.
    plan_rows: list[bulk.RowPlan] = []
    merged_rows: list[bulk.RowPlan] = []
    errors: list[str] = []
    if fetched.truncated and not resumable:
        errors.append(
            f"행이 {odata.MAX_ROWS}개를 넘어 끊었습니다 — `$filter` 로 나눠 동기화하세요."
        )
    for index, one in enumerate(mapped, start=1):
        if one.error:
            plan_rows.append(
                bulk.RowPlan(
                    row=index, action="error", label=one.external_id, message=one.error
                )
            )
        elif one.merged_into:
            merged_rows.append(_merged_plan(index, one))
    plan_rows.extend(merged_rows)
    good = [one for one in mapped if not one.error and not one.merged_into]
    # bulk 의 상한(한 번에 5,000행)은 조각으로 넘는다 — 계획은 전부 세운 뒤에 적용한다.
    pieces = [good[i : i + bulk.MAX_ROWS] for i in range(0, len(good), bulk.MAX_ROWS)]
    actor = _actor(db, user)
    plans = [
        bulk.plan_objects(
            db,
            actor,
            object_type,
            [one.row for one in piece],
            owner_workspace_id=source.workspace_id,
            # **자기 출처 이름을 말한다** — 잠긴 타입(`managed_by`)에 넣는 근거다. 안 넘기면
            # 허브가 내려준 타입은 받기까지 막힌다(잠근 뜻은 그것이 아니다).
            source=source_name(source),
            aliases_mode=aliases_mode,
        )
        for piece in pieces
    ]
    offset = 0
    for plan in plans:
        errors.extend(plan.errors)
        for row_plan in plan.rows:
            plan_rows.append(
                bulk.RowPlan(
                    row=row_plan.row + offset,
                    action=row_plan.action,
                    label=row_plan.label,
                    key=row_plan.key,
                    object_id=row_plan.object_id,
                    changes=row_plan.changes,
                    message=row_plan.message,
                )
            )
        offset += len(plan.rows)
    ok = not errors and all(one.action != "error" for one in plan_rows)
    counts: dict[str, int] = _counts(plan_rows)
    revive = _revivable(db, object_type, source, mapped)
    # 끊김을 기억해 이을 수 있으면 화면에 「끊김」 으로 알리지 않는다 — 적용을 막는 표시다.
    truncated = fetched.truncated and not resumable

    if not apply or not ok:
        if revive:
            counts["revived"] = len(revive)
        if wants_relations(source):
            # **선도 미리 세어 본다** — 계획에 객체만 나오면 사람은 선이 올 줄 모른다.
            edge_counts, edge_errors, edges_ok = _relations_or_note(
                db, user, source, object_type, apply=False
            )
            counts = {**counts, **edge_counts}
            errors.extend(edge_errors)
            ok = ok and edges_ok
        run.status = "planned" if ok else "failed"
        run.counts = counts
        run.errors = (
            errors
            + [
                f"{one.row}행 {one.label}: {one.message}"
                for one in plan_rows
                if one.action == "error"
            ]
            + notes
        )[:ERROR_SAMPLE]
        run.finished_at = datetime.now(UTC)
        if not ok:
            source.last_run_at = run.finished_at
            source.last_status = "failed"
        db.commit()
        return SyncResult(run=run, plan_rows=plan_rows, truncated=truncated)

    # 적용 — 조각마다 bulk 가 검증을 다시 하고 커밋한다(외부 식별자도 그 커밋에 탄다). 끝나기
    # 전에 멈추면 이 표시가 기록에 남는다.
    run.status = "failed"
    run.errors = [INTERRUPTED]
    applied_rows: list[bulk.RowPlan] = []
    for piece in pieces:
        done = _apply_chunk(
            db,
            actor,
            object_type,
            source,
            run,
            piece,
            known,
            owner=source.workspace_id,
            aliases_mode=aliases_mode,
        )
        if not done.ok:
            run.status = "failed"
            run.errors = (
                done.errors
                + [
                    f"{one.row}행 {one.label}: {one.message}"
                    for one in done.rows
                    if one.action == "error"
                ][:ERROR_SAMPLE]
                + ([PARTIAL] if run.applied else [])
            )
            run.finished_at = datetime.now(UTC)
            source.last_run_at = run.finished_at
            source.last_status = "failed"
            db.commit()
            return SyncResult(run=run, plan_rows=plan_rows, truncated=truncated)
        applied_rows.extend(done.rows)

    # 이번에 온 것 — 바깥 식별자로도, 객체로도(합친 객체는 식별자 하나만 와도 온 것이다).
    seen_norms = {compare_key(one.external_id) for one in mapped if not one.error}
    seen_ids = {one.object_id for one in applied_rows if one.object_id is not None}
    seen_ids |= {one.object_id for one in merged_rows if one.object_id is not None}
    revived = _revive(db, actor, object_type, source, revive)
    deprecated = _bury(db, actor, object_type, source, graves, mapping.external_key)
    held_back = 0
    if source.deprecate_missing:
        more, held_back = _deprecate_missing(
            db, actor, object_type, source, seen_norms, seen_ids
        )
        deprecated += more

    # **선은 객체 뒤에** — 선은 양 끝이 있어야 선다. 같은 실행 · 같은 트랜잭션이다.
    edge_counts = {}
    relations_failed = False
    if wants_relations(source):
        edge_counts, edge_errors, edges_ok = _relations_or_note(
            db, user, source, object_type, apply=True
        )
        errors.extend(edge_errors)
        if not edges_ok:
            # 객체는 들어갔다(조각마다 커밋) — 선만 못 넣었다. 기록을 「ok」 로 두면 사람은
            # 선이 빠진 줄 모른다. 선의 시계는 안 옮겼으니 다음 동기화가 같은 자리에서 다시
            # 받는다. 처음부터 다시 받기의 정리를 멈춘 것(`_prune_unseen`)도 여기로 온다 —
            # 그때는 받은 선은 넣고 시계도 옮겼다(그 까닭이 기록에 남는다).
            relations_failed = True

    # **끝까지 받고 적용에 성공했을 때만 시계를 옮긴다.** 중간에 옮기면 그 사이 것을 영영
    # 안 받고, 그 사실은 어디에도 안 뜬다. 끊은 형제 코어는 시계 대신 **끊은 자리**를 적는다.
    if source.kind == "sp_core":
        notes.extend(_advance(source, RESUME, fetched, resume, since_used))
    elif fetched.as_of and not fetched.truncated:
        source.since_mark = fetched.as_of

    warnings: list[str] = []
    if held_back:
        warnings.append(
            f"바깥에서 한꺼번에 {held_back}건이 오지 않았습니다 — 이 소스가 넣어 쓰는 것의 "
            "절반이 넘어 사용 중지하지 않았습니다(받은 행은 반영했습니다). 바깥 표의 필터 · "
            "권한 · 쪽 넘김을 확인하세요. 정말 사라진 것이면 객체 화면에서 골라 사용 "
            "중지하세요 — 그 뒤로는 멈추지 않습니다."
        )
    run.status = "failed" if relations_failed or held_back else "ok"
    run.applied = True
    run.counts = {
        **counts,
        "deprecated": deprecated,
        **({"revived": revived} if revived else {}),
        **({"gone_held_back": held_back} if held_back else {}),
        **edge_counts,
    }
    # 선 쪽의 말(처음부터 다시 받음 · 끊김 거절) · 끊은 자리는 **성공한 실행에도 남긴다** —
    # 사람이 「무엇이 더 있었나」 를 그 기록에서 읽는다.
    run.errors = (warnings + errors + notes)[:ERROR_SAMPLE]
    run.finished_at = datetime.now(UTC)
    source.last_run_at = run.finished_at
    source.last_status = run.status
    audit.record(
        db,
        action="datasource.sync",
        actor=user,
        target_table="data_sources",
        target_id=source.id,
        target_label=source.slug,
        workspace_id=source.workspace_id,
        changes={"rows": len(mapped), **run.counts},
    )
    db.commit()
    return SyncResult(run=run, plan_rows=applied_rows + merged_rows, truncated=truncated)


def _relations_or_note(
    db: Session,
    user: User | None,
    source: DataSource,
    object_type: ObjectType,
    *,
    apply: bool,
) -> tuple[dict[str, int], list[str], bool]:
    """선 받기 — **상대에 닿지 못해도 객체 쪽 결과는 남긴다.**

    예전에는 선 창구의 연결 오류가 예외로 빠져나가, 객체를 넣은 실행의 마지막 커밋(외부
    식별자 · 사용 중지 · 객체의 시계)까지 롤백되고 기록에는 「계획 · 미완료」 만 남았다
    (2026-10-08). 이제는 그 실행의 실패로 적고 선의 시계만 안 옮긴다."""
    try:
        return _sync_relations(db, user, source, object_type, apply=apply)
    except AppError as caught:
        return {}, [f"선을 받지 못했습니다 — {caught.message}"], False


def _resume_of(source: DataSource, key: str, mark: str) -> dict[str, Any] | None:
    """지난 차례가 **끊은 자리** — 지금의 시계에서 시작한 것일 때만(시계를 손으로
    비웠으면 버린다)."""
    got = (source.options or {}).get(key)
    if isinstance(got, dict) and got.get("next") and got.get("from") == mark:
        return got
    return None


def _set_resume(source: DataSource, key: str, value: dict[str, Any] | None) -> None:
    current = dict(source.options or {})
    if current.get(key) == value or (value is None and key not in current):
        return
    if value is None:
        current.pop(key, None)
    else:
        current[key] = value
    # JSONB 는 제자리 변경을 모른다 — 새 dict 로 갈아 끼운다.
    source.options = current


def public_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """화면에 내보이는 설정 — 동기화가 적어 두는 칸(`_` 로 시작)은 뺀다."""
    return {key: value for key, value in (options or {}).items() if not key.startswith("_")}


def internal_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """동기화가 적어 두는 칸 — 정의를 고쳐도 지킨다(화면은 이것을 모른다)."""
    return {key: value for key, value in (options or {}).items() if key.startswith("_")}


def _earlier(floor: Any, as_of: str) -> str:
    """둘 중 이른 시계. 시각으로 못 읽으면 `as_of`."""
    if not floor:
        return as_of
    try:
        first = datetime.fromisoformat(str(floor).replace("Z", "+00:00"))
        second = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return as_of
    return str(floor) if first < second else as_of


def _advance(
    source: DataSource,
    key: str,
    fetched: odata.Fetched,
    resume: dict[str, Any] | None,
    since_used: str,
) -> list[str]:
    """형제 코어의 시계를 옮긴다 — **다 받았으면 시계를, 끊겼으면 끊은 자리를.** 남길 말을
    돌려준다.

    예전에는 끊기면(5만 행) 시계도 자리도 안 남겨, 다음 차례가 같은 `since` 로 같은 5만
    행을 다시 받아 **영영 앞으로 못 갔다** — 「다음 동기화가 같은 자리에서 잇습니다」 는
    사실이 아니었다(2026-10-08). 이제 받은 만큼 넣고 상대가 준 다음 커서를 적어 두면 다음
    차례가 거기서 잇는다.

    다 받은 뒤의 시계는 **처음 끊었을 때 상대의 시계**(`floor`)와 `as_of` 중 이른 것이다.
    커서는 지나간 자리를 다시 안 보므로, 여러 차례에 걸쳐 받는 동안 늦게 커밋된 적재 · 받은
    뒤에 지워진 것은 커서만으로는 안 온다 — 그 시계에서 다시 받으면 온다(같은 행을 한 번
    더 받을 뿐이다).
    """
    edges = key == RESUME_EDGES
    clock = "relations_since_mark" if edges else "since_mark"
    mark = str(getattr(source, clock) or "")
    if fetched.truncated and fetched.next:
        floor = (resume or {}).get("floor") or fetchers.core_watermark(
            base_url=source.base_url,
            type_slug=source.entity_set,
            auth=_auth(source),
            transport=transport,
        )
        _set_resume(
            source,
            key,
            {"from": mark, "since": since_used, "next": fetched.next, "floor": floor},
        )
        noun, unit = ("선", "줄") if edges else ("행", "행")
        return [
            f"{noun}이 많아 이번에는 {len(fetched.rows):,}{unit}까지 넣었습니다 — 다음 "
            "동기화가 끊은 자리에서 잇습니다."
        ]
    if fetched.as_of and not fetched.truncated:
        setattr(source, clock, _earlier((resume or {}).get("floor"), fetched.as_of))
        _set_resume(source, key, None)
    return []


#: 선까지 받는 소스 — 코어 창구가 객체와 선을 같은 규칙으로 열어 주는 종류.
RELATION_KINDS = ("sp_core",)
#: 관계 적재가 모르는 칸 — 코어 봉투에서 끊김 표시는 떼고 보낸다. **도착 타입(`dst_type`)은
#: 싣는다** — 끝이 인터페이스인 종류는 구현 타입이 여럿이고 식별자는 타입 안에서만 하나라,
#: 그것 없이는 두 구현 타입에 같은 식별자가 있을 때 어느 것인지 못 정한다(`bulk._dst_allowed`).
#: 예전에는 떼고 보내, 그런 선이 「여러 타입에 있습니다」 로 영영 기다렸다.
EDGE_SKIP = (fetchers.CORE_DELETED,)


def _fail(
    db: Session, source: DataSource, run: DataSourceRun, errors: list[str]
) -> SyncResult:
    run.status = "failed"
    run.errors = errors[:ERROR_SAMPLE]
    run.finished_at = datetime.now(UTC)
    source.last_run_at = run.finished_at
    source.last_status = "failed"
    db.commit()
    return SyncResult(run=run, plan_rows=[], truncated=False)


def _sync_ra(
    db: Session,
    user: User | None,
    source: DataSource,
    object_type: ObjectType,
    defs: list[PropertyDef],
    run: DataSourceRun,
    *,
    apply: bool,
) -> SyncResult:
    """RA 보고서(ADR 0018) — 읽기 · 바꾸기 · 계획 → 적용은 다른 소스와 같고, 다른 것은 넷:
    이름으로 붙이지 않고, 행을 소유 부서끼리 모아 넘기고, 지우는 대신 원본 상태를 적고, 커서와
    대조 시각을 **끝까지 받고 적용에 성공했을 때만** 옮긴다."""
    # **RA 를 부르기 전에 끝낸다** — 이 판 전에 엉뚱한 타입으로 저장된 소스, 나중에 RA 번호
    # 칸이 지워진 타입. 실행 기록에 까닭이 남는다.
    refused = ra_reports.type_refusal(object_type, defs)
    if refused:
        return _fail(db, source, run, [refused])
    mode = ra_reports.mode_of(source)
    try:
        fetched = ra_reports.fetch(
            source,
            auth=_auth(source),
            mode=mode,
            page_size=source.page_size,
            max_rows=odata.MAX_ROWS,
            transport=transport,
        )
    except AppError as caught:
        return _fail(db, source, run, [caught.message])

    # 같은 보고서가 두 번 오면(겹쳐 읽기 · 쪽 경계) 뒤의 것 하나 — 한 계획에 같은 객체가 둘이면
    # 일괄 입력이 그 줄을 오류로 만든다.
    latest: dict[str, ra_reports.Converted] = {}
    broken: list[ra_reports.Converted] = []
    for raw in fetched.rows:
        one = ra_reports.to_row(raw, defs)
        if one.error:
            broken.append(one)
        else:
            latest[one.external_id] = one
    converted = list(latest.values())
    moved = ra_reports.prune_refs(db, defs, converted)
    ra_reports.clear_emptied(defs, converted)
    # **소유 부서끼리 모은다** — 일괄 입력은 소유 부서를 호출마다 하나 받는다. 차례를 정렬로
    # 바꾸고 그 차례 그대로 끝까지 가야 적용 결과와 외부 식별자가 짝이 맞는다.
    owner_ids = ra_reports.owners(db, {one.owner_slug for one in converted if one.owner_slug})

    def owner_of(one: ra_reports.Converted) -> uuid.UUID | None:
        return owner_ids.get(one.owner_slug or "", source.workspace_id)

    converted.sort(key=lambda one: str(owner_of(one) or ""))
    mapped = [Mapped(external_id=one.external_id, row=one.row) for one in converted]
    run.rows_seen = len(mapped) + len(broken)
    known = _match(db, object_type, source.slug, mapped, by_name=False)

    groups: list[tuple[uuid.UUID | None, list[Mapped]]] = []
    for one, row in zip(converted, mapped, strict=True):
        if row.merged_into:
            continue
        owner = owner_of(one)
        if not groups or groups[-1][0] != owner or len(groups[-1][1]) >= bulk.MAX_ROWS:
            groups.append((owner, []))
        groups[-1][1].append(row)

    actor = _actor(db, user)
    plan_rows: list[bulk.RowPlan] = [
        bulk.RowPlan(row=index, action="error", label=bad.external_id, message=bad.error or "")
        for index, bad in enumerate(broken, start=1)
    ]
    merged_rows = [
        _merged_plan(len(broken) + index, one)
        for index, one in enumerate(mapped, start=1)
        if one.merged_into
    ]
    errors: list[str] = []
    if fetched.truncated:
        errors.append(
            f"행이 {odata.MAX_ROWS}개를 넘어 중단했습니다 — 조직을 나눠 소스를 여러 개 "
            "생성하세요."
        )
    offset = len(plan_rows)
    plan_rows.extend(merged_rows)
    for owner, sent in groups:
        plan = bulk.plan_objects(
            db,
            actor,
            object_type,
            [one.row for one in sent],
            owner_workspace_id=owner,
            source=source_name(source),
            blank_missing=True,
        )
        errors.extend(plan.errors)
        for row_plan in plan.rows:
            plan_rows.append(
                bulk.RowPlan(
                    row=row_plan.row + offset,
                    action=row_plan.action,
                    label=row_plan.label,
                    key=row_plan.key,
                    object_id=row_plan.object_id,
                    changes=row_plan.changes,
                    message=row_plan.message,
                )
            )
        offset += len(plan.rows)
    # 깨진 행(번호 · 제목이 빈 것)은 건너뛰고 적어 둔다 — 한 건 때문에 조직 전체가 멈추지 않게.
    ok = not errors and all(one.action != "error" for one in plan_rows[len(broken) :])
    # 수만 담는다(실행 기록의 모양) — 전량으로 받았나는 0 · 1 로.
    counts: dict[str, Any] = {
        **_counts(plan_rows),
        "full_read": int(fetched.full),
        "tags_as_text": moved,
    }
    if not apply or not ok:
        run.status = "planned" if ok else "failed"
        run.counts = counts
        run.errors = (
            errors
            + [
                f"{one.row}행 {one.label}: {one.message}"
                for one in plan_rows
                if one.action == "error"
            ]
        )[:ERROR_SAMPLE]
        run.finished_at = datetime.now(UTC)
        if not ok:
            source.last_run_at = run.finished_at
            source.last_status = "failed"
        db.commit()
        return SyncResult(run=run, plan_rows=plan_rows, truncated=fetched.truncated)

    # 적용 — 외부 식별자는 조각의 커밋에 함께 탄다(`_apply_chunk`). 끝나기 전에 멈추면 이
    # 표시가 기록에 남는다.
    run.status = "failed"
    run.errors = [INTERRUPTED]
    applied: list[bulk.RowPlan] = []
    for owner, sent in groups:
        done = _apply_chunk(
            db, actor, object_type, source, run, sent, known, owner=owner, blank_missing=True
        )
        if not done.ok:
            return _fail(
                db,
                source,
                run,
                done.errors
                + [
                    f"{one.row}행 {one.label}: {one.message}"
                    for one in done.rows
                    if one.action == "error"
                ]
                + ([PARTIAL] if run.applied else []),
            )
        applied.extend(done.rows)

    # 이번에 온 것 — **깨진 행(제목이 빈 것)도 온 것이다.** 예전에는 그것을 빼서, 전량 대조가
    # 제목만 빈 보고서를 「원본에서 내려감」 으로 잘못 적었다(2026-10-08).
    seen = {compare_key(one.external_id) for one in mapped}
    seen |= {compare_key(bad.external_id) for bad in broken if bad.external_id}
    full = fetched.full and not fetched.truncated
    marks = ra_reports.reconcile(db, actor, object_type, source, seen, full=full)
    counts = {**counts, **marks}
    held_back = marks.get("gone_held_back", 0)
    now = datetime.now(UTC)
    if fetched.as_of and not fetched.truncated:
        source.since_mark = fetched.as_of
    warnings: list[str] = []
    if held_back:
        # 대조 시각을 안 옮긴다 — 다음 차례에 다시 대조하고, 그사이 사람이 본다.
        warnings.append(
            f"전량 대조에서 보고서 {held_back}건이 한꺼번에 수신되지 않았습니다 — 보유한 "
            "것의 절반이 넘어 「원본에서 내려감」 을 표시하지 않았습니다. RA 읽기 계정의 "
            "권한과 선택한 조직을 확인하세요(수신한 보고서는 반영했습니다)."
        )
    elif full:
        source.reconciled_at = now
    run.status = "failed" if held_back else "ok"
    run.applied = True
    run.counts = counts
    run.errors = (
        warnings
        + [
            f"{one.row}행 {one.label}: {one.message}"
            for one in plan_rows
            if one.action == "error"
        ]
    )[:ERROR_SAMPLE]
    run.finished_at = now
    source.last_run_at = now
    source.last_status = run.status
    audit.record(
        db,
        action="datasource.sync",
        actor=user,
        target_table="data_sources",
        target_id=source.id,
        target_label=source.slug,
        workspace_id=source.workspace_id,
        changes={"rows": run.rows_seen, **counts},
    )
    db.commit()
    return SyncResult(run=run, plan_rows=applied + merged_rows, truncated=fetched.truncated)


def wants_relations(source: DataSource) -> bool:
    """이 소스가 **선도 받나.** 종류가 코어 창구일 때만 — 그쪽이 선을 열어 준다."""
    return source.kind in RELATION_KINDS and bool((source.options or {}).get("relations"))


def _edge_row(raw: dict[str, Any]) -> dict[str, Any]:
    """코어의 선 한 줄 → 관계 적재의 한 줄. **봉투 이름이 그대로 칸 이름이다.**"""
    return {key: value for key, value in raw.items() if key not in EDGE_SKIP}


def _apply_edges(
    db: Session,
    user: User | None,
    source: DataSource,
    object_type: ObjectType,
    rows: list[dict[str, Any]],
    gone: list[dict[str, Any]],
    *,
    mode: str,
    apply: bool,
    skip_missing: bool = False,
    max_rows: int = bulk.MAX_ROWS,
    seen: set[uuid.UUID] | None = None,
) -> tuple[dict[str, int], list[str], list[Waiting], bool]:
    """선을 계획하고(또는 넣고) **셈 · 오류 줄 · 끝점을 못 찾아 건너뛴 줄 · 됐나**를 돌려준다 —
    코어 쪽과 대응 쪽이 함께 쓴다.

    `seen` 을 주면 이번에 온 줄이 가리킨 **이쪽 선**(이미 있던 것 · 새로 이은 것)의 id 를
    담는다 — 처음부터 다시 받을 때 안 온 선을 가리는 근거다(`_prune_unseen`).

    새로 이은 선의 기록에는 이 소스의 표식(`MARK`)을 단다 — 나중에 「이 소스가 이은 선」 만
    골라 끊을 근거다. 사람이 화면에서 이은 선과 가를 자리가 그것 말고 없다.

    **됐나(`ok`)를 셈으로 가르지 않는다** — 계획 전체가 거절되는 오류(한 번에 5,000줄 넘음 ·
    모르는 열 · 잠긴 타입)는 줄이 하나도 없어 `*_error` 셈이 0 이다. 셈만 보던 코어 쪽은 그것을
    성공으로 알고 시계를 옮겨, 그 증분의 선을 영영 놓쳤다(2026-10-08).

    `skip_missing` 이면 끝점을 못 찾은 줄이 **나머지를 막지 않는다** — 건너뛰고 돌려준다
    (`relations_waiting` 으로 센다). 타입마다 소스가 따로라 가리키는 쪽이 아직 안 들어왔을
    수 있다. 끄면 예전처럼 그런 줄 하나가 전부를 막는다.

    끊긴 선은 무덤과 같은 규칙으로 끊는다(`bundles/tombstones.py` — 줄마다 그 객체를 고칠
    수 있는지 다시 본다). 규칙을 두 벌로 적지 않으려고 그 모듈을 그대로 쓴다.

    `max_rows` — 동기화는 작업(워커)에서 돈다. 요청 경로의 5,000줄 상한을 그대로 두면 한 번에
    5,000줄이 넘게 온 증분이 매번 「한 번에 5000행까지」 로 거절되어 시계가 영영 안
    움직였다(2026-10-08). 받는 쪽의 상한(`odata.MAX_ROWS`)이 이미 크기를 정한다.
    """
    actor = _actor(db, user)
    errors: list[str] = []
    name = source_name(source)
    plan = bulk.plan_relations(
        db,
        actor,
        object_type,
        rows,
        mode=mode,
        source=name,
        skip_missing=skip_missing,
        max_rows=max_rows,
    )
    waiting = [Waiting(rows[one.row - 1], one.message) for one in plan.rows if one.skipped]
    counts = _edge_counts(plan.counts, len(waiting))
    errors.extend(plan.errors)
    errors.extend(
        f"선 {one.row}줄 {one.label}: {one.message}"
        for one in plan.rows
        if one.action == "error"
    )
    cut = graves.run(
        db,
        actor,
        TombstonesIn(
            relations=[
                RelationTombstoneIn(
                    type_slug=object_type.slug,
                    src=str(one.get("src") or ""),
                    relation=str(one.get("relation") or ""),
                    dst=str(one.get("dst") or ""),
                    # 끝이 인터페이스면 같은 식별자가 두 구현 타입에 있을 수 있다.
                    dst_type=str(one.get("dst_type") or ""),
                )
                for one in gone
                if one.get("src") and one.get("relation") and one.get("dst")
            ]
        ),
        source=name,
        apply=apply and plan.ok,
    )
    unlinked = cut.counts.get("unlink", 0)
    errors.extend(
        f"끊김 {one.label}: {one.message}" for one in cut.rows if one.action == "error"
    )
    if not apply or not plan.ok or not cut.ok:
        counts["relations_unlink"] = counts.get("relations_unlink", 0) + unlinked
        if seen is not None:
            seen.update(_edge_ids(plan.rows))
        return counts, errors, waiting, plan.ok and cut.ok

    done = bulk.apply_relations(
        db,
        actor,
        object_type,
        rows,
        mode=mode,
        source=name,
        skip_missing=skip_missing,
        max_rows=max_rows,
        mark={MARK: {"slug": source.slug}},
        datasource_id=source.id,
    )
    if seen is not None:
        seen.update(_edge_ids(done.rows))
    waiting = [Waiting(rows[one.row - 1], one.message) for one in done.rows if one.skipped]
    counts = _edge_counts(done.counts, len(waiting))
    counts["relations_unlink"] = counts.get("relations_unlink", 0) + unlinked
    if not done.ok:
        errors.extend(done.errors)
        errors.extend(
            f"선 {one.row}줄 {one.label}: {one.message}"
            for one in done.rows
            if one.action == "error"
        )
    return counts, errors, waiting, done.ok


def _edge_ids(rows: list[bulk.RowPlan]) -> set[uuid.UUID]:
    """계획 · 적용의 줄 → 그 줄이 가리킨 이쪽 선의 id(이미 있던 것 · 새로 이은 것). 건너뛴 줄 ·
    오류 줄은 없다 — 이쪽에 그 선이 없다."""
    return {
        one.object_id
        for one in rows
        if one.object_id is not None
        and not one.skipped
        and one.action in ("create", "update", "unchanged")
    }


@dataclass
class Waiting:
    """끝점을 못 찾아 건너뛴 선 한 줄 — 넣으려던 줄 그대로와 그 까닭."""

    row: dict[str, Any]
    why: str


def _edge_counts(raw: dict[str, int], waiting: int) -> dict[str, int]:
    """관계 적재의 셈 → 실행 기록의 셈. 건너뛴 줄은 `unchanged` 로 오므로 거기서 빼서
    `relations_waiting` 으로 따로 센다 — 「그대로」 와 「아직 못 넣음」 은 다른 말이다."""
    counts = {f"relations_{name}": value for name, value in raw.items()}
    if waiting:
        counts["relations_unchanged"] = counts.get("relations_unchanged", 0) - waiting
        counts["relations_waiting"] = waiting
    return counts


def _edge_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """선 한 줄의 정체 — 같은 선이 다시 오거나 끊겼다고 올 때 기다리던 줄과 맞춘다.

    **도착 타입까지 본다** — 끝이 인터페이스면 같은 식별자가 두 구현 타입에 있을 수 있다.
    도착 타입이 없는 줄(이 판 전에 기다리기 시작한 줄)은 빈 글자다 — `_edge_keys` 가 그것과도
    맞춘다."""
    return (
        compare_key(str(row.get("src") or "")),
        str(row.get("relation") or "").strip(),
        compare_key(str(row.get("dst") or "")),
        str(row.get("dst_type") or "").strip(),
    )


def _edge_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    """이번에 온 줄들의 정체 — **도착 타입을 뺀 것도** 함께 담는다. 도착 타입 없이 기다리던 옛
    줄이 같은 선의 새 줄(도착 타입이 있다)과 맞아야 두 번 실리지 않는다(실리면 「같은 관계가
    두 번」 으로 그 차례 전체가 막힌다)."""
    out: set[tuple[str, str, str, str]] = set()
    for one in rows:
        key = _edge_key(one)
        out.add(key)
        out.add((*key[:3], ""))
    return out


def _waiting_note(waiting: list[Waiting]) -> str:
    """사람에게 남기는 말 — 몇 줄이 왜 기다리는지(앞의 셋만)."""
    sample = " · ".join(
        f"{one.row.get('src')} -{one.row.get('relation')}-> {one.row.get('dst')}"
        f" ({one.why.removeprefix('끝점을 찾지 못해 건너뜁니다 — ')})"
        for one in waiting[:3]
    )
    more = f" 외 {len(waiting) - 3}줄" if len(waiting) > 3 else ""
    return (
        f"선 {len(waiting)}줄은 끝점이 아직 없어 기다립니다 — 다음 동기화에서 다시 넣습니다: "
        f"{sample}{more}"
    )


def _sync_relations(
    db: Session,
    user: User | None,
    source: DataSource,
    object_type: ObjectType,
    *,
    apply: bool,
) -> tuple[dict[str, int], list[str], bool]:
    """형제 설치의 **선**을 받는다 — `(셈, 오류 줄, 됐나)`.

    **객체를 넣은 뒤에 부른다** — 선은 양 끝이 있어야 선다. 끊긴 선(`deleted`)은 무덤과 같은
    규칙으로 끊는다(`bundles/tombstones.py` — 줄마다 그 객체를 고칠 수 있는지 다시 본다).
    규칙을 두 벌로 적지 않으려고 그 모듈을 그대로 쓴다.

    많으면(5만 줄) 객체 쪽처럼 **받은 만큼 넣고 끊은 자리를 적어** 다음 차례가 잇는다
    (`_advance`).

    ## 처음부터 다시 받기 — 끝까지 받은 차례에 정리한다

    상대가 `reset` 을 주면(그 시각부터는 끊긴 선을 알려 줄 수 없다 — 무덤의 보관 기간이
    지났다) 시계를 비우고 **전량을 다시 받는다.** 그 응답에는 끊긴 선이 없으므로 이쪽에서
    「안 온 선」 을 끊어야 둘이 맞는다. 시계를 손으로 비웠을 때도 같다 — 첫 쪽부터 전량을 받는
    일이다.

    전량이 한 번에 받는 상한(5만 줄)을 넘으면 여러 차례에 걸친다. 예전에는 그때 더하기로만 이어
    받고 정리를 못 해 「상대에서 끊긴 선이 이쪽에 남아 있을 수 있습니다」 만 적었다 — 끊긴 선이
    영영 남았다(2026-10-08). 이제는 **세대**(`FULL_EDGES` — 시작한 때)를 소스에 적어 두고:

    - 중간 차례마다 이번에 온 선에 「봤다」 를 적는다(`_touch_seen` — `datasource_seen_at`).
    - 마지막 쪽까지 받은 차례에 **이 소스가 이은 선** 가운데 그 세대에 한 번도 안 닿은 것을
      끊는다(`_prune_unseen`). 한 차례로 끝나면 표시 없이 그 차례에 온 것으로 가른다.
    - 어느 차례든 실패하거나 도중에 멈추면 끊지 않는다 — 그 차례의 표시도 함께 롤백되고 다음
      차례가 같은 자리에서 잇는다. 일부만 본 것으로 끊으면 아직 안 받은 선이 끊긴다.

    **사람이 이은 선은 안 끊는다** — 끊는 것은 이 소스가 이은 선
    (`ObjectRelation.datasource_id`)뿐이다.
    예전의 「파일대로 맞춤」 은 온 목록에 나온 (출발 객체 · 관계 종류) 안의 선을 누가 이었든
    끊었다.
    """
    fetch = partial(
        fetchers.fetch_sp_core_relations,
        base_url=source.base_url,
        type_slug=source.entity_set,
        auth=_auth(source),
        page_size=source.page_size,
        max_rows=odata.MAX_ROWS,
        transport=transport,
    )
    errors: list[str] = []
    mark = source.relations_since_mark
    resume = _resume_of(source, RESUME_EDGES, mark)
    since = str(resume.get("since") or "") if resume else mark
    # 끊은 자리에서 **전량 받기를 잇는** 차례면 그 세대 — 첫 쪽이 연 것이다.
    full = _full_of(source, mark) if resume is not None and not since else None
    if resume is not None:
        try:
            got = fetch(since=since, cursor=str(resume["next"]))
        except AppError as caught:
            errors.append(
                f"선은 지난번에 끊은 자리에서 잇지 못해 받은 자리부터 다시 받았습니다 — "
                f"{caught.message}"
            )
            resume = None
            full = None
            since = mark
            got = fetch(since=since)
    else:
        got = fetch(since=since)
    reset = got.reset
    if reset:
        why = got.reset_reason or "상대가 reset 을 보냈습니다"
        errors.append(f"선은 처음부터 다시 받았습니다 — {why}")
        # **소스의 시계는 여기서 안 비운다** — 계획(미리 보기)도 이 길을 지나고 그 실행도
        # 커밋한다. 비워 두면 상대는 빈 시계에는 reset 을 안 보내, 다음 적용이 「더하기」 로
        # 받아 정리가 영영 안 일어났다(2026-10-08). 적용에 성공하면 아래에서 새 시계로
        # 옮긴다 — 실패하면 옛 시계라 상대가 다시 reset 을 보낸다.
        resume = None
        since = ""
        got = fetch(since="")
    if resume is None and not since:
        # **첫 쪽부터 전량을 받는다**(reset · 시계를 손으로 비움 · 처음) — 세대를 연다.
        full = {"from": mark, "started": _db_now(db)}
        if reset and got.truncated:
            errors.append(
                "선이 많아 처음부터 다시 받기를 여러 차례에 나눠 받습니다 — 마지막 쪽까지 "
                "받은 차례에 상대에 없는 선(이 소스가 이은 것만)을 끊습니다."
            )

    rows = [_edge_row(one) for one in got.rows if not one.get(fetchers.CORE_DELETED)]
    gone = [one for one in got.rows if one.get(fetchers.CORE_DELETED)]
    # **기다리던 선을 다시 싣는다**(끝점이 지난번에 없었다). 이번에 같은 선이 다시 왔으면 새
    # 줄을 쓰고, 상대가 끊었다고 왔으면 버린다. 첫 쪽부터 전량을 다시 받는 세대의 첫 차례면
    # 기다리던 것도 그 안에 다시 오므로 버린다. 한 번에 넣는 상한을 넘기지 않게 남는 자리만큼만
    # — 나머지는 계속 기다린다.
    fresh = full is not None and resume is None
    waiting = [] if fresh else [dict(one) for one in source.relations_waiting or []]
    again = _edge_keys(rows) | _edge_keys(gone)
    retry = [one for one in waiting if _edge_key(one) not in again]
    room = max(odata.MAX_ROWS - len(rows), 0)
    retry, held = retry[:room], retry[room:]
    retry, dropped = _still_placeable(db, user, source, object_type, retry)
    if dropped:
        errors.append(_dropped_note(dropped))
    sent = rows + retry
    seen: set[uuid.UUID] = set()
    counts, more, still, edges_ok = _apply_edges(
        db,
        user,
        source,
        object_type,
        sent,
        gone,
        # **늘 더하기다** — 안 온 선의 정리는 세대가 끝난 차례에 이 소스가 이은 것만 한다
        # (`_prune_unseen`). 끝점을 못 찾은 줄은 나머지를 막지 않고 기다린다.
        mode="add",
        apply=apply,
        skip_missing=True,
        max_rows=max(len(sent), bulk.MAX_ROWS),
        seen=seen,
    )
    errors.extend(more)
    if still:
        errors.append(_waiting_note(still))
    ok = edges_ok and not any(
        name.endswith("_error") and value for name, value in counts.items()
    )
    # 끝까지 받았다 — 이때만 상대가 시계를 준다.
    complete = not got.truncated and bool(got.as_of)
    held_back = 0
    if ok and full is not None:
        started = _full_started(full)
        if complete:
            pruned = _prune_unseen(db, user, source, object_type, started, seen, apply=apply)
            held_back = pruned.held_back
            if pruned.cut:
                counts["relations_unlink"] = counts.get("relations_unlink", 0) + pruned.cut
            errors.extend(pruned.notes)
        elif apply:
            _touch_seen(db, source, started, seen)
    if apply and ok:
        source.relations_waiting = [one.row for one in still] + held
        if held:
            counts["relations_waiting"] = counts.get("relations_waiting", 0) + len(held)
        # 세대는 끝까지 받을 때까지 들고 간다 — 끝났거나 세대가 아니면 지운다.
        _set_resume(source, FULL_EDGES, full if full is not None and not complete else None)
        # **끝까지 받고 넣은 뒤에만** 시계를 옮긴다 — 중간에 옮기면 그 사이 선을 영영 안
        # 받는다. 끝점을 못 찾은 줄은 위에 남겼으니 시계를 막지 않는다. 끊겼으면 끊은 자리를.
        errors.extend(_advance(source, RESUME_EDGES, got, resume, since))
    elif not apply and got.truncated and got.next:
        errors.append(
            f"선이 많아 이번에는 {len(got.rows):,}줄까지 받았습니다 — 적용하면 받은 만큼 넣고 "
            "다음 동기화가 끊은 자리에서 잇습니다."
        )
    # 정리를 멈춘 적용은 실패로 적는다 — 「ok」 면 사람은 끊긴 선이 남은 줄 모른다.
    return counts, errors, ok and not (apply and held_back)


#: 처음부터 다시 받는 **세대** — `{from: 그때의 선 시계, started: 시작한 때(DB 시계)}`. 소스의
#: `options` 안에 둔다(`_` 로 시작하는 키라 화면에 안 나가고 정의를 고쳐도 지켜진다).
FULL_EDGES = "_full_relations"


def _full_of(source: DataSource, mark: str) -> dict[str, Any] | None:
    """지금 잇는 세대 — 그 세대를 연 때의 시계가 지금 시계일 때만(손으로 비웠으면 버린다)."""
    got = (source.options or {}).get(FULL_EDGES)
    if isinstance(got, dict) and got.get("from") == mark and got.get("started"):
        return got
    return None


def _db_now(db: Session) -> str:
    """**DB 의 시계**(이 트랜잭션이 시작한 때) — 선의 `updated_at` 과 같은 시계로 견준다. 이
    차례에 새로 잇거나 고친 선은 같은 트랜잭션이라 이 값과 같다(옛것이 아니다)."""
    now = db.scalar(select(func.now()))
    return (now or datetime.now(UTC)).isoformat()


def _full_started(full: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(full["started"]))


def _made_here(source: DataSource) -> ColumnElement[bool]:
    """**이 소스가 이은 선** — 처음 이을 때 주인으로 적힌다(`ObjectRelation.datasource_id`).

    사람이 화면에서 이은 선 · 파일로 넣은 선 · 다른 소스가 이은 선은 여기 안 든다 — 같은 선을
    이 소스가 나중에 받아도(「그대로」) 주인은 처음 이은 쪽이다. 주인 칸이 생기기 전(0.4.49
    까지)에 이 소스가 이은 선도 안 든다: 누가 이었는지 가를 기록이 없어, 끊어도 되는 선인지
    알 수 없다."""
    return ObjectRelation.datasource_id == source.id


def _touch_seen(
    db: Session, source: DataSource, started: datetime, seen: set[uuid.UUID]
) -> None:
    """이번 차례에 온 선 가운데 **이 소스가 이은 것**에 「이 세대에서 봤다」 를 적는다
    (`datasource_seen_at`). 이 세대에 새로 이은 것은 만든 때로 이미 가른다.

    `updated_at` 은 **그대로 둔다** — 처음 구현은 그것을 밀었는데, 그러면 안 바뀐 선 수만 줄이
    이 설치의 코어 창구로 다시 나가고 지표 증분이 「관계가 바뀌었다」 로 읽어 전량을 셌다
    (2026-10-08). ORM 의 갱신은 `onupdate` 를 따라 `updated_at` 도 밀므로 제 값으로 묶는다.

    한 차례에 5만 줄이면 갱신 문장 다섯(1만 줄씩) — 같은 차례에 그 5만 줄을 계획하고 넣는 일에
    견주면 작다."""
    for batch in id_batches(sorted(seen)):
        db.execute(
            update(ObjectRelation)
            .where(ObjectRelation.id.in_(batch), _made_here(source))
            .values(datasource_seen_at=func.now(), updated_at=ObjectRelation.updated_at)
            .execution_options(synchronize_session=False)
        )


@dataclass
class Pruned:
    """처음부터 다시 받기를 마친 차례의 정리 — 끊은(미리 보기면 끊을) 수 · 멈춘 수 · 말."""

    cut: int = 0
    held_back: int = 0
    notes: list[str] = field(default_factory=list)


def _prune_unseen(
    db: Session,
    user: User | None,
    source: DataSource,
    object_type: ObjectType,
    started: datetime,
    seen: set[uuid.UUID],
    *,
    apply: bool,
) -> Pruned:
    """처음부터 다시 받기를 **끝까지 마친 차례** — 이 소스가 이은 선 가운데 그 세대에 한 번도
    안 온 것을 끊는다.

    안 온 것 = 출발점이 이 소스의 타입이고(이 소스가 받는 창구가 그것이다), 세대를 시작하기
    전에 이었고, 그 세대의 앞 차례에서 본 적이 없고(`_touch_seen`), 이번 차례에도 안 온 것
    (`seen`). 끊는 길은 관계 파일의 맞춤과 같다 — 감사 기록을 남기고 행을
    지우면 무덤이 남아 이 설치를 받는 쪽에도 끊긴 선으로 간다(`_leave_tombstone`).

    **한꺼번에 절반 넘게 안 오면 끊지 않고 멈춘다**(`GONE_LIMIT` — 객체의 사용 중지와 같은
    무늬). 상대의 토큰이 부서를 잃거나 공개를 잠시 닫으면 전량이 거의 비어 오고, 그것을 믿으면
    이 소스가 이은 선이 한꺼번에 끊긴다. 시계는 옮긴다 — 안 옮기면 다음 차례마다 같은 전량을
    다시 받는다.
    """
    out = Pruned()
    src = aliased(ObjectInstance)
    mine = (
        select(ObjectRelation.id, ObjectRelation.created_at, ObjectRelation.datasource_seen_at)
        .join(src, src.id == ObjectRelation.src_object_id)
        .where(src.type_id == object_type.id, _made_here(source))
    )
    held = 0
    doomed: list[uuid.UUID] = []
    for edge_id, made, last_seen in db.execute(mine.execution_options(yield_per=10_000)):
        held += 1
        fresh = made >= started or (last_seen is not None and last_seen >= started)
        if not fresh and edge_id not in seen:
            doomed.append(edge_id)
    if len(doomed) > GONE_FLOOR and len(doomed) > GONE_LIMIT * held:
        out.held_back = len(doomed)
        out.notes.append(
            f"처음부터 다시 받았는데 이 소스가 이은 선 {held:,}줄 가운데 {len(doomed):,}줄이 "
            "오지 않았습니다 — 절반이 넘어 끊지 않았습니다(받은 선은 반영했습니다). 상대 "
            "토큰의 부서 · 공개 범위가 바뀌었는지 확인하세요. 정말 끊긴 것이면 화면에서 "
            "끊거나 관계 파일을 「파일대로 맞춤」 으로 넣으세요."
        )
        return out
    if not doomed:
        return out
    if not apply:
        out.cut = len(doomed)
        out.notes.append(
            f"처음부터 다시 받은 결과 상대에 없는 선 {len(doomed):,}줄을 적용할 때 끊습니다"
            "(이 소스가 이은 것만 — 사람이 이은 선은 그대로)."
        )
        return out

    actor = _actor(db, user)
    name = source_name(source)
    kinds = {one.slug: one for one in db.scalars(select(RelationType))}
    refused: dict[uuid.UUID | None, str] = {}
    blocked: list[str] = []
    dst = aliased(ObjectInstance)
    for batch in id_batches(doomed):
        records: list[tuple[uuid.UUID, str, uuid.UUID | None, dict[str, Any]]] = []
        for edge, owner, src_label, dst_label in db.execute(
            select(ObjectRelation, src.owner_workspace_id, src.label, dst.label)
            .join(src, src.id == ObjectRelation.src_object_id)
            .join(dst, dst.id == ObjectRelation.dst_object_id)
            .where(ObjectRelation.id.in_(batch))
        ).tuples():
            label = f"{src_label} -{edge.relation}-> {dst_label}"
            # **무덤과 같은 문턱** — 잠긴 관계 종류 · 고칠 수 없는 부서의 출발점은 안 끊는다.
            kind = kinds.get(edge.relation)
            owner_name = managed.owner_of(kind) if kind is not None else ""
            if owner_name and owner_name != name:
                blocked.append(f"{label}({owner_name} 가 관리하는 관계 종류)")
                continue
            if owner not in refused:
                try:
                    require_owner_edit(
                        db, actor, owner, what="객체", code_value=code("OBJECTS", 12)
                    )
                    refused[owner] = ""
                except AppError as denied:
                    refused[owner] = denied.message
            if refused[owner]:
                blocked.append(f"{label}({refused[owner]})")
                continue
            records.append(
                (
                    edge.id,
                    label,
                    owner,
                    {
                        **audit.relation_endpoints(edge, src_label, dst_label),
                        MARK: {"slug": source.slug, "why": "full_resync"},
                    },
                )
            )
            db.delete(edge)
        # 선마다 기록은 남기되(양끝 객체의 이력에 선다) **바깥에는 줄마다 알리지 않는다** —
        # 다시 받기 한 번이 수천 줄을 끊을 수 있고, 그 알림은 아무도 못 읽는다. 바깥에는 실행
        # 한 줄(`datasource.sync` 의 `relations_unlink`)이 간다.
        audit.record_rows(
            db,
            action="object.relation.remove",
            actor=actor,
            target_table="object_relations",
            rows=records,
            reason=f"{source.name} 에서 처음부터 다시 받았는데 오지 않아 끊음",
        )
        db.flush()  # 무덤(`_leave_tombstone`)도 여기서 남는다 — 덩어리마다 세션을 비운다
        out.cut += len(records)
    if out.cut:
        out.notes.append(
            f"처음부터 다시 받은 결과 상대에 없는 선 {out.cut:,}줄을 끊었습니다"
            "(이 소스가 이은 것만 — 사람이 이은 선은 그대로)."
        )
    if blocked:
        more = f" 외 {len(blocked) - 3}줄" if len(blocked) > 3 else ""
        out.notes.append(
            f"상대에 없는 선 {len(blocked):,}줄은 여기서 끊을 수 없어 남겼습니다: "
            f"{' · '.join(blocked[:3])}{more}"
        )
    return out


def _still_placeable(
    db: Session,
    user: User | None,
    source: DataSource,
    object_type: ObjectType,
    retry: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """기다리던 선 가운데 **이제는 넣을 수 없는 것**을 가려낸다 — `(다시 실을 것, 뺄 것과
    까닭)`.

    기다리던 줄이 그 사이 오류 줄이 되면(관계 종류가 지워짐 · 개수 제약 · 끝점이 둘로 갈림)
    그 한 줄이 계획 전체를 막는다. 그 줄은 적용에 성공해야만 목록에서 빠지므로, 예전에는
    **매번 같은 줄이 막아 그 소스의 선이 영영 안 섰다**(2026-10-08). 새로 온 줄은 그대로
    엄격하다 — 지금 바깥이 보낸 것이다. 끝점을 아직 못 찾은 것(`skipped`)은 계속 기다린다.
    """
    if not retry:
        return [], []
    probe = bulk.plan_relations(
        db,
        _actor(db, user),
        object_type,
        retry,
        source=source_name(source),
        skip_missing=True,
        max_rows=max(len(retry), bulk.MAX_ROWS),
    )
    if probe.errors:
        # 줄과 무관한 거절(모르는 열 — 그 사이 관계 속성이 지워졌다)은 어느 줄인지 못 가린다.
        why = " · ".join(probe.errors)
        return [], [(one, why) for one in retry]
    bad = {one.row: one.message for one in probe.rows if one.action == "error"}
    keep = [one for index, one in enumerate(retry, start=1) if index not in bad]
    dropped = [(one, bad[index]) for index, one in enumerate(retry, start=1) if index in bad]
    return keep, dropped


def _dropped_note(dropped: list[tuple[dict[str, Any], str]]) -> str:
    sample = " · ".join(
        f"{row.get('src')} -{row.get('relation')}-> {row.get('dst')} ({why})"
        for row, why in dropped[:3]
    )
    more = f" 외 {len(dropped) - 3}줄" if len(dropped) > 3 else ""
    return (
        f"기다리던 선 {len(dropped)}줄은 이제 넣을 수 없어 더 기다리지 않습니다: "
        f"{sample}{more}"
    )


def _counts(rows: list[bulk.RowPlan]) -> dict[str, int]:
    out = {"create": 0, "update": 0, "unchanged": 0, "error": 0}
    for one in rows:
        out[one.action] = out.get(one.action, 0) + 1
    return out


def source_name(source: DataSource) -> str:
    """이 소스가 적재할 때 **내보이는 출처 이름** — 비우면 slug.

    **한 자리에서만 나온다.** 세 다리(객체 · 선 · 무덤)가 각자 값을 고르면 한쪽만 통과하는
    상태가 생기고, 그때는 「선은 들어왔는데 객체가 안 들어온다」 를 물을 자리가 없다 — 선을
    끊는 다리만 slug 를 넘기고 나머지가 빈 값을 넘긴 것이 정확히 그 고장이었다.
    """
    return (source.source_name or source.slug).strip()


def _actor(db: Session, user: User | None) -> User:
    """타이머가 돌릴 때는 사람이 없다 — 시스템 관리자 하나를 대신 세운다(권한 판정용).
    감사 기록은 `actor=None` 으로 남겨 「타이머」 로 보이게 한다."""
    if user is not None:
        return user
    found = db.scalar(
        select(User)
        .where(User.is_system_admin.is_(True), User.deleted_at.is_(None))
        .order_by(User.created_at)
    )
    if found is None:
        raise AppError(
            code("DATASOURCES", 22),
            "동기화를 대신 돌릴 시스템 관리자 계정이 없습니다.",
            status=409,
        )
    return found


def _bury(
    db: Session,
    actor: User,
    object_type: ObjectType,
    source: DataSource,
    graves: list[dict[str, Any]],
    external_key: str,
) -> int:
    """상대에서 **사라진 것**을 이쪽에서 사용 중지로.

    지우지 않는 이유는 한 건씩 지울 때와 같다 — 이 객체를 가리키는 참조와 첨부가 밖에
    남아 있다. 「그만 쓴다」 는 상태로 두면 가리키던 화면이 빈 칸이 되지 않는다.

    합쳐져서 사라진 것(`merged_into`)은 **이긴 쪽을 기록에 적는다** — 나중에 「이건 왜
    중지됐지」 를 물으면 답이 있어야 한다.
    """
    if not graves:
        return 0
    kind = aliases.source_kind(source.slug)
    wanted = {compare_key(_text(one.get(external_key))) for one in graves}
    wanted.discard("")
    if not wanted:
        return 0
    rows = db.execute(
        select(ObjectInstance, ObjectAlias.norm)
        .join(ObjectAlias, ObjectAlias.object_id == ObjectInstance.id)
        .where(
            ObjectAlias.kind == kind,
            ObjectAlias.norm.in_(wanted),
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            ObjectInstance.status == "active",
        )
    )
    merged_of = {
        compare_key(_text(one.get(external_key))): _text(one.get("merged_into"))
        for one in graves
    }
    count = 0
    for row, norm in rows:
        row.status = "deprecated"
        count += 1
        winner = merged_of.get(norm) or ""
        audit.record(
            db,
            action="object.update",
            actor=actor,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes={"status": {"before": "active", "after": "deprecated"}},
            reason=(
                f"{source.name} 에서 「{winner}」 에 합쳐져 사용 중지로 표시"
                if winner
                else f"{source.name} 에서 지워져 사용 중지로 표시"
            ),
        )
    return count


#: 한 번에 이보다 많이(이 소스가 넣어 쓰는 것의 비율) 안 오면 사용 중지하지 않고 멈춘다 —
#: RA 보고서의 「원본에서 내려감」 과 같은 무늬(`ra_reports.GONE_LIMIT`). 바깥이 한 번 빈
#: 응답이나 일부만 주면(필터 · 권한이 바뀜, 쪽 넘김 설정이 틀림) 이 소스가 넣은 것 전부가 사용
#: 중지됐다 — 그리고 그것을 되돌리는 길은 다시 온 행이 아니었다(상태를 대응하지 않는 소스).
GONE_LIMIT = ra_reports.GONE_LIMIT
#: 그 비율을 보지 않는 작은 수 — 몇 건짜리 표에서 둘이 빠지는 것은 정상이다.
GONE_FLOOR = ra_reports.GONE_FLOOR


def _deprecate_missing(
    db: Session,
    actor: User,
    object_type: ObjectType,
    source: DataSource,
    seen: set[str],
    seen_ids: set[uuid.UUID],
) -> tuple[int, int]:
    """이 소스가 남긴 객체 중 이번에 안 온 것을 「사용 중지」 로 — `(중지한 수, 멈춘 수)`.
    사람이 만든 것은 안 건드린다.

    **객체로 센다.** 사람이 합친 객체는 이 소스의 외부 식별자를 둘 갖는다 — 하나만 와도 온
    것이다(예전에는 안 온 식별자 하나로 방금 고친 객체를 중지시켰다). 이번에 넣은 객체
    (`seen_ids`)도 온 것이다 — 외부 식별자가 바뀐 행은 옛 값으로는 안 맞는다.
    """
    kind = aliases.source_kind(source.slug)
    held: dict[uuid.UUID, ObjectInstance] = {}
    present: set[uuid.UUID] = set()
    for row, norm in db.execute(
        select(ObjectInstance, ObjectAlias.norm)
        .join(ObjectAlias, ObjectAlias.object_id == ObjectInstance.id)
        .where(
            ObjectAlias.kind == kind,
            ObjectInstance.type_id == object_type.id,
            ObjectInstance.deleted_at.is_(None),
            ObjectInstance.status == "active",
        )
    ).tuples():
        held[row.id] = row
        if norm in seen or row.id in seen_ids:
            present.add(row.id)
    gone = [row for object_id, row in held.items() if object_id not in present]
    if len(gone) > GONE_FLOOR and len(gone) > GONE_LIMIT * len(held):
        # **중지하지 않고 멈춘다** — 부르는 쪽이 실패로 적고 사람에게 알린다.
        return 0, len(gone)
    for row in gone:
        row.status = "deprecated"
        audit.record(
            db,
            action="object.update",
            actor=actor,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes={
                "status": {"before": "active", "after": "deprecated"},
                MARK: {"slug": source.slug, "why": "missing"},
            },
            reason=f"{source.name} 에서 사라져 사용 중지로 표시",
        )
    return len(gone), 0


def _revivable(
    db: Session, object_type: ObjectType, source: DataSource, mapped: list[Mapped]
) -> list[ObjectInstance]:
    """**이 소스가 「바깥에서 사라져」 중지시킨 것**이 다시 왔다 — 되살릴 객체들.

    상태 열을 대응하지 않는 소스(OData · REST · 파일 대부분)는 다시 온 행으로 상태를 못
    돌린다. 그래서 `deprecate_missing` 이 중지시킨 것이 바깥에 다시 올라와도 영영 중지로
    남았다(RA 는 「다시 올라옴」 을 따로 적는다 — `ra_reports.reconcile`). **사람이 중지한 것은
    그대로다** — 그 객체의 **마지막 상태 변경**이 이 소스의 「사라져 중지」 일 때만 되살린다.
    그 뒤에 사람이 상태를 만졌으면 그 사람의 결정이 이긴다. 행이 상태를 들고 오면 그 값이
    정한다(여기서는 안 본다).
    """
    ids = {
        uuid.UUID(str(one.row["id"]))
        for one in mapped
        if not one.error and one.row.get("id") and "status" not in one.row
    }
    stopped: list[ObjectInstance] = []
    for batch in id_batches(sorted(ids)):
        stopped.extend(
            db.scalars(
                select(ObjectInstance).where(
                    ObjectInstance.id.in_(batch),
                    ObjectInstance.status == "deprecated",
                    ObjectInstance.deleted_at.is_(None),
                )
            )
        )
    if not stopped:
        return []
    last: dict[uuid.UUID, tuple[dict[str, Any], str | None]] = {}
    for batch in id_batches([one.id for one in stopped]):
        for target_id, changes, reason in db.execute(
            select(AuditEntry.target_id, AuditEntry.changes, AuditEntry.reason)
            .where(
                AuditEntry.target_table == "objects",
                AuditEntry.target_id.in_(batch),
                AuditEntry.changes.op("?")("status"),
            )
            .order_by(AuditEntry.target_id, AuditEntry.seq.desc())
            .distinct(AuditEntry.target_id)
        ).tuples():
            if target_id is not None:
                last[target_id] = (dict(changes or {}), reason)
    return [one for one in stopped if _stopped_by(source, last.get(one.id))]


def _stopped_by(source: DataSource, entry: tuple[dict[str, Any], str | None] | None) -> bool:
    """그 상태 변경이 **이 소스의 「바깥에서 사라져 중지」** 인가."""
    if entry is None:
        return False
    changes, reason = entry
    status = changes.get("status")
    if not isinstance(status, dict) or status.get("after") != "deprecated":
        return False
    mark = changes.get(MARK)
    if isinstance(mark, dict):
        return mark.get("slug") == source.slug and mark.get("why") == "missing"
    # 표식을 박기 전(2026-10-08 전)의 기록 — 사유 글로 가린다.
    return reason == f"{source.name} 에서 사라져 사용 중지로 표시"


def _revive(
    db: Session,
    actor: User,
    object_type: ObjectType,
    source: DataSource,
    objects: list[ObjectInstance],
) -> int:
    """되살린다 — 「다시 올라옴」 을 기록에 남긴다(왜 다시 쓰게 됐는지 이력이 말한다)."""
    count = 0
    for row in objects:
        if row.status != "deprecated":  # 그 사이 바뀌었다
            continue
        row.status = "active"
        count += 1
        audit.record(
            db,
            action="object.update",
            actor=actor,
            target_table="objects",
            target_id=row.id,
            target_label=f"{object_type.slug}:{row.label}",
            workspace_id=row.owner_workspace_id,
            changes={
                "status": {"before": "deprecated", "after": "active"},
                MARK: {"slug": source.slug, "why": "returned"},
            },
            reason=f"{source.name} 에 다시 나타나 사용으로 되돌림",
        )
    return count


def suggest_core_mapping(
    db: Session, source: DataSource, object_type: ObjectType
) -> CoreSuggestOut:
    """상대의 카탈로그 → **칸 대응 초안.**

    잇는 규칙은 둘뿐이다: 키가 같으면 잇고, 아니면 이름(label)이 같은 칸에 잇는다.
    **짐작은 여기까지다** — 「비슷해 보이는 이름」 까지 이으면 틀린 값이 조용히 들어가고,
    그 값은 사람 눈에 맞는 값처럼 보여서 아무도 안 고친다. 못 이은 것은 까닭을 적는다.
    """
    body = fetchers.core_catalog(
        base_url=source.base_url, auth=_auth(source), transport=transport
    )
    wanted = source.entity_set.strip()
    remote = next(
        (
            one
            for one in body.get("types") or []
            if isinstance(one, dict) and one.get("slug") == wanted
        ),
        None,
    )
    if remote is None:
        opened = ", ".join(
            str(one.get("slug")) for one in body.get("types") or [] if isinstance(one, dict)
        )
        raise AppError(
            code("DATASOURCES", 43),
            f"그 설치가 연 코어 타입이 아닙니다: {wanted}. 열려 있는 것: {opened or '없음'}",
            status=422,
        )

    defs = [one for one in properties_of(db, object_type.id) if one.data_type != "file"]
    by_key = {one.key: one for one in defs}
    by_label = {one.label.strip(): one for one in defs}
    columns: list[dict[str, Any]] = [{"source": "label", "target": "label"}]
    shown: list[CoreSuggestProperty] = []
    notes: list[str] = []
    for one in remote.get("properties") or []:
        if not isinstance(one, dict):
            continue
        key = str(one.get("key") or "")
        label = str(one.get("label") or "")
        kind = str(one.get("data_type") or "")
        found = by_key.get(key) or by_label.get(label.strip())
        note = ""
        if found is None:
            note = "이쪽에 같은 이름의 칸이 없습니다 — 속성을 만들거나 그대로 두세요."
            notes.append(f"{label or key}({kind}): 이쪽에 없는 칸")
        elif found.data_type != kind:
            # **자료형이 다르면 잇지 않는다.** 숫자 칸에 글자가 들어가면 정렬과 집계가
            # 조용히 틀린다.
            notes.append(
                f"{label or key}: 자료형이 다릅니다(상대 {kind} · 이쪽 {found.data_type})"
            )
            note = "자료형이 달라 잇지 않았습니다."
            found = None
        else:
            columns.append({"source": key, "target": f"properties.{found.key}"})
        shown.append(
            CoreSuggestProperty(
                key=key,
                label=label,
                data_type=kind,
                target=f"properties.{found.key}" if found else None,
                note=note,
            )
        )

    return CoreSuggestOut(
        system=str(body.get("system") or ""),
        revision=str(body.get("revision") or ""),
        type_slug=wanted,
        type_label=str(remote.get("label") or wanted),
        count=int(remote.get("count") or 0),
        # **식별자는 상대의 `key` 다.** 그것이 다음 동기화가 같은 객체를 다시 찾는 근거다.
        mapping={"external_key": "key", "columns": columns},
        properties=shown,
        notes=notes,
    )


#: 타이머가 깨는 간격(`deploy/sync.timer.template` 의 `OnUnitActiveSec`) — 그만큼은 봐준다.
#: `last_run_at` 은 실행이 **끝난** 때라, 여유 없이 「끝난 때 + 간격」 을 견주면 차례가 늘
#: 다음 깸으로 밀린다 — 5분 소스가 실제로는 10분마다 돌았다(2026-10-08). 지표의 `DUE_GRACE`
#: 와 같은 무늬다.
DUE_GRACE = timedelta(minutes=5)


def due(db: Session, now: datetime | None = None) -> list[DataSource]:
    """타이머가 돌릴 차례인 소스 — 간격이 0 이 아니고 마지막 실행에서 그만큼(여유를 빼고)
    지난 것."""
    now = now or datetime.now(UTC)
    out: list[DataSource] = []
    for source in db.scalars(
        select(DataSource).where(
            DataSource.is_active.is_(True), DataSource.interval_minutes > 0
        )
    ):
        every = timedelta(minutes=source.interval_minutes)
        if source.last_run_at is None or source.last_run_at + every - DUE_GRACE <= now:
            out.append(source)
    return out


def pending_job(db: Session, source: DataSource, *, apply: bool | None = None) -> Job | None:
    """이 소스를 돌릴 작업이 **줄에 있거나 돌고 있나** — 따로 넣은 것과 차례(round)에 든
    것 모두.

    `apply` 를 주면 그 뜻의 작업만 본다. 돌고 있는 차례는 이 소스를 이미 지났으면(그 차례가
    시작한 뒤에 이 소스가 돌았다) 셈하지 않는다.
    """
    for row in db.scalars(
        select(Job)
        .where(
            Job.kind.in_(("datasource_sync", "datasource_sync_round")),
            Job.status.in_(("queued", "running")),
        )
        .order_by(Job.created_at)
    ):
        params = row.params or {}
        if apply is not None and bool(params.get("apply")) != apply:
            continue
        if row.kind == "datasource_sync":
            if params.get("slug") == source.slug:
                return row
            continue
        if source.slug not in (params.get("slugs") or []):
            continue
        passed = (
            row.status == "running"
            and row.started_at is not None
            and source.last_run_at is not None
            and source.last_run_at >= row.started_at
        )
        if not passed:
            return row
    return None


def edges_only(source: DataSource) -> bool:
    """**선만 받는 소스**(한 행이 선 하나 — BOM · 매핑 표). 객체를 만들지 않는다."""
    return bool((source.mapping or {}).get("relations"))


def sync_order(db: Session, sources: list[DataSource]) -> list[DataSource]:
    """**가리키는 쪽을 먼저** — 참조 칸 · 관계가 가리키는 타입의 소스가 앞에 선다.

    타입마다 소스가 따로이고 타이머가 한꺼번에 넣으면 순서가 없다 — 「고장 모드」 가
    「메커니즘」 보다 먼저 돌면 그 선이 끝점을 못 찾고, 그 고장 모드가 실패하면 그것을
    가리키는 「시험군」 의 선도 잇달아 실패했다(실측: 서른다섯 종류가 거의 같은 시각에 돌았다).

    무엇이 무엇을 가리키나는 정의에서 읽는다 — 참조 칸의 `ref_type_slug`, 그리고 선을 받는
    소스면 그 타입에서 나가는 관계 종류의 도착 타입(`dst_type_slugs`, 비어 있으면 모른다).
    선만 받는 소스는 출발점 타입의 객체 소스 뒤에 선다. **서로 가리키면**(순환) 이름 순으로
    끊는다 — 끝점을 못 찾은 선은 기다렸다가 다음 동기화에서 선다(`relations_waiting`).
    """
    ordered = sorted(sources, key=lambda one: one.slug)
    if len(ordered) < 2:
        return ordered
    type_ids = {one.type_id for one in ordered}
    slug_of = dict(
        db.execute(select(ObjectType.id, ObjectType.slug).where(ObjectType.id.in_(type_ids)))
        .tuples()
        .all()
    )
    refs: dict[uuid.UUID, set[str]] = {}
    for type_id, target in db.execute(
        select(PropertyDef.owner_id, PropertyDef.ref_type_slug).where(
            PropertyDef.owner_kind == "type",
            PropertyDef.owner_id.in_(type_ids),
            PropertyDef.ref_type_slug.is_not(None),
        )
    ):
        refs.setdefault(type_id, set()).add(str(target))
    kinds = list(db.scalars(select(RelationType).where(RelationType.is_active.is_(True))))
    # 끝 · 참조 대상에 적힌 **인터페이스는 구현 타입으로 편다** — 인터페이스 slug 는 어느
    # 소스의 타입도 아니라, 안 펴면 그 선 · 참조가 가리키는 소스를 못 찾아 순서 없이 돌았다
    # (그러면 끝점이 아직 없는 선이 한 차례씩 기다린다).
    ends = load_ends(db)

    def points_at(source: DataSource) -> set[str]:
        own = slug_of.get(source.type_id, "")
        out = set(ends.types_of(sorted(refs.get(source.type_id, set()))))
        if wants_relations(source) or edges_only(source):
            for kind in kinds:
                if kind.src_type_slugs is None or ends.allows(kind.src_type_slugs, own):
                    out |= set(ends.types_of(kind.dst_type_slugs or []))
        if edges_only(source):
            out.add(own)  # 출발점도 객체 소스가 먼저 만든다
        return out

    # slug 로 가른다 — 소스마다 유일하다.
    after = {
        one.slug: {
            other.slug
            for other in ordered
            if other.slug != one.slug
            and not edges_only(other)
            and slug_of.get(other.type_id) in points_at(one)
        }
        for one in ordered
    }
    out: list[DataSource] = []
    left = list(ordered)
    while left:
        waiting_on = {one.slug for one in left}
        ready = [one for one in left if not after[one.slug] & waiting_on]
        pick = ready[0] if ready else left[0]
        out.append(pick)
        left.remove(pick)
    return out


def _move_sources(db: Session, source_id: uuid.UUID, target_id: uuid.UUID) -> int:
    done = db.execute(
        update(DataSource)
        .where(DataSource.workspace_id == source_id)
        .values(workspace_id=target_id)
    )
    return extensions.rows_changed(done)


def workspace_content(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceContent]:
    """부서 통폐합 때 옮길 데이터 소스. **새로 들어오는 객체의 소유 부서가 이것으로
    정해진다** — 안 옮기면 통폐합 뒤에도 동기화가 없어진 부서로 계속 넣는다."""
    count = (
        db.scalar(
            select(func.count())
            .select_from(DataSource)
            .where(DataSource.workspace_id == workspace_id)
        )
        or 0
    )
    return [
        extensions.WorkspaceContent(
            kind="datasources", label="데이터 소스", count=int(count), move=_move_sources
        )
    ]


# --- 홈 「남은 일」 ------------------------------------------------------------

#: 간격의 이 배를 넘도록 안 돌았으면 「멎었다」 로 본다. 딱 한 배로 보면 5분 간격에서
#: 타이머가 한 번만 늦어도 경고가 뜨고, 그런 경고는 곧 무시된다.
STALE_FACTOR = 3


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """**조용히 멎은 것을 홈이 말한다.**

    동기화가 실패하면 지금까지는 `last_status` 에 failed 만 적혔다. 소스 화면을 열어
    보는 사람만 그것을 안다 — 그리고 잘 도는 동안에는 아무도 그 화면을 안 연다.
    그것이 정기 작업의 기본 실패 방식이고, 그 사실은 데이터가 몇 주 낡은 뒤에야
    드러난다.

    시스템 관리자에게만. 데이터 소스 화면을 그들만 열 수 있어서, 다른 사람에게 띄우면
    그 줄은 못 지우는 숫자가 된다.
    """
    if not viewer.is_system_admin:
        return []
    failed = list(
        db.scalars(
            select(DataSource).where(
                DataSource.is_active.is_(True), DataSource.last_status == "failed"
            )
        )
    )
    now = datetime.now(UTC)
    # **한 번도 안 돈 것도 센다** — 만든 때부터 잰다. 예전에는 `last_run_at` 이 없으면 빼서,
    # 타이머나 워커가 처음부터 없던 설치의 소스는 영영 「멎음」 에 안 걸렸다(2026-10-08).
    stale = [
        one
        for one in db.scalars(
            select(DataSource).where(
                DataSource.is_active.is_(True), DataSource.interval_minutes > 0
            )
        )
        if one.last_status != "failed"
        and (one.last_run_at or one.created_at)
        + timedelta(minutes=one.interval_minutes * STALE_FACTOR)
        < now
    ]
    return [
        extensions.MaintenanceItem(
            key="datasource_failed",
            label="동기화가 실패한 데이터 소스",
            count=len(failed),
            link="/admin/datasources",
            # **경고다.** 여기가 실패하면 화면의 값이 조용히 낡아 가고, 그 사실은
            # 값 자체로는 드러나지 않는다 — 틀린 값이 아니라 옛 값이기 때문이다.
            severity="warning",
        ),
        extensions.MaintenanceItem(
            key="datasource_stale",
            label="정해진 간격보다 오래 안 돈 데이터 소스",
            count=len(stale),
            link="/admin/datasources",
            severity="warning",
        ),
    ]


def stats(db: Session) -> list[extensions.StatItem]:
    total = db.scalar(select(func.count()).select_from(DataSource)) or 0
    return [extensions.StatItem(label="데이터 소스", count=int(total))]


def sync_out(result: SyncResult) -> SyncOut:
    """결과를 API 모양으로 — 라우터와 워커가 같은 것을 돌려준다."""
    return SyncOut(
        run=RunOut.model_validate(result.run),
        applied=result.run.applied,
        counts=dict(result.run.counts or {}),
        rows=[
            ImportRowOut(
                row=one.row,
                action=one.action,
                label=one.label,
                key=one.key,
                object_id=one.object_id,
                changes=one.changes,
                message=one.message,
            )
            for one in result.plan_rows
        ],
        errors=list(result.run.errors or []),
        truncated=result.truncated,
    )


#: 자기소개에 쓰는 종류의 이름 — 화면의 이름과 같게(`DataSourcesPage` 의 KIND_LABEL).
PROFILE_KIND_LABEL = {
    "odata": "OData",
    "rest": "REST",
    "file": "파일",
    "sp_core": "형제 코어",
    "ra_reports": "RA 보고서",
}


def profile_facts(db: Session) -> list[extensions.ProfileFact]:
    """자기소개의 「들어오는 곳」(ADR 0019) — 어느 바깥에서 무엇이 들어오나. 주소 · 인증은
    싣지 않는다(에이전트 안내문에 그대로 선다). 소스가 생기고 없어지면 사람이 쓴 소개가 낡은
    것이다."""
    rows = db.execute(
        select(DataSource, ObjectType.label)
        .join(ObjectType, ObjectType.id == DataSource.type_id)
        .where(DataSource.is_active.is_(True))
        .order_by(DataSource.name)
    ).all()
    lines: list[str] = []
    marks: dict[str, str] = {}
    for source, type_label in rows:
        kind = PROFILE_KIND_LABEL.get(source.kind, source.kind)
        board = (source.options or {}).get("board") if source.kind == ra_reports.KIND else None
        where = f", 조직 {board}" if board else ""
        lines.append(f"{source.name}({kind}{where}) → {type_label}")
        marks[f"source:{source.slug}"] = f"데이터 소스 「{source.name}」"
    if not lines:
        return []
    return [
        extensions.ProfileFact(
            key="datasources", label="들어오는 곳", lines=[" · ".join(lines)], marks=marks
        )
    ]
