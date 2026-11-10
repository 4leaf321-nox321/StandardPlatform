"""글 안의 사진 표시 — **첨부의 자리와 폭을 글 한 줄로** 적는다.

    ![캡션](photo:<첨부 id>?w=<폭>)

글 사이에 사진 · 그림을 세우는 두 자리가 같은 표기를 쓴다:

- 자주검증의 글 칸(ADR 0044) — 폭은 칸 너비의 %.
- 문서 속성의 그림(ADR 0056) — 폭은 본문 너비의 %.

표기를 한 곳에 둔 이유: 처음에는 자주검증 확장 안에 있었다. 문서가 같은 표기를 쓰려면 코어가
확장을 import 해야 하는데, 그러면 확장을 끈 설치에서 코어가 안 뜬다(AGENTS 「확장 모듈」). 두
벌로 적으면 한쪽만 고쳐져 AI 가 쓴 표시가 한 화면에서만 보인다.

- 제 줄 하나를 통째로 차지할 때가 표시다(앞뒤의 빈칸은 봐준다). 자주검증은 이 규칙으로 줄을
  옮기고 지운다. 문서는 마크다운 그림이라 글 사이에 있어도 그린다(`readable` 이 둘 다 걷는다).
- 폭은 10 ~ 100(정수). 비우면 부르는 쪽의 기본값.
- 캡션(`[...]`)은 사람이 읽으라고 둔 것이다 — 비워도 된다.
"""

from __future__ import annotations

import re
import uuid

DEFAULT_WIDTH = 50
MIN_WIDTH = 10
MAX_WIDTH = 100

#: 한 줄 전체가 표시일 때만 — 앞뒤의 빈칸은 봐준다.
MARKER = re.compile(
    r"^[ \t]*!\[(?P<alt>[^\]\n]*)\]"
    r"\(photo:(?P<id>[0-9a-fA-F-]{36})(?:\?w=(?P<w>\d{1,3}))?\)[ \t]*$"
)
#: 글 어디에 있든 — 뜻으로 찾기 · 요약처럼 글을 읽을거리로 바꾸는 자리가 쓴다.
ANYWHERE = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\(photo:[0-9a-fA-F-]{36}(?:\?w=\d{1,3})?\)")


def marker(photo_id: uuid.UUID | str, width: int = DEFAULT_WIDTH, alt: str = "") -> str:
    """표시 한 줄 — 폭은 10 ~ 100 으로 묶는다."""
    return f"![{alt}](photo:{photo_id}?w={clamp(width)})"


def clamp(width: int) -> int:
    return max(MIN_WIDTH, min(MAX_WIDTH, int(width)))


def marker_ids(text: str) -> list[uuid.UUID]:
    """글 안의 표시가 가리키는 첨부 — 나온 차례대로."""
    out: list[uuid.UUID] = []
    for line in text.split("\n"):
        found = MARKER.match(line)
        if found:
            try:
                out.append(uuid.UUID(found.group("id")))
            except ValueError:
                continue
    return out


def without_photos(text: str, ids: set[uuid.UUID]) -> str:
    """그 사진들의 표시 줄을 뺀다(사진을 뗐을 때) — 다른 줄은 그대로."""
    kept = []
    for line in text.split("\n"):
        found = MARKER.match(line)
        if found:
            try:
                if uuid.UUID(found.group("id")) in ids:
                    continue
            except ValueError:
                pass
        kept.append(line)
    return "\n".join(kept)


def plain(text: str) -> str:
    """표시 줄을 걷어 낸 글 — 감사의 이름표 · 요약처럼 글을 줄여 보이는 자리."""
    return "\n".join(line for line in text.split("\n") if not MARKER.match(line)).strip()


def readable(text: str) -> str:
    """표시를 **읽을거리로** — 캡션이 있으면 「그림: 캡션」, 없으면 걷는다. 글 사이의 것도.

    뜻으로 찾기의 조각에 첨부 id(uuid)가 그대로 들어가면 뜻이 없는 글자 36개가 거리를 흐린다.
    캡션은 남긴다 — 「하우징 단면」 으로 찾는 사람이 있다."""

    def spoken(found: re.Match[str]) -> str:
        alt = found.group("alt").strip()
        return f"그림: {alt}" if alt else ""

    return ANYWHERE.sub(spoken, text)
