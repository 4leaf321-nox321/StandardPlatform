"""확장 `jajuverify` — 자주검증 회의 표(ADR 0044).

여기서 지키는 것:

- **켜야 있다** — 꺼진 설치에서는 경로도, 첨부 자리도, 자기소개도 없다.
- 회의 한 장은 묶음 · 줄을 표의 차례대로, 칸마다 사진과 함께 한 번에 준다. 목록은 일자가
  새것부터.
- 권한 — 보기는 누구나, 만들기는 부서 구성원. **쓰기(줄 · 묶음 · 사진 · 작성 완료 · 배치)는 그
  회의 부서의 구성원 누구나**, 관리(제목 · 일자 · 삭제 · 확정)는 만든 사람 · 부서 관리자 ·
  시스템 관리자(2026-10-31 사용자 결정). 화면의 `can_write` · `can_manage` 와 서버의 거절이
  같다.
- **여럿이 함께 쓴다** — 같은 칸은 `expected` 가 지금 값과 다르면 409(지금 값 · 누가 · 언제),
  다른 칸끼리는 둘 다 남는다. 판 번호가 쓰기마다 오르고 `pulse` 가 그것과 「지금 수정하는 다른
  사람(30초 안)」 을 준다. 확정된 회의는 쓰기가 전부 409(사진까지), 확정을 해제하면 다시 된다.
- 사진은 공통 첨부 — 이미지 판별 · 미리보기 · 응답 머리 · MCP 업로드 표가 그대로 따라오고, 칸이
  아닌 자리 · 이미지가 아닌 파일은 거절한다. 줄 · 회의를 지워도 사진은 남는다(고아 정리 대상이
  아니다), 사진을 떼면 하루 뒤 고아 정리가 파일을 지운다.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.extensions.jajuverify.models import JvMeeting, JvPresence, JvRow
from app.modules.accounts.models import User
from app.modules.audit.models import RequestTiming
from app.modules.files import gc
from app.modules.files.models import Attachment
from app.modules.server.models import ExtensionState
from app.modules.workspaces.models import Workspace
from app.shared.request_timing import writer
from tests.api.conftest import Signed, _login, _make_user

JV = "/api/ext/jajuverify"


@pytest.fixture(autouse=True)
def _on(client: TestClient, admin: Signed, db: Session) -> Iterator[None]:
    """확장을 켜고 시작한다 — CI 에는 `.env` 가 없어 기본값이 「꺼짐」 이다. 끝나면 켜짐을
    지운다(전역 상태라 다음 시험이 물려받으면 엉뚱한 곳에서 실패한다)."""
    client.patch(
        "/api/server/extensions/jajuverify", json={"enabled": True}, headers=admin.headers
    )
    yield
    db.execute(delete(ExtensionState))
    db.commit()


def _png(seed: int = 0, width: int = 64, height: int = 48) -> bytes:
    """내용이 시험마다 다른 사진 — 같은 내용이면 저장소의 파일 하나를 함께 가리킨다."""
    out = io.BytesIO()
    color = ((seed * 37) % 256, (seed * 91) % 256, 200)
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def _meeting(
    client: TestClient,
    who: Signed,
    *,
    kind: str = "review",
    title: str = "1차 심사회",
    held_on: str | None = "2026-10-28",
) -> dict[str, Any]:
    got = client.post(
        f"{JV}/meetings",
        json={
            "kind": kind,
            "title": title,
            "held_on": held_on,
            "workspace_slug": who.workspace,
        },
        headers=who.headers,
    )
    assert got.status_code == 201, got.text
    return dict(got.json())


def _row(client: TestClient, who: Signed, meeting_id: str, **values: Any) -> dict[str, Any]:
    got = client.post(f"{JV}/meetings/{meeting_id}/rows", json=values, headers=who.headers)
    assert got.status_code == 201, got.text
    return dict(got.json())


def _sheet(client: TestClient, who: Signed, meeting_id: str) -> dict[str, Any]:
    got = client.get(f"{JV}/meetings/{meeting_id}", headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _attach(
    client: TestClient,
    who: Signed,
    row_id: str,
    field: str | None,
    content: bytes,
    name: str = "캡처.png",
) -> Any:
    data = {"owner_table": "jv_rows", "owner_id": row_id}
    if field is not None:
        data["owner_field"] = field
    return client.post(
        "/api/attachments",
        data=data,
        files={"file": (name, io.BytesIO(content), "image/png")},
        headers=who.headers,
    )


def _peer(client: TestClient, db: Session, slug: str, *, role: str = "member") -> Signed:
    room = db.scalar(select(Workspace).where(Workspace.slug == slug))
    assert room is not None
    user = _make_user(db, room, label="peer", is_system_admin=False, role=role)
    return Signed(email=user.email, token=_login(client, user.email), workspace=slug)


def _outsider(client: TestClient, db: Session) -> Signed:
    far = Workspace(slug=f"far-{uuid.uuid4().hex[:8]}", name="먼 부서")
    db.add(far)
    db.commit()
    user = _make_user(db, far, label="stranger", is_system_admin=False, role="member")
    return Signed(email=user.email, token=_login(client, user.email), workspace=far.slug)


def _audit(client: TestClient, admin: Signed, action: str) -> list[dict[str, Any]]:
    got = client.get(f"/api/audit/entries?action={action}&limit=100", headers=admin.headers)
    assert got.status_code == 200, got.text
    return list(got.json()["items"])


# --- 켜기 ----------------------------------------------------------------------------


def test_끄면_경로도_사진_자리도_없다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """확장이 꺼진 설치에는 경로가 없고, 공통 첨부로 돌아 들어와도 그 자리가 없다."""
    meeting = _meeting(client, member)
    row = _row(client, member, meeting["id"], topic="힌지")
    client.patch(
        "/api/server/extensions/jajuverify", json={"enabled": False}, headers=admin.headers
    )
    assert client.get(f"{JV}/meetings?kind=review", headers=member.headers).status_code == 404
    assert (
        client.get(f"{JV}/meetings/{meeting['id']}", headers=admin.headers).status_code == 404
    )
    refused = _attach(client, member, row["id"], "topic", _png(1))
    assert refused.status_code == 404, refused.text
    # 켜면 그대로 있다 — 「끔」 은 감추는 것이다.
    client.patch(
        "/api/server/extensions/jajuverify", json={"enabled": True}, headers=admin.headers
    )
    assert [one["topic"] for one in _sheet(client, member, meeting["id"])["rows"]] == ["힌지"]


def test_확장_자리가_목록에_보이고_자기소개가_말한다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """MCP 는 확장마다 도구를 만들지 않는다 — `extensions_schema` 가 읽는 목록에 자리가 보이고,
    플랫폼 자기소개에 한 줄이 선다. 끄면 둘 다 없다."""

    def paths() -> set[str]:
        got = client.get("/api/server/extension-api", headers=member.headers).json()
        return {
            f"{one['method']} {one['path']}"
            for ext in got
            if ext["name"] == "jajuverify"
            for one in ext["endpoints"]
        }

    def facts() -> list[str]:
        got = client.get("/api/server/profile/live", headers=member.headers).json()
        return [
            line for one in got["facts"] if one["key"] == "jajuverify" for line in one["lines"]
        ]

    _meeting(client, member)
    assert {"GET meetings", "POST meetings", "PATCH rows/{row_id}"} <= paths()
    told = " ".join(facts())
    assert "자주검증 회의 표" in told and "심사회" in told and "설명회" in told
    client.patch(
        "/api/server/extensions/jajuverify", json={"enabled": False}, headers=admin.headers
    )
    assert paths() == set() and facts() == []


# --- 회의 · 줄 -----------------------------------------------------------------------


def test_한_장은_줄을_차례대로_칸마다_사진과_함께_준다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    meeting = _meeting(client, member, title="1차 심사회")
    assert meeting["kind_label"] == "심사회" and meeting["workspace_slug"] == member.workspace
    first = _row(
        client,
        member,
        meeting["id"],
        topic="도어 힌지",
        problem="열림 소음\n둘째 줄",
        action="그리스 변경",
        schedule=[
            {"date": "2026-11-30", "label": "재시험"},
            {"date": "2026-11-15", "label": "설계 변경"},
        ],
    )
    last = _row(client, member, meeting["id"], topic="래치")
    middle = _row(client, member, meeting["id"], topic="실링", after_id=first["id"])
    up = _attach(client, member, first["id"], "problem", _png(2, 64, 48))
    assert up.status_code == 201, up.text

    sheet = _sheet(client, member, meeting["id"])
    assert [(one["label"], one["kind"]) for one in sheet["cells"]] == [
        ("항목", "text"),
        ("문제점", "text"),
        ("개선대책", "text"),
        ("일정", "schedule"),
        ("비고", "text"),
    ]
    assert [one["topic"] for one in sheet["rows"]] == ["도어 힌지", "실링", "래치"]
    assert [one["id"] for one in sheet["rows"]] == [first["id"], middle["id"], last["id"]]
    row = sheet["rows"][0]
    assert row["problem"] == "열림 소음\n둘째 줄"  # 줄바꿈 그대로
    # 일정은 날짜 차례로, 완료 예정일은 그 가장 늦은 날(파생값).
    assert [one["label"] for one in row["schedule"]] == ["설계 변경", "재시험"]
    assert row["due_on"] == "2026-11-30" and row["author_name"] == "member"
    shots = row["photos"]["problem"]
    assert len(shots) == 1 and shots[0]["name"] == "캡처.png"
    assert (shots[0]["width"], shots[0]["height"]) == (64, 48)
    assert shots[0]["thumbnail_path"] == f"/attachments/{shots[0]['id']}/thumbnail"
    assert row["photos"]["topic"] == [] and row["photos"]["action"] == []
    assert sheet["can_write"] is True and sheet["write_refusal"] is None
    assert sheet["can_manage"] is True and sheet["groups"] == []
    assert row["group_id"] is None and row["done"] is False
    # 미리보기는 공통 첨부의 것 — WebP, 파일 응답 머리.
    thumb = client.get(f"/api{shots[0]['thumbnail_path']}", headers=member.headers)
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    assert thumb.headers["x-content-type-options"] == "nosniff"

    # 칸 하나씩 — 보낸 칸만 바뀐다(자동 저장). 일정은 [] 로 비운다.
    patched = client.patch(
        f"{JV}/rows/{first['id']}",
        json={"remark": "다음 회의에서 확인", "expected": {"remark": ""}},
        headers=member.headers,
    ).json()
    assert patched["remark"] == "다음 회의에서 확인" and patched["action"] == "그리스 변경"
    assert patched["due_on"] == "2026-11-30"
    cleared = client.patch(
        f"{JV}/rows/{first['id']}",
        json={"schedule": [], "expected": {"schedule": row["schedule"]}},
        headers=member.headers,
    ).json()
    assert cleared["due_on"] is None and cleared["schedule"] == []
    assert cleared["remark"] == "다음 회의에서 확인"
    head = client.patch(
        f"{JV}/meetings/{meeting['id']}",
        json={"held_on": None, "expected": {"held_on": "2026-10-28"}},
        headers=member.headers,
    ).json()
    assert head["held_on"] is None and head["title"] == "1차 심사회"
    assert any(
        one["target_id"] == first["id"]
        for one in _audit(client, admin, "jajuverify.row.update")
    )


def test_목록은_종류마다_일자가_새것부터_검색은_줄의_글까지(
    client: TestClient, member: Signed
) -> None:
    tag = uuid.uuid4().hex[:8]
    old = _meeting(client, member, title=f"{tag} 0차", held_on="2026-09-01")
    new = _meeting(client, member, title=f"{tag} 1차", held_on="2026-10-28")
    undated = _meeting(client, member, title=f"{tag} 날짜 없음", held_on=None)
    other = _meeting(client, member, kind="briefing", title=f"{tag} 설명회")
    _row(client, member, old["id"], problem=f"하우징 {tag}-균열")
    _row(client, member, old["id"], problem="다른 줄")

    got = client.get(f"{JV}/meetings?kind=review&q={tag}", headers=member.headers).json()
    assert [one["id"] for one in got["items"]] == [new["id"], old["id"], undated["id"]]
    assert next(one for one in got["items"] if one["id"] == old["id"])["row_count"] == 2
    found = client.get(
        f"{JV}/meetings?kind=review&q={tag}-균열", headers=member.headers
    ).json()
    assert [one["id"] for one in found["items"]] == [old["id"]]
    briefing = client.get(
        f"{JV}/meetings?kind=briefing&q={tag}", headers=member.headers
    ).json()
    assert [one["id"] for one in briefing["items"]] == [other["id"]]
    assert client.get(f"{JV}/meetings?kind=nope", headers=member.headers).status_code == 422


def test_옮기기는_상대_동작이라_그사이_줄이_늘고_줄어도_부딪치지_않는다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """**차례 때문에 409 가 나는 길이 없다**(사용자 요청, 2026-10-31) — 「이 줄을 한 칸 위로」
    · 「이 줄들을 저 줄 뒤로」 는 서버가 그때의 차례에 대고 적용한다. 그사이 사라진 줄 · 묶음은
    건너뛰고 알린다."""
    meeting = _meeting(client, member)
    mid = meeting["id"]
    url = f"{JV}/meetings/{mid}/move"
    a, b, c, d = (_row(client, member, mid, topic=name) for name in "ABCD")
    peer = _peer(client, db, member.workspace)

    def topics(sheet: dict[str, Any]) -> list[str]:
        return [one["topic"] for one in sheet["rows"]]

    down = client.post(url, json={"rows": [a["id"]], "step": "down"}, headers=member.headers)
    assert down.status_code == 200, down.text
    assert topics(down.json()) == ["B", "A", "C", "D"] and down.json()["changed"] is True
    # 끝에서 한 칸 더 — 바뀐 것이 없을 뿐 거절이 아니다.
    end = client.post(url, json={"rows": [d["id"]], "step": "down"}, headers=member.headers)
    assert end.status_code == 200 and end.json()["changed"] is False

    # 그사이 동료가 줄을 더하고 지웠다 — 내 화면은 그것을 모른 채 옮긴다.
    e = _row(client, peer, mid, topic="E")
    assert client.delete(f"{JV}/rows/{c['id']}", headers=peer.headers).status_code == 204
    placed = client.post(
        url,
        json={"rows": [c["id"], a["id"]], "to": {"group": None, "at": "start"}},
        headers=member.headers,
    )
    assert placed.status_code == 200, placed.text
    assert topics(placed.json()) == ["A", "B", "D", "E"]
    assert placed.json()["skipped"] == [c["id"]]
    after_b = client.post(
        url,
        json={"rows": [e["id"]], "to": {"group": None, "after": b["id"]}},
        headers=member.headers,
    )
    assert topics(after_b.json()) == ["A", "B", "E", "D"]

    # 기준 줄이 사라졌으면 그 묶음의 끝으로.
    made = client.post(
        f"{JV}/meetings/{mid}/groups",
        json={"title": "설계", "row_ids": [d["id"]]},
        headers=member.headers,
    ).json()
    design = made["group_id"]
    lost = client.post(
        url,
        json={"rows": [a["id"]], "to": {"group": design, "before": c["id"]}},
        headers=member.headers,
    ).json()
    assert lost["anchor_missing"] is True and c["id"] in lost["skipped"]
    assert [(one["topic"], one["group_id"]) for one in lost["rows"]][-2:] == [
        ("D", design),
        ("A", design),
    ]
    # 기준 줄이 살아 있으면 그 줄의 묶음이 이긴다 — B 를 D 앞에(묶음을 안 적어도).
    front = client.post(
        url,
        json={"rows": [b["id"]], "to": {"group": None, "before": d["id"]}},
        headers=member.headers,
    ).json()
    assert [one["topic"] for one in front["rows"] if one["group_id"] == design] == [
        "B",
        "D",
        "A",
    ]
    # 사라진 묶음으로는 옮기지 않는다 — 건너뛴다.
    gone = client.post(
        f"{JV}/meetings/{mid}/groups", json={"title": "곧 지움"}, headers=member.headers
    ).json()["group_id"]
    assert client.delete(f"{JV}/groups/{gone}", headers=peer.headers).status_code == 200
    skipped = client.post(
        url, json={"rows": [e["id"]], "to": {"group": gone}}, headers=member.headers
    ).json()
    assert skipped["skipped"] == [gone] and skipped["changed"] is False
    # 묶음 한 칸 — 사라진 묶음이면 건너뛴다.
    assert client.post(url, json={"group": gone, "step": "up"}, headers=member.headers).json()[
        "skipped"
    ] == [gone]
    # 모양이 틀린 부탁은 422 — 옛 통째 배치도.
    for bad in (
        {"rows": [a["id"], b["id"]], "step": "up"},
        {"step": "up"},
        {"rows": [a["id"]]},
        {"ungrouped": [a["id"]], "groups": []},
    ):
        assert client.post(url, json=bad, headers=member.headers).status_code == 422, bad
    assert any(one["target_id"] == mid for one in _audit(client, admin, "jajuverify.move"))


def test_회의를_지우면_목록과_한_장에서_사라지고_사진은_남는다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    meeting = _meeting(client, member)
    row = _row(client, member, meeting["id"], topic="힌지")
    assert _attach(client, member, row["id"], "topic", _png(3)).status_code == 201
    gone = client.delete(f"{JV}/meetings/{meeting['id']}", headers=member.headers)
    assert gone.status_code == 204
    assert (
        client.get(f"{JV}/meetings/{meeting['id']}", headers=member.headers).status_code == 404
    )
    listed = client.get(f"{JV}/meetings?kind=review", headers=member.headers).json()
    assert meeting["id"] not in {one["id"] for one in listed["items"]}
    db.expire_all()
    kept = db.get(JvMeeting, uuid.UUID(meeting["id"]))
    assert kept is not None and kept.deleted_at is not None  # 지우지 않는다
    photos = list(
        db.scalars(select(Attachment).where(Attachment.owner_id == uuid.UUID(row["id"])))
    )
    assert len(photos) == 1 and photos[0].deleted_at is None
    entry = next(
        one
        for one in _audit(client, admin, "jajuverify.meeting.delete")
        if one["target_id"] == meeting["id"]
    )
    assert entry["changes"]["rows"] == 1


# --- 권한 ------------------------------------------------------------------------


def test_부서_구성원은_누구나_줄_묶음_사진_완료를_고치고_머리_삭제_확정은_관리자만(
    client: TestClient, admin: Signed, member: Signed, manager: Signed, db: Session
) -> None:
    """**여럿이 나눠 쓰는 표**(2026-10-31) — 같은 부서의 일반 구성원도 남이 만든 회의의 줄 ·
    묶음 · 사진 · 작성 완료를 고친다(담당자 지정 없이). 회의의 머리 · 삭제 · 확정은 만든 사람 ·
    부서 관리자 · 시스템 관리자뿐이고, 다른 부서 사람은 보기만 한다."""
    meeting = _meeting(client, member)
    row = _row(client, member, meeting["id"], topic="힌지")
    peer = _peer(client, db, member.workspace)

    seen = _sheet(client, peer, meeting["id"])
    assert seen["can_write"] is True and seen["write_refusal"] is None
    assert seen["can_manage"] is False and "만든 사람" in (seen["manage_refusal"] or "")
    for done in (
        client.patch(
            f"{JV}/rows/{row['id']}",
            json={"problem": "동료가 씀", "expected": {"problem": ""}},
            headers=peer.headers,
        ),
        client.post(f"{JV}/meetings/{meeting['id']}/rows", json={}, headers=peer.headers),
        client.put(f"{JV}/rows/{row['id']}/done", json={"done": True}, headers=peer.headers),
        client.post(
            f"{JV}/meetings/{meeting['id']}/groups",
            json={"title": "설계 부문", "row_ids": [row["id"]]},
            headers=peer.headers,
        ),
        client.put(
            f"{JV}/meetings/{meeting['id']}/presence",
            json={"row_id": row["id"], "field": "problem"},
            headers=peer.headers,
        ),
        _attach(client, peer, row["id"], "problem", _png(4)),
    ):
        assert done.status_code in (200, 201, 204), done.text
    got = _sheet(client, member, meeting["id"])
    first = next(one for one in got["rows"] if one["id"] == row["id"])
    assert first["problem"] == "동료가 씀" and first["updated_by_name"] == "peer"
    assert first["done"] is True and first["done_by_name"] == "peer"
    assert first["group_id"] == got["groups"][0]["id"]
    assert len(first["photos"]["problem"]) == 1
    # 회의의 머리 · 삭제 · 확정은 관리 — 일반 구성원은 못 한다.
    for refused in (
        client.patch(
            f"{JV}/meetings/{meeting['id']}", json={"title": "x"}, headers=peer.headers
        ),
        client.delete(f"{JV}/meetings/{meeting['id']}", headers=peer.headers),
        client.post(f"{JV}/meetings/{meeting['id']}/confirm", headers=peer.headers),
        client.delete(f"{JV}/meetings/{meeting['id']}/confirm", headers=peer.headers),
    ):
        assert refused.status_code == 403, refused.text
        assert "만든 사람" in refused.json()["error"]["message"]
    # 같은 부서 구성원은 제 회의를 만든다.
    assert _meeting(client, peer, title="동료의 회의")["workspace_slug"] == member.workspace

    # 부서 관리자 · 시스템 관리자는 관리도 한다 — 남이 만든 회의라도.
    for boss in (manager, admin):
        sheet = _sheet(client, boss, meeting["id"])
        assert sheet["can_write"] is True and sheet["can_manage"] is True
        done = client.patch(
            f"{JV}/meetings/{meeting['id']}",
            json={"title": boss.email, "expected": {"title": sheet["meeting"]["title"]}},
            headers=boss.headers,
        )
        assert done.status_code == 200, done.text

    # 다른 부서 사람은 보기만 하고(ADR 0034), 쓰기 · 사진 · 지금 수정하는 칸도 못 적는다.
    outsider = _outsider(client, db)
    far = _sheet(client, outsider, meeting["id"])
    assert far["can_write"] is False and far["can_manage"] is False
    assert "구성원만" in (far["write_refusal"] or "")
    for refused in (
        client.patch(
            f"{JV}/rows/{row['id']}", json={"topic": "남의 줄"}, headers=outsider.headers
        ),
        client.post(f"{JV}/meetings/{meeting['id']}/rows", json={}, headers=outsider.headers),
        client.put(
            f"{JV}/rows/{row['id']}/done", json={"done": False}, headers=outsider.headers
        ),
        client.post(
            f"{JV}/meetings/{meeting['id']}/groups",
            json={"title": "남의 묶음"},
            headers=outsider.headers,
        ),
        client.put(
            f"{JV}/meetings/{meeting['id']}/presence",
            json={"row_id": row["id"], "field": "topic"},
            headers=outsider.headers,
        ),
        client.delete(f"{JV}/rows/{row['id']}", headers=outsider.headers),
        _attach(client, outsider, row["id"], "problem", _png(41)),
    ):
        assert refused.status_code == 403, refused.text
    elsewhere = client.post(
        f"{JV}/meetings",
        json={"kind": "review", "title": "남의 부서", "workspace_slug": member.workspace},
        headers=outsider.headers,
    )
    assert elsewhere.status_code == 403


def test_부서로_좁힌_설치에서는_남의_부서_회의가_없는_것과_같다(
    client: TestClient, member: Signed, db: Session, department_scope: None
) -> None:
    meeting = _meeting(client, member)
    row = _row(client, member, meeting["id"], topic="힌지")
    outsider = _outsider(client, db)
    assert (
        client.get(f"{JV}/meetings/{meeting['id']}", headers=outsider.headers).status_code
        == 404
    )
    listed = client.get(f"{JV}/meetings?kind=review", headers=outsider.headers).json()
    assert meeting["id"] not in {one["id"] for one in listed["items"]}
    assert (
        client.patch(
            f"{JV}/rows/{row['id']}", json={"topic": "x"}, headers=outsider.headers
        ).status_code
        == 404
    )


# --- 사진 --------------------------------------------------------------------------


def test_사진은_칸에만_이미지만_붙고_떼기까지_감사에_남는다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    meeting = _meeting(client, member)
    row = _row(client, member, meeting["id"], topic="힌지")

    not_cell = _attach(client, member, row["id"], "nope", _png(5))
    assert not_cell.status_code == 409 and "칸" in not_cell.json()["error"]["message"]
    fake = client.post(
        "/api/attachments",
        data={"owner_table": "jv_rows", "owner_id": row["id"], "owner_field": "remark"},
        files={"file": ("가짜.png", io.BytesIO(b"<html>not an image</html>"), "image/png")},
        headers=member.headers,
    )
    assert fake.status_code == 415
    missing = _attach(client, member, str(uuid.uuid4()), "topic", _png(6))
    assert missing.status_code == 404

    up = _attach(client, member, row["id"], "action", _png(7))
    assert up.status_code == 201, up.text
    photo = up.json()
    assert photo["is_image"] is True and photo["owner_field"] == "action"
    # 첨부의 부서는 회의의 부서를 따른다.
    assert (
        _sheet(client, member, meeting["id"])["rows"][0]["photos"]["action"][0]["id"]
        == photo["id"]
    )
    assert (
        client.delete(f"/api/attachments/{photo['id']}", headers=member.headers).status_code
        == 204
    )
    assert _sheet(client, member, meeting["id"])["rows"][0]["photos"]["action"] == []

    added = [
        one
        for one in _audit(client, admin, "jajuverify.photo.add")
        if one["target_id"] == row["id"]
    ]
    removed = [
        one
        for one in _audit(client, admin, "jajuverify.photo.remove")
        if one["target_id"] == row["id"]
    ]
    assert len(added) == 1 and len(removed) == 1
    assert removed[0]["changes"] == {"photos.action": {"before": "캡처.png", "after": None}}


def test_MCP_업로드_표가_칸에도_붙는다(client: TestClient, member: Signed) -> None:
    """AI 의 셸이 바이트를 직접 올리는 길(ADR 0012) — 표를 낼 때 자리를 확인한다."""
    meeting = _meeting(client, member)
    row = _row(client, member, meeting["id"], topic="힌지")
    bad = client.post(
        "/api/attachments/tickets",
        json={"owner_table": "jv_rows", "owner_id": row["id"], "owner_field": "nope"},
        headers=member.headers,
    )
    assert bad.status_code == 409
    issued = client.post(
        "/api/attachments/tickets",
        json={
            "owner_table": "jv_rows",
            "owner_id": row["id"],
            "owner_field": "problem",
            "filename": "현장.png",
        },
        headers=member.headers,
    )
    assert issued.status_code == 201, issued.text
    put = client.put(
        "/api/attachments/upload",
        content=_png(8),
        headers={"X-Upload-Ticket": issued.json()["ticket"]},
    )
    assert put.status_code == 201, put.text
    shots = _sheet(client, member, meeting["id"])["rows"][0]["photos"]["problem"]
    assert [one["name"] for one in shots] == ["현장.png"]


def _content_path(store: Path, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    return f"{digest[:2]}/{digest[2:4]}/{digest}"


def _age(store: Path) -> None:
    then = time.time() - 2 * 24 * 3600
    for path in store.rglob("*"):
        if path.is_file():
            os.utime(path, (then, then))


def test_줄을_지워도_사진은_남고_뗀_사진만_고아_정리가_지운다(
    client: TestClient,
    member: Signed,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**지우지 않는다** — 지운 줄의 사진(첨부 행)은 살아 있어 파일이 고아가 아니다(객체의
    휴지통과 같은 규칙). 사진을 떼면 아무도 안 가리키는 파일이 되고 하루 뒤 고아 정리가
    지운다."""
    store = tmp_path / "filestore"
    store.mkdir()
    monkeypatch.setattr(get_settings(), "filestore_dir", store)
    meeting = _meeting(client, member)
    kept_row = _row(client, member, meeting["id"], topic="지울 줄")
    other_row = _row(client, member, meeting["id"], topic="남는 줄")
    kept_body, cut_body = _png(9), _png(10)
    assert _attach(client, member, kept_row["id"], "topic", kept_body).status_code == 201
    cut = _attach(client, member, other_row["id"], "remark", cut_body)
    assert cut.status_code == 201

    gone = client.delete(f"{JV}/rows/{kept_row['id']}", headers=member.headers)
    assert gone.status_code == 204
    assert [one["topic"] for one in _sheet(client, member, meeting["id"])["rows"]] == [
        "남는 줄"
    ]
    db.expire_all()
    deleted = db.get(JvRow, uuid.UUID(kept_row["id"]))
    assert deleted is not None and deleted.deleted_at is not None
    # 지운 줄의 사진은 더 못 붙이고 못 뗀다(없는 자리) — 행은 남는다.
    assert _attach(client, member, kept_row["id"], "topic", _png(11)).status_code == 404

    assert (
        client.delete(
            f"/api/attachments/{cut.json()['id']}", headers=member.headers
        ).status_code
        == 204
    )
    _age(store)
    orphans = {path for path, _ in gc.scan(db).orphans}
    assert _content_path(store, cut_body) in orphans
    assert _content_path(store, kept_body) not in orphans


