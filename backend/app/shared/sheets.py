"""표를 파일로 — CSV 와 Excel.

어느 모듈이든 「이 표를 내려받게」 가 필요해지면 여기를 쓴다. 화면마다 따로 만들면
BOM·수식 막기·파일 이름 규칙이 조금씩 어긋나고, 그 어긋남은 **엑셀에서 한글이 깨지는
날** 드러난다.

## 수식으로 읽히는 글자를 막는다

객체 이름은 사람이 적는 값이다. `=HYPERLINK(...)` 나 `+cmd|...` 로 시작하는 이름이
그대로 셀에 들어가면 **엑셀이 그것을 수식으로 실행한다**(CSV injection). 글자로 시작이
`= + - @` 이거나 탭·줄바꿈이면 앞에 `'` 를 붙여 글자로 둔다. 숫자는 건드리지 않는다.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

from fastapi import Response

#: 이 글자로 시작하는 **글자** 값은 엑셀이 수식으로 읽는다.
FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")

CSV_TYPE = "text/csv; charset=utf-8"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(FORMULA_LEADS):
        return "'" + value
    return value


def to_csv(header: list[str], rows: list[list[Any]]) -> bytes:
    """엑셀이 한글을 안 깨뜨리게 BOM 을 붙인다."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([safe_cell(one) for one in header])
    for row in rows:
        writer.writerow(["" if one is None else safe_cell(one) for one in row])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def to_xlsx(header: list[str], rows: list[list[Any]], *, sheet: str = "표") -> bytes:
    """머리줄은 굵게, 첫 줄은 고정 — 긴 표를 내려도 무슨 열인지 보이게."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    page = book.active
    # 시트 이름은 31자까지이고 몇몇 글자를 못 쓴다.
    page.title = "".join(ch for ch in sheet if ch not in "[]:*?/\\")[:31] or "표"
    page.append([safe_cell(one) for one in header])
    for cell in page[1]:
        cell.font = Font(bold=True)
    for row in rows:
        page.append([safe_cell(one) for one in row])
    page.freeze_panes = "A2"
    for index, title in enumerate(header, start=1):
        widest = max(
            [len(str(title)), *(len(str(r[index - 1])) for r in rows if len(r) >= index)]
        )
        page.column_dimensions[page.cell(row=1, column=index).column_letter].width = min(
            60, max(8, widest * 1.6)
        )
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def file_response(
    header: list[str], rows: list[list[Any]], *, fmt: str, stem: str, sheet: str = "표"
) -> Response:
    """내려받기 응답. 파일 이름은 **ASCII** 로 — 헤더에 한글을 그대로 넣으면 깨지는
    프록시가 있다. 사람이 읽는 이름은 화면이 붙인다."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    if fmt == "xlsx":
        body, media, ext = to_xlsx(header, rows, sheet=sheet), XLSX_TYPE, "xlsx"
    else:
        body, media, ext = to_csv(header, rows), CSV_TYPE, "csv"
    return Response(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{stem}-{stamp}.{ext}"'},
    )
