"""문서 올리기(`objects/docpush.py`)의 DB 없는 조각 — 그림 꼴의 빠르기 · 항목 읽기의 상한.

2026-11-27 점검: 그림 꼴(`IMAGE`)이 `![](` 가 이어진 본문에서 길이의 제곱으로 느려졌고(8만 자
31초 — 본문 상한 100만 자면 한 시간 넘게), 목록 · 마크다운은 상한 없이 통째로 풀렸다.
"""

from __future__ import annotations

import io
import time
import zipfile

from app.modules.objects import docpush, photos


def test_그림_꼴은_이어진_여는_괄호에도_본문_길이에_비례한다() -> None:
    for body in ("![](" * 50_000, "![a](b" * 40_000, "](" * 100_000, "![" * 100_000):
        started = time.perf_counter()
        hits = list(docpush.IMAGE.finditer(body))
        docpush.IMAGE.sub(lambda hit: hit.group(0), body)
        docpush._leftover(body)
        elapsed = time.perf_counter() - started
        assert len(body) >= 200_000 and not hits
        assert elapsed < 1.0, f"{body[:8]!r}… {len(body):,}자 {elapsed:.2f}초"


def test_그림_꼴은_키트와_같은_꼴을_읽는다() -> None:
    hit = docpush.IMAGE.search("앞 ![캡션](media/image3.png?w=60) 뒤")
    assert hit is not None
    assert (hit.group("target"), hit.group("query")) == ("media/image3.png", "?w=60")
    for odd in (
        '![a](img/a.png "t")',
        "![a](my pic.png)",
        "![a](img/a.png?w=1000)",
        "![a]b](img/a.png)",
        "![a](b(c).png)",
    ):
        assert docpush.IMAGE.search(odd) is None, odd
        assert docpush._leftover(odd) is not None, odd


def test_로컬처럼_보이지_않는_자리는_경고하지_않는다() -> None:
    for fine in (
        "![a](photo:0f8c3a8e-0000-4000-8000-000000000000?w=60)",
        "![a](https://example.com/a.png)",
        "![a](data:image/png;base64,AAAA)",
        "![a](#그림-1)",
        "[문서](notes.pdf) 와 ![a](photo:x)",
        "![a](photo:x) 뒤의 [링크](notes.pdf)",
        "![로고][1] 는 [안내](guide.md) 를 본다",
    ):
        assert docpush._leftover(fine) is None, fine
    found = docpush._leftover("![a](/img/a.png)\n![b](C:/그림/b.png)\n")
    assert found is not None and found[0] == 2, found


def _archive(name: str, data: bytes) -> tuple[zipfile.ZipFile, photos._Entry]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)
    opened = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    info = opened.getinfo(name)
    return opened, photos._Entry(info, name)


def test_항목은_상한까지만_풀고_머리가_거짓이어도_넘지_않는다() -> None:
    archive, entry = _archive("a.md", b"x" * 100)
    with archive:
        assert docpush._read(archive, entry, 100) == b"x" * 100
        assert docpush._read(archive, entry, 99) is None, "상한을 넘으면 읽지 않은 것으로"
        # 머리에 적힌 크기가 실제보다 작다 — 그만큼에서 끊고 CRC 가 안 맞아 「깨짐」 이다.
        entry.info.file_size = 10
        assert docpush._read(archive, entry, 50) is None
