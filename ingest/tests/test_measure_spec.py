"""Tests for asterism.measure_spec (契約メモ contract_pr_f4.md §1-2/§1-4).

Pure — no store, no fixtures beyond a hand-written ``class_schema(...)
['properties']``-shaped list. Property names are generic (day/value/site/
station/id), never a single domain's vocabulary (§0).
"""

from __future__ import annotations

import pytest

from asterism.measure_spec import (
    AGGS,
    SHAPES,
    MeasureSpecError,
    is_coordinate,
    output_kind_for,
    title_parts,
    validate_measure,
    x_candidates,
)

DAY = "https://ex/o#day"
VALUE = "https://ex/o#value"
SITE = "https://ex/o#site"
STATION = "https://ex/o#station"
RECORD_ID = "https://ex/o#id"
YEAR_KEY = "https://ex/o#yearKey"

SCHEMA = [
    {"iri": DAY, "label": "日", "kind": "quantity", "unit": None, "distinct_count": None},
    {
        "iri": VALUE,
        "label": "値",
        "kind": "quantity",
        "unit": "http://qudt.org/vocab/unit/M",
        "distinct_count": None,
    },
    {"iri": SITE, "label": "場所", "kind": "category", "unit": None, "distinct_count": 3},
    {"iri": STATION, "label": "観測局", "kind": "link", "unit": None, "distinct_count": None},
    {
        "iri": RECORD_ID,
        "label": "識別子",
        "kind": "identifier",
        "unit": None,
        "distinct_count": None,
    },
    {
        "iri": YEAR_KEY,
        "label": "年",
        "kind": "identifier",
        "datatype": "http://www.w3.org/2001/XMLSchema#integer",
        "unit": None,
        "distinct_count": None,
    },
]


# ---------------------------------------------------------------------------
# output_kind_for
# ---------------------------------------------------------------------------


def test_output_kind_for_is_identity_over_shapes() -> None:
    for shape in SHAPES:
        assert output_kind_for(shape) == shape


def test_output_kind_for_rejects_unknown_shape() -> None:
    with pytest.raises(MeasureSpecError):
        output_kind_for("pie")


# ---------------------------------------------------------------------------
# validate_measure — per-shape validity table.
# ---------------------------------------------------------------------------


def test_validate_measure_series_accepts_two_quantities() -> None:
    out = validate_measure({"shape": "series", "x": DAY, "y": VALUE}, SCHEMA)
    assert out == {"shape": "series", "x": DAY, "y": VALUE}


def test_validate_measure_series_rejects_category_x() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "series", "x": SITE, "y": VALUE}, SCHEMA)


def test_validate_measure_series_rejects_missing_y() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "series", "x": DAY}, SCHEMA)


def test_validate_measure_series_accepts_numeric_identifier_x() -> None:
    """年のような「キーの一部の座標」（kind=identifier・datatype=数値）は
    横軸に使える（実機所見: 見本「世界の国」の年ごとの記録）。"""
    out = validate_measure({"shape": "series", "x": YEAR_KEY, "y": VALUE}, SCHEMA)
    assert out == {"shape": "series", "x": YEAR_KEY, "y": VALUE}


def test_validate_measure_series_rejects_text_identifier_x() -> None:
    """datatype が数値でない identifier（例: 文字列の識別子）は座標ではない。"""
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "series", "x": RECORD_ID, "y": VALUE}, SCHEMA)


def test_validate_measure_ranked_rejects_numeric_identifier_item() -> None:
    """y・item は従来どおり quantity のみ — 座標の緩和は x 専用。"""
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "ranked", "item": YEAR_KEY}, SCHEMA)


def test_validate_measure_ranked_accepts_quantity_item_defaults_to_desc() -> None:
    out = validate_measure({"shape": "ranked", "item": VALUE}, SCHEMA)
    assert out == {"shape": "ranked", "item": VALUE, "order": "desc"}


def test_validate_measure_ranked_accepts_explicit_asc() -> None:
    out = validate_measure({"shape": "ranked", "item": VALUE, "order": "asc"}, SCHEMA)
    assert out["order"] == "asc"


def test_validate_measure_ranked_rejects_link_item() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "ranked", "item": STATION}, SCHEMA)


