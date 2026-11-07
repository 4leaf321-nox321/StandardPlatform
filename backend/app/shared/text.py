"""사람이 적은 짧은 문자열을 다루는 규칙 — **한 곳에서.**

부서 이름, 계정 표시 이름, 그리고 앞으로 도메인이 들일 모든 "사람이 타이핑하는
이름" 이 같은 문제를 갖는다: 눈에 같아 보이는데 DB 는 다르게 보는 값들이다.
`재료팀` 과 `재료팀 `(뒤 공백), `UTM-01` 과 `utm-01`. 비교키를 만드는 규칙이
모듈마다 다르면 어떤 화면은 중복을 막고 어떤 화면은 못 막는다.
"""

from __future__ import annotations

import re
import unicodedata

_SPACES = re.compile(r"\s+")


def clean(raw: str) -> str:
    """보여 줄 값. 앞뒤 공백과 연속 공백만 정리하고 표기는 그대로 둔다."""
    return _SPACES.sub(" ", raw.strip())


def compare_key(raw: str) -> str:
    """유일성·조회에 쓰는 비교키.

    **구두점은 안 지운다.** `포스코(주)` 가 계열사 구분일 수 있다 — 그런 것을
    묶는 것은 병합 후보 탐지의 몫이고, 거기서는 **사람에게 묻는다.**

    NFKC 로 모으는 이유: 전각 `ＡＳＴＭ` 과 반각 `ASTM` 이 다른 값으로 들어오면,
    목록에 둘이 나란히 서고 아무도 그 둘이 같다는 것을 모른다.
    """
    return unicodedata.normalize("NFKC", clean(raw)).casefold()


#: 찾을 말을 낱말로 나눌 때 이만큼까지만 본다 — 문장을 통째로 붙여 넣으면 낱말마다 조건이
#: 붙어 질의가 끝없이 길어진다. 넘으면 **두 글자 이상 낱말을 먼저 남긴다**(좁히는 것은
#: 그쪽이다).
MAX_WORDS = 8


def search_words(term: str | None) -> list[str]:
    """찾을 말을 낱말로 — 띄어 쓴 낱말이 **모두 든 것**을 찾는다(ADR 0057).

    「S24 후면 커버」 로 「갤럭시 S24 Ultra 후면 커버」 가 걸린다. 한 덩어리
    (`%S24 후면 커버%`)로 찾던 때는 낱말 차례나 사이 글자 하나만 달라도 0건이었고, 0건을 받은
    쪽(사람이든 AI 든)은 「없다」 로 읽었다. **낱말이 하나면 예전과 같은 질의다.**

    - 대소문자만 다른 낱말은 하나로 본다(찾기는 대소문자를 안 가린다).
    - 두 글자 이상인 낱말이 하나도 없으면(「a b」) 통째로 한 낱말로 둔다 — 한 글자 낱말만으로는
      거의 모든 행이 걸린다.
    - `MAX_WORDS` 를 넘으면 두 글자 이상 낱말부터 남기고, 남는 자리만 한 글자 낱말로 채운다 —
      앞에 한 글자 낱말이 여럿 와도 식별자가 버려지지 않게(2026-11-07 검토). 차례는 지킨다.
    """
    whole = (term or "").strip()
    seen: set[str] = set()
    words: list[str] = []
    for one in whole.split():
        folded = one.casefold()
        if folded not in seen:
            seen.add(folded)
            words.append(one)
    longer = [one for one in words if len(one) >= 2]
    if not longer:
        return [whole] if whole else []
    if len(words) > MAX_WORDS:
        short = [one for one in words if len(one) < 2]
        kept = set(longer[:MAX_WORDS] + short[: max(0, MAX_WORDS - len(longer))])
        words = [one for one in words if one in kept]
    return words


def like_escape(text: str) -> str:
    """`LIKE` 의 와일드카드(`%` · `_`)와 이스케이프 글자를 글자 그대로 — `escape="\\\\"` 와
    함께 쓴다. 안 그러면 「50%」 가 「50 뒤에 아무거나」 로, 「A_1」 이 「A 와 1 사이 아무
    글자」 로 읽혀 엉뚱한 것이 걸린다."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
