"""속성 값 검증 — **JSONB 에는 제약이 없으므로 여기가 유일한 방어선이다.**

여기서 새는 것은 DB 가 안 잡는다. 그래서 다른 시험보다 촘촘하게 본다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.modules.ontology.models import PropertyDef
from app.modules.ontology.services import (
    InvalidValue,
    merge_properties,
    object_ref_ids,
    require_key,
    require_slug,
    validate_properties,
)


def _def(key: str, data_type: str, **kw: Any) -> PropertyDef:
    return PropertyDef(
        id=uuid.uuid4(),
        owner_kind="type",
        owner_id=uuid.uuid4(),
        key=key,
        label=kw.pop("label", key),
        data_type=data_type,
        required=bool(kw.pop("required", False)),
        multi=bool(kw.pop("multi", False)),
        enum_options=kw.pop("enum_options", None),
        ref_type_slug=kw.pop("ref_type_slug", None),
    )


# --- slug 와 키 --------------------------------------------------------------


@pytest.mark.parametrize("value", ["part", "part_no", "a", "a1_b2"])
def test_쓸_수_있는_slug(value: str) -> None:
    assert require_slug(value, what="타입") == value


@pytest.mark.parametrize(
    "value", ["Part", "1part", "part-no", "part.no", "", "부품", "a" * 33]
)
def test_쓸_수_없는_slug(value: str) -> None:
    """**바꿀 수 없는 값이므로 들어올 때 막는다.** URL·관계·MCP 도구 이름이 물린다."""
    with pytest.raises(InvalidValue):
        require_slug(value, what="타입")


def test_속성_키에_점을_못_쓴다() -> None:
    """`list_view` 가 `properties.<key>` 로 가리키므로, 점이 들어가면 그 표기가 깨진다."""
    with pytest.raises(InvalidValue):
        require_key("vendor.name")


# --- 값 하나 ----------------------------------------------------------------


def test_숫자에_참거짓을_안_받는다() -> None:
    """파이썬에서 bool 은 int 의 하위형이다. 안 막으면 True 가 1 로 저장되고,
    그 값은 나중에 아무도 설명할 수 없다."""
    with pytest.raises(InvalidValue):
        validate_properties([_def("qty", "number")], {"qty": True})


def test_숫자는_정수도_실수도_받는다() -> None:
    defs = [_def("qty", "number")]
    assert validate_properties(defs, {"qty": 3})["qty"] == 3
    assert validate_properties(defs, {"qty": 3.5})["qty"] == 3.5


def test_날짜는_ISO_여야_한다() -> None:
    defs = [_def("due", "date")]
    assert validate_properties(defs, {"due": "2026-09-11"})["due"] == "2026-09-11"
    with pytest.raises(InvalidValue):
        validate_properties(defs, {"due": "2026/09/11"})


def test_선택지에_없는_값은_거절한다() -> None:
    defs = [_def("grade", "enum", enum_options=["A", "B"])]
    assert validate_properties(defs, {"grade": "A"})["grade"] == "A"
    with pytest.raises(InvalidValue):
        validate_properties(defs, {"grade": "C"})


def test_객체_참조는_uuid_여야_한다() -> None:
    defs = [_def("vendor", "object_ref")]
    ref = str(uuid.uuid4())
    assert validate_properties(defs, {"vendor": ref})["vendor"] == ref
    with pytest.raises(InvalidValue):
        validate_properties(defs, {"vendor": "삼성"})


def test_파일_속성은_properties_에_안_들어간다() -> None:
    """첨부는 `attachments.owner_field` 로 붙는다. 값이 여기 왔다는 것은 부르는
    쪽이 잘못 알고 있다는 뜻이라, **조용히 버리지 않고 말해 준다.**"""
    with pytest.raises(InvalidValue) as caught:
        validate_properties([_def("drawing", "file")], {"drawing": "x.pdf"})
    assert "첨부" in caught.value.message


# --- 값 묶음 ----------------------------------------------------------------


def test_모르는_키는_거절한다() -> None:
    """조용히 저장하면 **어느 화면에도 안 나오는 데이터**가 쌓이고, 그것이
    있다는 사실은 아무도 모른다."""
    with pytest.raises(InvalidValue) as caught:
        validate_properties([_def("qty", "number")], {"qty": 1, "ghost": "x"})
    assert "ghost" in caught.value.message


def test_필수_값이_비면_거절한다() -> None:
    defs = [_def("name", "text", required=True)]
    with pytest.raises(InvalidValue):
        validate_properties(defs, {})
    with pytest.raises(InvalidValue):
        validate_properties(defs, {"name": ""})


def test_빈_값은_저장하지_않는다() -> None:
    """빈 문자열을 넣어 두면 「값이 없음」 과 「빈 값을 넣음」 이 구별되지 않는다."""
    cleaned = validate_properties([_def("memo", "text")], {"memo": ""})
    assert "memo" not in cleaned


def test_multi_는_목록이어야_한다() -> None:
    defs = [_def("tags", "text", multi=True)]
    assert validate_properties(defs, {"tags": ["a", "b"]})["tags"] == ["a", "b"]
    with pytest.raises(InvalidValue):
        validate_properties(defs, {"tags": "a"})


def test_단일_속성에_목록을_넣으면_거절한다() -> None:
    with pytest.raises(InvalidValue):
        validate_properties([_def("name", "text")], {"name": ["a", "b"]})


# --- 부분 수정 --------------------------------------------------------------


def test_안_보낸_것은_그대로_두고_null_만_지운다() -> None:
    """**통째로 덮으면 이름 하나 바꿀 때마다 다른 속성이 함께 날아가고, 그 손실은
    저장한 사람 눈에 안 보인다.**"""
    current = {"a": 1, "b": 2, "c": 3}
    merged = merge_properties(current, {"a": 9, "b": None})
    assert merged == {"a": 9, "c": 3}


def test_병합은_원본을_안_고친다() -> None:
    current = {"a": 1}
    merge_properties(current, {"a": 2})
    assert current == {"a": 1}


# --- 참조 수집 --------------------------------------------------------------


def test_가리키는_객체_id_를_모은다() -> None:
    one, two = str(uuid.uuid4()), str(uuid.uuid4())
    defs = [_def("vendor", "object_ref"), _def("parts", "object_ref", multi=True)]
    found = object_ref_ids(defs, {"vendor": one, "parts": [two]})
    assert {str(x) for x in found} == {one, two}