def test_validate_measure_ranked_rejects_bad_order() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "ranked", "item": VALUE, "order": "sideways"}, SCHEMA)


def test_validate_measure_ranked_accepts_x_as_a_fallback_for_item() -> None:
    """Cross-team bridge (実機所見): ui-form's ``buildMeasureCard`` sends the
    「項目」into ``params.x`` (no dedicated key in its reading of §1-4's params
    列挙) — ``item`` wins when both are present, ``x`` is accepted when
    ``item`` is absent."""
    out = validate_measure({"shape": "ranked", "x": VALUE}, SCHEMA)
    assert out == {"shape": "ranked", "item": VALUE, "order": "desc"}


def test_validate_measure_quantity_accepts_x_as_a_fallback_for_item() -> None:
    out = validate_measure({"shape": "quantity", "x": VALUE, "agg": "avg"}, SCHEMA)
    assert out == {"shape": "quantity", "item": VALUE, "agg": "avg"}


def test_validate_measure_ranked_prefers_item_over_x_when_both_are_present() -> None:
    out = validate_measure({"shape": "ranked", "item": VALUE, "x": DAY}, SCHEMA)
    assert out["item"] == VALUE


def test_validate_measure_breakdown_accepts_category() -> None:
    out = validate_measure({"shape": "breakdown", "category": SITE}, SCHEMA)
    assert out == {"shape": "breakdown", "category": SITE}


def test_validate_measure_breakdown_rejects_quantity_category() -> None:
    """内訳の平均のような無意味な組み合わせ — UI をバイパスした呼び出しへの
    最後の砦（ADR O44）。"""
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "breakdown", "category": VALUE}, SCHEMA)


def test_validate_measure_pairs_accepts_two_quantities() -> None:
    out = validate_measure({"shape": "pairs", "x": DAY, "y": VALUE}, SCHEMA)
    assert out == {"shape": "pairs", "x": DAY, "y": VALUE}


def test_validate_measure_quantity_accepts_item_and_agg() -> None:
    for agg in AGGS:
        out = validate_measure({"shape": "quantity", "item": VALUE, "agg": agg}, SCHEMA)
        assert out == {"shape": "quantity", "item": VALUE, "agg": agg}


def test_validate_measure_quantity_rejects_unknown_agg() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "quantity", "item": VALUE, "agg": "median"}, SCHEMA)


def test_validate_measure_quantity_rejects_category_item() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "quantity", "item": SITE, "agg": "avg"}, SCHEMA)


def test_validate_measure_facts_accepts_quantity_and_category_mix() -> None:
    out = validate_measure({"shape": "facts", "items": [DAY, SITE]}, SCHEMA)
    assert out == {"shape": "facts", "items": [DAY, SITE]}


def test_validate_measure_facts_rejects_identifier_item() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "facts", "items": [RECORD_ID]}, SCHEMA)


def test_validate_measure_facts_rejects_empty_list() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "facts", "items": []}, SCHEMA)


def test_validate_measure_rejects_unknown_shape() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "pie", "item": VALUE}, SCHEMA)


def test_validate_measure_rejects_non_object_params() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure(["not", "a", "dict"], SCHEMA)


def test_validate_measure_rejects_property_not_on_schema() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "series", "x": DAY, "y": "https://ex/o#nope"}, SCHEMA)


def test_validate_measure_rejects_unsafe_iri() -> None:
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "quantity", "item": "not an iri", "agg": "avg"}, SCHEMA)


def test_validate_measure_without_schema_skips_kind_check_but_keeps_iri_shape() -> None:
    """``schema_properties=None`` — best-effort（呼び出し側の class_schema が
    引けなかった場合）: kind の照合は省くが、IRI の形式検査は変わらず効く。"""
    out = validate_measure({"shape": "quantity", "item": SITE, "agg": "avg"}, None)
    assert out == {"shape": "quantity", "item": SITE, "agg": "avg"}
    with pytest.raises(MeasureSpecError):
        validate_measure({"shape": "quantity", "item": "not an iri", "agg": "avg"}, None)


# ---------------------------------------------------------------------------
# title_parts
# ---------------------------------------------------------------------------


def test_title_parts_series_carries_y_label() -> None:
    params = validate_measure({"shape": "series", "x": DAY, "y": VALUE}, SCHEMA)
    assert title_parts(params, SCHEMA) == {"y": "値"}


