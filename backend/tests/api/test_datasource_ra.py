"""RA 보고서를 기록으로(ADR 0018) — 가짜 RA 를 httpx 에 끼워 소켓 없이 본다.

지키는 것: 조직 하나와 그 하위의 발행본만 · 본문까지 · 같은 제목이어도 따로 · 축 태그는
있는 SP 객체에만 · 증분은 커서에서 겹쳐 · 전량 대조에서 사라진 것은 지우지 않고 「원본에서
내려감」 · 다시 오면 돌아옴 · 한꺼번에 사라지면 표시하지 않고 멈춤.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.modules.datasources import services
from tests.api.conftest import Signed, finish_job
from tests.api.test_ontology import _make_object, _make_type, _uniq

BASE = "http://ra.local"
TOKEN = "Bearer ra_pat_test"


def _at(minutes: int) -> str:
    return (datetime(2026, 10, 1, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)).isoformat()


class FakeRA:
    """RA 흉내 — 조직 트리(`/api/workspaces`)와 발행 보고서 피드. 피드는 RA 와 같은 규칙으로
    거르고(게시판 · 하위 · 단계) `(changed_at, id)` 로 넘긴다."""

    def __init__(self, own_dept: str) -> None:
        self.orgs: list[dict[str, Any]] = [
            {
                "slug": "hq",
                "name": "본사",
                "parent_slug": None,
                "kind": "org",
                "virtual": False,
            },
            {
                "slug": "cae",
                "name": "CAE그룹",
                "parent_slug": "hq",
                "kind": "org",
                "virtual": False,
            },
            {
                "slug": own_dept,
                "name": "해석1팀",
                "parent_slug": "cae",
                "kind": "org",
                "virtual": False,
            },
            {
                "slug": "cae-2",
                "name": "해석2팀",
                "parent_slug": "cae",
                "kind": "org",
                "virtual": False,
            },
            {
                "slug": "sales",
                "name": "영업",
                "parent_slug": "hq",
                "kind": "org",
                "virtual": False,
            },
            {
                "slug": "vt",
                "name": "가상",
                "parent_slug": None,
                "kind": "org",
                "virtual": True,
            },
            {
                "slug": "me",
                "name": "나 (개인)",
                "parent_slug": None,
                "kind": "personal",
                "virtual": False,
            },
        ]
        self.reports: dict[int, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []

    def add(
        self,
        rid: int,
        title: str,
        *,
        board: str,
        changed: int,
        phase: str = "finalized",
        text: str | None = "본문",
        entities: list[dict[str, Any]] | None = None,
        workspace: str | None = None,
    ) -> None:
        names = {one["slug"]: one["name"] for one in self.orgs}
        self.reports[rid] = {
            "id": rid,
            "title": title,
            "url": f"{BASE}/w/{board}/reports/{rid}",
            "report_date": "2026-09-30",
            "created_at": _at(0),
            "changed_at": _at(changed),
            "revision": 1,
            "phase": phase,
            "report_type": "해석 보고서",
            "author": {"name": "홍길동", "email": "hong@example.com"},
            "workspace": {"slug": workspace or board, "name": names.get(workspace or board)},
            "boards": [{"slug": board, "name": names.get(board)}],
            "collab_workspaces": [],
            "tags": ["강성"],
            "entities": entities or [],
            "text": text,
        }

    def _subtree(self, root: str) -> set[str]:
        out = {root}
        grew = True
        while grew:
            grew = False
            for one in self.orgs:
                if one["parent_slug"] in out and one["slug"] not in out:
                    out.add(one["slug"])
                    grew = True
        return out

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != TOKEN:
            return httpx.Response(401, json={"success": False, "message": "no"})
        if request.url.path == "/api/workspaces":
            return httpx.Response(200, json={"success": True, "data": self.orgs})
        if request.url.path != "/api/feeds/published-reports":
            return httpx.Response(404, json={"success": False})
        query = {
            key: values[0] for key, values in parse_qs(request.url.query.decode()).items()
        }
        boards = (
            self._subtree(query["board"])
            if query.get("include_descendants") == "true"
            else {query["board"]}
        )
        phases = (
            {"finalized"} if query.get("phase") == "finalized" else {"reviewing", "finalized"}
        )
        rows = sorted(
            (
                one
                for one in self.reports.values()
                if one["phase"] in phases and {b["slug"] for b in one["boards"]} & boards
            ),
            key=lambda one: (one["changed_at"], one["id"]),
        )
        since = query.get("updated_since")
        if since:
            after = int(query.get("after_id") or 0)
            rows = [
                one
                for one in rows
                if one["changed_at"] > since
                or (one["changed_at"] == since and "after_id" in query and one["id"] > after)
            ]
        limit = int(query.get("limit") or 200)
        page, has_more = rows[:limit], len(rows) > limit
        items = []
        for one in page:
            item = dict(one)
            if query.get("include_text") != "true":
                item.pop("text")
            items.append(item)
        last = page[-1] if page else None
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": items,
                    "count": len(items),
                    "has_more": has_more,
                    "next": (
                        {"updated_since": last["changed_at"], "after_id": last["id"]}
                        if last
                        else None
                    ),
                    "mode": "incremental" if since else "full",
                },
            },
        )

    def feed_requests(self) -> list[dict[str, str]]:
        return [
            {k: v[0] for k, v in parse_qs(one.url.query.decode()).items()}
            for one in self.requests
            if one.url.path == "/api/feeds/published-reports"
        ]


@pytest.fixture
def ra(admin: Signed) -> Iterator[FakeRA]:
    fake = FakeRA(own_dept=admin.workspace)
    services.transport = httpx.MockTransport(fake)
    try:
        yield fake
    finally:
        services.transport = None


def _sync(client: TestClient, who: Signed, slug: str, *, apply: bool = True) -> dict[str, Any]:
    started = client.post(
        f"/api/datasources/{slug}/sync",
        params={"apply": "true" if apply else "false"},
        headers=who.headers,
    )
    assert started.status_code == 202, started.text
    done = finish_job(client, who, started.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def _setup(client: TestClient, admin: Signed, **options: Any) -> dict[str, Any]:
    """개발모델(축) 하나와 보고서 기록 타입(틀) · RA 보고서 소스."""
    model = _make_type(client, admin, label="개발모델", key_policy="required")
    made = client.post(
        "/api/datasources/ra-report-type",
        json={"slug": _uniq("report"), "label": "보고서", "axes": [model]},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    report = made.json()["type_slug"]
    source = client.post(
        "/api/datasources",
        json={
            "slug": _uniq("ra"),
            "name": "RA 보고서",
            "kind": "ra_reports",
            "base_url": BASE,
            "entity_set": "/api/feeds/published-reports",
            "auth_kind": "bearer",
            "auth_secret": TOKEN.removeprefix("Bearer "),
            "type_slug": report,
            "options": {"board": "cae", **options},
        },
        headers=admin.headers,
    )
    assert source.status_code == 201, source.text
    return {"model": model, "report": report, "source": source.json()["slug"]}


def _records(client: TestClient, who: Signed, report: str) -> dict[str, dict[str, Any]]:
    got = client.get(f"/api/objects/{report}", params={"limit": 200}, headers=who.headers)
    assert got.status_code == 200, got.text
    return {one["key"]: one for one in got.json()["items"]}


def _full(client: TestClient, admin: Signed, report: str, key: str) -> dict[str, Any]:
    listed = _records(client, admin, report)
    got = client.get(f"/api/objects/{report}/{listed[key]['id']}", headers=admin.headers)
    assert got.status_code == 200, got.text
    return dict(got.json()["object"])


def _age_reconcile(source_slug: str) -> None:
    """하루가 지난 것처럼 — 다음 동기화가 전량 대조를 한다."""
    from app.database import SessionLocal
    from app.modules.datasources.models import DataSource

    with SessionLocal() as db:
        db.execute(
            update(DataSource)
            .where(DataSource.slug == source_slug)
            .values(reconciled_at=datetime.now(UTC) - timedelta(days=2))
        )
        db.commit()


def test_보고서_기록_타입의_틀은_표준_칸과_축의_참조_칸을_한_번에_짓는다(
    client: TestClient, admin: Signed
) -> None:
    w = _setup(client, admin)
    listed = client.get("/api/ontology/types", headers=admin.headers)
    assert listed.status_code == 200, listed.text
    kind = next(one for one in listed.json() if one["slug"] == w["report"])
    props = client.get(f"/api/ontology/types/{w['report']}/properties", headers=admin.headers)
    keys = {one["key"]: one for one in props.json()}
    for key in ("ra_id", "url", "report_date", "phase", "body", "origin_state", "removed_on"):
        assert key in keys, key
    assert keys["body"]["data_type"] == "text_long"
    assert keys[f"ref_{w['model']}"]["ref_type_slug"] == w["model"]
    assert keys[f"ref_{w['model']}"]["multi"] is True
    assert kind["usage"] == "log"
    assert "properties.body" in kind["list_view"]["search"]
    # 다시 눌러도 된다 — 모자란 칸만 더한다.
    again = client.post(
        "/api/datasources/ra-report-type",
        json={"slug": w["report"], "label": "보고서", "axes": [w["model"]]},
        headers=admin.headers,
    )
    assert again.status_code == 201 and again.json()["created"] is False


def test_조직_하위의_발행본을_본문까지_넣고_같은_제목도_따로_둔다(
    client: TestClient, admin: Signed, ra: FakeRA
) -> None:
    w = _setup(client, admin)
    model = w["model"]
    _make_object(client, admin, model, key="M-100", label="모델 100")
    ra.add(
        1,
        "주간 보고",
        board=admin.workspace,
        changed=1,
        text="해석 결과: 응력 120MPa",
        entities=[
            {
                "type": "model",
                "type_label": "모델",
                "value": "모델 100",
                "code": "M-100",
                "sp": {"type": model, "key": "M-100"},
            },
            {
                "type": "model",
                "type_label": "모델",
                "value": "모델 999",
                "code": "M-999",
                "sp": {"type": model, "key": "M-999"},
            },
            {
                "type": "part",
                "type_label": "부품",
                "value": "브래킷",
                "code": None,
                "sp": None,
            },
        ],
    )
    ra.add(2, "주간 보고", board="cae-2", changed=2, workspace="cae-2")
    ra.add(3, "영업 보고", board="sales", changed=3)  # CAE 밖
    ra.add(4, "검토 중 보고", board="cae", changed=4, phase="reviewing")  # 발행본 아님

    preview = client.post(
        f"/api/datasources/{w['source']}/preview", headers=admin.headers
    ).json()
    assert preview["mapping_error"] is None
    assert {one["external_id"] for one in preview["mapped"]} == {"1", "2"}

    done = _sync(client, admin, w["source"])
    assert done["applied"] is True, done
    assert done["counts"]["create"] == 2 and done["counts"]["full_read"] == 1
    records = _records(client, admin, w["report"])
    assert set(records) == {"RA-1", "RA-2"}  # 같은 제목이어도 둘
    one = _full(client, admin, w["report"], "RA-1")
    props = one["properties"]
    assert props["body"] == "해석 결과: 응력 120MPa"
    assert props["origin_state"] == "게시 중" and props["phase"] == "발행"
    assert props["url"] == f"{BASE}/w/{admin.workspace}/reports/1"
    # 축 태그 — 있는 모델에만 참조, 없는 모델 · RA 전용 축은 글자로.
    assert len(props[f"ref_{model}"]) == 1
    assert set(props["ra_tags"]) == {"개발모델: M-999", "부품: 브래킷"}
    # 소유 부서 — RA 작성 부서와 같은 slug 의 SP 부서, 없으면 소스의 기본(전역).
    assert one["owner_workspace_slug"] == admin.workspace
    assert _full(client, admin, w["report"], "RA-2")["owner_workspace_slug"] is None
    # 모델에서 거꾸로 — 「이 모델의 보고서」(ADR 0017).
    fields = client.get(f"/api/objects/{model}/fields", headers=admin.headers).json()
    assert any(f["field"] == f"in.{w['report']}:ref_{model}" for f in fields)
    first = ra.feed_requests()[-1]
    assert first["board"] == "cae" and first["include_descendants"] == "true"
    assert first["phase"] == "finalized" and first["include_text"] == "true"


def test_증분은_커서에서_겹쳐_읽고_전량_대조는_지우지_않고_표시한다(
    client: TestClient, admin: Signed, ra: FakeRA
) -> None:
    w = _setup(client, admin)
    for rid in (1, 2, 3):
        ra.add(rid, f"보고 {rid}", board="cae-2", changed=rid)
    _sync(client, admin, w["source"])
    ra.requests.clear()

    # 고친 것과 새것만 — 커서에서 5분 겹쳐(경계에서 늦은 것을 놓치지 않게).
    ra.add(2, "보고 2", board="cae-2", changed=10, text="고친 본문")
    ra.add(4, "보고 4", board="cae-2", changed=11)
    done = _sync(client, admin, w["source"])
    asked = ra.feed_requests()[0]
    assert asked["updated_since"] == _at(3 - 5) and asked["include_text"] == "true"
    assert done["counts"]["full_read"] == 0
    assert done["counts"]["create"] == 1 and done["counts"]["update"] == 1
    assert _full(client, admin, w["report"], "RA-2")["properties"]["body"] == "고친 본문"

    # 하루가 지나 전량 대조 — 3 이 RA 에서 사라졌다. 본문 없이 읽어도 본문은 남는다.
    del ra.reports[3]
    _age_reconcile(w["source"])
    ra.requests.clear()
    done = _sync(client, admin, w["source"])
    asked = ra.feed_requests()[0]
    assert "updated_since" not in asked and "include_text" not in asked
    assert done["counts"]["gone"] == 1
    gone = _full(client, admin, w["report"], "RA-3")
    assert gone["status"] == "active"  # 지우지도 사용 중지하지도 않는다
    assert gone["properties"]["origin_state"] == "원본에서 내려감"
    assert gone["properties"]["removed_on"]
    assert _full(client, admin, w["report"], "RA-2")["properties"]["body"] == "고친 본문"

    # 다시 올라오면 「게시 중」 으로 돌아오고 내려간 날이 지워진다(증분으로도).
    ra.add(3, "보고 3", board="cae-2", changed=20)
    done = _sync(client, admin, w["source"])
    assert done["counts"]["back"] == 1
    back = _full(client, admin, w["report"], "RA-3")["properties"]
    assert back["origin_state"] == "게시 중" and "removed_on" not in back


def test_한꺼번에_사라지면_내려감을_적지_않고_멈춘다(
    client: TestClient, admin: Signed, ra: FakeRA
) -> None:
    """RA 계정의 권한이 바뀌거나 조직을 잘못 고르면 전량이 텅 빈다 — 그때 가진 것 전부를
    「내려감」 으로 적으면 안 된다."""
    w = _setup(client, admin)
    for rid in range(1, 14):
        ra.add(rid, f"보고 {rid}", board="cae-2", changed=rid)
    _sync(client, admin, w["source"])
    ra.reports.clear()
    _age_reconcile(w["source"])
    done = _sync(client, admin, w["source"])
    assert done["run"]["status"] == "failed"
    assert done["counts"]["gone_held_back"] == 13 and done["counts"]["gone"] == 0
    assert any("절반이 넘어" in str(one) for one in done["errors"])
    states = {
        one["properties"].get("origin_state")
        for one in _records(client, admin, w["report"]).values()
    }
    assert states == {"게시 중"}


def test_조직_트리와_설정을_거르고_사용_중지는_켤_수_없다(
    client: TestClient, admin: Signed, ra: FakeRA
) -> None:
    w = _setup(client, admin)
    boards = client.get(f"/api/datasources/{w['source']}/ra-boards", headers=admin.headers)
    assert boards.status_code == 200, boards.text
    shown = {one["slug"]: one for one in boards.json()}
    assert set(shown) == {"hq", "cae", admin.workspace, "cae-2", "sales"}  # 가상 · 개인 빼고
    assert (
        shown["cae-2"]["depth"] == 2 and shown["cae-2"]["path"] == "본사 / CAE그룹 / 해석2팀"
    )

    base = f"/api/datasources/{w['source']}"
    refused = client.patch(base, json={"deprecate_missing": True}, headers=admin.headers)
    assert refused.status_code == 422 and refused.json()["error"]["code"].endswith("0059")
    wrong = client.patch(
        base, json={"options": {"board": "cae", "phase": "draft"}}, headers=admin.headers
    )
    assert wrong.status_code == 422 and wrong.json()["error"]["code"].endswith("0050")
    mapped = client.patch(
        base, json={"mapping": {"external_key": "id"}}, headers=admin.headers
    )
    assert mapped.status_code == 422
    # 비밀이 틀리면 무엇을 고칠지 말한다.
    client.patch(base, json={"auth_secret": "wrong"}, headers=admin.headers)
    failed = _sync(client, admin, w["source"])
    assert failed["run"]["status"] == "failed"
    assert "토큰을 거절" in str(failed["errors"])
    # 한글이 섞인 토큰은 보내기 전에 깨진다 — 500 이 아니라 무엇을 고칠지.
    client.patch(base, json={"auth_secret": "토큰"}, headers=admin.headers)
    garbled = client.get(f"{base}/ra-boards", headers=admin.headers)
    assert garbled.status_code == 422 and garbled.json()["error"]["code"].endswith("0051")
    assert "영문" in str(_sync(client, admin, w["source"])["errors"])
    # 발행본만이 기본 — 게시된 것 전부로 넓힐 수 있다(코드 변경 없이).
    widened = client.patch(
        base,
        json={"options": {"board": "cae", "phase": "published"}, "auth_secret": "ra_pat_test"},
        headers=admin.headers,
    )
    assert widened.status_code == 200, widened.text
    ra.add(9, "검토 중", board="cae", changed=1, phase="reviewing")
    _sync(client, admin, w["source"])
    assert "RA-9" in _records(client, admin, w["report"])


def test_소스를_지우면_객체는_남는다(client: TestClient, admin: Signed, ra: FakeRA) -> None:
    w = _setup(client, admin)
    ra.add(1, "보고 1", board="cae-2", changed=1)
    _sync(client, admin, w["source"])
    assert (
        client.delete(f"/api/datasources/{w['source']}", headers=admin.headers).status_code
        == 204
    )
    assert set(_records(client, admin, w["report"])) == {"RA-1"}
