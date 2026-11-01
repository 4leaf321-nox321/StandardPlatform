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
    plm_model 6,000(→ 과제, base_code 는 셋에 하나꼴로 같다) — 운영의 SKU 자리
    plm_base 2,001 — 기본 모델. SKU 와 **관계(`sku_base`)와 참조 칸(`base`) 둘 다**로 잇는다 —
        사내에서는 관계로 잇는다. 지표가 두 길을 나란히 잰다.
    svc_part 300 — 교체 부품
    svc_case N — 속성 34칸(고를 값 · 날짜 · 숫자 · 예/아니오 · 글), model → plm_model.
        **5% 는 한 모델(뜨거운 모델)** 을 가리키고 나머지는 고르게 — 인기 모델의
        상세가 10만 건을 끌어안는 경우를 같이 본다. 생산일 → 판매일(0~59일 뒤) → 서비스일
        (와이블 k=1.5 · η=400일의 경과 — 2026-09-30 을 넘는 것은 안 심는다), 공장은 기본 모델의
        것, 교체 부품은 0~3개(여러 값 참조).
    svc_sales · svc_production — 판매 집계(기본 모델 x 월 x 대수) · 생산 집계(기본 모델 x 월 x
        공장 x 대수). 지표의 분모다.
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
FACTORIES = [f"F{n}" for n in range(1, 5)]
PARTS = 300
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
    print(f"[1/6] {name} 을 만들었습니다 — 마이그레이션")
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

    print("[2/6] 부서 · 계정")
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

    print("[3/6] 정의(가져오기)")
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
        print("[4/6] 축 — 프로젝트 · 과제 · 개발모델(SKU) · 기본 모델 · 부품")
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
        # 기본 모델 — base_code 마다 하나. SKU 와는 참조 칸 · 관계 둘 다로 잇는다.
        connection.execute(
            text(
                "INSERT INTO objects (id, type_id, key, label, created_at, updated_at) "
                "SELECT gen_random_uuid(), :t, code, code, now(), now() FROM ("
                "SELECT DISTINCT properties->>'base_code' AS code FROM objects "
                "WHERE type_id = :model) c"
            ),
            {"t": ids["plm_base"], "model": ids["plm_model"]},
        )
        connection.execute(
            text(
                "UPDATE objects m SET properties = m.properties || "
                "jsonb_build_object('base', b.id::text) FROM objects b "
                "WHERE m.type_id = :model AND b.type_id = :base "
                "AND b.key = m.properties->>'base_code'"
            ),
            {"model": ids["plm_model"], "base": ids["plm_base"]},
        )
        connection.execute(
            text(
                "INSERT INTO object_relations (id, src_object_id, dst_object_id, relation, "
                "properties, evidence_note, created_at, updated_at) "
                "SELECT gen_random_uuid(), m.id, b.id, 'sku_base', '{}'::jsonb, '', "
                "now(), now() "
                "FROM objects m JOIN objects b ON b.type_id = :base "
                "AND b.key = m.properties->>'base_code' WHERE m.type_id = :model"
            ),
            {"model": ids["plm_model"], "base": ids["plm_base"]},
        )
        connection.execute(
            text(
                "INSERT INTO objects (id, type_id, key, label, created_at, updated_at) "
                "SELECT gen_random_uuid(), :t, 'PT-' || lpad(n::text, 3, '0'), '부품 ' || n, "
                "now(), now() FROM generate_series(1, CAST(:count AS int)) n"
            ),
            {"t": ids["svc_part"], "count": PARTS},
        )

    print(f"[5/6] 기록 {rows:,}건 — 20만 건씩")
    chunk = 200_000
    started = time.perf_counter()
    for low in range(1, rows + 1, chunk):
        high = min(rows, low + chunk - 1)
        with engine.begin() as connection:
            connection.execute(
                text(_CASES_SQL),
                {
                    "t": ids["svc_case"],
                    "model": ids["plm_model"],
                    "part": ids["svc_part"],
                    "lo": low,
                    "hi": high,
                },
            )
        print(f"      {high:,} — {time.perf_counter() - started:.0f}초", flush=True)
    print("[6/6] 분모 — 판매 집계 · 생산 집계(기본 모델 x 월)")
    with engine.begin() as connection:
        for slug, extra, upto in (
            ("svc_sales", "", "2023-02-01"),
            (
                "svc_production",
                ", 'factory', 'F' || (1 + (substr(b.key, 5)::int % 4))",
                "2022-12-01",
            ),
        ):
            connection.execute(
                text(
                    "INSERT INTO objects (id, type_id, key, label, properties, created_at, "
                    "updated_at) SELECT gen_random_uuid(), :t, "
                    "b.key || '_' || to_char(mon, 'YYYYMM'), "
                    "b.key || ' ' || to_char(mon, 'YYYY-MM'), "
                    "jsonb_build_object('base_model', b.id::text, "
                    "'month', to_char(mon, 'YYYY-MM-DD'), "
                    "'units', 400 + abs(hashtext(b.key || mon::text || :salt)) % 100"
                    + extra
                    + "), now(), now() FROM objects b, "
                    "generate_series(date '2019-01-01', CAST(:upto AS date), "
                    "interval '1 month') mon WHERE b.type_id = :base"
                ),
                {"t": ids[slug], "salt": slug, "base": ids["plm_base"], "upto": upto},
            )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE"))
        size = connection.execute(
            text("SELECT pg_size_pretty(pg_database_size(current_database()))")
        ).scalar()
    print(f"끝 — DB 크기 {size}. 이제 `measure`.")


