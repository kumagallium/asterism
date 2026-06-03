"""Phase 5 関数ライブラリ v0 — Morph-KGC が FnO 経由で呼ぶ検証済み関数。

設計: docs/architecture/phase5-declarative-substrate.md §2(b)。
宣言的マッピングで書けない少数の変換だけをここに集約する。各関数は asterism の
**既存 vetted 実装を薄く露出するだけ**（単一の真実源・ロジック重複なし）。
新しい変換が要るソースは、人間がここに 1 行足す。per-dataset の codegen は無い。

morph-kgc は本ファイルを読み、`@udf` デコレータを注入して exec する
（morph_kgc/fnml/fnml_executer.py の load_udfs）。よって `udf` は import しない。

NOTE(spike): 本体 asterism を import するため cwd 基準で ../../ingest/src を path に足す。
production では関数ライブラリ自体を asterism パッケージ内に置くので、この hack は消える。
"""

import datetime as _dt
import os
import sys

if not hasattr(_dt, "UTC"):  # asterism.starrydata は py3.11 の datetime.UTC を使う
    _dt.UTC = _dt.timezone.utc
sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..", "ingest", "src")))

from asterism.qudt import quantity_kind_iri, unit_iri  # noqa: E402  既存 vetted 実装
from asterism.starrydata import parse_issued, safe_url  # noqa: E402

# 関数・パラメータの IRI namespace（RML 側から FnO で参照する）
FN = "https://kumagallium.github.io/asterism/fn/"


@udf(fun_id=FN + "parse_date", value=FN + "p_value")  # noqa: F821  (udf is injected by morph-kgc)
def parse_date(value):
    """'issued' 等の雑多な日付 → ISO 日付文字列（既存 parse_issued を露出）。"""
    return parse_issued(value) or ""


@udf(fun_id=FN + "sanitize_iri", value=FN + "p_value")  # noqa: F821
def sanitize_iri(value):
    """非絶対 / 不正 IRI を弾く・整える（既存 safe_url を露出）。"""
    return safe_url(value) or ""


@udf(fun_id=FN + "qudt_quantity_iri", value=FN + "p_value")  # noqa: F821
def qudt_quantity_iri(value):
    """物性ラベル → QUDT QuantityKind IRI（既存 quantity_kind_iri を露出）。"""
    return quantity_kind_iri(value) or ""


@udf(fun_id=FN + "qudt_unit_iri", value=FN + "p_value")  # noqa: F821
def qudt_unit_iri(value):
    """単位ラベル → QUDT Unit IRI（既存 unit_iri を露出）。"""
    return unit_iri(value) or ""
