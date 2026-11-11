"""SPARQL 사본에 **바뀐 것만** 쓴다(ADR 0071) — 밤 작업에서만 돈다(웹은 이것을 import 하지
않는다).

지금 판을 체크포인트로 떠서(같은 디스크에서 하드 링크 — 몇 초) 쉬는 쪽 폴더에 두고, 거기서
옛 줄을 지우고 새 줄을 넣은 뒤 바꿔 끼운다. 쓰는 동안 웹은 지금 판을 그대로 읽는다.

**빈 노드는 이름으로 못 찾는다** — `bulk_load` 가 이름을 새로 짓는다(`_:r<선 id>` 가 남지
않는다, 실측). 그래서 구체화한 진술은 몸통(`rdf:subject` · `rdf:predicate` · `rdf:object`)으로
찾아 지운다 — 같은 열쇠(출발 · 종류 · 도착)의 선은 **함께** 지우고 함께 다시 낸다.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyoxigraph import DefaultGraph, NamedNode, Quad, RdfFormat, Store, parse

_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_SUBJECT = NamedNode(_RDF + "subject")
_PREDICATE = NamedNode(_RDF + "predicate")
_OBJECT = NamedNode(_RDF + "object")
_TYPE = NamedNode(_RDF + "type")
_GRAPH = DefaultGraph()


def checkpoint(active: Path, target: Path) -> None:
    """지금 판(읽기 전용으로 연다 — 웹과 같이)을 `target` 에 뜬다. 고쳐도 지금 판은
    그대로다."""
    shutil.rmtree(target, ignore_errors=True)
    Store.read_only(str(active)).backup(str(target))


@dataclass
class Counted:
    removed: int = 0
    added: int = 0


class Writer:
    """쉬는 쪽 폴더의 사본에 쓴다. `relations` 는 관계 술어의 IRI(꺾쇠 없이)."""

    def __init__(self, path: Path, relations: Iterable[str]) -> None:
        self.store = Store(str(path))
        self.relations = {NamedNode(one) for one in relations}
        self.counted = Counted()

    def _drop(self, quads: Iterable[Quad]) -> None:
        for quad in list(quads):
            self.store.remove(quad)
            self.counted.removed += 1

    def _statements(self, node: NamedNode) -> set[Any]:
        """그 객체를 끝으로 둔 구체화한 진술(빈 노드)."""
        found = {q.subject for q in self.store.quads_for_pattern(None, _SUBJECT, node, _GRAPH)}
        found |= {q.subject for q in self.store.quads_for_pattern(None, _OBJECT, node, _GRAPH)}
        return found

    def drop_object(self, iri: str, *, moved: bool) -> None:
        """객체의 줄 — 그 객체가 주어인 줄(값 · 별칭 · 나가는 참조). `moved`(IRI 가 바뀌었거나
        생김 · 사라짐)면 그 객체를 가리키는 줄(들어오는 참조 · 관계 · 구체화한 진술)과 나가는
        관계까지."""
        node = NamedNode(iri)
        if moved:
            for said in self._statements(node):
                self._drop(self.store.quads_for_pattern(said, None, None, _GRAPH))
            self._drop(self.store.quads_for_pattern(None, None, node, _GRAPH))
            self._drop(self.store.quads_for_pattern(node, None, None, _GRAPH))
            return
        relations = self.relations
        self._drop(
            q
            for q in self.store.quads_for_pattern(node, None, None, _GRAPH)
            if q.predicate not in relations
        )

    def drop_keys(self, keys: Iterable[tuple[str, str, str]]) -> None:
        """열쇠(출발 · 종류 · 도착 IRI)마다 그 줄과, 같은 몸통의 구체화한 진술 전부. 출발마다
        그 진술을 한 번만 훑는다 — 한 출발에서 나간 선 수만 개가 바뀌어도 제곱이 안 된다."""
        by_src: dict[str, set[tuple[str, str]]] = {}
        for src, relation, dst in keys:
            by_src.setdefault(src, set()).add((relation, dst))
        for src, wanted in by_src.items():
            s = NamedNode(src)
            for relation, dst in wanted:
                self._drop(
                    self.store.quads_for_pattern(
                        s, NamedNode(relation), NamedNode(dst), _GRAPH
                    )
                )
            for said in {
                q.subject for q in self.store.quads_for_pattern(None, _SUBJECT, s, _GRAPH)
            }:
                body = list(self.store.quads_for_pattern(said, None, None, _GRAPH))
                p = next((q.object for q in body if q.predicate == _PREDICATE), None)
                o = next((q.object for q in body if q.predicate == _OBJECT), None)
                if (
                    isinstance(p, NamedNode)
                    and isinstance(o, NamedNode)
                    and (p.value, o.value) in wanted
                ):
                    self._drop(body)

    def add(self, text: str) -> None:
        """N-Triples 한 덩이를 넣는다 — 이미 있는 줄은 세지 않는다(판의 트리플 수가 맞게)."""
        # 같은 덩이 안의 겹침(나가는 참조 · 들어오는 참조가 같은 줄)은 한 번만 센다.
        parsed = dict.fromkeys(parse(text.encode("utf-8"), format=RdfFormat.N_TRIPLES))
        fresh = [quad for quad in parsed if quad not in self.store]
        if fresh:
            self.store.extend(fresh)
            self.counted.added += len(fresh)

    def count(self, query: str) -> int:
        """`SELECT (COUNT(*) AS ?n)` 하나의 값."""
        rows = list(self.store.query(query))  # type: ignore[arg-type]
        return int(rows[0][0].value) if rows and rows[0][0] is not None else 0

    def class_counts(self) -> dict[str, int]:
        """`rdf:type` 의 목적어마다 주어 수(꺾쇠 없이)."""
        out: dict[str, int] = {}
        for row in self.store.query(  # type: ignore[union-attr]
            "SELECT ?t (COUNT(?s) AS ?n) WHERE { ?s a ?t } GROUP BY ?t"
        ):
            out[row[0].value] = int(row[1].value)
        return out

    def close(self) -> None:
        self.store.flush()
        del self.store
