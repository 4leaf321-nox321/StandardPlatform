"""규모 실측 — 기록 타입 하나가 200만 건일 때 화면 · MCP 의 길이 견디나(ADR 0010).

개발 DB 와 **다른** 데이터베이스(`<이름>_scale`)를 만들어 심고, 진짜 앱(TestClient)으로 잰다.
개발 PC 의 Postgres 기본 설정(shared_buffers 128MB · work_mem 4MB)이다 — 운영 서버가 더 크면
더 빠르겠지만, **질의의 모양**(인덱스를 타나, 표 전체를 읽나)은 같다. 그것을 보려는 것이다.

    cd backend
    PYTHONPATH=. .venv/bin/python scripts/scale_rehearsal.py create   # 만들고 심는다(수 분)
    PYTHONPATH=. .venv/bin/python scripts/scale_rehearsal.py measure  # 잰다 → 표
    PYTHONPATH=. .venv/bin/python scripts/scale_rehearsal.py drop     # 지운다

`--rows` 로 기록 수를 바꾼다(기본 2,000,000). **이 DB 에만 쓴다** — 이름이 `_scale` 로
끝나지 않으면 멈춘다. 질의 하나가 `--timeout` 초(기본 120)를 넘으면 DB 가 끊고, 표에 그
오류가 적힌다.

심는 것:

    부서 10 · 시스템 관리자 1 · 부서 관리자 1(첫 부서)
    plm_project 120 · plm_task 5,000(→ 프로젝트)
    plm_model 6,000(→ 과제, base_code 는 셋에 하나꼴로 같다)
    svc_case N — 속성 31칸(고를 값 · 날짜 · 숫자 · 예/아니오 · 글), model → plm_model.
        **5% 는 한 모델(뜨거운 모델)** 을 가리키고 나머지는 고르게 — 인기 모델의
        상세가 10만 건을 끌어안는 경우를 같이 본다.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from app.config import Settings

HERE = Path(__file__).resolve().parents[1]
PASSWORD = "scale-rehearsal-password"
ADMIN = "scale-admin@example.local"
MANAGER = "scale-manager@example.local"
WORKSPACES = 10
PROJECTS, TASKS, MODELS = 120, 5_000, 6_000
SYMPTOMS = [f"S{n:02d}" for n in range(40)]
CENTERS = [f"C{n:02d}" for n in range(30)]
COUNTRIES = [f"N{n:02d}" for n in range(20)]
HANDLINGS = [f"H{n}" for n in range(8)]
SERIES = [f"X{n}" for n in range(10)]
TEXT_FIELDS = [f"t{n:02d}" for n in range(1, 19)]


def _scale_url() -> str:
    base, _, name = Settings().database_url.rpartition("/")
    return f"{base}/{name.split('?')[0]}_scale"


def _guard(url: str) -> str:
    name = url.rpartition("/")[2].split("?")[0]
    if not name.endswith("_scale"):
        raise SystemExit(f"실험용 DB 가 아닙니다: {name} — 이름이 _scale 로 끝나야 합니다.")
    return name


def _maintenance(url: str) -> Any:
    base = url.rpartition("/")[0]
    return create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")


# --- 만들기 -----------------------------------------------------------------------


def create(rows: int) -> None:
    url = _scale_url()
    name = _guard(url)
    with _maintenance(url).connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
        ).scalar()
        if exists:
            raise SystemExit(f"{name} 이 이미 있습니다 — 먼저 `drop` 하세요.")
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    print(f"[1/5] {name} 을 만들었습니다 — 마이그레이션")
    env = {**os.environ, "DATABASE_URL": url, "EXTENSIONS": ""}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=HERE, env=env, check=True
    )

    os.environ["DATABASE_URL"] = url
    os.environ["EXTENSIONS"] = ""
    from fastapi.testclient import TestClient

    from app.database import SessionLocal
    from app.main import app
    from app.modules.accounts.models import User
    from app.modules.auth import security
    from app.modules.workspaces.models import Workspace, WorkspaceMember

    print("[2/5] 부서 · 계정")
    with SessionLocal() as db:
        spaces = [Workspace(slug=f"ws{n:02d}", name=f"부서 {n:02d}") for n in range(1, 11)]
        db.add_all(spaces)
        db.flush()
        for email, admin, role in ((ADMIN, True, "manager"), (MANAGER, False, "manager")):
            user = User(
                email=email,
                password_hash=security.hash_password(PASSWORD),
                display_name=email.split("@")[0],
                status="active",
                is_system_admin=admin,
                home_workspace_id=spaces[0].id,
            )
            db.add(user)
            db.flush()
            db.add(WorkspaceMember(workspace_id=spaces[0].id, user_id=user.id, role=role))
        db.commit()

    print("[3/5] 정의(가져오기)")
    client = TestClient(app)
    headers = _login(client, ADMIN)
    got = client.post(
        "/api/ontology/import", params={"dry_run": "false"}, json=_schema(), headers=headers
    )
    if got.status_code != 200 or not got.json().get("applied"):
        raise SystemExit(f"정의를 못 넣었습니다: {got.text[:500]}")

    engine = create_engine(url)
    with engine.begin() as connection:
        ids: dict[str, Any] = {
            slug: one
            for slug, one in connection.execute(text("SELECT slug, id FROM object_types"))
        }
        print("[4/5] 축 — 프로젝트 · 과제 · 개발모델")
        connection.execute(
            text(
                "INSERT INTO objects (id, type_id, key, label, created_at, updated_at) "
                "SELECT gen_random_uuid(), :t, 'P' || lpad(n::text, 4, '0'), "
                "'프로젝트 ' || n, now(), now() - make_interval(secs => n) "
                "FROM generate_series(1, CAST(:count AS int)) n"
            ),
            {"t": ids["plm_project"], "count": PROJECTS},
        )
        connection.execute(
            text(
                "WITH p AS (SELECT array_agg(id::text ORDER BY key) a FROM objects "
                "WHERE type_id = :project) "
                "INSERT INTO objects "
                "(id, type_id, key, label, properties, created_at, updated_at) "
                "SELECT gen_random_uuid(), :t, 'T' || lpad(n::text, 5, '0'), '과제 ' || n, "
                "jsonb_build_object('project', p.a[1 + n % array_length(p.a, 1)]), now(), "
                "now() - make_interval(secs => n) "
                "FROM generate_series(1, CAST(:count AS int)) n, p"
            ),
            {"t": ids["plm_task"], "project": ids["plm_project"], "count": TASKS},
        )
        connection.execute(
            text(
                "WITH k AS (SELECT array_agg(id::text ORDER BY key) a FROM objects "
                "WHERE type_id = :task) "
                "INSERT INTO objects "
                "(id, type_id, key, label, properties, created_at, updated_at) "
                "SELECT gen_random_uuid(), :t, 'SM-X' || lpad(n::text, 5, '0') || '_KOR_01', "
                "'SM-X' || lpad(n::text, 5, '0') || '_KOR_01', "
                "jsonb_build_object('task', k.a[1 + n % array_length(k.a, 1)], "
                "'base_code', 'SM-X' || lpad((n / 3)::text, 5, '0'), "
                "'series', 'X' || (n % 10)), now(), now() - make_interval(secs => n) "
                "FROM generate_series(1, CAST(:count AS int)) n, k"
            ),
            {"t": ids["plm_model"], "task": ids["plm_task"], "count": MODELS},
        )

    print(f"[5/5] 기록 {rows:,}건 — 20만 건씩")
    chunk = 200_000
    started = time.perf_counter()
    for low in range(1, rows + 1, chunk):
        high = min(rows, low + chunk - 1)
        with engine.begin() as connection:
            connection.execute(
                text(_CASES_SQL),
                {"t": ids["svc_case"], "model": ids["plm_model"], "lo": low, "hi": high},
            )
        print(f"      {high:,} — {time.perf_counter() - started:.0f}초", flush=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE"))
        size = connection.execute(
            text("SELECT pg_size_pretty(pg_database_size(current_database()))")
        ).scalar()
    print(f"끝 — DB 크기 {size}. 이제 `measure`.")


_CASES_SQL = (
    "WITH m AS (SELECT array_agg(id::text ORDER BY key) a FROM objects "
    "WHERE type_id = :model), "
    "w AS (SELECT array_agg(id ORDER BY slug) a FROM workspaces WHERE slug LIKE 'ws%') "
    "INSERT INTO objects (id, type_id, key, label, properties, owner_workspace_id, "
    "created_at, updated_at) "
    "SELECT gen_random_uuid(), :t, 'C' || lpad(n::text, 8, '0'), "
    "'C' || lpad(n::text, 8, '0'), "
    "jsonb_build_object("
    # 5% 는 뜨거운 모델(첫 모델) — 나머지는 고르게.
    "'model', CASE WHEN n % 20 = 0 THEN m.a[1] "
    "ELSE m.a[1 + (n::bigint * 7919) % array_length(m.a, 1)] END, "
    "'symptom', 'S' || lpad((n % 40)::text, 2, '0'), "
    "'center', 'C' || lpad((n % 30)::text, 2, '0'), "
    "'country', 'N' || lpad((n % 20)::text, 2, '0'), "
    "'handling', 'H' || (n % 8), "
    "'service_date', to_char(date '2020-01-01' + (n % 2000), 'YYYY-MM-DD'), "
    "'production_date', to_char(date '2019-01-01' + (n % 2000), 'YYYY-MM-DD'), "
    "'cost', (n % 1000) * 10, "
    "'term', n % 36, "
    "'warranty', n % 3 = 0, "
    "'serial_no', 'SN' || n, "
    "'remark', substr(md5(n::text), 1, 20)"
    + "".join(
        f", '{one}', substr(md5((n + {i})::text), 1, 12)" for i, one in enumerate(TEXT_FIELDS)
    )
    + "), w.a[1 + n % 10], now(), now() - make_interval(secs => n) "
    "FROM generate_series(CAST(:lo AS int), CAST(:hi AS int)) n, m, w"
)


def _enum(key: str, label: str, options: list[str]) -> dict[str, Any]:
    return {"key": key, "label": label, "data_type": "enum", "enum_options": options}


def _schema() -> dict[str, Any]:
    case_props: list[dict[str, Any]] = [
        {
            "key": "model",
            "label": "개발모델",
            "data_type": "object_ref",
            "ref_type_slug": "plm_model",
            "inverse_label": "시장 서비스",
        },
        _enum("symptom", "증상", SYMPTOMS),
        _enum("center", "센터", CENTERS),
        _enum("country", "국가", COUNTRIES),
        _enum("handling", "처리유형", HANDLINGS),
        {"key": "service_date", "label": "서비스일자", "data_type": "date"},
        {"key": "production_date", "label": "생산일자", "data_type": "date"},
        {"key": "cost", "label": "비용", "data_type": "number"},
        {"key": "term", "label": "Term", "data_type": "number"},
        {"key": "warranty", "label": "워런티", "data_type": "bool"},
        {"key": "serial_no", "label": "S/N", "data_type": "text"},
        {"key": "remark", "label": "비고", "data_type": "text"},
        *({"key": one, "label": f"칸 {one}", "data_type": "text"} for one in TEXT_FIELDS),
    ]
    return {
        "types": [
            {"slug": "plm_project", "label": "프로젝트", "key_policy": "required"},
            {
                "slug": "plm_task",
                "label": "과제",
                "key_policy": "required",
                "properties": [
                    {
                        "key": "project",
                        "label": "프로젝트",
                        "data_type": "object_ref",
                        "ref_type_slug": "plm_project",
                    }
                ],
            },
            {
                "slug": "plm_model",
                "label": "개발모델",
                "key_policy": "required",
                "properties": [
                    {
                        "key": "task",
                        "label": "과제",
                        "data_type": "object_ref",
                        "ref_type_slug": "plm_task",
                    },
                    {"key": "base_code", "label": "기본 코드", "data_type": "text"},
                    _enum("series", "시리즈", SERIES),
                ],
            },
            {
                "slug": "svc_case",
                "label": "시장 서비스",
                "key_policy": "required",
                "temporal_kind": "evergreen",
                "properties": case_props,
            },
        ]
    }


def _login(client: Any, email: str) -> dict[str, str]:
    got = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    if got.status_code != 200:
        raise SystemExit(f"로그인 실패: {got.text[:300]}")
    return {"Authorization": f"Bearer {got.json()['access_token']}"}


# --- 재기 -------------------------------------------------------------------------


def measure(timeout: int, repeat: int, only: str | None) -> None:
    url = _scale_url()
    _guard(url)
    joiner = "&" if "?" in url else "?"
    os.environ["DATABASE_URL"] = (
        f"{url}{joiner}options=-c%20statement_timeout%3D{timeout * 1000}"
    )
    os.environ["EXTENSIONS"] = ""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    admin = _login(client, ADMIN)
    manager = _login(client, MANAGER)
    engine = create_engine(url)
    with engine.connect() as connection:
        hot = connection.execute(
            text(
                "SELECT o.id FROM objects o JOIN object_types t ON t.id = o.type_id "
                "WHERE t.slug = 'plm_model' ORDER BY o.key LIMIT 1"
            )
        ).scalar()
        typical = connection.execute(
            text(
                "SELECT o.id FROM objects o JOIN object_types t ON t.id = o.type_id "
                "WHERE t.slug = 'plm_model' ORDER BY o.key OFFSET 3001 LIMIT 1"
            )
        ).scalar()
        cases = connection.execute(
            text(
                "SELECT count(*) FROM objects o JOIN object_types t ON t.id = o.type_id "
                "WHERE t.slug = 'svc_case'"
            )
        ).scalar()
    print(
        f"기록 {cases:,}건 · 뜨거운 모델 {hot} · 보통 모델 {typical} · 질의 상한 {timeout}초\n"
    )

    def get(
        path: str,
        who: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        **more: Any,
    ) -> Callable[[], Any]:
        merged = {**(params or {}), **more}
        return lambda: client.get(path, params=merged, headers=who or admin)

    def post(path: str, body: Any, who: dict[str, str] = admin) -> Callable[[], Any]:
        return lambda: client.post(path, json=body, headers=who)

    cases_path = "/api/objects/svc_case"
    checks: list[tuple[str, str, Callable[[], Any]]] = [
        ("list", "목록 첫 쪽(기본 정렬)", get(cases_path, limit=50)),
        ("list", "목록 첫 쪽 — 부서 관리자(가시성)", get(cases_path, who=manager, limit=50)),
        (
            "list",
            "목록 — 고를 값 조건(증상=S07)",
            get(cases_path, limit=50, params={"f.symptom.eq": "S07"}),
        ),
        (
            "list",
            "목록 — 참조 조건(보통 모델)",
            get(cases_path, limit=50, params={"f.model.eq": str(typical)}),
        ),
        (
            "list",
            "목록 — 참조 조건(뜨거운 모델)",
            get(cases_path, limit=50, params={"f.model.eq": str(hot)}),
        ),
        (
            "list",
            "목록 — 참조 너머 칸(모델 시리즈=X3)",
            get(cases_path, limit=50, params={"f.ref.model.series.eq": "X3"}),
        ),
        ("list", "목록 — 글 검색 q", get(cases_path, limit=50, q="C0012345")),
        (
            "profile",
            "개발모델 상세(보통) — 관련 객체",
            get(f"/api/objects/plm_model/{typical}"),
        ),
        ("profile", "개발모델 상세(뜨거움) — 관련 객체", get(f"/api/objects/plm_model/{hot}")),
        (
            "profile",
            "가리키는 것(삭제 전, 보통)",
            get(f"/api/objects/plm_model/{typical}/references"),
        ),
        (
            "graph",
            "그래프 이웃(보통 모델, 1걸음)",
            get("/api/graph/neighborhood", focus=str(typical), depth=1),
        ),
        (
            "graph",
            "그래프 이웃(뜨거운 모델, 1걸음)",
            get("/api/graph/neighborhood", focus=str(hot), depth=1),
        ),
        ("graph", "그래프 개요", get("/api/graph/overview")),
        (
            "summary",
            "통계 — 증상별",
            get(f"{cases_path}/summary", group_by="properties.symptom"),
        ),
        (
            "summary",
            "통계 — 모델별(참조)",
            get(f"{cases_path}/summary", group_by="properties.model"),
        ),
        (
            "summary",
            "통계 — 모델 시리즈별(참조 너머)",
            get(f"{cases_path}/summary", group_by="ref.model.series"),
        ),
        ("search", "통합 검색", get("/api/search", q="C0012345")),
        (
            "search",
            "이름 풀이(MCP object_resolve)",
            get(f"{cases_path}/resolve", name="C0012345"),
        ),
        ("quality", "품질 보고", get("/api/objects/quality/report")),
        (
            "quality",
            "홈 「남은 일」(부서 관리자)",
            get("/api/server/maintenance", who=manager),
        ),
        ("ontology", "타입 목록(건수)", get("/api/ontology/types")),
        (
            "rdf",
            "SPARQL — 개발모델만",
            post(
                "/api/rdf/query",
                {
                    "query": "SELECT (COUNT(?x) AS ?n) WHERE { ?x a ?t }",
                    "types": ["plm_model"],
                },
            ),
        ),
    ]
    results: list[tuple[str, str, str, str]] = []
    for area, label, call in checks:
        if only and only not in area:
            continue
        times: list[float] = []
        note = ""
        for _ in range(repeat):
            started = time.perf_counter()
            response = call()
            times.append(time.perf_counter() - started)
            if response.status_code >= 400:
                body = response.text[:160].replace("\n", " ")
                note = f"HTTP {response.status_code} {body}"
                break
            if times[-1] > 20:
                # 한 번에 20초를 넘으면 되풀이하지 않는다 — 답은 이미 「목표선 밖」 이다.
                break
        if not note:
            note = _shape(response.json())
        median = statistics.median(times)
        results.append((area, label, f"{median:.2f}초", note))
        print(f"{median:8.2f}초  {label}  {note}", flush=True)

    if not only or "import" in (only or ""):
        results.append(_import_plan(client, admin, url, 100_000))
    if not only or "infer" in (only or ""):
        results.append(_infer_plan(client, admin, url, 5_000))

    print("\n| 영역 | 무엇 | 중앙값 | 비고 |\n| --- | --- | --- | --- |")
    for area, label, took, note in results:
        print(f"| {area} | {label} | {took} | {note} |")
    print()
    _explain(engine, typical)


def _shape(body: Any) -> str:
    """응답이 무엇을 담았나 한 마디 — 수만."""
    if isinstance(body, dict):
        for key in ("total", "count"):
            if isinstance(body.get(key), int):
                return f"{key}={body[key]}"
        for key in ("items", "rows", "nodes", "buckets", "related"):
            if isinstance(body.get(key), list):
                return f"{key} {len(body[key])}"
    if isinstance(body, list):
        return f"{len(body)}줄"
    return ""


def _import_plan(
    client: Any, headers: dict[str, str], url: str, count: int
) -> tuple[str, str, str, str]:
    """일괄 입력 10만 행의 **계획** — 작업으로 넣고 이 프로세스가 워커가 되어 돈다."""
    from app.modules.jobs import services as job_services

    with create_engine(url).connect() as connection:
        models = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT o.key FROM objects o JOIN object_types t ON t.id = o.type_id "
                    "WHERE t.slug = 'plm_model' ORDER BY o.key LIMIT 500"
                )
            )
        ]
    rows = [
        {
            "key": f"NEW{n:07d}",
            "label": f"NEW{n:07d}",
            "model": models[n % len(models)],
            "symptom": SYMPTOMS[n % 40],
            "cost": n % 1000,
        }
        for n in range(count)
    ]
    started = time.perf_counter()
    made = client.post(
        "/api/jobs",
        data={
            "kind": "objects_import",
            "params": json.dumps({"type_slug": "svc_case"}),
            "workspace_slug": "ws01",
        },
        files={"file": ("rows.json", json.dumps({"rows": rows}).encode(), "application/json")},
        headers=headers,
    )
    if made.status_code >= 400:
        return (
            "import",
            f"일괄 입력 계획 {count:,}행",
            "-",
            f"HTTP {made.status_code} {made.text[:160]}",
        )
    job_services.process_one("scale-rehearsal")
    took = time.perf_counter() - started
    job = client.get(f"/api/jobs/{made.json()['id']}", headers=headers).json()
    note = job.get("status", "?")
    if job.get("error"):
        note += f" — {str(job['error'])[:160]}"
    else:
        counts = (job.get("result") or {}).get("counts")
        note += f" — {counts}"
    print(f"{took:8.2f}초  일괄 입력 계획 {count:,}행  {note}", flush=True)
    return ("import", f"일괄 입력 계획 {count:,}행", f"{took:.2f}초", note)


def _infer_plan(
    client: Any, headers: dict[str, str], url: str, count: int
) -> tuple[str, str, str, str]:
    """표에서 타입 추론 + 참조 후보(ADR 0009) — 모델 · 과제 · **200만 건 기록**을 가리키는 열과
    아무것도 안 가리키는 글 열이 섞인 표."""
    with create_engine(url).connect() as connection:

        def keys(slug: str) -> list[str]:
            return [
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT o.key FROM objects o JOIN object_types t ON t.id = o.type_id "
                        "WHERE t.slug = :slug ORDER BY o.key LIMIT 3000"
                    ),
                    {"slug": slug},
                )
            ]

        models, tasks, cases = keys("plm_model"), keys("plm_task"), keys("svc_case")
    header = ["건번호", "이름", "모델", "과제", "원건", "증상", "비용"] + [
        f"메모{n}" for n in range(10)
    ]
    lines = [",".join(header)]
    for n in range(count):
        memo = [f"메모 {n * 7 + m} 번" for m in range(10)]
        lines.append(
            ",".join(
                [
                    f"R{n:06d}",
                    f"건 {n}",
                    models[n % len(models)],
                    tasks[n % len(tasks)],
                    cases[n % len(cases)],
                    SYMPTOMS[n % 40],
                    str(n % 1000),
                    *memo,
                ]
            )
        )
    body = "\n".join(lines).encode("utf-8")
    started = time.perf_counter()
    response = client.post(
        "/api/ontology/infer",
        files={"file": ("rows.csv", body, "text/csv")},
        headers=headers,
    )
    took = time.perf_counter() - started
    label = f"표 추론 + 참조 후보 {count:,}행 · {len(header)}열"
    if response.status_code >= 400:
        return ("infer", label, f"{took:.2f}초", f"HTTP {response.status_code}")
    found = []
    for column in response.json()["columns"]:
        if column["data_type"] == "object_ref":
            found.append(f"{column['header']}→{column['ref_type_slug']}")
        elif column["ref_note"]:
            found.append(f"{column['header']}: {column['ref_note'][:40]}")
    note = " · ".join(found)
    print(f"{took:8.2f}초  {label}  {note}", flush=True)
    return ("infer", label, f"{took:.2f}초", note)


def _explain(engine: Any, model_id: Any) -> None:
    """참조 질의의 **모양** — 지금 쓰는 꼴과 인덱스가 타는 꼴."""
    shapes = {
        "지금(->> 같음)": "SELECT count(*) FROM objects WHERE properties->>'model' = :id",
        "포함(@>)": (
            "SELECT count(*) FROM objects "
            "WHERE properties @> jsonb_build_object('model', CAST(:id AS text))"
        ),
    }
    with engine.connect() as connection:
        for name, sql in shapes.items():
            plan = connection.execute(
                text(f"EXPLAIN (ANALYZE, BUFFERS OFF, TIMING OFF) {sql}"),
                {"id": str(model_id)},
            ).all()
            lines = [row[0] for row in plan]
            took = next((line for line in lines if "Execution Time" in line), "")
            scan = next((line.strip() for line in lines if "Scan" in line), "")
            print(f"EXPLAIN {name}: {scan} · {took}")


def drop() -> None:
    url = _scale_url()
    name = _guard(url)
    with _maintenance(url).connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    print(f"{name} 을 지웠습니다.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=("create", "measure", "drop"))
    parser.add_argument("--rows", type=int, default=2_000_000)
    parser.add_argument("--timeout", type=int, default=120, help="질의 하나의 상한(초)")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--only", help="이 영역만 잰다(list · profile · graph · summary …)")
    args = parser.parse_args()
    if args.action == "create":
        create(args.rows)
    elif args.action == "measure":
        measure(args.timeout, args.repeat, args.only)
    else:
        drop()


if __name__ == "__main__":
    main()
