"""처리 길이 아직 없는 종류 — **「원래 화면으로」 링크만** 낸다(ADR 0027).

    job_plan          읽고 적용하기를 기다리는 계획   → 작업 화면(계획 표를 거기서 읽는다)
    waiting_relation  데이터 소스의 선 기다림         → 데이터 소스 화면(다음 동기화가 다시
                                                        넣는다)

여기서 처리하지 않는 까닭이 다르다. 계획은 **줄마다 읽어야 하는 표**가 붙어 있어 한 줄 물음으로
접을 수 없고, 선 기다림은 사람이 정할 것이 아니라 끝점(가리키는 객체)이 들어오기를 기다리는
것이다. 그래도 한 자리에 세는 것은 — 「판단을 기다리는 일이 어디 몇 건 있나」 를 한 화면이 다
말해야 다른 화면을 하나씩 열어 보지 않기 때문이다.

수는 원래 자리가 센 것을 그대로 쓴다(계획은 `jobs.services.maintenance`) — 따로 세면 홈과
검토함의 수가 갈린다.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.datasources.models import DataSource
from app.modules.jobs import services as job_services
from app.modules.ontology.models import ObjectType
from app.modules.review import registry
from app.modules.review.registry import Collected, Filters, Item
from app.shared.permissions import is_any_manager


def _manager(db: Session, user: User) -> bool:
    return is_any_manager(db, user)


def _admin(_db: Session, user: User) -> bool:
    return user.is_system_admin


def collect_plans(db: Session, user: User, flt: Filters) -> Collected:
    """내가 미리 보고 아직 적용하지 않은 계획 — 홈 「남은 일」 과 같은 셈."""
    if flt.type_slug is not None or flt.workspace_id is not None or flt.workspace_global:
        # 계획은 파일 하나 · 묶음 하나라 타입 · 부서로 가르지 않는다 — 거르면 안 보인다고
        # 말한다.
        return Collected(
            items=[], total=0, notes=["적용을 기다리는 계획은 타입 · 부서로 거르지 않습니다."]
        )
    found = next(
        (one for one in job_services.maintenance(db, user) if one.key == "job_awaiting_apply"),
        None,
    )
    if found is None or not found.count:
        return Collected(items=[], total=0)
    item = Item(
        kind="job_plan",
        id="job_awaiting_apply",
        question=f"읽고 적용하기를 기다리는 계획 {found.count:,}건",
        evidence=["올린 파일 · 묶음의 계획입니다 — 작업 화면에서 줄마다 읽고 적용합니다"],
        choices=[],
        recommended=None,
        confidence=None,
        reason="",
        type_slug="",
        type_label="",
        workspace_id=None,
        rows=found.count,
        link="/jobs",
        ignorable=False,
        priority=float(found.count),
    )
    if flt.item_ids is not None and item.id not in flt.item_ids:
        return Collected(items=[], total=0)
    return Collected(items=[item], total=1)


def collect_waiting(db: Session, user: User, flt: Filters) -> Collected:
    """끝점을 아직 못 찾아 기다리는 선 — 데이터 소스마다 한 줄."""
    waiting = func.jsonb_array_length(DataSource.relations_waiting)
    stmt = (
        select(DataSource, ObjectType.slug, ObjectType.label, waiting)
        .join(ObjectType, ObjectType.id == DataSource.type_id)
        .where(waiting > 0)
    )
    if flt.type_slug is not None:
        stmt = stmt.where(ObjectType.slug == flt.type_slug)
    if flt.workspace_global:
        stmt = stmt.where(DataSource.workspace_id.is_(None))
    elif flt.workspace_id is not None:
        stmt = stmt.where(DataSource.workspace_id == flt.workspace_id)
    items = [
        Item(
            kind="waiting_relation",
            id=str(source.id),
            question=f"「{source.name}」 의 선 {int(count):,}줄이 끝점을 기다립니다",
            evidence=[
                "가리키는 객체가 아직 안 들어왔습니다 — 다음 동기화가 다시 넣어 봅니다",
                f"마지막 실행 {source.last_run_at:%Y-%m-%d %H:%M}"
                if source.last_run_at
                else "",
            ],
            choices=[],
            recommended=None,
            confidence=None,
            reason="",
            type_slug=slug,
            type_label=label,
            workspace_id=source.workspace_id,
            rows=int(count),
            link="/admin/datasources",
            ignorable=False,
            priority=float(count),
        )
        for source, slug, label, count in db.execute(stmt.order_by(waiting.desc()))
        if flt.item_ids is None or str(source.id) in flt.item_ids
    ]
    return Collected(items=items, total=len(items))


JOB_PLAN = registry.Provider(
    kind="job_plan",
    label="적용을 기다리는 계획",
    description="미리 보고 아직 적용하지 않은 일괄 입력 · 묶음의 계획 — 작업 화면에서 읽고 "
    "적용합니다.",
    link="/jobs",
    can_view=_manager,
    collect=collect_plans,
    maintenance_keys=("job_awaiting_apply",),
    order=80,
)

WAITING_RELATION = registry.Provider(
    kind="waiting_relation",
    label="선 기다림",
    description="데이터 소스가 끝점을 아직 못 찾은 선 — 다음 동기화가 다시 넣어 봅니다.",
    link="/admin/datasources",
    can_view=_admin,
    collect=collect_waiting,
    order=90,
)

PROVIDERS = (JOB_PLAN, WAITING_RELATION)
