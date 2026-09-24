"""디지털 트윈 역량 — **정의의 단일 출처.**

부문 · 축 · 척도 · 문턱 · 근거 등급이 전부 여기 있다. 화면 · API · 시험이 이 파일 하나를
읽는다 — 정의가 두 곳이면 둘이 갈리고, 갈린 사실은 숫자가 달라진 뒤에 드러난다.

원본(`52_DigitalTwinPortal` 의 `dev_dt_maturity`)의 정의를 옮긴 것이다. 그쪽이 실측으로
남긴 경고를 함께 옮긴다 — 경고가 빠지면 다음 사람이 같은 자리를 다시 밟는다.

⚠️ **축은 네 종류다.** `value`(값을 적고 문턱이 칸을 정한다) · `set`(선후 없는 항목을 켜고
   끈다) · `rung`(칸을 고른다) · `matrix`(줄마다 칸). 이 넷을 하나로 뭉치지 않는다 —
   「단계 하나 고르기」 로 단순화하면 가상검증률이 값과 칸으로 갈려 둘이 되고(값을 고쳐도
   칸이 안 따라온다), 자동화는 선후 없는 항목에 억지 서열이 생긴다.

⚠️ **문구는 갈아 끼울 수 있지만 key 는 고정이다.** 이력과 평가가 key 로 묶여 있다.
   문구를 고치는 것과 칸을 지우는 것은 다른 일이다.

⚠️ **사업부 DX KPI 「가상 검증률」 과 이름이 같다.** 계산이 다르다 — 이쪽은 연계 하나의
   평가값이고 그쪽은 사업부가 보고하는 집계다. 화면 도움말이 그 관계를 한 줄로 말한다.
   안 적으면 언젠가 누가 이 표를 보고 KPI 를 고친다.
"""

from __future__ import annotations

from typing import Any, Literal

#: 이 확장이 담는 부문. **시뮬레이션 하나로 시작한다** — 검증 자동화 · 설계 자동화 ·
#: 디지털 스레드는 원본에서도 검토안이라, 여기 옮길 것이 아직 없다.
SECTOR = "simulation"
SECTOR_LABEL = "시뮬레이션"
SUBJECT_LABEL = "시험 항목"
AGENT_LABEL = "시뮬레이션"

AxisKind = Literal["value", "set", "rung", "matrix"]

#: 근거 등급 — **무엇을 보고 매겼나.** 등급만 받고 자료를 안 받으면 「검증」 이 말뿐이 된다.
EVIDENCE_TIERS: list[dict[str, Any]] = [
    {"key": "stated", "label": "진술", "description": "담당자 진술", "needs_ref": False},
    {
        "key": "checked",
        "label": "확인",
        "description": "결과 파일 · 화면의 직접 확인",
        "needs_ref": True,
    },
    {
        "key": "verified",
        "label": "검증",
        "description": "보고서 · 성적서로 확인",
        "needs_ref": True,
    },
]
TIER_KEYS = tuple(one["key"] for one in EVIDENCE_TIERS)
TIERS_NEEDING_REF = tuple(one["key"] for one in EVIDENCE_TIERS if one["needs_ref"])

#: 가상검증률 — **값 → 칸.** 낮은 칸부터, 값이 넘는 가장 높은 칸을 고른다.
#: 경계는 「같으면 위 칸」 이다(90.0 은 현상 재현).
ACCURACY_THRESHOLDS: list[dict[str, Any]] = [
    {"rung": "trend", "min": 0.0},
    {"rung": "quantitative", "min": 70.0},
    {"rung": "correlated", "min": 90.0},
]

#: 시험 항목 하나의 가상검증률을 그 항목에 걸린 시뮬레이션들의 값에서 어떻게 셈하나.
#: key 로 셈이 갈리므로 문구만 고친다.
ACCURACY_RULES: list[dict[str, str]] = [
    {"key": "auto", "label": "자동 — 하나면 그 값, 여럿이면 평균"},
    {"key": "mean", "label": "평균 — 값 있는 시뮬레이션의 평균"},
    {"key": "single", "label": "단일 — 대표 하나 (여럿이면 값 없음)"},
]
ACCURACY_RULE_KEYS = tuple(one["key"] for one in ACCURACY_RULES)