_CASES_SQL = (
    "WITH m AS (SELECT array_agg(id::text ORDER BY key) a FROM objects "
    "WHERE type_id = :model), "
    "w AS (SELECT array_agg(id ORDER BY slug) a FROM workspaces WHERE slug LIKE 'ws%'), "
    "pt AS (SELECT array_agg(id::text ORDER BY key) a FROM objects WHERE type_id = :part) "
    "INSERT INTO objects (id, type_id, key, label, properties, owner_workspace_id, "
    "created_at, updated_at) "
    "SELECT gen_random_uuid(), :t, 'C' || lpad(n::text, 8, '0'), "
    "'C' || lpad(n::text, 8, '0'), "
    "jsonb_build_object("
    # 5% 는 뜨거운 모델(첫 모델) — 나머지는 고르게.
    "'model', m.a[1 + mm.mi], "
    # 공장은 기본 모델의 것(생산 집계와 같은 규칙) — base_code 의 번호가 (모델 순번 + 1) / 3.
    "'factory', 'F' || (1 + ((mm.mi + 1) / 3) % 4), "
    "'parts', CASE n % 4 WHEN 0 THEN '[]'::jsonb "
    "WHEN 1 THEN jsonb_build_array(pt.a[1 + (n * 31) % 300]) "
    "WHEN 2 THEN jsonb_build_array(pt.a[1 + (n * 31) % 300], pt.a[1 + (n * 31 + 101) % 300]) "
    "ELSE jsonb_build_array(pt.a[1 + (n * 31) % 300], pt.a[1 + (n * 31 + 101) % 300], "
    "pt.a[1 + (n * 31 + 202) % 300]) END, "
    "'symptom', 'S' || lpad((n % 40)::text, 2, '0'), "
    "'center', 'C' || lpad((n % 30)::text, 2, '0'), "
    "'country', 'N' || lpad((n % 20)::text, 2, '0'), "
    "'handling', 'H' || (n % 8), "
    "'service_date', to_char(dd.served, 'YYYY-MM-DD'), "
    "'sale_date', to_char(dd.sold, 'YYYY-MM-DD'), "
    "'production_date', to_char(dd.made, 'YYYY-MM-DD'), "
    "'cost', (n % 1000) * 10, "
    "'term', n % 36, "
    "'warranty', n % 3 = 0, "
    "'serial_no', 'SN' || n, "
    "'remark', substr(md5(n::text), 1, 20)"
    + "".join(
        f", '{one}', substr(md5((n + {i})::text), 1, 12)" for i, one in enumerate(TEXT_FIELDS)
    )
    + "), w.a[1 + n % 10], now(), now() - make_interval(secs => n) "
    "FROM generate_series(CAST(:lo AS int), CAST(:hi AS int)) n, m, w, pt, "
    # 5% 는 뜨거운 모델(첫 모델) — 나머지는 고르게.
    "LATERAL (SELECT CASE WHEN n % 20 = 0 THEN 0 "
    "ELSE ((n::bigint * 7919) % array_length(m.a, 1))::int END AS mi) mm, "
    # 생산 → 판매(0~59일 뒤) → 서비스(와이블 k=1.5 · η=400일의 경과). 2026-09-30 을 넘는 것은
    # 아직 안 온 서비스라 안 심는다(관측의 끝).
    "LATERAL (SELECT date '2019-01-01' + (n % 1400) AS made) d0, "
    "LATERAL (SELECT d0.made, d0.made + (n % 60) AS sold, d0.made + (n % 60) + "
    "floor(400 * power(-ln(((n::bigint * 7919) % 9973 + 0.5) / 9973.0), 1 / 1.5))::int "
    "AS served) dd "
    "WHERE dd.served <= date '2026-09-30'"
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
        {"key": "sale_date", "label": "판매일", "data_type": "date"},
        _enum("factory", "공장", FACTORIES),
        {
            "key": "parts",
            "label": "교체 부품",
            "data_type": "object_ref",
            "ref_type_slug": "svc_part",
            "multi": True,
        },
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
            {"slug": "plm_base", "label": "기본 모델", "key_policy": "required"},
            {"slug": "svc_part", "label": "부품", "key_policy": "required"},
            {
                "slug": "plm_model",
                "label": "개발모델",
                "key_policy": "required",
                "properties": [
                    {
                        "key": "base",
                        "label": "기본 모델",
                        "data_type": "object_ref",
                        "ref_type_slug": "plm_base",
                    },
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
                "usage": "log",
                "temporal_kind": "evergreen",
                "properties": case_props,
            },
            {
                "slug": "svc_sales",
                "label": "판매 집계",
                "key_policy": "required",
                "usage": "log",
                "properties": [
                    {
                        "key": "base_model",
                        "label": "기본 모델",
                        "data_type": "object_ref",
                        "ref_type_slug": "plm_base",
                    },
                    {"key": "month", "label": "판매월", "data_type": "date"},
                    {"key": "units", "label": "대수", "data_type": "number"},
                ],
            },
            {
                "slug": "svc_production",
                "label": "생산 집계",
                "key_policy": "required",
                "usage": "log",
                "properties": [
                    {
                        "key": "base_model",
                        "label": "기본 모델",
                        "data_type": "object_ref",
                        "ref_type_slug": "plm_base",
                    },
                    {"key": "month", "label": "생산월", "data_type": "date"},
                    _enum("factory", "공장", FACTORIES),
                    {"key": "units", "label": "대수", "data_type": "number"},
                ],
            },
        ],
        "relation_types": [
            {
                "slug": "sku_base",
                "label": "기본 모델",
                "inverse_label": "SKU",
                "src_type_slugs": ["plm_model"],
                "dst_type_slugs": ["plm_base"],
                "directed": True,
                "cardinality": "many_to_one",
            }
        ],
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
        (
            "summary",
            "통계 — 서비스월별 추이(기간 단위 · 시간순)",
            get(
                f"{cases_path}/summary",
                group_by="properties.service_date",
                grain="month",
                order="key",
            ),
        ),
        ("search", "통합 검색", get("/api/search", q="C0012345")),
        # 두 글자 — trigram 은 세 글자부터라 조각 인덱스(0054)가 탄다. 흔한 조각(「C0」)은
        # 안 건다.
        ("search", "통합 검색 — 두 글자(과제)", get("/api/search", q="과제")),
        (
            "search",
            "과제 목록 — 두 글자 검색",
            get("/api/objects/plm_task", limit=50, q="과제"),
        ),
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
    if not only or "metrics" in (only or ""):
        results.extend(_metrics_rehearsal(client, admin, url))
    if not only or "recipes" in (only or ""):
        results.extend(_recipes_rehearsal(client, admin, url))
    if not only or "visits" in (only or ""):
        results.extend(_visits_rehearsal(client, admin, url))
    if not only or "alerts" in (only or ""):
        results.extend(_alerts_rehearsal(client, admin, url))
    if not only or "limits" in (only or ""):
        results.extend(_limits_rehearsal(client, admin, url))
    if not only or "incremental" in (only or ""):
        results.extend(_incremental_rehearsal(client, admin, url))
    if not only or "rules" in (only or ""):
        results.extend(_rules_rehearsal(client, admin))
    if not only or "periods" in (only or ""):
        results.extend(_periods_rehearsal(repeat))
    if not only or "explode" in (only or ""):
        results.extend(_explode_rehearsal(repeat))
    if not only or "lineage" in (only or ""):
        results.extend(_lineage_rehearsal(repeat))
    if not only or "proposals" in (only or ""):
        results.extend(_proposals_rehearsal(repeat))
    if not only or "edgefields" in (only or ""):
        results.extend(_edgefields_rehearsal(client, admin, repeat))
    if only and "map" in only.split(","):
        # 판을 만들어 **남긴다**(지도는 계산해 둔 사본 — 화면이 이 판으로 선다). 덧붙임 · 쓰기
        # 비용은 롤백 안에서 잰다. 전체 측정에는 안 든다(판 만들기가 분 단위).
        results.extend(_map_rehearsal(client, admin, manager, repeat))

    print("\n| 영역 | 무엇 | 중앙값 | 비고 |\n| --- | --- | --- | --- |")
    for area, label, took, note in results:
        print(f"| {area} | {label} | {took} | {note} |")
    print()
    _explain(engine, typical)


def _metric_definitions(tag: str) -> list[tuple[str, str, str, dict[str, Any]]]:
    """운영에서 세울 첫 지표 셋 — (slug, 이름, 원천 타입, 정의). 분모 둘이 앞에 온다.

    분모 둘(판매 집계 · 생산 집계)과 기록 지표 넷 — ① 판매월 코호트 인입(기본 모델을
    **관계**로 · **참조 칸**으로), ③ 생산월 코호트 x 공장(분모 생산 대수), ⑦ 부품 교체(여러
    값 기준 · 겹침, 분모는 판매 대수 전부 합). 지표 리허설과 분석 리허설이 함께 쓴다.
    """
    sales, prod = f"rh_sales_{tag}", f"rh_prod_{tag}"

    def when(address: str, grain: str = "month") -> dict[str, str]:
        return {"address": address, "grain": grain}

    base_ref = {"name": "base_model", "address": "ref.model.base"}
    base_rel = {"name": "base_model", "address": "ref.model.out.sku_base"}
    symptom = {"name": "symptom", "address": "properties.symptom"}
    factory = {"name": "factory", "address": "properties.factory"}
    part = {"name": "part", "address": "properties.parts"}
    q1 = {
        "measure": "count",
        "time": when("properties.service_date"),
        "cohort": when("properties.sale_date"),
        "dimensions": [base_rel, symptom],
        "denominator": {"metric": sales, "on": ["base_model"], "time": "cohort", "per": 100},
        "settle_days": 60,
    }
    return [
        (
            sales,
            "판매 대수",
            "svc_sales",
            {
                "measure": "sum",
                "measure_field": "properties.units",
                "time": when("properties.month"),
                "dimensions": [{"name": "base_model", "address": "properties.base_model"}],
            },
        ),
        (
            prod,
            "생산 대수",
            "svc_production",
            {
                "measure": "sum",
                "measure_field": "properties.units",
                "time": when("properties.month"),
                "dimensions": [
                    {"name": "base_model", "address": "properties.base_model"},
                    factory,
                ],
            },
        ),
        (f"rh_q1_{tag}", "① 판매월 코호트 인입 — 기본 모델을 관계로", "svc_case", q1),
        (
            f"rh_q1r_{tag}",
            "① 같은 것 — 기본 모델을 참조 칸으로",
            "svc_case",
            {**q1, "dimensions": [base_ref, symptom]},
        ),
        (
            f"rh_q3_{tag}",
            "③ 생산월 코호트 x 공장",
            "svc_case",
            {
                "measure": "count",
                "time": when("properties.service_date"),
                "cohort": when("properties.production_date"),
                "dimensions": [base_ref, factory],
                "denominator": {
                    "metric": prod,
                    "on": ["base_model", "factory"],
                    "time": "cohort",
                    "per": 1000,
                },
                "settle_days": 60,
            },
        ),
        (
            f"rh_q7_{tag}",
            "⑦ 부품 교체 — 부품 x 분기(여러 값 기준)",
            "svc_case",
            {
                "measure": "count",
                "time": when("properties.service_date", "quarter"),
                "dimensions": [part],
                "denominator": {"metric": sales, "on": [], "time": None, "per": 1000},
            },
        ),
    ]


def _build_metrics(
    client: Any,
    admin: dict[str, str],
    definitions: list[tuple[str, str, str, dict[str, Any]]],
    row: Callable[[str, float, str], None],
    made: list[str],
) -> bool:
    """정의마다 계획 → 저장 → 이 자리에서 재계산(워커를 거치지 않는다 — 재는 것은 SQL 한
    문장이다). 저장한 slug 는 `made` 에 — 부르는 쪽이 끝에 지운다. 저장이 실패하면 False."""
    from app.database import SessionLocal
    from app.modules.metrics import services as metrics_services

    for slug, label, source, spec in definitions:
        body = {"source_type_slug": source, "spec": spec}
        started = time.perf_counter()
        planned = client.post("/api/metrics/plan", json=body, headers=admin)
        plan = planned.json() if planned.status_code == 200 else {}
        note = (
            f"ok={plan.get('ok')} rows={plan.get('rows')} cells~{plan.get('estimated_cells')}"
            if plan.get("ok")
            else f"거절 {plan.get('errors') or planned.text[:160]}"
        )
        row(f"계획 — {label}", time.perf_counter() - started, note)
        saved = client.post(
            "/api/metrics", json={"slug": slug, "label": label, **body}, headers=admin
        )
        if saved.status_code != 201:
            row(f"정의 — {label}", 0.0, saved.text[:160])
            return False
        made.append(slug)
        started = time.perf_counter()
        with SessionLocal() as db:
            try:
                result = metrics_services.run_recompute(
                    db, [slug], job_id=None, progress=lambda *_: None
                )
                done = result["runs"][0]
                note = f"cells={done.get('cells'):,} rows={done.get('rows'):,}"
            except Exception as caught:  # 재는 자리다 — 이유만 적는다
                note = f"실패 {type(caught).__name__}: {str(caught)[:120]}"
        row(f"재계산 — {label}", time.perf_counter() - started, note)
    return True


def _metrics_rehearsal(
    client: Any, admin: dict[str, str], url: str
) -> list[tuple[str, str, str, str]]:
    """지표(ADR 0013) — 운영에서 세울 **첫 지표 셋을 그대로** 세워 잰다.

    분모 둘(판매 집계 · 생산 집계)과 기록 지표 넷 — ① 판매월 코호트 인입(기본 모델을
    **관계**로 · **참조 칸**으로, 두 길을 나란히), ③ 생산월 코호트 x 공장(분모 생산 대수),
    ⑦ 부품 교체(여러 값 기준 · 겹침, 분모는 판매 대수 전부 합). 계산은 워커를 거치지 않고 이
    자리에서 돌린다 — 재는 것은 SQL 한 문장이다. 끝나면 정의를 지운다(분모를 쓰는 것부터).
    """
    import uuid

    out: list[tuple[str, str, str, str]] = []
    tag = uuid.uuid4().hex[:4]
    definitions = _metric_definitions(tag)

    def row(label: str, took: float, note: str) -> None:
        out.append(("metrics", label, f"{took:.2f}초", note))
        print(f"{took:8.2f}초  {label}  {note}", flush=True)

    made: list[str] = []
    try:
        if not _build_metrics(client, admin, definitions, row, made):
            return out

        wide = {
            "measure": "count",
            "time": {"address": "properties.service_date", "grain": "month"},
            "dimensions": [
                {"name": "base_model", "address": "ref.model.base"},
                {"name": "part", "address": "properties.parts"},
            ],
        }
        started = time.perf_counter()
        planned = client.post(
            "/api/metrics/plan",
            json={"source_type_slug": "svc_case", "spec": wide},
            headers=admin,
        )
        plan = planned.json() if planned.status_code == 200 else {}
        row(
            "계획만 — ⑦ 기본 모델 x 부품 x 월(상한 확인)",
            time.perf_counter() - started,
            f"ok={plan.get('ok')} cells~{plan.get('estimated_cells')} "
            f"{plan.get('errors') or ''}",
        )

        with create_engine(url).connect() as connection:
            hot_base = connection.execute(
                text(
                    "SELECT o.properties->>'base' FROM objects o "
                    "JOIN object_types t ON t.id = o.type_id "
                    "WHERE t.slug = 'plm_model' ORDER BY o.key LIMIT 1"
                )
            ).scalar()
        first, third, seventh = definitions[2][0], definitions[4][0], definitions[5][0]
        reads: list[tuple[str, str, dict[str, Any]]] = [
            (
                "① 코호트 누적 비율 — 전체",
                f"/api/metrics/{first}/cohort",
                {"cumulative": "true"},
            ),
            (
                "① 코호트 누적 비율 — 기본 모델 하나(뜨거운)",
                f"/api/metrics/{first}/cohort",
                {"cumulative": "true", "d.base_model": hot_base},
            ),
            (
                "① 표 — 증상 x 판매월 코호트",
                f"/api/metrics/{first}/values",
                {"dims": "symptom", "by": "cohort"},
            ),
            ("① 추이 — 증상별 선", f"/api/metrics/{first}/series", {"split": "symptom"}),
            (
                "③ 표 — 공장 x 생산월 코호트(비율)",
                f"/api/metrics/{third}/values",
                {"dims": "factory", "by": "cohort"},
            ),
            (
                "⑦ 표 — 부품별(겹침, 판매 대수 전부 분모)",
                f"/api/metrics/{seventh}/values",
                {"dims": "part"},
            ),
            (
                "⑦ 표 — 부품 x 분기",
                f"/api/metrics/{seventh}/values",
                {"dims": "part", "by": "period"},
            ),
            ("기준 값 목록 — 기본 모델", f"/api/metrics/{first}/dims", {"name": "base_model"}),
        ]
        for label, path, params in reads:
            times: list[float] = []
            shape = ""
            for _ in range(3):
                started = time.perf_counter()
                got = client.get(path, params=params, headers=admin)
                times.append(time.perf_counter() - started)
                if got.status_code >= 400:
                    shape = f"HTTP {got.status_code} {got.text[:120]}"
                    break
                shape = _metric_shape(got.json())
            row(label, statistics.median(times), shape)
    finally:
        for slug in reversed(made):
            client.delete(f"/api/metrics/{slug}", headers=admin)
    return out


def _recipes_rehearsal(
    client: Any, admin: dict[str, str], url: str
) -> list[tuple[str, str, str, str]]:
    """분석(ADR 0014) — 운영 모양 자료 위에서 레시피 다섯을 **정답과 견주어** 잰다.

    정답: 서비스 경과가 와이블(형상 1.5 · 척도 400일), 판매 대수의 약 4.6%만 기록을 남긴다 →
    수명은 결함 모형 · 형상 1.4~1.6 · 척도 380~420일 · B10 「이르지 않음」. 순차 검정은 기록의
    5%를 받는 뜨거운 기본 모델 vs 보통 모델이 첫 시점에 「나쁨」, 보통끼리 무작위 짝 200개는
    「나쁨」 이 α/(1-β) 언저리 아래. 부품은 고르게 퍼져 몰림이 없고, 코호트 축에 변화가 없다.

    판매일은 생산일 + 0~59일이라 2019-01 · 02 코호트는 기록이 덜 차고(생산 첫 두 달),
    2022-11 뒤는 생산이 끝나 기록이 없는데 판매 집계는 2023-02 까지 있다 — 코호트를
    2019-03 ~ 2022-10 으로 좁혀 그 가장자리를 뺀다.
    """
    import random
    import uuid

    out: list[tuple[str, str, str, str]] = []
    tag = uuid.uuid4().hex[:4]
    every = {one[0]: one for one in _metric_definitions(tag)}
    sales, prod = f"rh_sales_{tag}", f"rh_prod_{tag}"
    first, third, seventh = f"rh_q1r_{tag}", f"rh_q3_{tag}", f"rh_q7_{tag}"
    definitions = [every[slug] for slug in (sales, prod, first, third, seventh)]
    inside = {"cohort_from": "2019-03-01", "cohort_to": "2022-11-01"}

    def row(label: str, took: float, note: str) -> None:
        out.append(("recipes", label, f"{took:.2f}초", note))
        print(f"{took:8.2f}초  {label}  {note}", flush=True)

    def analyse(slug: str, recipe: str, **params: Any) -> tuple[float, dict[str, Any]]:
        started = time.perf_counter()
        got = client.get(
            f"/api/metrics/{slug}/analysis/{recipe}", params=params, headers=admin
        )
        took = time.perf_counter() - started
        if got.status_code >= 400:
            return took, {"error": f"HTTP {got.status_code} {got.text[:160]}"}
        return took, dict(got.json())

    def codes(body: dict[str, Any]) -> str:
        return ",".join(one["code"] for one in body.get("caveats", []))

    made: list[str] = []
    try:
        if not _build_metrics(client, admin, definitions, row, made):
            return out
        # ② 수명 — 결함 모형을 고르고 B10 을 지어내지 않는가.
        took, life = analyse(first, "life", **inside)
        if "error" in life:
            row("② 수명", took, life["error"])
        else:
            fits = {one["model"]: one for one in life["fits"]}
            chosen = fits.get(life["chosen"] or "", {})
            lives = " · ".join(
                f"B{round(one['q'] * 100)} {one['status']}"
                + (f" {one['age']:.1f}" if one["age"] is not None else "")
                for one in life["b_lives"]
            )
            row(
                "② 수명 — 둘을 맞춰 고르기",
                took,
                f"고름 {life['chosen']} β={chosen.get('beta', 0):.3f} "
                f"η={chosen.get('eta_days', 0):.0f}일 p={chosen.get('p') or 0:.4f} · "
                f"{lives} · "
                f"코호트 {life['cohorts_used']} · 경과 {life['max_age']} · 주의 {codes(life)}",
            )
            standard = fits.get("weibull", {})
            row(
                "② 수명 — 표준 와이블(치우침 기록)",
                0.0,
                f"β={standard.get('beta', 0):.3f} η={standard.get('eta_days', 0):.0f}일 "
                f"LR p={life['lrt_p_value']}",
            )
        # ④ 순차 검정 — 뜨거운 기본 모델 vs 보통, 그리고 보통끼리 무작위 짝.
        with create_engine(url).connect() as connection:
            hot = connection.execute(
                text(
                    "SELECT o.properties->>'base' FROM objects o "
                    "JOIN object_types t ON t.id = o.type_id "
                    "WHERE t.slug = 'plm_model' ORDER BY o.key LIMIT 1"
                )
            ).scalar()
            bases = [
                str(one)
                for one in connection.execute(
                    text(
                        "SELECT o.id FROM objects o JOIN object_types t ON t.id = o.type_id "
                        "WHERE t.slug = 'plm_base' ORDER BY o.key"
                    )
                ).scalars()
            ]
        typical = [one for one in bases if one != hot]
        took, hot_vs = analyse(
            first, "sprt", target=hot, reference=typical[len(typical) // 2], compact="true"
        )
        row(
            "④ 순차 검정 — 뜨거운 기본 모델 vs 보통",
            took,
            hot_vs.get("error")
            or f"{hot_vs['decision']} @ {hot_vs['decided_at']} · "
            f"표준화 비 {hot_vs['smr']:.1f}",
        )
        rng = random.Random(7)
        tally: dict[str, int] = {}
        times: list[float] = []
        for _ in range(200):
            target, reference = rng.sample(typical, 2)
            took, pair = analyse(
                first, "sprt", target=target, reference=reference, compact="true"
            )
            times.append(took)
            key = pair.get("decision", "error")
            tally[key] = tally.get(key, 0) + 1
        row(
            "④ 순차 검정 — 보통끼리 무작위 짝 200",
            statistics.median(times),
            " · ".join(f"{key} {value}" for key, value in sorted(tally.items())),
        )
        # ③ 관리도 — 생산월 코호트(출고 3개월 안) x 공장.
        took, control = analyse(
            third, "control", axis="cohort", window=3, split="factory", **inside
        )
        row(
            "③ 관리도 — 생산월 코호트 x 공장(라니)",
            took,
            control.get("error")
            or " · ".join(
                f"{one['label']} 신호 {one['signals']}/{one['subgroups']} "
                f"σz {one['sigma_z_raw'] or 0:.1f}"
                for one in control["charts"]
            ),
        )
        # ⑩ 계절 · 변화점 — 판매월 코호트의 출고 3개월 안 인입률.
        took, changes = analyse(first, "changes", axis="cohort", window=3, **inside)
        row(
            "⑩ 변화점 — 판매월 코호트(출고 3개월 안)",
            took,
            changes.get("error")
            or f"변화 {len(changes['changes'])} · 계절 "
            f"{'씀' if changes['seasonal'] else '안 씀'} "
            f"· φ {changes['dispersion']:.2f} · 점 {len(changes['points'])}",
        )
        # ⑦ 파레토 — 부품(여러 값 · 나온 횟수 기준).
        took, pareto = analyse(seventh, "pareto", dim="part", compact="true")
        found = pareto.get("concentration") or {}
        row(
            "⑦ 파레토 — 부품",
            took,
            pareto.get("error")
            or f"값 {found.get('categories')} · 유효 {found.get('effective', 0):.0f} · "
            f"HHI {found.get('hhi', 0):.4f} · 지니 {found.get('gini', 0):.3f} · "
            f"핵심 소수 {found.get('vital_few')} · 기준 {pareto['basis']}",
        )
        # ⑤ 집단 비교 — 정답: 공장은 뜨거운 기본 모델이 있는 F1 이 높고 넷이 모두 다르다(기록을
        # 덜 받는 기본 모델이 공장마다 고르지 않다). 기본 모델은 뜨거운 것 하나만 「높음」, 세
        # 모델 중 둘만 기록을 받는 299개(순번이 20 의 배수인 모델은 기록이 없다)가 「낮음」 쪽.
        took, plants = analyse(
            third, "groups", dim="factory", axis="cohort", window=3, **inside
        )
        row(
            "⑤ 집단 비교 — 공장(생산월 코호트 · 출고 3개월 안)",
            took,
            plants.get("error")
            or f"집단 {plants['groups']} · 이질성 p {plants['heterogeneity_p']:.3g} · "
            f"흔들림 {plants['spread'] or 0:.3f} · 다름 {plants['flagged']}",
        )
        took, models = analyse(
            first,
            "groups",
            dim="base_model",
            axis="cohort",
            window=3,
            compact="true",
            **inside,
        )
        if "error" in models:
            row("⑤ 집단 비교 — 기본 모델", took, models["error"])
        else:
            top = models["rows"][0]
            row(
                "⑤ 집단 비교 — 기본 모델(판매월 코호트 · 출고 3개월 안)",
                took,
                f"집단 {models['groups']}(싣지 않음 {models['other_groups']}) · "
                f"다름 {models['flagged']}(실은 줄: 높음 "
                f"{sum(one['flag'] == 'high' for one in models['rows'])} · 낮음 "
                f"{sum(one['flag'] == 'low' for one in models['rows'])}) · 맨 위 "
                f"{'뜨거운 것' if top['key'] == hot else top['key']} "
                f"{top['ratio'] or 0:.1f}배 {top['flag']}",
            )
        # ④ 증상마다 훑기 — 뜨거운 것은 증상마다 「나쁨」. 합성 기록은 기본 모델마다 증상 셋이
        # 겹치지 않게 붙어(기록 번호로 모델 · 증상이 함께 정해진다) 대상의 증상은 대개 전작에
        # 없다(`new`) — 그것을 빼고 센 「나쁨」 이 잘못 선 것이다.
        took, hot_scan = analyse(
            first, "sprt/scan", by="symptom", target=hot, reference=typical[len(typical) // 2]
        )
        row(
            "④ 증상마다 — 뜨거운 기본 모델 vs 보통",
            took,
            hot_scan.get("error")
            or f"증상 {hot_scan['scanned']} · 나쁨 "
            f"{sum(one['decision'] == 'worse' for one in hot_scan['items'])}(전작에 없던 것 "
            f"{sum(one['new'] for one in hot_scan['items'])}) · "
            f"α/K {hot_scan['alpha_each']:.4f}",
        )
        any_worse = any_new = 0
        scan_times: list[float] = []
        for _ in range(20):
            target, reference = rng.sample(typical, 2)
            took, pair_scan = analyse(
                first, "sprt/scan", by="symptom", target=target, reference=reference
            )
            scan_times.append(took)
            items = pair_scan.get("items", [])
            any_worse += any(one["decision"] == "worse" and not one["new"] for one in items)
            any_new += any(one["new"] for one in items)
        row(
            "④ 증상마다 — 보통끼리 무작위 짝 20",
            statistics.median(scan_times),
            f"전작에도 있던 증상이 하나라도 나쁨 {any_worse}/20 · 전작에 없던 증상이 있음 "
            f"{any_new}/20",
        )
        # ⑩ 증상마다 변화점 — 정답은 변화 없음.
        took, change_scan = analyse(
            first, "changes/scan", by="symptom", axis="cohort", window=3, **inside
        )
        row(
            "⑩ 증상마다 — 판매월 코호트(출고 3개월 안)",
            took,
            change_scan.get("error")
            or f"증상 {change_scan['scanned']} · 오름 "
            f"{sum(one['direction'] == 'up' for one in change_scan['items'])} · 내림 "
            f"{sum(one['direction'] == 'down' for one in change_scan['items'])} · 벌점 "
            f"+{change_scan['extra_penalty']:.1f}",
        )
        # 클레임 예측 — 정답은 결함 와이블. 되짚어 보기(6 기간 전까지로 맞춰 그 뒤)가 실제를
        # 맞히나.
        took, ahead = analyse(first, "forecast", horizon=12, **inside)
        back = ahead.get("backtest") or {}
        row(
            "클레임 예측 — 판매월 코호트, 앞으로 12기간",
            took,
            ahead.get("error")
            or f"모형 {ahead['model']} · 앞으로 {ahead['total']['expected']:.0f}건"
            f"({ahead['total']['low']:.0f} ~ {ahead['total']['high']:.0f}) · 되짚어 보기 "
            f"{back.get('start')} 부터 예측 {back.get('predicted', 0):.0f} · 실제 "
            f"{back.get('actual', 0):.0f} · {'구간 안' if back.get('within') else '구간 밖'}",
        )
        # 전후 비교 — 정답은 「변화 없음」(대책이 없다). 보통 기본 모델 20 개를 아무 날짜로.
        decisions: dict[str, int] = {}
        cut_times: list[float] = []
        for target in rng.sample(typical, 20):
            took, cut = analyse(
                first,
                "cutin",
                at="2021-01-01",
                axis="cohort",
                window=3,
                **{"d.base_model": target},
                **inside,
            )
            cut_times.append(took)
            key = cut.get("decision") or "error"
            decisions[key] = decisions.get(key, 0) + 1
        row(
            "전후 비교 — 보통 기본 모델 20 · 아무 날짜(변화 없음이 정답)",
            statistics.median(cut_times),
            " · ".join(f"{key} {value}" for key, value in sorted(decisions.items())),
        )
    finally:
        for slug in reversed(made):
            client.delete(f"/api/metrics/{slug}", headers=admin)
    return out


def _alerts_rehearsal(
    client: Any, admin: dict[str, str], url: str
) -> list[tuple[str, str, str, str]]:
    """경보(ADR 0016) — 만들 때의 처음 확인과 **계산 하나에 붙는 확인 시간**.

    정답: 뜨거운 기본 모델의 순차 검정은 만들 때 이미 「나쁨」 이라 그것이 「처음부터 있던
    것」 이 되고, 같은 셀로 다시 확인하면 새것이 없다. 기본 모델은 모두 2019-01 에 처음 팔려
    최근 출시 훑기는 0개, 전부(60기간)를 훑으면 2,001개라 상한에서 거절한다.
    """
    import uuid

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.modules.metrics import alerts as alerts_module
    from app.modules.metrics.models import MetricDef

    out: list[tuple[str, str, str, str]] = []
    tag = uuid.uuid4().hex[:4]
    every = {one[0]: one for one in _metric_definitions(tag)}
    sales, prod = f"rh_sales_{tag}", f"rh_prod_{tag}"
    first, third = f"rh_q1r_{tag}", f"rh_q3_{tag}"
    definitions = [every[slug] for slug in (sales, prod, first, third)]
    inside = {"cohort_from": "2019-03-01", "cohort_to": "2022-11-01"}

    def row(label: str, took: float, note: str) -> None:
        out.append(("alerts", label, f"{took:.2f}초", note))
        print(f"{took:8.2f}초  {label}  {note}", flush=True)

    def create(slug: str, recipe: str, params: dict[str, str]) -> tuple[float, dict[str, Any]]:
        started = time.perf_counter()
        got = client.post(
            f"/api/metrics/{slug}/alerts",
            json={"name": f"리허설 {recipe}", "recipe": recipe, "params": params},
            headers=admin,
        )
        took = time.perf_counter() - started
        if got.status_code >= 400:
            return took, {"error": f"HTTP {got.status_code} {got.text[:160]}"}
        return took, dict(got.json())

    def seen(body: dict[str, Any]) -> str:
        if "error" in body:
            return str(body["error"])
        baseline = body.get("baseline") or {}
        findings = baseline.get("findings") or []
        head = f" · {findings[0]['title'][:60]}" if findings else ""
        notes = " · ".join(baseline.get("notes") or [])[:120]
        return f"처음부터 있던 것 {len(findings)}{head}" + (f" · {notes}" if notes else "")

    made: list[str] = []
    try:
        if not _build_metrics(client, admin, definitions, row, made):
            return out
        with create_engine(url).connect() as connection:
            hot = connection.execute(
                text(
                    "SELECT o.properties->>'base' FROM objects o "
                    "JOIN object_types t ON t.id = o.type_id "
                    "WHERE t.slug = 'plm_model' ORDER BY o.key LIMIT 1"
                )
            ).scalar()
            typical = connection.execute(
                text(
                    "SELECT o.id FROM objects o JOIN object_types t ON t.id = o.type_id "
                    "WHERE t.slug = 'plm_base' AND o.id::text <> :hot ORDER BY o.key "
                    "OFFSET 1000 LIMIT 1"
                ),
                {"hot": str(hot)},
            ).scalar()
        took, body = create(first, "sprt", {"target": str(hot), "reference": str(typical)})
        row("만들기 — 순차 검정(뜨거운 모델)", took, seen(body))
        took, body = create(
            third, "control", {"axis": "cohort", "window": "3", "split": "factory", **inside}
        )
        row("만들기 — 관리도(생산월 x 공장)", took, seen(body))
        took, body = create(first, "changes", {"axis": "cohort", "window": "3", **inside})
        row("만들기 — 변화점(판매월 코호트)", took, seen(body))
        took, body = create(first, "sprt", {"launched_within": "6", "reference": str(typical)})
        row("만들기 — 새 모델 훑기(최근 6기간)", took, seen(body))
        took, body = create(
            first, "sprt", {"launched_within": "60", "reference": str(typical)}
        )
        row("만들기 — 새 모델 훑기(60기간 = 전부)", took, seen(body))
        # 계산 뒤의 확인 — 같은 셀이라 새것이 없어야 한다. 지표마다 그 경보 전부.
        with SessionLocal() as db:
            for slug in (first, third):
                metric_id = db.scalars(
                    select(MetricDef.id).where(MetricDef.slug == slug)
                ).one()
                started = time.perf_counter()
                result = alerts_module.after_recompute(db, metric_id)
                row(
                    f"계산 뒤 확인 — {slug}",
                    time.perf_counter() - started,
                    f"경보 {result['alerts']} · 새것 {result['new']}",
                )
    finally:
        for slug in reversed(made):
            client.delete(f"/api/metrics/{slug}", headers=admin)
    return out


#: 심는 재방문의 정답 — 기본 확률 0.08, 공장 F2 오즈비 2 · F3 0.5, 증상 S05 ~ S09 1.5.
def _limits_rehearsal(
    client: Any, admin: dict[str, str], url: str
) -> list[tuple[str, str, str, str]]:
    """상한(ADR 0023) — 계획의 셀 어림이 실제 셀과 얼마나 맞나, 큰 셀의 계산 · 분석이 견디나.

    지표 다섯: 판매 대수 · 그것을 24개월 머물게 한 「쓰이는 대수」(머무는 기간) · ① 판매월
    코호트(기본 모델 x 증상, 분모 판매 대수) · 기본 모델 x 부품 x 월(예전 어림으로 거절되던 것)
    · 부품 x 분기. 지표마다 계획(어림 · 시간) → 계산(실제 셀 · 시간). 그다음 셀이 예전 읽기
    상한(2만)을 넘는 분석 — 부품마다 변화점 훑기(부품 x 기간), 기본 모델 x 부품 연관, 기본 모델
    집단 비교 · 순차 검정 훑기 · 수명 · 클레임 예측. 분석마다 이 프로세스의 메모리 최고치를
    함께 적는다. 끝나면 지표를 지운다.
    """
    import resource
    import uuid

    from app.database import SessionLocal
    from app.modules.metrics import services as metrics_services

    out: list[tuple[str, str, str, str]] = []
    tag = uuid.uuid4().hex[:4]

    def row(label: str, took: float, note: str) -> None:
        out.append(("limits", label, f"{took:.2f}초", note))
        print(f"{took:8.2f}초  {label}  {note}", flush=True)

    def peak_mb() -> float:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    def when(address: str, grain: str = "month") -> dict[str, str]:
        return {"address": address, "grain": grain}

    sales, stay, first = f"lm_sales_{tag}", f"lm_stay_{tag}", f"lm_q1_{tag}"
    wide, parts = f"lm_wide_{tag}", f"lm_parts_{tag}"
    base_sales = {"name": "base_model", "address": "properties.base_model"}
    base_ref = {"name": "base_model", "address": "ref.model.base"}
    part = {"name": "part", "address": "properties.parts"}
    sales_spec = {
        "measure": "sum",
        "measure_field": "properties.units",
        "time": when("properties.month"),
        "dimensions": [base_sales],
    }
    definitions: list[tuple[str, str, str, dict[str, Any]]] = [
        (sales, "판매 대수", "svc_sales", sales_spec),
        (
            stay,
            "쓰이는 대수(24개월 머묾)",
            "svc_sales",
            {**sales_spec, "stay": {"periods": 24}},
        ),
        (
            first,
            "① 판매월 코호트 — 기본 모델 x 증상",
            "svc_case",
            {
                "measure": "count",
                "time": when("properties.service_date"),
                "cohort": when("properties.sale_date"),
                "dimensions": [base_ref, {"name": "symptom", "address": "properties.symptom"}],
                "denominator": {
                    "metric": sales,
                    "on": ["base_model"],
                    "time": "cohort",
                    "per": 100,
                },
                "settle_days": 60,
            },
        ),
        (
            wide,
            "기본 모델 x 부품 x 월",
            "svc_case",
            {
                "measure": "count",
                "time": when("properties.service_date"),
                "dimensions": [base_ref, part],
            },
        ),
        (
            parts,
            "부품 x 분기",
            "svc_case",
            {
                "measure": "count",
                "time": when("properties.service_date", "quarter"),
                "dimensions": [part],
            },
        ),
    ]
    made: list[str] = []
    try:
        for slug, label, source, spec in definitions:
            body = {"source_type_slug": source, "spec": spec}
            started = time.perf_counter()
            planned = client.post("/api/metrics/plan", json=body, headers=admin)
            plan_took = time.perf_counter() - started
            plan = planned.json() if planned.status_code == 200 else {}
            estimated = plan.get("estimated_cells")
            if not plan.get("ok"):
                row(
                    f"계획 — {label}",
                    plan_took,
                    f"거절 {plan.get('errors') or planned.text[:200]}",
                )
                continue
            saved = client.post(
                "/api/metrics", json={"slug": slug, "label": label, **body}, headers=admin
            )
            if saved.status_code != 201:
                row(f"정의 — {label}", 0.0, saved.text[:200])
                continue
            made.append(slug)
            started = time.perf_counter()
            with SessionLocal() as db:
                try:
                    done = metrics_services.run_recompute(
                        db, [slug], job_id=None, progress=lambda *_: None
                    )["runs"][0]
                    cells = int(done["cells"])
                    note = (
                        f"어림 {estimated:,} · 실제 {cells:,} · 어림/실제 "
                        f"{(estimated or 0) / max(cells, 1):.2f} · 펼친 줄 {done['rows']:,} · "
                        f"계획 {plan_took:.1f}초"
                    )
                except Exception as caught:  # 재는 자리다 — 이유만 적는다
                    note = f"실패 {type(caught).__name__}: {str(caught)[:160]}"
            row(f"계산 — {label}", time.perf_counter() - started, note)
        with create_engine(url).connect() as connection:
            size = connection.execute(
                text("SELECT pg_size_pretty(pg_total_relation_size('metric_values'))")
            ).scalar()
            hot = connection.execute(
                text(
                    "SELECT o.properties->>'base' FROM objects o "
                    "JOIN object_types t ON t.id = o.type_id "
                    "WHERE t.slug = 'plm_model' ORDER BY o.key LIMIT 1"
                )
            ).scalar()
            typical = connection.execute(
                text(
                    "SELECT o.id FROM objects o JOIN object_types t ON t.id = o.type_id "
                    "WHERE t.slug = 'plm_base' ORDER BY o.key OFFSET 1000 LIMIT 1"
                )
            ).scalar()
        row("셀 표 크기", 0.0, f"metric_values {size}")
        inside = {"cohort_from": "2019-03-01", "cohort_to": "2022-11-01"}
        asks: list[tuple[str, str, str, dict[str, Any]]] = [
            (
                "부품마다 변화점 훑기(부품 x 월)",
                wide,
                "changes/scan",
                {"by": "part", "axis": "period"},
            ),
            (
                "기본 모델 x 부품 연관",
                wide,
                "assoc",
                {"rows": "base_model", "cols": "part", "compact": "true"},
            ),
            (
                "부품 파레토 · 기간별 추이",
                parts,
                "pareto",
                {"dim": "part", "by_period": "true", "compact": "true"},
            ),
            (
                "기본 모델 집단 비교(코호트 · 출고 3개월 안)",
                first,
                "groups",
                {
                    "dim": "base_model",
                    "axis": "cohort",
                    "window": 3,
                    "compact": "true",
                    **inside,
                },
            ),
            (
                "증상마다 순차 검정 — 뜨거운 기본 모델 vs 보통",
                first,
                "sprt/scan",
                {"by": "symptom", "target": hot, "reference": str(typical)},
            ),
            ("수명", first, "life", {"compact": "true", **inside}),
            ("클레임 예측(12기간)", first, "forecast", {"horizon": 12, **inside}),
        ]
        for label, slug, recipe, params in asks:
            if slug not in made:
                continue
            params = {key: value for key, value in params.items() if value is not None}
            before = peak_mb()
            started = time.perf_counter()
            got = client.get(
                f"/api/metrics/{slug}/analysis/{recipe}", params=params, headers=admin
            )
            took = time.perf_counter() - started
            note = (
                f"HTTP {got.status_code} {got.text[:160]}"
                if got.status_code >= 400
                else f"주의 {','.join(one['code'] for one in got.json().get('caveats', []))}"
            )
            row(label, took, f"{note} · 메모리 최고 {peak_mb():.0f}MB(전 {before:.0f})")
    finally:
        for slug in reversed(made):
            client.delete(f"/api/metrics/{slug}", headers=admin)
    return out


_VISIT_TRUTH = {"base": 0.08, "F2": 2.0, "F3": 0.5, "S05+": 1.5}

_VISITS_SQL = (
    "WITH s AS MATERIALIZED (SELECT g AS serial, 'F' || (1 + g % 4) AS factory, "
    # 증상은 시리얼을 4 로 나눈 몫에서 — g % 10 이면 g % 4(공장)와 홀짝이 엮여 홀수 증상이
    # F2 · F4 에서만 나오고, 두 요인이 완전히 겹쳐 오즈비를 가를 수 없다(첫 리허설에서 겪었다).
    "'S' || lpad(((g / 4) % 10)::text, 2, '0') AS symptom, "
    "date '2019-01-01' + (g % 1400) AS sold FROM generate_series(1, CAST(:n AS int)) g), "
    "p AS MATERIALIZED (SELECT s.*, 1 / (1 + exp(-(ln(0.08 / 0.92) "
    "+ CASE factory WHEN 'F2' THEN ln(2) WHEN 'F3' THEN ln(0.5) ELSE 0 END "
    "+ CASE WHEN symptom >= 'S05' THEN ln(1.5) ELSE 0 END))) AS prob, "
    "s.sold + floor(400 * power(-ln(1 - random()), 1 / 1.5))::int AS first, "
    "random() AS r2, random() AS r3, 1 + floor(random() * 90)::int AS d2, "
    "1 + floor(random() * 90)::int AS d3 FROM s), "
    "v AS (SELECT serial, factory, symptom, sold, first AS received, 1 AS k FROM p "
    "UNION ALL SELECT serial, factory, symptom, sold, first + d2, 2 FROM p WHERE r2 < prob "
    "UNION ALL SELECT serial, factory, symptom, sold, first + d2 + d3, 3 FROM p "
    "WHERE r2 < prob AND r3 < prob) "
    "INSERT INTO objects (id, type_id, key, label, properties, owner_workspace_id, "
    "created_at, updated_at) "
    "SELECT gen_random_uuid(), :t, 'V' || serial || '_' || k, 'V' || serial || '_' || k, "
    "jsonb_build_object('received', to_char(received, 'YYYY-MM-DD'), "
    "'sold', to_char(sold, 'YYYY-MM-DD'), 'serial', 'VS' || serial, "
    "'factory', factory, 'symptom', symptom), "
    "(SELECT id FROM workspaces WHERE slug = 'ws01'), now(), now() "
    "FROM v WHERE received <= date '2026-09-30'"
)


def _visits_rehearsal(
    client: Any, admin: dict[str, str], url: str
) -> list[tuple[str, str, str, str]]:
    """방문(ADR 0014) — 둘을 잰다. 끝나면 지표와 임시 타입을 지운다.

    1. 200만 건 기록에 방문 기준을 세워 **창 함수의 값**을 잰다. 시리얼(S/N)이 기록마다 달라
       모두 첫 방문이고 재방문은 없다 — 수는 뻔하고, 재는 것은 시간이다.
    2. 정답을 아는 재방문을 심은 임시 타입(시리얼 30만, 기록 약 33만)에서 위험 요인의 오즈비를
       되찾는다. 방문마다 그 뒤 90일 안에 다시 올 확률이 로지스틱(기본 0.08, 공장 F2 x2 · F3
       x0.5, 증상 S05 ~ S09 x1.5)이라 오즈비가 곧 정답이다.
    """
    import uuid

    out: list[tuple[str, str, str, str]] = []
    tag = uuid.uuid4().hex[:4]
    kind = f"rh_visit_{tag}"

    def row(label: str, took: float, note: str) -> None:
        out.append(("visits", label, f"{took:.2f}초", note))
        print(f"{took:8.2f}초  {label}  {note}", flush=True)

    def visit_spec(
        source_date: str, serial: str, more: list[dict[str, str]]
    ) -> dict[str, Any]:
        return {
            "measure": "count",
            "time": {"address": source_date, "grain": "month"},
            "visits": {"key": serial, "within_days": 90},
            "dimensions": [
                {"name": "visit_no", "address": "visit.number"},
                {"name": "again", "address": "visit.repeat"},
                *more,
            ],
            "settle_days": 60,
        }

    def counts(slug: str, dim: str) -> str:
        got = client.get(f"/api/metrics/{slug}/values", params={"dims": dim}, headers=admin)
        if got.status_code >= 400:
            return f"HTTP {got.status_code}"
        found: dict[str, int] = {}
        for cell in got.json()["cells"]:
            key = str(cell["dims"][dim])
            found[key] = found.get(key, 0) + cell["count"]
        return " · ".join(f"{key} {value:,}" for key, value in sorted(found.items()))

    made: list[str] = []
    planted = False
    try:
        wide = f"rh_v2m_{tag}"
        if not _build_metrics(
            client,
            admin,
            [
                (
                    wide,
                    "방문 — 200만 건(시리얼이 기록마다 다름)",
                    "svc_case",
                    visit_spec(
                        "properties.service_date",
                        "properties.serial_no",
                        [{"name": "factory", "address": "properties.factory"}],
                    ),
                )
            ],
            row,
            made,
        ):
            return out
        row("방문 차례 — 200만 건", 0.0, counts(wide, "visit_no"))
        row("재방문 — 200만 건", 0.0, counts(wide, "again"))

        imported = client.post(
            "/api/ontology/import",
            params={"dry_run": "false"},
            json={
                "types": [
                    {
                        "slug": kind,
                        "label": f"재방문 리허설 {tag}",
                        "usage": "log",
                        "properties": [
                            {"key": "received", "label": "접수일", "data_type": "date"},
                            {"key": "sold", "label": "판매일", "data_type": "date"},
                            {"key": "serial", "label": "시리얼", "data_type": "text"},
                            {"key": "factory", "label": "공장", "data_type": "text"},
                            {"key": "symptom", "label": "증상", "data_type": "text"},
                        ],
                    }
                ]
            },
            headers=admin,
        )
        if imported.status_code != 200 or not imported.json().get("applied"):
            row("임시 타입", 0.0, f"못 만듦 {imported.text[:160]}")
            return out
        planted = True
        engine = create_engine(url)
        started = time.perf_counter()
        with engine.begin() as connection:
            type_id = connection.execute(
                text("SELECT id FROM object_types WHERE slug = :s"), {"s": kind}
            ).scalar()
            connection.execute(text("SELECT setseed(0.42)"))
            inserted = connection.execute(
                text(_VISITS_SQL), {"n": 300_000, "t": type_id}
            ).rowcount
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("ANALYZE objects"))
        row("재방문 심기 — 시리얼 30만", time.perf_counter() - started, f"기록 {inserted:,}")

        risk = f"rh_vrisk_{tag}"
        if not _build_metrics(
            client,
            admin,
            [
                (
                    risk,
                    "방문 — 심은 재방문",
                    kind,
                    visit_spec(
                        "properties.received",
                        "properties.serial",
                        [
                            {"name": "factory", "address": "properties.factory"},
                            {"name": "symptom", "address": "properties.symptom"},
                        ],
                    ),
                )
            ],
            row,
            made,
        ):
            return out
        row("방문 차례 — 심은 재방문", 0.0, counts(risk, "visit_no"))
        started = time.perf_counter()
        got = client.get(
            f"/api/metrics/{risk}/analysis/logit",
            params={"factors": "factory,symptom"},
            headers=admin,
        )
        took = time.perf_counter() - started
        if got.status_code >= 400:
            row("⑥ 위험 요인", took, f"HTTP {got.status_code} {got.text[:160]}")
            return out
        body = got.json()
        # 레시피의 기준 수준은 기록이 가장 많은 값이다 — 정답과 견주려고 F1 · S00 대비로
        # 바꾼다.
        odds: dict[str, float] = {}
        for factor in body["factors"]:
            for level in factor["levels"]:
                if level["odds_ratio"] is not None:
                    odds[str(level["key"])] = float(level["odds_ratio"])

        def versus(key: str, base: str) -> str:
            if key not in odds or base not in odds:
                return "-"
            return f"{odds[key] / odds[base]:.2f}"

        symptoms = [odds[key] / odds["S00"] for key in odds if key >= "S05" and "S00" in odds]
        row(
            "⑥ 위험 요인 — F1 · S00 대비(정답 F2 2 · F3 0.5 · F4 1 · S05~09 1.5)",
            took,
            f"F2 {versus('F2', 'F1')} · F3 {versus('F3', 'F1')} · F4 {versus('F4', 'F1')} · "
            f"S05~09 {min(symptoms, default=0):.2f}~{max(symptoms, default=0):.2f} · "
            f"AUC {body['auc']:.3f} · 닫힌 기록 {body['records']:,} · "
            f"열림 {body['excluded'].get('open', 0):,} · "
            f"주의 {','.join(one['code'] for one in body['caveats'])}",
        )
    finally:
        for slug in reversed(made):
            client.delete(f"/api/metrics/{slug}", headers=admin)
        if planted:
            with create_engine(url).begin() as connection:
                connection.execute(
                    text(
                        "UPDATE objects SET deleted_at = now() WHERE type_id = "
                        "(SELECT id FROM object_types WHERE slug = :s)"
                    ),
                    {"s": kind},
                )
            gone = client.delete(
                f"/api/ontology/types/{kind}", params={"purge_deleted": "true"}, headers=admin
            )
            if gone.status_code != 204:
                row("임시 타입 지우기", 0.0, f"HTTP {gone.status_code} {gone.text[:160]}")
    return out


def _incremental_rehearsal(
    client: Any, admin: dict[str, str], url: str
) -> list[tuple[str, str, str, str]]:
    """지표 증분(ADR 0024) — **바뀐 기간만** 다시 센 것과 전부 센 것을 같은 지표로 잰다.

    지표 둘: 자기 칸만(서비스월 x 판매월 코호트 x 증상 x 공장)과 참조 너머(기본 모델 x 증상 —
    ① 의 참조 칸 길). 전량 → 최근 기록 1천 건의 증상 · 1백 건의 서비스일을 **화면과 같은
    길로**(감사를 남기며) 고친다 → 증분(날짜 칸 색인 없이) → 색인을 세운다 → 고친 것을 되돌린다
    → 증분(색인으로) → 전량. 증분 뒤의 셀이 전량의 셀과 같은지도 본다. 끝나면 지표 · 색인을
    지운다(되돌린 기록은 처음 값이다 — 감사 기록만 남는다).
    """
    import uuid as uuid_module
    from datetime import date as date_type

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.database import engine as app_engine
    from app.modules.metrics import compute, timeindex
    from app.modules.metrics.models import MetricDef, MetricValue
    from app.modules.objects.models import ObjectInstance
    from app.modules.objects.services import audit_state
    from app.shared import audit

    out: list[tuple[str, str, str, str]] = []
    tag = uuid_module.uuid4().hex[:4]

    def row(label: str, took: float, note: str) -> None:
        out.append(("incremental", label, f"{took:.2f}초", note))
        print(f"{took:8.2f}초  {label}  {note}", flush=True)

    def when(address: str) -> dict[str, str]:
        return {"address": address, "grain": "month"}

    own = {
        "measure": "count",
        "time": when("properties.service_date"),
        "cohort": when("properties.sale_date"),
        "dimensions": [
            {"name": "symptom", "address": "properties.symptom"},
            {"name": "factory", "address": "properties.factory"},
        ],
    }
    through = {
        "measure": "count",
        "time": when("properties.service_date"),
        "cohort": when("properties.sale_date"),
        "dimensions": [
            {"name": "base_model", "address": "ref.model.base"},
            {"name": "symptom", "address": "properties.symptom"},
        ],
    }
    metrics = [
        (f"rh_inc_own_{tag}", "자기 칸", own),
        (f"rh_inc_ref_{tag}", "참조 너머", through),
    ]
    made: list[str] = []
    index_name: str | None = None

    def cells(slug: str) -> list[tuple[Any, ...]]:
        with SessionLocal() as db:
            metric = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
            assert metric is not None
            run = metric.cells_run_id or metric.current_run_id
            found = db.execute(
                select(
                    MetricValue.cell_hash,
                    MetricValue.period,
                    MetricValue.cohort,
                    MetricValue.count,
                ).where(MetricValue.metric_id == metric.id, MetricValue.run_id == run)
            ).all()
        return sorted((tuple(one) for one in found), key=str)

    def recount(slug: str, *, full: bool, label: str) -> None:
        started = time.perf_counter()
        with SessionLocal() as db:
            metric = db.scalar(select(MetricDef).where(MetricDef.slug == slug))
            assert metric is not None
            done = compute.run_one(
                db, metric, job_id=None, progress=lambda *_: None, full=full
            )
            note = (
                f"{done.mode} cells={done.cells:,} rows={done.rows:,} "
                f"periods={len(done.periods or [])} — {done.note}"
            )
        row(label, time.perf_counter() - started, note)

    def edit(changes: list[tuple[Any, dict[str, Any]]]) -> float:
        """화면의 고치기와 같은 감사 기록을 남기며 — 한 트랜잭션으로."""
        started = time.perf_counter()
        with SessionLocal() as db:
            for object_id, patch in changes:
                found = db.get(ObjectInstance, object_id)
                assert found is not None
                before = audit_state(found)
                found.properties = {**found.properties, **patch}
                audit.record(
                    db,
                    action="object.update",
                    actor=None,
                    target_table="objects",
                    target_id=found.id,
                    target_label=found.label,
                    changes=audit.diff(before, audit_state(found)),
                )
            db.commit()
        return time.perf_counter() - started

    try:
        for slug, label, spec in metrics:
            saved = client.post(
                "/api/metrics",
                params={"recompute": "false"},
                json={
                    "slug": slug,
                    "label": label,
                    "source_type_slug": "svc_case",
                    "spec": spec,
                },
                headers=admin,
            )
            if saved.status_code != 201:
                row(f"정의 — {label}", 0.0, saved.text[:160])
                return out
            made.append(slug)
            recount(slug, full=True, label=f"전량 — {label}")

        with SessionLocal() as db:
            recent = db.execute(
                text(
                    "SELECT o.id, o.properties ->> 'symptom', o.properties ->> 'service_date' "
                    "FROM objects o JOIN object_types t ON t.id = o.type_id "
                    "WHERE t.slug = 'svc_case' AND o.deleted_at IS NULL "
                    "ORDER BY o.properties ->> 'service_date' DESC NULLS LAST LIMIT 1100"
                )
            ).all()
        symptoms = [(one[0], {"symptom": one[1]}) for one in recent[:1000]]
        dates = [(one[0], {"service_date": one[2]}) for one in recent[1000:]]
        flipped = [(object_id, {"symptom": "S00"}) for object_id, _ in symptoms]

        def moved(raw: str) -> str:
            day = date_type.fromisoformat(raw[:10])
            back = date_type(day.year - (day.month == 1), (day.month - 2) % 12 + 1, 1)
            return back.isoformat()

        shifted = [
            (object_id, {"service_date": moved(old["service_date"])})
            for object_id, old in dates
        ]
        took = edit(flipped + shifted)
        row("고치기 — 증상 1천 · 서비스일 1백(한 달 앞으로)", took, "감사 기록과 함께")
        for slug, label, _spec in metrics:
            recount(slug, full=False, label=f"증분(색인 없음) — {label}")

        with SessionLocal() as db:
            source = db.scalar(text("SELECT id FROM object_types WHERE slug = 'svc_case'"))
        index_name = timeindex.name_of(source, "service_date")
        started = time.perf_counter()
        with app_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.execute(text(timeindex.create_sql(index_name, source, "service_date")))
        row("날짜 칸 색인 세우기(CONCURRENTLY)", time.perf_counter() - started, index_name)

        took = edit(symptoms + dates)
        row("되돌리기 — 같은 1천 1백 건", took, "")
        incremental: dict[str, list[tuple[Any, ...]]] = {}
        for slug, label, _spec in metrics:
            recount(slug, full=False, label=f"증분(색인) — {label}")
            incremental[slug] = cells(slug)
        for slug, label, _spec in metrics:
            recount(slug, full=True, label=f"전량 — {label}")
            same = cells(slug) == incremental[slug]
            row(f"증분 = 전량 — {label}", 0.0, "같다" if same else "**다르다**")
    finally:
        if index_name is not None:
            with app_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                connection.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))
        for slug in reversed(made):
            client.delete(f"/api/metrics/{slug}", headers=admin)
    return out


