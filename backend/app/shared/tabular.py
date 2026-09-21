"""표 글을 행으로 — **파일이든 붙여 넣기든 같은 규칙.**

CSV(쉼표)·탭 구분(엑셀에서 복사한 것)·JSON(객체 배열 또는 `{"rows": [...]}`) 을 받는다.
BOM 을 벗긴다 — 엑셀이 붙이는 것이라 안 벗기면 첫 열 이름이 `\\ufeffkey` 가 되어 「모르는
열」 로 거절된다. 세미콜론은 구분자로 안 본다 — 여러 값의 구분자(`;`)와 겹친다.

객체 일괄 입력·부서 붙여 넣기·표에서 타입 만들기가 전부 이것을 쓴다.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any


class TabularError(ValueError):
    """읽을 수 없는 표 — 부르는 쪽이 자기 오류 코드로 감싼다."""


def parse_rows(name: str, raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8-sig")
    stripped = text.lstrip()
    if name.lower().endswith(".json") or stripped.startswith(("{", "[")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as caught:
            raise TabularError(f"JSON 을 읽을 수 없습니다: {caught}") from None
        rows = data.get("rows") if isinstance(data, dict) else data
        if not isinstance(rows, list) or not all(isinstance(one, dict) for one in rows):
            raise TabularError('JSON 은 객체의 배열이거나 {"rows": [...]} 여야 합니다.')
        return rows
    # 구분자 — 첫 줄에 탭이 있으면 탭(엑셀 복사), 아니면 쉼표.
    first = text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    out: list[dict[str, Any]] = []
    for row in reader:
        # 열 이름의 앞뒤 공백은 사람 눈에 안 보이는 오타다.
        out.append({(k or "").strip(): v for k, v in row.items() if k is not None})
    return out
