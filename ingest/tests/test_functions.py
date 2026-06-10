"""関数ライブラリ v0(asterism.functions)の単体テスト。

各関数は既存 vetted 実装への薄い委譲なので、ここでは (1) 中核変換の具体値、
(2) 委譲先と空文字契約の一致、(3) FnO 登録メタデータの健全性を確認する。
"""

from __future__ import annotations

import inspect

from asterism.functions import (
    FN,
    P_FIELD1,
    P_FIELD2,
    P_FIELD3,
    P_FIELD4,
    P_PATTERN,
    P_TABLE,
    P_TEMPLATE,
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

# コア(v0 8 関数)とパラメータ化プリミティブ(3)の名前。これらは IRI 安定のため
# 必ず REGISTRY に在る(削除/改名しない)。Track A 等が *追記* しても壊れないよう、
# 等値でなく包含で検証する(REGISTRY は append-only 運用)。
CORE_NAMES = {
    "date_iso",
    "float_array_max",
    "float_array_min",
    "float_array_count",
    "iri_safe",
    "slug",
    "qudt_quantity",
    "qudt_unit",
}
PRIMITIVE_NAMES = {"lookup", "regex_extract", "template"}
# コア関数拡充(Track A)。すべて単一入力(value)・str -> str。ロジックは asterism.transforms
# (bool_norm のみ primitives.lookup の bool 表に委譲)。
CORE_A_NAMES = {
    "number_clean",
    "percent_to_ratio",
    "range_min",
    "range_max",
    "datetime_iso",
    "year_only",
    "nfkc_norm",
    "trim_collapse",
    "strip_footnote",
    "bool_norm",
    "doi_norm",
    "url_canonical",
    "value_of",
    "unit_of",
}


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


def test_registry_names_unique_and_stable() -> None:
    names = [s.name for s in REGISTRY]
    assert len(names) == len(set(names)), "関数名は一意(IRI 衝突防止)"
    # コアとプリミティブは常に在る(IRI 安定)。append-only なので包含で検証。
    name_set = set(names)
    assert name_set >= CORE_NAMES
    assert name_set >= PRIMITIVE_NAMES
    assert name_set >= CORE_A_NAMES


def test_registry_specs_are_wellformed() -> None:
    """全 spec の健全性: fun_id 規約・呼び出し可能・パラメータ規約。

    パラメータ名(キー)は **関数の実シグネチャの引数名と一致必須** — ここが
    morph-kgc の束縛(どの定数/列がどの引数に入るか)を決めるので、ズレると静かに
    壊れる。IRI は FN 名前空間の ``p_`` 接頭辞(規約)。
    """
    for spec in REGISTRY:
        assert spec.fun_id == FN + spec.name
        assert callable(spec.func)
        assert spec.params, "全関数は最低 1 パラメータを取る"
        sig_params = inspect.signature(spec.func).parameters
        for arg_name, iri in spec.params.items():
            assert iri.startswith(FN + "p_"), f"{spec.name}: bad param IRI {iri}"
            assert arg_name in sig_params, f"{spec.name}: param {arg_name} not in signature"


def test_float_array_count_is_the_two_value_function() -> None:
    pair = {"value1": P_VALUE1, "value2": P_VALUE2}
    assert {s.name for s in REGISTRY if s.params == pair} == {"float_array_count"}


def test_primitive_specs_bind_constant_params() -> None:
    """プリミティブは value(列参照)に加え定数引数(table/pattern/template/field)を束縛。"""
    by_name = {s.name: s for s in REGISTRY}
    assert by_name["lookup"].params == {"value": P_VALUE, "table": P_TABLE}
    assert by_name["regex_extract"].params == {"value": P_VALUE, "pattern": P_PATTERN}
    assert by_name["template"].params == {
        "template": P_TEMPLATE,
        "field1": P_FIELD1,
        "field2": P_FIELD2,
        "field3": P_FIELD3,
        "field4": P_FIELD4,
    }


def test_core_a_functions_are_single_input() -> None:
    """Track A のコア関数は全て単一入力(value)で登録されている。"""
    single = {"value": P_VALUE}
    by_name = {s.name: s for s in REGISTRY}
    for name in CORE_A_NAMES:
        assert name in by_name, f"{name} が REGISTRY に無い"
        assert by_name[name].params == single, f"{name} は単一入力であるべき"


def test_bool_norm_delegates_to_bool_table() -> None:
    """bool_norm は真偽語彙をプリミティブの bool 表に委譲する(語彙の単一の真実源)。"""
    from asterism.functions import bool_norm
    from asterism.primitives import lookup

    for token in ("Yes", "no", "1", "off", "maybe"):
        assert bool_norm(token) == lookup(token, "bool")
    assert bool_norm("Yes") == "true"
    assert bool_norm("off") == "false"
    assert bool_norm("maybe") == ""  # 未知語は ""


def test_register_binds_every_function() -> None:
    """Morph-KGC の @udf 相当(fake)で、全関数が正しい IRI で登録されること。"""
    seen: list[tuple[dict, object]] = []

    def fake_udf(**kwargs):
        def deco(fn):
            seen.append((kwargs, fn))
            return fn

        return deco

    specs = register(fake_udf)
    assert len(seen) == len(specs) == len(REGISTRY)
    for (kwargs, fn), spec in zip(seen, REGISTRY, strict=True):
        assert kwargs["fun_id"] == FN + spec.name
        # 各パラメータ名 → IRI が udf に渡る(単一は value、2 入力は value1/value2)。
        for arg_name, iri in spec.params.items():
            assert kwargs[arg_name] == iri
        assert callable(fn)
