"""코어 구독(ADR 0085) — 형제 Standard Platform 의 코어를 **통째로** 받는다.

묶음 구독(ADR 0055)은 그 순간 있던 타입마다 형제 코어 소스를 한 번 만들고 끝났다. 그래서 허브에
공개 타입이 하나 늘 때마다 받는 쪽은 정의 묶음 파일을 다시 받아 가져오고, 토큰을 다시 넣어
구독을 다시 했다. 칸이 하나 늘면 정의를 다시 받고 그 소스의 칸 대응을 다시 맞춰야 했고 — 안
하면 **새 칸의 값이 오류 없이 빠졌다**(대응에 없는 칸은 받는 쪽이 버린다).

구독은 그 일을 확인마다 한다(`reconcile`):

1. **정의를 맞춘다** — 허브의 `sp-definitions` 를 지문(`known`)으로 묻고, 바뀌었으면 가져오기와
   같은 계획을 세운다. 저장값을 잃지 않는 변경(새 타입 · 새 칸 · 이름 · 설명 · 고를 값 추가)은
   곧장 적용하고, 잃을 수 있는 것(속성 종류 · 고를 값 제외 · 필수 · 유일 · 여러 값 · 개수 제약
   · 이 설치가 만든 정의를 허브 관리로 넘기기 — 가져오기 계획의 경고 · 종류 변경 · 오류)은
   **정의 검토 대기**(`held`)로 남긴다. 시스템 관리자가 줄을 읽고 적용한다. 플랫폼 칸과 같은
   키(`status` …)의 새 속성은 **그 속성만** 남기고 나머지는 적용한다(`hold_reserved`).
2. **소스를 맞춘다** — 공개 타입마다 형제 코어 소스를 두고(없으면 만든다), 칸 대응에 허브에
   생긴 칸을 더하고 빠진 칸을 뺀다. 토큰 · 간격은 구독의 값으로. 이미 받은 행에도 값이 있을
   수 있는 칸을 새로 이으면 그 소스는 처음부터 다시 받는다.
3. **공개가 해제된 타입은 수신 중지** — 그 소스를 비활성으로 두고 받은 자료는 남긴다. 다시
   공개되면 구독이 다시 활성화한다(구독이 멈춘 것만 — 사람이 비활성화한 소스는 안 건드린다).
4. **허브가 타입의 객체를 다른 타입으로 옮겼으면**(카탈로그의 `moved_at` · `moved_to`, ADR
   0094) 옮겨 간 곳의 소스를 처음부터 다시 받고, 그 소스가 같은 식별자로 받아 온 것은 이 설치의
   원 타입 쪽 객체를 **사용 중지**한다(지우지 않는다 — 이 설치의 기록이 그것을 가리킬 수 있다).
   원 타입은 허브에서 비어 「바뀐 것」 이 없으니, 그대로 두면 같은 객체가 두 타입에 산다
   (`_moved`).

## 건드리지 않는 것

- **이 설치가 만든 같은 slug 의 정의**(관리 이름이 구독의 것이 아닌 타입 · 관계 종류 ·
  인터페이스) — 허브에 같은 이름이 있다고 남의 정의를 빼앗지 않는다. 까닭을 적고 건너뛴다.
- **허브에서 빠진 칸 · 타입의 정의와 값** — 가져오기는 지우지 않는다(`importer` 머리말). 칸
  대응에서만 빼서, 허브가 그 칸을 안 보낸다고 이쪽 값을 비우지 않게 한다(형제 코어는
  거울이다).
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session, aliased

from app.modules.accounts.models import User
from app.modules.bundles import services as bundle_services
from app.modules.bundles.schemas import BundleIn
from app.modules.datasources import fetchers, odata
from app.modules.datasources import services as source_services
from app.modules.datasources.models import CoreSubscription, DataSource
from app.modules.datasources.schemas import (
    CoreGroupChoiceOut,
    CoreHeldOut,
    CoreSubscriptionOut,
    CoreSubscriptionPlanOut,
    CoreSubscriptionRequest,
    CoreSubscriptionSourceOut,
    CoreSubscriptionTypeOut,
)
from app.modules.objects import aliases
from app.modules.objects.models import ObjectAlias, ObjectInstance
from app.modules.ontology import importer, managed
from app.modules.ontology.models import ObjectInterface, ObjectType, RelationType
from app.modules.ontology.services import RESERVED_PROPERTY_KEYS
from app.shared import audit, singleton
from app.shared.errors import AppError, Conflict, NotFound, code

#: 같은 구독을 다른 작업이 지금 확인하고 있다 — 실패가 아니다(그 작업이 한다).
BUSY = code("DATASOURCES", 66)

#: 받는 행의 봉투 칸 — 허브의 속성이 아니다. 칸 대응을 허브에 맞출 때 빼지 않는다.
ENVELOPE = frozenset({*fetchers.CORE_ENVELOPE, "aliases"})
#: 허브에 정의 창구(`sp-definitions`)가 없을 때 — 0.4.90 까지의 설치.
NO_DEFINITIONS = (
    "허브에 정의 창구가 없습니다(0.4.90 까지) — 정의는 「정의만」 묶음으로 받고, 이 설치에 "
    "정의가 있는 타입만 받습니다."
)
#: 알림 줄의 상한 — 수천 줄을 화면에 내면 아무도 안 읽는다. **넘치면 끝에 「외 N건」 을
#: 적는다** — 말없이 자르면 사람은 그것이 전부인 줄 안다(60줄에서 잘린 운영 화면, 2026-11-18).
#: 정의 검토 대기의 줄은 자르지 않는다 — 적용하는 사람이 무엇이 바뀌는지 전부 봐야 한다.
NOTES_MAX = 200


def capped(lines: list[str], limit: int = NOTES_MAX) -> list[str]:
    """`limit` 줄까지 — 넘치면 마지막 줄에 「… 외 N건」."""
    if len(lines) <= limit:
        return list(lines)
    return [*lines[:limit], f"… 외 {len(lines) - limit}건"]


def managed_name(db: Session, wanted: str, system: str, slugs: set[str]) -> str:
    """구독이 받는 정의의 **관리 이름** — 적은 이름, 또는 이 설치의 허브 정의가 이미 허브의
    이름(카탈로그의 `system`)으로 관리되고 있으면 그 이름.

    허브의 정의를 「정의만」 묶음 · 파이프라인으로 받을 때 `source` 를 허브의 이름으로 적은
    설치가 있다. 구독이 기본 관리 이름(`hub`)으로 그 타입들을 보면 전부 「이 설치가 만든 같은
    이름 타입」 이 되어 하나도 못 받았다(운영 37개, 2026-11-18). **공개 타입(`core`)이고 관리
    이름이 허브의 이름이면 허브의 것이다** — 적은 이름이 관리하는 허브 타입이 하나도 없을 때 그
    이름으로 받는다 (둘이 섞여 있으면 적은 이름 그대로 — 한 구독은 관리 이름 하나로 넣는다)."""
    if not system or system == wanted or not slugs:
        return wanted
    rows = list(db.scalars(select(ObjectType).where(ObjectType.slug.in_(sorted(slugs)))))
    if any(managed.owner_of(one) == wanted for one in rows):
        return wanted
    if any(managed.owner_of(one) == system and one.core for one in rows):
        return system
    return wanted


#: 허브가 옮긴 타입의 객체를 한 번 확인에서 사용 중지할 수 — 넘치면 다음 확인이 잇는다(한
#: 트랜잭션에 수백만 줄의 감사를 쌓지 않게).
MOVED_PER_CHECK = 20_000


#: 자동으로는 바꾸지 않는 속성의 칸 — 바꾸면 저장값을 변환하거나(종류) 못 읽게 된다(참조 대상 ·
#: 여러 값). 정의 검토 대기로 남는다.
_FIELDS_HELD_AS_LOCAL = ("data_type", "ref_type_slug", "multi")


# --- 허브에 묻기 ----------------------------------------------------------------


def auth_of(row: CoreSubscription) -> odata.Auth:
    raw = row.auth or {}
    return odata.Auth(
        kind=str(raw.get("kind") or "bearer"),
        user=str(raw.get("user") or ""),
        secret=str(raw.get("secret") or ""),
    )


def _catalog(base_url: str, auth: odata.Auth) -> dict[str, Any]:
    body = fetchers.core_catalog(
        base_url=base_url, auth=auth, transport=source_services.transport
    )
    if "sp" not in (body.get("shapes") or []):
        raise AppError(
            code("DATASOURCES", 60),
            "상대 설치가 기준정보를 온전히 주는 모양(shape=sp)을 모릅니다 — 상대를 0.4.78 "
            "이상으로 올린 뒤 구독하세요. 그 전에는 기간이 다른 선 · 계보 고정이 빠집니다.",
            status=422,
        )
    return body


def _definitions(
    base_url: str, auth: odata.Auth, groups: list[str], known: str
) -> dict[str, Any] | None:
    return fetchers.core_definitions(
        base_url=base_url,
        auth=auth,
        transport=source_services.transport,
        groups=groups,
        known=known,
    )


# --- 정의 -----------------------------------------------------------------------


@dataclass
class Prepared:
    """허브의 정의를 **이 설치에 세울 수 있는 만큼** 다듬은 것."""

    payload: dict[str, Any]
    skipped: dict[str, str] = field(default_factory=dict)
    """받지 않는 타입 → 까닭(이 설치가 만든 같은 slug 의 정의)."""
    notes: list[str] = field(default_factory=list)


def prepare(db: Session, ontology: dict[str, Any], source_name: str) -> Prepared:
    """허브의 정의에서 **남의 것과 못 세울 것**을 뺀다.

    - 이 설치에 같은 slug 의 정의가 있고 그 관리 이름이 구독의 것이 아니면 뺀다 — 가져오기는
      그것을 허브 관리로 넘긴다(그 뒤로 이 설치에서 못 고친다). 남의 정의를 이름이 같다고
      빼앗지 않는다.
    - 참조 칸이 이 설치에 없는 타입을 가리키면(허브의 비공개 타입) 그 칸을 빼고 세운다 — 그
      값은 허브가 코어로 내보내지 않는다(`withheld_references`). 그 타입의 화면 모양도 이쪽
      것을 둔다(뺀 칸을 가리킬 수 있다).
    - 양끝이 다 서지 않는 관계 종류는 뺀다.
    """
    types = {one.slug: one for one in db.scalars(select(ObjectType))}
    kinds = {one.slug: one for one in db.scalars(select(RelationType))}
    ifaces = {one.slug: one for one in db.scalars(select(ObjectInterface))}
    out = Prepared(payload={})

    def ours(row: ObjectType | RelationType | ObjectInterface | None) -> bool:
        return row is None or managed.owner_of(row) == source_name

    kept_ifaces = []
    for one in ontology.get("interfaces") or []:
        if ours(ifaces.get(str(one.get("slug")))):
            kept_ifaces.append(copy.deepcopy(one))
        else:
            out.notes.append(
                f"인터페이스 {one.get('slug')}: 이 설치가 만든 같은 이름의 인터페이스가 있어 "
                "받지 않습니다."
            )
    kept_types = []
    for one in ontology.get("types") or []:
        slug = str(one.get("slug"))
        if ours(types.get(slug)):
            kept_types.append(copy.deepcopy(one))
        else:
            out.skipped[slug] = (
                "이 설치가 만든 같은 이름의 타입이 있어 받지 않습니다 — 허브 것으로 받으려면 "
                "그 타입을 다른 slug 로 옮기세요."
            )
    # 참조 · 관계 끝이 설 수 있는 것 — 함께 받는 것과, 이 구독이 이미 받아 둔 것.
    reachable = {str(one["slug"]) for one in [*kept_types, *kept_ifaces]}
    reachable |= {slug for slug, row in types.items() if managed.owner_of(row) == source_name}
    reachable |= {slug for slug, row in ifaces.items() if managed.owner_of(row) == source_name}

    def trim(owner: dict[str, Any], what: str) -> list[str]:
        dropped = [
            str(prop.get("key"))
            for prop in owner.get("properties") or []
            if prop.get("data_type") == "object_ref"
            and prop.get("ref_type_slug") not in reachable
        ]
        if dropped:
            owner["properties"] = [
                prop
                for prop in owner.get("properties") or []
                if str(prop.get("key")) not in dropped
            ]
            out.notes.append(
                f"{what} {owner.get('slug')}: 참조 칸 {', '.join(dropped)} 은(는) 이 설치에 "
                "없는 타입을 가리켜 빼고 세웁니다 — 그 값은 허브가 코어로 내보내지 않습니다."
            )
        return dropped

    for one in kept_ifaces:
        trim(one, "인터페이스")
    for one in kept_types:
        dropped = trim(one, "타입")
        if dropped:
            for name in ("list_view", "form_view", "detail_view", "title_template"):
                one.pop(name, None)
            if one.get("revision_key") in dropped:
                one.pop("revision_key", None)
            if one.get("label_prefix") in {f"properties.{key}" for key in dropped}:
                one.pop("label_prefix", None)
    kept_kinds = []
    for one in ontology.get("relation_types") or []:
        slug = str(one.get("slug"))
        ends = {*(one.get("src_type_slugs") or []), *(one.get("dst_type_slugs") or [])}
        if not ours(kinds.get(slug)):
            out.notes.append(
                f"관계 종류 {slug}: 이 설치가 만든 같은 이름의 관계 종류가 있어 받지 않습니다."
            )
        elif not ends <= reachable:
            out.notes.append(
                f"관계 종류 {slug}: 끝 {', '.join(sorted(ends - reachable))} 이(가) 이 설치에 "
                "서지 않아 받지 않습니다."
            )
        else:
            kept = copy.deepcopy(one)
            trim(kept, "관계 종류")
            kept_kinds.append(kept)
    out.payload = {
        "groups": copy.deepcopy(ontology.get("groups") or []),
        "interfaces": kept_ifaces,
        "types": kept_types,
        "relation_types": kept_kinds,
    }
    return out


def clean(plan: importer.Plan) -> bool:
    """**저장값을 잃지 않고** 적용되는 계획인가 — 경고 · 종류 변경 · 오류가 하나도 없다.

    가져오기 계획의 경고는 「적용은 되지만 조용히 무언가를 잃는 것」 이다(`importer.Plan`).
    그것이 하나라도 있으면 사람이 읽어야 한다 — 구독이 대신 정하지 않는다."""
    return not plan.errors and not plan.warnings and not plan.retypes


def _changes(plan: importer.Plan) -> int:
    return sum(1 for one in plan.changes if one.action != "unchanged")


def lines_of(plan: importer.Plan) -> list[str]:
    """검토 대기로 남길 줄 — 오류를 먼저, 그다음 경고. 종류 변경은 경고가 이미 말한다."""
    out = [f"적용 안 됨: {one}" for one in plan.errors] + list(plan.warnings)
    for target in plan.retypes:
        if not any(target.name in one for one in out):
            out.append(f"속성 {target.name}: 종류를 바꿉니다.")
    return out


def _union(first: list[Any] | None, second: list[Any] | None) -> list[Any]:
    out = list(first or [])
    out.extend(one for one in second or [] if one not in out)
    return out


def _soft_property(prop: dict[str, Any], local: dict[str, Any] | None) -> dict[str, Any]:
    if local is None:
        return prop
    out = dict(prop)
    for name in _FIELDS_HELD_AS_LOCAL:
        if name in out:
            out[name] = local.get(name)
    for name in ("required", "unique"):
        if out.get(name) and not local.get(name):
            out[name] = False
    if "enum_options" in out and local.get("enum_options"):
        out["enum_options"] = _union(out.get("enum_options"), local.get("enum_options"))
    return out


def _soft_properties(owner: dict[str, Any], local: dict[str, Any]) -> None:
    by_key = {str(one.get("key")): one for one in local.get("properties") or []}
    owner["properties"] = [
        _soft_property(prop, by_key.get(str(prop.get("key"))))
        for prop in owner.get("properties") or []
    ]


_TIGHTNESS = {"many_to_many": 0, "one_to_many": 1, "many_to_one": 1, "one_to_one": 2}


def soften(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """**저장값을 잃을 수 있는 변경을 이쪽 값으로 되돌린** 정의 — 나머지(새 타입 · 새 칸 · 이름
    · 설명 · 고를 값 추가 …)만 바뀐다.

    속성의 종류 · 참조 대상 · 여러 값은 이쪽 값, 필수 · 유일은 켜기만 되돌리고, 고를 값과 구현
    인터페이스 · 상위 인터페이스는 합친다(빼기만 되돌린다). 타입의 식별자 필수 · 비활성 · 분류,
    관계 종류의 개수 제약 강화도 이쪽 값. 이것으로도 계획에 경고가 남으면 아무것도 자동으로
    적용하지 않는다(`reconcile`) — 여기 적지 못한 위험을 짐작으로 넘기지 않는다."""
    local = importer.capture(db)
    local_types = {str(one["slug"]): one for one in local["types"]}
    local_ifaces = {str(one["slug"]): one for one in local["interfaces"]}
    local_kinds = {str(one["slug"]): one for one in local["relation_types"]}
    out = copy.deepcopy(payload)
    for one in out.get("types") or []:
        have = local_types.get(str(one.get("slug")))
        if have is None:
            continue
        if one.get("key_policy") == "required" and have.get("key_policy") != "required":
            one["key_policy"] = have.get("key_policy")
        if one.get("is_active") is False and have.get("is_active"):
            one["is_active"] = True
        if "kind_class" in one:
            one["kind_class"] = have.get("kind_class")
        if "interface_slugs" in one:
            one["interface_slugs"] = _union(
                one.get("interface_slugs"), have.get("interface_slugs")
            )
        _soft_properties(one, have)
    for one in out.get("interfaces") or []:
        have = local_ifaces.get(str(one.get("slug")))
        if have is None:
            continue
        if "extends_slugs" in one:
            one["extends_slugs"] = _union(one.get("extends_slugs"), have.get("extends_slugs"))
        _soft_properties(one, have)
    for one in out.get("relation_types") or []:
        have = local_kinds.get(str(one.get("slug")))
        if have is None:
            continue
        wanted = str(one.get("cardinality") or "")
        if _TIGHTNESS.get(wanted, 0) > _TIGHTNESS.get(str(have.get("cardinality")), 0):
            one["cardinality"] = have.get("cardinality")
        _soft_properties(one, have)
    return out


#: 플랫폼 칸과 같은 키의 속성을 뺀 타입 · 인터페이스에서 이번에는 바꾸지 않는 칸 — 그 키를
#: 가리킬 수 있다(목록의 열 · 폼 · 제목 틀). 정의 검토 대기를 적용하면 허브의 것이 들어간다.
_VIEWS_HELD_WITH_PROPERTY = ("list_view", "form_view", "detail_view", "title_template")


def hold_reserved(db: Session, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """플랫폼 칸과 같은 키(`status` …)의 **새** 속성을 뺀 정의와, 뺀 속성(`<slug>.<키>`).

    그 키는 받는 쪽에서 경고(일괄 입력 · 묶음의 행으로 못 채운다)라 계획이 「깨끗」 하지 않다 —
    전에는 그 경고 하나로 묶음 전체가 정의 검토 대기에 걸려, 같은 때 허브에 생긴 다른 타입 ·
    칸까지 자동으로 들어오지 않았다(2026-11-27 점검). 그 속성만 남기고(그 타입의 화면 설정도
    이번에는 그대로) 나머지를 적용한다. 이미 있는 그 키의 속성은 경고가 없으니 그대로 둔다.
    다른 길(묶음 가져오기 · `?source=` 정의 가져오기)은 그대로 막는다."""
    local = importer.capture(db)
    have = {
        (section, str(one["slug"])): {str(prop.get("key")) for prop in one["properties"]}
        for section in ("types", "interfaces")
        for one in local[section]
    }
    out = copy.deepcopy(payload)
    held: list[str] = []
    for section in ("types", "interfaces"):
        for one in out.get(section) or []:
            slug = str(one.get("slug"))
            known = have.get((section, slug), set())
            dropped = [
                str(prop.get("key"))
                for prop in one.get("properties") or []
                if str(prop.get("key")) in RESERVED_PROPERTY_KEYS
                and str(prop.get("key")) not in known
            ]
            if not dropped:
                continue
            one["properties"] = [
                prop
                for prop in one.get("properties") or []
                if str(prop.get("key")) not in dropped
            ]
            for name in _VIEWS_HELD_WITH_PROPERTY:
                one.pop(name, None)
            if one.get("revision_key") in dropped:
                one.pop("revision_key", None)
            if one.get("label_prefix") in {f"properties.{key}" for key in dropped}:
                one.pop("label_prefix", None)
            held.extend(f"{slug}.{key}" for key in dropped)
    return out, held


def held_reserved_note(names: list[str]) -> str:
    return (
        f"속성 {', '.join(names)}: 플랫폼 칸과 같은 키라 이 속성만 정의 검토 대기로 남기고 "
        "나머지 정의는 적용했습니다(그 타입의 화면 설정도 이번에는 그대로). 정의 검토 대기를 "
        "적용하면 들어갑니다."
    )


@dataclass
class Settled:
    """정의를 맞춘 결과."""

    applied: int = 0
    """적용한 정의 변경 수."""
    held: list[str] = field(default_factory=list)
    """정의 검토 대기로 남긴 줄."""
    payload: dict[str, Any] = field(default_factory=dict)
    """허브의 정의를 다듬은 것 — 검토 대기를 적용할 때 이것을 그대로 넣는다."""
    notes: list[str] = field(default_factory=list)
    """구독의 알림 줄 — 나머지를 적용하고 무엇만 남겼나(`hold_reserved`)."""


def _apply(
    actor: User,
    payload: dict[str, Any],
    source_name: str,
    commit_check: Callable[[Session], None] | None,
) -> None:
    """묶음 가져오기와 **같은 길로** 넣는다 — 스냅샷 · 감사 · 묶음 기록(되돌리기)이 함께
    남는다."""
    outcome = bundle_services.run(
        actor,
        BundleIn(
            ontology=payload,
            source=source_name,
            apply=True,
            audit="summary",
            events="summary",
        ),
        commit_check=commit_check,
        # 허브가 예전부터 쓰던 `status` 같은 칸 — 받는 쪽이 막으면 수신이 통째로 보류된다.
        keep_reserved=True,
    )
    if not outcome.ok or not outcome.applied:
        said = [
            *outcome.errors,
            *(outcome.ontology.errors if outcome.ontology is not None else []),
        ]
        raise AppError(
            code("DATASOURCES", 61),
            "허브의 정의를 적용하지 못했습니다: " + ("; ".join(said[:5]) or "까닭 모름"),
            status=409,
        )


def settle(
    db: Session,
    actor: User,
    prepared: Prepared,
    source_name: str,
    *,
    apply: bool,
    commit_check: Callable[[Session], None] | None = None,
) -> Settled:
    """정의를 맞춘다 — 깨끗한 계획이면 통째로, 아니면 위험한 변경을 되돌리고(`soften`) 플랫폼
    칸과 같은 키의 새 속성을 뺀 것만(`hold_reserved`).

    `apply` 가 거짓이면 계획만 세운다(구독의 미리 보기)."""
    payload = prepared.payload
    out = Settled(payload=payload)
    full = importer.plan(db, payload, source=source_name, keep_reserved=True)
    if clean(full):
        if apply and _changes(full):
            db.commit()  # 따로 연 연결이 넣는다 — 이 세션이 쥔 것을 먼저 놓는다.
            _apply(actor, payload, source_name, commit_check)
            db.expire_all()
        out.applied = _changes(full)
        return out
    safe, reserved = hold_reserved(db, soften(db, payload))
    partial = importer.plan(db, safe, source=source_name, keep_reserved=True)
    if not clean(partial) or not _changes(partial):
        # 되돌려도 위험이 남거나, 되돌리면 바뀌는 것이 없다 — 통째로 남긴다.
        out.held = lines_of(full)
        return out
    out.applied = _changes(partial)
    if not apply:
        out.held = lines_of(full)
        return out
    db.commit()
    _apply(actor, safe, source_name, commit_check)
    db.expire_all()
    if reserved:
        out.notes.append(held_reserved_note(reserved))
    # 남은 것만 — 되돌린 변경이 그대로 다시 나온다.
    out.held = lines_of(importer.plan(db, payload, source=source_name, keep_reserved=True))
    return out


# --- 소스 -----------------------------------------------------------------------


def _own_sources(db: Session, row: CoreSubscription) -> list[DataSource]:
    """이 구독의 소스 — 이 구독이 맡은 것과, 같은 허브를 가리키는 형제 코어
    소스(넘겨받는다)."""
    return list(
        db.scalars(
            select(DataSource)
            .where(
                DataSource.kind == "sp_core",
                (DataSource.subscription_id == row.id) | (DataSource.base_url == row.base_url),
            )
            .order_by(DataSource.slug)
        )
    )


def rival(db: Session, object_type: ObjectType, base_url: str) -> DataSource | None:
    """이 타입을 이미 받는 **다른 주소의 형제 코어 소스**(다른 구독의 것 포함) — 있으면 구독은
    그 타입에 소스를 만들지 않는다.

    같은 허브를 IP · 호스트 이름처럼 다르게 적은 소스는 넘겨받지 못한다(같은 주소만 맡는다).
    그대로 두면 구독이 같은 타입에 소스를 하나 더 만들어 둘이 번갈아 받는다 — 같은 허브면 같은
    행을 두 번, 다른 허브면 서로의 값을 덮는다."""
    return db.scalar(
        select(DataSource)
        .where(
            DataSource.kind == "sp_core",
            DataSource.type_id == object_type.id,
            DataSource.base_url != base_url,
        )
        .order_by(DataSource.slug)
    )


def rival_reason(other: DataSource) -> str:
    return (
        f"다른 주소({other.base_url})의 형제 코어 소스 「{other.name}」({other.slug})가 이미 "
        "이 타입을 받아 건너뜁니다 — 한 타입을 소스 둘이 받지 않게. 같은 허브면 그 소스의 "
        "주소를 구독 주소와 같게 수정하세요(다음 확인에서 구독이 맡습니다). 필요 없는 소스면 "
        "삭제하면 구독이 새로 만듭니다(받은 자료는 남고, 처음부터 다시 받습니다)."
    )


def _merge_mapping(
    db: Session, source: DataSource, found: dict[str, Any], object_type: ObjectType
) -> tuple[list[str], list[str], list[str]]:
    """칸 대응을 허브에 맞춘다 — (새로 이은 허브 칸, 뺀 칸, 못 이은 것).

    있는 줄은 그대로 둔다(사람이 다른 칸으로 이어 둔 것이 있을 수 있다). 허브에 생긴 칸은
    대응 제안의 규칙으로 더하고, 허브에서 빠진 칸은 뺀다 — 형제 코어는 거울이라, 대응에 남겨
    두면 허브가 안 보내는 그 칸을 이쪽에서 비운다(값은 남기기로 했다)."""
    fresh, notes = source_services.sibling_mapping(db, found, object_type)
    current = dict(source.mapping or {})
    if current.get("relations"):
        return [], [], []  # 선만 받는 소스 — 객체 칸이 없다.
    columns = [dict(one) for one in current.get("columns") or []]
    remote = {str(one.get("key")) for one in found.get("properties") or []}
    kept = [one for one in columns if one.get("source") in remote | ENVELOPE]
    dropped = [str(one.get("source")) for one in columns if one not in kept]
    have = {one.get("source") for one in kept}
    gained = [one for one in fresh["columns"] if one.get("source") not in have]
    if gained or dropped or not columns:
        source.mapping = {
            **current,
            "external_key": current.get("external_key") or fresh["external_key"],
            "columns": kept + gained,
        }
    return [str(one["source"]) for one in gained if one["source"] in remote], dropped, notes


def _later(added: str, mark: str) -> bool:
    """칸이 생긴 때(`added`)가 받은 자리(`mark`)보다 뒤인가 — 못 읽으면 거짓(다시 받는다)."""
    try:
        first = datetime.fromisoformat(added.replace("Z", "+00:00"))
        second = datetime.fromisoformat(mark.replace("Z", "+00:00"))
    except ValueError:
        return False
    return first > second


@dataclass
class Reconciled:
    """확인 한 번의 결과 — 작업 결과 · 구독의 알림이 이것을 싣는다."""

    applied: int = 0
    held: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    paused: list[str] = field(default_factory=list)
    restarted: list[str] = field(default_factory=list)
    moved: dict[str, int] = field(default_factory=dict)
    """허브가 다른 타입으로 옮긴 원 타입(slug) → 이번에 사용 중지한 이 설치의 객체 수."""
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "definitions_applied": self.applied,
            "held": len(self.held),
            "created": self.created,
            "adopted": self.adopted,
            "resumed": self.resumed,
            "paused": self.paused,
            "restarted": self.restarted,
            "moved": self.moved,
            "notes": capped(self.notes),
        }


def _scope(
    db: Session, row: CoreSubscription, definitions: dict[str, Any] | None
) -> set[str] | None:
    """받는 허브 타입의 slug — 정의가 왔으면 그 타입들, 안 왔으면(지문이 같음) 지난번 것, 정의
    창구가 없는 허브면 범위의 묶음에 든 이 설치의 타입. None 이면 카탈로그 전부."""
    if definitions is not None and definitions.get("changed"):
        return {
            str(one.get("slug"))
            for one in (definitions.get("ontology") or {}).get("types") or []
        }
    if definitions is not None:
        known = (row.state or {}).get("scope")
        return {str(one) for one in known} if isinstance(known, list) else None
    if not row.groups:
        return None
    from app.modules.bundles.export import types_in
    from app.modules.ontology.models import NavGroup

    groups = list(db.scalars(select(NavGroup).where(NavGroup.slug.in_(row.groups))))
    return {one.slug for one in types_in(db, groups)}


def _sources(
    db: Session,
    actor: User | None,
    row: CoreSubscription,
    catalog: dict[str, Any],
    scope: set[str] | None,
    skipped: dict[str, str],
    added: dict[str, str],
    out: Reconciled,
) -> None:
    """공개 타입마다 소스를 두고 맞춘다 · 빠진 타입의 소스는 수신 중지."""
    remote = {
        str(one.get("slug")): one
        for one in catalog.get("types") or []
        if isinstance(one, dict) and (scope is None or str(one.get("slug")) in scope)
    }
    state = dict(row.state or {})
    paused = set(state.get("paused") or [])
    kinds_seen: dict[str, list[str]] = dict(state.get("kinds") or {})
    mine = _own_sources(db, row)
    by_entity = {one.entity_set.strip(): one for one in mine}
    system = row.system or "형제 SP"

    for slug, found in remote.items():
        source = by_entity.get(slug)
        if slug in skipped:
            out.notes.append(f"{slug}: {skipped[slug]}")
            if source is not None:
                _release(source, paused, out)
            continue
        if source is None:
            object_type = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
            if object_type is None:
                out.notes.append(
                    f"{slug}: 이 설치에 정의가 없어 받지 않습니다 — 정의 검토 대기를 "
                    "적용하거나 「정의만」 묶음으로 받은 뒤 다음 확인에서 받습니다."
                )
                continue
            if managed.owner_of(object_type) != row.source_name:
                owner = managed.owner_of(object_type) or "이 설치"
                out.notes.append(
                    f"{slug}: 이 설치의 같은 이름 타입은 {owner} 가 관리해 받지 않습니다"
                    f"(구독의 관리 이름 {row.source_name})."
                )
                continue
            other = rival(db, object_type, row.base_url)
            if other is not None:
                out.notes.append(f"{slug}: {rival_reason(other)}")
                continue
            source = source_services.core_source(
                db,
                actor,
                base_url=row.base_url,
                auth=row.auth or {},
                object_type=object_type,
                found=found,
                system=system,
                source_name=row.source_name,
                interval_minutes=row.interval_minutes,
                via="core_subscription",
                subscription_id=row.id,
            )
            kinds_seen[slug] = sorted(str(one) for one in found.get("relations") or [])
            out.created.append(source.slug)
            continue

        target = db.get(ObjectType, source.type_id)
        if target is None:
            continue
        if managed.owner_of(target) != row.source_name:
            # **넘겨받지 못하는 타입의 소스는 건드리지 않는다** — 하던 대로 받게 둔다.
            owner = managed.owner_of(target) or "이 설치"
            out.notes.append(
                f"{slug}: 기존 소스 {source.slug} 의 타입은 {owner} 가 관리해 넘겨받지 "
                f"않습니다(구독의 관리 이름 {row.source_name}) — 그 소스는 하던 대로 받습니다."
            )
            _release(source, paused, out)
            continue
        if source.subscription_id != row.id:
            source.subscription_id = row.id
            out.adopted.append(source.slug)
        if (source.auth or {}) != (row.auth or {}):
            source.auth = dict(row.auth or {})
        if source.interval_minutes != row.interval_minutes:
            source.interval_minutes = row.interval_minutes
        if source.slug in paused:
            source.is_active = True
            paused.discard(source.slug)
            out.resumed.append(source.slug)
        gained, dropped, unmapped = _merge_mapping(db, source, found, target)
        out.notes.extend(f"{source.slug}: {one}" for one in unmapped)
        if dropped:
            out.notes.append(
                f"{source.slug}: 허브에서 빠진 칸 {', '.join(dropped)} 을(를) 칸 대응에서 "
                "뺐습니다 — 이쪽 값은 남습니다."
            )
        stale = [
            one
            for one in gained
            if not _later(added.get(f"{slug}.{one}", ""), source.since_mark)
        ]
        if stale and source.since_mark:
            source_services.restart(source)
            out.restarted.append(source.slug)
            out.notes.append(
                f"{source.slug}: 새로 이은 칸 {', '.join(stale)} 은(는) 이미 받은 행에도 값이 "
                "있을 수 있어 처음부터 다시 받습니다."
            )
        # 선 — 허브가 연 관계 종류가 늘면 선을 처음부터 다시 받는다(새 종류의 옛 선이 안 온다).
        opened = sorted(str(one) for one in found.get("relations") or [])
        options = dict(source.options or {})
        edges = source_services.opens_edges(found)
        if bool(options.get("relations")) != edges:
            options["relations"] = edges
            source.options = options
        before = kinds_seen.get(slug)
        if (
            before is not None
            and set(opened) - set(before)
            and source.relations_since_mark
            and source.slug not in out.restarted
        ):
            source_services.restart(source, edges_only=True)
            grown = ", ".join(sorted(set(opened) - set(before)))
            out.notes.append(
                f"{source.slug}: 받는 관계 종류가 늘어({grown}) 선을 처음부터 다시 받습니다."
            )
        kinds_seen[slug] = opened

    for source in mine:
        slug = source.entity_set.strip()
        # **넘겨받은 소스만 끈다.** 공개 타입(받는 것) · 받지 않는 타입(건너뛴 것)은 위에서
        # 다뤘고, 구독이 맡은 적 없는 옛 소스는 그대로 둔다 — 거절한 타입의 옛 소스까지 끄면 옛
        # 길도 새 길도 안 도는 타입이 생긴다(운영 37개, 2026-11-18).
        if slug in remote or slug in skipped or source.subscription_id != row.id:
            continue
        if source.is_active:
            source.is_active = False
            paused.add(source.slug)
            out.paused.append(source.slug)
            out.notes.append(
                f"{source.slug}: 허브가 {slug} 의 코어 공개를 해제했거나 구독 범위에서 빠져 "
                "수신을 중지했습니다 — 받은 자료는 남습니다. 다시 공개되면 이어 받습니다."
            )
    state["moved"] = _moved(db, actor, row, remote, dict(state.get("moved") or {}), out)
    state["paused"] = sorted(paused)
    state["kinds"] = kinds_seen
    row.state = state


def _moved(
    db: Session,
    actor: User | None,
    row: CoreSubscription,
    remote: dict[str, dict[str, Any]],
    known: dict[str, Any],
    out: Reconciled,
) -> dict[str, Any]:
    """허브가 **다른 타입으로 옮긴** 타입(ADR 0094) — 처음 본 옮기기는 간 곳의 소스를 처음부터
    다시 받게 하고, 확인마다 간 곳이 같은 식별자로 받아 온 원 타입 쪽 객체를 사용 중지한다.
    돌려주는 것은 구독이 기억할 옮기기(`state.moved` — 원 타입 → 간 곳 · 때 · 처음 본 때).

    허브의 원 타입은 객체가 떠나 비어 있다 — 그 소스는 받을 것이 없고, 그대로 두면 같은 객체가
    이 설치의 두 타입에 산다(간 곳의 소스가 새로 받아 온다). 지우지 않고 **사용 중지**로 두는
    것은 사라진 것을 다루는 길(`services._bury`)과 같다 — 이 설치의 기록이 원 타입의 객체를
    가리킬 수 있다. 간 곳에 **같은 식별자가 온 것만** 중지한다 — 아직 안 온 것(간 곳의 소스가
    실패했거나 다음 차례)은 그대로 둔다. 처음 본 때보다 뒤에 고쳐진 것도 그대로 둔다(옮긴
    뒤 허브의 원 타입에 같은 식별자가 새로 생겨 다시 받은 것이다).

    옮기기는 기억해 둔다 — 원 타입을 허브가 지우거나 공개를 해제해 카탈로그에서 빠져도 남은
    객체를 마저 다룬다. 간 곳을 처음부터 다시 받는 것은 **처음 볼 때 한 번**이다: 옮긴 객체는
    바뀐 때가 옮긴 때로 올라 증분으로도 오지만, 이 판 전의 허브에서 옮긴 것은 안 올랐고 그
    객체의 선(끝 타입만 바뀌었다)은 어느 쪽이든 증분으로 안 온다."""
    now = datetime.now(UTC).isoformat()
    sources = {one.entity_set.strip(): one for one in _own_sources(db, row)}
    for slug, found in remote.items():
        at = str(found.get("moved_at") or "")
        if not at:
            continue
        before = known.get(slug) if isinstance(known.get(slug), dict) else None
        to = str(found.get("moved_to") or "")
        if before is not None and before.get("at") == at:
            continue
        known[slug] = {"to": to, "at": at, "seen": now}
        if not to:
            out.notes.append(
                f"{slug}: 허브가 이 타입의 객체를 공개하지 않는 타입으로 옮겼습니다({at}) "
                "— 더 받을 수 없어 이 설치의 객체는 그대로 둡니다(필요하면 사람이 정리)."
            )
            continue
        target = sources.get(to)
        if target is None:
            out.notes.append(
                f"{slug}: 허브가 이 타입의 객체를 {to}(으)로 옮겼습니다({at}) — {to} 는 이 "
                "구독이 받지 않아(범위 · 건너뜀) 이 설치의 객체는 그대로 둡니다."
            )
            continue
        if target.since_mark and target.slug not in out.restarted:
            source_services.restart(target)
            out.restarted.append(target.slug)
        # 옮긴 객체를 가리키는 선 — 도착 타입이 간 곳으로 바뀌었다(선은 안 바뀌어 증분으로 안
        # 온다). 간 곳을 끝으로 둔 관계 종류를 받는 소스는 선만 처음부터.
        for other_slug, other in remote.items():
            source = sources.get(other_slug)
            if (
                source is None
                or other_slug in (slug, to)
                or not source.relations_since_mark
                or source.slug in out.restarted
            ):
                continue
            if any(
                to in (end.get("types") or [])
                for kind in other.get("relation_kinds") or []
                for end in kind.get("dst") or []
            ):
                source_services.restart(source, edges_only=True)
        out.notes.append(
            f"{slug}: 허브가 이 타입의 객체를 {to}(으)로 옮겼습니다({at}) — {to} 를 처음부터 "
            f"다시 받고, {to} 로 같은 식별자가 온 것은 이 설치의 {slug} 쪽을 사용 중지합니다"
            "(지우지 않음). 이 설치의 기록이 그 객체를 가리키면 "
            f"{to} 의 같은 식별자로 바꾸십시오."
        )
    for slug, info in known.items():
        if not isinstance(info, dict) or not info.get("to"):
            continue
        origin, target = sources.get(slug), sources.get(str(info["to"]))
        if origin is None or target is None:
            continue
        done, left = _retire_moved(db, actor, origin, target, str(info.get("seen") or now))
        if done:
            out.moved[slug] = done
            out.notes.append(
                f"{slug}: 허브가 {info['to']}(으)로 옮긴 객체 {done:,}건을 사용 "
                f"중지했습니다 — 이 설치의 {info['to']} 로 같은 식별자가 왔습니다."
            )
        if done >= MOVED_PER_CHECK:
            out.notes.append(
                f"{slug}: 한 번에 {MOVED_PER_CHECK:,}건까지 사용 중지합니다 — 나머지는 다음 "
                "확인이 잇습니다."
            )
        if left and done:
            out.notes.append(
                f"{slug}: 아직 {info['to']} 로 안 온 {left:,}건은 다음 확인에서 다시 봅니다."
            )
    return known


def _retire_moved(
    db: Session, actor: User | None, origin: DataSource, target: DataSource, seen: str
) -> tuple[int, int]:
    """원 타입의 소스가 받아 온 객체 가운데 간 곳의 소스가 **같은 외부 식별자**로 받아 온 것을
    사용 중지한다 — (중지한 수, 아직 간 곳에 없는 수). 처음 본 때(`seen`) 뒤에 고쳐진 것은
    그대로 둔다(`_moved`).

    ⚠️ **두 소스가 이 설치의 같은 타입에 넣으면 아무것도 안 한다** — 같은 허브 주소를 가리키는
       사람이 만든 형제 코어 소스도 구독이 본다(`_own_sources`). 원 타입과 간 곳이 이 설치의 한
       타입이면 「간 곳에 온 같은 식별자」 가 그 객체 자신이라, 살아 있는 객체를 사용 중지했다
       (2026-11-28 검토). 간 곳의 객체는 늘 **다른 객체**여야 한다(`there.id != 그 객체`)."""
    if origin.type_id == target.type_id:
        return 0, 0
    try:
        since = datetime.fromisoformat(seen)
    except ValueError:
        since = datetime.now(UTC)
    mine, theirs = aliased(ObjectAlias), aliased(ObjectAlias)
    there = aliased(ObjectInstance)
    alive = (
        mine.kind == aliases.source_kind(origin.slug),
        mine.type_id == origin.type_id,
        ObjectInstance.type_id == origin.type_id,
        ObjectInstance.deleted_at.is_(None),
        ObjectInstance.status == "active",
        ObjectInstance.updated_at < since,
    )
    arrived = exists().where(
        theirs.kind == aliases.source_kind(target.slug),
        theirs.type_id == target.type_id,
        theirs.norm == mine.norm,
        theirs.object_id == there.id,
        there.id != ObjectInstance.id,
        there.deleted_at.is_(None),
    )
    rows = list(
        db.execute(
            select(ObjectInstance.id, ObjectInstance.label, ObjectInstance.owner_workspace_id)
            .join(mine, mine.object_id == ObjectInstance.id)
            .where(*alive, arrived)
            .distinct()
            .limit(MOVED_PER_CHECK)
        ).tuples()
    )
    if rows:
        db.execute(
            update(ObjectInstance)
            .where(ObjectInstance.id.in_([one for one, _, _ in rows]))
            .values(status="deprecated")
            .execution_options(synchronize_session=False)
        )
        origin_type = db.get(ObjectType, origin.type_id)
        slug = origin_type.slug if origin_type is not None else origin.entity_set
        audit.record_rows(
            db,
            action="object.update",
            actor=actor,
            target_table="objects",
            rows=[
                (
                    object_id,
                    f"{slug}:{label}",
                    workspace_id,
                    {"status": {"before": "active", "after": "deprecated"}},
                )
                for object_id, label, workspace_id in rows
            ],
            reason=(
                f"{origin.name} — 허브가 객체를 「{target.entity_set}」(으)로 옮겨 사용 "
                "중지로 표시(이 설치의 그 타입에 같은 식별자가 왔다)"
            ),
        )
    left = int(
        db.scalar(
            select(func.count(func.distinct(ObjectInstance.id)))
            .join(mine, mine.object_id == ObjectInstance.id)
            .where(*alive, ~arrived)
        )
        or 0
    )
    return len(rows), left


def _release(source: DataSource, paused: set[str], out: Reconciled) -> None:
    """구독이 맡지 않는 소스로 돌린다 — 구독이 수신 중지해 둔 것이면 다시 활성화한다(옛 길이
    하던 대로 받게)."""
    if source.slug in paused:
        source.is_active = True
        paused.discard(source.slug)
        out.resumed.append(source.slug)
        out.notes.append(
            f"{source.slug}: 구독이 맡지 않는 타입이라 수신 중지를 풀었습니다 — 하던 대로 "
            "받습니다."
        )
    source.subscription_id = None


def _slugs(catalog: dict[str, Any]) -> set[str]:
    return {
        str(one.get("slug")) for one in catalog.get("types") or [] if isinstance(one, dict)
    }


def reconcile(
    db: Session,
    user: User | None,
    row: CoreSubscription,
    *,
    approve: str = "",
    commit_check: Callable[[Session], None] | None = None,
) -> Reconciled:
    """**확인 한 번** — 정의를 맞추고 소스를 맞춘다. 끝에 커밋한다.

    `approve` 는 검토한 정의 검토 대기의 지문 — 주면 그 정의를 **통째로** 적용한다(허브가 그
    사이 바뀌었으면 거절). 실패하면 구독에 까닭을 적고 커밋한 뒤 다시 던진다.

    **한 구독은 한 곳에서만 확인한다** — DB 의 자문 잠금(`singleton.held`). 화면의 「동기화」
    와 타이머의 차례가 함께 돌면 같은 정의를 두 번 넣고 같은 소스를 둘 만든다. 못 잡으면
    아무것도 안 하고 `BUSY` 로 던진다(실패로 적지 않는다)."""
    with singleton.held(f"core-subscription:{row.id}") as locked:
        if locked is None:
            raise Conflict(
                BUSY,
                "다른 작업이 이 코어 구독을 지금 확인하고 있습니다 — 그 작업이 끝난 뒤에 다시 "
                "하세요.",
            )
        return _reconcile(db, user, row, approve=approve, commit_check=commit_check)


def _reconcile(
    db: Session,
    user: User | None,
    row: CoreSubscription,
    *,
    approve: str,
    commit_check: Callable[[Session], None] | None,
) -> Reconciled:
    out = Reconciled()
    try:
        actor = source_services._actor(db, user)
        auth = auth_of(row)
        catalog = _catalog(row.base_url, auth)
        row.system = str(catalog.get("system") or row.system)[:100]
        effective = managed_name(db, row.source_name, row.system, _slugs(catalog))
        if effective != row.source_name:
            out.notes.append(
                f"관리 이름을 「{row.source_name}」 에서 허브의 이름 「{effective}」 로 "
                "맞췄습니다 — 이 설치의 허브 타입이 그 이름으로 관리되고 있습니다."
            )
            row.source_name = effective
            row.definitions_mark = ""  # 정의를 다시 받아 그 이름으로 다시 맞춘다
        known = "" if approve else row.definitions_mark
        if approve and (row.held or {}).get("mark") != approve:
            raise Conflict(
                code("DATASOURCES", 62),
                "검토한 정의가 지금 검토 대기와 다릅니다 — 화면을 새로 읽고 다시 보세요.",
            )
        definitions = _definitions(row.base_url, auth, list(row.groups or []), known)
        prepared: Prepared | None = None
        added: dict[str, str] = dict((row.state or {}).get("added") or {})
        if definitions is None:
            out.notes.append(NO_DEFINITIONS)
        elif definitions.get("changed"):
            if approve and definitions.get("mark") != approve:
                raise Conflict(
                    code("DATASOURCES", 62),
                    "허브의 정의가 그 사이 다시 바뀌었습니다 — 다음 확인이 새 검토 대기를 "
                    "세웁니다. 그것을 보고 적용하세요.",
                )
            prepared = prepare(db, definitions.get("ontology") or {}, row.source_name)
            out.notes.extend(prepared.notes)
            added = {str(k): str(v) for k, v in (definitions.get("added") or {}).items()}
            if approve:
                out.applied = _changes(
                    importer.plan(
                        db, prepared.payload, source=row.source_name, keep_reserved=True
                    )
                )
                db.commit()
                _apply(actor, prepared.payload, row.source_name, commit_check)
                db.expire_all()
            else:
                settled = settle(
                    db, actor, prepared, row.source_name, apply=True, commit_check=commit_check
                )
                out.applied = settled.applied
                out.held = settled.held
                out.notes.extend(settled.notes)
            row.definitions_mark = str(definitions.get("mark") or "")
            row.held = {"mark": row.definitions_mark, "lines": out.held} if out.held else {}
        else:
            out.held = list((row.held or {}).get("lines") or [])
        state = dict(row.state or {})
        scope = _scope(db, row, definitions)
        if definitions is not None and definitions.get("changed"):
            state["scope"] = sorted(scope or [])
            state["added"] = added
        row.state = state
        _sources(
            db,
            actor if user is not None else None,
            row,
            catalog,
            scope,
            prepared.skipped if prepared is not None else dict(state.get("skipped") or {}),
            added,
            out,
        )
        if prepared is not None:
            state = dict(row.state or {})
            state["skipped"] = prepared.skipped
            row.state = state
    except AppError as caught:
        mark_failed(db, row, caught.message)
        raise
    state = dict(row.state or {})
    state["notes"] = capped(out.notes)
    row.state = state
    row.checked_at = datetime.now(UTC)
    row.last_status = "held" if row.held else "ok"
    row.last_error = ""
    db.commit()
    return out


def mark_failed(db: Session, row: CoreSubscription, message: str) -> None:
    """확인이 실패했다 — 하던 것을 물리고 까닭을 구독에 적는다(화면 · 남은 일이 읽는다)."""
    db.rollback()
    db.refresh(row)
    row.checked_at = datetime.now(UTC)
    row.last_status = "failed"
    row.last_error = message[:2000]
    db.commit()


# --- 등록 · 화면 ------------------------------------------------------------------


def due(db: Session, now: datetime | None = None) -> list[CoreSubscription]:
    """타이머가 확인할 차례인 구독 — 데이터 소스의 `due` 와 같은 규칙(여유 5분)."""
    now = now or datetime.now(UTC)
    out: list[CoreSubscription] = []
    for row in db.scalars(
        select(CoreSubscription).where(
            CoreSubscription.is_active.is_(True), CoreSubscription.interval_minutes > 0
        )
    ):
        every = timedelta(minutes=row.interval_minutes)
        if row.checked_at is None or row.checked_at + every - source_services.DUE_GRACE <= now:
            out.append(row)
    return out


def find(db: Session, subscription_id: str) -> CoreSubscription:
    try:
        key = uuid.UUID(subscription_id)
    except ValueError:
        key = None
    found = db.get(CoreSubscription, key) if key is not None else None
    if found is None:
        raise NotFound(
            code("DATASOURCES", 63), f"코어 구독을 찾을 수 없습니다: {subscription_id}"
        )
    return found


def out_of(db: Session, row: CoreSubscription) -> CoreSubscriptionOut:
    auth = row.auth or {}
    paused = set((row.state or {}).get("paused") or [])
    held = row.held or {}
    types = {
        one.id: one.slug
        for one in db.scalars(
            select(ObjectType).where(
                ObjectType.id.in_(
                    select(DataSource.type_id).where(DataSource.subscription_id == row.id)
                )
            )
        )
    }
    return CoreSubscriptionOut(
        id=row.id,
        base_url=row.base_url,
        system=row.system,
        source_name=row.source_name,
        groups=list(row.groups or []),
        interval_minutes=row.interval_minutes,
        is_active=row.is_active,
        auth_kind=str(auth.get("kind") or "bearer"),
        auth_user=str(auth.get("user") or ""),
        has_secret=bool(auth.get("secret")),
        checked_at=row.checked_at,
        last_status=row.last_status,
        last_error=row.last_error,
        held=(
            CoreHeldOut(mark=str(held.get("mark") or ""), lines=list(held.get("lines") or []))
            if held.get("lines")
            else None
        ),
        notes=list((row.state or {}).get("notes") or []),
        sources=[
            CoreSubscriptionSourceOut(
                slug=one.slug,
                name=one.name,
                type_slug=types.get(one.type_id, ""),
                is_active=one.is_active,
                paused=one.slug in paused,
                last_status=one.last_status,
                last_success_at=one.last_success_at,
            )
            for one in db.scalars(
                select(DataSource)
                .where(DataSource.subscription_id == row.id)
                .order_by(DataSource.slug)
            )
        ],
        created_at=row.created_at,
    )


def plan(
    db: Session, user: User, payload: CoreSubscriptionRequest, *, base_url: str
) -> CoreSubscriptionPlanOut:
    """**무엇을 할지** — 허브의 카탈로그 · 정의를 읽어 이 설치와 맞춰 본다. 아무것도 저장하지
    않는다."""
    if db.scalar(select(CoreSubscription).where(CoreSubscription.base_url == base_url)):
        raise Conflict(
            code("DATASOURCES", 64),
            f"이 주소의 코어 구독이 이미 있습니다: {base_url} — 그 구독을 수정하세요.",
        )
    auth = odata.Auth(
        kind=payload.auth_kind, user=payload.auth_user.strip(), secret=payload.auth_secret
    )
    catalog = _catalog(base_url, auth)
    groups = [one.strip() for one in payload.groups if one.strip()]
    definitions = _definitions(base_url, auth, groups, "")
    system = str(catalog.get("system") or "")
    name = managed_name(db, payload.source_name, system, _slugs(catalog))
    out = CoreSubscriptionPlanOut(
        system=system,
        source_name=name,
        applied=False,
        definitions=definitions is not None,
        groups=[
            CoreGroupChoiceOut(
                slug=str(one.get("slug")),
                label=str(one.get("label") or one.get("slug")),
                parent_slug=one.get("parent_slug"),
            )
            for one in (definitions or {}).get("groups") or []
        ],
        types=[],
        held=[],
        notes=[],
    )
    if name != payload.source_name:
        out.notes.append(
            f"관리 이름을 「{payload.source_name}」 대신 허브의 이름 「{name}」 로 씁니다 — "
            "이 설치의 허브 타입이 그 이름으로 관리되고 있어 그대로 넘겨받습니다."
        )
    prepared: Prepared | None = None
    if definitions is not None:
        prepared = prepare(db, definitions.get("ontology") or {}, name)
        out.notes.extend(prepared.notes)
        settled = settle(db, source_services._actor(db, user), prepared, name, apply=False)
        out.held = settled.held
        scope: set[str] | None = {
            str(one.get("slug"))
            for one in (definitions.get("ontology") or {}).get("types") or []
        }
    else:
        out.notes.append(NO_DEFINITIONS)
        if groups:
            from app.modules.bundles.export import types_in
            from app.modules.ontology.models import NavGroup

            rows = list(db.scalars(select(NavGroup).where(NavGroup.slug.in_(groups))))
            scope = {one.slug for one in types_in(db, rows)}
        else:
            scope = None
    taken = {
        one.entity_set.strip(): one
        for one in db.scalars(
            select(DataSource).where(
                DataSource.kind == "sp_core", DataSource.base_url == base_url
            )
        )
    }
    shown = {
        str(one.get("slug"))
        for one in catalog.get("types") or []
        if isinstance(one, dict) and (scope is None or str(one.get("slug")) in scope)
    }
    outside = sorted(one.slug for slug, one in taken.items() if slug not in shown)
    if outside:
        out.notes.append(
            "이 허브를 가리키는 소스 중 공개되지 않았거나 범위 밖인 것은 구독이 맡지 않고 "
            "그대로 둡니다(하던 대로 받습니다): " + ", ".join(outside)
        )
    for one in catalog.get("types") or []:
        if not isinstance(one, dict):
            continue
        slug = str(one.get("slug"))
        if scope is not None and slug not in scope:
            continue
        item = CoreSubscriptionTypeOut(
            type_slug=slug,
            type_label=str(one.get("label") or slug),
            action="create",
            count=int(one.get("count") or 0),
        )
        out.types.append(item)
        local = db.scalar(select(ObjectType).where(ObjectType.slug == slug))
        existing = taken.get(slug)
        target = db.get(ObjectType, existing.type_id) if existing is not None else local
        if prepared is not None and slug in prepared.skipped:
            item.action = "skip"
            item.reason = prepared.skipped[slug] + (
                f" 기존 소스 {existing.slug} 는 하던 대로 받습니다." if existing else ""
            )
        elif existing is not None and target is not None:
            if managed.owner_of(target) == name:
                item.action = "adopt"
            else:
                item.action = "skip"
                owner = managed.owner_of(target) or "이 설치"
                item.reason = (
                    f"이 설치의 같은 이름 타입은 {owner} 가 관리합니다(구독의 관리 이름 "
                    f"{name}) — 기존 소스는 하던 대로 받습니다."
                )
            item.source_slug = existing.slug
        elif local is None:
            if prepared is None:
                item.action = "skip"
                item.reason = "이 설치에 정의가 없습니다 — 「정의만」 묶음으로 먼저 받으세요."
            else:
                item.action = "define"
        elif managed.owner_of(local) != name:
            item.action = "skip"
            owner = managed.owner_of(local) or "이 설치"
            item.reason = (
                f"이 설치의 같은 이름 타입은 {owner} 가 관리합니다(구독의 관리 이름 {name})."
            )
        elif (other := rival(db, local, base_url)) is not None:
            item.action = "skip"
            item.reason = rival_reason(other)
            item.source_slug = other.slug
    return out


def create(
    db: Session,
    user: User,
    payload: CoreSubscriptionRequest,
    *,
    base_url: str,
    source_name: str | None = None,
) -> CoreSubscription:
    """`source_name` — 미리 본 관리 이름(`plan` 이 허브의 이름으로 맞췄을 수 있다)."""
    row = CoreSubscription(
        base_url=base_url,
        auth={
            "kind": payload.auth_kind,
            "user": payload.auth_user.strip(),
            "secret": payload.auth_secret,
        },
        source_name=source_name or payload.source_name,
        groups=[one.strip() for one in payload.groups if one.strip()],
        interval_minutes=payload.interval_minutes,
        is_active=True,
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="core_subscription.create",
        actor=user,
        target_table="core_subscriptions",
        target_id=row.id,
        target_label=base_url,
        changes={
            "base_url": base_url,
            "source_name": row.source_name,
            "groups": row.groups,
            "interval_minutes": row.interval_minutes,
        },
    )
    return row
