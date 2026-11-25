"""속성 키 · 타입 slug 바꾸기(ADR 0093) — **닿는 곳을 세어 보이고 한 번에.**

AI 가 `w` 로 만든 무게 칸, `part2` 로 만든 타입은 보이는 이름(label)만 바뀔 뿐 키 · slug 는
영영 그대로였다 — 화면 주소 · MCP 인자 · 내보내기 · SPARQL 에 남는다. 키 · slug 는 정의 하나가
아니라 **수십 곳에 글자로** 적힌다: 객체 값(JSONB 키), 타입 화면 설정, 저장된 뷰 · 지표 · 장표
틀의 주소(`ref.vendor.country` · `in.part:vendor`), 데이터 소스 칸 대응, 뜻으로 찾기 대상,
그래프 지도, 웹훅, 검토함의 기억 …. 하나라도 빠지면 그 자리가 조용히 깨진다.

## 세 갈래

- **바꾼다** — 이 설치의 DB 에 구조로 적힌 자리(`Plan.places`). 계획이 자리마다 건수를 말한다.
- **알린다** — 바꿀 수 없는 바깥 자리(`Plan.warnings`): `.env` 의 칸 목록, 정제 키트 · 원천
  레시피, 공개 타입의 수신 시스템 코드, 문서 본문의 링크, 옛 스냅샷.
- **막는다** — 바꾸면 깨지는데 고칠 길이 없는 것(`Plan.errors`): 문서 · 파일 속성(첨부 ·
  판 표가 키를 쥔다), 공통 속성(인터페이스에서), 원천이 잠근 정의, 원 표 타입, 확장이 코드에
  박은 타입, RA 보고서의 틀 칸, 그 타입을 쥔 작업이 도는 중.

## 객체 값 — 이력은 그대로, 계보는 낡지 않게

객체 값은 SQL 로 키만 옮긴다(`updated_at` 을 안 건드린다 — 값이 바뀐 것이 아니다). 개정 칸
(`revision_key`)이면 「새 키로 복사 → 개정 칸 이름 변경 → 옛 키 삭제」 차례라 계보 선이 낡지
않는다(`objects/pinning.py` 의 트리거가 앞뒤 값을 견준다). 감사 기록은 고치지 않는다 — 이력
(`objects/history.py`)이 이 변경의 감사 기록(`ontology.property.rename`)을 보고 그 전
기록의 키를 새 키로 읽는다. 값이 있는 객체가 많으면 작업(`ontology_rename`)으로 — 종류
변경과 같은 문턱.
"""

from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.datasources import ra_reports
from app.modules.datasources.models import DataSource
from app.modules.graph.models import MapCounted, MapHubKey, MapLayout
from app.modules.jobs.models import Job
from app.modules.metrics.models import MetricAlert, MetricDef
from app.modules.notifications.models import Notification
from app.modules.objects import system
from app.modules.objects.models import (
    ObjectDocument,
    ObjectInstance,
    ObjectLink,
    SavedView,
    SearchMiss,
)
from app.modules.ontology import interfaces
from app.modules.ontology.models import (
    DETACHED_KINDS,
    ObjectInterface,
    ObjectType,
    PropertyDef,
    RelationType,
)
from app.modules.review.models import ReviewDecision
from app.modules.semantic.models import SemanticTarget
from app.modules.slides.models import SlideTemplate
from app.modules.webhooks.models import Webhook
from app.shared import extensions as extension_points

#: 값이 있는 객체가 이만큼 넘으면 요청 안에서 안 바꾸고 작업으로 — 종류
# 변경(`RETYPE_INLINE`)과 같다.
INLINE_OBJECTS = 20_000
#: 이보다 큰 타입은 **계획도** 작업으로 — 값 · 대응을 세느라 객체를 훑는다. 2,000만 건에서 키
#: 변경 계획이 처음 54초(이후 7초), 옮기기 계획은 200만 건에 5초라(2026-11-25) 요청 안에서는
#: 앞단의 시간 제한에 걸린다.
PLAN_INLINE_OBJECTS = 1_000_000
#: 작업이 한 번에 옮기는 객체 수.
CHUNK = 5_000
#: 그 타입을 쥐고 있으면 이름을 못 바꾸는 작업 상태.
ACTIVE = ("queued", "running")
HOP_KINDS = ("ref", "out", "in")
PROP = "properties."

Progress = Callable[[str, int, int], None] | None


@dataclass
class Rename:
    """무엇을 무엇으로 — `kind` 가 `property` 면 `type_slug` 타입의 키 `old` → `new`,
    `type` 이면 slug `old` → `new`."""

    kind: str
    type_slug: str
    old: str
    new: str

    def key(self, owner: str | None, key: str) -> str:
        if self.kind == "property" and owner == self.type_slug and key == self.old:
            return self.new
        return key

    def slug(self, slug: str) -> str:
        return self.new if self.kind == "type" and slug == self.old else slug


