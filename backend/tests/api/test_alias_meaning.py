"""별칭 후보의 「이것 아닐까」 에 **뜻이 가까운 것**도(ADR 0076).

글자로 비슷한 것(pg_trgm)만 찾으면 「뒷면 덮개」 → 「후면 커버」 같은 짝은 영영 제안되지 않는다
— 글자가 하나도 안 겹친다. 그러면 검토함에서 「추천 없음」 으로 남고, 사람이 하나씩 찾아
붙인다. 여기서 보는 것: 워커가 검토함에 서는 줄을 뜻으로도 찾아 적어 두나 · 글자로 찾은 것이
먼저이고 뜻으로 찾은 것도 한 자리는 보이나 · 뜻으로만 찾은 추천의 확신도는 자동 확인의 문턱에
닿지 않나 · 못 부르면 그 차례에는 다시 부르지 않나 · 대상이 아닌 축 · 몇 번 안 찾은 줄.

가짜 Ollama 는 뜻 찾기 시험(`test_semantic`)의 것과 같은 모양이다 — 「커버 · cover · 덮개」 를
한 뜻으로 본다. 뜻 찾기 대상은 설치에 하나뿐이라 시험마다 비운다(`_clean`)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.modules.jobs.models import Job
from app.modules.objects import misses
from app.modules.objects import review as objects_review
from app.modules.objects.models import SearchMiss
from app.modules.review import services as review_services
from app.modules.semantic import services as semantic
from app.modules.semantic.models import ObjectEmbedding, SemanticMark, SemanticTarget
from app.shared import embedding, rerank, vectors
from tests.api.conftest import Signed
from tests.api.test_ontology import _make_object, _make_type
from tests.api.test_review import _items, _miss
from tests.api.test_semantic import _put, _sync_now, _vector


@pytest.fixture
def ollama() -> Iterator[list[list[str]]]:
    """가짜 Ollama — 받은 글 묶음을 차례로 모은다(`test_semantic.ollama` 와 같은 뜻)."""
    asked: list[list[str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/show"):
            return httpx.Response(
                200, json={"model_info": {"bert.embedding_length": vectors.DIM}}
            )
        texts = list(json.loads(request.content or b"{}").get("input") or [])
        asked.append(texts)
        return httpx.Response(200, json={"embeddings": [_vector(one) for one in texts]})

    embedding.transport = httpx.MockTransport(handle)
    yield asked
    embedding.transport = None


@pytest.fixture(autouse=True)
def _clean(db: Session) -> Iterator[None]:
    def wipe() -> None:
        db.execute(delete(ObjectEmbedding))
        # 맞춰 본 때도 — 남기면 대상이 없는 타입의 줄(「지울 자리」)이 되어 뒤 시험의 타이머가
        # 할 일이 있다고 본다(같은 작업자에서 다른 시험 뒤에 돌 때 떨어졌다, 2026-11-17).
        db.execute(delete(SemanticMark))
        db.execute(delete(SemanticTarget))
        db.execute(delete(Job).where(Job.kind == semantic.SYNC_KIND))
        db.commit()

    wipe()
    yield
    wipe()


def _refresh(db: Session, scope: str, **kw: Any) -> dict[str, Any]:
    return misses.refresh(db, progress=lambda *_: None, commit=db.commit, scope=scope, **kw)


def _stored(db: Session, row: SearchMiss) -> list[dict[str, Any]]:
    db.expire_all()
    found = db.get(SearchMiss, row.id)
    assert found is not None and found.suggested_at is not None
    return list(found.suggested or [])


def test_글자가_다른_짝을_뜻으로_찾아_검토함에_싣는다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    """「뒷면 덮개」 와 「후면 커버」 는 글자가 하나도 안 겹친다 — 뜻으로 찾는다. 리랭커가
    없으면 (시험은 닿지 않는다) 뜻의 거리로 고르고, 그 추천의 확신도는 0.6 까지다."""
    part = _make_type(client, admin, label="부품")
    cover = _make_object(client, admin, part, label="후면 커버")
    _make_object(client, admin, part, label="배터리 단자")  # 뜻이 먼 것 — 안 나온다
    _put(client, admin, [part])
    _sync_now(db)
    row = _miss(db, part, "뒷면 덮개", times=3)

    done = _refresh(db, part)
    assert done["done"] == 1 and done["meaning"] == 1 and done["meaning_state"] == "ready"
    assert "뜻으로 찾은 것 있음 1" in done["summary"]
    stored = _stored(db, row)
    assert [one["id"] for one in stored] == [cover["id"]]
    assert stored[0]["matched"] == "meaning" and stored[0]["scored_by"] == "distance"
    assert stored[0]["score"] > 0.45
    # 모델에 묻는 것은 못 찾은 말 그대로다(정규화한 말이 아니라 — 뜻은 띄어쓰기도 읽는다).
    assert ["뒷면 덮개"] in ollama

    page = _items(client, admin, kind="alias_candidate", type=part)
    (item,) = page["items"]
    assert item["recommended"] == cover["id"]
    assert item["confidence"] == objects_review.MEANING_CONFIDENCE_MAX["distance"]
    assert "글자가 비슷한 것은 없고 뜻이 가까움(뜻의 거리 유사도" in item["reason"]
    assert "확신도를 0.6까지로" in item["reason"]
    assert item["choices"][0]["detail"].endswith("· 뜻으로 찾음")

    listed = client.get(
        "/api/objects/alias-candidates", params={"type": part}, headers=admin.headers
    )
    (one,) = listed.json()["items"]
    (hit,) = one["suggestions"]
    assert (hit["id"], hit["matched"], hit["scored_by"]) == (
        cover["id"],
        "meaning",
        "distance",
    )


def test_글자로_찾은_것이_먼저이고_뜻으로_찾은_것도_한_자리는_보인다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    """둘은 점수의 눈금이 달라 한 줄로 섞지 않는다 — 글자로 찾은 것이 추천이 되고, 그 확신도 ·
    「차이가 작음」 은 글자로 찾은 것끼리만 견준다. 글자로 이미 찾은 객체는 뜻으로 또 내지
    않는다."""
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label="부품")
    glyph = _make_object(client, admin, part, label=f"Kestrel{tag} 커버")
    meaning = _make_object(client, admin, part, label="후면 덮개 판")
    _put(client, admin, [part])
    _sync_now(db)
    row = _miss(db, part, f"Kestrel{tag} 덮개", times=3)

    _refresh(db, part)
    stored = _stored(db, row)
    assert [(one["id"], one["matched"]) for one in stored] == [
        (glyph["id"], "label"),
        (meaning["id"], "meaning"),
    ]
    (item,) = _items(client, admin, kind="alias_candidate", type=part)["items"]
    assert item["recommended"] == glyph["id"]
    assert item["reason"].startswith("이름이 비슷함")
    assert "차이가 작음" not in item["reason"]  # 뜻으로 찾은 것과는 견주지 않는다
    assert [one["value"] for one in item["choices"]] == [glyph["id"], meaning["id"]]


def _hit(label: str, matched: str) -> misses.Suggestion:
    return misses.Suggestion(
        id=uuid.uuid4(),
        type_slug="part",
        type_label="부품",
        label=label,
        key=None,
        matched=matched,
        matched_text="",
        score=0.5,
        scored_by="distance" if matched == "meaning" else "",
    )


def test_보여_줄_셋은_글자_먼저_뜻은_적어도_한_자리() -> None:
    g = [_hit(f"g{one}", "label") for one in range(5)]
    m = [_hit(f"m{one}", "meaning") for one in range(3)]

    def labels(hits: list[misses.Suggestion]) -> list[str]:
        return [one.label for one in hits]

    assert labels(misses.pick(g + m, 3)) == ["g0", "g1", "m0"]
    assert labels(misses.pick(g[:1] + m, 3)) == ["g0", "m0", "m1"]
    assert labels(misses.pick(m, 3)) == ["m0", "m1", "m2"]
    assert labels(misses.pick(g, 3)) == ["g0", "g1", "g2"]
    assert misses.pick([], 3) == []


@pytest.fixture
def reranker() -> Iterator[dict[str, Any]]:
    """가짜 리랭커 — 글에 「커버」 가 들면 날 점수 5(시그모이드 0.99), 아니면 -9."""
    seen: dict[str, Any] = {"asked": []}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["asked"].append(body)
        results = [
            {"index": index, "relevance_score": 5.0 if "커버" in doc else -9.0}
            for index, doc in enumerate(body["documents"])
        ]
        return httpx.Response(200, json={"results": results})

    rerank.transport = httpx.MockTransport(handle)
    rerank.resume()
    yield seen
    rerank.transport = None
    rerank.resume()


def test_뜻으로만_찾은_추천은_자동_확인의_문턱에_닿지_않는다(
    client: TestClient,
    admin: Signed,
    db: Session,
    ollama: list[list[str]],
    reranker: dict[str, Any],
) -> None:
    """리랭커가 0.99 라고 해도 확신도는 0.85 — 자동 확인의 근거는 글자로 찾은 추천에서
    쌓였다."""
    for cap in objects_review.MEANING_CONFIDENCE_MAX.values():
        assert cap < review_services.AUTO_MIN_THRESHOLD
    part = _make_type(client, admin, label="부품")
    cover = _make_object(client, admin, part, label="후면 커버")
    _put(client, admin, [part])
    _sync_now(db)
    row = _miss(db, part, "뒷면 덮개", times=3)

    _refresh(db, part)
    (stored,) = _stored(db, row)
    assert stored["scored_by"] == "rerank" and stored["score"] > 0.99
    assert reranker["asked"] and reranker["asked"][-1]["query"] == "뒷면 덮개"
    (item,) = _items(client, admin, kind="alias_candidate", type=part)["items"]
    assert item["recommended"] == cover["id"]
    assert item["confidence"] == objects_review.MEANING_CONFIDENCE_MAX["rerank"]
    assert "(관련도 0.99)" in item["reason"] or "(관련도 1.00)" in item["reason"]
    assert "확신도를 0.85까지로" in item["reason"]


def test_뜻으로_찾기를_못_부르면_그_차례에는_다시_부르지_않고_글자로만_적는다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    """Ollama 가 꺼지면 줄마다 시간 상한(몇 초)을 기다리게 된다 — 한 번 못 부르면 그 차례의
    나머지 줄은 묻지 않는다. 밤에 다시 센다."""
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="후면 커버")
    _put(client, admin, [part])
    _sync_now(db)
    first = _miss(db, part, "뒷면 덮개", times=3)
    second = _miss(db, part, "앞면 덮개", times=3)

    knocked: list[str] = []

    def down(request: httpx.Request) -> httpx.Response:
        knocked.append(request.url.path)
        raise httpx.ConnectError("Ollama 가 꺼졌다", request=request)

    embedding.transport = httpx.MockTransport(down)
    done = _refresh(db, part)
    assert done["done"] == 2 and done["meaning_state"] == "unavailable"
    assert "뜻으로 찾기를 못 불러 글자로만" in done["summary"]
    assert len(knocked) == 1
    for row in (first, second):
        assert _stored(db, row) == []
        db.expire_all()
        found = db.get(SearchMiss, row.id)
        assert found is not None and found.suggest_note == misses.MEANING_DOWN_NOTE


def test_대상이_아닌_축이면_그렇다고_적고_몇_번_안_찾은_줄은_뜻으로_찾지_않는다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    """뜻으로 찾기를 쓰는 설치에서 이 축만 대상이 아니면 그 까닭을 적는다(대상을 추가할 수
    있다). 검토함에 서지 않는 줄(몇 번 안 찾은 것 — 대기의 대부분)은 모델을 부르지 않는다."""
    other = _make_type(client, admin, label="공급사")
    _put(client, admin, [other])
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="후면 커버")
    shown = _miss(db, part, "뒷면 덮개", times=3)
    rare = _miss(db, part, "옆면 덮개", times=1)

    _sync_now(db)
    ollama.clear()
    # 몇 번 안 찾은 줄은 밤 시각을 지나야 센다 — 이틀 뒤로 친다.
    done = _refresh(db, part, now=datetime.now(UTC) + timedelta(days=2))
    assert done["done"] == 2 and done["meaning_state"] == "off"
    db.expire_all()
    found = db.get(SearchMiss, shown.id)
    assert found is not None and found.suggest_note == misses.MEANING_OFF_NOTE
    quiet = db.get(SearchMiss, rare.id)
    assert quiet is not None and quiet.suggest_note == "" and quiet.suggested == []
    assert ollama == []  # 대상이 아니니 뜻으로 바꾸지도 않았다

    # 대상에 넣으면 검토함에 서는 줄만 뜻으로 묻는다 — 새로 선 두 줄(셈한 적 없다)로 본다.
    _put(client, admin, [other, part])
    _sync_now(db)
    _miss(db, part, "윗면 덮개", times=3)
    _miss(db, part, "아래 덮개", times=1)
    ollama.clear()
    again = _refresh(db, part, now=datetime.now(UTC) + timedelta(days=2))
    assert again["done"] == 2 and again["meaning"] == 1
    asked = [text for batch in ollama for text in batch]
    assert asked == ["윗면 덮개"]
