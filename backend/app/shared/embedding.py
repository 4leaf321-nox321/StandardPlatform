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
