"""타입을 전용 표로 승격한다 — **절차의 마지막 단계.** 앞 단계는 `docs/승격-경로.md`.

    python scripts/promote_type.py --type supplier --source supplier          # 계획만
    python scripts/promote_type.py --type supplier --source supplier --apply  # 적용

전용 표가 **같은 id 로** 채워져 있고 `system_sources` 에 등록돼 있어야 한다. 하나라도
빠진 id 가 있으면 아무것도 안 바꾸고 그 목록을 찍는다 — 반쯤 옮긴 상태는 어느 쪽이
맞는지 아무도 모른다.

운영에서는 컨테이너 안에서 돈다(백엔드와 같은 SIF·같은 .env):

    apptainer exec --bind ... app.sif sh -c 'cd /opt/app/backend && \\
      /opt/app/venv/bin/python scripts/promote_type.py --type ... --source ... --apply'
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

import app.all_models
import app.main  # noqa: F401  (system_sources 등록이 여기서 일어난다)
from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.ontology import promotion
from app.modules.ontology.models import ObjectType


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", required=True, help="승격할 타입 slug")
    parser.add_argument("--source", required=True, help="system_sources 에 등록한 원 표 키")
    parser.add_argument("--actor", default="", help="감사 기록에 남길 계정(로그인 아이디)")
    parser.add_argument("--apply", action="store_true", help="실제로 적용 (없으면 계획만)")
    args = parser.parse_args()

    with SessionLocal() as db:
        object_type = db.scalar(select(ObjectType).where(ObjectType.slug == args.type))
        if object_type is None:
            print(f"타입을 찾을 수 없습니다: {args.type}", file=sys.stderr)
            return 1
        actor = db.scalar(select(User).where(User.email == args.actor)) if args.actor else None
        if args.actor and actor is None:
            print(f"계정을 찾을 수 없습니다: {args.actor}", file=sys.stderr)
            return 1

        prepared = promotion.plan(db, object_type, args.source)
        print(f"타입 {prepared.type_slug} → 원 표 {prepared.source_key}")
        print(f"  객체 {prepared.objects}개 · 관계 {prepared.relations}개")
        for message in prepared.errors:
            print(f"  오류: {message}")
        if prepared.missing_ids:
            missing = len(prepared.missing_ids)
            print(f"  전용 표에 없는 id {missing}개 — 먼저 같은 id 로 채우세요:")
            for one in prepared.missing_ids[:20]:
                print(f"    {one}")
        if not prepared.ok:
            return 1
        if not args.apply:
            print("  (계획만. --apply 로 적용)")
            return 0

        if actor is None:
            print("  --actor <로그인 아이디> 로 누가 했는지 남기세요.", file=sys.stderr)
            return 1
        promotion.apply(db, actor, object_type, args.source)
        db.commit()
        print("  적용했습니다. 화면과 MCP 는 같은 slug·같은 id 로 계속 봅니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
