"""찾을 말을 낱말로(ADR 0057) — 나누기 규칙만. 질의는 `tests/api/test_search.py` 가 본다."""

from __future__ import annotations

from app.shared.text import MAX_WORDS, like_escape, search_words


def test_낱말로_나누고_대소문자만_다른_것은_하나로_본다() -> None:
    assert search_words("  S24   후면\t커버 ") == ["S24", "후면", "커버"]
    assert search_words("SM sm Sm X0") == ["SM", "X0"]  # 찾기는 대소문자를 안 가린다
    assert search_words("BT-2041") == ["BT-2041"]  # 낱말 하나 — 예전과 같은 질의
    assert search_words("") == [] and search_words(None) == [] and search_words("   ") == []


def test_두_글자_이상_낱말이_없으면_통째로_한_낱말이다() -> None:
    """한 글자 낱말만으로는 거의 모든 행이 걸린다."""
    assert search_words("a b") == ["a b"]


def test_넘치면_두_글자_이상_낱말을_먼저_남기고_차례는_지킨다() -> None:
    """앞에 한 글자 낱말이 여럿 와도 좁히는 낱말(식별자)이 버려지지 않는다(2026-11-07 검토)."""
    words = search_words("a b c d e f g h SM-X02993")
    assert len(words) == MAX_WORDS and words[-1] == "SM-X02993"
    assert words == ["a", "b", "c", "d", "e", "f", "g", "SM-X02993"]
    many = " ".join(f"w{n}" for n in range(12))
    assert search_words(many) == [f"w{n}" for n in range(MAX_WORDS)]


def test_와일드카드는_글자_그대로() -> None:
    assert like_escape("50%_\\") == "50\\%\\_\\\\"