@dataclass
class Place:
    what: str
    count: int


@dataclass
class Plan:
    kind: str
    slug: str
    key: str | None
    to: str
    label: str
    objects: int = 0
    """값을 옮길 객체(속성) — 타입은 객체가 id 로 타입을 가리켜 0 이다."""
    places: list[Place] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    core_consumers: list[str] = field(default_factory=list)
    edits: list[Callable[[], None]] = field(default_factory=list, repr=False)
    """DB 에 적힌 자리를 바꾸는 일 — 정의 · 설정 · 주소. 객체 값은 따로(`move_values`)."""

    def place(self, what: str, count: int) -> None:
        if count:
            self.places.append(Place(what, count))


# --- 주소 ------------------------------------------------------------------------


class Walker:
    """주소(`ref.vendor.country` · `in.part:vendor` · `properties.w` · `w@1`)를 걸음마다
    그 칸의 주인 타입을 따라 읽고, 바꿀 키 · slug 만 갈아 끼운다.

    걸음의 상대가 하나로 정해지지 않으면(끝이 여럿인 관계) 거기서 멈춘다 — 그 뒤는 짐작해서
    바꾸지 않는다. 남은 것은 바꾼 뒤 흉내 내 보기(`retype.breaking`)가 「깨진다」 로 말한다."""

    def __init__(self, db: Session, rename: Rename) -> None:
        self.rename = rename
        self.ends = interfaces.load_ends(db)
        self.types = {one.id: one.slug for one in db.scalars(select(ObjectType))}
        self.props: dict[str, dict[str, PropertyDef]] = {}
        for row in db.scalars(select(PropertyDef).where(PropertyDef.owner_kind == "type")):
            owner = self.types.get(row.owner_id)
            if owner is not None:
                self.props.setdefault(owner, {})[row.key] = row
        self.relations = {one.slug: one for one in db.scalars(select(RelationType))}

    def _single(self, slugs: Iterable[str] | None) -> str | None:
        found = self.ends.expand(list(slugs or []))
        return next(iter(found)) if found is not None and len(found) == 1 else None

    def _next(self, owner: str, kind: str, name: str) -> str | None:
        if kind == "ref":
            prop = self.props.get(owner, {}).get(name)
            return self._single([prop.ref_type_slug]) if prop and prop.ref_type_slug else None
        if kind == "in" and ":" in name:
            return name.partition(":")[0]
        relation = self.relations.get(name)
        if relation is None:
            return None
        if not relation.directed:
            ends = set(relation.src_type_slugs or []) | set(relation.dst_type_slugs or [])
            return self._single(ends)
        return self._single(
            relation.dst_type_slugs if kind == "out" else relation.src_type_slugs
        )

    def address(self, value: Any, root: str | None) -> Any:
        """주소 하나 — 글자가 아니거나 뿌리 타입을 모르면 그대로."""
        if not isinstance(value, str) or not value or root is None:
            return value
        head, tilde, edge = value.partition("~")
        base, at, level = head.partition("@")
        if base.startswith(PROP):
            base = PROP + self.rename.key(root, base[len(PROP) :])
        else:
            tokens = base.split(".")
            owner: str | None = root
            index = 0
            while index + 1 < len(tokens) and tokens[index] in HOP_KINDS and owner:
                kind, name = tokens[index], tokens[index + 1]
                following = self._next(owner, kind, name)
                if kind == "ref":
                    tokens[index + 1] = self.rename.key(owner, name)
                elif kind == "in" and ":" in name:
                    other, _, key = name.partition(":")
                    tokens[index + 1] = (
                        f"{self.rename.slug(other)}:{self.rename.key(other, key)}"
                    )
                owner = following
                index += 2
            if owner and index == len(tokens) - 1:
                tokens[index] = self.rename.key(owner, tokens[index])
            base = ".".join(tokens)
        return base + (at + level if at else "") + (tilde + edge if tilde else "")

    def tree_relation(self, value: Any) -> Any:
        """`ref:<타입>.<키>` — 참조 칸으로 세운 트리."""
        if not isinstance(value, str) or not value.startswith("ref:"):
            return value
        owner, _, key = value[len("ref:") :].partition(".")
        return f"ref:{self.rename.slug(owner)}.{self.rename.key(owner, key)}"


def _swap(items: Any, old: str, new: str) -> Any:
    return [new if one == old else one for one in items] if isinstance(items, list) else items


