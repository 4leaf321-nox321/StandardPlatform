"""정의 가져오기의 계획을 **응답 모양으로** — 가져오기 경로와 정의 제안(ADR 0096)이 한 벌로
쓴다.

두 벌로 두면 제안의 계획과 미리 보기의 계획이 다른 말을 한다(한쪽만 비슷한 정의를 싣는다 …).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid

from app.modules.ontology import importer, similar
from app.modules.ontology.schemas import ChangeOut, ImportPlanOut, SimilarNearOut, SimilarOut


def plan_out(
    prepared: importer.Plan,
    *,
    applied: bool,
    snapshot_id: uuid.UUID | None,
    found: similar.Found | None = None,
) -> ImportPlanOut:
    return ImportPlanOut(
        applied=applied,
        changes=[
            ChangeOut(kind=c.kind, slug=c.slug, action=c.action, fields=c.fields, via=c.via)
            for c in prepared.changes
        ],
        warnings=prepared.warnings + (found.lines() if found else []),
        errors=prepared.errors,
        snapshot_id=snapshot_id,
        similar=[
            SimilarOut(
                kind=one.kind,
                slug=one.slug,
                label=one.label,
                shape=one.shape,
                near=[SimilarNearOut(**dataclasses.asdict(near)) for near in one.near],
            )
            for one in (found.items if found else [])
        ],
        similar_meaning=found.meaning if found else "none",
    )


def fingerprint(prepared: importer.Plan) -> str:
    """계획의 지문 — 바뀌는 것(종류 · 이름 · 어떻게 · 칸 · 까닭) · 경고 · 오류. 비슷한 정의는
    넣지 않는다(뜻 비교는 임베딩 서버가 있을 때만이라 같은 계획이 달리 보인다)."""
    body = {
        "changes": [
            [c.kind, c.owner_kind, c.slug, c.action, sorted(c.fields), c.via]
            for c in prepared.changes
        ],
        "warnings": prepared.warnings,
        "errors": prepared.errors,
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


#: 요약의 낱말 — 「타입 새로 2」.
KIND_WORDS = {
    "group": "묶음",
    "interface": "인터페이스",
    "interface_property": "인터페이스 속성",
    "type": "타입",
    "property": "속성",
    "relation_type": "관계 종류",
}
ACTION_WORDS = {"create": "새로", "update": "수정"}


def summary(prepared: importer.Plan) -> str:
    """「타입 새로 2 · 속성 새로 12 · 속성 수정 1」 — 바뀌지 않는 것은 세지 않는다."""
    counts: dict[tuple[str, str], int] = {}
    for one in prepared.changes:
        if one.action == "unchanged":
            continue
        counts[(one.kind, one.action)] = counts.get((one.kind, one.action), 0) + 1
    order = list(KIND_WORDS)
    parts = [
        f"{KIND_WORDS.get(kind, kind)} {ACTION_WORDS.get(action, action)} {count}"
        for (kind, action), count in sorted(
            counts.items(),
            key=lambda one: (
                order.index(one[0][0]) if one[0][0] in order else 99,
                one[0][1] != "create",
            ),
        )
    ]
    return " · ".join(parts) or "바뀌는 것 없음"


#: 원천 잠금 없이 새 정의를 세운다는 경고 — 기계(토큰)가 부를 때 · 정의 제안(ADR 0087 · 0096).
UNLOCKED_WARNING = (
    "source 없이 새 정의({names})를 생성합니다 — 원천 잠금(managed_by)이 없어 화면 · 파일 · "
    "MCP 로 누구나 수정할 수 있고, 원천이 다시 적재하면 사람이 수정한 값을 덮어씁니다. 원천 "
    "시스템이 관리하는 정의면 source=<원천 이름>(묶음 가져오기의 source · 원천이 적재할 때 "
    "쓰는 이름과 같게)으로 다시 보내세요. 이 설치에서 직접 관리할 정의면 그대로 두어도 됩니다."
)


def unlocked_warning(prepared: importer.Plan) -> str | None:
    """새로 세우는 타입 · 관계 종류 · 인터페이스가 있으면 그 경고 한 줄 — 부르는 쪽이 `source`
    가 비었는지 본다."""
    made = [
        one.slug
        for one in prepared.changes
        if one.action == "create" and one.kind in ("type", "relation_type", "interface")
    ]
    if not made:
        return None
    shown = ", ".join(made[:5]) + (f" 외 {len(made) - 5}개" if len(made) > 5 else "")
    return UNLOCKED_WARNING.format(names=shown)
