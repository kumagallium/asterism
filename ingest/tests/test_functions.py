"""関数ライブラリ v0(asterism.functions)の単体テスト。

各関数は既存 vetted 実装への薄い委譲なので、ここでは (1) 中核変換の具体値、
(2) 委譲先と空文字契約の一致、(3) FnO 登録メタデータの健全性を確認する。
"""

from __future__ import annotations

from asterism.functions import (
    FN,
    P_VALUE,
    P_VALUE1,
    P_VALUE2,
    REGISTRY,
    date_iso,
    float_array_count,
    float_array_max,
    float_array_min,
    iri_safe,
    qudt_quantity,
    qudt_unit,
    register,
    slug,
)
from asterism.qudt import quantity_kind_iri, unit_iri
from asterism.starrydata import safe_url


def test_date_iso_concrete() -> None:
    assert date_iso('{"date_parts":[[2013,12,5]]}') == "2013-12-05"
    assert date_iso('{"date_parts":[[2013]]}') == "2013-01-01"  # 月日欠落は 1 で補完
    assert date_iso("") == ""
    assert date_iso("not json") == ""


def test_float_array_max_min_concrete() -> None:
    assert float_array_max("[1, 2.5, -3]") == "2.5"
    assert float_array_min("[1, 2.5, -3]") == "-3.0"
    # 壊れ要素は除外、配列が壊れていれば空文字
    assert float_array_max("[1, null, 2]") == "2.0"
    assert float_array_max("[]") == ""
    assert float_array_max("garbage") == ""


def test_float_array_count_concrete() -> None:
    # 点数 = min(len(xs), len(ys))。手続き経路と同じ定義。
    assert float_array_count("[1, 2, 3]", "[10, 20, 30]") == "3"
    assert float_array_count("[1, 2, 3, 4]", "[10, 20]") == "2"  # 短い方に合わせる
    assert float_array_count("[1, null, 3]", "[10, 20, 30]") == "2"  # 壊れ要素は除外
    # 片方でも有効点が無ければ 0 点 → "" (トリプル無し)
    assert float_array_count("[]", "[10, 20]") == ""
    assert float_array_count("[1, 2]", "garbage") == ""
    assert float_array_count("", "") == ""


def test_slug_concrete() -> None:
    assert slug("Hello World!") == "hello-world"
    assert slug("") == "unknown"


def test_delegation_and_empty_contract() -> None:
    """委譲先が None を返す入力で、ライブラリは "" を返す(手続き経路の None 相当)。"""
    for url in ("https://doi.org/10.1/x", "unknown", ""):
        assert iri_safe(url) == (safe_url(url) or "")
    for name in ("Seebeck coefficient", "definitely-not-a-property"):
        assert qudt_quantity(name) == (quantity_kind_iri(name) or "")
    for unit in ("V/K", "definitely-not-a-unit"):
        assert qudt_unit(unit) == (unit_iri(unit) or "")
    # 未マップは必ず空文字
    assert qudt_quantity("definitely-not-a-property") == ""
    assert qudt_unit("definitely-not-a-unit") == ""


def test_registry_is_closed_and_unique() -> None:
    names = [s.name for s in REGISTRY]
    assert len(names) == len(set(names)), "関数名は一意(IRI 衝突防止)"
    assert len(REGISTRY) == 8
    single = {"value": P_VALUE}
    pair = {"value1": P_VALUE1, "value2": P_VALUE2}
    for spec in REGISTRY:
        assert spec.fun_id == FN + spec.name
        assert spec.params in (single, pair)
        assert callable(spec.func)
    # 2 入力は float_array_count のみ。それ以外は単一入力。
    assert {s.name for s in REGISTRY if s.params == pair} == {"float_array_count"}


def test_register_binds_every_function() -> None:
    """Morph-KGC の @udf 相当(fake)で、全関数が正しい IRI で登録されること。"""
    seen: list[tuple[dict, object]] = []

    def fake_udf(**kwargs):
        def deco(fn):
            seen.append((kwargs, fn))
            return fn

        return deco

    specs = register(fake_udf)
    assert len(seen) == len(specs) == 8
    for (kwargs, fn), spec in zip(seen, REGISTRY, strict=True):
        assert kwargs["fun_id"] == FN + spec.name
        # 各パラメータ名 → IRI が udf に渡る(単一は value、2 入力は value1/value2)。
        for arg_name, iri in spec.params.items():
            assert kwargs[arg_name] == iri
        assert callable(fn)