#: 규칙 기준(ADR 0015)의 리허설 — 규칙 열(차례가 뜻이다). 일곱째는 **참조 너머**(SKU 의 기본
#: 코드)라 기록마다 EXISTS 하나를 더 판다.
_SCALE_RULES: list[dict[str, Any]] = [
    {"label": "SW", "when": [{"field": "handling", "op": "eq", "value": "H7"}]},
    {"label": "고객과실", "when": [{"field": "symptom", "op": "in", "value": "S01|S02|S07"}]},
    {
        "label": "NTF",
        "when": [
            {"field": "parts", "op": "empty", "value": ""},
            {"field": "handling", "op": "eq", "value": "H0"},
        ],
    },
    {
        "label": "보증 외 고비용",
        "when": [
            {"field": "warranty", "op": "eq", "value": "false"},
            {"field": "cost", "op": "gte", "value": "8000"},
        ],
    },
    {
        "label": "센터 점검",
        "when": [
            {"field": "center", "op": "in", "value": "C00|C01|C02"},
            {"field": "parts", "op": "empty", "value": ""},
        ],
    },
    {
        "label": "국가 캠페인",
        "when": [
            {"field": "country", "op": "eq", "value": "N05"},
            {"field": "symptom", "op": "in", "value": "S10|S11|S12|S13|S14"},
        ],
    },
    {
        "label": "초기 모델",
        "when": [{"field": "ref.model.base_code", "op": "starts", "value": "SM-X000"}],
    },
    {"label": "비용 없음", "when": [{"field": "cost", "op": "eq", "value": "0"}]},
    {
        "label": "보증 H3",
        "when": [
            {"field": "handling", "op": "eq", "value": "H3"},
            {"field": "warranty", "op": "eq", "value": "true"},
        ],
    },
    {"label": "제품결함", "when": [{"field": "parts", "op": "notempty", "value": ""}]},
]


