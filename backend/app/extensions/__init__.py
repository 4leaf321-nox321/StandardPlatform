"""확장 모듈 — **인스턴스마다 켜고 끄는 기능.**

번들 하나로 여러 플랫폼(허브 · 그룹 쌍둥이들)을 띄우는데, 어떤 기능은 한 인스턴스에만 있다.
그것을 여기 `app/extensions/<이름>/` 에 두고, 설치의 `.env` 가 `EXTENSIONS=hub,bom` 으로 켠다.

## 규칙 하나

**확장은 코어(`app.modules` · `app.shared`)를 부르되, 코어는 확장을 모른다.** 코어가 확장을
import 하는 순간 「끄면 안 뜨는 코어」 가 되고, 그 사실은 확장을 안 켠 인스턴스에서만 드러난다.
구조 시험이 이것을 지킨다(`tests/architecture/test_boundaries.py`).

## 확장의 모양

    app/extensions/<이름>/__init__.py   register(router: APIRouter) -> None  (필수)
    app/extensions/<이름>/models.py     ORM 모델 — all_models.py 에도 적는다
                                        (마이그레이션은 늘 전부 돈다)
    frontend/src/extensions/<이름>/     화면 쪽 짝 — 메뉴 · 페이지
                                        (frontend/src/extensions/index.ts)

`register` 는 라우터를 받아 자기 라우터를 붙이고, 필요하면 공통 화면의 훅
(`shared/extensions`)과 PAT 범위(`shared/scopes`)를 연다. **안 켠 인스턴스에서는 그 확장의
표가 비어 있을 뿐** 다른 것은 같다 — 마이그레이션을 확장마다 가르지 않는 이유다.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from fastapi import APIRouter


class Extension(ModuleType):
    """`load()` 가 돌려주는 모듈의 모양 — 타입 검사용."""

    def register(self, router: APIRouter) -> None: ...


def load(name: str) -> Extension:
    """이름으로 확장을 찾는다. 없거나 `register` 가 없으면 **기동에서 멈춘다** — `.env` 의
    오타가 「메뉴가 안 보이는」 조용한 고장으로 남지 않게."""
    if not name.isidentifier() or name.startswith("_"):
        raise RuntimeError(f"EXTENSIONS 의 이름이 이상합니다: {name!r}")
    try:
        module = importlib.import_module(f"app.extensions.{name}")
    except ModuleNotFoundError as missing:
        if missing.name and missing.name.startswith(f"app.extensions.{name}"):
            raise RuntimeError(
                f"확장 {name!r} 이 없습니다 (app/extensions/{name}/). "
                ".env 의 EXTENSIONS 를 확인하세요."
            ) from missing
        raise
    if not callable(getattr(module, "register", None)):
        raise RuntimeError(f"확장 {name!r} 에 register(router) 가 없습니다.")
    return module  # type: ignore[return-value]
