"""리랭크 실측(ADR 0072) — 지금의 「뜻으로 찾기」(거리 기준)와 리랭크를 같은 질문으로 견준다.

    .venv/bin/python scripts/rerank_eval.py \
        [--ollama http://127.0.0.1:11434] [--rerank http://127.0.0.1:11436] [--candidates 30]
    # 리랭커 서버 없이 원본 모델로(정답 점수 — transformers · torch 가 든 환경에서)
    python scripts/rerank_eval.py --reference <bge-reranker-v2-m3 폴더>

자료는 `rerank_eval_set.json`(가짜 — 운영에서 본 꼴을 겨눈다: 관계없는 질문 · 이름이 포함되는
다른 것).
지금 방식은 bge-m3 거리(코사인) 0.45 이상을 가까운 것부터 8개(`semantic.MIN_SIMILARITY` ·
`NEAR_LIMIT`). 리랭크는 거리로 후보 N개를 받아 리랭커 점수(0 ~ 1) 순으로, 기준 이상만 8개. 앱을
import 하지 않는다 — Ollama 와 리랭커만 있으면 돈다(DB 없음).

재는 것(질문 묶음마다):
    관계없음      아무것도 안 낸 질문의 비율(높을수록 좋다) · 낸 줄 수
    그 밖         첫 줄이 정답인가(hit@1) · 정답을 얼마나 냈나(정답 재현) · 틀린 줄(정답 · 허용
                  밖) 수
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
LIMIT = 8
COSINE_MIN = 0.45
SWEEP = (0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)


def embed(client: httpx.Client, url: str, model: str, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for start in range(0, len(texts), 32):
        reply = client.post(
            f"{url}/api/embed", json={"model": model, "input": texts[start : start + 32]}
        )
        reply.raise_for_status()
        out.extend(reply.json()["embeddings"])
    return out


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def sigmoid(value: float) -> float:
    # 큰 음수에서 넘치지 않게(앱의 `rerank._sigmoid` 와 같다).
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    small = math.exp(value)
    return small / (1 + small)


def rerank(
    client: httpx.Client, url: str, query: str, documents: list[str]
) -> tuple[list[float], float]:
    """리랭커(llama-server `/v1/rerank`)의 점수 — 문서 차례대로, 0 ~ 1. (점수, 걸린 초)."""
    started = time.perf_counter()
    reply = client.post(
        f"{url}/v1/rerank",
        json={"query": query, "documents": documents, "top_n": len(documents)},
    )
    reply.raise_for_status()
    took = time.perf_counter() - started
    raw = [0.0] * len(documents)
    for one in reply.json()["results"]:
        raw[one["index"]] = float(one["relevance_score"])
    # llama.cpp 는 날 점수(logit)를 준다 — 앱(`app/shared/rerank.py`)처럼 늘 시그모이드로 편다.
    return [sigmoid(one) for one in raw], took


class Reference:
    """원본 모델(BAAI 가중치)을 `transformers` 로 — 리랭커 서버가 같은 점수를 내는지 견줄 정답.
    FlagEmbedding 의 `compute_score(normalize=True)` 와 같다(로짓 → 시그모이드)."""

    def __init__(self, path: str) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSequenceClassification.from_pretrained(path).eval()

    def __call__(self, query: str, documents: list[str]) -> tuple[list[float], float]:
        started = time.perf_counter()
        scores: list[float] = []
        with self.torch.no_grad():
            for start in range(0, len(documents), 16):
                part = documents[start : start + 16]
                batch = self.tokenizer(
                    [query] * len(part),
                    part,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                )
                logits = self.model(**batch).logits.view(-1).float()
                scores.extend(sigmoid(float(one)) for one in logits)
        return scores, time.perf_counter() - started


def judge(shown: list[str], gold: list[str], ok: list[str]) -> dict[str, Any]:
    allowed = set(gold) | set(ok)
    return {
        "shown": len(shown),
        "hit1": bool(shown) and shown[0] in gold,
        "recall": (len(set(shown) & set(gold)) / len(gold)) if gold else None,
        "wrong": [one for one in shown if one not in allowed],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """묶음마다 — 관계없음은 「아무것도 안 낸 비율」, 그 밖은 hit@1 · 재현 · 틀린 줄."""
    out: dict[str, Any] = {}
    for group in sorted({one["group"] for one in rows}):
        mine = [one for one in rows if one["group"] == group]
        if group == "관계없음":
            out[group] = {
                "질문": len(mine),
                "안 낸 비율": round(sum(one["shown"] == 0 for one in mine) / len(mine), 2),
                "낸 줄": sum(one["shown"] for one in mine),
            }
        else:
            out[group] = {
                "질문": len(mine),
                "hit@1": round(sum(one["hit1"] for one in mine) / len(mine), 2),
                "정답 재현": round(statistics.mean(one["recall"] for one in mine), 2),
                "틀린 줄": sum(len(one["wrong"]) for one in mine),
            }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--embed-model", default="bge-m3")
    parser.add_argument("--rerank", default="http://127.0.0.1:11436")
    parser.add_argument("--candidates", type=int, default=30)
    parser.add_argument(
        "--reference",
        default=None,
        help="리랭커 서버 대신 원본 모델 폴더(BAAI/bge-reranker-v2-m3)를 transformers 로",
    )
    parser.add_argument("--set", default=str(HERE / "rerank_eval_set.json"))
    parser.add_argument(
        "--detail", type=float, default=None, help="이 기준의 질문별 결과도 찍는다"
    )
    parser.add_argument("--json", default=None, help="결과를 JSON 으로도 쓴다")
    args = parser.parse_args()

    data = json.loads(Path(args.set).read_text(encoding="utf-8"))
    corpus = data["corpus"]
    ids = [one["id"] for one in corpus]
    texts = [one["text"] for one in corpus]
    reference = Reference(args.reference) if args.reference else None
    with httpx.Client(timeout=120) as client:
        vectors = embed(client, args.ollama, args.embed_model, texts)
        asked = embed(
            client, args.ollama, args.embed_model, [one["q"] for one in data["queries"]]
        )
        baseline: list[dict[str, Any]] = []
        ranked: list[dict[str, Any]] = []
        times: list[float] = []
        for query, at in zip(data["queries"], asked, strict=True):
            near = sorted(
                ((cosine(at, vec), ids[n]) for n, vec in enumerate(vectors)), reverse=True
            )
            shown = [one for score, one in near if score >= COSINE_MIN][:LIMIT]
            baseline.append(
                {
                    **judge(shown, query["gold"], query["ok"]),
                    "group": query["group"],
                    "q": query["q"],
                    "top": [(one, round(score, 3)) for score, one in near[:3]],
                }
            )
            pool = [one for _score, one in near[: args.candidates]]
            documents = [texts[ids.index(one)] for one in pool]
            scores, took = (
                reference(query["q"], documents)
                if reference
                else rerank(client, args.rerank, query["q"], documents)
            )
            times.append(took)
            ranked.append(
                {
                    "group": query["group"],
                    "q": query["q"],
                    "gold": query["gold"],
                    "ok": query["ok"],
                    "scored": sorted(zip(scores, pool, strict=True), reverse=True),
                }
            )

    report: dict[str, Any] = {
        "지금(거리 0.45)": summarize(baseline),
        "리랭크 지연(초)": {
            "p50": round(statistics.median(times), 3),
            "p95": round(sorted(times)[int(len(times) * 0.95) - 1], 3),
            "최대": round(max(times), 3),
            "후보": args.candidates,
        },
    }
    for threshold in SWEEP:
        rows = []
        for one in ranked:
            shown = [doc for score, doc in one["scored"] if score >= threshold][:LIMIT]
            rows.append(
                {**judge(shown, one["gold"], one["ok"]), "group": one["group"], "q": one["q"]}
            )
        report[f"리랭크 ≥ {threshold}"] = summarize(rows)
    print(json.dumps(report, ensure_ascii=False, indent=1))

    if args.detail is not None:
        print(f"\n## 질문별 — 지금(거리) vs 리랭크 ≥ {args.detail}")
        for before, after in zip(baseline, ranked, strict=True):
            shown = [(doc, round(s, 3)) for s, doc in after["scored"] if s >= args.detail][
                :LIMIT
            ]
            top = [(doc, round(s, 3)) for s, doc in after["scored"][:3]]
            print(f"- [{after['group']}] {after['q']}")
            print(f"    지금 {before['shown']}줄 · 맨 위 {before['top']}")
            print(f"    리랭크 {len(shown)}줄 {shown[:4]} · 맨 위 점수 {top}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"report": report, "baseline": baseline, "ranked": ranked}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