def _rules_rehearsal(client: Any, admin: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """규칙 기준(ADR 0015) — 보통 기준의 지표에 **규칙 열 개짜리 기준**을 더하면 계산이 얼마나
    느려지나. 같은 모양 셋(보통 · 규칙 열 · 참조 너머를 뺀 규칙 아홉)을 계획 → 저장 →
    재계산하고, 구성비 표 · 추이와 셀의 「N건 보기」(제외 묶음 열까지) 목록 수가 셀의 수와
    같은지 본다. 끝나면 지표를 지운다."""
    import uuid as uuid_module

    out: list[tuple[str, str, str, str]] = []
    tag = uuid_module.uuid4().hex[:4]

    def row(label: str, took: float, note: str) -> None:
        out.append(("rules", label, f"{took:.2f}초", note))
        print(f"{took:8.2f}초  {label}  {note}", flush=True)

    base = {
        "measure": "count",
        "time": {"address": "properties.service_date", "grain": "month"},
        "dimensions": [
            {"name": "symptom", "address": "properties.symptom"},
            {"name": "factory", "address": "properties.factory"},
        ],
    }

    def nature(rules: list[dict[str, Any]]) -> dict[str, Any]:
        return {"name": "nature", "label": "성격", "rules": rules, "else": "기타"}

    own_rules = [one for one in _SCALE_RULES if one["label"] != "초기 모델"]
    definitions = [
        (f"rh_rule_base_{tag}", "보통 기준(증상 x 공장)", "svc_case", base),
        (
            f"rh_rule_ten_{tag}",
            "+ 규칙 기준 열(참조 너머 하나)",
            "svc_case",
            {**base, "dimensions": [*base["dimensions"], nature(_SCALE_RULES)]},
        ),
        (
            f"rh_rule_own_{tag}",
            "+ 규칙 기준 아홉(자기 칸만)",
            "svc_case",
            {**base, "dimensions": [*base["dimensions"], nature(own_rules)]},
        ),
    ]
    made: list[str] = []
    try:
        planned = client.post(
            "/api/metrics/plan",
            json={"source_type_slug": "svc_case", "spec": definitions[1][3]},
            headers=admin,
        ).json()
        (plan,) = planned.get("rules") or [{}]
        won = " · ".join(
            f"{one['label']} {one['won']:,}" + ("(막힘)" if one["blocked"] else "")
            for one in plan.get("rules", [])
        )
        print(
            f"계획의 규칙별 수(센 기록 {plan.get('seen', 0):,}건, "
            f"표본={plan.get('sampled')}): {won} · 기타 {plan.get('other_count', 0):,} / "
            f"경고 {planned.get('warnings')}",
            flush=True,
        )
        if not _build_metrics(client, admin, definitions, row, made):
            return out
        slug = definitions[1][0]
        reads: list[tuple[str, str, dict[str, Any]]] = [
            (
                "구성비 표 — 성격 x 월(기간마다 100%)",
                f"/api/metrics/{slug}/values",
                {"dims": "nature", "by": "period", "composition": "nature"},
            ),
            (
                "구성비 추이 — 성격별 선",
                f"/api/metrics/{slug}/series",
                {"split": "nature", "composition": "true"},
            ),
            ("표 — 성격 x 공장", f"/api/metrics/{slug}/values", {"dims": "nature,factory"}),
        ]
        for label, path, params in reads:
            times: list[float] = []
            shape = ""
            for _ in range(3):
                started = time.perf_counter()
                got = client.get(path, params=params, headers=admin)
                times.append(time.perf_counter() - started)
                if got.status_code >= 400:
                    shape = f"HTTP {got.status_code} {got.text[:120]}"
                    break
                shape = _metric_shape(got.json())
            row(label, statistics.median(times), shape)
        # 셀의 건 보기 — 목록의 수가 셀의 수와 같은가(기타는 제외 묶음 열).
        table = client.get(
            f"/api/metrics/{slug}/values", params={"dims": "nature"}, headers=admin
        ).json()
        counts = {one["dims"]["nature"]: one for one in table["cells"]}
        print(
            "성격별 건수: "
            + " · ".join(f"{key} {one['count']:,}" for key, one in counts.items()),
            flush=True,
        )
        for key in ("기타", "NTF", "초기 모델", "제품결함"):
            cell = counts.get(key)
            if cell is None:
                continue
            started = time.perf_counter()
            listed = client.get(
                "/api/objects/svc_case",
                params={**cell["drill"]["params"], "limit": 1},
                headers=admin,
            )
            took = time.perf_counter() - started
            total = listed.json().get("total") if listed.status_code == 200 else listed.text
            groups = sum(1 for one in cell["drill"]["params"] if one.startswith("nf"))
            row(
                f"건 보기 목록 — {key}(제외 묶음 조건 {groups}개)",
                took,
                f"셀 {cell['count']:,} · 목록 {total} · 같음={total == cell['count']}",
            )
    finally:
        for one in reversed(made):
            client.delete(f"/api/metrics/{one}", headers=admin)
    return out


def _metric_shape(body: dict[str, Any]) -> str:
    """지표 응답 한 마디 — 수와, 숨기지 말아야 할 것(잘림 · 겹침 · 분모 없음)."""
    if "rows" in body and "ages" in body:
        rows = body["rows"]
        note = f"코호트 {len(rows)} x 경과 {len(body['ages'])}"
        if rows and rows[0]["cells"]:
            last = rows[0]["cells"][-1]
            note += f" · 첫 코호트 끝 누적 비율 {last.get('ratio')}"
    elif "lines" in body:
        note = f"선 {len(body['lines'])}" + (" (잘림)" if body.get("lines_truncated") else "")
    elif "cells" in body:
        note = f"셀 {len(body['cells'])} · 합 {body.get('total_count'):,}"
    else:
        note = f"값 {len(body.get('values', []))}"
    if body.get("overlap"):
        note += " · 겹침"
    if body.get("truncated"):
        note += " · 잘림"
    missing = (body.get("denominator") or {}).get("missing")
    if missing:
        note += f" · 분모 없는 셀 {missing}"
    return note


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


def _periods_rehearsal(repeat: int) -> list[tuple[str, str, str, str]]:
    """관계의 유효 기간(ADR 0039) — 기준일(`at`)을 주면 트리 · 하위 전체 · 롤업 · 연결 경로가
    얼마나 달라지나, 그리고 **유효 기간이 꺼진 종류**(이 DB 의 `sku_base` 6,000줄)의 질의가
    기준일 때문에 느려지지 않나.

    한 세션 안에서 심고 재고 **롤백한다**(아무것도 안 남는다): 유효 기간을 켠 BOM 관계 하나와
    부품 타입 — 제품 40 · 어셈블리 400 · 서브 4,000 · 부품 36,000(선 40,440)에, 부품 선의 20%
    (7,200)를 「3월 1일부터 다른 부품으로 바뀜」 으로(끝난 선 + 새 부품의 새 선) — 선 47,600,
    기간이 든 선 14,400. 기준일 None(예전과 같은 SQL — 판정이 `TRUE`) · 2월 · 4월을 견준다."""
    from datetime import date as date_type

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.modules.accounts.models import User
    from app.modules.objects import graph
    from app.modules.objects.models import ObjectRelation
    from app.modules.ontology.models import ObjectType

    out: list[tuple[str, str, str, str]] = []

    def row(label: str, took: float, note: str) -> None:
        out.append(("periods", label, f"{took:.3f}초", note))
        print(f"{took:8.3f}초  {label}  {note}", flush=True)

    def timed(call: Callable[[], Any]) -> tuple[float, Any]:
        times: list[float] = []
        found: Any = None
        for _ in range(max(repeat, 3)):
            started = time.perf_counter()
            found = call()
            times.append(time.perf_counter() - started)
        return statistics.median(times), found

    feb, apr = date_type(2026, 2, 15), date_type(2026, 4, 15)
    days: list[tuple[str, date_type | None]] = [
        ("기간 안 봄", None),
        ("2월", feb),
        ("4월", apr),
    ]
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == ADMIN))
        assert user is not None
        # --- 꺼진 종류(sku_base) — 기준일이 붙어도 같은 속도인가 ---------------------------
        base_type = db.scalar(select(ObjectType).where(ObjectType.slug == "plm_base"))
        assert base_type is not None
        hub = db.scalar(
            select(ObjectRelation.dst_object_id)
            .where(ObjectRelation.relation == "sku_base")
            .group_by(ObjectRelation.dst_object_id)
            .order_by(func_count_desc())
            .limit(1)
        )
        for label, day in days[:2]:
            took, kids = timed(
                lambda day=day: graph.child_ids(
                    db, relation="sku_base", parent_id=hub, parent_end="dst", at=day
                )
            )
            row(f"꺼진 종류 · 펼침 한 단계(기본 모델 → SKU) — {label}", took, f"{len(kids)}개")
            took, roots = timed(
                lambda day=day: graph.parentless_ids(
                    db,
                    relation="sku_base",
                    type_id=base_type.id,
                    parent_end="dst",
                    at=day,
                    user=user,
                )
            )
            row(f"꺼진 종류 · 뿌리 찾기(기본 모델 2,001) — {label}", took, f"{len(roots)}개")
            took, counts = timed(
                lambda day=day, roots=roots: graph.child_counts(
                    db,
                    relation="sku_base",
                    parent_ids=roots,
                    parent_end="dst",
                    at=day,
                    user=user,
                )
            )
            row(f"꺼진 종류 · 자식 수(뿌리 전부) — {label}", took, f"{len(counts)}개")

        # --- 켜진 종류 — 심는다(롤백) ------------------------------------------------------
        seeded, part, kind = _seed_bom(db)
        edges_n, dated_n = _bom_counts(db, kind.slug)
        row(
            "켜진 종류 · 심기(객체 47,640 · 선 INSERT, 트리거 포함)",
            seeded,
            f"선 {edges_n:,} · 기간 {dated_n:,}",
        )
        product, leaf, products = _bom_ends(db, part.id)
        tree = graph.Tree(kind.slug, "dst", part.id, part.label)
        for label, day in days:
            took, kids = timed(
                lambda day=day: graph.child_ids(
                    db, relation=kind.slug, parent_id=product, parent_end="dst", at=day
                )
            )
            row(f"켜진 종류 · 트리 펼침 한 단계(제품) — {label}", took, f"{len(kids)}개")
            took, roots = timed(
                lambda day=day: graph.parentless_ids(
                    db,
                    relation=kind.slug,
                    type_id=part.id,
                    parent_end="dst",
                    at=day,
                    user=user,
                )
            )
            row(f"켜진 종류 · 뿌리 · 외톨이 찾기 — {label}", took, f"{len(roots)}개")
            took, below = timed(
                lambda day=day: graph.descendant_ids(
                    db, relation=kind.slug, root_id=product, parent_end="dst", at=day
                )
            )
            row(
                f"켜진 종류 · 하위 전체(제품 하나 — 롤업이 펼치는 것) — {label}",
                took,
                f"{len(below)}개",
            )
            took, everything = timed(
                lambda day=day: graph.subtree_ids(db, tree, products, at=day)
            )
            row(
                f"켜진 종류 · 하위 전체(제품 40 — 조건 `under`) — {label}",
                took,
                f"{len(everything)}개",
            )
            took, folded = timed(lambda day=day: graph.levels(db, tree, 1, at=day))
            row(
                f"켜진 종류 · 계층 축 @1(전체 접기) — {label}",
                took,
                f"깊은 노드 {len(folded.up):,}",
            )
            took, found = timed(
                lambda day=day: graph.shortest_paths(
                    db, source=leaf, target=product, user=user, at=day, max_depth=6
                )
            )
            row(
                f"켜진 종류 · 연결 경로(부품 → 제품) — {label}",
                took,
                f"길이 {found.length} · 들른 {found.visited:,}",
            )
    finally:
        db.rollback()
        db.close()
    return out


