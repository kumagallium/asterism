"""「見せ方 → 項目」の妥当性表（object-cards-ui.md O43/O44、契約メモ
contract_pr_f4.md §1-2・§1-4）。

純関数のみ・store アクセスなし・SPARQL なし。``subject_tools.set_measure`` と
ui の両方がこの ONE 表を読む（O44: 「弱い入力を弾く最後の砦」がサーバ側の
:func:`validate_measure`、「迷わせない先回り」が ui 側の同じ表の先読み — 二重
管理ではなく別の役目）。

見せ方（``SHAPES``）は :data:`asterism.query_tools.OUTPUT_KINDS` の
``series``/``ranked``/``breakdown``/``pairs``/``quantity``/``facts`` とそのまま
1 対 1 で対応する（契約メモ §1-2 の表の「出口の型」列）。受け付ける項目の
``kind`` は class_schema の 5 種類（identifier/link/quantity/category/text）の
うち ``quantity``/``category`` の 2 つだけ — 内訳の平均や表の識別子列といった
意味の無い組み合わせは、この表の外として ``ValueError``（:class:`MeasureSpecError`
— どちらも ``ValueError`` を継承する既存の ``SetSpecError`` と同じ形）になる。
"""

from __future__ import annotations

import re
from typing import Any

from asterism.class_schema import _is_numeric_datatype
from asterism.subjects import safe_http_iri

__all__ = [
    "AGGS",
    "SHAPES",
    "MeasureSpecError",
    "is_coordinate",
    "output_kind_for",
    "title_parts",
    "validate_measure",
    "x_candidates",
]


class MeasureSpecError(ValueError):
    """A measure ``params`` dict falls outside §1-2's validity table (→ 400 at
    the ``set_measure`` boundary). A plain ``ValueError`` subclass — same
    shape as :class:`asterism.subjects.SetSpecError` — so a caller that only
    catches ``ValueError`` still catches this."""


#: 見せ方の語彙（契約メモ §1-2 の「①見せ方」列。順は表と同じ: 推移/比べる/
#: 内訳/散らばり/数字1つ/表）。それぞれ :data:`asterism.query_tools.OUTPUT_KINDS`
#: の同名エントリと 1 対 1 — ``output_kind_for`` は恒等写像でしかない。
SHAPES: tuple[str, ...] = ("series", "ranked", "breakdown", "pairs", "quantity", "facts")

#: 「数字 1 つ」の集計語彙（契約メモ §1-2・§3 言葉: 平均/最大/最小/合計/件数）。
AGGS: tuple[str, ...] = ("avg", "max", "min", "sum", "count")

#: 見せ方ごとに要る項目キー → 受け付ける ``kind``（class_schema の 5 種類の
#: うち quantity/category だけがどの見せ方でも対象になり得る — 契約メモ
#: 「受け付ける kind（quantity/category）の妥当性表」）。
#: ``series``/``pairs`` の ``x``（横軸）はここでは判定に使わない — 座標は
#: quantity に加え数値 identifier も受けるため :func:`is_coordinate` で別途
#: 検証する（:func:`_check_coordinate_field_iri`）。このタプルは「x も quantity
#: が中心」というドキュメントとしてのみ残す。
_FIELD_KINDS: dict[str, dict[str, tuple[str, ...]]] = {
    "series": {"x": ("quantity",), "y": ("quantity",)},
    "ranked": {"item": ("quantity",)},
    "breakdown": {"category": ("category",)},
    "pairs": {"x": ("quantity",), "y": ("quantity",)},
    "quantity": {"item": ("quantity",)},
    "facts": {"items": ("quantity", "category")},
}

#: 見せ方 → :data:`asterism.query_tools.OUTPUT_KINDS` の対応（恒等 — 表の
#: 「出口の型」列そのもの）。ここに独自の別名は作らない: 呼び出し側
#: （``subject_tools.set_measure`` / ui）がどちらも同じ語を扱えるように。
_OUTPUT_KIND_OF_SHAPE: dict[str, str] = dict(zip(SHAPES, SHAPES, strict=True))

_ORDER_DIRS: tuple[str, ...] = ("asc", "desc")


