"""뜻으로 찾기(ADR 0052) — 켠 타입을 뜻의 자리로 바꿔 두고, 검색이 뜻이 가까운 것을 더한다.

진짜 Ollama 에 닿지 않는다(`conftest._no_real_ollama`) — 여기서 가짜를 끼운다. 가짜는 「같은
뜻의 말」 묶음(배터리 = battery = 전지 …)마다 한 방향을 주고, 글마다 제 방향을 조금 더한다 —
그러면 낱말이 달라도 뜻이 같은 것끼리 유사도 약 0.92, 상관없는 것은 0 이라 기준(0.45) 아래다.

pgvector 는 시험 DB 에 진짜로 있다 — 거리 계산 · HNSW 색인은 진짜다.
"""

from __future__ import annotations

import json
import math
import uuid
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.jobs.models import Job
from app.modules.ontology.models import ObjectType
from app.modules.semantic import services
from app.modules.semantic.models import ObjectEmbedding, SemanticTarget
from app.modules.workspaces.models import Workspace
from app.shared import embedding, vectors
from app.shared.errors import code
from tests.api.conftest import Signed, work_until
from tests.api.test_ontology import _make_object, _make_property, _make_type

#: 같은 뜻의 말 묶음 — 묶음마다 한 방향.
CONCEPTS = (
    ("배터리", "battery", "전지"),
    ("커버", "cover", "덮개"),
    ("침수", "물에 빠짐"),
)


def _vector(text: str) -> list[float]:
    at = [0.0] * vectors.DIM
    for dim, words in enumerate(CONCEPTS):
        if any(word in text for word in words):
            at[dim] = 1.0
    # 글마다 제 방향 하나 — 묶음이 안 맞는 글끼리는 서로 멀고(0), 묶음이 같으면 약 0.92 다.
    at[len(CONCEPTS) + zlib.crc32(text.encode()) % (vectors.DIM - len(CONCEPTS))] = 0.3
    size = math.sqrt(sum(one * one for one in at))
    return [one / size for one in at]