def _edgefields_rehearsal(
    client: Any, admin: dict[str, str], repeat: int
) -> list[tuple[str, str, str, str]]:
    """선의 칸 조건 · 이어진 칸(ADR 0043).

    1. **이어진 칸**(읽기만 — 이 DB 그대로): 개발모델 목록 200줄에 열 셋 — 한 걸음 관계
       (`out.sku_base.label`), 두 걸음 참조(`ref.task.ref.project.label`), 기록으로 내려가는 것
       (`in.svc_case:model` — 뜨거운 모델은 기록 10만 건, 상한 20 에서 자른다). 열 없는 목록과
       견준다. 서비스 기록 목록 200줄에 두 걸음 열(참조 → 관계)도.
    2. **선의 칸 조건**(한 세션 안에서 심고 롤백): `--only periods` 와 같은 BOM(선 47,600 ·
       수량 칸 qty 1 ~ 4)에서 묶지 않은 · 묶은(같은 관계 묶음) · 제외 묶음 안의 묶음 · 날짜
       선의 칸 · 선의 칸 통계 · 제품 40 의 이어진 칸."""
    from datetime import date as date_type

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.modules.accounts.models import User
    from app.modules.objects import conditions, linked, paths, summary
    from app.modules.objects.models import ObjectInstance

    out: list[tuple[str, str, str, str]] = []

    def row(label: str, took: float, note: str) -> None:
        out.append(("edgefields", label, f"{took:.3f}초", note))
        print(f"{took:8.3f}초  {label}  {note}", flush=True)

    def timed(call: Callable[[], Any]) -> tuple[float, Any]:
        times: list[float] = []
        found: Any = None
        for _ in range(max(repeat, 3)):
            started = time.perf_counter()
            found = call()
            times.append(time.perf_counter() - started)
        return statistics.median(times), found

    # --- 1. 이어진 칸 — 이 DB 그대로(읽기만) ------------------------------------------------
    def page(slug: str, columns: list[str]) -> Callable[[], Any]:
        return lambda: client.get(
            f"/api/objects/{slug}",
            params={"limit": 200, "columns": columns},
            headers=admin,
        )

    def shaped(response: Any, columns: list[str]) -> str:
        if response.status_code >= 400:
            return f"HTTP {response.status_code} {response.text[:120]}"
        body = response.json()
        filled = {
            column: sum(1 for cells in body["linked"].values() if column in cells)
            for column in columns
        }
        more = sum(
            1 for cells in body["linked"].values() for cell in cells.values() if cell["more"]
        )
        return f"{len(body['items'])}줄 · 채운 칸 {filled} · 더 있음 {more}"

    model_columns = ["out.sku_base.label", "ref.task.ref.project.label", "in.svc_case:model"]
    for label, slug, columns in (
        ("이어진 칸 없음 — 개발모델 200줄(기준)", "plm_model", []),
        (
            "이어진 칸 셋 — 개발모델 200줄(관계 · 두 걸음 · 기록 상한)",
            "plm_model",
            model_columns,
        ),
        ("이어진 칸 — 기록으로(`in.svc_case:model`)만", "plm_model", ["in.svc_case:model"]),
        (
            "이어진 칸 — 기록의 증상(`in.svc_case:model.symptom`)",
            "plm_model",
            ["in.svc_case:model.symptom"],
        ),
        ("이어진 칸 없음 — 서비스 기록 200줄(기준)", "svc_case", []),
        (
            "이어진 칸 둘 — 서비스 기록 200줄(참조 → 관계 · 참조 → 참조)",
            "svc_case",
            ["ref.model.out.sku_base.label", "ref.model.ref.task.label"],
        ),
    ):
        took, response = timed(page(slug, columns))
        row(label, took, shaped(response, columns))

    # --- 2. 선의 칸 조건 — 심고 롤백 --------------------------------------------------------
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == ADMIN))
        assert user is not None
        seeded, part, kind = _seed_bom(db, quantities=True)
        # 끝을 부품으로 정한다 — 끝이 안 정해진 관계는 「있음 · 없음」 만 걸린다.
        kind.src_type_slugs = [part.slug]
        kind.dst_type_slugs = [part.slug]
        db.flush()
        row("심기(BOM — 수량 칸 qty)", seeded, f"선 {_bom_counts(db, kind.slug)[0]:,}")
        _product, _leaf, products = _bom_ends(db, part.id)
        today = date_type.today()
        # X — 수량이 4 가 아닌 서브(N442, 수량 (442 % 4) + 1 = 3). 묶지 않으면 그 부모의 다른
        # 서브(수량 4)와 짝이 되어 걸리고, 묶으면 안 걸린다.
        x = db.scalar(
            select(ObjectInstance.id).where(
                ObjectInstance.type_id == part.id, ObjectInstance.key == "N442"
            )
        )
        rel = kind.slug

        def count(asked: list[conditions.Condition]) -> Callable[[], int]:
            def run() -> int:
                resolver = paths.Resolver(db, part, viewer=user, at=today)
                stmt = (
                    select(func_count())
                    .select_from(ObjectInstance)
                    .where(
                        ObjectInstance.type_id == part.id, ObjectInstance.deleted_at.is_(None)
                    )
                )
                return int(db.scalar(conditions.apply(stmt, [], asked, resolver)) or 0)

            return run

        cond = conditions.Condition
        for label, asked in (
            ("선의 칸 · 묶지 않음(`in.bom~qty ≥ 4`)", [cond(f"in.{rel}~qty", "gte", "4")]),
            (
                "선의 칸 · 묶지 않은 둘(`in.bom = X` · `~qty ≥ 4`)",
                [cond(f"in.{rel}", "eq", str(x)), cond(f"in.{rel}~qty", "gte", "4")],
            ),
            (
                "선의 칸 · 같은 관계 묶음(X 이고 4개 이상)",
                [
                    cond(f"in.{rel}", "eq", str(x), edge=1),
                    cond(f"in.{rel}~qty", "gte", "4", edge=1),
                ],
            ),
            (
                "선의 칸 · 같은 관계 묶음 — 상대의 칸과(`.label` 시작 N44 이고 4개 이상)",
                [
                    cond(f"in.{rel}.label", "starts", "N44", edge=1),
                    cond(f"in.{rel}~qty", "gte", "4", edge=1),
                ],
            ),
            (
                "선의 칸 · 제외 묶음 안의 같은 관계 묶음",
                [
                    cond(f"in.{rel}.label", "starts", "N44", exclude=1, edge=1),
                    cond(f"in.{rel}~qty", "gte", "4", exclude=1, edge=1),
                ],
            ),
            (
                "선의 칸 · 시작일(`in.bom~valid_from ≥ 3월 1일`)",
                [cond(f"in.{rel}~valid_from", "gte", "2026-03-01")],
            ),
        ):
            took, found = timed(count(asked))
            row(label, took, f"{found:,}개")

        def grouped(address: str) -> Callable[[], Any]:
            def run() -> Any:
                filtered = select(ObjectInstance).where(
                    ObjectInstance.type_id == part.id, ObjectInstance.deleted_at.is_(None)
                )
                return summary.summarize(
                    db, part, filtered, group_by=address, viewer=user, at=today
                )

            return run

        for address in (f"in.{rel}~qty", f"out.{rel}~qty"):
            took, found = timed(grouped(address))
            row(
                f"선의 칸 통계(`{address.replace(rel, 'bom')}`)",
                took,
                ", ".join(f"{one.key}: {one.count:,}" for one in found.buckets[:5]),
            )

        def cells() -> Any:
            resolver = paths.Resolver(db, part, viewer=user, at=today)
            columns = linked.parse(resolver, [f"in.{rel}~qty", f"in.{rel}.label"])
            return linked.cells(db, user, columns, products)

        took, found = timed(cells)
        row(
            "이어진 칸 — 제품 40 x (`in.bom~qty` · `in.bom.label`)",
            took,
            f"{len(found)}줄 · 더 있음 "
            f"{sum(1 for one in found.values() for cell in one.values() if cell.more)}",
        )
    finally:
        db.rollback()
        db.close()
    return out


