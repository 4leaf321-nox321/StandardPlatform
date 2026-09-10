"""플랫폼 식별자가 **한 자리에서 나오는가.**

포크할 때 이름을 바꾸는 자리가 여럿이면 언젠가 하나가 안 바뀐다. 그리고 안 바뀐
하나는 전부 **조용한 사고**로 나타난다 — 오류가 안 나고 잘못된 데이터가 보인다.
그래서 「따로 안 적혀 있다」 를 시험이 지킨다.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.branding import APP_SLUG
from app.config import Settings
from app.modules.auth import security

BACKEND = Path(__file__).resolve().parents[2]


def test_DB_이름이_APP_SLUG_에서_나온다() -> None:
    """두 플랫폼이 같은 DB 를 보면 **오류 없이 남의 users 표를 읽는다** — 공통
    틀에서 나온 표는 이름이 같기 때문이다."""
    # .env 가 통째로 덮으므로 기본값(모델 필드)을 본다.
    default = str(Settings.model_fields["database_url"].default)
    assert default.rsplit("/", 1)[-1] == APP_SLUG


def test_쿠키_이름이_APP_SLUG_에서_나온다() -> None:
    """쿠키는 포트를 구분하지 않는다. 같은 이름이면 한쪽 로그인이 다른 쪽 세션을
    덮어써서 번갈아 로그아웃되고, 그 원인은 코드 어디에도 없다."""
    default = str(Settings.model_fields["refresh_cookie_name"].default)
    assert default == f"{APP_SLUG}_refresh"


def test_토큰_표식이_APP_SLUG_에서_나온다() -> None:
    """같으면 옆 플랫폼 토큰을 붙여 넣었을 때 「형식은 맞는데 인증이 안 되는」
    상태가 되는데, 그것은 오타와 구별되지 않는다."""
    assert f"{APP_SLUG}_pat_" == security.PAT_PREFIX
    assert security.new_pat()[0].startswith(f"{APP_SLUG}_pat_")


def test_표시용_앞자리가_컬럼에_들어간다() -> None:
    """**slug 가 길어지면 이 값도 길어진다.** 16자로 잡았다가 slug 를
    `standardplatform` 으로 두는 순간 넘쳤고, 그 오류는 토큰을 발급하는 자리에서
    났다 — 거기서는 컬럼 길이가 원인이라는 것이 안 보인다.
    """
    from sqlalchemy import String

    from app.modules.auth.models import PersonalAccessToken

    # isinstance 로 좁힌다 — 컬럼 타입은 제네릭이라 그냥 .length 를 읽으면 mypy 가
    # 막는다. 그리고 이 단언 자체가 「여기는 길이가 있는 타입이어야 한다」 를 말한다.
    column_type = PersonalAccessToken.__table__.c.prefix.type
    assert isinstance(column_type, String)
    assert column_type.length is not None
    assert len(security.new_pat()[1]) <= column_type.length


def test_시험_DB_는_이름에서_파생된다() -> None:
    """접속 정보를 두 곳에 적으면 한쪽만 고쳐지고, 그때 시험이 어느 DB 에서
    도는지 아무도 모른다."""
    from app.database import engine

    name = engine.url.database or ""
    # conftest 가 개발 이름에 _test 를 붙여 만든 것이어야 한다.
    assert name.endswith("_test"), name
    assert not name.endswith("__test"), f"이름이 두 번 붙었습니다: {name}"


def test_APP_SLUG_는_소문자_한_덩어리다() -> None:
    """`matnexus` · `testscope` · `crossaxtf` — 옆 플랫폼들이 이미 그 규약이고,
    DB 이름을 눈으로 대조하는 자리가 있어서 한 줄이 튀면 그때마다 다시 확인하게
    된다. 그리고 대문자가 섞이면 Postgres 가 따옴표를 요구해 스크립트가 갈린다.

    **32자로 막는다.** 이 값에서 DB 이름(`<slug>_test`)과 토큰 표식이 나오는데,
    둘 다 길이 제한이 있는 자리로 들어간다 — Postgres 식별자는 63바이트고 토큰
    앞자리 컬럼은 64자다. 넘치면 **토큰을 발급하는 자리**에서 터지는데, 거기서는
    이름이 길어서라는 것이 안 보인다.
    """
    assert re.fullmatch(r"[a-z][a-z0-9]{0,31}", APP_SLUG), APP_SLUG


def test_프론트와_백엔드의_APP_SLUG_가_같다() -> None:
    """저쪽은 이 값으로 localStorage 키를 만든다. 갈라지면 한쪽만 바뀐 채로
    **아무 증상 없이** 돌다가, 두 플랫폼을 같은 출처에 얹는 날 드러난다."""
    branding_ts = BACKEND.parent / "frontend" / "src" / "shared" / "branding.ts"
    if not branding_ts.exists():  # pragma: no cover - 백엔드만 받은 설치
        return

    found = re.search(r"APP_SLUG\s*=\s*'([^']+)'", branding_ts.read_text(encoding="utf-8"))
    assert found, "frontend/src/shared/branding.ts 에 APP_SLUG 가 없습니다"
    assert found.group(1) == APP_SLUG, (
        f"프론트는 {found.group(1)!r}, 백엔드는 {APP_SLUG!r} 입니다"
    )
