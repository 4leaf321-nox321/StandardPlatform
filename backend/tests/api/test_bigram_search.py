"""두 글자 검색 — **「소음」 · 「볼트」 도 인덱스를 탄다**(0054, ADR 0010).

trigram 은 세 글자 조각이라 두 글자 검색은 표 전체를 읽었다. 한글은 두 글자 낱말이 흔하다.
두 글자 조각(`sp_bigrams`)으로 거른 뒤 `ILIKE` 가 다시 보므로, 찾는 것은 전과 같아야 한다 —
이름 · 식별자 · 별칭 어디에 들어 있든, 가운데에 있든.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_type


def test_두_글자_조각을_소문자로_만든다(db: Session) -> None:
    got = db.execute(text("select sp_bigrams('AB 소음')")).scalar()
    assert sorted(got) == sorted(["ab", "b ", " 소", "소음"])
    assert db.execute(text("select sp_bigrams('가')")).scalar() == []
    assert db.execute(text("select sp_bigrams(NULL)")).scalar() is None


def test_두_글자로_이름_식별자_별칭_가운데까지_찾는다(
    client: TestClient, admin: Signed
) -> None:
    tag = uuid.uuid4().hex[:4]
    # 두 글자 낱말이 겹치지 않게 시험마다 다른 글자 쌍을 쓴다(시험 DB 는 함께 쓴다).
    pair = f"뷁{chr(0xAC00 + int(tag, 16) % 11000)}"
    kind = _make_type(client, admin, label="부품", key_policy="optional")
    by_label = _make_object(client, admin, kind, label=f"모델 {pair} 검사")
    by_key = _make_object(client, admin, kind, label="다른 이름", key=f"K{pair}9")
    by_alias = _make_object(client, admin, kind, label="별칭으로만")
    added = client.put(
        f"/api/objects/{kind}/{by_alias['id']}/aliases",
        json={"aliases": [f"앞{pair}뒤"]},
        headers=admin.headers,
    )
    assert added.status_code == 200, added.text
    _make_object(client, admin, kind, label=f"{pair[0]} 떨어진 {pair[1]}")

    found = client.get("/api/search", params={"q": pair}, headers=admin.headers).json()
    ids = {one["id"] for one in found["items"]}
    assert ids == {by_label["id"], by_key["id"], by_alias["id"]}, found

    listed = client.get(f"/api/objects/{kind}", params={"q": pair}, headers=admin.headers)
    assert {one["id"] for one in listed.json()["items"]} == ids