def _seed_bom(
    db: Any, *, quantities: bool = False, weights: bool = False
) -> tuple[float, Any, Any]:
    """유효 기간을 켠 BOM 하나를 심는다 — `--only periods` · `--only explode` 가 같은 모양을
    쓴다.

    제품 40 · 어셈블리 400 · 서브 4,000 · 부품 36,000(선 40,440)에, 부품 선의 20%(7,200)를
    「3월 1일부터 다른 부품으로 바뀜」 으로(끝난 선 + 새 부품의 새 선) — 선 47,600, 기간이 든
    선 14,400. `quantities` 면 선마다 수량 칸 `qty`(1 ~ 4)를 싣고 그 종류의 수량 칸으로 정한다.
    `weights` 면 객체마다 무게(`weight`)와 신뢰도(`rel`)를 싣는다. (걸린 초, 타입, 관계
    종류)."""
    from app.modules.ontology.models import ObjectType, PropertyDef, RelationType

    tag = uuid_hex()
    part = ObjectType(slug=f"per_part_{tag}", label=f"BOM 부품 {tag}")
    db.add(part)
    kind = RelationType(
        slug=f"per_bom_{tag}",
        label="BOM",
        transitive=True,
        acyclic=True,
        effectivity=True,
        quantity_key="qty" if quantities else "",
    )
    db.add(kind)
    db.flush()
    if quantities:
        db.add(
            PropertyDef(
                owner_kind="relation",
                owner_id=kind.id,
                key="qty",
                label="수량",
                data_type="number",
            )
        )
    if weights:
        for key, label in (("weight", "무게"), ("rel", "신뢰도")):
            db.add(
                PropertyDef(
                    owner_kind="type",
                    owner_id=part.id,
                    key=key,
                    label=label,
                    data_type="number",
                )
            )
    db.flush()
    started = time.perf_counter()
    props = (
        "jsonb_build_object('weight', (g % 7) + 1, 'rel', 0.999)" if weights else "'{}'::jsonb"
    )
    db.execute(
        text(
            f"""
            INSERT INTO objects (id, type_id, label, key, properties)
            SELECT gen_random_uuid(), :type, 'N' || g, 'N' || g, {props}
              FROM generate_series(1, 47640) g
            """
        ),
        {"type": str(part.id)},
    )
    amount = "jsonb_build_object('qty', (c.k % 4) + 1)" if quantities else "'{}'::jsonb"
    # 1 ~ 40 제품, 41 ~ 440 어셈블리, 441 ~ 4440 서브, 4441 ~ 40440 부품,
    # 40441 ~ 47640 3월부터 바뀐 부품.
    db.execute(
        text(
            f"""
            WITH n AS (
                SELECT id, substr(key, 2)::int AS k FROM objects WHERE type_id = :type
            )
            INSERT INTO object_relations (id, src_object_id, dst_object_id, relation,
                                          valid_from, valid_to, properties)
            SELECT gen_random_uuid(), c.id, p.id, :rel,
                   CASE WHEN c.k > 40440 THEN DATE '2026-03-01' END,
                   CASE WHEN c.k BETWEEN 4441 AND 40440 AND c.k % 5 = 0
                        THEN DATE '2026-03-01' END,
                   {amount}
              FROM n c
              JOIN n p ON p.k = CASE
                   WHEN c.k BETWEEN 41 AND 440 THEN (c.k - 41) / 10 + 1
                   WHEN c.k BETWEEN 441 AND 4440 THEN (c.k - 441) / 10 + 41
                   WHEN c.k BETWEEN 4441 AND 40440 THEN (c.k - 4441) / 9 + 441
                   WHEN c.k > 40440 THEN ((c.k - 40441) * 5 + 4445 - 4441) / 9 + 441
              END
            """
        ),
        {"type": str(part.id), "rel": kind.slug},
    )
    db.execute(text("ANALYZE objects"))
    db.execute(text("ANALYZE object_relations"))
    return time.perf_counter() - started, part, kind


def _bom_counts(db: Any, relation: str) -> tuple[int, int]:
    """(선 수, 기간이 든 선 수)."""
    from sqlalchemy import select

    from app.modules.objects.models import ObjectRelation

    edges_n = db.scalar(
        select(func_count())
        .select_from(ObjectRelation)
        .where(ObjectRelation.relation == relation)
    )
    dated_n = db.scalar(
        select(func_count())
        .select_from(ObjectRelation)
        .where(
            ObjectRelation.relation == relation,
            (ObjectRelation.valid_from.is_not(None)) | (ObjectRelation.valid_to.is_not(None)),
        )
    )
    return int(edges_n or 0), int(dated_n or 0)


def _bom_ends(db: Any, type_id: Any) -> tuple[Any, Any, list[Any]]:
    """(제품 N1, 부품 N4445, 제품 40)."""
    from sqlalchemy import select

    from app.modules.objects.models import ObjectInstance

    def one(key: str) -> Any:
        return db.scalar(
            select(ObjectInstance.id).where(
                ObjectInstance.type_id == type_id, ObjectInstance.key == key
            )
        )

    products = list(
        db.scalars(
            select(ObjectInstance.id).where(
                ObjectInstance.type_id == type_id,
                ObjectInstance.key.in_([f"N{n}" for n in range(1, 41)]),
            )
        )
    )
    return one("N1"), one("N4445"), products


def _explode_rehearsal(repeat: int) -> list[tuple[str, str, str, str]]:
    """BOM 전개(ADR 0040) — 수량을 경로 따라 곱하는 세 보기와 수량 반영 롤업.

    한 세션 안에서 심고 재고 **롤백한다**: `--only periods` 와 같은 BOM(유효 기간, 선 47,600)에
    선마다 수량(1 ~ 4)을 싣고, **공용 부품** 200개를 서브마다 둘씩 더 잇는다(서브 4,000 x 2 =
    선 8,000 — 공용 부품 하나가 서브 40개 · 제품 여럿에서 쓰인다). 그리고 **경로가 곱으로 느는
    사다리**(단마다 둘, 20단 — 단마다 앞 단 둘 모두에 이어, 선 74로 맨 아래 하나까지 경로
    2^18)를 따로 세워 선 집합과 경로 수의 차이를 본다. 기준일은 4월(바뀐 부품이 든 구성)."""
    from datetime import date as date_type

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.modules.accounts.models import User
    from app.modules.objects import explode, graph, rollup
    from app.modules.objects.models import ObjectInstance
    from app.modules.ontology.models import ObjectType, RelationType

    out: list[tuple[str, str, str, str]] = []

    def row(label: str, took: float, note: str) -> None:
        out.append(("explode", label, f"{took:.3f}초", note))
        print(f"{took:8.3f}초  {label}  {note}", flush=True)

    def timed(call: Callable[[], Any]) -> tuple[float, Any]:
        times: list[float] = []
        found: Any = None
        for _ in range(max(repeat, 3)):
            started = time.perf_counter()
            found = call()
            times.append(time.perf_counter() - started)
        return statistics.median(times), found

    apr = date_type(2026, 4, 15)
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == ADMIN))
        assert user is not None
        seeded, part, kind = _seed_bom(db, quantities=True, weights=True)
        started = time.perf_counter()
        # 공용 부품 200(N47641 ~ N47840) — 서브(441 ~ 4440)마다 둘(k % 200, k * 7 % 200).
        db.execute(
            text(
                """
                INSERT INTO objects (id, type_id, label, key, properties)
                SELECT gen_random_uuid(), :type, 'N' || g, 'N' || g,
                       jsonb_build_object('weight', 0.1, 'rel', 0.9999)
                  FROM generate_series(47641, 47840) g
                """
            ),
            {"type": str(part.id)},
        )
        db.execute(
            text(
                """
                WITH n AS (
                    SELECT id, substr(key, 2)::int AS k FROM objects WHERE type_id = :type
                )
                INSERT INTO object_relations (id, src_object_id, dst_object_id, relation,
                                              properties)
                SELECT gen_random_uuid(), c.id, s.id, :rel,
                       jsonb_build_object('qty', (s.k % 3) + 2)
                  FROM n s
                  JOIN n c ON c.k IN (47641 + s.k % 200, 47641 + (s.k * 7 + 1) % 200)
                 WHERE s.k BETWEEN 441 AND 4440
                """
            ),
            {"type": str(part.id), "rel": kind.slug},
        )
        db.execute(text("ANALYZE object_relations"))
        seeded += time.perf_counter() - started
        edges_n, _ = _bom_counts(db, kind.slug)
        row("심기(객체 47,840 · 선 INSERT, 수량 · 무게 포함)", seeded, f"선 {edges_n:,}")
        product, _leaf, products = _bom_ends(db, part.id)
        common = db.scalar(
            select(ObjectInstance.id).where(
                ObjectInstance.type_id == part.id, ObjectInstance.key == "N47641"
            )
        )
        root = db.get(ObjectInstance, product)
        tree = graph.Tree(kind.slug, "dst", part.id, part.label)

        for mode, label in (
            ("summary", "정리"),
            ("indented", "단계별"),
        ):
            took, found = timed(
                lambda mode=mode: explode.explode(db, user, tree, root, mode=mode, at=apr)
            )
            row(
                f"제품 하나 · {label}",
                took,
                f"객체 {found.objects:,} · 선 {found.links:,} · 줄 {len(found.lines):,}"
                f"/{found.lines_total:,}",
            )
        took, found = timed(
            lambda: explode.explode(
                db, user, tree, db.get(ObjectInstance, common), mode="where_used", at=apr
            )
        )
        row(
            "공용 부품 하나 · 어디에 쓰였나",
            took,
            f"맨 위 {len(found.tops)} · 줄 {len(found.lines):,}/{found.lines_total:,} · "
            f"선 {found.links:,}",
        )
        took, found = timed(
            lambda: [
                explode.explode(
                    db, user, tree, db.get(ObjectInstance, one), mode="summary", at=apr
                )
                for one in products
            ]
        )
        row(
            "제품 40 · 정리(하나씩)",
            took,
            f"객체 합 {sum(one.objects for one in found):,}",
        )
        took, links = timed(
            lambda: explode.fetch(db, tree, product, up=False, at=apr, quantity_key="qty")
        )
        row("제품 하나 · 선 집합만(SQL)", took, f"선 {len(links.links):,}")

        holder = db.get(ObjectType, part.id)
        assert holder is not None
        for specs, label in (
            ([{"property": "weight", "fn": "sum"}], "롤업 합계(예전 — 하위 전체)"),
            (
                [
                    {"property": "weight", "fn": "sum", "weighted": True},
                    {"property": "weight", "fn": "sum", "weighted": True, "leaves_only": True},
                    {
                        "property": "rel",
                        "fn": "product",
                        "weighted": True,
                        "leaves_only": True,
                    },
                ],
                "수량 반영 롤업 셋(합계 · 말단만 합계 · 곱)",
            ),
        ):
            holder.list_view = {
                "tree": {"relation": kind.slug, "parent": "dst"},
                "rollups": specs,
            }
            took, found = timed(
                lambda: rollup.compute(
                    db, user, holder, root, [], relation=kind.slug, parent_end="dst", at=apr
                )
            )
            row(
                f"제품 하나 · {label}",
                took,
                " · ".join(
                    f"{one.value:,.4g}" if one.value is not None else "-" for one in found
                ),
            )

        # --- 사다리 — 선은 78, 경로는 2^19 ----------------------------------------------
        ladder = RelationType(
            slug=f"ladder_{uuid_hex()}", label="사다리", transitive=True, acyclic=True
        )
        db.add(ladder)
        db.flush()
        db.execute(
            text(
                """
                INSERT INTO objects (id, type_id, label, key)
                SELECT gen_random_uuid(), :type, 'L' || g, 'L' || g
                  FROM generate_series(0, 39) g
                """
            ),
            {"type": str(part.id)},
        )
        db.execute(
            text(
                """
                WITH n AS (
                    SELECT id, substr(key, 2)::int AS k FROM objects
                     WHERE type_id = :type AND key LIKE 'L%'
                )
                INSERT INTO object_relations (id, src_object_id, dst_object_id, relation)
                SELECT gen_random_uuid(), c.id, p.id, :rel
                  FROM n p JOIN n c ON c.k / 2 = p.k / 2 + 1
                """
            ),
            {"type": str(part.id), "rel": ladder.slug},
        )
        top = db.scalar(
            select(ObjectInstance).where(
                ObjectInstance.type_id == part.id, ObjectInstance.key == "L0"
            )
        )
        steps = graph.Tree(ladder.slug, "dst", part.id, part.label)
        for mode, label in (("summary", "정리"), ("indented", "단계별(잘림)")):
            took, found = timed(
                lambda mode=mode: explode.explode(db, user, steps, top, mode=mode, at=apr)
            )
            row(
                f"사다리(20단 x 2) · {label}",
                took,
                f"선 {found.links} · 줄 {len(found.lines):,}/{found.lines_total:,} · "
                f"잘림 {found.truncated}",
            )
    finally:
        db.rollback()
        db.close()
    return out