# --- 부서 · 기계 자격 ------------------------------------------------------------------


def test_부서를_지우려_하면_회의가_걸린다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """FK 가 RESTRICT 라 등록하지 않으면 부서 삭제가 500 이다 — 확인 창에 「자주검증 회의」 가
    막는 참조로 뜬다."""
    _meeting(client, member)
    got = client.get(f"/api/workspaces/{member.workspace}/references", headers=admin.headers)
    assert got.status_code == 200, got.text
    line = next(one for one in got.json() if one["table"] == "jv_meetings")
    assert line["label"] == "자주검증 회의" and line["count"] >= 1 and line["blocks_delete"]


def test_부서를_합치면_회의가_옮겨_간다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """통폐합(작업)이 회의를 옮긴다 — 안 옮기면 원래 부서를 비울 수 없어 지우지도 못한다.
    확장이 꺼져 있어도 회의가 있으면 옮길 종류로 선다(그 부서를 비워야 하므로)."""
    from tests.api.test_workspace_reassign_job import reassign

    tag = uuid.uuid4().hex[:6]
    for slug in (f"js{tag}", f"jt{tag}"):
        made = client.post(
            "/api/workspaces", json={"slug": slug, "name": slug}, headers=admin.headers
        )
        assert made.status_code == 201, made.text
    got = client.post(
        f"{JV}/meetings",
        json={"kind": "briefing", "title": "옮길 회의", "workspace_slug": f"js{tag}"},
        headers=admin.headers,
    )
    assert got.status_code == 201, got.text
    client.patch(
        "/api/server/extensions/jajuverify", json={"enabled": False}, headers=admin.headers
    )
    kinds = client.get(f"/api/workspaces/js{tag}/contents", headers=admin.headers).json()
    assert next(one for one in kinds if one["kind"] == "jv_meetings")["count"] == 1
    done = reassign(client, admin, f"js{tag}", f"jt{tag}", ["jv_meetings"])
    assert done["moved"] == {"jv_meetings": 1}
    db.expire_all()
    moved = db.get(JvMeeting, uuid.UUID(got.json()["id"]))
    target = db.scalar(select(Workspace).where(Workspace.slug == f"jt{tag}"))
    assert moved is not None and target is not None and moved.workspace_id == target.id


