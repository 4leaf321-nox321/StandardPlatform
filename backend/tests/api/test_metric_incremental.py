"""지표 증분 — **바뀐 기간만 다시 센 값이 전부 다시 센 값과 같다**(`metrics/incremental.py`).

증분은 빠르려고 있는 것이고, 틀리면 아무 쓸모가 없다. 그래서 이 시험은 늘 같은 셀을 두 번 —
바뀐 기간만 센 것과 전부 다시 센 것 — 만들어 한 줄도 안 다른지 본다. 그리고 증분이 **못
보는 변경**(다른 객체 너머 · 감사 없는 변경 · 정의 변경)에서는 전량으로 가고 그 까닭을
남기는지 본다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.api.conftest import Signed, finish_job
from tests.api.test_metrics import _define, _world
from tests.api.test_ontology import _make_object


def _recount(
    client: TestClient, admin: Signed, slug: str, *, reason: str = "timer"
) -> dict[str, Any]:
    """그 까닭으로 작업을 넣어 돌리고 — 가장 최근 실행 기록."""
    from app.database import SessionLocal
    from app.modules.metrics import services

    with SessionLocal() as db:
        metric = services.get(db, slug)
        job = services.enqueue_recompute(db, metric, user=None, reason=reason)
        db.commit()
        job_id = str(job.id)
    done = finish_job(client, admin, {"id": job_id})
    assert done["status"] == "done", done
    runs = client.get(f"/api/metrics/{slug}/runs", headers=admin.headers)
    assert runs.status_code == 200, runs.text
    return dict(runs.json()[0])


def _clock_went_back(type_slug: str, watermark: str) -> bool:
    """**시계가 뒤로 튀었나** — 계산 전에 넣은 기록의 시각이 그 계산의 워터마크보다 뒤다.

    이 PC(WSL)의 시계는 몇 초씩 뒤로 튄다(2026-10-08 실측 — 45초에 2.9초). 그러면 먼저 넣은
    기록이 워터마크 「뒤」 로 읽혀 증분이 그 기간을 한 번 더 센다 — 틀린 것이 아니라 넉넉한
    쪽이다(값은 같다). 그때만 「아무 기간도 안 센다」 를 보지 않는다."""
    from datetime import datetime

    from app.database import SessionLocal
    from app.modules.objects.models import ObjectInstance
    from app.modules.ontology.models import ObjectType

    with SessionLocal() as db:
        latest = db.scalar(
            select(func.max(ObjectInstance.updated_at))
            .join(ObjectType, ObjectType.id == ObjectInstance.type_id)
            .where(ObjectType.slug == type_slug)
        )
    return latest is not None and latest > datetime.fromisoformat(watermark)


def _cells(slug: str) -> list[tuple[Any, ...]]:
    """셀이 든 실행의 셀 전부 — 실행 번호는 빼고(증분과 전량은 번호가 다르다)."""
    from app.database import SessionLocal
    from app.modules.metrics.models import MetricDef, MetricValue

    with SessionLocal() as db:
        metric = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
        assert metric is not None
        run = metric.cells_run_id or metric.current_run_id
        rows = db.execute(
            select(
                MetricValue.cell_hash,
                MetricValue.workspace_id,
                MetricValue.period,
                MetricValue.cohort,
                MetricValue.age,
                MetricValue.dims,
                MetricValue.count,
                MetricValue.value_count,
                MetricValue.sum,
                MetricValue.min,
                MetricValue.max,
            ).where(MetricValue.metric_id == metric.id, MetricValue.run_id == run)
        ).all()
    return sorted((tuple(row) for row in rows), key=str)


def _ids(type_slug: str) -> dict[str, uuid.UUID]:
    from app.database import SessionLocal
    from app.modules.objects.models import ObjectInstance
    from app.modules.ontology.models import ObjectType

    with SessionLocal() as db:
        return {
            label: object_id
            for label, object_id in db.execute(
                select(ObjectInstance.label, ObjectInstance.id)
                .join(ObjectType, ObjectType.id == ObjectInstance.type_id)
                .where(ObjectType.slug == type_slug)
            )
        }


def _patch(
    client: TestClient, admin: Signed, type_slug: str, object_id: uuid.UUID, **body: Any
) -> None:
    got = client.patch(
        f"/api/objects/{type_slug}/{object_id}", json=body, headers=admin.headers
    )
    assert got.status_code == 200, got.text


def _own_spec() -> dict[str, Any]:
    """자기 칸만 쓰는 지표 — 시간 · 코호트 · 기준 둘 · 합."""
    return {
        "measure": "sum",
        "measure_field": "properties.cost",
        "time": {"address": "properties.received", "grain": "month"},
        "cohort": {"address": "properties.sold", "grain": "month"},
        "dimensions": [
            {"name": "symptom", "address": "properties.symptom"},
            {"name": "factory", "address": "properties.factory"},
        ],
    }


def test_바뀐_기간만_다시_센_값이_전부_다시_센_값과_같다(
    client: TestClient, admin: Signed
) -> None:
    w = _world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_own_spec(), label="증분")
    slug = metric["slug"]
    first = client.get(f"/api/metrics/{slug}/runs", headers=admin.headers).json()[0]
    assert first["mode"] == "full" and first["note"] == "처음 센다"

    # 바뀐 것이 없으면 아무 기간도 안 센다 — 그래도 실행은 남는다(워터마크가 나아간다).
    idle = _recount(client, admin, slug)
    assert idle["mode"] == "incremental"
    if not _clock_went_back(w["case"], first["watermark"]):
        assert idle["periods"] == []
    assert idle["cells"] == first["cells"] and idle["rows"] == first["rows"]

    cases = _ids(w["case"])
    # 1월 → 3월로 옮긴다(옛 기간은 감사 기록에서) · 같은 기간 안에서 기준만 · 지운다 ·
    # 못 읽던 날짜(2026-02-30)를 고친다(날짜 없음 → 1월) · 2월에 하나 더.
    _patch(client, admin, w["case"], cases["건1"], properties={"received": "2026-03-01"})
    _patch(client, admin, w["case"], cases["건2"], properties={"symptom": "누수"})
    gone = client.delete(f"/api/objects/{w['case']}/{cases['건4']}", headers=admin.headers)
    assert gone.status_code == 204, gone.text
    _patch(client, admin, w["case"], cases["건6"], properties={"received": "2026-01-31"})
    _make_object(
        client,
        admin,
        w["case"],
        label="건9",
        properties={"received": "2026-02-11", "symptom": "발열", "factory": "F2", "cost": 7},
    )

    step = _recount(client, admin, slug)
    assert step["mode"] == "incremental", step["note"]
    assert step["periods"] == ["2026-01-01", "2026-02-01", "2026-03-01", None]
    assert "바뀐 기록 5건" in step["note"]
    increment = _cells(slug)

    full = _recount(client, admin, slug, reason="manual")
    assert full["mode"] == "full" and full["note"] == "전량 요청"
    assert _cells(slug) == increment
    assert full["rows"] == step["rows"] and full["cells"] == step["cells"]
    assert full["stats"] == step["stats"]

    # 한 기록을 두 번 고쳐도(1월 → 2월 → 3월) 그 사이의 기간까지 다시 센다.
    _patch(client, admin, w["case"], cases["건3"], properties={"received": "2026-01-20"})
    _patch(client, admin, w["case"], cases["건3"], properties={"received": "2026-03-21"})
    twice = _recount(client, admin, slug)
    assert twice["mode"] == "incremental"
    assert set(twice["periods"]) == {"2026-01-01", "2026-02-01", "2026-03-01"}
    increment = _cells(slug)
    _recount(client, admin, slug, reason="manual")
    assert _cells(slug) == increment


def test_증분이_못_보는_변경이면_전량으로_가고_까닭을_남긴다(
    client: TestClient, admin: Signed
) -> None:
    from app.database import SessionLocal
    from app.modules.metrics.models import MetricDef
    from app.modules.objects.models import ObjectInstance

    w = _world(client, admin)
    spec = {
        "measure": "count",
        "time": {"address": "properties.received", "grain": "month"},
        "dimensions": [{"name": "base_model", "address": "ref.model.base"}],
    }
    metric = _define(client, admin, source=w["case"], spec=spec, label="참조 너머")
    slug = metric["slug"]
    assert _recount(client, admin, slug)["mode"] == "incremental"

    # 참조 너머(SKU 의 기본 모델)가 바뀌면 원천 기록의 시각은 그대로다 — 전량.
    skus = _ids(w["sku"])
    _patch(client, admin, w["sku"], skus["S-1"], properties={"base": w["a_base"]})
    run = _recount(client, admin, slug)
    assert run["mode"] == "full" and "너머" in run["note"]
    assert _recount(client, admin, slug)["mode"] == "incremental"

    # 감사 없이 바뀐 기록(백필 등) — 옛 날짜를 모른다.
    cases = _ids(w["case"])
    with SessionLocal() as db:
        row = db.get(ObjectInstance, cases["건3"])
        assert row is not None
        row.properties = {**row.properties, "received": "2026-03-30"}
        db.commit()
    run = _recount(client, admin, slug)
    assert run["mode"] == "full" and "옛 날짜를 모른다" in run["note"]

    # 정의가 바뀌었다 — 셀을 만든 정의와 지문이 다르다.
    with SessionLocal() as db:
        found = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
        assert found is not None
        found.spec = {
            **found.spec,
            "time": {"address": "properties.received", "grain": "year"},
        }
        db.commit()
    run = _recount(client, admin, slug)
    assert run["mode"] == "full" and "정의가 바뀌었다" in run["note"]

    # 시간 칸이 없는 지표는 늘 전량.
    flat = _define(
        client,
        admin,
        source=w["case"],
        spec={
            "measure": "count",
            "dimensions": [{"name": "symptom", "address": "properties.symptom"}],
        },
        label="시간 없음",
    )
    run = _recount(client, admin, flat["slug"])
    assert run["mode"] == "full" and "시간 칸" in run["note"]


def test_날짜_칸_색인을_세우고_증분이_그것을_탄다(client: TestClient, admin: Signed) -> None:
    """색인 식은 계산 쪽 식(`incremental.date_expr`)과 글자 그대로 같아야 플래너가 맞춰 본다 —
    어긋나면 색인은 서 있는데 아무도 안 쓴다(조용히 느려질 뿐이라 아무도 모른다).

    시험 DB 에는 다른 시험의 지표가 쌓여 있어 `sync` 로 세우면 그것들 것까지 선다 — 이 지표의
    것 하나만 같은 문장(`create_sql`)으로 세운다. 작은 타입이라 기본 `sync` 는 그것을
    지운다."""
    from datetime import date

    from sqlalchemy import func, text

    from app.database import SessionLocal, engine
    from app.modules.metrics import compute, incremental, timeindex
    from app.modules.metrics.models import MetricDef
    from app.modules.objects.models import ObjectInstance

    w = _world(client, admin)
    metric = _define(client, admin, source=w["case"], spec=_own_spec(), label="색인")
    with SessionLocal() as db:
        found = db.scalar(select(MetricDef).where(MetricDef.slug == metric["slug"]))
        assert found is not None
        source = found.source_type_id
        name = timeindex.name_of(source, "received")
        assert timeindex.wanted(db, min_rows=0)[name] == (source, "received")
        assert name not in timeindex.wanted(db)  # 작은 타입에는 안 세운다
        # 열린 트랜잭션이 있으면 CONCURRENTLY 가 그것이 끝나기를 기다린다 — 닫고, 닫은 뒤에는
        # 세션의 객체를 건드리지 않는다(건드리면 다시 연다).
        db.rollback()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("SET lock_timeout = '20s'"))
            connection.execute(text(timeindex.create_sql(name, source, "received")))
        assert timeindex.existing(db).get(name) is True

        built = compute.built_of(db, found)
        assert built.time is not None
        periods: list[date | None] = [date(2026, 2, 1), None]
        where = incremental.period_filter(built, periods).compile(
            dialect=engine.dialect, compile_kwargs={"literal_binds": True}
        )
        db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            db.scalars(
                text(
                    "EXPLAIN SELECT count(*) FROM objects "
                    f"WHERE deleted_at IS NULL AND {where}"
                )
            )
        )
        assert name in plan, plan
        # 범위로 건 것이 기간 식과 같은 뜻이다 — 2월 둘(건2 · 건3) + 못 읽는 날짜 하나(건6).
        counted = db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.deleted_at.is_(None), incremental.period_filter(built, periods)
            )
        )
        by_period = db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.type_id == built.source.id,
                ObjectInstance.deleted_at.is_(None),
                (built.time.expr == date(2026, 2, 1)) | built.time.expr.is_(None),
            )
        )
        # 색인이 없을 때의 식(기간 식을 한 번 계산해 견준다)도 같은 기록을 고른다.
        unindexed = db.scalar(
            select(func.count())
            .select_from(ObjectInstance)
            .where(
                ObjectInstance.type_id == built.source.id,
                ObjectInstance.deleted_at.is_(None),
                incremental.period_filter(built, periods, indexed=False),
            )
        )
        assert counted == by_period == unindexed == 3
        assert timeindex.has(db, source, "received") is True
        db.rollback()

        done = timeindex.sync(engine, db)
        assert name in done.dropped, done
        assert name not in timeindex.existing(db)
        assert timeindex.has(db, source, "received") is False


def test_도는_계산에는_합치지_않고_기다리는_것만_합친다(
    client: TestClient, admin: Signed
) -> None:
    """도는 계산은 이미 원천을 읽기 시작해 그 뒤의 적재를 못 본다 — 거기에 합치면 적재 뒤
    계산이 사라졌다(2026-10-08). 기다리는 작업은 합치고, 사람의 「다시 세기」 면 전량으로
    올린다."""
    from app.database import SessionLocal
    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job
    from app.modules.metrics import services
    from app.modules.metrics.models import MetricDef

    w = _world(client, admin)
    metric = _define(
        client, admin, source=w["case"], spec=_own_spec(), label="합치기", recompute=False
    )
    with SessionLocal() as db:
        found = db.scalar(select(MetricDef).where(MetricDef.slug == metric["slug"]))
        assert found is not None
        waiting = services.enqueue_recompute(db, found, user=None, reason="timer")
        db.commit()
        # 기다리는 것에는 합친다 — 「다시 세기」 면 전량으로 올려서.
        again = services.enqueue_recompute(db, found, user=None, reason="manual")
        db.commit()
        assert again.id == waiting.id and again.params.get("full") is True
        # 도는 중이면 새로 넣는다 — 적재 뒤 훅도 마찬가지.
        db.get(Job, waiting.id).status = "running"  # type: ignore[union-attr]
        db.commit()
        made = services.enqueue_for_type(db, found.source_type_id, reason="import:x")
        db.commit()
        assert [one.id for one in made] != [waiting.id] and len(made) == 1
        # 치운다 — 남기면 뒤 시험이 남의 작업을 집는다.
        db.get(Job, waiting.id).status = "cancelled"  # type: ignore[union-attr]
        db.get(Job, made[0].id).status = "cancelled"  # type: ignore[union-attr]
        db.commit()
    while job_services.process_one("test-worker"):
        pass