def output_kind_for(shape: str) -> str:
    """``shape`` に対応する :data:`asterism.query_tools.OUTPUT_KINDS` の値。
    見せ方の語彙が :data:`SHAPES` の外なら :class:`MeasureSpecError`。"""
    if shape not in _OUTPUT_KIND_OF_SHAPE:
        raise MeasureSpecError(f"shape must be one of {SHAPES}, got {shape!r}")
    return _OUTPUT_KIND_OF_SHAPE[shape]


def _index_properties(
    schema_properties: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]] | None:
    """``{property_iri: property}`` — ``None`` means "この class の schema が
    引けなかった"（呼び出し側が best-effort で ``class_schema`` を諦めた場合）。
    その場合 :func:`validate_measure` は IRI の形だけ確かめ、``kind`` の照合は
    省く（弱い入力を弾く「最後の砦」は形式検査だけになるが、通常経路では常に
    schema が渡るので実質発生しない — 契約メモ §1-4 の best-effort 方針
    （``_load_class_schema`` の失敗が個別の built-in を壊さない）と同じ扱い）。
    """
    if schema_properties is None:
        return None
    return {p["iri"]: p for p in schema_properties if isinstance(p, dict) and p.get("iri")}


def is_coordinate(prop: dict[str, Any] | None) -> bool:
    """この性質が「座標」として横軸（x）に使えるか。

    kind が ``quantity`` なら常に使える。年・日付・連番のような座標は
    主語テンプレートの一部（``kind == "identifier"``）としてモデル化される
    ことが一般的な形なので、datatype が数値（
    :func:`asterism.class_schema._is_numeric_datatype` と同じ判定 — この
    module は store を持たないので同関数をそのまま import して使う）の
    identifier も座標として受ける。文字列 datatype の identifier（例:
    国コードのような主語テンプレートの列）は座標ではない — y・item・category
    の対象は従来どおり quantity のみで、この関数は x 専用。
    """
    if not isinstance(prop, dict):
        return False
    kind = prop.get("kind")
    if kind == "quantity":
        return True
    if kind == "identifier":
        return _is_numeric_datatype(prop.get("datatype"))
    return False


def _check_field_iri(
    value: Any, by_iri: dict[str, dict[str, Any]] | None, *, allowed: tuple[str, ...], what: str
) -> str:
    text = safe_http_iri(value)
    if text is None:
        raise MeasureSpecError(f"{what} must be a well-formed http(s) IRI, got {value!r}")
    if by_iri is not None:
        prop = by_iri.get(text)
        if prop is None:
            raise MeasureSpecError(f"{what} {text!r} is not a property of this class")
        kind = prop.get("kind")
        if kind not in allowed:
            raise MeasureSpecError(
                f"{what} must be one of kind {allowed}, got {kind!r} for {text!r}"
            )
    return text


def _check_coordinate_field_iri(
    value: Any, by_iri: dict[str, dict[str, Any]] | None, *, what: str
) -> str:
    """x（横軸）専用の :func:`_check_field_iri` — kind の固定タプルでなく
    :func:`is_coordinate` で判定する（座標は quantity か数値 datatype の
    identifier）。"""
    text = safe_http_iri(value)
    if text is None:
        raise MeasureSpecError(f"{what} must be a well-formed http(s) IRI, got {value!r}")
    if by_iri is not None:
        prop = by_iri.get(text)
        if prop is None:
            raise MeasureSpecError(f"{what} {text!r} is not a property of this class")
        if not is_coordinate(prop):
            raise MeasureSpecError(
                f"{what} must be a coordinate property (quantity, or numeric identifier), "
                f"got kind {prop.get('kind')!r} for {text!r}"
            )
    return text


def _item_value(params: dict[str, Any]) -> Any:
    """``ranked``/``quantity``'s 「項目」の生値。

    Cross-team bridge (実機所見、統合時に確認): この module の妥当性表は
    「項目」専用の ``params["item"]`` キーを正とするが、
    ``ui/src/cards/measureCardFields.ts``'s ``buildMeasureCard`` は契約メモ
    §1-4 が挙げる params 列挙 ``{class, where, shape, x?, y?, category?,
    items?, agg?, order?}`` に専用キーが無いと読み、②の「項目」を
    ``params.x`` に詰め替えて送る（同ファイルのコメント参照）。``item`` が
    あれば優先、無ければ ``x`` を見ることで、どちらの送り方でも通す —
    ``x``/``y`` の両方が要る series/pairs の妥当性表とは無関係（そちらは
    このフォールバックの対象ではない）。
    """
    if params.get("item") is not None:
        return params.get("item")
    return params.get("x")


