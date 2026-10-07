"""데이터 소스 동기화를 **작업으로 넣는다** — 타이머가 부르는 자리.

    python scripts/sync_datasources.py --due            차례가 된 것(간격이 지난 것)만
    python scripts/sync_datasources.py --all            켜진 것 전부
    python scripts/sync_datasources.py --slug plm_sup   하나만
    python scripts/sync_datasources.py --due --plan     계획만(적용 안 함)
    python scripts/sync_datasources.py --slug x --now   넣지 않고 이 자리에서 돌린다(진단용)

systemd 타이머(`<slug>-sync.timer`, deploy.sh 가 설치)가 몇 분마다 이 스크립트를 컨테이너
안에서 돌린다. 예전에는 여기서 직접 동기화했다 — 지금은 `jobs` 표에 넣고 워커
(`<slug>-worker`)가 돌린다. 그래야 「지금 뭐가 돌고 있나」 가 「작업」 화면 한 곳에 모이고,
진행 · 실패 이유 · 취소가 다른 작업과 같은 모양이 된다(`docs/작업-워커-설계.md`).

**`--due` · `--all` 은 작업 하나(`datasource_sync_round`)로 넣는다** — 그 작업이 소스들을
**가리키는 쪽부터 한 줄로** 돌린다(`services.sync_order`). 소스마다 따로 넣으면 순서가 없어,
「고장 모드」 가 「메커니즘」 보다 먼저 돌면 그 선이 끝점을 못 찾았다. `--slug` 는 하나라 따로.

**두 서버의 타이머가 같은 시각에 돈다.** 잠금을 못 잡은 쪽은 물러나고, 잡은 쪽도 아직 안 끝난
같은 작업이 있으면 또 넣지 않는다 — 같은 원천이 두 번 들어가는 것보다 한 번 건너뛰는 것이
낫다.

감사 기록의 actor 는 「타이머」 다(작업의 시킨 사람이 없다). 권한 판정은 시스템 관리자 계정
하나를 빌린다(`services._actor`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.all_models
import app.main  # noqa: F401  (레지스트리 조립 — 웹훅·원 표)
from app.modules.datasources import services
from app.modules.datasources.models import DataSource
from app.modules.jobs import services as job_services
from app.shared import singleton
from app.shared.errors import AppError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--due", action="store_true", help="간격이 지난 것만")
    group.add_argument("--all", action="store_true", help="켜진 것 전부")
    group.add_argument("--slug", help="하나만")
    parser.add_argument("--plan", action="store_true", help="계획만 (적용 안 함)")
    parser.add_argument(
        "--now", action="store_true", help="작업으로 넣지 않고 이 자리에서 돌린다(진단용)"
    )
    args = parser.parse_args()

    with singleton.held("datasource-sync") as db:
        if db is None:
            print("다른 서버가 동기화 중입니다 — 이번 차례는 건너뜁니다.")
            return 0
        return _run_now(db, args) if args.now else _enqueue(db, args)


def _sources(db: Session, args: argparse.Namespace) -> list[DataSource] | None:
    if args.slug:
        found = db.scalar(select(DataSource).where(DataSource.slug == args.slug))
        if found is None:
            print(f"데이터 소스를 찾을 수 없습니다: {args.slug}", file=sys.stderr)
            return None
        return [found]
    if args.all:
        return list(db.scalars(select(DataSource).where(DataSource.is_active.is_(True))))
    return services.due(db)


def _enqueue(db: Session, args: argparse.Namespace) -> int:
    sources = _sources(db, args)
    if sources is None:
        return 1
    if not sources:
        print("돌릴 것이 없습니다.")
        return 0
    if not args.slug:
        return _enqueue_round(db, sources, apply=not args.plan)
    for source in sources:
        waiting = job_services.pending_for(db, "datasource_sync", slug=source.slug)
        if waiting is not None:
            print(
                f"{source.slug}: 아직 안 끝난 작업이 있습니다({waiting.status}) — 안 넣습니다."
            )
            continue
        job = job_services.enqueue(
            db,
            kind="datasource_sync",
            params={"slug": source.slug, "apply": not args.plan},
            user=None,
            workspace_id=None,
        )
        db.commit()
        print(f"{source.slug}: 작업 {job.id} 넣음 — 워커가 돌립니다")
    return 0


def _enqueue_round(db: Session, sources: list[DataSource], *, apply: bool) -> int:
    """차례 하나 — 작업 하나가 가리키는 쪽부터 한 줄로 돌린다. 앞 차례가 아직 안 끝났으면 안
    넣는다(같은 원천이 두 번 들어가는 것보다 한 번 건너뛰는 것이 낫다). 소스 하나만 따로 넣은
    작업(화면의 「동기화」)이 아직 돌고 있으면 그 소스는 이번 차례에서 뺀다."""
    waiting = job_services.pending_for(db, "datasource_sync_round")
    if waiting is not None:
        print(f"앞 차례가 아직 안 끝났습니다({waiting.status}) — 이번 차례는 건너뜁니다.")
        return 0
    slugs: list[str] = []
    for source in services.sync_order(db, sources):
        alone = job_services.pending_for(db, "datasource_sync", slug=source.slug)
        if alone is not None:
            print(
                f"{source.slug}: 따로 넣은 작업이 아직 안 끝났습니다({alone.status}) — 뺍니다."
            )
            continue
        slugs.append(source.slug)
    if not slugs:
        print("돌릴 것이 없습니다.")
        return 0
    job = job_services.enqueue(
        db,
        kind="datasource_sync_round",
        params={"slugs": slugs, "apply": apply},
        user=None,
        workspace_id=None,
    )
    db.commit()
    print(f"작업 {job.id} 넣음 — 이 순서로 돕니다: {' → '.join(slugs)}")
    return 0


def _run_now(db: Session, args: argparse.Namespace) -> int:
    """워커를 거치지 않고 이 자리에서 — 워커가 죽었을 때 원인을 볼 때만."""
    sources = _sources(db, args)
    if sources is None:
        return 1
    failed = 0
    for source in services.sync_order(db, sources):
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
