"""뷰 스펙 검증 — **화면이 이미 아는 것만 가리킨다.**

목록·폼·상세의 모양이 데이터가 되면 그 스펙은 작은 언어가 되기 쉽고, 언어는
무한정 자란다. [ADR 0005](../../../../docs/adr/0005-온톨로지-메타모델.md) 가 못을
박은 자리다:

> 새 표현이 필요하면 화면에 기능을 먼저 넣고, 그다음 스펙에 이름을 준다.
> 그리고 **모르는 키가 오면 서버가 거절한다 — 조용히 무시하지 않는다.**

무시하면 「스펙에는 있는데 안 그려지는 필드」 가 쌓인다. 적은 쪽(사람이든 기계든)
은 적었다고 믿고, 화면은 아무 말도 안 한다.

## 묶음은 속성이 들고 있다

`property_defs.section` 이 **어느 묶음에 속하는지**를 정하고, 뷰는 **묶음의 순서와
모양**(열 수·접힘)만 정한다. 뷰가 소속까지 정하면 두 벌이 되고, 갈린 두 벌은
한쪽만 고쳐진다.
"""

from __future__ import annotations

from typing import Any

from app.modules.ontology.models import PropertyDef
from app.modules.ontology.services import InvalidValue
from app.shared.errors import code

#: 목록 화면이 아는 것.
LIST_KEYS = {"columns", "sort", "filters", "search", "tree", "rollups"}
SORT_KEYS = {"field", "dir"}
TREE_KEYS = {"relation", "parent"}
ROLLUP_KEYS = {"property", "fn", "label"}
#: 롤업이 하는 셈. 「아래 전부」 의 숫자 속성을 트리 관계로 모은다.
ROLLUP_FNS = ("sum", "min", "max", "avg", "count")

#: 폼·상세가 아는 것. 둘이 같은 모양인 이유는 **같은 묶음을 쓰기 때문**이다.
FORM_KEYS = {"sections"}
SECTION_KEYS = {"name", "columns", "collapsed"}

#: 속성이 아닌, 어느 타입에나 있는 자리.
BUILT_IN_FIELDS = {"key", "label", "status", "updated_at", "created_at", "owner_workspace"}


def _reject_unknown(spec: dict[str, Any], allowed: set[str], *, what: str) -> None:
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise InvalidValue(
            code("ONTOLOGY", 50),
            f"{what}에 모르는 항목이 있습니다: {', '.join(unknown)}. "
            f"쓸 수 있는 것: {', '.join(sorted(allowed))}. "
            "화면이 모르는 것을 조용히 무시하면, 적어 둔 사람은 적용된 줄 압니다.",
        )


def _require_field(field: Any, keys: set[str], *, what: str) -> None:
    """`properties.<키>` 또는 기본 자리인가.

    **가리키는 속성이 없으면 빈 열이 서고, 빈 열은 「값이 없다」 로 읽힌다.**
    오타를 여기서 잡지 않으면 그 열이 왜 비었는지 물을 자리가 없다.
    """
    if not isinstance(field, str):
        raise InvalidValue(code("ONTOLOGY", 51), f"{what}: 이름은 글자여야 합니다.")
    if field in BUILT_IN_FIELDS:
        return
    if field.startswith("properties."):
        key = field.split(".", 1)[1]
        if key in keys:
            return
        raise InvalidValue(
            code("ONTOLOGY", 52),
            f"{what}: 정의되지 않은 속성입니다: {key}. 속성을 먼저 만드세요.",
        )
    raise InvalidValue(
        code("ONTOLOGY", 53),
        f"{what}: 모르는 자리입니다: {field}. "
        f"속성은 properties.<키>, 그 밖에는 {', '.join(sorted(BUILT_IN_FIELDS))}.",
    )


