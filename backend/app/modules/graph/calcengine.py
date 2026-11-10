"""밤 그래프 계산의 엔진 — **수치 · GPU 라이브러리의 문**(ADR 0067).

scipy · NetworKit · cupy · pylibcugraph 는 이 파일에서만, 그것도 함수 안에서 늦게 부른다 — 웹
워커는 이 문을 열지 않는다(구조 시험). 받는 것 · 내는 것은 numpy 배열뿐이다.

- **기대는 수 · 비슷한 것**은 CPU 와 GPU 가 **같은 계산**을 한다 — 희소 행렬(numpy · scipy 또는
  cupy · cupyx)로 맨 위에서 끝까지 따라간다. 두 판의 답이 같아야 「GPU 판으로 바꾸니 순위가
  바뀌었다」 가 안 생긴다.
- **군집 · 다리 역할**은 엔진마다 다른 구현이다 — CPU 는 NetworKit(PLM · 매개 중심성
  어림), GPU 는 pylibcugraph(Louvain · 매개 중심성). 둘 다 돌릴 때마다 조금 다를 수 있다.
  다리 역할의 날값은 엔진마다 크기가 달라(NetworKit 은 순서 있는 짝을 센다 — cuGraph 의
  2배) **가장 큰 다리 대비 비율**로 맞춘다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

Ints = NDArray[np.int64]
Floats = NDArray[np.float64]

#: 한 번에 따라가는 맨 위의 수 — 묶음마다 (묶음 x 노드) 희소 행렬 하나.
BATCH_ROOTS = 2048
#: 비슷한 것을 세려면 맨 위마다 끝까지 펼친 구성을 들고 있어야 한다 — 그 칸 수의 상한. 넘으면
#: 비슷한 것만 건너뛴다(기대는 수는 묶음마다 세고 버려 상한이 없다). 칸 하나에 8바이트.
SIMILAR_MAX_CELLS = 500_000_000
#: 비슷한 것 한 묶음의 밀집 칸(행 x 같은 타입의 맨 위 수) — 4바이트씩.
SIMILAR_CHUNK_CELLS = 50_000_000
TOP_SIMILAR = 10
#: 다리 역할의 출발점 표본(견주기: 30만 노드에 2천 — 위 10이 엔진 셋에서 같았다).
BRIDGE_SAMPLES = 2000
SEED = 7


@dataclass(frozen=True)
class Availability:
    gpu: bool
    gpu_name: str | None
    networkit: bool
    reason: str | None
    """GPU 를 못 쓰는 까닭(패키지가 없다 · 장치가 안 보인다) — 화면 · 운영 문서."""


def availability() -> Availability:
    """이 프로세스에서 쓸 수 있는 엔진 — 늦게 불러 본다(없으면 까닭)."""
    try:
        import networkit  # noqa: F401

        has_nk = True
    except ImportError:
        has_nk = False
    try:
        import cupy
        import pylibcugraph  # noqa: F401
    except ImportError:
        return Availability(
            False, None, has_nk, "GPU 판 번들이 아닙니다(cupy · pylibcugraph 없음)"
        )
    try:
        count = int(cupy.cuda.runtime.getDeviceCount())
    except Exception as caught:  # 드라이버 · --nv 가 없으면 여기서 실패한다
        return Availability(False, None, has_nk, f"GPU 가 안 보입니다: {caught}")
    if count < 1:
        return Availability(False, None, has_nk, "GPU 가 안 보입니다(장치 0개)")
    raw = cupy.cuda.runtime.getDeviceProperties(0).get("name", b"")
    name = raw.decode() if isinstance(raw, bytes) else str(raw)
    return Availability(True, name, has_nk, None)


@dataclass
class Transitive:
    """이어지는 관계 하나의 셈."""

    depends_top: Ints
    """노드마다 끝까지 따라 올라가면 기대는 맨 위의 수(맨 위 자신은 0)."""
    roots: Ints
    similar_node: Ints = field(default_factory=lambda: np.zeros(0, np.int64))
    similar_other: Ints = field(default_factory=lambda: np.zeros(0, np.int64))
    similar_score: Floats = field(default_factory=lambda: np.zeros(0, np.float64))
    similar_rank: Ints = field(default_factory=lambda: np.zeros(0, np.int64))
    similar_skipped: str | None = None
    reach_cells: int = 0
    """맨 위마다 끝까지 펼친 구성의 칸 수 합 — 크기를 가늠하는 운영 숫자."""


@dataclass
class Whole:
    """기준정보 그래프 전체(방향 없음)의 셈."""

    cluster: Ints | None
    """노드마다 무리 번호(0 부터, 이음이 없으면 -1). 건너뛰었으면 None."""
    modularity: float | None
    bridge: Floats | None
    """노드마다 다리 역할 — 가장 큰 다리 대비 비율(0 ~ 1)."""
    skipped: dict[str, str] = field(default_factory=dict)


class Engine:
    """엔진 하나 — `cpu` 또는 `gpu`. 배열은 numpy 로 받아 numpy 로 낸다."""

    name = "cpu"

    def __init__(self, threads: int = 0) -> None:
        self.threads = threads or (os.cpu_count() or 1)

    # 배열 모듈 — CPU 는 numpy · scipy.sparse, GPU 는 cupy · cupyx.scipy.sparse.
    def _modules(self) -> tuple[Any, Any]:
        from scipy import sparse

        return np, sparse

    def _host(self, array: Any) -> Any:
        return np.asarray(array)

    def transitive(self, n: int, src: Ints, dst: Ints, type_of: Ints) -> Transitive:
        """이어지는 관계(부모 `src` → 자식 `dst`) — 맨 위(들어오는 선이 없는 노드)마다 끝까지
        따라가 (1) 노드마다 기대는 맨 위의 수 (2) 같은 타입의 맨 위끼리 끝까지 펼친 구성의
        자카드 위 `TOP_SIMILAR` 개.

        고리가 있어도 끝난다(이미 닿은 것은 다시 안 간다) — 고리 안에만 있어 맨 위에서 못 닿는
        노드는 0 이다."""
        xp, sp = self._modules()
        counts = np.zeros(n, np.int64)
        if n == 0 or len(src) == 0:
            return Transitive(counts, np.zeros(0, np.int64))
        has_parent = np.zeros(n, bool)
        has_parent[dst] = True
        has_child = np.zeros(n, bool)
        has_child[src] = True
        roots = np.flatnonzero(has_child & ~has_parent).astype(np.int64)
        ones = xp.ones(len(src), dtype=xp.float32)
        adj = sp.csr_matrix((ones, (xp.asarray(src), xp.asarray(dst))), shape=(n, n))
        adj.sum_duplicates()
        adj.data[:] = 1
        keep: list[Any] | None = []
        cells = 0
        for start in range(0, len(roots), BATCH_ROOTS):
            batch = roots[start : start + BATCH_ROOTS]
            reach = self._reach(xp, sp, adj, batch, n)
            cells += int(reach.nnz)
            counts += self._host(reach.sum(axis=0)).ravel().astype(np.int64)
            if keep is not None:
                if cells > SIMILAR_MAX_CELLS:
                    keep = None
                else:
                    keep.append(reach)
        out = Transitive(counts, roots, reach_cells=cells)
        if keep is None:
            out.similar_skipped = (
                f"끝까지 펼친 구성이 {cells:,}칸으로 상한({SIMILAR_MAX_CELLS:,})을 넘습니다"
            )
            return out
        whole = sp.vstack(keep).tocsr() if len(keep) > 1 else keep[0].tocsr()
        self._similar(xp, sp, whole, roots, type_of, out)
        return out

    @staticmethod
    def _reach(xp: Any, sp: Any, adj: Any, batch: Ints, n: int) -> Any:
        """맨 위 묶음에서 끝까지 닿는 것(자신은 빼고) — (묶음 x 노드) 희소 행렬, 값은 1."""
        k = len(batch)
        rows = xp.arange(k)
        start = sp.csr_matrix(
            (xp.ones(k, dtype=xp.float32), (rows, xp.asarray(batch))), shape=(k, n)
        )
        seen = start.copy()
        frontier = start
        for _ in range(n):
            nxt = frontier @ adj
            if nxt.nnz == 0:
                break
            nxt.data[:] = 1
            nxt = nxt - nxt.multiply(seen)
            nxt.eliminate_zeros()
            if nxt.nnz == 0:
                break
            seen = seen + nxt
            frontier = nxt
        reach = seen - start
        reach.eliminate_zeros()
        return reach

    def _similar(
        self, xp: Any, sp: Any, whole: Any, roots: Ints, type_of: Ints, out: Transitive
    ) -> None:
        """같은 타입의 맨 위끼리 — 끝까지 펼친 구성의 자카드, 줄마다 위 `TOP_SIMILAR` 개.

        차례는 (점수 큰 것, 노드 번호 작은 것) — 두 엔진의 답을 같게. 겹침 0 은 안 낸다."""
        indptr = self._host(whole.indptr)
        sizes_all = np.diff(indptr).astype(np.float64)
        nodes: list[Ints] = []
        others: list[Ints] = []
        scores: list[Floats] = []
        ranks: list[Ints] = []
        root_types = type_of[roots]
        for kind in np.unique(root_types):
            rows = np.flatnonzero(root_types == kind)
            rows = rows[sizes_all[rows] > 0]
            g = len(rows)
            if g < 2:
                continue
            group = whole[xp.asarray(rows)]
            group_t = group.T.tocsr()
            sizes = xp.asarray(sizes_all[rows], dtype=xp.float32)
            chunk = max(1, min(g, SIMILAR_CHUNK_CELLS // g))
            top = min(TOP_SIMILAR, g - 1)
            # 경계에서 같은 점수가 갈라지지 않게 넉넉히 받아 호스트에서 다시 세운다.
            take = min(g - 1, top * 2 + 1)
            for begin in range(0, g, chunk):
                end = min(g, begin + chunk)
                inter = (group[begin:end] @ group_t).toarray()
                union = sizes[begin:end, None] + sizes[None, :] - inter
                jac = xp.where(union > 0, inter / xp.maximum(union, 1), 0)
                span = xp.arange(end - begin)
                jac[span, span + begin] = -1
                pick = xp.argpartition(-jac, take - 1, axis=1)[:, :take]
                vals = self._host(xp.take_along_axis(jac, pick, axis=1)).astype(np.float64)
                cols = self._host(pick).astype(np.int64)
                for i in range(end - begin):
                    order = np.lexsort((roots[rows[cols[i]]], -vals[i]))[:top]
                    good = order[vals[i][order] > 0]
                    if not len(good):
                        continue
                    nodes.append(np.full(len(good), roots[rows[begin + i]], np.int64))
                    others.append(roots[rows[cols[i][good]]])
                    scores.append(np.round(vals[i][good], 6))
                    ranks.append(np.arange(1, len(good) + 1, dtype=np.int64))
        if nodes:
            out.similar_node = np.concatenate(nodes)
            out.similar_other = np.concatenate(others)
            out.similar_score = np.concatenate(scores)
            out.similar_rank = np.concatenate(ranks)

    def whole(
        self,
        n: int,
        a: Ints,
        b: Ints,
        *,
        want_cluster: bool = True,
        want_bridge: bool = True,
        bridge_samples: int = BRIDGE_SAMPLES,
    ) -> Whole:
        """방향 없는 그래프(짝 `a`-`b`, 겹침 없음) — 군집 · 다리 역할. NetworKit 이 없으면
        둘 다 건너뛴다(까닭을 남긴다)."""
        try:
            import networkit as nk
        except ImportError:
            reason = "NetworKit 이 없습니다"
            return Whole(None, None, None, {"cluster": reason, "bridge": reason})
        nk.setNumberOfThreads(self.threads)
        nk.setSeed(SEED, False)
        graph = nk.Graph(n, weighted=False, directed=False)
        if len(a):
            graph.addEdges((np.asarray(a, np.uint64), np.asarray(b, np.uint64)))
        degree = _degree(n, a, b)
        cluster = modularity = bridge = None
        if want_cluster:
            plm = nk.community.PLM(graph, refine=True, turbo=True)
            plm.run()
            partition = plm.getPartition()
            modularity = float(nk.community.Modularity().getQuality(partition, graph))
            cluster = _compact(np.asarray(partition.getVector(), np.int64), degree)
        if want_bridge:
            samples = max(1, min(bridge_samples, n))
            estimate = nk.centrality.EstimateBetweenness(
                graph, samples, normalized=False, parallel=True
            )
            estimate.run()
            bridge = _relative(np.asarray(estimate.scores(), np.float64))
        return Whole(cluster, modularity, bridge)


class GpuEngine(Engine):
    """GPU — cupy · pylibcugraph. 기대는 수 · 비슷한 것은 CPU 와 같은 계산을 cupy 로 한다."""

    name = "gpu"

    def _modules(self) -> tuple[Any, Any]:
        import cupy
        from cupyx.scipy import sparse

        return cupy, sparse

    def _host(self, array: Any) -> Any:
        import cupy

        return cupy.asnumpy(array) if isinstance(array, cupy.ndarray) else np.asarray(array)

    def transitive(self, n: int, src: Ints, dst: Ints, type_of: Ints) -> Transitive:
        try:
            return super().transitive(n, src, dst, type_of)
        finally:
            _release()

    def whole(
        self,
        n: int,
        a: Ints,
        b: Ints,
        *,
        want_cluster: bool = True,
        want_bridge: bool = True,
        bridge_samples: int = BRIDGE_SAMPLES,
    ) -> Whole:
        import cupy
        import pylibcugraph as plc

        degree = _degree(n, a, b)
        if n == 0 or not len(a):
            empty = np.full(n, -1, np.int64)
            return Whole(
                empty if want_cluster else None, None, np.zeros(n) if want_bridge else None
            )
        handle = plc.ResourceHandle()
        props = plc.GraphProperties(is_symmetric=True, is_multigraph=False)
        src = cupy.asarray(np.concatenate([a, b]), dtype=cupy.int32)
        dst = cupy.asarray(np.concatenate([b, a]), dtype=cupy.int32)
        graph = plc.SGGraph(
            handle,
            props,
            src,
            dst,
            vertices_array=cupy.arange(n, dtype=cupy.int32),
            weight_array=cupy.ones(len(src), dtype=cupy.float32),
            store_transposed=False,
            renumber=True,
            do_expensive_check=False,
        )
        cluster = modularity = bridge = None
        if want_cluster:
            vertices, parts, modularity = plc.louvain(handle, graph, 100, 1e-7, 1.0, False)
            raw = np.full(n, -1, np.int64)
            raw[cupy.asnumpy(vertices).astype(np.int64)] = cupy.asnumpy(parts).astype(np.int64)
            cluster = _compact(raw, degree)
            modularity = float(modularity)
        if want_bridge:
            samples = max(1, min(bridge_samples, n))
            vertices, values = plc.betweenness_centrality(
                handle, graph, samples, SEED, False, False, False
            )
            raw_b = np.zeros(n, np.float64)
            raw_b[cupy.asnumpy(vertices).astype(np.int64)] = cupy.asnumpy(values)
            bridge = _relative(raw_b)
        return Whole(cluster, modularity, bridge)


def _release() -> None:
    """cupy 가 쥔 GPU 메모리를 돌려준다 — 기대는 수의 희소 행렬이 풀에 남으면 다음 계산(매개
    중심성)이 메모리가 모자라 느려진다(RTX 3070 8GB 에서 7.6GB 를 쥐고 있었다)."""
    import cupy

    cupy.get_default_memory_pool().free_all_blocks()
    cupy.get_default_pinned_memory_pool().free_all_blocks()


def _degree(n: int, a: Ints, b: Ints) -> Ints:
    return (
        np.bincount(np.concatenate([a, b]), minlength=n).astype(np.int64)
        if n
        else np.zeros(0, np.int64)
    )


def _compact(raw: Ints, degree: Ints) -> Ints:
    """무리 번호를 0 부터 촘촘히 — 이음이 없는 노드는 -1(혼자인 것은 무리가 아니다)."""
    out = np.full(len(raw), -1, np.int64)
    linked = degree > 0
    if linked.any():
        _, compact = np.unique(raw[linked], return_inverse=True)
        out[linked] = compact.astype(np.int64)
    return out


def _relative(raw: Floats) -> Floats:
    top = float(raw.max()) if len(raw) else 0.0
    return raw / top if top > 0 else np.zeros(len(raw))


def make(name: str, threads: int = 0) -> Engine:
    """설정(`auto` · `cpu` · `gpu`) → 엔진. `gpu` 인데 못 쓰면 까닭과 함께 실패한다 — 말없이
    CPU 로 돌면 「GPU 로 돈다」 고 믿은 운영자가 모른다. `auto` 는 보이면 GPU."""
    if name == "cpu":
        return Engine(threads)
    seen = availability()
    if seen.gpu:
        return GpuEngine(threads)
    if name == "gpu":
        raise RuntimeError(f"GPU 엔진을 고르셨는데 쓸 수 없습니다 — {seen.reason}")
    return Engine(threads)
