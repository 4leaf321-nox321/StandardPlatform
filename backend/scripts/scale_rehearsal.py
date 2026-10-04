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
            "alerts …)"
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
