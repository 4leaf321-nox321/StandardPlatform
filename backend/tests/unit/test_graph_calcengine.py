"""밤 그래프 계산의 엔진(ADR 0067) — 손으로 셀 수 있는 그래프에서 답이 그대로인가.

    모델 M1 ─ A1 ─ P1        기대는 수(맨 위 = 모델 둘): A1 2 · A2 1 · P1 2 · P2 2 ·
                             P3 1 · P4 1
       │     └── P2        M1 의 구성 {A1, P4, P1, P2}, M2 의 구성 {A1, A2, P1, P2, P3}
       └ P4                 → 겹침 3 / 합 6 = 0.5
    모델 M2 ─ A1, A2 ─ P2 · P3

GPU 가 보이면 같은 그래프에서 CPU 와 GPU 의 기대는 수 · 비슷한 것이 **같아야** 한다.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.modules.graph import calcengine

M1, M2, A1, A2, P1, P2, P3, P4 = range(8)
SRC = np.array([M1, M1, M2, M2, A1, A1, A2, A2], np.int64)
DST = np.array([A1, P4, A1, A2, P1, P2, P2, P3], np.int64)
TYPES = np.array([1, 1, 0, 0, 0, 0, 0, 0], np.int64)


def test_기대는_수는_끝까지_올라가_닿는_맨_위의_수다() -> None:
    got = calcengine.Engine(threads=1).transitive(8, SRC, DST, TYPES)
    assert sorted(got.roots.tolist()) == [M1, M2]
    assert got.depends_top.tolist() == [0, 0, 2, 1, 2, 2, 1, 1]
    assert got.reach_cells == 4 + 5


def test_비슷한_것은_같은_타입의_맨_위끼리_끝까지_펼친_구성의_자카드다() -> None:
    got = calcengine.Engine(threads=1).transitive(8, SRC, DST, TYPES)
    pairs = sorted(
        zip(
            got.similar_node.tolist(),
            got.similar_other.tolist(),
            got.similar_score.tolist(),
            got.similar_rank.tolist(),
            strict=True,
        )
    )
    assert pairs == [(M1, M2, 0.5, 1), (M2, M1, 0.5, 1)]


def test_구성을_들고_있을_칸이_상한을_넘으면_비슷한_것만_건너뛴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calcengine, "SIMILAR_MAX_CELLS", 3)
    got = calcengine.Engine(threads=1).transitive(8, SRC, DST, TYPES)
    assert got.depends_top.tolist() == [0, 0, 2, 1, 2, 2, 1, 1]
    assert len(got.similar_node) == 0
    assert got.similar_skipped and "상한" in got.similar_skipped


def test_선이_없으면_빈_답이다() -> None:
    empty = np.zeros(0, np.int64)
    got = calcengine.Engine(threads=1).transitive(3, empty, empty, np.zeros(3, np.int64))
    assert got.depends_top.tolist() == [0, 0, 0]
    assert len(got.roots) == 0


def test_군집은_뭉친_무리를_가르고_혼자인_것은_무리가_아니다() -> None:
    """세모 둘(0-1-2 · 3-4-5)을 6 이 잇는다. 7 은 아무것과도 안 이어졌다."""
    a = np.array([0, 0, 1, 3, 3, 4, 2, 3], np.int64)
    b = np.array([1, 2, 2, 4, 5, 5, 6, 6], np.int64)
    got = calcengine.Engine(threads=1).whole(8, a, b, bridge_samples=8)
    assert got.cluster is not None and got.bridge is not None
    cluster = got.cluster.tolist()
    assert cluster[0] == cluster[1] == cluster[2]
    assert cluster[3] == cluster[4] == cluster[5]
    assert cluster[0] != cluster[3]
    assert cluster[7] == -1
    assert got.modularity is not None and got.modularity > 0.3
    # 다리 — 세모 안의 꼭짓점(0 · 1 · 4 · 5)은 어떤 길도 안 지난다. 가장 큰 다리가 1.
    assert got.bridge[[0, 1, 4, 5]].tolist() == [0.0, 0.0, 0.0, 0.0]
    assert float(got.bridge.max()) == 1.0
    assert int(np.argmax(got.bridge)) in (2, 3, 6)


def test_엔진_고르기_GPU_를_골랐는데_못_쓰면_까닭과_함께_실패한다() -> None:
    seen = calcengine.availability()
    assert calcengine.make("cpu").name == "cpu"
    if seen.gpu:
        assert calcengine.make("auto").name == "gpu"
        return
    assert calcengine.make("auto").name == "cpu"
    with pytest.raises(RuntimeError, match="GPU"):
        calcengine.make("gpu")


@pytest.mark.skipif(not calcengine.availability().gpu, reason="GPU 가 없다(CPU 판)")
def test_GPU_와_CPU_의_기대는_수와_비슷한_것이_같다() -> None:
    rng = np.random.default_rng(3)
    n, roots = 400, 40
    src_parts, dst_parts = [], []
    for child in range(roots, n):
        parents = rng.choice(child, size=min(child, 2), replace=False)
        src_parts.extend(parents.tolist())
        dst_parts.extend([child] * len(parents))
    src = np.asarray(src_parts, np.int64)
    dst = np.asarray(dst_parts, np.int64)
    types = (np.arange(n) % 2).astype(np.int64)
    cpu = calcengine.Engine(threads=2).transitive(n, src, dst, types)
    gpu = calcengine.GpuEngine(threads=2).transitive(n, src, dst, types)
    assert cpu.depends_top.tolist() == gpu.depends_top.tolist()
    assert cpu.similar_node.tolist() == gpu.similar_node.tolist()
    assert np.allclose(cpu.similar_score, gpu.similar_score)
