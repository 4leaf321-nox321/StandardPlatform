#!/usr/bin/env python3
"""Standard Platform 코어 수신 클라이언트 — 설정만 고치면 도는 한 파일.

    pip install requests
    python sp_core_pull.py --config config.ini

무엇을 하나
    1. 카탈로그(GET /core)로 공개 타입과 칸을 읽는다.
    2. 타입마다 GET /core/<타입>?since=... 를 next 가 빌 때까지 이어 받는다.
    3. 그 타입에 열린 관계가 있으면 GET /core/<타입>/relations 도 같은 식으로 받는다.
    4. 받은 행을 CSV 와 SQLite 에 저장한다(save_rows 를 고치면 자기 DB 로 간다).
    5. 마지막에 온 as_of 를 state.json 에 적어 두고, 다음 실행에서 그대로 돌려준다.

지켜야 할 것 여섯 — 이 파일은 이미 지키고 있다
    · as_of 는 서버가 준 값을 그대로 돌려준다(자기 시계로 만들지 않는다)
    · next 가 있으면 as_of 를 저장하지 않는다(끝까지 받은 뒤에만)
    · key 를 저장한다(다음 수신이 수정이 되는 근거)
    · deleted 행은 비활성 처리한다(삭제하지 않는다)
    · 모르는 칸은 무시한다(공개 측이 칸을 추가해도 깨지지 않는다)
    · reset 이 오면 그 타입을 비우고 처음부터 받는다(아래)

reset 은 무엇인가
    오래 안 받아 갔으면 서버가 「그 시점부터는 **끊긴 것을 알려 줄 수 없다**」 고 말한다
    (끊긴 선의 기록은 한동안만 들고 있다). 그때 빈 쪽을 「바뀐 것 없음」 으로 읽으면 이미
    끊긴 선을 영영 들고 있게 된다 — 그래서 저장한 시각을 버리고, 그 타입의 현재 상태를
    비우고, 처음부터 다시 받는다. 비우지 않으면 그 사이에 사라진 것이 그대로 남는다.
"""
from __future__ import annotations

import argparse
import configparser
import csv
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import requests

ENVELOPE = ("key", "label", "status", "updated_at", "deleted", "merged_into")


def load_config(path: str) -> dict[str, Any]:
    parser = configparser.ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        sys.exit(f"설정 파일을 읽지 못했습니다: {path}")
    section = parser["sp"]
    token = os.environ.get("SP_TOKEN") or section.get("token", "")
    if not token:
        sys.exit("토큰이 없습니다. config.ini 의 token 또는 환경변수 SP_TOKEN 을 지정하세요.")
    types = [one.strip() for one in section.get("types", "").split(",") if one.strip()]
    return {
        "base_url": section.get("base_url", "").rstrip("/"),
        "token": token,
        "types": types,
        "out_dir": Path(section.get("out_dir", "out")),
        "page_size": section.getint("page_size", 500),
        "timeout": section.getint("timeout_seconds", 60),
        "retries": section.getint("retries", 3),
    }