def _require_field_iri(
    params: dict[str, Any],
    key: str,
    by_iri: dict[str, dict[str, Any]] | None,
    *,
    allowed: tuple[str, ...],
) -> str:
    return _check_field_iri(params.get(key), by_iri, allowed=allowed, what=key)


def validate_measure(params: Any, schema_properties: list[dict[str, Any]] | None) -> dict[str, Any]:
    """§1-2 の妥当性表に照らして ``params`` を検証し、正規化した dict を返す
    （表の外は :class:`MeasureSpecError`、``ValueError`` のサブクラス）。

    ``schema_properties`` は呼び出し側が既に引いた
    ``class_schema(...)['properties']``（このクラスの性質一覧・
    ``{iri,label,kind,unit,...}``）— この関数自身は store に触らない。``None``
    のときは IRI の形式検査だけに倒れる（:func:`_index_properties` 参照）。

    返り値の形は見せ方ごとに固定:

    - ``series``/``pairs``: ``{"shape", "x", "y"}``
    - ``ranked``: ``{"shape", "item", "order"}``（``order`` 省略時 ``"desc"``）
    - ``breakdown``: ``{"shape", "category"}``
    - ``quantity``: ``{"shape", "item", "agg"}``
    - ``facts``: ``{"shape", "items"}``（IRI のリスト、1 件以上）
    """
    if not isinstance(params, dict):
        raise MeasureSpecError("params must be an object")
    shape = params.get("shape")
    if shape not in SHAPES:
        raise MeasureSpecError(f"shape must be one of {SHAPES}, got {shape!r}")
    by_iri = _index_properties(schema_properties)
    fields = _FIELD_KINDS[shape]

    if shape in ("series", "pairs"):
        x = _check_coordinate_field_iri(params.get("x"), by_iri, what="x")
        y = _require_field_iri(params, "y", by_iri, allowed=fields["y"])
        return {"shape": shape, "x": x, "y": y}
    if shape == "ranked":
        item = _check_field_iri(_item_value(params), by_iri, allowed=fields["item"], what="item")
        order = params.get("order", "desc")
        if order not in _ORDER_DIRS:
            raise MeasureSpecError(f"order must be one of {_ORDER_DIRS}, got {order!r}")
        return {"shape": shape, "item": item, "order": order}
    if shape == "breakdown":
        category = _require_field_iri(params, "category", by_iri, allowed=fields["category"])
        return {"shape": shape, "category": category}
    if shape == "quantity":
        item = _check_field_iri(_item_value(params), by_iri, allowed=fields["item"], what="item")
        agg = params.get("agg")
        if agg not in AGGS:
            raise MeasureSpecError(f"agg must be one of {AGGS}, got {agg!r}")
        return {"shape": shape, "item": item, "agg": agg}
    # shape == "facts"
    items_raw = params.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise MeasureSpecError("items must be a non-empty list for shape 'facts'")
    items = [
        _check_field_iri(v, by_iri, allowed=fields["items"], what=f"items[{i}]")
        for i, v in enumerate(items_raw)
    ]
    return {"shape": shape, "items": items}


# ----------------------------------------------------------------------------
# 題名の材料（i18n テンプレートに渡す label だけ — 文言自体は ui の持ち場）。
# ----------------------------------------------------------------------------

# subject_tools._humanize と手で同期した同じ最小限のヒューマナイザ（§0: 各
# 担当ファイル別・共有 module を増やさない、この PR で確立された慣習 —
# subject_tools.py の同名コメント参照）。この module は store にもレジストリ
# にも触れない純関数のみなので、ラベルは常に「schema にあればその label、
# 無ければ人間化したローカル名」止まり（K4: 生の識別子のまま出さない）。
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _local_name(iri: str) -> str:
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or iri


def _humanize(local: str) -> str:
    if not local:
        return ""
    stripped = re.sub(r"^(has|is)(?=[A-Z])", "", local)
    spaced = _CAMEL_BOUNDARY.sub(" ", stripped.replace("_", " ").replace("-", " "))
    return " ".join(spaced.split())


def _fallback_label(iri: str) -> str:
    local = _local_name(iri)
    return _humanize(local) or local


def _label_of(iri: str, by_iri: dict[str, dict[str, Any]]) -> str:
    prop = by_iri.get(iri)
    label = prop.get("label") if isinstance(prop, dict) else None
    return label or _fallback_label(iri)


