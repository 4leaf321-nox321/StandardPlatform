"""리랭커 — 질문과 찾은 글을 **함께 읽고** 관련도(0 ~ 1)를 매긴다(ADR 0072).

뜻으로 찾기(ADR 0050)는 질문과 글을 **따로** 뜻의 자리로 바꿔 거리만 잰다 — 「같은 동네」
인지는 알아도 「답이 되나」 는 모른다. 그래서 상관없는 질문(CAE 업무 백서에 「회의실 예약」)도
기준을 넘고, 이름이 포함되는 다른 것(PMIC ↔ MIC)이 가깝게 나온다. 리랭커(bge-reranker-v2-m3)는
둘을 한 번에 읽어 「답이 되나」 를 점수로 준다.

서버에 하나 — llama.cpp 의 `llama-server --reranking`(`sp-rerank.service`, 127.0.0.1:11436,
GPU). 이 서버의 SP 설치들이 함께 쓴다(Ollama 와 같다). 배포가 깔고, 못 깔아도 배포는 멈추지
않는다.

**못 하면 거리로만** — 꺼짐 · 닿지 않음 · 시간을 넘김이면 부르는 쪽(검색)이 거리로만 고른다.
검색은 멈추지 않는다. 한 번 실패하면 `PAUSE` 동안은 부르지 않는다 — 꺼진 리랭커를 기다리느라
검색마다 몇 초씩 늦어지지 않게.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import get_settings

#: 한 번 실패하면 이만큼(초)은 부르지 않는다.
PAUSE = 60.0
#: 서버 화면이 기다리는 시간 — 글 하나를 매겨 본다(실측 GPU 20ms · CPU 45ms).
PROBE_SECONDS = 5.0
#: 리랭커에 보내는 글의 상한(글자) — llama.cpp 서버는 자리(슬롯)마다 받는 길이가 정해져
#: 있어(배포: 4,096 토큰) 넘는 글이 하나라도 있으면 요청 전체를 거절한다. 문서의 절은 이미
#: 1,500자로 나뉘어 있어 기본 글(4,000자까지)만 잘린다 — 앞부분(이름 · 다른 이름 · 설명)이
#: 뜻을 거의 다 든다.
TEXT_MAX = 2_000

#: 시험이 가짜 리랭커를 끼우는 자리(`httpx.MockTransport`).
transport: httpx.BaseTransport | None = None

_lock = threading.Lock()
_paused_until = 0.0
_why = ""

State = Literal["ready", "off", "problem"]


class Unavailable(RuntimeError):
    """관련도를 못 매겼다 — 끔 · 닿지 않음 · 시간을 넘김 · 답이 이상함. 말은 로그에 남긴다."""


def enabled() -> bool:
    return get_settings().rerank_backend != "off"


def resume() -> None:
    """쉬는 것을 푼다 — 시험 · 서버 화면의 다시 보기."""
    global _paused_until, _why
    with _lock:
        _paused_until, _why = 0.0, ""


def _pause(why: str) -> None:
    global _paused_until, _why
    with _lock:
        _paused_until, _why = time.monotonic() + PAUSE, why


def _sigmoid(value: float) -> float:
    # 큰 음수에서 넘치지 않게 — 관계없는 글의 날 점수는 -10 아래로도 간다.
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    small = math.exp(value)
    return small / (1 + small)


def _post(url: str, body: dict[str, Any], seconds: float) -> Any:
    with httpx.Client(timeout=seconds, transport=transport) as client:
        response = client.post(f"{url}/v1/rerank", json=body)
    response.raise_for_status()
    return response.json()


def score(query: str, documents: list[str], *, seconds: float | None = None) -> list[float]:
    """글마다 관련도(0 ~ 1) — 넣은 차례 그대로.

    llama.cpp 는 날 점수(로짓)를 준다 — 시그모이드로 편다(원본 모델의 `normalize=True` 와
    같다). **못 매기면 `Unavailable`** 이고 `PAUSE` 동안 쉰다.
    """
    if not documents:
        return []
    settings = get_settings()
    if settings.rerank_backend == "off":
        raise Unavailable("리랭커가 비활성입니다(RERANK_BACKEND=off)")
    with _lock:
        if time.monotonic() < _paused_until:
            raise Unavailable(f"리랭커가 잠깐 쉬는 중입니다 — {_why}")
    url = settings.rerank_url.rstrip("/")
    try:
        found = _post(
            url,
            {
                "model": settings.rerank_model,
                "query": query[:TEXT_MAX],
                "documents": [one[:TEXT_MAX] for one in documents],
                "top_n": len(documents),
            },
            settings.rerank_seconds if seconds is None else seconds,
        )
        raw: list[float | None] = [None] * len(documents)
        for one in found["results"]:
            raw[int(one["index"])] = float(one["relevance_score"])
    except httpx.TimeoutException as caught:
        _pause(f"{settings.rerank_seconds}초 안에 답하지 않음")
        raise Unavailable(
            f"리랭커가 {settings.rerank_seconds}초 안에 답하지 않았습니다"
        ) from caught
    except httpx.HTTPStatusError as caught:
        _pause(f"{caught.response.status_code} 로 답함")
        raise Unavailable(
            f"리랭커가 {caught.response.status_code} 로 답했습니다({url})"
        ) from caught
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as caught:
        _pause("닿지 않음")
        raise Unavailable(
            f"리랭커에 닿지 않습니다({url}) — sudo ./deploy.sh rerank"
        ) from caught
    if any(one is None for one in raw):
        _pause("답에 빠진 글이 있음")
        raise Unavailable("리랭커의 답에 빠진 글이 있습니다")
    return [_sigmoid(float(one)) for one in raw if one is not None]


@dataclass(frozen=True)
class Readiness:
    state: State
    backend: str
    model: str
    url: str
    probe_ms: int | None
    """글 하나를 매기는 데 걸린 시간 — 실측 GPU 20ms · CPU 45ms(2026-11-14)라 이것으로 GPU
    인지 가르지 않는다(`nvidia-smi`)."""
    problem: str | None


def readiness() -> Readiness:
    """서버 화면 — 리랭커가 답하나, 얼마나 빠른가(글 하나를 매겨 본다)."""
    settings = get_settings()
    url = settings.rerank_url.rstrip("/")

    def answer(state: State, problem: str | None = None, ms: int | None = None) -> Readiness:
        return Readiness(
            state=state,
            backend=settings.rerank_backend,
            model=settings.rerank_model,
            url=url,
            probe_ms=ms,
            problem=problem,
        )

    if settings.rerank_backend == "off":
        return answer("off")
    started = time.perf_counter()
    try:
        found = _post(
            url,
            {
                "model": settings.rerank_model,
                "query": "확인",
                "documents": ["확인"],
                "top_n": 1,
            },
            PROBE_SECONDS,
        )
        float(found["results"][0]["relevance_score"])
    except httpx.HTTPStatusError as caught:
        return answer(
            "problem",
            f"리랭커가 {caught.response.status_code} 로 답했습니다({url}) — 모델을 싣는 "
            "중이면 잠시 뒤 다시 봅니다",
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return answer(
            "problem",
            f"리랭커에 닿지 않습니다({url}) — sudo ./deploy.sh rerank 가 깔고 띄운다",
        )
    resume()
    return answer("ready", ms=round((time.perf_counter() - started) * 1000))
