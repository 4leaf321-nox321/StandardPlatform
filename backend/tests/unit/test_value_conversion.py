"""값 변환 — 세션 없이 본다(ADR 0007).

종류 변경 · 일괄 입력 · 조건이 이 규칙을 함께 쓴다. 여기서 틀리면 그 길들이 **같이** 틀린다.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.ontology import conversion
from app.modules.ontology.conversion import Unconvertible
from app.modules.ontology.models import PropertyDef


def _def(data_type: str, **kw: Any) -> PropertyDef:
    return PropertyDef(key="k", label="칸", data_type=data_type, multi=False, **kw)


def _ok(target: PropertyDef, raw: Any, mapping: dict[str, str | None] | None = None) -> Any:
    out = conversion.convert_stored(target, raw, mapping)
    assert out.failures == [], out.failures
    return None if out.remove else out.value


def _fails(target: PropertyDef, raw: Any) -> str:
    out = conversion.convert_stored(target, raw)
    assert out.failures, f"변환돼 버렸다: {out.value!r}"
    return out.failures[0][1]


def test_숫자는_쉼표를_읽고_단위_nan_inf_밑줄은_거절한다() -> None:
    number = _def("number")
    assert _ok(number, "1,234") == 1234
    assert _ok(number, " -12.5 ") == -12.5
    assert _ok(number, "1e3") == 1000
    assert _ok(number, ".5") == 0.5
    for text in ("12 kg", "nan", "inf", "1_000", "1,23", "1,2345", ""):
        if text:
            assert "숫자" in _fails(number, text)
    # 긴 정수가 float 을 거치며 깎이지 않는다 — 화면 정밀도는 경고로만 센다.
    big = conversion.convert_stored(number, "123456789012345678901")
    assert big.value == 123456789012345678901 and "precision" in big.lossy


def test_숫자의_범위와_자릿수는_정의가_본다() -> None:
    bounded = _def("number", min_value=0, max_value=10, decimals=1)
    assert _ok(bounded, "9.5") == 9.5
    assert "작을 수 없습니다" in _fails(bounded, "-1")
    assert "클 수 없습니다" in _fails(bounded, "11")
    assert "소수점" in _fails(bounded, "1.25")


def test_날짜는_여러_표기를_ISO_로_시각은_버리면_손실로_센다() -> None:
    day = _def("date")
    for text in (
        "2024-03-05",
        "2024.3.5",
        "2024. 3. 5.",
        "2024/03/05",
        "20240305",
        "2024년 3월 5일",
    ):
        assert _ok(day, text) == "2024-03-05", text
    assert "없는 날짜" in _fails(day, "2024-02-30")
    assert _fails(day, "2024-03/05")  # 구분자가 섞이면 날짜가 아니다
    moment = conversion.convert_stored(day, "2024-03-05T13:05")
    assert moment.value == "2024-03-05" and moment.lossy == ["time"]
    assert conversion.convert_stored(day, "2024-03-05T00:00").lossy == []


def test_시각은_시간대_없이_적고_시간대가_붙으면_변환하지_않는다() -> None:
    moment = _def("datetime")
    assert _ok(moment, "2024-03-05") == "2024-03-05T00:00"
    assert _ok(moment, "2024-03-05 13:05") == "2024-03-05T13:05"
    assert _ok(moment, "2024.3.5 9:05:07") == "2024-03-05T09:05:07"
    assert "시간대" in _fails(moment, "2024-03-05T13:05+09:00")
    assert "시간대" in _fails(moment, "2024-03-05T13:05Z")


def test_참거짓은_한_어휘다() -> None:
    flag = _def("bool")
    for text in ("예", "Y", "true", "1", "o", "참"):
        assert _ok(flag, text) is True, text
    for text in ("아니오", "아니요", "N", "false", "0", "x", "거짓"):
        assert _ok(flag, text) is False, text
    assert "참/거짓" in _fails(flag, "아마도")


def test_고를_값은_정확히_맞아야_하고_대체_값으로_맞춘다() -> None:
    country = _def("enum", enum_options=["한국", "미국"])
    assert _ok(country, " 한국 ") == "한국"
    assert _fails(country, "Korea")
    assert _ok(country, "Korea", {"Korea": "한국"}) == "한국"
    # 값 삭제 — 키를 지운다.
    assert conversion.convert_stored(country, "Korea", {"Korea": None}).remove
    # 대체 값도 같은 규칙으로 검사한다.
    wrong = conversion.convert_stored(country, "Korea", {"Korea": "코리아"})
    assert wrong.failures and "대체 값" in wrong.failures[0][1]


def test_글로_바꾸면_정해진_글자가_된다() -> None:
    text = _def("text")
    assert _ok(text, 3) == "3"
    assert _ok(text, 3.0) == "3"
    assert _ok(text, 0.1) == "0.1"
    assert _ok(text, True) == "예"
    assert _ok(text, "2024-03-05") == "2024-03-05"
    patterned = _def("text", pattern=r"^[A-Z]+$")
    assert "모양" in _fails(patterned, "abc")


def test_주소는_http_로_시작해야_한다() -> None:
    link = _def("url")
    assert _ok(link, " https://example.com ") == "https://example.com"
    assert _fails(link, "example.com")


def test_빈_값은_실패가_아니라_지운다() -> None:
    number = _def("number")
    assert conversion.convert_stored(number, "").remove
    assert conversion.convert_stored(number, "   ").remove
    many = PropertyDef(key="k", label="칸", data_type="number", multi=True)
    assert conversion.convert_stored(many, []).remove


def test_여러_값은_원소마다_빈_것은_빼고_겹친_것은_하나로() -> None:
    many = PropertyDef(key="k", label="칸", data_type="number", multi=True)
    out = conversion.convert_stored(many, ["1", "", "1.0", "2"])
    assert out.value == [1, 2] and "duplicate" in out.lossy
    broken = conversion.convert_stored(many, ["1", "x"])
    assert broken.failures == [("x", "숫자가 아닙니다")]
    # 값 하나만 담는 속성에 목록이 있으면 변환할 수 없다(열쇠는 정렬한 JSON).
    single = conversion.convert_stored(_def("number"), ["1"])
    assert single.failures and single.failures[0][0] == '["1"]'


def test_정해진_글자와_열쇠() -> None:
    assert conversion.as_text(False) == "아니오"
    assert conversion.as_text(1e20) == "100000000000000000000"
    assert conversion.key_of({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    with pytest.raises(Unconvertible):
        conversion.as_text({"a": 1})


def test_안_바뀌면_바뀌지_않았다고_한다() -> None:
    text = _def("text")
    same = conversion.convert_stored(text, "그대로")
    assert same.value == "그대로" and not same.changed
    # 같은 값이라도 종류가 바뀌면 바뀐 것이다(1 과 True 는 파이썬에서 같다).
    flag = _def("number")
    assert conversion.convert_stored(flag, True).failures  # 예 → 숫자 아님
    assert conversion.convert_stored(_def("text"), 1).changed


def test_연월만_적힌_값은_그_달_1일이다() -> None:
    """월 집계 표의 「2026-09」 — 엑셀도 9월 1일로 읽는다. 여섯 자리는 해가 그럴듯할 때만."""
    day = _def("date")
    for text in ("2024-03", "2024.3", "2024. 3.", "2024/03", "202403", "2024년 3월"):
        assert _ok(day, text) == "2024-03-01", text
    assert "없는 날짜" in _fails(day, "2024-13")
    assert _fails(day, "240305")
    assert _ok(_def("datetime"), "2024-03") == "2024-03-01T00:00"
