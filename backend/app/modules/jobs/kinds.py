"""작업의 종류 — **이름 하나가 처리 함수 하나다.**

새 종류는 여기 `register` 한 줄이다. 라우터 · 워커 · 화면은 종류를 모른다 — `needs_file` 과
`two_step` 만 보고, 나머지는 `run(work)` 가 한다. 결과(`dict`)는 그대로 `jobs.result` 에
들어가 화면이 그린다.

일괄 입력 둘(`objects_import` · `relations_import`)은 `objects/bulk.py` 를 그대로 부른다 —
요청 경로(`import-rows`)와 **같은 규칙**이어야 「화면으로는 되는데 파일로는 안 되는」 상태가
안 생긴다. 다른 점은 행 상한(`job_max_rows`)과 진행률뿐이다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.bundles import export as bundle_export_service
from app.modules.bundles import services as bundle_services
from app.modules.bundles.schemas import BundleIn
from app.modules.jobs import files
from app.modules.jobs.models import Job, JobFile
from app.modules.objects import bulk
from app.modules.objects.schemas import ImportPlanOut, ImportRowOut
from app.modules.objects.services import apply_sort, properties_of
from app.modules.ontology.models import ObjectType
from app.shared import sheets
from app.shared.errors import Conflict, NotFound, code
from app.shared.permissions import resolve_owner_workspace


@dataclass
class Work:
    db: Session
    """본 트랜잭션. 처리 함수는 커밋하지 않는다 — 워커가 끝에서 한 번 한다."""
    job: Job
    user: User | None
    """시킨 사람. 타이머가 넣은 작업(`allow_system` 인 종류)은 None — 감사 기록의 actor 가
    「타이머」 가 된다."""
    params: dict[str, Any]
    input_file: JobFile | None
    progress: Callable[[str, int, int], None]
    """(단계, 처리한 수, 전체). 취소 요청이 있으면 **여기서 `Cancelled` 가 난다** — 단계
    사이에서만 멈추는 이유다."""
    output_file_id: uuid.UUID | None = None
    """결과 파일을 만들었으면 `emit` 이 여기 적는다 — 워커가 끝에 작업 행에 옮긴다."""

    def emit(self, *, name: str, content_type: str, data: bytes) -> JobFile:
        """결과 파일 — 사람은 `GET /api/jobs/{id}/download` 로 받는다."""
        stored = files.store(self.db, name=name, content_type=content_type, data=data)
        self.output_file_id = stored.id
        return stored


@dataclass(frozen=True)
class Kind:
    name: str
    label: str
    needs_file: bool
    two_step: bool
    """계획 → 사람 확정 → 적용의 두 단계인가. 그러면 `params["apply"]` 와 지문을 쓴다."""
    run: Callable[[Work], dict[str, Any]]
    owns_workspace: bool = False
    """넣을 때 부서를 정하는 종류인가(객체 가져오기). 관계는 양끝의 것이라 부서가 없다."""
    allow_system: bool = False
    """시킨 사람 없이(타이머) 돌아도 되는 종류인가."""


_registry: dict[str, Kind] = {}


def register(kind: Kind) -> None:
    _registry[kind.name] = kind


def get(name: str) -> Kind | None:
    return _registry.get(name)


def names() -> list[str]:
    return sorted(_registry)


def all_kinds() -> list[Kind]:
    return [_registry[name] for name in names()]


# --- 가져오기 둘 ---------------------------------------------------------------


def fingerprint(plan: bulk.Plan) -> str:
    """계획의 지문 — 미리 본 것과 적용하는 것이 **같은 계획**인지.

    `object_id` 는 뺀다: 적용하면서 새로 만든 행에 id 가 붙어 지문이 달라지기 때문이다.
    행 번호 · 무엇이 될지 · 무엇이 바뀔지가 같으면 같은 계획이다.
    """
    body = [
        (one.row, one.action, one.label, one.key, sorted(one.changes), one.message)
        for one in plan.rows
    ]
    raw = json.dumps({"rows": body, "errors": plan.errors}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _plan_result(plan: bulk.Plan, *, applied: bool) -> dict[str, Any]:
    out = ImportPlanOut(
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
    ).model_dump(mode="json")
    out["fingerprint"] = fingerprint(plan)
    return out


def _user(work: Work) -> User:
    if work.user is None:
        raise Conflict(code("JOBS", 14), "이 종류의 작업은 시킨 사람이 있어야 합니다.")
    return work.user


def _type(db: Session, slug: str) -> ObjectType:
    row = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
    if row is None:
        raise NotFound(code("OBJECTS", 10), f"타입을 찾을 수 없습니다: {slug}")
    return row


def _rows(work: Work) -> list[dict[str, Any]]:
    assert work.input_file is not None
    work.progress("읽기", 0, 0)
    name = work.input_file.name
    if not name.lower().endswith((".csv", ".json", ".tsv", ".txt")):
        raise Conflict(code("OBJECTS", 48), "CSV 나 JSON 파일만 받습니다.")
    return bulk.parse_file(name, work.input_file.data)


def _fingerprint_guard(work: Work) -> Callable[[bulk.Plan], None]:
    """적용 직전 — 미리 본 계획과 지금 계획이 다르면 **넣지 않는다.** 그 사이에 다른 사람이
    바꾼 것이 있다는 뜻이고, 옛 계획대로 넣으면 그 변경이 조용히 덮인다.

    `apply_*` 가 끝에서 커밋하므로 **쓰기 전**(`before_apply`)에 봐야 한다."""
    wanted = work.params.get("fingerprint")

    def check(plan: bulk.Plan) -> None:
        if wanted and fingerprint(plan) != wanted:
            raise Conflict(
                code("JOBS", 20),
                "미리 본 것과 달라졌습니다 — 그 사이에 누군가 바꿨습니다. 아무것도 넣지 "
                "않았으니 다시 미리 보고 적용하세요.",
            )

    return check


def objects_import(work: Work) -> dict[str, Any]:
    object_type = _type(work.db, str(work.params.get("type_slug") or ""))
    rows = _rows(work)
    owner = resolve_owner_workspace(
        work.db,
        _user(work),
        work.params.get("workspace_slug"),
        what="객체",
        code_value=code("OBJECTS", 15),
    )
    max_rows = get_settings().job_max_rows
    if work.params.get("apply"):
        plan = bulk.apply_objects(
            work.db,
            _user(work),
            object_type,
            rows,
            owner_workspace_id=owner,
            max_rows=max_rows,
            on_progress=work.progress,
            before_apply=_fingerprint_guard(work),
        )
        return _plan_result(plan, applied=plan.ok)
    plan = bulk.plan_objects(
        work.db,
        _user(work),
        object_type,
        rows,
        owner_workspace_id=owner,
        max_rows=max_rows,
        on_progress=work.progress,
    )
    return _plan_result(plan, applied=False)


def relations_import(work: Work) -> dict[str, Any]:
    object_type = _type(work.db, str(work.params.get("type_slug") or ""))
    rows = _rows(work)
    max_rows = get_settings().job_max_rows
    if work.params.get("apply"):
        plan = bulk.apply_relations(
            work.db,
            _user(work),
            object_type,
            rows,
            max_rows=max_rows,
            on_progress=work.progress,
            before_apply=_fingerprint_guard(work),
        )
        return _plan_result(plan, applied=plan.ok)
    plan = bulk.plan_relations(
        work.db, _user(work), object_type, rows, max_rows=max_rows, on_progress=work.progress
    )
    return _plan_result(plan, applied=False)


# --- 묶음 ------------------------------------------------------------------------


def bundle_import(work: Work) -> dict[str, Any]:
    """정의 · 객체 · 관계 한 묶음 — `bundles/services.run` 이 제 연결로 전부 아니면 무를
    지킨다. `work.db` 는 안 쓴다."""
    assert work.input_file is not None
    try:
        raw = json.loads(work.input_file.data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as caught:
        raise Conflict(code("BUNDLES", 3), f"묶음 파일을 읽을 수 없습니다: {caught}") from None
    bundle = BundleIn.model_validate({**raw, "apply": bool(work.params.get("apply"))})
    wanted = work.params.get("fingerprint")

    def guard(outcome: bundle_services.Outcome) -> None:
        if wanted and bundle_services.fingerprint(outcome) != wanted:
            raise Conflict(
                code("JOBS", 20),
                "미리 본 것과 달라졌습니다 — 그 사이에 누군가 바꿨습니다. 아무것도 넣지 "
                "않았으니 다시 미리 보고 적용하세요.",
            )

    outcome = bundle_services.run(
        _user(work),
        bundle,
        max_rows=get_settings().job_max_rows,
        on_progress=work.progress,
        before_commit=guard,
    )
    out = bundle_services.outcome_out(outcome).model_dump(mode="json")
    out["fingerprint"] = bundle_services.fingerprint(outcome)
    return out


def bundle_export(work: Work) -> dict[str, Any]:
    group = str(work.params.get("group") or "")
    work.progress("내보내기", 0, 0)
    body = bundle_export_service.export_group(work.db, group)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    work.emit(
        name=f"bundle-{group}-{stamp}.json",
        content_type="application/json",
        data=json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8"),
    )
    return {
        "group": group,
        "counts": body.get("counts") or {},
        "exported_at": body.get("exported_at"),
    }


# --- 내보내기 — 목록 그대로 파일로 ---------------------------------------------------


def _list_query(work: Work) -> tuple[ObjectType, Any]:
    """목록과 **같은 거르기** — `objects/routes._filtered` 를 그대로 부른다. 그 함수는 요청의
    쿼리 문자열을 읽으므로, 넣을 때 적어 둔 것을 요청 모양으로 되돌린다.

    여기서 import 하는 이유: `objects/routes` 가 `jobs/routes` 를 부르므로 위에서 import 하면
    돌고 돈다."""
    from starlette.datastructures import QueryParams

    from app.modules.objects import routes as objects_routes

    object_type = _type(work.db, str(work.params.get("type_slug") or ""))
    query = work.params.get("query") or {}
    pairs = [(key, value) for key, values in query.items() for value in values]
    request = SimpleNamespace(query_params=QueryParams(pairs))
    params = QueryParams(pairs)
    under = params.get("under")
    year = params.get("year")
    stmt = objects_routes._filtered(
        work.db,
        _user(work),
        object_type,
        request,  # type: ignore[arg-type]
        q=params.get("q") or None,
        status=params.get("status") or None,
        year=int(year) if year else None,
        under=uuid.UUID(under) if under else None,
        deep=(params.get("deep") or "true").lower() != "false",
    )
    return object_type, stmt


def objects_export(work: Work) -> dict[str, Any]:
    object_type, stmt = _list_query(work)
    defs = properties_of(work.db, object_type.id)
    work.progress("읽기", 0, 0)
    rows = list(work.db.scalars(apply_sort(stmt, object_type)))
    work.progress("변환", 0, len(rows))
    records = bulk.export_rows(work.db, defs, rows)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    fmt = str(work.params.get("format") or "csv")
    if fmt == "json":
        payload = json.dumps({"rows": records}, ensure_ascii=False, indent=1).encode("utf-8")
        work.emit(
            name=f"{object_type.slug}-{stamp}.json",
            content_type="application/json",
            data=payload,
        )
    else:
        work.emit(
            name=f"{object_type.slug}-{stamp}.csv",
            content_type="text/csv; charset=utf-8",
            data=bulk.to_csv(bulk.export_columns(defs), records),
        )
    work.progress("변환", len(rows), len(rows))
    return {"type_slug": object_type.slug, "rows": len(rows), "format": fmt}


def relations_export(work: Work) -> dict[str, Any]:
    object_type = _type(work.db, str(work.params.get("type_slug") or ""))
    work.progress("읽기", 0, 0)
    records = bulk.export_relations(work.db, _user(work), object_type)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    fmt = str(work.params.get("format") or "csv")
    if fmt == "json":
        payload = json.dumps({"rows": records}, ensure_ascii=False, indent=1).encode("utf-8")
        work.emit(
            name=f"{object_type.slug}-relations-{stamp}.json",
            content_type="application/json",
            data=payload,
        )
    else:
        work.emit(
            name=f"{object_type.slug}-relations-{stamp}.csv",
            content_type="text/csv; charset=utf-8",
            data=bulk.to_csv(list(bulk.RELATION_COLUMNS), records),
        )
    return {"type_slug": object_type.slug, "rows": len(records), "format": fmt}


# --- 온톨로지 통째로 ------------------------------------------------------------


def ontology_export(work: Work) -> dict[str, Any]:
    """구조와 **그 안에 채워진 객체까지** 한 파일로.

    작업인 이유: 구조만이면 순식간이지만 데이터가 붙으면 타입 수만큼 행을 읽는다. 요청 안에서
    만들면 큰 설치에서 먼저 끊기고, 끊긴 자리에는 아무것도 안 남는다.
    """
    from app.modules.ontology import export as ontology_service

    user = _user(work)
    fmt = str(work.params.get("format") or "xlsx")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    skipped: list[str] = []
    if fmt == "json":
        body = ontology_service.bundle(work.db, user, progress=work.progress, skipped=skipped)
        work.emit(
            name=f"ontology-full-{stamp}.json",
            content_type="application/json",
            data=json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8"),
        )
        return {
            "format": fmt,
            "types": len(body["ontology"]["types"]),
            "objects": sum(len(one["rows"]) for one in body["objects"]),
            "relations": sum(len(one["rows"]) for one in body["relations"]),
            # 건너뛴 것은 **작업 화면에 적힌다** — 조용히 빠지면
            # 그 파일은 「관계가 없다」 로 읽힌다.
            "skipped": skipped,
        }

    pages = ontology_service.data_pages(work.db, user, progress=work.progress)
    work.progress("파일 만들기", 0, 0)
    work.emit(
        name=f"ontology-full-{stamp}.xlsx",
        content_type=sheets.XLSX_TYPE,
        data=sheets.to_workbook(pages),
    )
    return {
        "format": fmt,
        "sheets": len(pages),
        "objects": sum(len(one.rows) for one in pages if one.name not in STRUCTURE_ONLY),
    }


#: 개요에 「내보낸 객체」 를 따로 세므로, 여기서는 구조 시트를 빼고 센다.
STRUCTURE_ONLY = ("개요", "묶음", "타입", "속성", "관계 종류", "관계 속성", "참조 칸", "관계")


# --- 데이터 소스 ----------------------------------------------------------------


def datasource_sync(work: Work) -> dict[str, Any]:
    """바깥 표를 읽어 그 타입의 객체로 — `datasources/services.sync` 가 스스로 커밋하고 기록을
    남긴다. 타이머가 넣은 작업은 시킨 사람이 없다(actor 「타이머」)."""
    from app.modules.datasources import services as datasource_services
    from app.modules.datasources.models import DataSource

    slug = str(work.params.get("slug") or "")
    source = work.db.scalar(select(DataSource).where(DataSource.slug == slug))
    if source is None:
        raise NotFound(code("DATASOURCES", 1), f"데이터 소스를 찾을 수 없습니다: {slug}")
    work.progress("읽기", 0, 0)
    result = datasource_services.sync(
        work.db, work.user, source, apply=bool(work.params.get("apply"))
    )
    return datasource_services.sync_out(result).model_dump(mode="json")


# --- 웹훅 ------------------------------------------------------------------------


def webhook_dispatch(work: Work) -> dict[str, Any]:
    """밀린 웹훅을 보낸다 — `webhooks/services.deliver_pending` 이 제 연결로 한 건씩 커밋한다.

    남은 수(다시 보낼 것)를 결과에 적는다. 워커가 `RETRY_AFTER_SECONDS` 뒤에 남은 것을 보고
    작업을 다시 넣는다 — 전에는 프로세스 안의 타이머였고, 그래서 재시작하면 사라졌다.
    """
    from app.modules.webhooks import services as webhook_services

    work.progress("보내기", 0, 0)
    left = webhook_services.dispatcher.deliver_pending()
    return {"left": left}


register(Kind("objects_import", "객체 일괄 입력", True, True, objects_import, True))
register(Kind("relations_import", "관계 일괄 입력", True, True, relations_import))
register(Kind("bundle_import", "묶음 가져오기", True, True, bundle_import))
register(Kind("bundle_export", "묶음 내보내기", False, False, bundle_export))
register(Kind("objects_export", "객체 내보내기", False, False, objects_export))
register(Kind("ontology_export", "온톨로지 통째로 내보내기", False, False, ontology_export))
register(Kind("relations_export", "관계 내보내기", False, False, relations_export))
register(
    Kind(
        "webhook_dispatch",
        "웹훅 보내기",
        False,
        False,
        webhook_dispatch,
        allow_system=True,
    )
)
register(
    Kind(
        "datasource_sync",
        "데이터 소스 동기화",
        False,
        False,
        datasource_sync,
        allow_system=True,
    )
)