def list_view(spec: dict[str, Any] | None, rename: Rename, walker: Walker, owner: str) -> Any:
    """타입 목록 화면 설정 — 자기 칸(`properties.<키>`) · 트리 · 롤업. 바뀐 것이 없으면
    그대로."""
    if not isinstance(spec, dict):
        return spec
    out = copy.deepcopy(spec)
    if rename.kind == "property" and owner == rename.type_slug:
        old, new = PROP + rename.old, PROP + rename.new
        for name in ("columns", "search", "filters"):
            if name in out:
                out[name] = _swap(out[name], old, new)
        sort = out.get("sort")
        if isinstance(sort, dict) and sort.get("field") == old:
            out["sort"] = {**sort, "field": new}
        rollups = out.get("rollups")
        if isinstance(rollups, list):
            out["rollups"] = [
                {**one, "property": rename.new}
                if isinstance(one, dict) and one.get("property") == rename.old
                else one
                for one in rollups
            ]
    tree = out.get("tree")
    if isinstance(tree, dict):
        tree = dict(tree)
        if rename.kind == "property" and owner == rename.type_slug:
            old, new = PROP + rename.old, PROP + rename.new
            if isinstance(tree.get("sort"), list):
                tree["sort"] = _swap(tree["sort"], old, new)
            if tree.get("prefix") == old:
                tree["prefix"] = new
        if "relation" in tree:
            tree["relation"] = walker.tree_relation(tree["relation"])
        out["tree"] = tree
    return out


def _view_specs(view: SavedView, walker: Walker, root: str | None) -> tuple[Any, Any]:
    query = copy.deepcopy(view.query or {})
    for one in query.get("conditions") or []:
        if isinstance(one, dict) and "field" in one:
            one["field"] = walker.address(one["field"], root)
    sort = query.get("sort")
    if isinstance(sort, dict) and "field" in sort:
        sort["field"] = walker.address(sort["field"], root)
    summary = copy.deepcopy(view.summary or {})
    for name in ("group_by", "split_by", "metric_field"):
        if name in summary:
            summary[name] = walker.address(summary[name], root)
    return query, summary


def _metric_spec(spec: dict[str, Any], walker: Walker, root: str | None) -> dict[str, Any]:
    out = copy.deepcopy(spec or {})
    for name in ("time", "cohort"):
        one = out.get(name)
        if isinstance(one, dict) and "address" in one:
            one["address"] = walker.address(one["address"], root)
    for dimension in out.get("dimensions") or []:
        if not isinstance(dimension, dict):
            continue
        if "address" in dimension:
            dimension["address"] = walker.address(dimension["address"], root)
        for rule in dimension.get("rules") or []:
            for cond in (rule or {}).get("when") or []:
                if isinstance(cond, dict) and "field" in cond:
                    cond["field"] = walker.address(cond["field"], root)
    for name in ("filters", "share_when"):
        for cond in out.get(name) or []:
            if isinstance(cond, dict) and "field" in cond:
                cond["field"] = walker.address(cond["field"], root)
    if out.get("measure_field"):
        out["measure_field"] = walker.address(out["measure_field"], root)
    visits = out.get("visits")
    if isinstance(visits, dict) and visits.get("key"):
        visits["key"] = walker.address(visits["key"], root)
    stay = out.get("stay")
    if isinstance(stay, dict) and stay.get("periods_from"):
        stay["periods_from"] = walker.address(stay["periods_from"], root)
    return out


def _areas(areas: list[Any], rename: Rename, walker: Walker) -> list[Any]:
    out = copy.deepcopy(areas or [])
    for area in out:
        if not isinstance(area, dict):
            continue
        root = area.get("type_slug")
        if rename.kind == "property" and root == rename.type_slug:
            for name in ("fields", "ref_line"):
                if isinstance(area.get(name), list):
                    area[name] = _swap(area[name], rename.old, rename.new)
        for name in ("subject_field", "follow_field"):
            if area.get(name):
                area[name] = walker.address(area[name], root)
        for cond in area.get("conditions") or []:
            if isinstance(cond, dict) and "field" in cond:
                cond["field"] = walker.address(cond["field"], root)
        if isinstance(root, str):
            area["type_slug"] = rename.slug(root)
    return out


# --- 계획 ------------------------------------------------------------------------


def _active_jobs(db: Session, slug: str, skip: uuid.UUID | None) -> list[str]:
    """그 타입을 인자로 쥐고 도는 작업 — 끝난 뒤 이름이 바뀌면 그 작업이 엉뚱한 곳을 본다.
    `skip` 은 지금 이 일을 하는 작업 자신이다."""
    query = select(Job).where(Job.status.in_(ACTIVE))
    if skip is not None:
        query = query.where(Job.id != skip)
    rows = db.scalars(query)
    found: list[str] = []
    for job in rows:
        params = job.params or {}
        if slug in (params.get("slug"), params.get("type_slug"), params.get("type")):
            found.append(f"{job.kind}({job.status})")
    return found


