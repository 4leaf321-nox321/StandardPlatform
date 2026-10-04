"""RA 피드 항목 → 행(ADR 0018) — RA 의 실제 응답 모양에서 나온 두 가지.

- RA 에 기준 주소(`APP_BASE_URL`)가 비어 있으면 원문 주소가 `/w/…` 상대 경로로 온다 — 그대로
  두면 주소 칸이 거절하거나, 받아도 열리지 않는다. 소스의 RA 루트를 붙인다.
- 본문이 한 건 1MB 를 넘으면 RA 가 앞부분만 주고 `text_truncated` 를 단다 — 앞부분을 전부로
  읽지 않게 잘렸다고 적는다.
"""

from __future__ import annotations

from app.modules.datasources import ra_reports


def test_상대_주소에는_RA_루트를_붙인다() -> None:
    root = "https://hub.example.com/report-archive"
    assert ra_reports.absolute(root, "/w/cae/reports/7") == f"{root}/w/cae/reports/7"
    # 루트를 `…/api` 까지 적었어도 같은 곳.
    assert ra_reports.absolute(f"{root}/api/", "/w/cae/reports/7") == f"{root}/w/cae/reports/7"
    assert ra_reports.absolute(root, "https://ra.local/w/x/reports/1") == (
        "https://ra.local/w/x/reports/1"
    )
    assert ra_reports.absolute(root, None) is None and ra_reports.absolute(root, " ") is None


def test_잘린_본문은_잘렸다고_적고_없는_본문은_싣지_않는다() -> None:
    base = {"id": 7, "title": "강성 해석", "url": "/w/cae/reports/7"}
    cut = ra_reports.flatten({**base, "text": "앞부분", "text_truncated": True})
    assert cut["body"] == "앞부분" + ra_reports.CUT_NOTE
    whole = ra_reports.flatten({**base, "text": "전문", "text_truncated": False})
    assert whole["body"] == "전문"
    # 본문이 없으면(null) 칸을 안 싣는다 — 「본문을 지우라」 로 읽지 않게.
    assert "body" not in ra_reports.flatten({**base, "text": None})
    assert ra_reports.flatten(base, base_url="https://ra.local")["url"] == (
        "https://ra.local/w/cae/reports/7"
    )


def test_긴_제목은_이름_칸에_맞게_줄인다() -> None:
    """RA 제목은 255자, 이름 칸은 200자 — 넘긴 채 보내면 적용에서 DB 가 거절하고 같은 보고서가
    다음 차례에도 와서 동기화가 계속 멈춘다."""
    long = "가" * 255
    label = ra_reports.flatten({"id": 1, "title": long})["label"]
    assert len(label) == ra_reports.TITLE_MAX and label.endswith("…")
    assert ra_reports.flatten({"id": 1, "title": "  강성\n해석  "})["label"] == "강성 해석"