def _lineage_rehearsal(repeat: int) -> list[tuple[str, str, str, str]]:
    """파생 · 계보와 개정 고정(ADR 0041) — 낡음 판정이 규모에서 얼마나 드나.

    한 세션 안에서 심고 재고 **롤백한다**(아무것도 안 남는다): 도면 2,000(개정 칸 `rev`) →
    메시 10,000 → 결과 50,000. 도면 → 메시는 원천이 **출발**인 계보 종류(메시 하나에 도면 하나,
    도면 하나에 메시 다섯), 결과 → 메시는 원천이 **도착**인 종류(결과 하나에 메시 하나, 메시
    하나에 결과 다섯) — 두 방향이 한 사슬에 섞인다. 선 6만 줄은 트리거가 박는다(넣기의 값을
    잰다 — 같은 수의 계보 아닌 선 · 트리거를 끈 선과 견준다). 그다음 도면 40(2%)의 개정을
    바꾸고(메시 200 바로 낡음 · 결과 1,000 위에서 낡음), 메시 100(1%)의 이름만 고친다(개정 칸이
    없어 바뀐 때로 — 결과 500 바로 낡음)."""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.modules.accounts.models import User
    from app.modules.objects import lineage, lineage_review
    from app.modules.objects.models import ObjectInstance
    from app.modules.ontology.models import ObjectType, PropertyDef, RelationType
    from app.modules.review.registry import Filters

    out: list[tuple[str, str, str, str]] = []

    def row(label: str, took: float, note: str) -> None:
        out.append(("lineage", label, f"{took:.3f}초", note))
        print(f"{took:8.3f}초  {label}  {note}", flush=True)

    def timed(call: Callable[[], Any]) -> tuple[float, Any]:
        times: list[float] = []
        found: Any = None
        for _ in range(max(repeat, 3)):
            started = time.perf_counter()
            found = call()
            times.append(time.perf_counter() - started)
        return statistics.median(times), found

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == ADMIN))
        assert user is not None
        tag = uuid_hex()
        cad = ObjectType(slug=f"lin_cad_{tag}", label="도면", revision_key="rev")
        mesh = ObjectType(slug=f"lin_mesh_{tag}", label="메시")
        result = ObjectType(slug=f"lin_res_{tag}", label="결과")
        made = RelationType(slug=f"lin_made_{tag}", label="만든 것", lineage_source="src")
        used = RelationType(slug=f"lin_used_{tag}", label="사용한 메시", lineage_source="dst")
        plain = RelationType(slug=f"lin_plain_{tag}", label="그냥 관계")
        db.add_all([cad, mesh, result, made, used, plain])
        db.flush()
        db.add(
            PropertyDef(
                owner_kind="type", owner_id=cad.id, key="rev", label="개정", data_type="text"
            )
        )
        db.flush()
        for type_row, count, prefix, props in (
            (cad, 2000, "C", "jsonb_build_object('rev', 'A')"),
            (mesh, 10000, "M", "'{}'::jsonb"),
            (result, 50000, "R", "'{}'::jsonb"),
        ):
            db.execute(
                text(
                    f"""
                    INSERT INTO objects (id, type_id, label, key, properties)
                    SELECT gen_random_uuid(), :type, :prefix || g, :prefix || g, {props}
                      FROM generate_series(1, :n) g
                    """
                ),
                {"type": str(type_row.id), "prefix": prefix, "n": count},
            )
        db.execute(text("ANALYZE objects"))

        def link(rel: str, src_type: Any, dst_type: Any, src_mod: int, dst_mod: int) -> float:
            """출발 타입의 k 번째가 도착 타입의 (k % dst_mod) + 1 번째에 — 걸린 초."""
            started = time.perf_counter()
            db.execute(
                text(
                    """
                    WITH s AS (SELECT id, substr(key, 2)::int AS k FROM objects
                                WHERE type_id = :src),
                         d AS (SELECT id, substr(key, 2)::int AS k FROM objects
                                WHERE type_id = :dst)
                    INSERT INTO object_relations (id, src_object_id, dst_object_id, relation)
                    SELECT gen_random_uuid(), s.id, d.id, :rel
                      FROM s JOIN d ON d.k = (s.k % :dst_mod) + 1
                     WHERE s.k <= :src_mod
                    """
                ),
                {
                    "src": str(src_type.id),
                    "dst": str(dst_type.id),
                    "rel": rel,
                    "src_mod": src_mod,
                    "dst_mod": dst_mod,
                },
            )
            return time.perf_counter() - started

        # 도면 → 메시: 메시마다 도면 하나(메시 j 의 원천 = 도면 (j % 2000)+1).
        started = time.perf_counter()
        db.execute(
            text(
                """
                WITH c AS (SELECT id, substr(key, 2)::int AS k FROM objects
                            WHERE type_id = :cad),
                     m AS (SELECT id, substr(key, 2)::int AS k FROM objects
                            WHERE type_id = :mesh)
                INSERT INTO object_relations (id, src_object_id, dst_object_id, relation)
                SELECT gen_random_uuid(), c.id, m.id, :rel
                  FROM m JOIN c ON c.k = (m.k % 2000) + 1
                """
            ),
            {"cad": str(cad.id), "mesh": str(mesh.id), "rel": made.slug},
        )
        row("넣기 · 도면 → 메시 계보 선 1만(트리거가 고정)", time.perf_counter() - started, "")
        took = link(used.slug, result, mesh, 50000, 10000)
        row("넣기 · 결과 → 메시 계보 선 5만(트리거가 고정)", took, "원천이 도착인 종류")
        unpinned = db.scalar(
            text(
                "SELECT count(*) FROM object_relations WHERE relation IN (:a, :b) "
                "AND pinned_at IS NULL"
            ),
            {"a": made.slug, "b": used.slug},
        )
        row("고정 안 된 계보 선", 0.0, f"{unpinned}줄(0 이어야 한다)")
        db.execute(text("ANALYZE object_relations"))

        # 개정을 바꾼다 — 도면 40(2%), 그리고 메시 100(1%)의 이름만(바뀐 때로 판정).
        db.execute(text("SELECT pg_sleep(0.01)"))
        db.execute(
            text(
                """
                UPDATE objects SET properties = jsonb_set(properties, '{rev}', '"B"'),
                       updated_at = clock_timestamp()
                 WHERE type_id = :cad AND substr(key, 2)::int % 50 = 0
                """
            ),
            {"cad": str(cad.id)},
        )
        db.execute(
            text(
                """
                UPDATE objects SET label = label || '-2', updated_at = clock_timestamp()
                 WHERE type_id = :mesh AND substr(key, 2)::int % 100 = 7
                """
            ),
            {"mesh": str(mesh.id)},
        )

        def one(type_row: Any, key: str) -> ObjectInstance:
            found = db.scalar(
                select(ObjectInstance).where(
                    ObjectInstance.type_id == type_row.id, ObjectInstance.key == key
                )
            )
            assert found is not None
            return found

        changed_cad = one(cad, "C50")
        stale_mesh = one(mesh, "M49")  # (49 % 2000)+1 = 도면 50 — 바뀐 도면
        below = one(result, "R48")  # (48 % 10000)+1 = 메시 49
        took, seen = timed(lambda: lineage.view(db, user, changed_cad, cad))
        row(
            "객체 하나의 계보 · 도면(위 0 · 아래 30)",
            took,
            f"아래 {seen.down_total}줄 · 영향 {seen.impact.derived}(낡음 {seen.impact.stale})",
        )
        took, seen = timed(lambda: lineage.view(db, user, stale_mesh, mesh))
        row(
            "객체 하나의 계보 · 메시(위 1 · 아래 5)",
            took,
            f"{seen.state} · {seen.reasons[:1]}",
        )
        took, seen = timed(lambda: lineage.view(db, user, below, result))
        row("객체 하나의 계보 · 결과(위 2)", took, f"{seen.state}")
        took, counted = timed(lambda: lineage.impact(db, changed_cad.id))
        row("원천 하나의 영향 범위(도면)", took, f"{counted.derived}개(낡음 {counted.stale})")

        def whole(direct_only: bool) -> int:
            lineage.forget(db)  # 세션이 기억한 집합을 버리고 — 되풀이마다 처음부터 센다
            return len(lineage.stale_ids(db, direct_only=direct_only))

        took, n = timed(lambda: whole(False))
        row("전체 낡음 집합(낡은 것만의 재료)", took, f"{n}개(메시 200 + 결과 1,500)")
        took, n = timed(lambda: whole(True))
        row("전체 바로 낡음 집합", took, f"{n}개(메시 200 + 결과 500)")

        def stale_count(type_row: Any, value: str) -> int:
            return int(
                db.scalar(
                    select(func_count())
                    .select_from(ObjectInstance)
                    .where(
                        ObjectInstance.type_id == type_row.id,
                        ObjectInstance.deleted_at.is_(None),
                        lineage.condition(db, ObjectInstance, "eq", value),
                    )
                )
                or 0
            )

        lineage.forget(db)
        took, n = timed(lambda: stale_count(result, "stale"))
        row("목록 「낡은 것만」 세기 · 결과 5만(집합은 기억)", took, f"{n}건(1,000 + 500)")
        took, n = timed(lambda: stale_count(mesh, "stale"))
        row("목록 「낡은 것만」 세기 · 메시 1만(집합은 기억)", took, f"{n}건(200)")
        stale_page = list(
            db.scalars(
                select(ObjectInstance.id)
                .where(
                    ObjectInstance.type_id == result.id,
                    lineage.condition(db, ObjectInstance, "eq", "stale"),
                )
                .limit(50)
            )
        )
        plain_page = list(
            db.scalars(
                select(ObjectInstance.id).where(ObjectInstance.type_id == result.id).limit(50)
            )
        )
        took, n = timed(lambda: len(lineage.page_states(db, stale_page)))
        row("목록 한 쪽(50) 낡음 표시 · 낡은 줄", took, f"표시 {n}줄")
        took, n = timed(lambda: len(lineage.page_states(db, plain_page)))
        row("목록 한 쪽(50) 낡음 표시 · 아무 줄", took, f"낡은 줄 {n}")
        took, got = timed(lambda: lineage_review.collect(db, user, Filters()))
        row(
            "검토함 모으기 · 낡은 파생물",
            took,
            f"항목 {len(got.items)} · {'; '.join(got.notes) or '잘림 없음'}",
        )
        took, done = timed(lambda: lineage.repin(db, user, stale_mesh, basis="checked"))
        row("다시 고정 · 메시 하나", took, f"바뀐 선 {len(done)}(첫 번째 뒤로는 0)")
        # 넣기의 값 견주기 — 읽기를 다 잰 뒤에(트리거를 끄는 DDL · 5만 줄 지우기가 같은
        # 트랜잭션의 읽기 계획을 흔들지 않게).
        took = link(plain.slug, result, mesh, 50000, 10000)
        row("넣기 · 같은 모양의 계보 아닌 선 5만", took, "종류 조회만")
        db.execute(text("ALTER TABLE object_relations DISABLE TRIGGER object_relations_kind"))
        db.execute(
            text("DELETE FROM object_relations WHERE relation = :rel"), {"rel": plain.slug}
        )
        took = link(plain.slug, result, mesh, 50000, 10000)
        row("넣기 · 같은 모양 5만 — 트리거 끔", took, "견줌")
        db.execute(text("ALTER TABLE object_relations ENABLE TRIGGER object_relations_kind"))
        db.execute(
            text("DELETE FROM object_relations WHERE relation = :rel"), {"rel": plain.slug}
        )
    finally:
        db.rollback()
        db.close()
    return out


