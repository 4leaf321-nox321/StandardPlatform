"""사람이 고친 칸을 **표시하고 지켜 준다.**

## 왜 있나

파일로 넣기는 같은 식별자면 덮는다(upsert). 그래서 원천을 다시 정제해 넣으면 **사람이 화면에서
고친 값이 사라진다** — 그리고 고친 사람은 그 사실을 모른다. 다음 적재가 조용히 되돌리고, 몇 주
뒤 「내가 고쳤는데 다시 틀려 있다」 로 만난다. 그때 사람은 고치기를 그만둔다.

## 규칙 둘

- **표시하는 쪽**은 사람이 손으로 고치는 길뿐이다(화면 · 사람 세션). 기계 자격(PAT)으로 같은
  경로를 불러도 표시하지 않는다 — 그 값은 다음 적재가 덮어도 되는 값이다. 가르는 기준은
  **그 요청에 토큰이 있었나**다(`request_context`).
- **지키는 쪽**은 파일 · 묶음 · 데이터 소스 적재다. 표시된 칸은 비켜 가고 **계획에 적는다**.
  `human_edits="overwrite"` 로 보내면 덮고 표시를 지운다 — 사람이 그렇게 정했을 때만.

## 칸 이름

고정 칸은 그대로(`label` · `status` · `description` · `valid_from_year` · `valid_to_year`),
속성은 `properties.<키>` 다. 목록 열(`list_view.columns`)과 같은 표기이고, `label` 이라는
이름의 속성이 있어도 섞이지 않는다.

**식별자(`key`)는 표시하지 않는다** — 파일이 그 값으로 이 객체를 찾는다. 못 바꾸게 두면 파일과
플랫폼이 영영 다른 식별자를 들고 간다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.modules.objects.models import ObjectInstance
from app.modules.ontology.models import PropertyDef
from app.shared.request_context import get_actor_token

#: 표시하는 고정 칸. `key` 는 일부러 빠져 있다(위).
FIXED = ("label", "description", "status", "valid_from_year", "valid_to_year")

#: 속성 칸의 앞머리 — 목록 열과 같은 표기.
PROP = "properties."

#: 적재에 주는 선택. `keep` 은 지키고 적는다, `overwrite` 는 덮고 표시를 지운다.
MODES = ("keep", "overwrite")


def by_human() -> bool:
    """이 요청이 **사람 세션**인가 — 기계 자격(PAT)이면 거짓.

    워커(작업)에는 요청 문맥이 없어 토큰도 없다. 그래서 이 함수는 **요청 안에서만** 부른다 —
    워커가 돌리는 적재는 애초에 표시하는 길이 아니다.
    """
    return get_actor_token() is None


def of(row: ObjectInstance) -> dict[str, str]:
    return dict(row.human_edits or {})


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """`audit_state` 두 장을 견줘 **바뀐 칸 이름**을 낸다 — 고정 칸과 속성 하나하나."""
    out = [name for name in FIXED if before.get(name) != after.get(name)]
    old = dict(before.get("properties") or {})
    new = dict(after.get("properties") or {})
    out.extend(
        f"{PROP}{key}" for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)
    )
    return out


def record(row: ObjectInstance, before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """사람이 방금 고친 칸을 표시한다 — **기계 자격이면 아무 일도 안 한다.**

    `audit.record` 를 부르는 자리 바로 옆에서 부른다: 같은 diff 를 보고, 같은 트랜잭션에
    남는다. 표시한 칸 이름을 돌려준다(시험과 로그가 읽는다).
    """
    if not by_human():
        return []
    names = changed_fields(before, after)
    if not names:
        return []
    now = datetime.now(UTC).isoformat()
    row.human_edits = {**of(row), **{name: now for name in names}}
    return names


def protected(row: ObjectInstance, names: Iterable[str]) -> list[str]:
    """이 칸들 중 **사람이 고쳐 둔 것** — 적재가 비켜 갈 자리."""
    marks = of(row)
    return [name for name in names if name in marks]


def release(row: ObjectInstance, names: Iterable[str]) -> None:
    """표시를 지운다 — `overwrite` 로 덮었으면 그 칸은 이제 파일의 값이다."""
    marks = of(row)
    for name in names:
        marks.pop(name, None)
    row.human_edits = marks


def label_of(name: str, defs: list[PropertyDef] | None = None) -> str:
    """사람이 읽는 칸 이름 — 계획의 줄에 적는다. 속성은 그 속성의 라벨로."""
    fixed = {
        "label": "이름",
        "description": "설명",
        "status": "상태",
        "valid_from_year": "유효 시작",
        "valid_to_year": "유효 끝",
    }
    if name in fixed:
        return fixed[name]
    key = name[len(PROP) :] if name.startswith(PROP) else name
    for definition in defs or []:
        if definition.key == key:
            return definition.label
    return key


def note(names: list[str], defs: list[PropertyDef] | None = None) -> str:
    """계획에 적을 한 줄. **무엇을 안 덮었는지 이름으로 말한다** — 수만 말하면 사람은
    자기가 고친 칸이 그중에 있는지 알 수 없다."""
    if not names:
        return ""
    shown = " · ".join(label_of(name, defs) for name in names[:5])
    more = f" 외 {len(names) - 5}칸" if len(names) > 5 else ""
    return f"사람이 고친 칸은 그대로 둡니다 — {shown}{more}"