def request_json(cfg: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
    """GET 한 번 — 일시적 오류는 다시 시도하고, 권한 오류는 즉시 중단한다."""
    url = f"{cfg['base_url']}{path}"
    headers = {"Authorization": f"Bearer {cfg['token']}", "Accept": "application/json"}
    for attempt in range(1, cfg["retries"] + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=cfg["timeout"])
        except requests.RequestException as caught:
            if attempt == cfg["retries"]:
                sys.exit(f"연결하지 못했습니다: {caught}")
            time.sleep(2 * attempt)
            continue
        if response.status_code in (401, 403):
            sys.exit(f"인증이 거절되었습니다(HTTP {response.status_code}). 토큰과 범위를 확인하세요: {said(response)}")
        if response.status_code == 404:
            sys.exit(f"공개된 타입이 아닙니다: {path} — {said(response)}")
        if response.status_code >= 500 or response.status_code == 429:
            if attempt == cfg["retries"]:
                sys.exit(f"서버 오류가 계속됩니다(HTTP {response.status_code}): {said(response)}")
            time.sleep(2 * attempt)
            continue
        if response.status_code >= 400:
            sys.exit(f"요청이 거절되었습니다(HTTP {response.status_code}): {said(response)}")
        return response.json()
    return None


def said(response: requests.Response) -> str:
    try:
        return str((response.json().get("error") or {}).get("message") or "")[:300]
    except Exception:
        return response.text[:300]


def state_path(cfg: dict[str, Any]) -> Path:
    return cfg["out_dir"] / "state.json"


def load_state(cfg: dict[str, Any]) -> dict[str, str]:
    path = state_path(cfg)
    if path.exists():
        return dict(json.loads(path.read_text(encoding="utf-8")))
    return {}


def save_state(cfg: dict[str, Any], state: dict[str, str]) -> None:
    state_path(cfg).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    """봉투 + properties 를 한 줄로. 봉투 이름이 우선한다.

    다중 값은 `;` 로 잇는다 — 파이썬 표기(`['A', 'B']`)로 두면 엑셀에서 읽을 수 없다.
    """
    out: dict[str, Any] = {}
    for key, value in (row.get("properties") or {}).items():
        out[key] = ";".join(str(one) for one in value) if isinstance(value, list) else value
    for name in ENVELOPE:
        if name in row:
            value = row[name]
            out[name] = ";".join(str(one) for one in value) if isinstance(value, list) else value
    return out


def save_rows(cfg: dict[str, Any], type_slug: str, rows: list[dict[str, Any]]) -> None:
    """저장 — **여기만 고치면 자기 DB 로 간다.**

    기본 동작
        CSV      이번에 받은 행을 이어 적는다 — **수신 이력**이다(같은 key 가 여러 번 나온다).
        SQLite   `key` 기준 upsert — **현재 상태**다. 삭제 행은 deleted=1 로 남는다.
    
    """
    if not rows:
        return
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    flat = [flatten(one) for one in rows]
    columns: list[str] = []
    for one in flat:
        for name in one:
            if name not in columns:
                columns.append(name)

    csv_path = cfg["out_dir"] / f"{type_slug}.csv"
    exists = csv_path.exists()
    with csv_path.open("a" if exists else "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(flat)

    db = sqlite3.connect(cfg["out_dir"] / "sp_core.sqlite")
    try:
        db.execute(
            f'CREATE TABLE IF NOT EXISTS "{type_slug}" '
            '(key TEXT PRIMARY KEY, label TEXT, status TEXT, updated_at TEXT, '
            "deleted INTEGER, merged_into TEXT, properties TEXT)"
        )
        db.executemany(
            f'INSERT INTO "{type_slug}" (key,label,status,updated_at,deleted,merged_into,properties) '
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "label=excluded.label, status=excluded.status, updated_at=excluded.updated_at, "
            "deleted=excluded.deleted, merged_into=excluded.merged_into, properties=excluded.properties",
            [
                (
                    one["key"],
                    one.get("label"),
                    one.get("status"),
                    one.get("updated_at"),
                    1 if one.get("deleted") else 0,
                    one.get("merged_into"),
                    json.dumps(one.get("properties") or {}, ensure_ascii=False),
                )
                for one in rows
            ],
        )
        db.commit()
    finally:
        db.close()


def save_relations(cfg: dict[str, Any], type_slug: str, rows: list[dict[str, Any]]) -> None:
    """선 저장 — CSV 는 수신 이력, SQLite 는 현재 상태(세 끝이 키다).

    `deleted` 인 줄은 **지운다** — 선은 상태가 아니라 있음/없음이다.
    """
    if not rows:
        return
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    columns = ["src", "relation", "dst", "dst_type", "evidence_note", "updated_at", "deleted"]
    flat = [
        {
            **{name: one.get(name) for name in columns},
            **{
                key: ";".join(str(item) for item in value)
                if isinstance(value, list)
                else value
                for key, value in (one.get("properties") or {}).items()
            },
        }
        for one in rows
    ]
    names: list[str] = list(columns)
    for one in flat:
        for name in one:
            if name not in names:
                names.append(name)
    csv_path = cfg["out_dir"] / f"{type_slug}-relations.csv"
    exists = csv_path.exists()
    with csv_path.open("a" if exists else "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=names, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(flat)

    table = f"{type_slug}__relations"
    db = sqlite3.connect(cfg["out_dir"] / "sp_core.sqlite")
    try:
        db.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" '
            "(src TEXT, relation TEXT, dst TEXT, dst_type TEXT, evidence_note TEXT, "
            "updated_at TEXT, properties TEXT, PRIMARY KEY (src, relation, dst))"
        )
        for one in rows:
            keys = (one.get("src"), one.get("relation"), one.get("dst"))
            if one.get("deleted"):
                db.execute(
                    f'DELETE FROM "{table}" WHERE src=? AND relation=? AND dst=?', keys
                )
                continue
            db.execute(
                f'INSERT INTO "{table}" '
                "(src,relation,dst,dst_type,evidence_note,updated_at,properties) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(src,relation,dst) DO UPDATE SET "
                "dst_type=excluded.dst_type, evidence_note=excluded.evidence_note, "
                "updated_at=excluded.updated_at, properties=excluded.properties",
                (
                    *keys,
                    one.get("dst_type"),
                    one.get("evidence_note"),
                    one.get("updated_at"),
                    json.dumps(one.get("properties") or {}, ensure_ascii=False),
                ),
            )
        db.commit()
    finally:
        db.close()


def forget(cfg: dict[str, Any], table: str) -> None:
    """그 표의 **현재 상태를 비운다** — 처음부터 다시 받기 전에.

    CSV(수신 이력)는 그대로 둔다. 「무엇이 언제 왔나」 는 지우면 안 되는 기록이고, 현재
    상태는 SQLite 쪽이다 — 비우지 않으면 그 사이에 사라진 행 · 선이 영영 남는다.
    """
    path = cfg["out_dir"] / "sp_core.sqlite"
    if not path.exists():
        return
    db = sqlite3.connect(path)
    try:
        db.execute(f'DELETE FROM "{table}"')
        db.commit()
    except sqlite3.OperationalError:
        # 아직 만들지도 않은 표다 — 비울 것이 없다.
        pass
    finally:
        db.close()


def pull_relations(
    cfg: dict[str, Any], type_slug: str, state: dict[str, str]
) -> dict[str, int]:
    """그 타입에서 출발하는 선 — 객체와 **같은 규칙**(since · next · as_of · reset)."""
    mark = f"{type_slug}#relations"
    since = state.get(mark, "")
    cursor: str | None = None
    counts = {"rows": 0, "deleted": 0, "pages": 0, "reset": 0}
    while True:
        params: dict[str, Any] = {"limit": cfg["page_size"]}
        if since:
            params["since"] = since
        if cursor:
            params["cursor"] = cursor
        body = request_json(cfg, f"/core/{type_slug}/relations", params)
        if body.get("reset"):
            # **처음부터 다시 받으라는 말이다.** 빈 쪽을 「바뀐 것 없음」 으로 읽으면 이미
            # 끊긴 선을 영영 들고 있는다.
            if counts["reset"]:
                sys.exit(
                    f"{type_slug} 관계: since 를 비웠는데도 reset 이 옵니다 — "
                    f"공개 측에 문의하세요: {body.get('reset_reason') or ''}"
                )
            print(f"    처음부터 다시 받습니다 — {body.get('reset_reason') or 'reset'}")
            forget(cfg, f"{type_slug}__relations")
            state.pop(mark, None)
            since, cursor = "", None
            counts["reset"] = 1
            continue
        items = body.get("items") or []
        save_relations(cfg, type_slug, items)
        counts["rows"] += len(items)
        counts["deleted"] += sum(1 for one in items if one.get("deleted"))
        counts["pages"] += 1
        cursor = body.get("next")
        if cursor:
            continue
        if body.get("as_of"):
            state[mark] = str(body["as_of"])
        return counts


def pull_type(cfg: dict[str, Any], type_slug: str, state: dict[str, str]) -> dict[str, int]:
    since = state.get(type_slug, "")
    cursor: str | None = None
    counts = {"rows": 0, "deleted": 0, "pages": 0, "reset": 0}
    while True:
        params: dict[str, Any] = {"limit": cfg["page_size"]}
        if since:
            params["since"] = since
        if cursor:
            params["cursor"] = cursor
        body = request_json(cfg, f"/core/{type_slug}", params)
        if body.get("reset"):
            # 오늘은 선 쪽만 이 말을 하지만, **말이 오면 따른다** — 나중에 객체 쪽이
            # 같은 말을 하기 시작할 때 받는 쪽을 고치러 다니지 않는다.
            if counts["reset"]:
                sys.exit(
                    f"{type_slug}: since 를 비웠는데도 reset 이 옵니다 — "
                    f"공개 측에 문의하세요: {body.get('reset_reason') or ''}"
                )
            print(f"    처음부터 다시 받습니다 — {body.get('reset_reason') or 'reset'}")
            forget(cfg, type_slug)
            state.pop(type_slug, None)
            since, cursor = "", None
            counts["reset"] = 1
            continue
        items = body.get("items") or []
        save_rows(cfg, type_slug, items)
        counts["rows"] += len(items)
        counts["deleted"] += sum(1 for one in items if one.get("deleted"))
        counts["pages"] += 1
        cursor = body.get("next")
        if cursor:
            continue
        # **끝까지 받았을 때만 시각을 옮긴다.**
        if body.get("as_of"):
            state[type_slug] = str(body["as_of"])
        return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Standard Platform 코어 수신")
    parser.add_argument("--config", default="config.ini")
    parser.add_argument("--full", action="store_true", help="처음부터 다시 받는다")
    args = parser.parse_args()

    cfg = load_config(args.config)
    cfg["out_dir"].mkdir(parents=True, exist_ok=True)
    catalog = request_json(cfg, "/core")
    opened = [one["slug"] for one in catalog.get("types") or []]
    print(f"시스템 {catalog.get('system')} · 정의 판 {catalog.get('revision')} · 공개 타입 {opened}")

    wanted = cfg["types"] or opened
    unknown = [one for one in wanted if one not in opened]
    if unknown:
        sys.exit(f"공개된 타입이 아닙니다: {unknown}. 공개 측 관리자에게 확인하세요.")

    has_relations = {
        one["slug"]: bool(one.get("relations"))
        for one in catalog.get("types") or []
    }
    state = {} if args.full else load_state(cfg)
    for type_slug in wanted:
        counts = pull_type(cfg, type_slug, state)
        print(
            f"  {type_slug}: {counts['rows']}건 수신"
            f"(삭제 {counts['deleted']}) · {counts['pages']}페이지"
            + (" · 처음부터 다시 받음" if counts["reset"] else "")
        )
        if has_relations.get(type_slug):
            edges = pull_relations(cfg, type_slug, state)
            print(
                f"  {type_slug} 관계: {edges['rows']}건 수신"
                f"(끊김 {edges['deleted']}) · {edges['pages']}페이지"
                + (" · 처음부터 다시 받음" if edges["reset"] else "")
            )
        save_state(cfg, state)
    print(f"저장 위치: {cfg['out_dir'].resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
