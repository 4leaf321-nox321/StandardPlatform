"""임베딩 모델 — 서버 안의 Ollama 가 돌리는 bge-m3(ADR 0050).

글을 뜻의 자리(숫자 1,024개)로 바꾼다. 뜻이 가까운 글은 자리도 가깝다 — 낱말이
달라도(「냉각수 누설」 과 「쿨런트 샘」) 찾는다. 모델은 이 서버 안에서 돈다. 글이 서버 밖으로
나가지 않는다.

여기는 **준비됐는지**를 본다 — 확장(pgvector) · Ollama · 모델 · 차원. 서버 화면이 이것을
띄운다. 배포가 깔지만 실패해도 배포는 멈추지 않으므로(앱은 모델 없이도 돈다), 그 사실이 드러날
자리가 여기다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.shared import vectors

#: 서버 화면이 기다리는 시간 — 모델을 올리지 않는 물음(`/api/show`)이라 짧다.
SHOW_SECONDS = 3.0
#: 워커가 글 한 묶음을 바꾸며 기다리는 시간 — GPU 없는 서버는 한 묶음(32개)에 수십 초가 든다.
#: 모델을 처음 메모리에 올리는 몇 초도 여기 든다.
BATCH_SECONDS = 300.0
#: 검색 한 번이 찾을 말을 바꾸며 기다리는 시간 — 넘으면 「뜻으로 찾기를 지금 못 씀」 으로
#: 답하고 글자로 찾은 것은 그대로 낸다(찾기 전체가 멈추지 않는다).
QUERY_SECONDS = 5.0

#: 시험이 가짜 Ollama 를 끼우는 자리(`httpx.MockTransport`).
transport: httpx.BaseTransport | None = None

State = Literal["ready", "off", "problem"]


@dataclass(frozen=True)
class Readiness:
    state: State
    backend: str
    model: str
    url: str
    vector_version: str | None
    """pgvector 의 판 — 없으면 None."""
    dim: int | None
    """모델이 말하는 차원 — 담는 칸(`vectors.DIM`)과 같아야 한다."""
    problem: str | None


def _show(url: str, model: str) -> dict[str, Any]:
    with httpx.Client(timeout=SHOW_SECONDS, transport=transport) as client:
        response = client.post(f"{url}/api/show", json={"model": model})
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    return body


def _dim(shown: dict[str, Any]) -> int | None:
    """모델 정보의 `<계열>.embedding_length`(bge-m3 은 `bert.embedding_length`)."""
    for key, value in (shown.get("model_info") or {}).items():
        if key.endswith(".embedding_length") and isinstance(value, int):
            return value
    return None


class Unavailable(RuntimeError):
    """뜻으로 바꾸지 못했다 — 끔 · 닿지 않음 · 모델 없음 · 차원 다름.

    말은 사람에게 그대로 보인다."""


def enabled() -> bool:
    return get_settings().embedding_backend != "off"


def embed(texts: list[str], *, seconds: float = BATCH_SECONDS) -> list[list[float]]:
    """글들을 뜻의 자리로 — 넣은 차례 그대로. Ollama 는 길이 1 로 맞춘 값을 준다
    (코사인 = 내적).

    **못 바꾸면 `Unavailable`** — 부르는 쪽(워커 · 검색)이 그 말을 그대로 적는다.
    """
    if not texts:
        return []
    settings = get_settings()
    if settings.embedding_backend == "off":
        raise Unavailable("뜻으로 찾기가 꺼져 있습니다(EMBEDDING_BACKEND=off)")
    url = settings.ollama_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=seconds, transport=transport) as client:
            response = client.post(
                f"{url}/api/embed", json={"model": settings.embedding_model, "input": texts}
            )
        response.raise_for_status()
        found = response.json().get("embeddings")
    except httpx.HTTPStatusError as caught:
        raise Unavailable(
            f"Ollama 가 {caught.response.status_code} 로 답했습니다({url}, 모델 "
            f"{settings.embedding_model}) — 서버 화면의 「뜻으로 찾기」 를 보세요"
        ) from caught
    except (httpx.HTTPError, ValueError) as caught:
        raise Unavailable(
            f"Ollama 에 닿지 않습니다({url}) — sudo ./deploy.sh ollama 가 깔고 띄운다"
        ) from caught
    if not isinstance(found, list) or len(found) != len(texts):
        raise Unavailable("Ollama 의 답에 뜻의 자리가 없거나 수가 다릅니다")
    for one in found:
        if not isinstance(one, list) or len(one) != vectors.DIM:
            raise Unavailable(
                f"모델 {settings.embedding_model} 의 차원이 {vectors.DIM} 이 아닙니다 — "
                "담는 칸은 "
                f"{vectors.DIM}차원이라 차원이 같은 모델만 됩니다(EMBEDDING_MODEL)"
            )
    return [[float(value) for value in one] for one in found]


def readiness(db: Session) -> Readiness:
    settings = get_settings()
    url = settings.ollama_base_url.rstrip("/")
    model = settings.embedding_model
    found = vectors.installed(db)

    def answer(state: State, problem: str | None = None, dim: int | None = None) -> Readiness:
        return Readiness(
            state=state,
            backend=settings.embedding_backend,
            model=model,
            url=url,
            vector_version=found[0] if found else None,
            dim=dim,
            problem=problem,
        )

    if settings.embedding_backend == "off":
        return answer("off")
    if why := vectors.problem(found):
        return answer("problem", why)
    try:
        shown = _show(url, model)
    except httpx.HTTPStatusError as caught:
        if caught.response.status_code == 404:
            return answer(
                "problem",
                f"Ollama 에 모델 {model} 이 없습니다 — sudo ./deploy.sh ollama 가 받는다",
            )
        return answer(
            "problem", f"Ollama 가 {caught.response.status_code} 로 답했습니다({url})"
        )
    except (httpx.HTTPError, ValueError):
        return answer(
            "problem",
            f"Ollama 에 닿지 않습니다({url}) — sudo ./deploy.sh ollama 가 깔고 띄운다",
        )
    dim = _dim(shown)
    if dim != vectors.DIM:
        return answer(
            "problem",
            f"모델 {model} 의 차원이 {dim or '알 수 없음'} 입니다 — 담는 칸은 "
            f"{vectors.DIM}차원이라 차원이 같은 모델만 됩니다(EMBEDDING_MODEL)",
            dim,
        )
    return answer("ready", dim=dim)
