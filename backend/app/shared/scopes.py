"""기계 자격(PAT)의 범위 — **표에 없는 경로는 못 쓴다.**

사람 세션에는 범위를 안 건다. 그 사람의 권한이 이미 한계이고, 둘을 같은 축으로
섞으면 화면에서 되던 일이 이유 없이 막힌다. 범위는 **기계 자격에만 있는 개념**이다.

## 왜 레지스트리인가

공통 틀은 도메인 경로를 모른다. 그런데 판정은 한 곳(`shared/auth.py`)에서 해야
하므로, 도메인이 자기 경로와 범위를 **등록**한다. 조립은 `app/main.py` 가 한다 —
모듈이 서로를 import 하지 않게 하려면 조립 지점이 하나여야 한다.

## 기본은 읽기뿐이다

`register_scope` 로 더하지 않은 범위는 존재하지 않고, `register_write_scope` 로
열지 않은 경로는 어떤 범위로도 못 고친다. **모르는 것은 막는다** 가 맞는 기본값이다:
새 엔드포인트가 생길 때마다 자동으로 열리면, 그것을 알아채는 사람이 아무도 없다.
"""

from __future__ import annotations

#: 언제나 있는 범위. 읽기는 하나로 묶는다 — 읽기를 경로마다 쪼개면 토큰 하나를
#: 만들 때마다 목록을 다 훑어야 하고, 그러면 사람은 전부 켜 버린다.
READ = "read"

#: 이 설치가 아는 범위 전부. 발급 요청에 없는 범위가 오면 거절한다.
_known: list[str] = [READ]

#: (경로 앞머리, 그 아래 **쓰기**에 필요한 범위).
_write: list[tuple[str, str]] = []

#: POST 지만 **읽기인** 경로. 본문에 물음을 싣거나(검색) 결과를 만들어 주기만 하는
#: 자리(내보내기)다.
#:
#: 이걸 빼먹으면 읽기 전용 토큰이 검색을 못 한다 — 그리고 검색은 대개 그 플랫폼이
#: 존재하는 이유다. 도메인이 `/api/search/` 같은 것을 만들면 여기 등록한다.
_read_only_posts: list[str] = []

#: POST 지만 읽기인 경로 — **중간에 이름이 끼는 것**(`/api/objects/<타입>/export`).
#: 앞머리로는 못 적는다: `/api/objects/` 로 열면 그 아래 쓰기까지 통째로 열린다.
_read_only_post_suffixes: list[str] = []

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def register_scope(scope: str) -> None:
    """이 설치가 아는 범위에 하나 더한다. 같은 것을 두 번 넣어도 안전하다."""
    if scope not in _known:
        _known.append(scope)


def register_write_scope(path_prefix: str, scope: str) -> None:
    """이 경로 아래를 고치려면 이 범위가 있어야 한다.

    범위 자체도 함께 등록한다 — 열어 놓고 발급할 수 없는 상태를 안 만든다.
    """
    register_scope(scope)
    if (path_prefix, scope) not in _write:
        _write.append((path_prefix, scope))


def register_read_only_post(path_prefix: str) -> None:
    """POST 지만 읽기인 경로. 읽기 범위로 통과시킨다."""
    if path_prefix not in _read_only_posts:
        _read_only_posts.append(path_prefix)


def register_read_only_post_suffix(suffix: str) -> None:
    """끝이 이런 POST 는 읽기다 — `/api/objects/<타입>/export` 처럼 중간에 이름이 끼는 자리.

    **내보내기는 읽기다.** 작업 한 줄을 남기므로 표로는 쓰기지만, 사람이 하는 일은 「가진
    것을 파일로 받기」 다. 읽기 토큰으로 못 하게 두면 「허브에서 정의를 받아 가는」 길이
    쓰기 권한을 요구하게 되고, 그러면 받아만 가면 되는 쪽에 쓰기 토큰을 주게 된다.
    """
    if suffix not in _read_only_post_suffixes:
        _read_only_post_suffixes.append(suffix)


def known_scopes() -> tuple[str, ...]:
    return tuple(_known)


def is_reading(method: str, path: str) -> bool:
    if method in SAFE_METHODS:
        return True
    if any(path.startswith(prefix) for prefix in _read_only_posts):
        return True
    return any(path.endswith(suffix) for suffix in _read_only_post_suffixes)


def needed_scope(path: str) -> str | None:
    """이 경로의 쓰기에 필요한 범위. 없으면 None (= 아무 범위로도 못 쓴다).

    **긴 앞머리부터 본다.** `/api/equipment-series` 가 `/api/equipment` 보다 먼저
    걸려야 한다 — 아니면 계열 수정이 장비 범위로 통과한다. 이 함정은 등록 순서가
    아니라 길이로만 막을 수 있다.
    """
    for prefix, scope in sorted(_write, key=lambda one: -len(one[0])):
        if path.startswith(prefix):
            return scope
    return None
