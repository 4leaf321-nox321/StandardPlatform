"""데이터 소스 동기화를 돌린다 — **타이머가 부르는 자리.**

    python scripts/sync_datasources.py --due            차례가 된 것(간격이 지난 것)만
    python scripts/sync_datasources.py --all            켜진 것 전부
    python scripts/sync_datasources.py --slug plm_sup   하나만
    python scripts/sync_datasources.py --due --plan     계획만(적용 안 함)

이 틀에는 워커가 없다. systemd 타이머(`<slug>-sync.timer`, deploy.sh 가 설치)가 몇 분마다
이 스크립트를 컨테이너 안에서 돌린다 — 앱과 같은 SIF·같은 .env 라 다른 코드가 다른 스키마를
보는 일이 없다. 언제 돌았고 무엇을 넣었는지는 journal 과 동기화 기록(화면)에 남는다.

감사 기록의 actor 는 「타이머」 다. 권한 판정은 시스템 관리자 계정 하나를 빌린다
(`services._actor`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

import app.all_models
import app.main  # noqa: F401  (레지스트리 조립 — 웹훅·원 표)
from app.database import SessionLocal
from app.modules.datasources import services
from app.modules.datasources.models import DataSource
from app.shared.errors import AppError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--due", action="store_true", help="간격이 지난 것만")
    group.add_argument("--all", action="store_true", help="켜진 것 전부")
    group.add_argument("--slug", help="하나만")
    parser.add_argument("--plan", action="store_true", help="계획만 (적용 안 함)")
    args = parser.parse_args()

    failed = 0
    with SessionLocal() as db:
        if args.slug:
            found = db.scalar(select(DataSource).where(DataSource.slug == args.slug))
            if found is None:
                print(f"데이터 소스를 찾을 수 없습니다: {args.slug}", file=sys.stderr)
                return 1
            sources = [found]
        elif args.all:
            sources = list(
                db.scalars(select(DataSource).where(DataSource.is_active.is_(True)))
            )
        else:
            sources = services.due(db)
        if not sources:
            print("돌릴 것이 없습니다.")
            return 0
        for source in sources:
            try:
                result = services.sync(db, None, source, apply=not args.plan)
            except AppError as caught:
                failed += 1
                print(f"{source.slug}: 오류 — {caught.message}", file=sys.stderr)
                continue
            run = result.run
            counts = " ".join(f"{k}={v}" for k, v in (run.counts or {}).items())
            print(f"{source.slug}: {run.status} 행 {run.rows_seen} {counts}")
            for message in (run.errors or [])[:10]:
                print(f"    {message}")
            if run.status == "failed":
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