@pytest.fixture
def ollama() -> Iterator[list[list[str]]]:
    """가짜 Ollama — 받은 글 묶음을 차례로 모은다."""
    asked: list[list[str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/show"):  # 관리 화면의 준비 확인
            return httpx.Response(
                200, json={"model_info": {"bert.embedding_length": vectors.DIM}}
            )
        body = json.loads(request.content or b"{}")
        texts = list(body.get("input") or [])
        asked.append(texts)
        return httpx.Response(200, json={"embeddings": [_vector(one) for one in texts]})

    embedding.transport = httpx.MockTransport(handle)
    yield asked
    embedding.transport = None


@pytest.fixture(autouse=True)
def _clean(db: Session) -> Iterator[None]:
    """켠 타입 · 자리 · 계산 작업은 설치에 하나뿐이다 — 앞뒤 시험과 섞이지 않게 비운다."""

    def wipe() -> None:
        db.execute(delete(ObjectEmbedding))
        db.execute(delete(SemanticTarget))
        db.execute(delete(Job).where(Job.kind == services.SYNC_KIND))
        db.commit()

    wipe()
    yield
    wipe()


def _put(
    client: TestClient, who: Signed, targets: list[str] | dict[str, list[str] | None]
) -> Any:
    """대상 통째로 — 목록이면 칸은 자동, 사전이면 타입마다 고른 칸."""
    chosen = targets if isinstance(targets, dict) else dict.fromkeys(targets)
    body = {
        "targets": [{"type_slug": slug, "fields": fields} for slug, fields in chosen.items()]
    }
    return client.put("/api/semantic/targets", json=body, headers=who.headers)


def _sync_now(db: Session) -> None:
    job = db.scalar(
        select(Job)
        .where(Job.kind == services.SYNC_KIND, Job.status == "queued")
        .order_by(Job.created_at.desc())
    )
    assert job is not None, "계산이 들어가지 않았습니다"
    work_until(db, job.id)
    db.expire_all()
    finished = db.get(Job, job.id)
    assert finished is not None and finished.status == "done", finished and finished.error


def _find(client: TestClient, who: Signed, q: str, **params: Any) -> dict[str, Any]:
    got = client.get("/api/search", params={"q": q, **params}, headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def test_시스템_관리자만_본다(client: TestClient, member: Signed) -> None:
    assert client.get("/api/semantic", headers=member.headers).status_code == 403
    assert _put(client, member, []).status_code == 403


def test_기록은_칸을_골라야_대상이_된다(client: TestClient, admin: Signed) -> None:
    """기록은 수십만 건이라 전부(자동)는 서버가 감당하지 못한다 — 칸을 고른다(ADR 0054).

    고를 수 있는 것은 글 · 여러 줄 글 · 선택 · 참조 · 문서와 이름."""
    log_type = _make_type(client, admin, label="시장 서비스", usage="log")
    _make_property(
        client, admin, log_type, key="symptom", label="고객 증상", data_type="text_long"
    )
    _make_property(client, admin, log_type, key="cost", label="비용", data_type="number")

    refused = _put(client, admin, [log_type])
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == code("SEMANTIC", 4)
    assert "칸을 선택합니다" in refused.json()["error"]["message"]
    wrong = _put(client, admin, {log_type: ["cost", "nothing"]})
    assert wrong.json()["error"]["code"] == code("SEMANTIC", 5)
    assert wrong.json()["error"]["details"]["fields"] == ["cost", "nothing"]
    missing = _put(client, admin, ["없는_타입"])
    assert missing.json()["error"]["code"] == code("SEMANTIC", 1)

    shown = client.get("/api/semantic", headers=admin.headers).json()
    row = next(one for one in shown["types"] if one["type_slug"] == log_type)
    assert row["enabled"] is False and row["why_not"] is None
    assert [one["key"] for one in row["options"]] == ["symptom"]  # 숫자는 고를 수 없다

    ok = _put(client, admin, {log_type: ["symptom", "label"]})
    assert ok.status_code == 200, ok.text
    row = next(one for one in ok.json()["types"] if one["type_slug"] == log_type)
    assert (row["enabled"], row["fields"]) == (True, ["symptom", "label"])


def test_켜면_계산이_들어가고_기본_글을_뜻으로_바꿔_담는다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    model = _make_type(client, admin, label="모델")
    part = _make_type(client, admin, label="부품")
    _make_property(client, admin, part, key="family", label="부품군", data_type="enum",
                   enum_options=["전원", "외관"])  # fmt: skip
    _make_property(client, admin, part, key="price", label="단가", data_type="number")
    _make_property(client, admin, part, key="model", label="모델", data_type="object_ref",
                   ref_type_slug=model)  # fmt: skip
    n9 = _make_object(client, admin, model, label="N9")
    made = _make_object(
        client,
        admin,
        part,
        label="N9 전지 팩",
        description="교체용",
        properties={"family": "전원", "price": 12000, "model": n9["id"]},
    )
    client.put(
        f"/api/objects/{part}/{made['id']}/aliases",
        json={"aliases": ["배터리 팩"]},
        headers=admin.headers,
    )

    put = _put(client, admin, [part, model])
    assert put.status_code == 200, put.text
    assert {one["type_slug"] for one in put.json()["types"] if one["enabled"]} == {part, model}
    _sync_now(db)

    sent = [text for batch in ollama for text in batch]
    body = next(one for one in sent if "N9 전지 팩" in one)
    # 타입 이름 · 이름 · 다른 이름 · 설명 · 선택 칸 · 참조 칸이 가리키는 것의 이름 — 숫자는
    # 뺀다.
    assert body.startswith("부품: N9 전지 팩")
    for piece in ("다른 이름: 배터리 팩", "교체용", "부품군: 전원", "모델: N9"):
        assert piece in body, body
    assert "12000" not in body and "단가" not in body

    stored = db.scalar(select(ObjectEmbedding).where(ObjectEmbedding.object_id == made["id"]))
    assert stored is not None and stored.model == get_settings().embedding_model
    assert len(stored.embedding) == vectors.DIM

    shown = client.get("/api/semantic", headers=admin.headers).json()
    row = next(one for one in shown["types"] if one["type_slug"] == part)
    assert (row["objects"], row["synced"], row["pieces"], row["pending"]) == (1, 1, 1, 0)
    assert row["estimate_bytes"] == services.KB_PER_OBJECT * 1024
    assert shown["job"]["status"] == "done"


def test_바뀐_것만_다시_바꾸고_끄면_자리를_지운다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    part = _make_type(client, admin, label="부품")
    one = _make_object(client, admin, part, label="볼트")
    _make_object(client, admin, part, label="너트")
    _put(client, admin, [part])
    _sync_now(db)
    assert sum(len(batch) for batch in ollama) == 2

    # 아무것도 안 바뀌었으면 Ollama 를 부르지 않는다 — 전부를 해시로 맞춰 봐도.
    ollama.clear()
    client.post("/api/semantic/sync", headers=admin.headers)
    _sync_now(db)
    assert ollama == []
    assert services.needs_work(db) is False

    # 이름을 고치면 그것 하나만.
    client.patch(f"/api/objects/{part}/{one['id']}", json={"label": "육각 볼트"},
                 headers=admin.headers)  # fmt: skip
    assert services.needs_work(db) is True
    services.enqueue(db, full=False, reason="test", user=None)
    db.commit()
    _sync_now(db)
    assert [text for batch in ollama for text in batch] == ["부품: 육각 볼트"]

    # 끄면 그 타입의 자리가 지워진다.
    _put(client, admin, [])
    _sync_now(db)
    assert db.scalar(select(func.count()).select_from(ObjectEmbedding)) == 0


def test_통합_검색이_글자가_달라도_뜻이_가까운_것을_더한다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label="부품")
    battery = _make_object(client, admin, part, label=f"{tag} 배터리")
    cover = _make_object(client, admin, part, label=f"{tag} 후면 커버")
    _make_object(client, admin, part, label=f"{tag} 볼트")
    _put(client, admin, [part])
    _sync_now(db)

    found = _find(client, admin, "battery", meaning="true")
    assert found["meaning_state"] == "ready"
    assert found["meaning_narrowed"] is False  # 작은 설치는 색인이 모자랄 일이 없다
    assert [one["id"] for one in found["meaning"]] == [battery["id"]]
    assert found["meaning"][0]["similarity"] > 0.9
    assert found["meaning"][0]["type_label"] == "부품"

    # 글자로 이미 찾은 것은 뜻 쪽에 또 세우지 않는다.
    both = _find(client, admin, f"{tag} 후면 커버", meaning="true")
    assert [one["id"] for one in both["items"]] == [cover["id"]]
    assert cover["id"] not in [one["id"] for one in both["meaning"]]

    # 상관없는 말 — 기준(0.45) 아래는 안 낸다.
    assert _find(client, admin, "날씨", meaning="true")["meaning"] == []
    # 묻지 않으면 뜻으로 바꾸지 않는다(별칭 후보 창 같은 다른 부르는 곳).
    asked = len(ollama)
    plain = _find(client, admin, "battery")
    assert (plain["meaning"], plain["meaning_state"]) == ([], "")
    assert len(ollama) == asked


def test_작은_범위는_정확히_재고_못_채운_채_끝나면_덜_나왔다고_알린다(
    client: TestClient,
    admin: Signed,
    db: Session,
    ollama: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pgvector 0.6 의 HNSW 는 가까운 조각 `EF_SEARCH` 개를 먼저 받고 그 뒤에 타입 · 부서로
    거른다. 그래서 고른 범위가 작으면 정확히 재고(`EXACT_MAX`), 큰 범위에서 못 채운 채 끝나면
    빈 줄을 「뜻으로도 없다」 로 읽지 않게 알린다(ADR 0052 「추가」)."""
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label="부품")
    pack = _make_type(client, admin, label="포장")
    bundle = _make_type(client, admin, label="묶음")
    for n in range(4):
        _make_object(client, admin, part, label=f"{tag} 배터리 {n}")
    # 두 뜻이 섞여 「battery」 와 덜 가깝다(약 0.66) — 그래도 기준(0.45) 안이다.
    box = _make_object(client, admin, pack, label=f"{tag} 배터리 커버 상자")
    for _ in range(3):  # 글이 똑같다 — 같은 자리를 옮겨 쓴다
        _make_object(client, admin, bundle, label=f"{tag} 배터리 묶음")
    _put(client, admin, [part, pack, bundle])
    _sync_now(db)

    user = db.scalar(select(User).where(User.email == admin.email))
    ids = dict(db.execute(select(ObjectType.slug, ObjectType.id)).tuples().all())
    assert user is not None
    monkeypatch.setattr(services, "EF_SEARCH", 2)  # 받는 수를 작게 — 운영의 400 을 흉내 낸다

    # 1) 작은 범위는 정확히 잰다 — 받는 수가 2 라도 좁힌 타입의 것이 온다.
    state, near, narrowed = services.nearest(db, user, "battery", among={ids[pack]})
    assert (state, [str(one.row.id) for one in near], narrowed) == (
        "ready",
        [box["id"]],
        False,
    )

    # 2) 큰 범위는 색인으로 받는다(여기서는 `EXACT_MAX` 를 0 으로) — 가까운 두 조각이 다른
    # 타입이라 걸러지면 빈 줄인데, 「뜻으로도 없다」 가 아니다. 작은 표에서는 Postgres 가 색인
    # 대신 전부 읽고 정렬하므로 색인 순서로 훑게 한다.
    monkeypatch.setattr(services, "EXACT_MAX", 0)
    db.execute(text("SET LOCAL enable_seqscan = off"))
    db.execute(text("SET LOCAL enable_sort = off"))
    assert services.nearest(db, user, "battery", among={ids[pack]}) == ("ready", [], True)
    monkeypatch.setattr(services, "EXACT_MAX", 10_000)

    # 3) 받은 줄이 끝까지 기준 안인데 낼 수를 못 채웠다 — 더 있을 수 있다. 다 찼으면 아니다
    # (안 본 것은 받은 어느 것보다 멀다).
    state, near, narrowed = services.nearest(db, user, "battery", among={ids[part]})
    assert len(near) == 2 and narrowed is True
    assert services.nearest(db, user, "battery", among={ids[part]}, limit=1)[2] is False

    # 4) 같은 글이 받는 수를 채웠다 — 한 줄만 섰지만 다른 가까운 글이 뒤에 있을 수 있다.
    state, near, narrowed = services.nearest(db, user, "battery", among={ids[bundle]})
    assert len(near) == 1 and near[0].same == 2 and narrowed is True


def test_볼_수_없는_부서의_것은_뜻으로도_안_나온다(
    client: TestClient,
    admin: Signed,
    member: Signed,
    db: Session,
    ollama: list[list[str]],
    department_scope: None,
) -> None:
    other = Workspace(slug=f"other-{uuid.uuid4().hex[:8]}", name="다른 부서")
    db.add(other)
    db.commit()
    part = _make_type(client, admin, label="부품")
    hidden = _make_object(client, admin, part, label="남의 배터리", workspace_slug=other.slug)
    mine = _make_object(
        client, admin, part, label="우리 전지", workspace_slug=member.workspace
    )
    _put(client, admin, [part])
    _sync_now(db)

    found = _find(client, member, "battery", meaning="true")
    ids = [one["id"] for one in found["meaning"]]
    assert mine["id"] in ids and hidden["id"] not in ids


def test_Ollama_가_꺼져_있으면_검색은_글자로만_답한다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    tag = uuid.uuid4().hex[:6]
    part = _make_type(client, admin, label="부품")
    made = _make_object(client, admin, part, label=f"{tag} 배터리 팩")
    _put(client, admin, [part])
    _sync_now(db)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("꺼짐", request=request)

    embedding.transport = httpx.MockTransport(down)
    found = _find(client, admin, f"{tag} 배터리", meaning="true")
    assert found["meaning_state"] == "unavailable"
    assert [one["id"] for one in found["items"]] == [made["id"]]


def test_켠_타입이_없거나_끔이면_뜻으로_바꾸지_않는다(
    client: TestClient,
    admin: Signed,
    ollama: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _find(client, admin, "battery", meaning="true")["meaning_state"] == "off"
    part = _make_type(client, admin, label="부품")
    _put(client, admin, [part])
    monkeypatch.setattr(get_settings(), "embedding_backend", "off")
    assert _find(client, admin, "battery", meaning="true")["meaning_state"] == "off"
    assert ollama == []


def test_계산이_실패하면_그_말이_작업에_남는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """Ollama 에 닿지 않으면 실패로 끝난다 — 타이머는 한 시간 뒤에 다시 넣는다."""
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트")
    _put(client, admin, [part])
    job = db.scalar(select(Job).where(Job.kind == services.SYNC_KIND))
    assert job is not None
    work_until(db, job.id)
    db.refresh(job)
    assert job.status == "failed"
    assert "Ollama 에 닿지 않습니다" in (job.error or "")

    assert services.schedule(db) is None
    later = datetime.now(UTC) + services.RETRY_AFTER + timedelta(minutes=1)
    assert services.schedule(db, now=later) == "full"


def test_타이머는_차례가_되면_넣고_돌거나_기다리면_안_넣는다(
    client: TestClient,
    admin: Signed,
    db: Session,
    ollama: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert services.schedule(db) is None  # 켠 타입도, 지울 자리도 없다
    part = _make_type(client, admin, label="부품")
    _make_object(client, admin, part, label="볼트")
    db.add(SemanticTarget(type_id=db.scalar(_type_id(part))))
    db.commit()
    assert services.schedule(db) == "full"  # 처음 — 전부
    assert services.schedule(db) is None  # 이미 기다린다
    _sync_now(db)
    assert services.schedule(db) is None  # 방금 맞췄고 바뀐 것이 없다

    _make_object(client, admin, part, label="너트")
    assert services.schedule(db) == "changed"
    _sync_now(db)
    tomorrow = datetime.now(UTC) + services.FULL_EVERY + timedelta(minutes=1)
    assert services.schedule(db, now=tomorrow) == "full"  # 하루 한 번은 전부를 해시로
    _sync_now(db)

    monkeypatch.setattr(get_settings(), "embedding_backend", "off")
    assert services.schedule(db, now=tomorrow + services.FULL_EVERY) is None


def _type_id(slug: str) -> Any:
    from app.modules.ontology.models import ObjectType

    return select(ObjectType.id).where(ObjectType.slug == slug)


# --- 조각 — 기록의 고른 칸 · 문서의 절(ADR 0054) ---------------------------------------------


def test_절은_제목으로_나뉘고_id_는_화면과_같다() -> None:
    """제목 id 는 화면(`MarkdownView` 의 `slugOf`)과 같은 규칙 — 찾은 줄이 그 절로 바로 간다.
    코드 울타리 안의 「#」 은 제목이 아니고, 같은 제목이 또 나오면 「-2」."""
    body = (
        "머리말\n\n# 힌지 **내구** 백서\n\n## 1. 개요\n\n개요 글\n\n```\n# 코드\n```\n\n"
        "## 3.1 모서리 [찍힘](/o/x/y)\n\n찍힘 글\n\n#### 작은 제목\n\n그대로\n\n"
        "## 1. 개요\n\n또"
    )
    found = services.sections(body)
    assert [(one.heading, one.anchor) for one in found] == [
        ("", ""),
        ("힌지 내구 백서 › 1. 개요", "1-개요"),
        ("힌지 내구 백서 › 3.1 모서리 찍힘", "3-1-모서리-찍힘"),
        ("힌지 내구 백서 › 1. 개요", "1-개요-2"),
    ]
    assert "# 코드" in found[1].text  # 울타리 안은 글이다
    assert "#### 작은 제목" in found[2].text  # h4 는 절을 나누지 않는다
    assert services.slug_of("snake_case Ⅱ") == "snake-case-ⅱ"

    long = "## 긴 절\n\n" + "\n\n".join("가" * 600 for _ in range(4))
    chunks = services.sections(long)[0].text
    assert len(services._chunks(chunks)) == 2  # 1,500자 안으로 문단을 모은다


def test_기록은_고른_칸만_문서는_절마다_조각이_된다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    log_type = _make_type(client, admin, label="서비스 건", usage="log")
    for key, label, kind in (
        ("symptom", "고객 증상", "text_long"),
        ("memo", "메모", "text"),
        ("report", "보고서", "document"),
    ):
        _make_property(client, admin, log_type, key=key, label=label, data_type=kind)
    one = _make_object(
        client,
        admin,
        log_type,
        label="SR-1",
        properties={"symptom": "전지가 부풀었어요", "memo": "x"},
    )
    blank = _make_object(client, admin, log_type, label="SR-2", properties={"memo": "y"})
    client.put(
        f"/api/objects/{log_type}/{one['id']}/documents/report",
        json={"body": "# 분석\n\n## 원인\n\n셀 팽창", "base_revision": 0},
        headers=admin.headers,
    )
    _put(client, admin, {log_type: ["symptom", "report"]})
    _sync_now(db)

    sent = [text for batch in ollama for text in batch]
    # 고른 칸만 — 이름(SR-1) · 메모는 안 든다.
    assert "서비스 건 · 고객 증상: 전지가 부풀었어요" in sent
    assert not any("SR-2" in text or "메모" in text for text in sent)
    pieces = {
        (row.part, row.heading, row.anchor)
        for row in db.scalars(
            select(ObjectEmbedding).where(ObjectEmbedding.object_id == one["id"])
        )
    }
    assert pieces == {("", "", ""), ("doc:report:0", "분석 › 원인", "원인")}
    # 고른 칸이 빈 기록은 조각이 없어도 맞춰 본 것이다 — 5분마다 다시 보지 않는다.
    assert db.scalar(select(func.count()).where(ObjectEmbedding.object_id == blank["id"])) == 0
    assert services.needs_work(db) is False

    # 칸을 바꾸면 그 타입을 전부 다시 맞춘다.
    _put(client, admin, {log_type: ["memo"]})
    assert services.needs_work(db) is True


def test_같은_글은_다시_바꾸지_않고_검색에는_하나만_선다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    tag = uuid.uuid4().hex[:6]
    log_type = _make_type(client, admin, label="서비스 건", usage="log")
    _make_property(client, admin, log_type, key="symptom", label="고객 증상", data_type="text")
    for number in range(3):
        _make_object(
            client,
            admin,
            log_type,
            label=f"{tag}-{number}",
            properties={"symptom": "배터리 부풂"},
        )
    _put(client, admin, {log_type: ["symptom"]})
    _sync_now(db)
    assert sum(len(batch) for batch in ollama) == 1  # 같은 글 셋 — 한 번만 바꾼다

    found = _find(client, admin, "battery", meaning="true", type=log_type)
    assert len(found["meaning"]) == 1 and found["meaning"][0]["same"] == 2


def test_검색은_문서의_절을_가리킨다(
    client: TestClient, admin: Signed, db: Session, ollama: list[list[str]]
) -> None:
    paper = _make_type(client, admin, label="백서")
    _make_property(client, admin, paper, key="body", label="본문", data_type="document")
    made = _make_object(client, admin, paper, label="내구 백서")
    client.put(
        f"/api/objects/{paper}/{made['id']}/documents/body",
        json={
            "body": "## 시험\n\n볼트 조임\n\n## 3.2 덮개 내구\n\n덮개가 갈라짐",
            "base_revision": 0,
        },
        headers=admin.headers,
    )
    _put(client, admin, [paper])
    _sync_now(db)
    found = _find(client, admin, "cover", meaning="true", type=paper)
    assert [(one["id"], one["section"], one["anchor"]) for one in found["meaning"]] == [
        (made["id"], "3.2 덮개 내구", "3-2-덮개-내구")
    ]
