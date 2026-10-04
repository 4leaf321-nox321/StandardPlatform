"""목록 한 쪽의 길 — **방금 센 수로 고른 길이 무엇이든 같은 쪽이 나온다.**

맞는 줄이 적으면 먼저 모아 정렬하고(`page_rows` · `GATHER_BELOW`), 많으면 정렬 색인을 따라
걷는다. 기록 200만 건에서 「0건인 조건」 의 첫 쪽이 53초 걸리던 것을 그렇게 고쳤다 — 길이 둘이
되었으니 둘이 같은 줄을 같은 차례로 내는지를 본다. 날짜 읽기는 DB 함수(`sp_date`, 0061)가 한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_property, _make_type


def test_날짜_읽기_함수는_못_읽는_값을_NULL_로(db: Session) -> None:
    """통계의 기간 묶기 · 날짜 조건 · 지표의 기간이 함께 쓰는 뜻 — 앞 열 글자에서 대시를 떼고
    여덟 자리 숫자, 달의 날수 안이어야 날짜다."""
    cases = {
        "2024-02-29": "2024-02-29",
        "2023-02-29": None,
        "1900-02-29": None,
        "2000-02-29": "2000-02-29",
        "20240105": "2024-01-05",
        "2024-01-05T10:00:00": "2024-01-05",
        "2026-03-10 09:00": "2026-03-10",
        "2026-02-30": None,
        "2026-13-01": None,
        "2026-04-31": None,
        "0000-01-01": None,
        "2024-4-05": None,
        "abc": None,
        "": None,
    }
    for raw, wanted in cases.items():
        got = db.execute(
            text("select sp_date(CAST(:raw AS text))::text"), {"raw": raw}
        ).scalar()
        assert got == wanted, raw
    assert db.execute(text("select sp_date(NULL)")).scalar() is None


def _listed(client: TestClient, who: Signed, slug: str, **params: Any) -> dict[str, Any]:
    got = client.get(f"/api/objects/{slug}", params=params, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_적게_맞으면_모아서_정렬해도_색인_길과_같은_쪽이다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind = _make_type(client, admin, label="사건")
    _make_property(client, admin, kind, key="made", label="만든 날", data_type="date")
    _make_property(client, admin, kind, key="grade", label="등급", data_type="text")
    for index in range(12):
        _make_object(
            client,
            admin,
            kind,
            label=f"사건{(index * 7) % 12:02d}",
            properties={
                "made": f"2026-0{1 + index % 3}-15",
                "grade": "A" if index % 2 else "B",
            },
        )
    asked = [
        {"f.grade.eq": "A", "limit": 3, "offset": 0},
        {"f.grade.eq": "A", "limit": 3, "offset": 3},
        {"f.made.gte": "2026-02-01", "f.made.lt": "2026-03-01", "limit": 50, "offset": 0},
        {"f.grade.eq": "Z", "limit": 50, "offset": 0},
        {"f.grade.eq": "A", "limit": 3, "offset": 6},
    ]

    def pages() -> list[tuple[int, list[str]]]:
        out = []
        for params in asked:
            found = _listed(client, admin, kind, **params)
            out.append((found["total"], [one["label"] for one in found["items"]]))
        return out

    gathered = pages()  # 시험의 타입은 작아서 늘 모으는 길이다
    monkeypatch.setattr("app.modules.objects.services.GATHER_BELOW", 0)
    walked = pages()
    assert gathered == walked
    assert gathered[0] == (6, ["사건01", "사건03", "사건05"])
    assert gathered[1] == (6, ["사건07", "사건09", "사건11"])
    assert gathered[2][0] == 4 and gathered[2][1] == sorted(gathered[2][1])
    assert gathered[3] == (0, [])
    assert gathered[4] == (6, [])
