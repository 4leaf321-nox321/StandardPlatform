"""자주검증 입력 — 설정과 「문서 한 장」 읽기.

쓰기는 여기 없다. 문서 · 문제점 · 하위 기록은 코어 객체 API 로 만들고 고친다 — 검증 · 권한 ·
감사 · 참조 색인이 그대로 따라오고, 쓰기 길이 두 벌이 되지 않는다(ADR 0051).

**연결은 참조 칸 또는 관계다.** 역할의 `report_ref` · `issue_ref` 는 속성 키(`report`)
또는 `rel:<관계 종류 slug>` 다 — 한 문제점이 심사회 · 포스트모텀에 함께 실리려면 여럿 대
여럿이어야 하고(ADR 0051 7절), 허브의 실제 연결이 어느 쪽이든 설정으로 맞춘다. 관계는 방향을
가리지 않고 본다(문서 → 문제점이든 문제점 → 문서든 같은 뜻이다).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.extensions.svinput import definitions as D
from app.extensions.svinput.models import SvInputSetting
from app.modules.accounts.models import User
from app.modules.objects import access, edges
from app.modules.objects.models import ObjectInstance, ObjectRelation
from app.modules.objects.services import properties_of
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
from app.modules.server import services as server_services
from app.shared import audit
from app.shared import extensions as extension_points
from app.shared.errors import AppError, NotFound, code
from app.shared.permissions import visible_owner_clause

#: 판정 칸의 값을 통과 · 실패로 읽는 말 — 허브는 PASS/FAIL, 회의록은 OK/NG 를 쓴다.
PASS_WORDS = ("pass", "ok", "양호", "유효", "합격")
FAIL_WORDS = ("fail", "ng", "불량", "미흡", "불합격")

#: 연결 칸의 값이 관계 종류를 가리킬 때의 접두.
REL = "rel:"


def enabled(db: Session) -> bool:
    """이 설치에서 켜져 있나 — 자기소개 · 서버 화면은 확장의 문을 거치지 않고 부른다."""
    return D.NAME in server_services.enabled_names(db)


# ── 설정 ──────────────────────────────────────────────────────────────────────


def setting(db: Session) -> SvInputSetting:
    """설정 한 줄 — 없으면 만든다. **커밋은 부르는 쪽이 한다.**"""
    row = db.get(SvInputSetting, 1)
    if row is None:
        row = SvInputSetting(id=1, roles={})
        db.add(row)
        db.flush()
    return row


def _type_or_none(db: Session, slug: str | None) -> ObjectType | None:
    if not slug:
        return None
    return db.scalar(select(ObjectType).where(ObjectType.slug == slug))


def _relation_exists(db: Session, slug: str) -> bool:
    return db.scalar(select(RelationType.id).where(RelationType.slug == slug)) is not None


def _keys_of(db: Session, kind: ObjectType) -> set[str]:
    return {one.key for one in properties_of(db, kind.id)}


def _field_ok(db: Session, keys: set[str], value: str) -> bool:
    """칸 역할의 값이 실제로 있나 — 속성 키이거나 `rel:<있는 관계 종류>`."""
    if value.startswith(REL):
        return _relation_exists(db, value[len(REL) :])
    return value in keys


def status(db: Session) -> dict[str, Any]:
    """화면이 「쓸 준비가 됐나」 를 묻는 자리 — 역할마다 설정값과 **실제로 있는지**."""
    roles = setting(db).roles or {}
    states: list[dict[str, Any]] = []
    ready = True
    for role in D.ROLES:
        key = str(role["key"])
        got = roles.get(key) or {}
        slug = got.get("type") or None
        kind = _type_or_none(db, slug)
        missing: list[str] = []
        if slug and kind is None:
            missing.append("type")
        keys = _keys_of(db, kind) if kind else set()
        fields: dict[str, str | None] = {}
        for field in role["fields"]:
            fkey = str(field["key"])
            value = got.get(fkey) or None
            fields[fkey] = value
            if kind is not None and value and not _field_ok(db, keys, value):
                missing.append(fkey)
            if kind is not None and field["required"] and not value:
                missing.append(fkey)
        if key in D.REQUIRED_ROLES and (kind is None or missing):
            ready = False
        states.append(
            {
                "key": key,
                "type_slug": slug,
                "type_label": kind.label if kind else None,
                "fields": fields,
                "missing": missing,
            }
        )
    return {"roles": states, "ready": ready, "defs": D.ROLES}


def candidates(db: Session) -> dict[str, Any]:
    """설정 화면의 후보 — 타입 전부(허브 접두 여부 표시) · 속성 키 · 관계 종류. **없는 칸을
    고를 수
    없게.** 스파인(코어 과제 · 모델)은 접두가 아닌 것에서 고른다."""
    kinds = db.scalars(
        select(ObjectType).where(ObjectType.is_active.is_(True)).order_by(ObjectType.slug)
    ).all()
    relations = db.scalars(
        select(RelationType)
        .where(RelationType.is_active.is_(True))
        .order_by(RelationType.slug)
    ).all()
    return {
        "prefix": D.CANDIDATE_PREFIX,
        "types": [
            {
                "slug": kind.slug,
                "label": kind.label,
                "usage": kind.usage,
                "hub": kind.slug.startswith(D.CANDIDATE_PREFIX),
                "properties": sorted(_keys_of(db, kind)),
            }
            for kind in kinds
        ],
        "relations": [{"slug": one.slug, "label": one.label} for one in relations],
    }


def save_roles(db: Session, user: User, roles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """역할 → 타입 · 칸을 적는다. 모르는 역할 · 없는 타입 · 그 타입에 없는 칸은 거절한다.

    **보낸 역할만 바꾼다** — 안 보낸 역할은 그대로다(부분 수정에서 「안 보낸 것」 과 「비운
    것」 을 가른다). 역할을 비우려면 `type` 을 null 로 보낸다.
    """
    row = setting(db)
    before = dict(row.roles or {})
    after = dict(before)
    for key, got in roles.items():
        role = D.ROLE_BY_KEY.get(key)
        if role is None:
            raise AppError(code("SVINPUT", 1), f"모르는 역할입니다: {key}", status=422)
        slug = got.get("type") or None
        if slug is None:
            after.pop(key, None)
            continue
        kind = _type_or_none(db, slug)
        if kind is None:
            raise AppError(code("SVINPUT", 2), f"타입을 찾을 수 없습니다: {slug}", status=422)
        keys = _keys_of(db, kind)
        entry: dict[str, Any] = {"type": slug}
        for field in role["fields"]:
            fkey = str(field["key"])
            value = (got.get("fields") or {}).get(fkey) or None
            if value is not None and not _field_ok(db, keys, value):
                raise AppError(
                    code("SVINPUT", 3),
                    f"{kind.label}({slug}) 에 그런 칸이 없습니다: {value}"
                    " (관계 종류면 rel:<slug>)",
                    status=422,
                )
            if value is not None:
                entry[fkey] = value
        after[key] = entry
    row.roles = after
    audit.record(
        db,
        action="svinput.settings",
        actor=user,
        target_table="svinput_settings",
        target_id=None,
        target_label="자주검증 입력 설정",
        changes={"before": before, "after": after},
    )
    return status(db)


# ── 읽기 ──────────────────────────────────────────────────────────────────────


def _role(db: Session, key: str) -> tuple[ObjectType, dict[str, Any]] | None:
    got = (setting(db).roles or {}).get(key) or {}
    kind = _type_or_none(db, got.get("type"))
    if kind is None:
        return None
    return kind, got


def _refers(key: str, target: uuid.UUID) -> Any:
    """참조 칸이 그 객체를 가리키나 — 값 하나(`"id"`)든 여럿(`["id", …]`)이든."""
    column = ObjectInstance.properties[key]
    return or_(column.astext == str(target), column.contains([str(target)]))


def _visible(user: User, kind: ObjectType) -> Any:
    return select(ObjectInstance).where(
        ObjectInstance.type_id == kind.id,
        ObjectInstance.deleted_at.is_(None),
        visible_owner_clause(user, ObjectInstance.owner_workspace_id),
    )


def members(
    db: Session,
    user: User,
    kind: ObjectType,
    spec: str,
    targets: list[uuid.UUID],
) -> dict[uuid.UUID, list[ObjectInstance]]:
    """`spec`(참조 칸 또는 `rel:<slug>`)으로 `targets` 를 가리키는 `kind` 의 객체 — 대상마다.
    볼 수 있는 것만. 관계는 방향을 가리지 않고, 오늘 살아 있는 선만(ADR 0039)."""
    out: dict[uuid.UUID, list[ObjectInstance]] = {one: [] for one in targets}
    if not targets:
        return out
    if spec.startswith(REL):
        slug = spec[len(REL) :]
        pairs = db.execute(
            select(ObjectRelation.src_object_id, ObjectRelation.dst_object_id).where(
                ObjectRelation.relation == slug,
                or_(
                    ObjectRelation.src_object_id.in_(targets),
                    ObjectRelation.dst_object_id.in_(targets),
                ),
                edges.alive(ObjectRelation, edges.today()),
            )
        ).all()
        wanted: dict[uuid.UUID, set[uuid.UUID]] = {}
        for src, dst in pairs:
            for target, other in ((src, dst), (dst, src)):
                if target in out:
                    wanted.setdefault(other, set()).add(target)
        if not wanted:
            return out
        rows = db.scalars(
            _visible(user, kind)
            .where(ObjectInstance.id.in_(list(wanted)))
            .order_by(ObjectInstance.created_at, ObjectInstance.label)
        ).all()
        for row in rows:
            for target in wanted.get(row.id, ()):
                out[target].append(row)
        return out
    rows = db.scalars(
        _visible(user, kind)
        .where(or_(*[_refers(spec, one) for one in targets]))
        .order_by(ObjectInstance.created_at, ObjectInstance.label)
    ).all()
    for row in rows:
        value = (row.properties or {}).get(spec)
        for one in value if isinstance(value, list) else [value]:
            try:
                target = uuid.UUID(str(one))
            except (ValueError, TypeError):
                continue
            if target in out:
                out[target].append(row)
    return out


def _ref_labels(
    db: Session, defs: list[PropertyDef], rows: list[ObjectInstance]
) -> dict[str, str]:
    """참조 칸이 가리키는 객체의 이름 — 한 번에 모아 준다(화면이 id 를 그리지 않게)."""
    ref_keys = [one.key for one in defs if one.data_type == "object_ref"]
    ids: set[uuid.UUID] = set()
    for row in rows:
        for key in ref_keys:
            value = (row.properties or {}).get(key)
            for one in value if isinstance(value, list) else [value]:
                try:
                    ids.add(uuid.UUID(str(one)))
                except (ValueError, TypeError):
                    continue
    if not ids:
        return {}
    found = db.execute(
        select(ObjectInstance.id, ObjectInstance.label).where(ObjectInstance.id.in_(ids))
    ).all()
    return {str(oid): label for oid, label in found}


def _object_out(
    row: ObjectInstance, kind: ObjectType, labels: dict[str, str]
) -> dict[str, Any]:
    mine: set[str] = set()
    for value in (row.properties or {}).values():
        for one in value if isinstance(value, list) else [value]:
            mine.add(str(one))
    return {
        "id": str(row.id),
        "type_slug": kind.slug,
        "key": row.key,
        "label": row.label,
        "description": row.description,
        "properties": row.properties or {},
        "ref_labels": {k: v for k, v in labels.items() if k in mine},
        "status": row.status,
        "owner_workspace_id": str(row.owner_workspace_id) if row.owner_workspace_id else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _verdict(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if any(word in text for word in PASS_WORDS):
        return "pass"
    if any(word in text for word in FAIL_WORDS):
        return "fail"
    return None


def _blank_counts() -> dict[str, int]:
    return {
        "stage_result": 0,
        "cause": 0,
        "prevention": 0,
        "figure": 0,
        "passed": 0,
        "failed": 0,
    }


def report_sheet(db: Session, user: User, report_id: uuid.UUID) -> dict[str, Any]:
    """문서 한 장 — 머리 · 문제점 줄 · 하위 기록 수 · 지표. **볼 수 있는 것만**(설치의 조회
    범위)."""
    got = _role(db, "report")
    issue_role = _role(db, "issue")
    if got is None or issue_role is None:
        raise NotFound(code("SVINPUT", 4), "먼저 설정에서 문서 · 문제점 타입을 고르세요.")
    report_kind, _report_cfg = got
    issue_kind, issue_cfg = issue_role
    report = db.scalar(_visible(user, report_kind).where(ObjectInstance.id == report_id))
    if report is None:
        raise NotFound(code("SVINPUT", 5), "문서를 찾을 수 없습니다.")
    ref_spec = issue_cfg.get("report_ref")
    if not ref_spec:
        raise NotFound(code("SVINPUT", 6), "설정에 문제점의 문서 참조 칸이 없습니다.")

    issues = members(db, user, issue_kind, str(ref_spec), [report.id])[report.id]
    issue_defs = properties_of(db, issue_kind.id)
    labels = _ref_labels(db, issue_defs, issues)
    status_key = issue_cfg.get("status")

    counts = {row.id: _blank_counts() for row in issues}
    if issues:
        issue_ids = [row.id for row in issues]
        for role_key in ("stage_result", "cause", "prevention", "figure"):
            child = _role(db, role_key)
            if child is None or not child[1].get("issue_ref"):
                continue
            child_kind, child_cfg = child
            result_key = child_cfg.get("result") if role_key == "stage_result" else None
            per_issue = members(db, user, child_kind, str(child_cfg["issue_ref"]), issue_ids)
            for issue_id, rows in per_issue.items():
                bucket = counts[issue_id]
                for row in rows:
                    bucket[role_key] += 1
                    if result_key:
                        verdict = _verdict((row.properties or {}).get(result_key))
                        if verdict == "pass":
                            bucket["passed"] += 1
                        elif verdict == "fail":
                            bucket["failed"] += 1

    kpis: list[dict[str, Any]] = []
    kpi_role = _role(db, "kpi")
    if kpi_role is not None and kpi_role[1].get("report_ref"):
        kpi_kind, kpi_cfg = kpi_role
        kpi_rows = members(db, user, kpi_kind, str(kpi_cfg["report_ref"]), [report.id])[
            report.id
        ]
        kpis = [_object_out(row, kpi_kind, {}) for row in kpi_rows]

    report_labels = _ref_labels(db, properties_of(db, report_kind.id), [report])
    out_issues = []
    for row in issues:
        value = (row.properties or {}).get(status_key) if status_key else None
        out_issues.append(
            {
                **_object_out(row, issue_kind, labels),
                "plan_status": str(value) if value is not None and value != "" else None,
                "counts": counts[row.id],
            }
        )
    return {
        "report": _object_out(report, report_kind, report_labels),
        "report_type_slug": report_kind.slug,
        "issue_type_slug": issue_kind.slug,
        "issues": out_issues,
        "total_issues": len(issues),
        "kpis": kpis,
        "can_edit": access.can_edit(db, user, report_kind, report),
    }


# ── 공통 화면 ──────────────────────────────────────────────────────────────────


def _count(db: Session, kind: ObjectType) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(ObjectInstance.type_id == kind.id, ObjectInstance.deleted_at.is_(None))
        )
        or 0
    )


def stats(db: Session) -> list[extension_points.StatItem]:
    """서버 화면의 「쌓인 것」 — 켠 설치에서만. 설정된 역할의 객체 수."""
    if not enabled(db):
        return []
    out: list[extension_points.StatItem] = []
    for role in D.ROLES:
        got = _role(db, str(role["key"]))
        if got is None:
            continue
        out.append(
            extension_points.StatItem(
                label=f"자주검증 {role['label']}", count=_count(db, got[0])
            )
        )
    return out


def profile_facts(db: Session) -> list[extension_points.ProfileFact]:
    """플랫폼 자기소개(MCP 안내문)의 한 갈래 — 켠 설치에서만."""
    if not enabled(db):
        return []
    report = _role(db, "report")
    issue = _role(db, "issue")
    if report is None or issue is None:
        text = "자주검증 입력 — 아직 설정 전(시스템 관리자 › 자주검증 입력 › 설정)"
    else:
        text = (
            f"자주검증 입력 — 문제점({issue[0].slug}) {_count(db, issue[0])}건이 관리 단위, "
            f"포스트모텀 문서({report[0].slug}) {_count(db, report[0])}건이 그것을 참조한다. "
            f"문서 한 장은 GET /api/ext/{D.NAME}/reports/<id>, 쓰기는 코어 객체 API 로."
        )
    return [extension_points.ProfileFact(key=D.NAME, label=D.LABEL, lines=[text], marks={})]


def maintenance(db: Session, viewer: User) -> list[extension_points.MaintenanceItem]:
    """홈의 「남은 일」 — 설정이 안 됐으면 시스템 관리자에게만 말한다. 0 건은 레지스트리가
    거른다."""
    if not enabled(db):
        return []
    if not viewer.is_system_admin:
        return []
    state = status(db)
    if state["ready"]:
        return []
    return [
        extension_points.MaintenanceItem(
            key="svinput_setup",
            label="자주검증 입력 — 타입 설정이 안 됐습니다",
            count=1,
            link=f"/ext/{D.NAME}/setup",
            severity="warning",
        )
    ]