def validate_list_view(spec: dict[str, Any], defs: list[PropertyDef]) -> dict[str, Any]:
    """목록 화면 스펙."""
    if not spec:
        return {}
    _reject_unknown(spec, LIST_KEYS, what="목록 화면")
    keys = {d.key for d in defs}

    for field in spec.get("columns") or []:
        _require_field(field, keys, what="목록의 열")
    for field in spec.get("search") or []:
        _require_field(field, keys, what="검색 대상 속성")
    for field in spec.get("filters") or []:
        _require_field(field, keys, what="거르기 칸")

    sort = spec.get("sort")
    if sort:
        _reject_unknown(sort, SORT_KEYS, what="기본 정렬")
        _require_field(sort.get("field"), keys, what="기본 정렬")
        if sort.get("dir") not in (None, "asc", "desc"):
            raise InvalidValue(code("ONTOLOGY", 54), "정렬 방향은 asc 나 desc 여야 합니다.")

    tree = spec.get("tree")
    if tree:
        _reject_unknown(tree, TREE_KEYS, what="트리")
        if not tree.get("relation"):
            raise InvalidValue(
                code("ONTOLOGY", 55),
                "트리를 표시하려면 어느 관계로 그릴지 선택해야 합니다 — "
                "재귀로 펼치는 관계가 둘 이상일 수 있어서, 아무거나 그리면 그 트리는 "
                "무엇을 보여 주는지 말할 수 없습니다.",
            )
        if tree.get("parent") not in (None, "src", "dst"):
            raise InvalidValue(
                code("ONTOLOGY", 56), "트리의 부모 쪽은 src 나 dst 여야 합니다."
            )

    # 롤업 — 트리가 있어야 뜻이 있고, 숫자 칸만 모은다. 글자를 더하면 그 수는 아무 뜻도 없다.
    rollups = spec.get("rollups") or []
    if rollups and not tree:
        raise InvalidValue(
            code("ONTOLOGY", 60),
            "롤업은 트리 관계가 있어야 합니다 — 무엇의 「아래 전부」 를 모을지 "
            "트리가 정합니다.",
        )
    numeric = {d.key for d in defs if d.data_type == "number"}
    for one in rollups:
        if not isinstance(one, dict):
            raise InvalidValue(code("ONTOLOGY", 60), "롤업은 표여야 합니다.")
        _reject_unknown(one, ROLLUP_KEYS, what="롤업")
        if one.get("property") not in numeric:
            raise InvalidValue(
                code("ONTOLOGY", 60),
                f"롤업은 숫자 속성만 모읍니다: {one.get('property')!r} 은 숫자 칸이 아닙니다.",
            )
        if one.get("fn") not in ROLLUP_FNS:
            raise InvalidValue(
                code("ONTOLOGY", 60),
                f"롤업의 셈은 {', '.join(ROLLUP_FNS)} 중 하나여야 합니다: {one.get('fn')!r}",
            )
    return spec


def validate_form_view(
    spec: dict[str, Any], defs: list[PropertyDef], *, what: str
) -> dict[str, Any]:
    """폼·상세 스펙. **묶음의 소속은 여기서 안 정한다** — 속성이 들고 있다."""
    if not spec:
        return {}
    _reject_unknown(spec, FORM_KEYS, what=what)
    known_sections = {d.section for d in defs if d.section}

    seen: set[str] = set()
    for section in spec.get("sections") or []:
        if not isinstance(section, dict):
            raise InvalidValue(code("ONTOLOGY", 57), f"{what}: 묶음은 표여야 합니다.")
        _reject_unknown(section, SECTION_KEYS, what=f"{what}의 묶음")

        name = section.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidValue(code("ONTOLOGY", 58), f"{what}: 묶음에 이름이 없습니다.")
        if name in seen:
            raise InvalidValue(
                code("ONTOLOGY", 59), f"{what}: 같은 묶음이 두 번 나옵니다: {name}"
            )
        seen.add(name)
        if name not in known_sections:
            # **속성이 하나도 없는 묶음은 빈 칸으로 선다.** 비어 있는 제목은
            # 「뭔가 안 나온다」 로 읽힌다.
            raise InvalidValue(
                code("ONTOLOGY", 60),
                f"{what}: 그 묶음에 속한 속성이 없습니다: {name}. "
                "속성 정의의 「묶음」 에 같은 이름을 적으세요.",
            )

        columns = section.get("columns")
        if columns is not None and columns not in (1, 2, 3):
            raise InvalidValue(
                code("ONTOLOGY", 61), f"{what}: 열 수는 1·2·3 중 하나여야 합니다."
            )
        if section.get("collapsed") not in (None, True, False):
            raise InvalidValue(
                code("ONTOLOGY", 62), f"{what}: 접힘은 예/아니오 값이어야 합니다."
            )
    return spec


def prune_field(spec: dict[str, Any], key: str) -> dict[str, Any]:
    """지워진 속성을 뷰에서 걷어낸다.

    **안 걷어내면 그 뒤로 타입을 고칠 때마다 「없는 속성」 이라고 거절당한다** —
    그리고 사람은 자기가 방금 고친 것과 상관없는 그 오류를 이해할 수 없다.
    """
    field = f"properties.{key}"
    out = dict(spec)
    for name in ("columns", "search", "filters"):
        if name in out and isinstance(out[name], list):
            out[name] = [one for one in out[name] if one != field]
    sort = out.get("sort")
    if isinstance(sort, dict) and sort.get("field") == field:
        out.pop("sort")
    return out
