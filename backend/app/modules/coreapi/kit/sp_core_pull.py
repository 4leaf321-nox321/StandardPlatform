#!/usr/bin/env python3
"""Standard Platform 코어 수신 클라이언트 — 설정만 고치면 도는 한 파일.

    pip install requests
    python sp_core_pull.py --config config.ini

무엇을 하나
    1. 카탈로그(GET /core)로 공개 타입과 칸을 읽는다.
    2. 타입마다 GET /core/<타입>?since=... 를 next 가 빌 때까지 이어 받는다.
    3. 받은 행을 CSV 와 SQLite 에 저장한다(save_rows 를 고치면 자기 DB 로 간다).
    4. 마지막에 온 as_of 를 state.json 에 적어 두고, 다음 실행에서 그대로 돌려준다.

지켜야 할 것 다섯 — 이 파일은 이미 지키고 있다
    · as_of 는 서버가 준 값을 그대로 돌려준다(자기 시계로 만들지 않는다)
    · next 가 있으면 as_of 를 저장하지 않는다(끝까지 받은 뒤에만)
    · key 를 저장한다(다음 수신이 수정이 되는 근거)
    · deleted 행은 비활성 처리한다(삭제하지 않는다)
    · 모르는 칸은 무시한다(공개 측이 칸을 추가해도 깨지지 않는다)
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


def pull_type(cfg: dict[str, Any], type_slug: str, state: dict[str, str]) -> dict[str, int]:
    since = state.get(type_slug, "")
    cursor: str | None = None
    counts = {"rows": 0, "deleted": 0, "pages": 0}
    while True:
        params: dict[str, Any] = {"limit": cfg["page_size"]}
        if since:
            params["since"] = since
        if cursor:
            params["cursor"] = cursor
        body = request_json(cfg, f"/core/{type_slug}", params)
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

    state = {} if args.full else load_state(cfg)
    for type_slug in wanted:
        counts = pull_type(cfg, type_slug, state)
        print(
            f"  {type_slug}: {counts['rows']}건 수신"
            f"(삭제 {counts['deleted']}) · {counts['pages']}페이지"
        )
        save_state(cfg, state)
    print(f"저장 위치: {cfg['out_dir'].resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