def _env_mentions(prefix: str) -> list[str]:
    settings = get_settings()
    out: list[str] = []
    for name in ("date_index_fields", "graph_calc_whole_relations"):
        raw = str(getattr(settings, name, "") or "")
        hits = [one.strip() for one in raw.split(",") if one.strip().startswith(prefix)]
        if hits:
            out.append(f"{name.upper()}({', '.join(hits)})")
    return out


def _common_refusals(
    db: Session, owner: ObjectType, errors: list[str], skip_job: uuid.UUID | None = None
) -> None:
    """이름 바꾸기 · 타입 옮기기가 함께 막는 것 — 원천 잠금 · 원 표 · 확장 · 도는 작업."""
    if owner.managed_by:
        errors.append(
            f"{owner.label}은(는) {owner.managed_by} 가 관리하는 정의라 여기서 이름을 바꾸지 "
            f"않습니다 — 원천이 다시 보내면 옛 이름으로 되살아납니다. {owner.managed_by} 에서 "
            "바꾸거나, 원천 연동을 해제했으면 「잠금 해제」 한 뒤 바꾸십시오."
        )
    if system.is_system(owner):
        errors.append(f"{owner.label}은(는) 원 표를 비추는 타입이라 이름을 바꾸지 않습니다.")
    guarded = extension_points.guarded_types(db).get(owner.slug)
    if guarded:
        errors.append(f"{owner.label}: {guarded}")
    jobs = _active_jobs(db, owner.slug, skip_job)
    if jobs:
        errors.append(
            f"이 타입을 쥔 작업이 도는 중입니다 — {', '.join(jobs)}. 끝난 뒤에 바꾸십시오."
        )


def _core(owner: ObjectType, consumers: list[str], out: Plan, what: str) -> None:
    if not owner.core:
        return
    out.core_consumers = consumers
    out.warnings.append(
        f"{owner.label}은(는) 외부에 공개 중입니다 — {what}은(는) 수신 시스템의 코드 · 주소에 "
        "박혀 있어 그쪽도 함께 바꿔야 합니다. 통보를 확인하고 accept_core 로 적용하십시오."
    )


def _common_places(db: Session, rename: Rename, walker: Walker, out: Plan) -> None:
    """뷰 · 지표 · 장표 틀 · 다른 타입의 화면 설정 — 속성과 타입이 함께 쓴다."""
    types = {one.id: one for one in db.scalars(select(ObjectType))}

    changed_types = 0
    for one in types.values():
        new_spec = list_view(one.list_view, rename, walker, one.slug)
        if new_spec != one.list_view:
            changed_types += 1
            out.edits.append(_setter(one, "list_view", new_spec))
    out.place("타입 목록 화면 설정(열 · 정렬 · 트리 · 롤업)", changed_types)

    views = 0
    for view in db.scalars(select(SavedView)):
        root = types[view.type_id].slug if view.type_id in types else None
        query, summary = _view_specs(view, walker, root)
        if query != (view.query or {}) or summary != (view.summary or {}):
            views += 1
            out.edits.append(_setter(view, "query", query))
            out.edits.append(_setter(view, "summary", summary))
    out.place("저장된 뷰(조건 · 통계 기준)", views)

    metrics = 0
    for metric in db.scalars(select(MetricDef)):
        source = types.get(metric.source_type_id)
        spec = _metric_spec(metric.spec or {}, walker, source.slug if source else None)
        if spec != (metric.spec or {}):
            metrics += 1
            out.edits.append(_setter(metric, "spec", spec))
    out.place("지표 정의(기간 · 기준 · 거르기 · 집계 칸)", metrics)

    templates = 0
    for template in db.scalars(select(SlideTemplate)):
        areas = _areas(template.areas or [], rename, walker)
        if areas != (template.areas or []):
            templates += 1
            out.edits.append(_setter(template, "areas", areas))
    out.place("장표 틀의 영역", templates)


def _setter(row: Any, name: str, value: Any) -> Callable[[], None]:
    def run() -> None:
        setattr(row, name, value)

    return run


