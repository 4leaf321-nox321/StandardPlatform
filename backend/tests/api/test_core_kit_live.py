"""연동 키트를 **실제 창구에** 돌린다 — 바뀐 것을 따라온 받는 쪽이 처음부터 받은 쪽과 같은가.

키트 시험(`tests/unit/test_core_kit.py`)은 응답을 손으로 지어 낸다. 지어 낸 응답이 실제 창구와
어긋나면(오류 코드 모양, 선의 칸, 지운 객체의 선) 둘 다 통과한 채로 상대 회사에서 틀린다.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_core_api import _token, _world
from tests.unit.test_core_kit import _kit


def _rows(client: TestClient, admin: Signed, slug: str, rows: list[dict[str, Any]]) -> None:
    done = client.post(
        f"/api/objects/{slug}/import-rows",
        json={"rows": rows, "apply": True},
        headers=admin.headers,
    )
    assert done.status_code == 200, done.text


def _id_of(client: TestClient, admin: Signed, slug: str, key: str) -> str:
    rows = client.get(f"/api/objects/{slug}", headers=admin.headers).json()["items"]
    return str(next(one for one in rows if one["key"] == key)["id"])


def _held(
    out: Path, vendor: str, part: str
) -> tuple[set[tuple[str, str]], set[tuple[str, ...]]]:
    """받는 쪽의 현재 상태 — 살아 있는 객체와 선."""
    db = sqlite3.connect(out / "sp_core.sqlite")
    try:
        alive = {
            (slug, key)
            for slug in (vendor, part)
            for (key,) in db.execute(f'SELECT key FROM "{slug}" WHERE deleted=0')
        }
        edges = {
            tuple(one)
            for one in db.execute(
                f'SELECT src, relation, dst, dst_type FROM "{part}__relations"'
            )
        }
    finally:
        db.close()
    return alive, edges


def test_키트가_고침_삭제_합치기_식별자_변경을_따라가_처음_받은_쪽과_같아진다(
    client: TestClient, admin: Signed, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = _kit()
    vendor, part = _world(client, admin)
    kind = f"supplies_{uuid.uuid4().hex[:6]}"
    made = client.post(
        "/api/ontology/import",
        json={
            "relation_types": [
                {
                    "slug": kind,
                    "label": "공급",
                    "src_type_slugs": [part],
                    "dst_type_slugs": [vendor],
                }
            ]
        },
        params={"dry_run": "false"},
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    _rows(
        client,
        admin,
        vendor,
        [
            {"key": "V-1", "label": "ACME"},
            {"key": "V-2", "label": "한화"},
            {"key": "V-3", "label": "옛 ACME"},
        ],
    )
    _rows(
        client, admin, part, [{"key": "P-1", "label": "볼트"}, {"key": "P-2", "label": "너트"}]
    )
    linked = client.post(
        f"/api/objects/{part}/relations/import-rows",
        json={
            "rows": [
                {"src": "P-1", "relation": kind, "dst": "V-1"},
                {"src": "P-2", "relation": kind, "dst": "V-2"},
                {"src": "P-1", "relation": kind, "dst": "V-3"},
            ],
            "apply": True,
        },
        headers=admin.headers,
    )
    assert linked.status_code == 200, linked.text

    token = _token(client, admin, f"kit-{uuid.uuid4().hex[:6]}", ["core:read"])

    def get(url: str, **kwargs: Any) -> Any:
        kwargs.pop("timeout", None)
        return client.get(url.replace("http://testserver", ""), **kwargs)

    monkeypatch.setattr(kit.requests, "get", get, raising=False)

    def run(name: str, *args: str) -> Path:
        out = tmp_path / name
        config = tmp_path / f"{name}.ini"
        config.write_text(
            "[sp]\nbase_url = http://testserver/api\n"
            f"token = {token}\ntypes = {vendor}, {part}\nout_dir = {out}\n",
            encoding="utf-8",
        )
        assert kit.main(["--config", str(config), *args]) == 0
        return out

    ours = run("ours")
    alive, edges = _held(ours, vendor, part)
    assert {key for _slug, key in alive} == {"V-1", "V-2", "V-3", "P-1", "P-2"}
    assert {(one[0], one[2]) for one in edges} == {
        ("P-1", "V-1"),
        ("P-2", "V-2"),
        ("P-1", "V-3"),
    }

    # 공개 측에서 고친다 — 식별자 바꿈 · 지움 · 합침.
    _rows(client, admin, vendor, [{"renamed_from": "V-1", "key": "V-1N", "label": "ACME"}])
    # 선이 걸린 객체는 「참조를 비우고」 지운다(그냥 지우기는 막힌다) — 선은 무덤으로 온다.
    gone = client.delete(
        f"/api/objects/{vendor}/{_id_of(client, admin, vendor, 'V-2')}",
        params={"mode": "detach"},
        headers=admin.headers,
    )
    assert gone.status_code in (200, 204), gone.text
    merged = client.post(
        f"/api/objects/{vendor}/{_id_of(client, admin, vendor, 'V-3')}/merge",
        json={"into": _id_of(client, admin, vendor, "V-1N")},
        headers=admin.headers,
    )
    assert merged.status_code == 200, merged.text

    # 증분으로 따라온 쪽과 처음부터 받은 쪽이 **같아야** 한다.
    ours = run("ours")
    fresh = run("fresh")
    assert _held(ours, vendor, part) == _held(fresh, vendor, part)
    alive, edges = _held(ours, vendor, part)
    assert {key for slug, key in alive if slug == vendor} == {"V-1N"}
    assert {(one[0], one[2], one[3]) for one in edges} == {("P-1", "V-1N", vendor)}

    db = sqlite3.connect(ours / "sp_core.sqlite")
    try:
        assert ("V-1", "V-1N") in set(
            db.execute(f'SELECT old_key, key FROM "{vendor}__previous_keys"')
        )
        # 옛 key 로 된 줄이 따로 남지 않았다 — 같은 것이 둘이 되지 않는다.
        keys = [key for (key,) in db.execute(f'SELECT key FROM "{vendor}"')]
        assert "V-1" not in keys and sorted(keys) == ["V-1N", "V-2", "V-3"]
    finally:
        db.close()

    # 처음부터 다시 받아도 아무것도 바뀌지 않는다(정리할 것이 없다).
    ours = run("ours", "--full")
    assert _held(ours, vendor, part) == _held(fresh, vendor, part)