AXES: list[dict[str, Any]] = [
    {
        "key": "accuracy",
        # 원본은 「정확도」 였다. 화면에 나가는 이름만 바꾼다(2026-09-24) — key 는 고정.
        "label": "가상검증률",
        "kind": "value",
        "unit": "%",
        "question": "시험 결과와의 일치 수준",
        "evidence_label": "비교 시험 건수 · 오차 · 첨부",
        "evidence": ["compared_tests", "error_pct"],
        # **칸을 사람이 고르지 않는다.** 값이 문턱을 넘으면 올라간다.
        "rungs": [
            {
                "key": "trend",
                "label": "경향 일치",
                "description": "경향 일치, 정량값 불일치 — 대안 간 우열 판정 불가",
            },
            {
                "key": "quantitative",
                "label": "우열 판정",
                "description": "정량값 일치 — 대안 간 우열 및 원인의 시뮬레이션 판정 가능",
            },
            {
                "key": "correlated",
                "label": "현상 재현",
                "description": "시험 현상의 재현, 결과의 직접 활용 가능",
            },
        ],
    },
    {
        "key": "automation",
        "label": "자동화",
        "kind": "set",
        "question": "해석 파이프라인의 단계별 자동화 범위",
        # 1회 소요 시간은 **단계마다** 받는다. 하나로 받으면 무엇에 걸리는 시간인지 모르고,
        # 단계를 더 자동화해도 숫자가 안 움직인다.
        "evidence_per_flag": ["hours_per_run"],
        "evidence_label": "단계별 1회 소요 시간(Hr)",
        # **척도가 아니라 묶음이다.** 전처리 · 실행 · 후처리는 선후가 없다. 서열은 켠 개수다.
        # 「수동」 은 아무것도 안 켠 것이므로 화면에 칸으로 안 세운다 — key 는 남겨 둔다.
        "hide_empty": True,
        "rungs": [
            {
                "key": "manual",
                "label": "수동",
                "description": "자동화 단계 없음 — 전 과정 수작업",
            },
            {
                "key": "pre",
                "label": "전처리 자동화",
                "description": "형상 · 메시 · 해석 조건 준비의 자동화",
            },
            {
                "key": "run",
                "label": "실행 자동화",
                "description": "템플릿 기반 해석 실행의 자동화",
            },
            {
                "key": "post",
                "label": "후처리 자동화",
                "description": "결과 추출 · 그래프 작성의 자동화",
            },
            {
                "key": "rom",
                "label": "ROM 모델 도입",
                "description": "축약 모델로 즉시 답을 내는 단계",
            },
            {"key": "report", "label": "보고서 자동화", "description": "보고서 생성의 자동화"},
            {
                "key": "pipeline",
                "label": "파이프라인",
                "description": "단계를 이어 한 번에 도는 오케스트레이션",
            },
        ],
    },
    {
        "key": "modeling",
        "label": "모델링 수준",
        "kind": "matrix",
        "question": "어떤 불량까지 재현하나",
        "evidence_label": "재현 근거",
        # 줄은 **시험 항목의 불량 유형**이 아니라 재현의 종류다(원본 rows). 항목의 불량
        # 유형은 근거(evidence.defects)의 키로 쓴다.
        "rows": [
            {
                "key": "shape",
                "label": "형상 재현",
                "description": "치수 · 재질 · 경계 조건의 실물 일치",
            },
            {
                "key": "behavior",
                "label": "거동 재현",
                "description": "변형 · 온도 · 유동 등 물리 거동의 시험 일치",
            },
            {
                "key": "reliability",
                "label": "신뢰성 시험 불량 재현",
                "description": "신뢰성 시험 불량의 재현",
            },
            {
                "key": "field",
                "label": "시장 불량 재현",
                "description": "시장 불량(사용 조건 · 누적 이력)의 재현",
            },
        ],
        "rungs": [
            {"key": "none", "label": "없음", "description": "재현 항목 없음"},
            {"key": "shape", "label": "형상 재현", "description": "형상 재현에 한정"},
            {"key": "behavior", "label": "거동 재현", "description": "물리 거동까지 재현"},
            {
                "key": "defect_some",
                "label": "일부 불량 시험 재현",
                "description": "일부 불량 유형의 시험 불량 재현",
            },
            {
                "key": "defect_all",
                "label": "전 유형 시험 재현",
                "description": "전 불량 유형의 시험 불량 재현",
            },
            {"key": "field", "label": "시장 불량까지", "description": "시장 불량까지 재현"},
        ],
    },
    {
        "key": "scope",
        "label": "적용 범위",
        "kind": "rung",
        "question": "어디까지 적용됐나",
        "evidence_label": "적용 근거",
        "rungs": [
            {
                "key": "issue",
                "label": "이슈 대응",
                "description": "문제 발생 후 해당 모델에 한정",
            },
            {
                "key": "basic",
                "label": "대표(Basic) 모델",
                "description": "제품군 대표 모델 개발에 적용",
            },
            {
                "key": "new_all",
                "label": "신규 개발 전 모델",
                "description": "전 신규 개발 과제에 적용",
            },
            {
                "key": "derivative",
                "label": "파생 · 지역 변형까지",
                "description": "파생 · 지역 변형 과제까지 적용",
            },
        ],
    },
    {
        "key": "substitution",
        "label": "시험 대체",
        "kind": "set",
        "question": "시험을 얼마나 대신하나",
        "evidence_label": "대체 근거",
        "hide_empty": True,
        "rungs": [
            {"key": "none", "label": "없음", "description": "시험 대체 미적용"},
            {
                "key": "parallel",
                "label": "시험 병행(참고)",
                "description": "시험 유지, 참고 자료로만 활용",
            },
            {
                "key": "cause",
                "label": "원인 분석",
                "description": "시험에서 난 문제의 시뮬레이션 기반 원인 분석",
            },
            {
                "key": "pre_check",
                "label": "사전 검증",
                "description": "시험 전 자주 검증으로 활용",
            },
            {
                "key": "gate",
                "label": "신뢰성 인증 게이트",
                "description": "PLM 안 CAE 항목으로 관리되는 게이트",
            },
            {
                "key": "full",
                "label": "완전 대체",
                "description": "해당 시험을 시뮬레이션이 대신한다",
            },
        ],
    },
]