def plan_property(
    db: Session,
    owner: ObjectType,
    prop: PropertyDef,
    to: str,
    *,
    consumers: list[str],
    skip_job: uuid.UUID | None = None,
) -> Plan:
    """속성 키 `prop.key` → `to` — 아무것도 안 바꾸고 무엇이 바뀌는지 센다. `to` 는 검사를
    마친 키다(`services.require_key`). `consumers` 는 공개 타입의 수신 자격(라우터가 센다)."""
    old = prop.key
    out = Plan("property", owner.slug, old, to, f"{owner.label} · {prop.label}")
    rename = Rename("property", owner.slug, old, to)
    _common_refusals(db, owner, out.errors, skip_job)
    if prop.data_type in DETACHED_KINDS:
        out.errors.append(
            f"{prop.label}: 문서 · 파일 속성의 키는 바꾸지 않습니다 — 첨부 · 문서의 판 · 언급 "
            "표가 "
            "이 키로 묶여 있습니다."
        )
    bound = interfaces.bound_keys(interfaces.load(db), owner.slug).get(old)
    if bound:
        out.errors.append(
            f"{prop.label}은(는) 인터페이스 「{bound}」 의 공통 속성이라 타입에서 바꾸지 "
            "않습니다."
        )
    defs = list(
        db.scalars(
            select(PropertyDef).where(
                PropertyDef.owner_kind == "type", PropertyDef.owner_id == owner.id
            )
        )
    )
    if any(one.key == to for one in defs):
        out.errors.append(f"{owner.label}에 이미 키 「{to}」 인 속성이 있습니다.")
    if ra_reports.type_refusal(owner, defs) is None and (
        old in {one[0] for one in ra_reports.FIELDS}
        or old == ra_reports.ANCHOR
        or old.startswith(ra_reports.REF_PREFIX)
    ):
        out.errors.append(
            f"{prop.label}은(는) RA 보고서 틀의 칸이라 바꾸지 않습니다 — 보고서를 받을 때 이 "
            "키를 찾습니다."
        )
    orphan = _count_with(db, owner.id, to)
    if orphan:
        out.errors.append(
            f"키 「{to}」 에 값이 남은 객체가 {orphan:,}건 있습니다(예전에 삭제한 속성의 "
            "저장값) "
            "— 섞이지 않게 다른 키를 고르십시오."
        )

    out.objects = _count_with(db, owner.id, old)
    out.place("객체 값", out.objects)
    edited = db.scalar(
        select(func.count())
        .select_from(ObjectInstance)
        .where(
            ObjectInstance.type_id == owner.id,
            ObjectInstance.human_edits.has_key(PROP + old),
        )
    )
    out.place("사람이 수정한 칸 표시", int(edited or 0))
    out.place("속성 정의", 1)
    out.edits.append(_setter(prop, "key", to))

    settings: dict[str, Any] = {}
    if owner.label_prefix == PROP + old:
        settings["label_prefix"] = PROP + to
    if owner.revision_key == old:
        settings["revision_key"] = to
    template = owner.title_template or ""
    if "{" + old + "}" in template:
        settings["title_template"] = template.replace("{" + old + "}", "{" + to + "}")
    for name, value in settings.items():
        if name != "revision_key":  # 개정 칸은 값 옮기기의 차례 안에서(`move_values`)
            out.edits.append(_setter(owner, name, value))
    out.place("타입 설정(앞머리 · 개정 칸 · 이름 틀)", len(settings))

    walker = Walker(db, rename)
    _common_places(db, rename, walker, out)
    _sources(db, owner, rename, out)
    _semantic(db, owner, rename, out)
    _graph_map(db, owner, rename, out)
    _review_ids(db, f"{owner.slug}:{old}:", f"{owner.slug}:{to}:", out)

    alerts = db.scalar(
        select(func.count())
        .select_from(MetricAlert)
        .where(MetricAlert.params["reference_via"].astext == old)
    )
    if alerts:
        out.warnings.append(
            f"경보 {alerts}개가 기준 너머의 칸 이름 「{old}」 를 씁니다(reference_via) — 그 "
            "칸이 이 "
            "속성이면 경보에서 수정하십시오."
        )
    env = _env_mentions(f"{owner.slug}.{old}")
    if env:
        out.warnings.append(
            f".env 의 {' · '.join(env)} 에 옛 키가 있습니다 — 서버 설정에서 "
            f"「{owner.slug}.{to}」 "
            "로 고치십시오(바꾸지 않으면 그 색인 · 계산이 빠집니다)."
        )
    _core(owner, consumers, out, f"속성 키 「{old}」")
    out.warnings.append(
        "바깥에 적힌 것은 바꾸지 않습니다 — 정제 키트의 정의 · 칸 대응, 원천(datarelay) "
        "레시피, "
        f"MCP · 스크립트가 「{old}」 로 부르는 곳은 함께 고치십시오. 이 변경 전 스냅샷을 "
        "복원하면 "
        "옛 키가 빈 속성으로 되살아납니다."
    )
    return out


def _count_with(db: Session, type_id: uuid.UUID, key: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(ObjectInstance.type_id == type_id, ObjectInstance.properties.has_key(key))
        )
        or 0
    )


def _sources(db: Session, owner: ObjectType, rename: Rename, out: Plan) -> None:
    changed = 0
    for source in db.scalars(select(DataSource).where(DataSource.type_id == owner.id)):
        mapping = copy.deepcopy(source.mapping or {})
        for one in mapping.get("columns") or []:
            if isinstance(one, dict) and one.get("target") == PROP + rename.old:
                one["target"] = PROP + rename.new
        options = copy.deepcopy(source.options or {})
        for one in options.get("_refs_waiting") or []:
            if not isinstance(one, dict):
                continue
            if one.get("field") == rename.old:
                one["field"] = rename.new
            row = one.get("row")
            if isinstance(row, dict) and rename.old in row:
                row[rename.new] = row.pop(rename.old)
        if mapping != (source.mapping or {}) or options != (source.options or {}):
            changed += 1
            out.edits.append(_setter(source, "mapping", mapping))
            out.edits.append(_setter(source, "options", options))
    out.place("데이터 소스의 칸 대응 · 참조 대기", changed)


