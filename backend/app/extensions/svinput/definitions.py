"""자주검증 입력 — **역할의 단일 출처.**

이 확장은 표를 만들지 않는다. 허브에 이미 있는 온톨로지 타입(`sv_pm_*`)을 **역할**로 가리키고,
화면은 역할을 보고 그린다. 역할마다 어느 칸이 무엇인지(문서 참조 · 단계 · 판정 · 상태)도 설정이
정한다 — 타입 slug · 칸 키를 코드에 박으면 형제 설치(다른 이름 · 아직 적재 안 된 설치)에서
깨지고,온톨로지를 고치는 순간 확장이 멈춘다(ADR 0051).

⚠️ **역할 key 는 고정이다.** 설정 표(`svinput_settings.roles`)와 화면이 key 로 묶여 있다. 문구는
   갈아 끼워도 되지만 key 를 바꾸는 것은 지우고 새로 만드는 것과 같다.
⚠️ **기본 후보는 제안일 뿐이다.** 설정이 비어 있으면 화면은 「먼저 설정에서 타입을 고르세요」 를
   말한다 — 기본값을 DB 에 미리 심으면 기본값을 고쳐도 이미 깔린 설치는 옛 값을 계속 든다.
"""

from __future__ import annotations

from typing import Any

#: 설정 · 화면이 공통으로 쓰는 이름.
NAME = "svinput"
LABEL = "자주검증 입력"

#: 허브의 타입 접두 — 설정 화면이 **후보를 제안**할 때만 쓴다(ADR 0051, 2026-11-04 실측).
CANDIDATE_PREFIX = "sv_pm_"

#: 역할 — key 고정. `fields` 는 그 역할의 타입에서 골라야 하는 칸(역할 key → 뜻).
#: `required` 가 거짓인 칸은 비워도 된다(그 기능이 안 설 뿐이다).
ROLES: list[dict[str, Any]] = [
    {
        "key": "spine",
        "label": "과제 · 모델(스파인)",
        "description": "코어의 과제 · 개발모델 타입 — 문서 · 집계가 이 축으로 묶인다.",
        "default_type": "plm_project",
        "hub": False,
        "fields": [],
    },
    {
        "key": "report",
        "label": "포스트모텀 문서",
        "description": "한 장 — 과제(개발모델) · 일자. 비이슈 장표는 이미지로 보존된다.",
        "default_type": "sv_pm_report",
        "fields": [
            {
                "key": "model",
                "label": "과제 · 모델을 가리키는 참조 칸(스파인)",
                "required": False,
                "default": "project",
            },
        ],
    },
    {
        "key": "issue",
        "label": "문제점",
        "description": "관리 단위 — 현상 · 대책 · 판정 · 재발방지 · 부품 · 고장모드 · PLM.",
        "default_type": "sv_pm_issue",
        "fields": [
            {
                "key": "report_ref",
                "label": "문서와의 연결 — 참조 칸 또는 rel:<관계 종류>",
                "required": True,
                "default": "report",
            },
            {
                "key": "status",
                "label": "일정 상태 칸(진행 · 완료)",
                "required": False,
                "default": "plan_status",
            },
        ],
    },
    {
        "key": "stage_result",
        "label": "단계별 판정",
        "description": "문제점의 하위 기록 — 단계(DV1 · DV2 · PV …)마다 판정(PASS · FAIL).",
        "default_type": "sv_pm_stage_result",
        "fields": [
            {
                "key": "issue_ref",
                "label": "문제점과의 연결 — 참조 칸 또는 rel:<관계 종류>",
                "required": True,
                "default": "issue",
            },
            {"key": "stage", "label": "단계 칸", "required": False, "default": "stage"},
            {"key": "result", "label": "판정 칸", "required": False, "default": "result"},
        ],
    },
    {
        "key": "cause",
        "label": "원인",
        "description": "문제점의 하위 기록.",
        "default_type": "sv_pm_issue_cause",
        "fields": [
            {
                "key": "issue_ref",
                "label": "문제점과의 연결 — 참조 칸 또는 rel:<관계 종류>",
                "required": True,
                "default": "issue",
            },
        ],
    },
    {
        "key": "prevention",
        "label": "재발방지",
        "description": "문제점의 하위 기록.",
        "default_type": "sv_pm_prevention",
        "fields": [
            {
                "key": "issue_ref",
                "label": "문제점과의 연결 — 참조 칸 또는 rel:<관계 종류>",
                "required": True,
                "default": "issue",
            },
        ],
    },
    {
        "key": "kpi",
        "label": "결과요약 지표",
        "description": "문서마다 셋(해석현황 · 대책유효성 · coverage) — 분자 · 분모는 숫자.",
        "default_type": "sv_pm_kpi",
        "fields": [
            {
                "key": "report_ref",
                "label": "문서와의 연결 — 참조 칸 또는 rel:<관계 종류>",
                "required": True,
                "default": "report",
            },
            {
                "key": "numerator",
                "label": "분자 칸",
                "required": False,
                "default": "numerator",
            },
            {
                "key": "denominator",
                "label": "분모 칸",
                "required": False,
                "default": "denominator",
            },
        ],
    },
    {
        "key": "figure",
        "label": "장표 그림",
        "description": "이슈 · 문서의 그림 — 보이는 것 · 왜 중요한지가 글로 뽑혀 있다.",
        "default_type": "sv_pm_figure",
        "fields": [
            {
                "key": "issue_ref",
                "label": "문제점과의 연결 — 참조 칸 또는 rel:<관계 종류>",
                "required": False,
                "default": "issue",
            },
        ],
    },
]
ROLE_KEYS = tuple(str(one["key"]) for one in ROLES)
ROLE_BY_KEY = {str(one["key"]): one for one in ROLES}

#: **스파인은 코어의 것이다** — 확장은 만들지도 고치지도 않고, 참조 칸으로 가리켜 목록 · 집계를
#: 묶는다. 허브 타입이 아닌 역할은 `hub: False` 로 후보를 접두로 거르지 않는다.
#: 화면이 서려면 있어야 하는 역할 — 나머지는 비어도 그 부분만 안 선다.
REQUIRED_ROLES = ("report", "issue")
