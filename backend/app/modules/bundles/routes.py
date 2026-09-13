"""묶음 가져오기 라우터 — 로컬 정제 도구와 MCP 가 부른다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.modules.accounts.models import User
from app.modules.bundles import services
from app.modules.bundles.schemas import BatchOut, BundleIn, BundleOut
from app.modules.objects import bulk
from app.modules.objects.schemas import ImportPlanOut as RowsPlanOut
from app.modules.objects.schemas import ImportRowOut
from app.modules.ontology.schemas import ChangeOut
from app.modules.ontology.schemas import ImportPlanOut as SchemaPlanOut
from app.shared.auth import current_user
from app.shared.errors import Conflict, Forbidden, code

router = APIRouter(prefix="/bundles", tags=["bundles"])

#: 정의가 든 묶음에 더 필요한 범위. 경로의 범위(objects:write)만으로는 부족하다.
ONTOLOGY_SCOPE = "ontology:write"


@router.post("/import", response_model=BundleOut)
def import_bundle(
    payload: BundleIn,
    request: Request,
    user: User = Depends(current_user),
) -> BundleOut:
    """정의 · 객체 · 관계를 **한 묶음으로** — `apply=false`(기본)면 아무것도 저장하지 않는다.

    **한 번에 미리 본다.** 정의를 먼저 적용하지 않아도 그 정의로 객체와 관계를 맞춰 본다.
    **적용은 전부 아니면 무** — 한 곳이라도 오류면 아무것도 안 들어간다. 규칙은 정의
    가져오기 · 파일로 넣기와 같다(그 서비스를 그대로 부른다).
    """
    if payload.ontology is None and not payload.objects and not payload.relations:
        raise Conflict(
            code("BUNDLES", 2),
            "묶음이 비어 있습니다 — ontology · objects · relations 중 하나는 있어야 합니다.",
        )
    granted: list[str] | None = getattr(request.state, "token_scopes", None)
    if payload.ontology is not None and granted is not None and ONTOLOGY_SCOPE not in granted:
        raise Forbidden(
            code("BUNDLES", 1),
            f"이 토큰에는 {ONTOLOGY_SCOPE} 범위가 없습니다 — 정의가 든 묶음은 정의와 객체 "
            "쓰기 범위가 모두 필요합니다.",
            details={"needed": ONTOLOGY_SCOPE, "granted": granted},
        )
    outcome = services.run(user, payload)
    return BundleOut(
        applied=outcome.applied,
        ok=outcome.ok,
        ontology=_schema_out(outcome) if outcome.ontology is not None else None,
        objects=[_batch_out(one, outcome.applied) for one in outcome.objects],
        relations=[_batch_out(one, outcome.applied) for one in outcome.relations],
        errors=outcome.errors,
        snapshot_id=outcome.snapshot_id,
        counts=outcome.counts,
    )


def _schema_out(outcome: services.Outcome) -> SchemaPlanOut:
    planned = outcome.ontology
    assert planned is not None
    return SchemaPlanOut(
        applied=outcome.applied,
        changes=[
            ChangeOut(kind=c.kind, slug=c.slug, action=c.action, fields=c.fields)
            for c in planned.changes
        ],
        warnings=planned.warnings,
        errors=planned.errors,
        snapshot_id=outcome.snapshot_id,
    )


def _batch_out(batch: services.Batch, applied: bool) -> BatchOut:
    return BatchOut(
        type_slug=batch.type_slug,
        plan=_rows_out(batch.plan, applied) if batch.plan is not None else None,
        error=batch.error,
    )


def _rows_out(plan: bulk.Plan, applied: bool) -> RowsPlanOut:
    return RowsPlanOut(
        applied=applied,
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
            for one in plan.rows
        ],
        errors=plan.errors,
        counts=plan.counts,
    )