def _semantic(db: Session, owner: ObjectType, rename: Rename, out: Plan) -> None:
    row = db.get(SemanticTarget, owner.id)
    if row is not None and isinstance(row.fields, list) and rename.old in row.fields:
        out.place("뜻으로 찾기 대상의 칸", 1)
        out.edits.append(_setter(row, "fields", _swap(row.fields, rename.old, rename.new)))


def _graph_map(db: Session, owner: ObjectType, rename: Rename, out: Plan) -> None:
    count = 0
    hub = db.get(MapHubKey, owner.id)
    if hub is not None and hub.key == rename.old:
        count += 1
        out.edits.append(_setter(hub, "key", rename.new))
    mine = str(owner.id)
    for model, name in ((MapLayout, "hub_keys"), (MapCounted, "points_keys")):
        for row in db.scalars(select(model)):
            keys = getattr(row, name)
            if isinstance(keys, dict) and keys.get(mine) == rename.old:
                count += 1
                out.edits.append(_setter(row, name, {**keys, mine: rename.new}))
    out.place("지식 그래프 지도의 허브 칸", count)


def _review_ids(db: Session, old_prefix: str, new_prefix: str, out: Plan) -> None:
    """검토함이 「무시」 를 기억하는 줄 — 항목 id 에 slug · 키가 든다(`<slug>:<키>:…`)."""
    rows = list(
        db.scalars(select(ReviewDecision).where(ReviewDecision.item_id.startswith(old_prefix)))
    )
    for row in rows:
        out.edits.append(_setter(row, "item_id", new_prefix + row.item_id[len(old_prefix) :]))
    out.place("검토함의 결정 기억", len(rows))


SLUG_IN_LINK = re.compile(r"/o/([a-z][a-z0-9_]*)(?=[/?#)\s]|$)")


