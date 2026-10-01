"""인터페이스 규칙의 순수한 반쪽 — 세션 없이 본다(ADR 0006).

화면과 가져오기가 이 함수들을 그대로 부르므로, 여기서 틀리면 두 길이 **같이** 틀린다.
"""

from __future__ import annotations

from app.modules.ontology import interfaces as rules
from app.modules.ontology.interfaces import Catalog, Iface, Prop, Shape, TypeDef


def _prop(key: str, **shape: object) -> Prop:
    return Prop(key=key, shape=rules.shape_of(shape), label=key)


def _catalog(
    ifaces: dict[str, tuple[list[str], list[Prop]]],
    types: dict[str, tuple[list[str], list[Prop]]],
) -> Catalog:
    out = Catalog()
    for slug, (extends, props) in ifaces.items():
        out.interfaces[slug] = Iface(
            slug=slug, label=slug, extends=extends, props={p.key: p for p in props}
        )
    for slug, (implements, props) in types.items():
        out.types[slug] = TypeDef(
            slug=slug, label=slug, interfaces=implements, props={p.key: p for p in props}
        )
    return out


def test_모양의_차이를_사람의_말로_적는다() -> None:
    want = rules.shape_of({"data_type": "enum", "enum_options": ["KR", "US"]})
    have = rules.shape_of({"data_type": "text"})
    assert rules.shape_diff(want, have) == ["종류: 인터페이스는 enum, 이 타입은 text"]

    extra = rules.shape_of({"data_type": "enum", "enum_options": ["US", "JP"]})
    said = rules.shape_diff(want, extra)
    assert said == ["고를 값 — 인터페이스에만 KR · 이 타입에만 JP"]


def test_순서만_다른_고를_값과_안_쓰는_칸은_차이가_아니다() -> None:
    want = rules.shape_of({"data_type": "enum", "enum_options": ["KR", "US"]})
    have = rules.shape_of({"data_type": "enum", "enum_options": ["US", "KR"], "min_value": 3})
    assert rules.shape_diff(want, have) == []
    # 필수는 한 방향이라 차이로 안 친다(맞출 수 있다).
    strict = rules.shape_of({"data_type": "text", "required": True})
    assert rules.shape_diff(strict, rules.shape_of({"data_type": "text"})) == []


def test_공통_속성이_될_수_없는_것() -> None:
    assert rules.interface_property_error("d", {"data_type": "file"})
    assert rules.interface_property_error("o", {"data_type": "object_ref"})
    assert rules.interface_property_error("s", {"data_type": "text", "unique": True})
    assert rules.interface_property_error("g", {"data_type": "text", "default_value": "A"})
    assert rules.interface_property_error("r", {"data_type": "text", "inverse_label": "역"})
    assert rules.interface_property_error("ok", {"data_type": "text"}) is None


def test_상위_인터페이스는_자기_없는_것_고리를_막는다() -> None:
    extends_of = {"a": [], "b": ["a"], "c": ["b"]}
    known = set(extends_of)
    assert rules.extends_error("a", ["a"], extends_of, known)
    assert rules.extends_error("a", ["z"], extends_of, known)
    looped = rules.extends_error("a", ["c"], extends_of, known)
    assert looped and "a → c → b → a" in looped
    assert rules.extends_error("c", ["a"], extends_of, known) is None
    assert rules.closure(["c"], extends_of) == ["a", "b", "c"]


def test_할_일_없는_키는_만들고_같으면_채택하고_다르면_충돌() -> None:
    before = _catalog(
        {
            "eq": (
                [],
                [
                    _prop("maker", data_type="text"),
                    _prop("country", data_type="enum", enum_options=["KR"]),
                ],
            )
        },
        {"t": ([], [_prop("maker", data_type="text"), _prop("country", data_type="text")])},
    )
    after = before.clone()
    after.types["t"].interfaces = ["eq"]
    by_key = {one.key: one for one in rules.plan_bindings(before, after)}
    assert by_key["maker"].action == "adopt" and by_key["maker"].fresh
    assert by_key["country"].action == "conflict"
    assert "종류: 인터페이스는 enum, 이 타입은 text" in by_key["country"].conflict

    fresh = before.clone()
    fresh.types["u"] = TypeDef(slug="u", label="u", interfaces=["eq"])
    made = {one.key: one.action for one in rules.plan_bindings(before, fresh)}
    assert made == {"country": "create", "maker": "create"}


def test_이미_묶인_속성은_인터페이스를_따라_바뀐다() -> None:
    enum = {"data_type": "enum", "enum_options": ["KR"]}
    before = _catalog(
        {"eq": ([], [_prop("country", **enum)])}, {"t": (["eq"], [_prop("country", **enum)])}
    )
    after = before.clone()
    after.interfaces["eq"].props["country"] = _prop(
        "country", data_type="enum", enum_options=["KR", "US"], required=True
    )
    [todo] = rules.plan_bindings(before, after)
    assert todo.action == "sync" and not todo.fresh
    assert set(todo.changed) == {"enum_options", "required"}
    assert todo.shape == Shape(data_type="enum", enum_options=("KR", "US"), required=True)


def test_파일이_직접_보낸_모양은_전파로_덮지_않고_견준다() -> None:
    enum = {"data_type": "enum", "enum_options": ["KR"]}
    before = _catalog(
        {"eq": ([], [_prop("country", **enum)])}, {"t": (["eq"], [_prop("country", **enum)])}
    )
    after = before.clone()
    sent = _prop("country", data_type="enum", enum_options=["KR", "JP"])
    sent.explicit = True
    after.types["t"].props["country"] = sent
    [todo] = rules.plan_bindings(before, after)
    assert todo.action == "conflict"
    assert "인터페이스에서 수정합니다" in todo.conflict


def test_주인이_다른_타입은_바꾸지_않는다() -> None:
    before = _catalog({"eq": ([], [_prop("maker", data_type="text")])}, {"t": ([], [])})
    before.types["t"].managed_by = "hub"
    after = before.clone()
    after.types["t"].interfaces = ["eq"]
    [todo] = rules.plan_bindings(before, after)
    assert todo.action == "conflict" and "hub" in todo.conflict
    # 허브 묶음(source=hub)이면 된다.
    [ok] = rules.plan_bindings(before, after, source="hub")
    assert ok.action == "create"


def test_두_인터페이스가_같은_키를_다르게_정하면_구현할_수_없다() -> None:
    before = _catalog(
        {
            "a": ([], [_prop("k", data_type="text")]),
            "b": ([], [_prop("k", data_type="number")]),
            "c": ([], [_prop("k", data_type="text", required=True)]),
        },
        {"t": ([], [])},
    )
    after = before.clone()
    after.types["t"].interfaces = ["a", "b"]
    assert [one.action for one in rules.plan_bindings(before, after)] == ["conflict"]
    # 모양이 같으면 하나로 — 필수는 더 엄격한 쪽.
    both = before.clone()
    both.types["t"].interfaces = ["a", "c"]
    [made] = rules.plan_bindings(before, both)
    assert made.action == "create" and made.shape is not None and made.shape.required
