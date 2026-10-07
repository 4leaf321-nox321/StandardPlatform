"""표 글 읽기 — **한국어 엑셀이 그냥 저장한 CSV(CP949)도 읽는다.**

한국어 Windows 엑셀의 「CSV (쉼표로 분리)」 는 CP949 다. UTF-8 만 받았더니 일괄 입력 작업은
`UnicodeDecodeError` 한 줄로 실패하고, 표에서 타입 추론은 500 이 났다. 원천 연결의 파일도
같은 규칙으로 읽는다.
"""

from __future__ import annotations

import pytest

from app.modules.datasources import fetchers
from app.shared import tabular
from app.shared.errors import AppError, code

TEXT = "key,label,재질\nP-1,볼트,스틸\nP-2,너트,고무\n"
ROWS = [
    {"key": "P-1", "label": "볼트", "재질": "스틸"},
    {"key": "P-2", "label": "너트", "재질": "고무"},
]
NEITHER = b"key,label\nP-1,\x80\n"  # UTF-8 도 CP949 도 아닌 바이트


@pytest.mark.parametrize(
    "raw",
    [TEXT.encode("utf-8"), ("﻿" + TEXT).encode("utf-8"), TEXT.encode("cp949")],
    ids=["utf-8", "utf-8-bom", "cp949"],
)
def test_어느_인코딩으로_저장했든_같은_행(raw: bytes) -> None:
    assert tabular.parse_rows("rows.csv", raw) == ROWS
    assert fetchers.parse_file(raw, fmt="csv") == ROWS


def test_둘_다_아니면_무엇을_할지_말한다() -> None:
    """파이썬 예외 이름이 아니라 사람이 할 일 — 엑셀에서 어떻게 저장하면 되는지."""
    with pytest.raises(tabular.TabularError, match="CSV UTF-8"):
        tabular.parse_rows("rows.csv", NEITHER)
    with pytest.raises(AppError, match="CSV UTF-8") as caught:
        fetchers.parse_file(NEITHER, fmt="csv")
    assert caught.value.code == code("DATASOURCES", 10)