def test_title_parts_ranked_carries_item_label() -> None:
    params = validate_measure({"shape": "ranked", "item": VALUE}, SCHEMA)
    assert title_parts(params, SCHEMA) == {"item": "値"}


def test_title_parts_breakdown_carries_category_label() -> None:
    params = validate_measure({"shape": "breakdown", "category": SITE}, SCHEMA)
    assert title_parts(params, SCHEMA) == {"category": "場所"}


def test_title_parts_pairs_carries_both_labels() -> None:
    params = validate_measure({"shape": "pairs", "x": DAY, "y": VALUE}, SCHEMA)
    assert title_parts(params, SCHEMA) == {"x": "日", "y": "値"}


def test_title_parts_quantity_carries_item_label_and_raw_agg() -> None:
    params = validate_measure({"shape": "quantity", "item": VALUE, "agg": "sum"}, SCHEMA)
    assert title_parts(params, SCHEMA) == {"item": "値", "agg": "sum"}


def test_title_parts_facts_carries_item_labels_in_order() -> None:
    params = validate_measure({"shape": "facts", "items": [DAY, SITE]}, SCHEMA)
    assert title_parts(params, SCHEMA) == {"items": ["日", "場所"]}


def test_title_parts_falls_back_to_humanized_local_name_without_schema() -> None:
    params = {"shape": "series", "x": DAY, "y": "https://ex/o#peakValue"}
    assert title_parts(params, None) == {"y": "peak Value"}


# ---------------------------------------------------------------------------
# x_candidates — ranking.
# ---------------------------------------------------------------------------

YEAR = "https://ex/o#year"
INDEX_COL = "https://ex/o#index"
WEIGHT = "https://ex/o#weight"
X_SCHEMA = [
    {"iri": YEAR, "label": "年", "kind": "quantity", "unit": None, "distinct_count": 5},
    {
        "iri": INDEX_COL,
        "label": "通し番号",
        "kind": "quantity",
        "unit": None,
        "distinct_count": 100,
    },
    {
        "iri": WEIGHT,
        "label": "重さ",
        "kind": "quantity",
        "unit": "http://qudt.org/vocab/unit/KiloGM",
        "distinct_count": 200,
    },
    {"iri": SITE, "label": "場所", "kind": "category", "unit": None, "distinct_count": 3},
]


def test_is_coordinate_accepts_quantity_and_numeric_identifier() -> None:
    assert is_coordinate({"kind": "quantity"}) is True
    numeric_id = {"kind": "identifier", "datatype": "http://www.w3.org/2001/XMLSchema#integer"}
    assert is_coordinate(numeric_id) is True


def test_is_coordinate_rejects_text_identifier_and_other_kinds() -> None:
    assert is_coordinate({"kind": "identifier", "datatype": None}) is False
    assert is_coordinate({"kind": "identifier"}) is False
    assert is_coordinate({"kind": "category"}) is False
    assert is_coordinate({"kind": "link"}) is False


def test_x_candidates_ranks_time_like_name_first() -> None:
    """名前が時間らしい（年）列が、より distinct の多い他の quantity 列より
    上に来る（契約メモ §1-4: 「推移」の横軸として最も具体的な手がかり）。"""
    assert x_candidates(X_SCHEMA)[0] == YEAR


def test_x_candidates_prefers_no_unit_over_more_distinct() -> None:
    """時間名でない候補どうしでは、単位が無い列が distinct の多寡より先に来る
    （INDEX_COL は unit 無し・distinct 100、WEIGHT は unit 有り・distinct
    200 — INDEX_COL が先）。"""
    ranked = x_candidates(X_SCHEMA)
    assert ranked.index(INDEX_COL) < ranked.index(WEIGHT)


def test_x_candidates_excludes_non_quantity_kinds() -> None:
    assert SITE not in x_candidates(X_SCHEMA)


def test_x_candidates_full_order() -> None:
    assert x_candidates(X_SCHEMA) == [YEAR, INDEX_COL, WEIGHT]


def test_x_candidates_empty_schema_is_empty_list() -> None:
    assert x_candidates([]) == []
    assert x_candidates(None) == []
