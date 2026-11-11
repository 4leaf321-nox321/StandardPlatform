"""SPARQL 사본에 묻는 **따로 도는 프로세스**(ADR 0070) — 표준 입력의 JSON 하나를 받아 표준
출력에 JSON 하나를 낸다.

Oxigraph(pyoxigraph)의 질의에는 시간 상한이 없다 — 웹 프로세스 안에서 돌리면 잘못 쓴 질의
하나가 그 프로세스를 붙잡는다. 그래서 질의마다 이 프로세스를 띄우고, 부른 쪽(`rdf/store.py`)이
시간 · 메모리 상한을 건다(넘으면 끊는다). **앱을 import 하지 않는다** — 뜨는 데 드는 시간을
줄인다(파이썬 표준 라이브러리 + pyoxigraph 만).

    입력: {"store": 폴더, "query": SPARQL, "prefixes": {…}, "limit": 줄}
    출력: {"kind": "select", "vars": [...], "rows": [[term, …]], "truncated": bool}
          {"kind": "ask", "answer": bool} · {"error": "…"}
    term: {"t": "uri" | "bnode" | "literal", "v": 값, "dt": 형 IRI, "lang": 말}
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _term(value: Any) -> dict[str, Any] | None:
    from pyoxigraph import BlankNode, Literal, NamedNode

    if value is None:
        return None
    if isinstance(value, NamedNode):
        return {"t": "uri", "v": value.value}
    if isinstance(value, BlankNode):
        return {"t": "bnode", "v": value.value}
    if isinstance(value, Literal):
        out: dict[str, Any] = {"t": "literal", "v": value.value}
        if value.language:
            out["lang"] = value.language
        else:
            out["dt"] = value.datatype.value
        return out
    return {"t": "literal", "v": str(value)}


def main() -> int:
    asked = json.loads(sys.stdin.read())
    try:
        from pyoxigraph import QueryBoolean, QuerySolutions, Store

        store = Store.read_only(asked["store"])
        result = store.query(asked["query"], prefixes=asked.get("prefixes") or None)
        if isinstance(result, QueryBoolean):
            out: dict[str, Any] = {"kind": "ask", "answer": bool(result)}
        elif not isinstance(result, QuerySolutions):
            out = {"error": "SELECT 또는 ASK 질의만 됩니다."}
        else:
            names = [one.value for one in result.variables]
            limit = int(asked.get("limit") or 200)
            rows: list[list[Any]] = []
            truncated = False
            for solution in result:
                if len(rows) >= limit:
                    truncated = True
                    break
                rows.append([_term(solution[name]) for name in names])
            out = {"kind": "select", "vars": names, "rows": rows, "truncated": truncated}
    except Exception as failure:  # 문법 · 평가 오류 — 부른 쪽이 사람의 말로 옮긴다
        out = {"error": f"{type(failure).__name__}: {failure}"}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
