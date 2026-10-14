"""MCP 운영 · 설정 도구를 **진짜 앱에 붙여** 본다(2026-10-15).

화면의 관리 기능(코어 공개 현황 · 부서 · 저장된 뷰 · 지켜보기 · 연도 배정 · 공지 ·
데이터 소스 · 경보 · 검토 이력 · 서버 · 내보내기 · 설정 지우기)을 AI 도 쓰게 한 도구들이
**진짜 라우터를 타고** 화면과 같은 결과를 내나, 고치는 것은 미리 보기가 먼저인가를 본다.
도구가 보내는 모양이 서버의 스키마와 어긋나면 가짜 백엔드 시험(`mcp_server/tests`)으로는
모른다 — 여기서만 드러난다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_mcp_tools import Bot, ToolError, _uniq, bot, server
from tests.api.test_metric_analysis import _monthly, _plant_months
from tests.api.test_metrics import _world

__all__ = ["bot"]  # 진짜 앱에 붙인 기계 자격(픽스처)을 이 파일에서도 쓴다


def _kind(bot: Bot, **type_extra: Any) -> str:
    slug = _uniq("adm")
    bot.call(
        server.ontology_import,
        {
            "types": [
                {
                    "slug": slug,
                    "label": "관리 시험",
                    "key_policy": "optional",
                    "properties": [{"key": "grade", "label": "등급", "data_type": "text"}],
                    **type_extra,
                }
            ]
        },
        apply=True,
    )
    return slug


def test_코어_현황과_서버_상태를_도구로_본다(bot: Bot) -> None:
    status = bot.call(server.core_status)
    wanted = {"types", "consumers", "recent", "withheld_relations", "withheld_references"}
    assert wanted <= set(status)
    assert all("properties" not in one for one in status["types"])

    found = bot.call(server.server_status)
    assert found["status"]["version"] and isinstance(found["extensions"], list)
    assert isinstance(bot.call(server.server_status, performance="jobs"), dict)


def test_부서_공지_웹훅은_읽기만_한다(bot: Bot) -> None:
    """관리(부서 · 공지 · 웹훅 · 서버)는 토큰에 안 열린다 — 도구도 읽기만 낸다. 쓰기 경로를
    MCP 때문에 넓히지 않는다는 것을 서버의 말로도 확인한다."""
    listed = bot.call(server.workspaces)
    assert listed and {"slug", "name", "path"} <= set(listed[0])
    detail = bot.call(server.workspaces, listed[0]["slug"])
    assert isinstance(detail["contents"], list) and isinstance(detail["references"], list)
    assert isinstance(bot.call(server.notices, include_drafts=True), list)
    assert isinstance(bot.call(server.webhooks), list)
    for name in ("workspace_save", "notice_post", "webhook_send", "extension_toggle"):
        assert not hasattr(server, name), name
    with pytest.raises(ToolError):
        bot.call(server.config_delete, "workspace", slug=listed[0]["slug"])


def test_뷰_지켜보기_연도_배정을_도구로_다룬다(bot: Bot) -> None:
    kind = _kind(bot, temporal_kind="yearly")
    made = bot.call(server.object_create, kind, label="볼트", properties={"grade": "A"})

    query = {"q": "", "conditions": [{"field": "grade", "op": "eq", "value": "A"}]}
    assert bot.call(server.view_save, kind, name="A 등급", query=query)["applied"] is False
    view = bot.call(server.view_save, kind, name="A 등급", query=query, apply=True)
    assert [one["name"] for one in bot.call(server.views, kind)] == ["A 등급"]
    bot.call(server.view_save, kind, view_id=view["id"], name="A 만", apply=True)
    assert bot.call(server.views, kind)[0]["name"] == "A 만"
    bot.call(server.config_delete, "view", slug=kind, id=view["id"], apply=True)
    assert bot.call(server.views, kind) == []

    watched = bot.call(server.object_watch, kind, made["id"], on=True)
    assert watched["watching"] is True
    assert made["id"] in {one["id"] for one in bot.call(server.object_watch)}
    assert bot.call(server.object_watch, kind, made["id"], on=False)["watching"] is False

    plan = bot.call(server.object_years, kind, made["id"], years=[2026, 2025])
    assert plan["applied"] is False and plan["would"] == {"years": [2025, 2026]}
    bot.call(server.object_years, kind, made["id"], years=[2025, 2026], apply=True)
    assert bot.call(server.object_years, kind, made["id"]) == [2025, 2026]


def test_데이터_소스를_도구로_만들고_고치고_지운다(bot: Bot) -> None:
    kind = _kind(bot)
    slug = _uniq("src")
    fields = {
        "name": "바깥 표",
        "base_url": "https://erp.example.com/odata",
        "entity_set": "Parts",
        "type_slug": kind,
    }
    with pytest.raises(ToolError, match="비밀"):
        bot.call(server.datasource_save, slug, {**fields, "auth_secret": "x"}, apply=True)
    assert bot.call(server.datasource_save, slug, fields)["would"]["action"] == "create"
    bot.call(server.datasource_save, slug, fields, apply=True)
    bot.call(server.datasource_save, slug, {"interval_minutes": 60}, apply=True)
    assert bot.call(server.datasources_list, slug)["source"]["interval_minutes"] == 60
    bot.call(server.config_delete, "datasource", slug=slug, apply=True)
    assert slug not in {one["slug"] for one in bot.call(server.datasources_list)}


def test_경보를_도구로_걸고_지금_확인하고_끄고_지운다(
    client: TestClient, admin: Signed, bot: Bot
) -> None:
    w = _world(client, admin)
    sales, monthly = _monthly(client, admin, w)
    slug = monthly["slug"]
    counts = [200 + (i % 3) * 5 for i in range(24)]
    _plant_months(w, sales, monthly, counts, 20000.0, datetime(2028, 8, 15, tzinfo=UTC))

    plan = bot.call(server.metric_alert_save, slug, name="관리도", recipe="control", params={})
    assert plan["applied"] is False and "now" in plan
    made = bot.call(
        server.metric_alert_save, slug, name="관리도", recipe="control", params={}, apply=True
    )
    assert "findings" in bot.call(server.metric_alerts, slug, alert_id=made["id"], check=True)
    assert isinstance(bot.call(server.metric_alerts, slug, alert_id=made["id"]), list)

    bot.call(server.metric_alert_save, slug, alert_id=made["id"], is_active=False, apply=True)
    mine = bot.call(server.metric_alerts, slug)["alerts"]
    assert [one["is_active"] for one in mine if one["id"] == made["id"]] == [False]
    bot.call(server.config_delete, "metric_alert", slug=slug, id=made["id"], apply=True)
    left = bot.call(server.metric_alerts, slug)["alerts"]
    assert all(one["id"] != made["id"] for one in left)


def test_검토_이력_내보내기_작업_정리를_도구로_본다(bot: Bot) -> None:
    history = bot.call(server.review_history)
    assert {"accuracy", "decisions", "batches"} <= set(history)
    assert isinstance(bot.call(server.review_undo), list)

    kind = _kind(bot)
    bot.call(server.object_create, kind, label="볼트", properties={"grade": "A"})
    exported = bot.call(server.export_start, kind, properties={"grade": "A"})
    assert exported["kind"] == "objects_export" and exported["status"] == "done", exported
    assert "화면" in exported["next"]

    assert bot.call(server.jobs_purge)["applied"] is False