def plan_type(db: Session, owner: ObjectType, to: str, *, consumers: list[str]) -> Plan:
    """타입 slug `owner.slug` → `to` — 아무것도 안 바꾸고 센다. `to` 는 검사를 마친 slug 다."""
    old = owner.slug
    out = Plan("type", old, None, to, owner.label)
    rename = Rename("type", old, old, to)
    _common_refusals(db, owner, out.errors)
    taken = db.scalar(select(ObjectType.id).where(ObjectType.slug == to)) or db.scalar(
        select(ObjectInterface.id).where(ObjectInterface.slug == to)
    )
    if taken:
        out.errors.append(f"이미 「{to}」 인 타입 · 인터페이스가 있습니다.")

    out.place("타입 정의", 1)
    out.edits.append(_setter(owner, "slug", to))
    refs = list(db.scalars(select(PropertyDef).where(PropertyDef.ref_type_slug == old)))
    for one in refs:
        out.edits.append(_setter(one, "ref_type_slug", to))
    out.place("이 타입을 가리키는 참조 속성", len(refs))
    ends = 0
    for kind in db.scalars(select(RelationType)):
        src, dst = list(kind.src_type_slugs or []), list(kind.dst_type_slugs or [])
        if old in src or old in dst:
            ends += 1
            out.edits.append(_setter(kind, "src_type_slugs", _swap(src, old, to)))
            out.edits.append(_setter(kind, "dst_type_slugs", _swap(dst, old, to)))
    out.place("관계 종류의 끝", ends)

    walker = Walker(db, rename)
    _common_places(db, rename, walker, out)

    links = int(
        db.scalar(
            select(func.count())
            .select_from(ObjectLink)
            .where(or_(ObjectLink.src_type == old, ObjectLink.dst_type == old))
        )
        or 0
    )
    out.place("원 표(계정 · 부서)와 맺은 관계", links)
    misses = int(
        db.scalar(select(func.count()).select_from(SearchMiss).where(SearchMiss.scope == old))
        or 0
    )
    out.place("못 찾은 말(별칭 후보)", misses)
    hooks = [one for one in db.scalars(select(Webhook)) if old in (one.type_slugs or [])]
    for hook in hooks:
        out.edits.append(
            _setter(hook, "type_slugs", _swap(list(hook.type_slugs or []), old, to))
        )
    out.place("웹훅의 타입 거르기", len(hooks))
    decided = list(db.scalars(select(ReviewDecision).where(ReviewDecision.type_slug == old)))
    for decision in decided:
        out.edits.append(_setter(decision, "type_slug", to))
        if isinstance(decision.undo, dict) and decision.undo.get("type_slug") == old:
            out.edits.append(_setter(decision, "undo", {**decision.undo, "type_slug": to}))
    _review_ids(db, f"{old}:", f"{to}:", out)
    out.place("검토함 결정의 타입", len(decided))
    notices = int(
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.link.startswith(f"/o/{old}/"))
        )
        or 0
    )
    out.place("알림의 링크", notices)

    dangling = int(
        db.scalar(
            select(func.count())
            .select_from(PropertyDef)
            .where(PropertyDef.ref_type_slug == to)
        )
        or 0
    ) + int(
        db.scalar(select(func.count()).select_from(SearchMiss).where(SearchMiss.scope == to))
        or 0
    )
    if dangling:
        out.warnings.append(
            f"「{to}」 를 가리키던 옛 자리가 {dangling}곳 있습니다(예전에 삭제한 타입의 이름) "
            "— "
            "바꾸면 그 자리가 이 타입을 가리키게 됩니다."
        )
    documents = int(
        db.scalar(
            select(func.count())
            .select_from(ObjectDocument)
            .where(ObjectDocument.body.contains(f"/o/{old}/"))
        )
        or 0
    )
    if documents:
        out.warnings.append(
            f"문서 {documents}개의 본문에 옛 주소(/o/{old}/…) 링크가 있습니다 — 본문은 바꾸지 "
            "않습니다. 옛 주소로 열면 새 slug 를 안내합니다."
        )
    ra = int(
        db.scalar(
            select(func.count())
            .select_from(PropertyDef)
            .where(PropertyDef.key == f"{ra_reports.REF_PREFIX}{old}")
        )
        or 0
    )
    if ra:
        out.warnings.append(
            f"RA 보고서 타입의 참조 칸 「{ra_reports.REF_PREFIX}{old}」 은 이 slug 를 "
            "따릅니다 — "
            f"다음 받기부터 「{ra_reports.REF_PREFIX}{to}」 를 찾으니 그 칸의 키도 바꾸십시오."
        )
    env = _env_mentions(f"{old}.")
    if env:
        out.warnings.append(
            f".env 의 {' · '.join(env)} 에 옛 slug 가 있습니다 — 서버 설정에서 고치십시오."
        )
    _core(owner, consumers, out, f"slug 「{old}」(주소 /api/core/{old})")
    out.warnings.append(
        f"바깥에 적힌 것은 바꾸지 않습니다 — 즐겨찾기 · 공유한 화면 주소(/o/{old}), 정제 "
        "키트의 "
        "정의, 원천 레시피, MCP · 스크립트. 옛 주소로 열면 새 slug 를 안내합니다. 이 변경 전 "
        "스냅샷을 복원하면 옛 slug 의 빈 타입이 생깁니다. 지식 그래프 · SPARQL 사본은 다음 "
        "밤에 새 "
        "이름으로 다시 섭니다."
    )
    return out


# --- 적용 ------------------------------------------------------------------------


def _chunks(db: Session, type_id: uuid.UUID, where: Any) -> Iterable[list[uuid.UUID]]:
    last: uuid.UUID | None = None
    while True:
        query = select(ObjectInstance.id).where(ObjectInstance.type_id == type_id, where)
        if last is not None:
            query = query.where(ObjectInstance.id > last)
        ids = list(db.scalars(query.order_by(ObjectInstance.id).limit(CHUNK)))
        if not ids:
            return
        yield ids
        last = ids[-1]


_OLD = "CAST(:old AS text)"
_NEW = "CAST(:new AS text)"
_COPY = text(
    f"UPDATE objects SET properties = properties || jsonb_build_object({_NEW}, "
    f"properties -> {_OLD}) WHERE id = ANY(:ids)"
)
_DROP = text(f"UPDATE objects SET properties = properties - {_OLD} WHERE id = ANY(:ids)")
_MOVE = text(
    f"UPDATE objects SET properties = (properties - {_OLD}) || jsonb_build_object({_NEW}, "
    f"properties -> {_OLD}) WHERE id = ANY(:ids)"
)
_EDITS = text(
    f"UPDATE objects SET human_edits = (human_edits - {_OLD}) || jsonb_build_object({_NEW}, "
    f"human_edits -> {_OLD}) WHERE type_id = :type_id AND human_edits ? {_OLD}"
)


