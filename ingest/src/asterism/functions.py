"""宣言経路(RML/FnO)が参照してよい、閉じた検証済み関数ライブラリ(v0)。

なぜこのモジュールがあるか
--------------------------
宣言的マッピング(RML)は「列 → 述語」の対応しか書けない。日付の正規化や
配列セルの集計といった *real computation*(宣言で書けない難所)は、ここに集めた
**閉じた集合の関数**だけが担う。AI が出すマッピングはこの集合を *参照* できるだけで、
新しいコードを混ぜ込めない — これが「生成コードを毎回レビューする」負担を
「閉じた関数集合を一度レビューする」負担へ置き換える肝。

各関数は ``asterism.starrydata`` / ``asterism.qudt`` の既存実装へ薄く委譲するだけで、
ロジックを二重化しない(単一の真実源)。FnO は文字列を受け渡すので、ここでは
すべて ``str -> str`` 形にし、「該当なし」は空文字 ``""`` で表す
(手続き経路の ``None`` 相当。空文字の objectMap は substrate 側で出力しない)。

Morph-KGC への登録
------------------
Morph-KGC は ``udfs.py`` を読み込むとき ``udf`` デコレータをそのモジュール名前空間へ
注入する。薄い ``udfs.py`` 側で次の 1 行を書けばライブラリ全体が登録される::

    from asterism.functions import register
    register(udf)  # noqa: F821  ← udf は Morph-KGC が注入
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from asterism.primitives import array_at, json_pluck, lookup, regex_extract, split, template
from asterism.qudt import quantity_kind_iri, unit_iri
from asterism.text import (
    parse_float_array,
    parse_issued,
    safe_url,
    slugify,
)
from asterism.transforms import (
    datetime_iso,
    doi_norm,
    json_array,
    json_array_single,
    nfkc_norm,
    number_clean,
    percent_to_ratio,
    range_max,
    range_min,
    strip_footnote,
    trim_collapse,
    unit_of,
    url_canonical,
    value_of,
    year_only,
)

# FnO 名前空間。関数 IRI = FN + 関数名、単一入力のパラメータ IRI = FN + "p_value"。
# 2 入力関数は p_value1 / p_value2 で区別する(RML の rmlf:parameter が指す先)。
# IRI はデータ同一性なので名前空間・関数名・パラメータ名は安定させる(軽率に rename しない)。
FN = "https://kumagallium.github.io/asterism/fn/"
P_VALUE = FN + "p_value"
P_VALUE1 = FN + "p_value1"
P_VALUE2 = FN + "p_value2"

# パラメータ化プリミティブ(§5.1)の「定数(固定値)引数」を指すパラメータ IRI。
# value(列参照)に加え、表名 / 正規表現 / 雛形といった *定数* を RML が
# rmlf:inputValueMap [ rmlf:constant "…" ] で渡す先。p_value 系と同じ命名規約で安定。
P_TABLE = FN + "p_table"
P_PATTERN = FN + "p_pattern"
P_TEMPLATE = FN + "p_template"
P_FIELD1 = FN + "p_field1"
P_FIELD2 = FN + "p_field2"
P_FIELD3 = FN + "p_field3"
P_FIELD4 = FN + "p_field4"
P_INDEX = FN + "p_index"
P_DELIMITER = FN + "p_delimiter"
P_FIELD = FN + "p_field"


# ---- 検証済み関数(既存実装への薄い委譲。FnO 形 str -> str) -------------------

def date_iso(value: str) -> str:
    """雑多な日付表現 → ISO 8601 日付。該当なしは ""(既存 ``parse_issued``)。"""
    return parse_issued(value) or ""


def float_array_max(value: str) -> str:
    """セル内 JSON 数値配列 → 最大値の文字列。空配列は ""(既存 ``parse_float_array``)。"""
    arr = parse_float_array(value)
    return str(max(arr)) if arr else ""


def float_array_min(value: str) -> str:
    """セル内 JSON 数値配列 → 最小値の文字列。空配列は ""(既存 ``parse_float_array``)。"""
    arr = parse_float_array(value)
    return str(min(arr)) if arr else ""


def float_array_count(value1: str, value2: str) -> str:
    """curve の x / y JSON 配列 → 有効データ点数 ``min(len(xs), len(ys))`` の文字列。

    手続き経路 (``starrydata`` の curve 集約) と同じ定義。点数 0 は ""(トリプル無し)
    で素通り (手続き経路の ``if point_count:`` 相当)。FnO は多入力可なので 2 入力で受ける。
    """
    n = min(len(parse_float_array(value1)), len(parse_float_array(value2)))
    return str(n) if n else ""


def iri_safe(value: str) -> str:
    """URL を IRI-safe 化(不正文字を percent-encode)。scheme 無し等は ""(既存 ``safe_url``)。"""
    return safe_url(value) or ""


def slug(value: str) -> str:
    """IRI セグメント用 slug(a-z0-9 と単一 ``-``)。空は "unknown"(既存 ``slugify``)。"""
    return slugify(value)


def qudt_quantity(value: str) -> str:
    """物性名 → QUDT QuantityKind IRI。該当なしは ""(既存 ``quantity_kind_iri``)。"""
    return quantity_kind_iri(value) or ""


def qudt_unit(value: str) -> str:
    """単位文字列 → QUDT Unit IRI。該当なしは ""(既存 ``unit_iri``)。"""
    return unit_iri(value) or ""


# ---- コア関数拡充(Track A。ロジックは asterism.transforms。全 str -> str・該当なし "") ----
# 数値/日付/文字列/ID/値+単位の「頭」の高頻度変換。bool_norm は真偽語彙を
# プリミティブの bool 表に委譲(単一の真実源 — 表は datasets でなく Tier0 同梱)。


def bool_norm(value: str) -> str:
    """真偽語彙 → "true"/"false"。lookup の bool 表に委譲(語彙の単一の真実源)。該当なし ""。"""
    return lookup(value, "bool")


# ---- FnO 登録メタデータ -------------------------------------------------------

@dataclass(frozen=True)
class FunctionSpec:
    """1 関数の FnO 束縛情報。``params`` は {python 引数名: パラメータ IRI}。

    ほぼ全関数は ``str -> str``(該当なし "")。例外は多値関数(``split`` /
    ``json_array`` / ``json_pluck``)で ``list[str]`` を返し、Morph-KGC が各要素を
    1 トリプルへ explode する(宣言的多値経路・入れ子 TriplesMap 不要)。多値関数は
    「値なし」を ``None`` で返す(Morph-KGC が explode 前に行を落とす。空 list は
    explode で NaN 化し直列化を壊すため不可)。型はこの 3 形を許容する。
    """

    name: str
    func: Callable[..., str | list[str] | None]
    params: dict[str, str]

    @property
    def fun_id(self) -> str:
        return FN + self.name


def _single(name: str, func: Callable[[str], str]) -> FunctionSpec:
    """単一入力(value: str)の関数 spec。"""
    return FunctionSpec(name=name, func=func, params={"value": P_VALUE})


def _pair(name: str, func: Callable[[str, str], str]) -> FunctionSpec:
    """2 入力(value1, value2: str)の関数 spec。RML 側は p_value1 / p_value2 を指す。"""
    return FunctionSpec(name=name, func=func, params={"value1": P_VALUE1, "value2": P_VALUE2})


# 宣言マッピングが参照してよい関数の「閉じた集合」。ここに無いものは呼べない。
# 末尾のパラメータ化プリミティブ(§5.1)は value(列参照)に加え定数引数を取る:
# 可変性(表・パターン・雛形)を *データ* に逃がし、コアを有界に保つ。RML 側は定数を
# rmlf:inputValueMap [ rmlf:constant "…" ] で渡す(propose §9 / step0-rml-emission.md)。
REGISTRY: list[FunctionSpec] = [
    _single("date_iso", date_iso),
    _single("float_array_max", float_array_max),
    _single("float_array_min", float_array_min),
    _pair("float_array_count", float_array_count),
    _single("iri_safe", iri_safe),
    _single("slug", slug),
    _single("qudt_quantity", qudt_quantity),
    _single("qudt_unit", qudt_unit),
    # パラメータ化プリミティブ(asterism.primitives への委譲。定数引数つき str -> str)。
    FunctionSpec(name="lookup", func=lookup, params={"value": P_VALUE, "table": P_TABLE}),
    FunctionSpec(
        name="regex_extract", func=regex_extract, params={"value": P_VALUE, "pattern": P_PATTERN}
    ),
    FunctionSpec(
        name="template",
        func=template,
        params={
            "template": P_TEMPLATE,
            "field1": P_FIELD1,
            "field2": P_FIELD2,
            "field3": P_FIELD3,
            "field4": P_FIELD4,
        },
    ),
    # コア関数拡充(Track A。asterism.transforms への委譲。全 _single = 単一入力 value)。
    _single("number_clean", number_clean),
    _single("percent_to_ratio", percent_to_ratio),
    _single("range_min", range_min),
    _single("range_max", range_max),
    _single("datetime_iso", datetime_iso),
    _single("year_only", year_only),
    _single("nfkc_norm", nfkc_norm),
    _single("trim_collapse", trim_collapse),
    _single("strip_footnote", strip_footnote),
    _single("bool_norm", bool_norm),
    _single("doi_norm", doi_norm),
    _single("url_canonical", url_canonical),
    _single("value_of", value_of),
    _single("unit_of", unit_of),
    # 多値/ネストの「容易な勝ち筋」(tier0-coverage-gate.md §5)。スカラ抽出と、
    # list を返して Morph-KGC に explode させる多値 split。入れ子 object 配列は
    # 別途(入れ子 TriplesMap)。
    _single("json_array_single", json_array_single),
    FunctionSpec(name="array_at", func=array_at, params={"value": P_VALUE, "index": P_INDEX}),
    FunctionSpec(
        name="split", func=split, params={"value": P_VALUE, "delimiter": P_DELIMITER}
    ),
    # JSON-string 配列の多値展開(list を返し Morph-KGC が explode)。scalar 配列は
    # json_array、object 配列の sub-field は json_pluck。native JSON ソースの入れ子は
    # morph-kgc が関数に配列を渡せないため対象外(入れ子 iterator・substrate 制約)。
    FunctionSpec(name="json_array", func=json_array, params={"value": P_VALUE}),
    FunctionSpec(
        name="json_pluck", func=json_pluck, params={"value": P_VALUE, "field": P_FIELD}
    ),
]


def register(udf: Callable[..., Callable]) -> list[FunctionSpec]:
    """Morph-KGC が注入する ``udf`` デコレータでライブラリ全体を登録する。

    ``udfs.py`` から ``register(udf)`` の 1 行で呼ぶ。登録した spec のリストを返す
    (テスト・点検用)。
    """
    for spec in REGISTRY:
        udf(fun_id=spec.fun_id, **spec.params)(spec.func)
    return REGISTRY
