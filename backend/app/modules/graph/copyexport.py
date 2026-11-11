"""그래프 사본(4단계)을 실을 파일 — PostgreSQL → `neo4j-admin database import` 의 CSV.

사본은 **SP 를 비춘 읽기용**이다(설치마다 Neo4j 무료판 하나, 127.0.0.1). 밤마다 이것으로 쉬고
있는 쪽 폴더에 통째로 싣고 바꿔 끼운다(`deploy/graphcopy.sh`) — 바뀐 것만 고치는 길보다
단순하고, 사본이 SP 와 어긋날 틈이 없다. 백업하지 않는다: 망가지면 다시 싣는다.

## 이름 — 질의를 쓰는 쪽(AI)이 읽는 규칙. `copyschema` 가 같은 규칙으로 알려 준다.

- 노드: 라벨 `Obj` + **타입 slug**(`:gb_part`). 속성 `id`(SP 의 uuid) · `key` · `label` ·
  `status` · `ws`(소유 부서 slug) + 그 타입의 칸(키 그대로 — 고정 이름과 겹치면 `p_<키>`).
  숫자는 수, 예/아니오는 참/거짓, 날짜 · 시각은 ISO 글자(글자로 견줘도 차례가 맞다), 여러 값
  칸은 목록. 긴 글은 앞 `LONG_TEXT`자. 참조 · 파일 · 문서 칸은 속성이 아니다(참조는 선이다).
- 관계: **관계 slug 그대로**(`-[:gb_bom]->`, 방향은 SP 의 출발 → 도착) — **오늘 유효한 선만**,
  선의 칸(`qty` …)과 `valid_from` · `valid_to`.
- 참조 칸: `-[:ref_<칸 키>]->`(가리키는 쪽 → 가리켜지는 쪽). 「이 모델을 가리키는 시험」 은
  `(t:gb_test)-[:ref_model]->(m:gb_model)`.
- 지운 것 · 병합돼 사라진 것은 없다. 원 표를 비추는 타입(부서 · 계정)은 없다.
- 판 표시: 라벨 `_SPCopy` 노드 하나 — `copied_at` · `sp_version` · 수.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import sql
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import version
from app.modules.objects import edges as edge_rule
from app.modules.objects import system
from app.modules.objects.axes import NUMERIC_RE
from app.modules.objects.scope import as_scope
from app.modules.ontology.models import ObjectType, PropertyDef, RelationType

#: 노드의 고정 속성 — 칸 키가 이것과 같으면 `p_<키>` 로 싣는다.
FIXED = ("id", "key", "label", "status", "ws")
NODE_LABEL = "Obj"
META_LABEL = "_SPCopy"
REF_PREFIX = "ref_"
#: 긴 글 칸은 앞부분만 — 보고서 본문을 통째로 실으면 사본이 본문 저장소가 된다.
LONG_TEXT = 2000
#: 여러 값 칸의 원소 구분 — 값 안에 거의 안 나오는 글자(단위 구분 기호 U+001F).
ARRAY_DELIMITER = "\x1f"
_SAFE = re.compile(r"^[a-z][a-z0-9_]*$")
#: 칸의 종류 → CSV 머리의 형(빈 글은 글자).
_KIND = {"number": "double", "bool": "boolean"}
_SKIP = frozenset({"object_ref", "file", "document"})


def prop_name(key: str) -> str:
    """칸 키 → 노드 속성 이름."""
    return f"p_{key}" if key in FIXED else key


def ref_type(key: str) -> str:
    """참조 칸 → 관계 종류 이름."""
    return f"{REF_PREFIX}{key}"


def copied(definition: PropertyDef) -> bool:
    """이 칸이 노드 속성으로 실리나."""
    return definition.data_type not in _SKIP


def value_kind(definition: PropertyDef) -> str:
    """사본에서의 값의 꼴 — `string` · `double` · `boolean`, 여러 값이면 끝에 `[]`."""
    kind = _KIND.get(definition.data_type, "string")
    return f"{kind}[]" if definition.multi else kind


@dataclass
class Part:
    """`neo4j-admin import` 의 파일 한 벌 — `--nodes=<labels>=<header>,<data>` 또는
    `--relationships=<type>=…`."""

    kind: str
    """`nodes` · `relationships`."""
    name: str
    """노드면 라벨들(`Obj:gb_part`), 관계면 종류 이름."""
    header: str
    data: str
    rows: int = 0


@dataclass
class Manifest:
    copied_at: str
    sp_version: str
    parts: list[Part] = field(default_factory=list)
    nodes: int = 0
    relationships: int = 0
    seconds: float = 0.0

    def args(self, root: Path) -> list[str]:
        """`neo4j-admin database import full` 의 파일 인자 — 한 줄에 하나(`graphcopy.sh` 가
        읽는다)."""
        return [
            f"--{part.kind}={part.name}={root / part.header},{root / part.data}"
            for part in self.parts
            if part.rows
        ]


def _lit(value: str) -> sql.Composable:
    return sql.Literal(value)


def _prop_expr(definition: PropertyDef, source: str = "o") -> sql.Composable:
    """칸 하나를 CSV 칸으로 — 못 읽는 값은 비운다(가져오기가 그 줄에서 멈추지 않게). `source`
    는 `properties` 를 가진 별칭(객체 `o` · 선 `r`)."""
    key = _lit(definition.key)
    column = sql.SQL("{}.properties").format(sql.Identifier(source))
    raw = sql.SQL("{}->>{}").format(column, key)
    node = sql.SQL("{}->{}").format(column, key)
    numeric = _lit(NUMERIC_RE)
    if definition.multi:
        elements = sql.SQL(
            "jsonb_array_elements_text(CASE WHEN jsonb_typeof({node}) = 'array' THEN {node} "
            "ELSE '[]'::jsonb END)"
        ).format(node=node)
        if definition.data_type == "number":
            picked = sql.SQL("SELECT e FROM {} e WHERE e ~ {}").format(elements, numeric)
        elif definition.data_type == "bool":
            picked = sql.SQL(
                "SELECT lower(e) FROM {} e WHERE lower(e) IN ('true', 'false')"
            ).format(elements)
        else:
            picked = sql.SQL("SELECT replace(e, {sep}, ' ') FROM {elements} e").format(
                sep=_lit(ARRAY_DELIMITER), elements=elements
            )
        return sql.SQL("NULLIF(array_to_string(ARRAY({}), {}), '')").format(
            picked, _lit(ARRAY_DELIMITER)
        )
    if definition.data_type == "number":
        return sql.SQL("CASE WHEN {raw} ~ {re} THEN {raw} END").format(raw=raw, re=numeric)
    if definition.data_type == "bool":
        return sql.SQL(
            "CASE WHEN lower({raw}) IN ('true', 'false') THEN lower({raw}) END"
        ).format(raw=raw)
    if definition.data_type == "text_long":
        return sql.SQL("left({}, {})").format(raw, sql.Literal(LONG_TEXT))
    return raw


def _copy_to(db: Session, query: sql.Composable, path: Path) -> int:
    """`COPY (…) TO STDOUT (FORMAT csv)` 를 파일로 — 수천만 줄을 파이썬 객체로 안 만든다."""
    raw = db.connection().connection.driver_connection
    assert raw is not None
    with raw.cursor() as cursor, path.open("wb") as out:
        statement = sql.SQL("COPY ({}) TO STDOUT WITH (FORMAT csv)").format(query)
        with cursor.copy(statement) as copy:
            for chunk in copy:
                out.write(chunk)
        return max(0, int(cursor.rowcount))


def _header(path: Path, columns: list[str]) -> None:
    path.write_text(",".join(columns) + "\n", encoding="utf-8")


def object_types(db: Session) -> list[ObjectType]:
    """사본에 싣는 타입 — 원 표를 비추는 타입(부서 · 계정)은 뺀다."""
    rows = db.scalars(select(ObjectType).order_by(ObjectType.slug)).all()
    return [row for row in rows if not system.is_system(row) and _SAFE.match(row.slug)]


def relation_types(db: Session) -> list[RelationType]:
    rows = db.scalars(select(RelationType).order_by(RelationType.slug)).all()
    return [row for row in rows if _SAFE.match(row.slug)]


def edge_defs(db: Session) -> dict[uuid.UUID, list[PropertyDef]]:
    """관계 종류 → 선의 칸(관계 속성)."""
    out: dict[uuid.UUID, list[PropertyDef]] = {}
    for one in db.scalars(
        select(PropertyDef)
        .where(PropertyDef.owner_kind == "relation")
        .order_by(PropertyDef.key)
    ):
        out.setdefault(one.owner_id, []).append(one)
    return out


def export(db: Session, root: Path) -> Manifest:
    """`root` 에 CSV 와 `manifest.json` · `import.args` 를 쓴다. 트랜잭션 하나에서 읽는다 —
    노드와 선이 같은 때의 것이다(REPEATABLE READ 는 부르는 쪽이 건다)."""
    started = datetime.now(UTC)
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob("*.csv"):
        old.unlink()
    manifest = Manifest(copied_at=started.isoformat(), sp_version=version.current())
    alive_obj = sql.SQL("{a}.deleted_at IS NULL AND {a}.merged_into_id IS NULL")
    kinds: dict[str, ObjectType] = {}

    for row in object_types(db):
        defs = [one for one in as_scope(db, row).defs if copied(one) and _SAFE.match(one.key)]
        columns = ["id:ID", "key", "label", "status", "ws"]
        columns += [f"{prop_name(one.key)}:{value_kind(one)}" for one in defs]
        exprs = [_prop_expr(one) for one in defs]
        query = sql.SQL(
            "SELECT o.id::text, o.key, o.label, o.status, w.slug{extra} FROM objects o "
            "LEFT JOIN workspaces w ON w.id = o.owner_workspace_id "
            "WHERE o.type_id = {type_id}::uuid AND {alive}"
        ).format(
            extra=sql.SQL("").join(sql.SQL(", ") + one for one in exprs),
            type_id=_lit(str(row.id)),
            alive=alive_obj.format(a=sql.Identifier("o")),
        )
        part = Part(
            "nodes", f"{NODE_LABEL}:{row.slug}", f"n_{row.slug}.h.csv", f"n_{row.slug}.csv"
        )
        _header(root / part.header, columns)
        part.rows = _copy_to(db, query, root / part.data)
        manifest.parts.append(part)
        manifest.nodes += part.rows
        kinds[row.slug] = row

    day = edge_rule.at(None)
    alive_edge = sql.SQL(edge_rule.alive_sql("r", day))
    owned = edge_defs(db)
    for relation in relation_types(db):
        defs = [
            one for one in owned.get(relation.id, []) if copied(one) and _SAFE.match(one.key)
        ]
        columns = [":START_ID", ":END_ID", "valid_from", "valid_to"]
        columns += [f"{one.key}:{value_kind(one)}" for one in defs if one.key not in FIXED]
        exprs = [_prop_expr(one, "r") for one in defs if one.key not in FIXED]
        query = sql.SQL(
            "SELECT r.src_object_id::text, r.dst_object_id::text, r.valid_from::text, "
            "r.valid_to::text{extra} FROM object_relations r "
            "JOIN objects s ON s.id = r.src_object_id AND {alive_s} "
            "JOIN objects d ON d.id = r.dst_object_id AND {alive_d} "
            "WHERE r.relation = {slug} AND {alive}"
        ).format(
            extra=sql.SQL("").join(sql.SQL(", ") + one for one in exprs),
            alive_s=alive_obj.format(a=sql.Identifier("s")),
            alive_d=alive_obj.format(a=sql.Identifier("d")),
            slug=_lit(relation.slug),
            alive=alive_edge,
        )
        part = Part(
            "relationships",
            relation.slug,
            f"r_{relation.slug}.h.csv",
            f"r_{relation.slug}.csv",
        )
        _header(root / part.header, columns)
        part.rows = _copy_to(db, query, root / part.data)
        manifest.parts.append(part)
        manifest.relationships += part.rows

    for row in kinds.values():
        for one in as_scope(db, row).defs:
            if one.data_type != "object_ref" or not _SAFE.match(one.key):
                continue
            name = ref_type(one.key)
            query = sql.SQL(
                "SELECT f.src_id::text, f.dst_id::text FROM object_refs f "
                "JOIN objects s ON s.id = f.src_id AND {alive_s} "
                "JOIN objects d ON d.id = f.dst_id AND {alive_d} "
                "WHERE f.src_type_id = {type_id}::uuid AND f.key = {key}"
            ).format(
                alive_s=alive_obj.format(a=sql.Identifier("s")),
                alive_d=alive_obj.format(a=sql.Identifier("d")),
                type_id=_lit(str(row.id)),
                key=_lit(one.key),
            )
            stem = f"f_{row.slug}__{one.key}"
            part = Part("relationships", name, f"{stem}.h.csv", f"{stem}.csv")
            _header(root / part.header, [":START_ID", ":END_ID"])
            part.rows = _copy_to(db, query, root / part.data)
            manifest.parts.append(part)
            manifest.relationships += part.rows

    meta = Part("nodes", META_LABEL, "meta.h.csv", "meta.csv", rows=1)
    _header(
        root / meta.header,
        ["id:ID", "copied_at", "sp_version", "nodes:long", "relationships:long"],
    )
    (root / meta.data).write_text(
        f"_sp_copy,{manifest.copied_at},{manifest.sp_version},"
        f"{manifest.nodes},{manifest.relationships}\n",
        encoding="utf-8",
    )
    manifest.parts.append(meta)
    manifest.seconds = round((datetime.now(UTC) - started).total_seconds(), 2)
    (root / "manifest.json").write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return manifest


def write_args(manifest: Manifest, root: Path, target: Path) -> None:
    """`import.args` — 컨테이너 안에서 보이는 경로(`target`)로."""
    (root / "import.args").write_text(
        "\n".join(manifest.args(target)) + "\n", encoding="utf-8"
    )


def as_json(manifest: Manifest) -> dict[str, Any]:
    return asdict(manifest)