def test_기계_자격은_범위가_있어야_고친다(client: TestClient, member: Signed) -> None:
    """등록한 범위(`jajuverify:write`)가 없는 토큰은 읽기만 한다 — 모르는 것은 막는다."""

    def token(scopes: list[str]) -> dict[str, str]:
        got = client.post(
            "/api/auth/tokens",
            json={"name": f"jv-{uuid.uuid4().hex[:6]}", "scopes": scopes},
            headers=member.headers,
        )
        assert got.status_code == 201, got.text
        return {"Authorization": f"Bearer {got.json()['token']}"}

    reader = token(["read"])
    assert client.get(f"{JV}/meetings?kind=review", headers=reader).status_code == 200
    body = {"kind": "review", "title": "토큰", "workspace_slug": member.workspace}
    assert client.post(f"{JV}/meetings", json=body, headers=reader).status_code == 403
    writer = token(["read", "jajuverify:write"])
    assert client.post(f"{JV}/meetings", json=body, headers=writer).status_code == 201


# --- 여럿이 나눠 쓰기(2026-10-31) ----------------------------------------------------


def _user_id(db: Session, who: Signed) -> uuid.UUID:
    found = db.scalar(select(User.id).where(User.email == who.email))
    assert found is not None
    return found


def _pulse(client: TestClient, who: Signed, meeting_id: str) -> dict[str, Any]:
    got = client.get(f"{JV}/meetings/{meeting_id}/pulse", headers=who.headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _clash(response: Any) -> dict[str, Any]:
    """덮어쓰기 충돌 — 409 와 그 `details`."""
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"].endswith("JAJUVERIFY-0010"), error
    return dict(error["details"])


def test_같은_칸은_본_값이_다르면_409_다른_칸은_둘_다_남는다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """**칸 단위 덮어쓰기 보호** — 둘이 같은 값을 보고 시작해 같은 칸을 저장하면 나중 저장이
    거절되고(지금 값 · 누가 · 언제), 다른 칸끼리는 부딪치지 않는다. 본 값을 지금 값으로 다시
    보내면(「내 글로 덮어쓰기」) 지나가고, **`expected` 를 안 보내면 422** 다(AI · 스크립트도
    읽고 나서 쓴다 — 사용자 결정, 2026-10-31)."""
    meeting = _meeting(client, member)
    row = _row(client, member, meeting["id"], topic="힌지", problem="처음")
    peer = _peer(client, db, member.workspace)
    url = f"{JV}/rows/{row['id']}"

    first = client.patch(
        url,
        json={"problem": "동료의 글", "expected": {"problem": "처음"}},
        headers=peer.headers,
    )
    assert first.status_code == 200, first.text
    other = client.patch(
        url, json={"remark": "내 비고", "expected": {"remark": ""}}, headers=member.headers
    )
    assert other.status_code == 200, other.text

    clash = client.patch(
        url, json={"problem": "내 글", "expected": {"problem": "처음"}}, headers=member.headers
    )
    details = _clash(clash)
    assert details["row_id"] == row["id"] and details["field"] == "problem"
    assert details["current"] == "동료의 글" and details["updated_by"] == "peer"
    assert details["updated_at"]
    assert "peer 님이" in clash.json()["error"]["message"]
    kept = _sheet(client, member, meeting["id"])["rows"][0]
    assert (kept["problem"], kept["remark"]) == ("동료의 글", "내 비고")

    # 「내 글로 덮어쓰기」 — 본 값을 지금 값으로 다시 보낸다.
    again = client.patch(
        url,
        json={"problem": "내 글", "expected": {"problem": "동료의 글"}},
        headers=member.headers,
    )
    assert again.status_code == 200 and again.json()["problem"] == "내 글"
    # `expected` 가 없으면 422 — 아무것도 바꾸지 않고 무엇이 빠졌는지 말한다.
    plain = client.patch(url, json={"problem": "덮음"}, headers=peer.headers)
    assert plain.status_code == 422, plain.text
    error = plain.json()["error"]
    assert (
        error["code"].endswith("JAJUVERIFY-0014")
        and "먼저 한 장을 읽으세요" in error["message"]
    )
    assert error["details"]["missing"] == ["problem"]
    partly = client.patch(
        url,
        json={"problem": "덮음", "remark": "x", "expected": {"remark": "내 비고"}},
        headers=peer.headers,
    )
    assert partly.status_code == 422 and partly.json()["error"]["details"]["missing"] == [
        "problem"
    ]
    assert _sheet(client, member, meeting["id"])["rows"][0]["remark"] == "내 비고"
    plain = client.patch(
        url, json={"problem": "덮음", "expected": {"problem": "내 글"}}, headers=peer.headers
    )
    assert plain.status_code == 200 and plain.json()["problem"] == "덮음"
    # 옛 칸(`due_on`)이나 오타는 말없이 버리지 않는다.
    old = client.patch(
        url, json={"due_on": "2026-11-01", "expected": {}}, headers=peer.headers
    )
    assert old.status_code == 422, old.text
    assert "due_on — 모르는 칸입니다" in old.json()["error"]["message"]
    # 지금 값이 보내는 값과 같으면 부딪칠 것이 없다.
    same = client.patch(
        url, json={"problem": "덮음", "expected": {"problem": "옛날"}}, headers=member.headers
    )
    assert same.status_code == 200, same.text

    # 일정도 같다 — 목록 통째로 견준다(비어 있던 것을 본 채로). 같은 단계를 다른 차례로 보내도
    # 같은 목록이다.
    steps = [
        {"date": "2026-11-30", "label": "재시험"},
        {"date": "2026-11-01", "label": "변경"},
    ]
    put = client.patch(
        url, json={"schedule": steps, "expected": {"schedule": []}}, headers=peer.headers
    )
    assert put.status_code == 200 and put.json()["due_on"] == "2026-11-30"
    due = client.patch(
        url,
        json={"schedule": [{"date": "2026-12-01"}], "expected": {"schedule": []}},
        headers=member.headers,
    )
    assert _clash(due)["current"] == [
        {"date": "2026-11-01", "label": "변경"},
        {"date": "2026-11-30", "label": "재시험"},
    ]
    reordered = client.patch(
        url,
        json={"schedule": [{"date": "2026-12-01"}], "expected": {"schedule": steps}},
        headers=member.headers,
    )
    assert reordered.status_code == 200, reordered.text
    assert reordered.json()["schedule"] == [{"date": "2026-12-01", "label": ""}]

    # 묶음 이름 · 회의 제목도 같은 규칙.
    made = client.post(
        f"{JV}/meetings/{meeting['id']}/groups", json={"title": "설계"}, headers=member.headers
    )
    assert made.status_code == 201, made.text
    group_id = made.json()["groups"][0]["id"]
    renamed = client.patch(
        f"{JV}/groups/{group_id}",
        json={"title": "설계 부문", "expected": {"title": "설계"}},
        headers=peer.headers,
    )
    assert renamed.status_code == 200 and renamed.json()["title"] == "설계 부문"
    group_clash = _clash(
        client.patch(
            f"{JV}/groups/{group_id}",
            json={"title": "설계 파트", "expected": {"title": "설계"}},
            headers=member.headers,
        )
    )
    assert group_clash["group_id"] == group_id and group_clash["current"] == "설계 부문"
    assert (
        client.patch(
            f"{JV}/meetings/{meeting['id']}",
            json={"title": "관리자 제목", "expected": {"title": "1차 심사회"}},
            headers=admin.headers,
        ).status_code
        == 200
    )
    head = _clash(
        client.patch(
            f"{JV}/meetings/{meeting['id']}",
            json={"title": "2차 심사회", "expected": {"title": "1차 심사회"}},
            headers=member.headers,
        )
    )
    assert head["meeting_id"] == meeting["id"] and head["current"] == "관리자 제목"
    assert head["updated_by"] == "admin"
    # 묶음 이름 · 회의 제목 · 일자도 본 값이 없으면 422.
    for bare in (
        client.patch(f"{JV}/groups/{group_id}", json={"title": "x"}, headers=member.headers),
        client.patch(
            f"{JV}/meetings/{meeting['id']}", json={"held_on": None}, headers=member.headers
        ),
    ):
        assert bare.status_code == 422, bare.text


def test_판_번호가_쓰기마다_오르고_pulse_에_보인다(client: TestClient, member: Signed) -> None:
    """화면의 자동 반영은 이 번호만 묻는다 — 쓰기마다 오르고, 바뀐 것이 없는 수정과 「지금
    수정하는 칸」 은 올리지 않는다. 쓰기의 응답에 그 쓰기의 번호가 실린다(내 쓰기 때문에 다시
    읽지 않게)."""
    meeting = _meeting(client, member)
    mid = meeting["id"]
    start = _pulse(client, member, mid)
    assert start["confirmed"] is False and start["presence"] == []
    base = int(start["revision"])

    def now() -> int:
        return int(_pulse(client, member, mid)["revision"])

    row = _row(client, member, mid, topic="힌지")
    assert row["revision"] == base + 1 == now()
    patched = client.patch(
        f"{JV}/rows/{row['id']}",
        json={"problem": "소음", "expected": {"problem": ""}},
        headers=member.headers,
    )
    assert patched.json()["revision"] == base + 2 == now()
    # 바뀐 것이 없는 수정은 판을 올리지 않는다.
    client.patch(
        f"{JV}/rows/{row['id']}",
        json={"problem": "소음", "expected": {"problem": "소음"}},
        headers=member.headers,
    )
    assert now() == base + 2
    done = client.put(
        f"{JV}/rows/{row['id']}/done", json={"done": True}, headers=member.headers
    )
    assert done.json()["revision"] == base + 3 == now()
    made = client.post(
        f"{JV}/meetings/{mid}/groups",
        json={"title": "설계", "row_ids": [row["id"]]},
        headers=member.headers,
    )
    assert made.json()["meeting"]["revision"] == base + 4 == now()
    group_id = made.json()["groups"][0]["id"]
    # 옮기기 — 그대로면 안 오르고, 바뀌면 오른다.
    same = {"rows": [row["id"]], "to": {"group": group_id, "at": "end"}}
    client.post(f"{JV}/meetings/{mid}/move", json=same, headers=member.headers)
    assert now() == base + 4
    moved = {"rows": [row["id"]], "to": {"group": None}}
    laid = client.post(f"{JV}/meetings/{mid}/move", json=moved, headers=member.headers)
    assert laid.status_code == 200 and laid.json()["meeting"]["revision"] == base + 5
    # 사진도 남의 화면이 받아야 한다.
    assert _attach(client, member, row["id"], "topic", _png(50)).status_code == 201
    assert now() == base + 6
    assert _sheet(client, member, mid)["rows"][0]["revision"] == base + 6
    head = client.patch(
        f"{JV}/meetings/{mid}",
        json={"held_on": None, "expected": {"held_on": "2026-10-28"}},
        headers=member.headers,
    )
    assert head.json()["revision"] == base + 7 == now()
    # 지금 수정하는 칸은 내용이 아니다.
    touched = client.put(
        f"{JV}/meetings/{mid}/presence",
        json={"row_id": row["id"], "field": "topic"},
        headers=member.headers,
    )
    assert touched.status_code == 204 and now() == base + 7
    assert _sheet(client, member, mid)["meeting"]["revision"] == base + 7


def test_지금_수정하는_사람은_남만_30초_안의_것만_준다(
    client: TestClient, member: Signed, db: Session
) -> None:
    """칸에 들어가면 적고, `pulse` 는 **다른 사람의 30초 안의 것만** 준다. 시계를 기다리지
    않는다 — `seen_at` 을 SQL 로 과거로 고친다(이 기계의 벽시계는 뒤로 튄다 — 그래서 넉넉히
    1분)."""
    meeting = _meeting(client, member)
    mid = meeting["id"]
    row = _row(client, member, mid, topic="힌지")
    peer = _peer(client, db, member.workspace)
    url = f"{JV}/meetings/{mid}/presence"

    assert (
        client.put(
            url, json={"row_id": row["id"], "field": "problem"}, headers=peer.headers
        ).status_code
        == 204
    )
    assert (
        client.put(
            url, json={"row_id": row["id"], "field": "remark"}, headers=member.headers
        ).status_code
        == 204
    )
    seen = _pulse(client, member, mid)["presence"]
    assert [(one["name"], one["row_id"], one["field"]) for one in seen] == [
        ("peer", row["id"], "problem")
    ]
    assert [one["name"] for one in _pulse(client, peer, mid)["presence"]] == ["member"]

    # 30초가 지난 것은 빠진다.
    db.execute(
        update(JvPresence)
        .where(JvPresence.user_id == _user_id(db, peer))
        .values(seen_at=func.now() - timedelta(minutes=1))
    )
    db.commit()
    assert _pulse(client, member, mid)["presence"] == []
    # 다시 들어오면 다시 보인다(같은 줄을 고쳐 쓴다 — 사람마다 한 줄).
    client.put(url, json={"row_id": row["id"], "field": "action"}, headers=peer.headers)
    assert [one["field"] for one in _pulse(client, member, mid)["presence"]] == ["action"]
    # 나가면 지운다.
    assert client.delete(url, headers=member.headers).status_code == 204
    assert _pulse(client, peer, mid)["presence"] == []
    # 이 회의에 없는 줄 · 칸이 아닌 이름은 거절한다.
    assert (
        client.put(
            url, json={"row_id": str(uuid.uuid4()), "field": "topic"}, headers=peer.headers
        ).status_code
        == 404
    )
    assert (
        client.put(
            url, json={"row_id": row["id"], "field": "nope"}, headers=peer.headers
        ).status_code
        == 422
    )


def test_묶음은_머리줄과_그_아래_줄_만들기_옮기기_지우기(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """**묶음 없는 줄이 맨 위**, 그 아래 묶음이 차례대로 — 고른 줄로 새 묶음을 만들고, 배치로
    옮기고, 묶음만 해제하거나 줄까지 지운다."""
    meeting = _meeting(client, member)
    mid = meeting["id"]
    a, b, c, _d = (_row(client, member, mid, topic=name) for name in "ABCD")

    def layout_of(sheet: dict[str, Any]) -> list[tuple[str, str | None]]:
        titles = {one["id"]: one["title"] for one in sheet["groups"]}
        return [(one["topic"], titles.get(one["group_id"])) for one in sheet["rows"]]

    # 고른 줄(C, A)은 표의 차례대로 그 묶음에 선다.
    made = client.post(
        f"{JV}/meetings/{mid}/groups",
        json={"title": " 설계 부문 ", "row_ids": [c["id"], a["id"]]},
        headers=member.headers,
    )
    assert made.status_code == 201, made.text
    design = made.json()["groups"][0]["id"]
    assert layout_of(made.json()) == [
        ("B", None),
        ("D", None),
        ("A", "설계 부문"),
        ("C", "설계 부문"),
    ]
    empty = client.post(
        f"{JV}/meetings/{mid}/groups", json={"title": "품질 부문"}, headers=member.headers
    ).json()
    quality = empty["groups"][1]["id"]
    assert [one["title"] for one in empty["groups"]] == ["설계 부문", "품질 부문"]

    # 줄 추가 — 그 묶음의 맨 아래, 또는 그 줄 아래(그 줄의 묶음).
    e = _row(client, member, mid, topic="E", group_id=quality)
    f = _row(client, member, mid, topic="F", after_id=a["id"])
    assert e["group_id"] == quality and f["group_id"] == design
    assert layout_of(_sheet(client, member, mid))[2:] == [
        ("A", "설계 부문"),
        ("F", "설계 부문"),
        ("C", "설계 부문"),
        ("E", "품질 부문"),
    ]

    # 옮기기 — 묶음 차례를 바꾸고 B 를 품질 부문의 맨 아래로.
    up = client.post(
        f"{JV}/meetings/{mid}/move",
        json={"group": quality, "step": "up"},
        headers=member.headers,
    )
    assert up.status_code == 200, up.text
    laid = client.post(
        f"{JV}/meetings/{mid}/move",
        json={"rows": [b["id"]], "to": {"group": quality, "at": "end"}},
        headers=member.headers,
    )
    assert laid.status_code == 200, laid.text
    assert layout_of(laid.json()) == [
        ("D", None),
        ("E", "품질 부문"),
        ("B", "품질 부문"),
        ("A", "설계 부문"),
        ("F", "설계 부문"),
        ("C", "설계 부문"),
    ]
    # 맨 위 묶음을 한 칸 더 위로 — 그대로일 뿐 거절이 아니다.
    top = client.post(
        f"{JV}/meetings/{mid}/move",
        json={"group": quality, "step": "up"},
        headers=member.headers,
    )
    assert top.status_code == 200 and top.json()["changed"] is False

    # 묶음만 해제 — 그 줄은 묶음 없음의 맨 아래로.
    loose = client.delete(f"{JV}/groups/{quality}", headers=member.headers)
    assert loose.status_code == 200, loose.text
    assert layout_of(loose.json()) == [
        ("D", None),
        ("E", None),
        ("B", None),
        ("A", "설계 부문"),
        ("F", "설계 부문"),
        ("C", "설계 부문"),
    ]
    # 지운 묶음에는 더 못 넣는다.
    gone = client.post(
        f"{JV}/meetings/{mid}/rows", json={"group_id": quality}, headers=member.headers
    )
    assert gone.status_code == 404
    # 줄까지 삭제.
    wiped = client.delete(f"{JV}/groups/{design}?with_rows=true", headers=member.headers)
    assert wiped.status_code == 200, wiped.text
    assert layout_of(wiped.json()) == [("D", None), ("E", None), ("B", None)]
    assert wiped.json()["groups"] == []

    # 감사 — 묶음 만들기 · 배치 · 삭제(줄 수 · 줄까지인지).
    deleted = [
        one["changes"]
        for one in _audit(client, admin, "jajuverify.group.delete")
        if one["target_id"] in (quality, design)
    ]
    assert sorted((one["rows"], one["with_rows"]) for one in deleted) == [
        (2, False),
        (3, True),
    ]
    assert any(one["target_id"] == mid for one in _audit(client, admin, "jajuverify.move"))
    assert any(
        one["target_id"] == design for one in _audit(client, admin, "jajuverify.group.create")
    )


def test_확정하면_그_회의의_쓰기가_전부_409_해제하면_다시(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """확정은 관리(만든 사람 · 부서 관리자 · 시스템 관리자)가 하고, 그 뒤 쓰기 — 줄 · 묶음 ·
    사진 · 작성 완료 · 머리 · 배치 · 지금 수정하는 칸 · 회의 삭제 — 가 전부 409 다. 해제하면
    다시 된다."""
    meeting = _meeting(client, member)
    mid = meeting["id"]
    row = _row(client, member, mid, topic="힌지")
    photo = _attach(client, member, row["id"], "topic", _png(60))
    assert photo.status_code == 201
    made = client.post(
        f"{JV}/meetings/{mid}/groups", json={"title": "설계"}, headers=member.headers
    ).json()
    group_id = made["groups"][0]["id"]
    peer = _peer(client, db, member.workspace)
    client.put(
        f"{JV}/meetings/{mid}/presence",
        json={"row_id": row["id"], "field": "topic"},
        headers=peer.headers,
    )

    confirmed = client.post(f"{JV}/meetings/{mid}/confirm", headers=member.headers)
    assert confirmed.status_code == 200, confirmed.text
    sheet = confirmed.json()
    assert (
        sheet["meeting"]["confirmed_at"] and sheet["meeting"]["confirmed_by_name"] == "member"
    )
    assert sheet["can_write"] is False and "확정" in (sheet["write_refusal"] or "")
    assert sheet["can_manage"] is True
    seen = _pulse(client, member, mid)
    assert seen["confirmed"] is True and seen["presence"] == []  # 수정 중 표시도 지운다

    move = {"rows": [row["id"]], "to": {"group": group_id}}
    for refused in (
        client.patch(
            f"{JV}/rows/{row['id']}",
            json={"topic": "x", "expected": {"topic": "힌지"}},
            headers=peer.headers,
        ),
        client.post(f"{JV}/meetings/{mid}/rows", json={}, headers=peer.headers),
        client.put(f"{JV}/rows/{row['id']}/done", json={"done": True}, headers=peer.headers),
        client.delete(f"{JV}/rows/{row['id']}", headers=peer.headers),
        client.post(f"{JV}/meetings/{mid}/groups", json={"title": "y"}, headers=peer.headers),
        client.patch(
            f"{JV}/groups/{group_id}",
            json={"title": "z", "expected": {"title": "설계"}},
            headers=peer.headers,
        ),
        client.delete(f"{JV}/groups/{group_id}", headers=peer.headers),
        client.post(f"{JV}/meetings/{mid}/move", json=move, headers=peer.headers),
        client.put(
            f"{JV}/meetings/{mid}/presence",
            json={"row_id": row["id"], "field": "topic"},
            headers=peer.headers,
        ),
        client.patch(
            f"{JV}/meetings/{mid}",
            json={"title": "확정 뒤", "expected": {"title": "1차 심사회"}},
            headers=member.headers,
        ),
        client.delete(f"{JV}/meetings/{mid}", headers=member.headers),
        _attach(client, peer, row["id"], "problem", _png(61)),
        client.delete(f"/api/attachments/{photo.json()['id']}", headers=peer.headers),
    ):
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"].endswith("JAJUVERIFY-0009")
    # 확정 해제도 관리 — 일반 구성원은 못 한다.
    assert (
        client.delete(f"{JV}/meetings/{mid}/confirm", headers=peer.headers).status_code == 403
    )
    listed = client.get(f"{JV}/meetings?kind=review", headers=member.headers).json()
    assert next(one for one in listed["items"] if one["id"] == mid)["confirmed_at"]

    opened = client.delete(f"{JV}/meetings/{mid}/confirm", headers=member.headers)
    assert opened.status_code == 200 and opened.json()["can_write"] is True
    assert opened.json()["meeting"]["confirmed_at"] is None
    again = client.patch(
        f"{JV}/rows/{row['id']}",
        json={"topic": "다시", "expected": {"topic": "힌지"}},
        headers=peer.headers,
    )
    assert again.status_code == 200, again.text
    assert any(
        one["target_id"] == mid for one in _audit(client, admin, "jajuverify.meeting.confirm")
    )
    assert any(
        one["target_id"] == mid
        for one in _audit(client, admin, "jajuverify.meeting.unconfirm")
    )


def test_목록은_줄_수_작성_완료_수를_준다(client: TestClient, member: Signed) -> None:
    tag = uuid.uuid4().hex[:8]
    meeting = _meeting(client, member, title=f"{tag} 완료 세기")
    rows = [_row(client, member, meeting["id"], topic=name) for name in "ABC"]
    for one in rows[:2]:
        client.put(f"{JV}/rows/{one['id']}/done", json={"done": True}, headers=member.headers)
    client.put(f"{JV}/rows/{rows[1]['id']}/done", json={"done": False}, headers=member.headers)
    got = client.get(f"{JV}/meetings?kind=review&q={tag}", headers=member.headers).json()
    line = got["items"][0]
    assert (line["row_count"], line["done_count"], line["confirmed_at"]) == (3, 1, None)
    sheet = _sheet(client, member, meeting["id"])
    assert [one["done"] for one in sheet["rows"]] == [True, False, False]
    assert sheet["rows"][0]["done_by_name"] == "member" and sheet["rows"][0]["done_at"]
    assert sheet["rows"][1]["done_by_name"] is None and sheet["rows"][1]["done_at"] is None


def test_폴링_경로는_접근_로그와_요청_시간에_안_남는다(
    client: TestClient, admin: Signed, member: Signed, db: Session
) -> None:
    """한 장을 연 사람마다 10~15초에 한 번(바뀌었나) · 10초에 한 번(수정 중) — 남기면 접근
    로그와 요청 시간 기록이 그 줄로 찬다. 경로에 회의 id 가 들어 틀로
    견준다(`register_polling_route`)."""
    meeting = _meeting(client, member)
    mid = meeting["id"]
    row = _row(client, member, mid, topic="힌지")
    writer.discard()
    assert (
        client.put(
            f"{JV}/meetings/{mid}/presence",
            json={"row_id": row["id"], "field": "topic"},
            headers=member.headers,
        ).status_code
        == 204
    )
    _pulse(client, member, mid)
    assert (
        client.patch(
            f"{JV}/rows/{row['id']}",
            json={"remark": "남는다", "expected": {"remark": ""}},
            headers=member.headers,
        ).status_code
        == 200
    )
    logged = client.get("/api/audit/access?limit=200", headers=admin.headers).json()["items"]
    paths = [one["path"] for one in logged]
    assert f"/api/ext/jajuverify/rows/{row['id']}" in paths
    assert f"/api/ext/jajuverify/meetings/{mid}/presence" not in paths
    writer.flush()
    db.expire_all()
    timed = set(db.scalars(select(RequestTiming.route)))
    assert "/api/ext/jajuverify/rows/{row_id}" in timed
    assert (
        not {
            "/api/ext/jajuverify/meetings/{meeting_id}/pulse",
            "/api/ext/jajuverify/meetings/{meeting_id}/presence",
        }
        & timed
    )


# --- 폭 · 사진 · 일정 · AI · 옮기기(2026-10-31) ---------------------------------------


def test_사진_표시_줄의_모양과_걷어_내기() -> None:
    """칸 글 안의 한 줄 표시 — 제 줄 하나 · 첨부 id · 폭(10 ~ 100)."""
    from app.extensions.jajuverify import cells

    one, two = uuid.uuid4(), uuid.uuid4()
    text = (
        f"앞 글\n![캡처]({'photo'}:{one}?w=50)\n뒤 글 ![](photo:{two})\n  ![](photo:{two})  "
    )
    assert cells.marker_ids(text) == [one, two]  # 글 사이의 것은 표시가 아니다
    assert cells.plain(text) == f"앞 글\n뒤 글 ![](photo:{two})"
    assert cells.without_photos(text, {one}) == (
        f"앞 글\n뒤 글 ![](photo:{two})\n  ![](photo:{two})  "
    )
    assert cells.marker(one, 75) == f"![](photo:{one}?w=75)"
    assert (cells.clamp(3), cells.clamp(64), cells.clamp(180)) == (10, 64, 100)
    assert cells.schedule_of(
        [{"date": "2026-11-30", "label": " 재시험 "}, {"date": "2026-11-15", "label": ""}]
    ) == [{"date": "2026-11-15", "label": ""}, {"date": "2026-11-30", "label": "재시험"}]
    assert cells.last_date([]) is None


def test_사진은_첨부가_정본이고_떼면_그_표시_줄도_빠진다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """**그 칸의 첨부 목록이 정본** — 글 안의 표시는 자리와 폭만 말한다. 사진을 떼면 서버가 그
    표시 줄을 뺀다(AI 가 떼도 같다). 표시가 없는 사진 · 남의 칸 사진을 가리키는 표시는 글에
    그대로 남는다(그리는 쪽이 끝에 세우거나 안 그린다)."""
    meeting = _meeting(client, member)
    row = _row(client, member, meeting["id"], topic="힌지")
    placed = _attach(client, member, row["id"], "problem", _png(70)).json()["id"]
    loose = _attach(client, member, row["id"], "problem", _png(71)).json()["id"]
    elsewhere = _attach(client, member, row["id"], "remark", _png(72)).json()["id"]
    text = f"앞 글\n![](photo:{placed}?w=50)\n뒤 글\n![](photo:{elsewhere}?w=30)"
    saved = client.patch(
        f"{JV}/rows/{row['id']}",
        json={"problem": text, "expected": {"problem": ""}},
        headers=member.headers,
    )
    assert saved.status_code == 200, saved.text
    shots = {one["id"] for one in saved.json()["photos"]["problem"]}
    assert shots == {placed, loose}  # 표시가 없는 사진도 그 칸의 것이다

    assert (
        client.delete(f"/api/attachments/{placed}", headers=member.headers).status_code == 204
    )
    after = _sheet(client, member, meeting["id"])["rows"][0]
    assert after["problem"] == f"앞 글\n뒤 글\n![](photo:{elsewhere}?w=30)"
    assert [one["id"] for one in after["photos"]["problem"]] == [loose]
    entry = next(
        one
        for one in _audit(client, admin, "jajuverify.photo.remove")
        if one["target_id"] == row["id"]
    )
    assert entry["changes"]["problem"]["after"] == after["problem"]
    # 표시가 없는 사진을 떼면 글은 그대로다(판 번호만 오른다).
    assert (
        client.delete(f"/api/attachments/{loose}", headers=member.headers).status_code == 204
    )
    assert _sheet(client, member, meeting["id"])["rows"][0]["problem"] == after["problem"]

    # 감사의 이름표에는 표시 줄을 안 싣는다.
    client.patch(
        f"{JV}/rows/{row['id']}",
        json={"topic": f"![](photo:{elsewhere})\n도어 힌지", "expected": {"topic": "힌지"}},
        headers=member.headers,
    )
    labels = [
        one["target_label"]
        for one in _audit(client, admin, "jajuverify.row.update")
        if one["target_id"] == row["id"]
    ]
    assert any(label.endswith("· 도어 힌지") for label in labels), labels


def test_칸_사진은_업로드_표로도_붙고_토큰은_objects_write_가_함께_든다(
    client: TestClient, member: Signed
) -> None:
    """AI 의 셸이 바이트를 직접 올리는 길(ADR 0012) — 칸 사진은 **공통 첨부**라 경로의 범위
    (`/api/attachments` = `objects:write`)를 따른다. `jajuverify:write` 하나로는 표를 못 받는다
    — 자주검증을 고칠 토큰은 둘을 함께 받는다(GUIDE)."""
    meeting = _meeting(client, member)
    row = _row(client, member, meeting["id"], topic="힌지")

    def token(scopes: list[str]) -> dict[str, str]:
        got = client.post(
            "/api/auth/tokens",
            json={"name": f"jv-{uuid.uuid4().hex[:6]}", "scopes": scopes},
            headers=member.headers,
        )
        assert got.status_code == 201, got.text
        return {"Authorization": f"Bearer {got.json()['token']}"}

    ask = {
        "owner_table": "jv_rows",
        "owner_id": row["id"],
        "owner_field": "action",
        "filename": "현장.png",
    }
    narrow = token(["read", "jajuverify:write"])
    assert client.post("/api/attachments/tickets", json=ask, headers=narrow).status_code == 403
    both = token(["read", "jajuverify:write", "objects:write"])
    issued = client.post("/api/attachments/tickets", json=ask, headers=both)
    assert issued.status_code == 201, issued.text
    put = client.put(
        "/api/attachments/upload",
        content=_png(73),
        headers={"X-Upload-Ticket": issued.json()["ticket"]},
    )
    assert put.status_code == 201, put.text
    photo = put.json()["id"]
    # 올린 뒤 AI 는 그 id 로 표시 줄을 칸 글에 넣어 자리 · 폭을 정한다(본 값과 함께).
    placed = client.patch(
        f"{JV}/rows/{row['id']}",
        json={"action": f"![](photo:{photo}?w=40)\n그리스 변경", "expected": {"action": ""}},
        headers=both,
    )
    assert placed.status_code == 200, placed.text
    assert [one["id"] for one in placed.json()["photos"]["action"]] == [photo]