def move_values(
    db: Session, owner: ObjectType, old: str, new: str, *, total: int, progress: Progress
) -> None:
    """객체 값의 키만 옮긴다 — `updated_at` 은 그대로. 개정 칸이면 복사 → 개정 칸 → 삭제.

    줄은 id 차례로 끊어 읽는다(앞 끊음의 끝 id 다음부터) — 복사는 옛 키를 남기므로 같은
    조건으로
    다시 고르면 같은 줄이 또 잡힌다."""
    revision = owner.revision_key == old
    steps = [_COPY, _DROP] if revision else [_MOVE]
    done = 0
    for step in steps:
        if step is _DROP:
            # 트리거(`object_types_revision`)가 새 칸으로 같은 값을 다시 박는다 — 낡지 않는다.
            owner.revision_key = new
            db.flush()
        for ids in _chunks(db, owner.id, ObjectInstance.properties.has_key(old)):
            db.execute(step, {"old": old, "new": new, "ids": ids})
            done += len(ids)
            if progress is not None:
                progress("객체 값", done, total * len(steps))
    db.execute(_EDITS, {"old": PROP + old, "new": PROP + new, "type_id": owner.id})


def apply_edits(db: Session, plan: Plan) -> None:
    for edit in plan.edits:
        edit()
    db.flush()


def residual(db: Session, plan: Plan, *, large_ref: bool) -> list[str]:
    """바꾼 뒤에도 **깨지는** 뷰 · 지표 — 걸음의 상대가 여럿이라 못 바꾼 주소 등. 세이브포인트
    안에서 정의 · 주소를 바꿔 보고 되돌린다(`retype.breaking`). 큰 참조 칸은 키를 바꾸는 순간
    참조 색인을 통째로 다시 세우는 트리거가 돌아 흉내가 비싸다 — 건너뛰고 그렇다고 말한다."""
    from app.modules.ontology import retype

    if large_ref:
        return [
            "참조 칸이 큰 타입이라 「바꾼 뒤에도 깨지는 뷰 · 지표」 흉내를 건너뛰었습니다 — "
            "적용 뒤 "
            "뷰 · 지표 화면에서 확인하십시오."
        ]
    return [
        f"바꾼 뒤에도 깨집니다(주소를 자동으로 못 바꿈) — {one}"
        for one in retype.breaking(db, lambda: apply_edits(db, plan))
    ]


def type_side_tables(db: Session, old: str, new: str) -> None:
    """slug 를 글자로 쥔 큰 표 — SQL 로 한 번에."""
    db.execute(update(ObjectLink).where(ObjectLink.src_type == old).values(src_type=new))
    db.execute(update(ObjectLink).where(ObjectLink.dst_type == old).values(dst_type=new))
    # 같은 말이 새 이름 아래 이미 있으면(삭제한 타입의 남은 줄) 옛 줄은 지운다 — 유일 제약.
    db.execute(
        text(
            "DELETE FROM search_misses o WHERE o.scope = :old AND EXISTS (SELECT 1 FROM "
            "search_misses n WHERE n.scope = :new AND n.norm = o.norm)"
        ),
        {"old": old, "new": new},
    )
    db.execute(update(SearchMiss).where(SearchMiss.scope == old).values(scope=new))
    db.execute(
        text(
            "UPDATE notifications SET link = :new || substr(link, :cut) WHERE link LIKE "
            ":pattern"
        ),
        {"new": f"/o/{new}/", "cut": len(f"/o/{old}/") + 1, "pattern": f"/o/{old}/%"},
    )


def fingerprint(plan: Plan) -> str:
    """미리 본 계획과 적용 직전 계획이 같은가 — 작업의 적용이 본다."""
    import hashlib
    import json

    body: list[Any] = [plan.kind, plan.slug, plan.key, plan.to, plan.objects, plan.errors]
    body.append([(one.what, one.count) for one in plan.places])
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest()


def current_slug(db: Session, slug: str) -> str | None:
    """옛 slug 가 지금 무엇인가 — 타입 slug 변경 기록(`ontology.type.rename`)을 따라간다.
    없으면 None. 옛 주소 · 북마크를 연 사람에게 새 주소를 말할 자리다."""
    from app.modules.audit.models import AuditEntry

    seen = {slug}
    current = slug
    for _ in range(10):
        found = db.scalar(
            select(AuditEntry.changes["to"].astext)
            .where(
                AuditEntry.action == "ontology.type.rename",
                AuditEntry.changes["from"].astext == current,
            )
            .order_by(AuditEntry.created_at.desc())
            .limit(1)
        )
        if not found or found in seen:
            break
        seen.add(found)
        current = found
    if current == slug:
        return None
    exists = db.scalar(select(ObjectType.id).where(ObjectType.slug == current))
    return current if exists is not None else None


def not_found_message(db: Session, slug: str) -> tuple[str, dict[str, str]]:
    """타입을 못 찾았을 때의 말 — 이름이 바뀐 것이면 새 slug 를 함께."""
    renamed = current_slug(db, slug)
    if renamed:
        return (
            f"타입을 찾을 수 없습니다: {slug} — slug 가 「{renamed}」 로 바뀌었습니다"
            f"(주소 /o/{renamed}).",
            {"renamed_to": renamed},
        )
    return f"타입을 찾을 수 없습니다: {slug}", {}