def _map_rehearsal(
    client: Any, admin: dict[str, str], manager: dict[str, str], repeat: int
) -> list[tuple[str, str, str, str]]:
    """전체 지도(ADR 0046) — 판 만들기 · 시간마다 집계 · 멀리 보기 응답 · 확대 상자 · 쓰기
    비용.

    1. 전체 배치(처음이면 처음 판, 있으면 앞 판에서 출발) 두 번 — 두 번째의 움직인 거리.
       **남긴다.**
    2. 시간마다 집계 · 덧붙임(새 기준정보 없음)과 새 개발모델 60(1%) + 기록 6,000 을 심은
       덧붙임 — 기존 자리 움직임 0 을 확인하고 **롤백**.
    3. `GET /graph/map`(관리자 · 부서 관리자 — 부서별 반지름) 시간 · 크기(gzip).
    4. 확대 상자(측정 보고의 a · b · b' · c) — 처음 · 다시, 점 수, 크기.
    5. 쓰기 — 기록 2만 줄 넣기와 그 뒤 시간마다의 점 표 맞춤(바뀐 기록만, 롤백)."""
    import gzip
    import math

    from app.database import SessionLocal
    from app.modules.graph import mapbuild, mapread

    out: list[tuple[str, str, str, str]] = []

    def row(label: str, took: float, note: str) -> None:
        out.append(("map", label, f"{took:.3f}초", note))
        print(f"{took:8.3f}초  {label}  {note}", flush=True)

    def job(name: str, call: Callable[[Any], Any], commit: bool) -> Any:
        with SessionLocal() as db:
            started = time.perf_counter()
            got = call(db)
            took = time.perf_counter() - started
            if commit:
                db.commit()
            else:
                db.rollback()
        mapread.forget()
        return took, got

    took, got = job("full", lambda db: mapbuild.run_full(db, user_id=None), True)
    row(
        "전체 배치(1)",
        took,
        f"기준정보 {got['masters']:,} · 허브 {got['hubs']:,} · "
        f"공동 참조 짝 {got['coref_pairs']:,} · 선 {got['edges']:,} · "
        f"선 모으기 {got['edge_seconds']}초 · 집계 {got['count_seconds']}초 · "
        f"배치 {got['layout_seconds']}초 · 겹침 바로잡기 {got['overlap_rounds']}번",
    )
    took, got = job("full", lambda db: mapbuild.run_full(db, user_id=None), True)
    row("전체 다시 배치(2, 앞 판에서)", took, f"움직임(지도 폭 대비) {got['moved']}")
    took, got = job("count", lambda db: mapbuild.run_count(db), True)
    row("시간마다 집계", took, f"줄 {got.get('rows', 0):,}")
    took, got = job("append", lambda db: mapbuild.run_append(db), False)
    row("매일 덧붙임 — 새 기준정보 없음", took, f"{got}")

    def seeded_append(db: Any) -> Any:
        """새 개발모델 60 + 기록 6,000(모델마다 100)을 심고 덧붙임 — 롤백한다."""
        before = {
            one: (x, y)
            for one, x, y in db.execute(text("SELECT object_id, x, y FROM graph_map_masters"))
        }
        db.execute(
            text("""
            CREATE TEMP TABLE new_models ON COMMIT DROP AS
            SELECT gen_random_uuid() AS id, o.type_id, o.key || '-new' AS key, o.label,
                   o.properties, o.owner_workspace_id
              FROM objects o JOIN object_types t ON t.id = o.type_id
             WHERE t.slug = 'plm_model' ORDER BY o.key LIMIT 60
            """)
        )
        db.execute(
            text("""
            INSERT INTO objects (id, type_id, key, label, properties, owner_workspace_id)
            SELECT id, type_id, key, label, properties, owner_workspace_id FROM new_models
            """)
        )
        db.execute(
            text("""
            INSERT INTO objects (id, type_id, key, label, properties, owner_workspace_id)
            SELECT gen_random_uuid(), c.type_id, c.key || '-n' || g.n, c.label,
                   jsonb_set(c.properties, '{model}', to_jsonb(m.id::text)),
                   c.owner_workspace_id
              FROM (SELECT id, row_number() OVER () AS k FROM new_models) m
              CROSS JOIN generate_series(1, 100) AS g(n)
              JOIN LATERAL (
                SELECT o.* FROM objects o JOIN object_types t ON t.id = o.type_id
                 WHERE t.slug = 'svc_case' LIMIT 1 OFFSET (m.k * 100 + g.n)
              ) c ON true
            """)
        )
        started = time.perf_counter()
        got = mapbuild.run_append(db)
        took = time.perf_counter() - started
        after = {
            one: (x, y)
            for one, x, y in db.execute(text("SELECT object_id, x, y FROM graph_map_masters"))
        }
        moved = sum(1 for one, at in before.items() if after.get(one) != at)
        return took, got, moved

    _took_all, (took, got, moved) = job("append+", seeded_append, False)
    row(
        "매일 덧붙임 — 새 개발모델 60 · 기록 6,000",
        took,
        f"새 기준정보 {got.get('new')} · 새 허브 {got.get('new_hubs')} · "
        f"기존 자리 움직임 {moved}개"
        " (롤백)",
    )

    def far(who: dict[str, str], label: str) -> Any:
        times: list[float] = []
        response: Any = None
        for _ in range(max(repeat, 2)):
            started = time.perf_counter()
            response = client.get("/api/graph/map", headers={**who, "Accept-Encoding": "gzip"})
            times.append(time.perf_counter() - started)
        body = response.json()
        raw = len(response.content)
        packed = len(gzip.compress(response.content, 5))
        row(
            label,
            statistics.median(times),
            f"기준정보 {len(body['masters']['ids']):,} · 원반 {len(body['discs']['x']):,} · "
            f"JSON {raw / 1e6:.2f}MB · gzip {packed / 1e6:.2f}MB",
        )
        return body

    body = far(admin, "멀리 보기 GET /graph/map(관리자)")
    far(manager, "멀리 보기 GET /graph/map(부서 관리자 — 보는 사람 기준 반지름)")

    discs = body["discs"]
    radius = discs["r"]
    hot = max(range(len(radius)), key=lambda at: radius[at])
    hx, hy, hr = discs["x"][hot], discs["y"][hot], radius[hot]
    order = sorted(range(len(radius)), key=lambda at: radius[at])
    typical = order[len(order) // 3]
    tx, ty = discs["x"][typical], discs["y"][typical]
    # (a) 보통 허브를 가운데로, 닿는 원반이 30개 남짓이 될 때까지 넓힌다.
    half = radius[typical]
    for _ in range(200):
        touching = sum(
            1
            for at in range(len(radius))
            if math.hypot(discs["x"][at] - tx, discs["y"][at] - ty) <= radius[at] + half * 1.42
        )
        if touching >= 30:
            break
        half *= 1.15
    # (a') 같은 자리에서 기대 수가 상한 아래로 들 때까지 줄인다 — 허브 여럿을 실제로 읽는 상자.
    # (고르는 동안 마지막 한 번은 점을 읽으므로 아래 「처음」 은 찬 캐시가 아니다.)
    small = half
    for _ in range(60):
        got = client.get(
            "/api/graph/map/points",
            params={"x0": tx - small, "y0": ty - small, "x1": tx + small, "y1": ty + small},
            headers=admin,
        ).json()
        if got["expected"] <= 45_000:
            break
        small *= 0.85
    boxes = {
        "a 허브 30여 개": (tx - half, ty - half, tx + half, ty + half),
        "a' 허브 여럿(기대 4.5만 이하)": (tx - small, ty - small, tx + small, ty + small),
        "b 뜨거운 원반 안(변 0.1R)": (
            hx + 0.45 * hr,
            hy - 0.05 * hr,
            hx + 0.55 * hr,
            hy + 0.05 * hr,
        ),
        "b' 뜨거운 원반 안(변 0.3R)": (
            hx + 0.35 * hr,
            hy - 0.15 * hr,
            hx + 0.65 * hr,
            hy + 0.15 * hr,
        ),
        "c 뜨거운 원반 전체": (hx - hr, hy - hr, hx + hr, hy + hr),
    }
    for label, (x0, y0, x1, y1) in boxes.items():
        for attempt in ("처음", "다시"):
            started = time.perf_counter()
            response = client.get(
                "/api/graph/map/points",
                params={"x0": x0, "y0": y0, "x1": x1, "y1": y1},
                headers={**admin, "Accept-Encoding": "gzip"},
            )
            took = time.perf_counter() - started
            got = response.json()
            row(
                f"확대 {label} · {attempt}",
                took,
                f"기대 {got['expected']:,} · 점 {got['count']:,}"
                f"{' · 상한 넘음(안 읽음)' if got['over'] else ''} · JSON "
                f"{len(response.content) / 1e6:.2f}MB · gzip "
                f"{len(gzip.compress(response.content, 5)) / 1e6:.2f}MB",
            )
        started = time.perf_counter()
        response = client.get(
            "/api/graph/map/points",
            params={"x0": x0, "y0": y0, "x1": x1, "y1": y1},
            headers=manager,
        )
        took = time.perf_counter() - started
        got = response.json()
        row(
            f"확대 {label} · 부서 관리자",
            took,
            f"기대 {got['expected']:,} · 점 {got['count']:,}",
        )

    def ingest_then_refresh() -> tuple[float, float, dict[str, Any]]:
        """기록 2만 줄을 넣고(쓰기 길 — 지도는 아무것도 안 붙인다) 시간마다의 점 표 맞춤을
        잰다(롤백)."""
        with SessionLocal() as db:
            db.execute(
                text("""
                CREATE TEMP TABLE src_rows ON COMMIT DROP AS
                SELECT o.type_id, o.key, o.label, o.properties, o.owner_workspace_id
                  FROM objects o JOIN object_types t ON t.id = o.type_id
                 WHERE t.slug = 'svc_case' LIMIT 20000
                """)
            )
            started = time.perf_counter()
            db.execute(
                text("""
                INSERT INTO objects (id, type_id, key, label, properties, owner_workspace_id)
                SELECT gen_random_uuid(), type_id, key || 'z', label, properties,
                       owner_workspace_id
                  FROM src_rows
                """)
            )
            took_insert = time.perf_counter() - started
            layout = mapbuild.current(db)
            assert layout is not None
            started = time.perf_counter()
            got = mapbuild.refresh_points(db, layout.hub_keys)
            took_refresh = time.perf_counter() - started
            db.rollback()
        return took_insert, took_refresh, got

    for rnd in range(2):
        took_insert, took_refresh, got = ingest_then_refresh()
        row(
            f"쓰기 — 기록 2만 줄 넣기({rnd + 1}회)",
            took_insert,
            "지도는 쓰기 길에 아무것도 안 붙인다 · 다음 집계의 점 표 맞춤 "
            f"{took_refresh:.3f}초 "
            f"(바뀐 기록 {got['points_changed']:,} · 넣은 점 {got['points_rows']:,})",
        )
    with SessionLocal() as db:
        size = db.execute(
            text(
                "SELECT pg_size_pretty(pg_total_relation_size('graph_map_points')), "
                "pg_size_pretty(pg_relation_size('ix_graph_map_points_hub')), "
                "pg_size_pretty(pg_total_relation_size('graph_map_masters') + "
                "pg_total_relation_size('graph_map_frozen') + "
                "pg_total_relation_size('graph_map_counts') + "
                "pg_total_relation_size('graph_map_links'))"
            )
        ).one()
    row(
        "저장 크기",
        0.0,
        f"확대 점 표(색인 포함) {size[0]} · 그중 (허브, 해시) 색인 {size[1]} · "
        f"지도 표 넷 {size[2]}",
    )
    return out


def _proposals_rehearsal(repeat: int) -> list[tuple[str, str, str, str]]:
    """제안된 관계(ADR 0042) — 제안 10만 건이 규모에서 얼마나 드나.

    한 세션 안에서 심고 재고 **롤백한다**(아무것도 안 남는다): 부품 2만 · 고장 모드 2,000(둘
    다 부서 관리자의 부서 것)과 그 사이 관계 종류 하나, 부품마다 고장 모드 다섯으로 가는 열린
    제안 10만(고장 모드 하나에 제안 쉰, 확신도는 0 ~ 1 에 고르게, 출처 글 스무 가지). 그다음
    객체 하나의 제안 목록 · 전체 목록 첫 쪽 · 검토함 모으기 · 홈 「남은 일」 · 검토함 미리
    보기(확정 100건) · 확정 100건(「관계 추가」 와 같은 길) · 여러 건 제안 500줄을 잰다."""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.modules.accounts.models import User
    from app.modules.objects import proposal_review, proposals
    from app.modules.objects.models import ObjectInstance, RelationProposal
    from app.modules.ontology.models import ObjectType, RelationType
    from app.modules.review import services as review_services
    from app.modules.review.registry import Filters
    from app.modules.workspaces.models import WorkspaceMember

    out: list[tuple[str, str, str, str]] = []

    def row(label: str, took: float, note: str) -> None:
        out.append(("proposals", label, f"{took:.3f}초", note))
        print(f"{took:8.3f}초  {label}  {note}", flush=True)

    def timed(call: Callable[[], Any]) -> tuple[float, Any]:
        times: list[float] = []
        found: Any = None
        for _ in range(max(repeat, 3)):
            started = time.perf_counter()
            found = call()
            times.append(time.perf_counter() - started)
        return statistics.median(times), found

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == ADMIN))
        boss = db.scalar(select(User).where(User.email == MANAGER))
        assert user is not None and boss is not None
        home = db.scalar(
            select(WorkspaceMember.workspace_id).where(
                WorkspaceMember.user_id == boss.id, WorkspaceMember.role == "manager"
            )
        )
        tag = uuid_hex()
        part = ObjectType(slug=f"prp_part_{tag}", label="부품")
        mode = ObjectType(slug=f"prp_mode_{tag}", label="고장 모드")
        db.add_all([part, mode])
        db.flush()
        kind = RelationType(
            slug=f"prp_fails_{tag}",
            label="고장 모드",
            inverse_label="나는 부품",
            src_type_slugs=[part.slug],
            dst_type_slugs=[mode.slug],
        )
        db.add(kind)
        db.flush()
        for type_row, count, prefix in ((part, 20000, "P"), (mode, 2000, "F")):
            db.execute(
                text(
                    """
                    INSERT INTO objects
                        (id, type_id, label, key, properties, owner_workspace_id)
                    SELECT gen_random_uuid(), :type, :prefix || g, :prefix || g,
                           '{}'::jsonb, :ws
                      FROM generate_series(1, :n) g
                    """
                ),
                {"type": str(type_row.id), "prefix": prefix, "n": count, "ws": str(home)},
            )
        db.execute(text("ANALYZE objects"))
        started = time.perf_counter()
        db.execute(
            text(
                """
                WITH p AS (SELECT id, substr(key, 2)::int AS k FROM objects
                            WHERE type_id = :p),
                     f AS (SELECT id, substr(key, 2)::int AS k FROM objects
                            WHERE type_id = :f),
                     j AS (SELECT generate_series(0, 4) AS j)
                INSERT INTO relation_proposals
                    (id, relation, src_object_id, dst_object_id, evidence_note, confidence,
                     origin, origin_ref, status, proposed_by_id, created_at)
                SELECT gen_random_uuid(), :rel, p.id, f.id,
                       'FMEA 시트 ' || p.k || '행 — 원인 칸이 이 고장 모드를 말함',
                       ((p.k * 37 + j.j * 11) % 101) / 100.0,
                       'mcp', 'FMEA-' || (p.k % 20), 'proposed', :user,
                       now() - make_interval(secs => p.k + j.j)
                  FROM p CROSS JOIN j
                  JOIN f ON f.k = ((p.k * 5 + j.j) % 2000) + 1
                """
            ),
            {"p": str(part.id), "f": str(mode.id), "rel": kind.slug, "user": str(user.id)},
        )
        planted = db.scalar(
            select(func_count())
            .select_from(RelationProposal)
            .where(RelationProposal.relation == kind.slug)
        )
        row(
            "넣기 · 열린 제안 10만(SQL 한 문장)",
            time.perf_counter() - started,
            f"{planted:,}건",
        )
        db.execute(text("ANALYZE relation_proposals"))

        def pick(type_row: Any, key: str) -> ObjectInstance:
            found = db.scalar(
                select(ObjectInstance).where(
                    ObjectInstance.type_id == type_row.id, ObjectInstance.key == key
                )
            )
            assert found is not None
            return found

        p1, f1 = pick(part, "P77"), pick(mode, "F386")
        took, seen = timed(lambda: proposals.of_object(db, user, p1))
        row("객체 하나의 제안 목록 · 부품(출발 다섯)", took, f"{len(seen)}줄")
        took, seen = timed(lambda: proposals.of_object(db, user, f1))
        row("객체 하나의 제안 목록 · 고장 모드(도착 쉰)", took, f"{len(seen)}줄")
        took, (page, total) = timed(
            lambda: proposals.page(db, user, proposals.PageQuery(), limit=50, offset=0)
        )
        row(
            "전체 목록 첫 쪽(50) · 열린 제안(전체 수 세기 포함)",
            took,
            f"{len(page)}줄 / {total:,}",
        )
        took, (page, total) = timed(
            lambda: proposals.page(
                db, boss, proposals.PageQuery(decidable=True), limit=50, offset=0
            )
        )
        row("전체 목록 첫 쪽 · 부서 관리자 · 확정할 수 있는 것", took, f"{total:,}건")
        took, got = timed(lambda: proposal_review.collect(db, user, Filters()))
        row(
            "검토함 모으기 · 제안된 관계(시스템 관리자)",
            took,
            f"항목 {len(got.items):,} / {got.total:,} · 추천 "
            f"{sum(1 for one in got.items if one.recommended):,}",
        )
        took, got = timed(
            lambda: proposal_review.collect(db, boss, Filters(type_slug=mode.slug))
        )
        row("검토함 모으기 · 부서 관리자 · 타입 거르기", took, f"항목 {len(got.items):,}")
        took, items = timed(lambda: proposals.maintenance(db, boss))
        row("홈 「남은 일」 · 부서 관리자", took, f"{items[0].count:,}건" if items else "0")

        # 확신도 높은 것부터 100건 — 미리 보기(검토함의 계획)와 확정.
        top = list(
            db.scalars(
                select(RelationProposal)
                .where(
                    RelationProposal.relation == kind.slug,
                    RelationProposal.status == "proposed",
                )
                .order_by(RelationProposal.confidence.desc(), RelationProposal.id)
                .limit(300)
            )
        )
        decisions = [
            review_services.Decision(
                kind=proposal_review.KIND, item_id=str(one.id), action="accept"
            )
            for one in top[:100]
        ]
        took, planned = timed(lambda: review_services.plan(db, boss, decisions))
        row(
            "검토함 미리 보기 · 확정 100건(부서 관리자)",
            took,
            f"적용 {sum(1 for one in planned if one.status == 'apply')}",
        )
        times: list[float] = []
        for start in (0, 100, 200):
            batch = top[start : start + 100]
            started = time.perf_counter()
            for one in batch:
                proposals.accept(db, boss, one, note="검토함 — 확정")
            db.flush()
            times.append(time.perf_counter() - started)
        made = db.scalar(
            select(func_count())
            .select_from(RelationProposal)
            .where(
                RelationProposal.relation == kind.slug, RelationProposal.status == "accepted"
            )
        )
        row(
            "확정 100건(「관계 추가」 와 같은 길 · 감사 포함)",
            statistics.median(times),
            f"확정 {made}건(100건씩 3번)",
        )
        specs = [
            proposals.Spec(
                relation=kind.slug,
                src_id=pick(part, f"P{k}").id,
                dst_id=pick(mode, f"F{(k * 13) % 2000 + 1}").id,
                confidence=0.7,
                evidence_note="여러 건 제안 리허설",
                origin="mcp",
                origin_ref="rehearsal",
            )
            for k in range(1, 501)
        ]
        started = time.perf_counter()
        results = proposals.propose_many(db, user, specs, again=False)
        took = time.perf_counter() - started
        counts: dict[str, int] = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        row("여러 건 제안 500줄(줄마다 검사 · savepoint)", took, str(counts))
    finally:
        db.rollback()
        db.close()
    return out


def uuid_hex() -> str:
    import uuid as uuid_module

    return uuid_module.uuid4().hex[:6]


def func_count() -> Any:
    from sqlalchemy import func

    return func.count()


def func_count_desc() -> Any:
    from sqlalchemy import func

    return func.count().desc()


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
    parser.add_argument(
        "--only",
        help=(
            "이 영역만 잰다(list · profile · graph · summary · metrics · recipes · visits · "
            "alerts · incremental · rules · periods · explode · lineage · proposals · "
            "edgefields · map …). map 은 이름으로만(판을 만들어 남긴다)"
        ),
    )
    args = parser.parse_args()
    if args.action == "create":
        create(args.rows)
    elif args.action == "measure":
        measure(args.timeout, args.repeat, args.only)
    else:
        drop()


if __name__ == "__main__":
    main()
