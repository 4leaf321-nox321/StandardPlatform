"""지표 다시 계산을 **작업으로 넣는다** — 타이머가 부르는 자리(ADR 0013).

    python scripts/recompute_metrics.py --due            차례가 된 것(주기가 지난 것)만
    python scripts/recompute_metrics.py --all            켜진 것 전부
    python scripts/recompute_metrics.py --slug cases     하나만
    python scripts/recompute_metrics.py --slug x --now   넣지 않고 이 자리에서 돌린다(진단용)

systemd 타이머(`<slug>-metrics.timer`, deploy.sh 가 설치)가 밤마다 이 스크립트를 컨테이너
안에서 돌린다. 데이터 소스 동기화(`sync_datasources.py`)와 같은 무늬다 — `jobs` 표에
**지표마다 한 줄**을 넣고 워커(`<slug>-worker`)가 돌린다. 그래야 「지금 뭐가 돌고 있나」 가
「작업」 화면 한 곳에 모이고, 진행 · 실패 이유 · 취소가 다른 작업과 같은 모양이 된다.

**두 서버의 타이머가 같은 시각에 돈다.** 잠금을 못 잡은 쪽은 물러나고, 잡은 쪽도 아직 안 끝난
같은 지표의 작업이 있으면 또 넣지 않는다. 적재 뒤 훅(`jobs/kinds.py`)이 넣은 작업과도 같은
규칙으로 겹치지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.all_models
import app.main  # noqa: F401  (레지스트리 조립 — 작업 종류 · 원 표)
from app.modules.metrics import services
from app.modules.metrics.models import MetricDef
from app.shared import singleton
from app.shared.errors import AppError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--due", action="store_true", help="주기가 지난 것만")
    group.add_argument("--all", action="store_true", help="켜진 것 전부")
    group.add_argument("--slug", help="하나만")
    parser.add_argument(
        "--now", action="store_true", help="작업으로 넣지 않고 이 자리에서 돌린다(진단용)"
    )
    args = parser.parse_args()

    with singleton.held("metrics-recompute") as db:
        if db is None:
            print("다른 서버가 지표를 넣는 중입니다 — 이번 차례는 건너뜁니다.")
            return 0
        return run_now(db, args) if args.now else enqueue(db, args)


def pick(db: Session, args: argparse.Namespace) -> list[MetricDef] | None:
    if args.slug:
        found = db.scalar(select(MetricDef).where(MetricDef.slug == args.slug))
        if found is None:
            print(f"지표를 찾을 수 없습니다: {args.slug}", file=sys.stderr)
            return None
        return [found]
    if args.all:
        return list(
            db.scalars(
                select(MetricDef).where(MetricDef.is_active.is_(True)).order_by(MetricDef.slug)
            )
        )
    return services.due(db)


def enqueue(db: Session, args: argparse.Namespace) -> int:
    metrics = pick(db, args)
    if metrics is None:
        return 1
    if not metrics:
        print("돌릴 것이 없습니다.")
        return 0
    for metric in metrics:
        waiting = services.pending_recompute(db, metric.slug)
        if waiting is not None:
            print(
                f"{metric.slug}: 아직 안 끝난 작업이 있습니다({waiting.status}) — 안 넣습니다."
            )
            continue
        job = services.enqueue_recompute(db, metric, user=None, reason="timer")
        db.commit()
        print(f"{metric.slug}: 작업 {job.id} 넣음 — 워커가 돌립니다")
    return 0


def run_now(db: Session, args: argparse.Namespace) -> int:
    """워커를 거치지 않고 이 자리에서 — 워커가 죽었을 때 원인을 볼 때만."""
    metrics = pick(db, args)
    if metrics is None:
        return 1
    slugs = [one.slug for one in metrics]
    try:
        result = services.run_recompute(
            db, slugs, job_id=None, progress=lambda stage, done, total: None
        )
    except AppError as caught:
        print(f"오류 — {caught.message}", file=sys.stderr)
        return 1
    for run in result["runs"]:
        print(
            f"{run['slug']}: {run['status']} 기록 {run.get('rows', 0)} "
            f"셀 {run.get('cells', 0)} {run.get('seconds', 0)}초"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