AXIS_BY_KEY = {one["key"]: one for one in AXES}
AXIS_KEYS = tuple(one["key"] for one in AXES)


def rung_keys(axis_key: str) -> tuple[str, ...]:
    return tuple(one["key"] for one in AXIS_BY_KEY[axis_key]["rungs"])


def rung_for_value(value: float | None) -> str | None:
    """가상검증률 값 → 칸 key. 값이 없으면 `None`(미평가).

    **사람이 칸을 고르지 않는다.** 값과 칸을 따로 받으면 값을 고쳐도 칸이 안 따라와
    가상검증률이 둘이 된다.
    """
    if value is None:
        return None
    chosen: str | None = None
    for step in ACCURACY_THRESHOLDS:
        if float(value) >= float(step["min"]):
            chosen = str(step["rung"])
    return chosen


def aggregate_accuracy(
    values: list[float | None], rule: str = "auto"
) -> tuple[float | None, int, int]:
    """시험 항목 하나의 가상검증률 — `(값, 채운 수, 전체 수)`.

    **평균은 값 있는 것만으로** 낸다. 미입력을 0 으로 넣으면 아직 재지 않은 시뮬레이션이
    항목 값을 끌어내리고, 그러면 사람은 아직 못 잰 것과 못하는 것을 구별할 수 없다.
    """
    total = len(values)
    filled = [float(one) for one in values if one is not None]
    if not filled:
        return None, 0, total
    if rule not in ACCURACY_RULE_KEYS:
        rule = "auto"
    if rule == "auto":
        rule = "single" if total == 1 else "mean"
    if rule == "single":
        # 여럿인데 대표가 정해져 있지 않으면 값을 만들지 않는다 — 화면이 「대표를
        # 고르세요」 를 말해야 한다. 아무 값이나 고르면 그것이 정본처럼 보인다.
        return (filled[0] if total == 1 else None), len(filled), total
    return round(sum(filled) / len(filled), 2), len(filled), total
