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

from asterism.qudt import quantity_kind_iri, unit_iri
from asterism.text import (
    parse_float_array,
    parse_issued,
    safe_url,
    slugify,
)

# FnO 名前空間。関数 IRI = FN + 関数名、単一入力のパラメータ IRI = FN + "p_value"。
# 2 入力関数は p_value1 / p_value2 で区別する(RML の rmlf:parameter が指す先)。
# IRI はデータ同一性なので名前空間・関数名・パラメータ名は安定させる(軽率に rename しない)。
FN = "https://kumagallium.github.io/asterism/fn/"
P_VALUE = FN + "p_value"
P_VALUE1 = FN + "p_value1"
P_VALUE2 = FN + "p_value2"


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


# ---- FnO 登録メタデータ -------------------------------------------------------

@dataclass(frozen=True)
class FunctionSpec:
    """1 関数の FnO 束縛情報。``params`` は {python 引数名: パラメータ IRI}。"""

    name: str
    func: Callable[..., str]
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
REGISTRY: list[FunctionSpec] = [
    _single("date_iso", date_iso),
    _single("float_array_max", float_array_max),
    _single("float_array_min", float_array_min),
    _pair("float_array_count", float_array_count),
    _single("iri_safe", iri_safe),
    _single("slug", slug),
    _single("qudt_quantity", qudt_quantity),
    _single("qudt_unit", qudt_unit),
]


def register(udf: Callable[..., Callable]) -> list[FunctionSpec]:
    """Morph-KGC が注入する ``udf`` デコレータでライブラリ全体を登録する。

    ``udfs.py`` から ``register(udf)`` の 1 行で呼ぶ。登録した spec のリストを返す
    (テスト・点検用)。
    """
    for spec in REGISTRY:
        udf(fun_id=spec.fun_id, **spec.params)(spec.func)
    return REGISTRY