def title_parts(
    params: dict[str, Any], schema_properties: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """§1-2 の「既定の題名」テンプレートに渡す材料 —
    :func:`validate_measure` が返した正規化済み ``params`` を受け、テンプレート
    の ``{...}`` に埋める label（``agg`` だけは値そのもの — i18n の文言は ui の
    持ち場、ここでは翻訳しない）の dict を返す。

    - ``series``: ``{"y": <label>}`` （「{y} の推移」）
    - ``ranked``: ``{"item": <label>}`` （「{item} が多い順」）
    - ``breakdown``: ``{"category": <label>}`` （「{category} ごとの件数」）
    - ``pairs``: ``{"x": <label>, "y": <label>}`` （「{x} と {y}」）
    - ``quantity``: ``{"item": <label>, "agg": <agg>}`` （「{item} の{agg}」）
    - ``facts``: ``{"items": [<label>, ...]}`` （「{items}」）
    """
    shape = params.get("shape")
    if shape not in SHAPES:
        raise MeasureSpecError(f"shape must be one of {SHAPES}, got {shape!r}")
    by_iri = _index_properties(schema_properties) or {}

    if shape == "series":
        return {"y": _label_of(params["y"], by_iri)}
    if shape == "ranked":
        return {"item": _label_of(params["item"], by_iri)}
    if shape == "breakdown":
        return {"category": _label_of(params["category"], by_iri)}
    if shape == "pairs":
        return {"x": _label_of(params["x"], by_iri), "y": _label_of(params["y"], by_iri)}
    if shape == "quantity":
        return {"item": _label_of(params["item"], by_iri), "agg": params.get("agg")}
    # shape == "facts"
    return {"items": [_label_of(i, by_iri) for i in params.get("items") or []]}


# ----------------------------------------------------------------------------
# 横軸候補の順位づけ（②の前に UI が「横軸」の選択肢を並べる材料）。
# ----------------------------------------------------------------------------

#: 名前が時間らしいと判定するキーワード（分野語ではなく座標の一般名 — K4 の
#: 対象外: year/date/time/年/日 は特定分野に属さない一般語）。
_TIME_LIKE = re.compile(r"(year|date|time|年|日)", re.IGNORECASE)


def _is_time_like(prop: dict[str, Any]) -> bool:
    haystack = f"{prop.get('label') or ''} {_local_name(str(prop.get('iri') or ''))}"
    return bool(_TIME_LIKE.search(haystack))


def x_candidates(schema_properties: list[dict[str, Any]] | None) -> list[str]:
    """「推移」「散らばり」の横軸に向く座標列の IRI を、向いている順に並べる
    （契約メモ §1-4: 「単位なし・distinct が多い・名前に year/date/time/年/日
    を含む quantity を上に。identifier で数値のものはキーの一部の座標として
    quantity より上に置く」）。

    優先順位（強い順 — 同点は次の基準、最後は IRI の辞書順で決定論）:

    1. 数値 datatype の identifier（主語テンプレートの一部＝キーの一部の座標
       — 年・日付・連番の一般的な形）を quantity より先に。
    2. 名前が時間らしい（年・日付・時刻）— 「推移」の横軸として最も具体的な
       手がかり。
    3. 単位が無い（座標であって測定量ではない、O25 と同じ直感）。
    4. distinct 値が多い（連続的な横軸として細かく分かれている）。

    :func:`is_coordinate` の外（category/link/文字列 identifier など）は
    候補に入れない — 妥当性表と矛盾する候補を見せない。
    """
    if not schema_properties:
        return []
    candidates = [p for p in schema_properties if is_coordinate(p) and p.get("iri")]

    def _sort_key(prop: dict[str, Any]) -> tuple[int, int, int, int, str]:
        identifier_rank = 0 if prop.get("kind") == "identifier" else 1
        time_rank = 0 if _is_time_like(prop) else 1
        unit_rank = 0 if not prop.get("unit") else 1
        distinct = prop.get("distinct_count")
        is_real_int = isinstance(distinct, int) and not isinstance(distinct, bool)
        distinct_rank = -distinct if is_real_int else 0
        return (identifier_rank, time_rank, unit_rank, distinct_rank, str(prop["iri"]))

    return [p["iri"] for p in sorted(candidates, key=_sort_key)]
