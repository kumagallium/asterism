"""Tests for asterism.view_spec_check (契約メモ contract_pr_f13.md §1-2)。

純関数のみ・I/O 無し。fixture は分野に依存しない一般的なグラフ/表/フロー
チャートだけ（§0）。
"""

from __future__ import annotations

from asterism.view_spec_check import check_mermaid, check_table, check_vega_lite

# ---------------------------------------------------------------------------
# check_vega_lite
# ---------------------------------------------------------------------------


def test_vega_lite_allows_a_plain_line_chart_in_the_allow_list() -> None:
    spec = {
        "mark": "line",
        "encoding": {
            "x": {"field": "x", "type": "quantitative"},
            "y": {"field": "y", "type": "quantitative"},
        },
        "title": "推移",
    }
    ok, reason = check_vega_lite(spec)
    assert ok, reason


def test_vega_lite_allows_a_layered_chart_with_a_known_transform() -> None:
    spec = {
        "layer": [
            {
                "mark": "bar",
                "encoding": {"x": {"field": "x", "type": "nominal"}},
                "transform": [{"filter": "datum.x != null"}],
            },
            {"mark": "point", "encoding": {"y": {"field": "y", "type": "quantitative"}}},
        ]
    }
    ok, reason = check_vega_lite(spec)
    assert ok, reason


def test_vega_lite_rejects_data_anywhere() -> None:
    ok, reason = check_vega_lite({"mark": "line", "data": {"values": [{"x": 1}]}})
    assert not ok
    assert "data" in (reason or "")


def test_vega_lite_rejects_data_url() -> None:
    ok, reason = check_vega_lite({"mark": "line", "data": {"url": "https://ex/data.json"}})
    assert not ok
    assert "data" in (reason or "")


def test_vega_lite_rejects_data_url_nested_in_layer() -> None:
    ok, reason = check_vega_lite(
        {"layer": [{"mark": "line", "data": {"url": "https://ex/data.json"}}]}
    )
    assert not ok
    assert "data" in (reason or "")


def test_vega_lite_rejects_expr_signal_and_params() -> None:
    for bad in (
        {"mark": "line", "encoding": {"x": {"field": "x", "type": "quantitative"}}, "params": []},
        {"mark": {"type": "line", "tooltip": {"expr": "datum.x"}}},
        {"mark": "line", "config": {"signal": "s"}},
    ):
        ok, _reason = check_vega_lite(bad)
        assert not ok, bad


def test_vega_lite_rejects_lookup_loader_calculate_transforms() -> None:
    """``lookup``/``loader`` はどこにあっても拒否される深さ問わずのキー
    (:data:`_DENIED_ANYWHERE_KEYS`)でもあるため、理由の文言は「key not
    allowed」/「transform type not allowed」のどちらもあり得る — ここでは
    「通らない」ことだけを見る(``calculate`` は transform 固有の拒否)。"""
    for bad_step in (
        {"lookup": "key", "from": {}},
        {"loader": "http"},
        {"calculate": "datum.x * 2", "as": "y"},
    ):
        ok, _reason = check_vega_lite({"mark": "line", "transform": [bad_step]})
        assert not ok, bad_step


def test_vega_lite_rejects_unknown_transform_type() -> None:
    ok, _reason = check_vega_lite({"mark": "line", "transform": [{"mystery": True}]})
    assert not ok


def test_vega_lite_rejects_url_like_strings() -> None:
    ok, reason = check_vega_lite({"mark": "line", "title": "see https://ex/leak for more"})
    assert not ok
    assert "url" in (reason or "")


def test_vega_lite_rejects_usermeta() -> None:
    ok, _reason = check_vega_lite({"mark": "line", "usermeta": {"anything": True}})
    assert not ok


def test_vega_lite_rejects_unknown_mark() -> None:
    ok, _reason = check_vega_lite({"mark": "arc"})
    assert not ok


def test_vega_lite_rejects_unknown_encoding_channel() -> None:
    ok, _reason = check_vega_lite({"mark": "line", "encoding": {"detail": {"field": "x"}}})
    assert not ok


def test_vega_lite_rejects_unknown_top_level_key() -> None:
    ok, _reason = check_vega_lite({"mark": "line", "datasets": {}})
    assert not ok


def test_vega_lite_rejects_a_spec_that_is_not_an_object() -> None:
    ok, _reason = check_vega_lite(["mark", "line"])
    assert not ok


def test_vega_lite_rejects_oversized_spec() -> None:
    huge_title = "x" * (17 * 1024)
    ok, reason = check_vega_lite({"mark": "line", "title": huge_title})
    assert not ok
    assert "16KB" in (reason or "")


# ---------------------------------------------------------------------------
# check_table
# ---------------------------------------------------------------------------


def test_table_allows_columns_with_key_label_unit() -> None:
    spec = {
        "variant": "grid",
        "columns": [{"key": "x", "label": "X"}, {"key": "y", "label": "Y", "unit": "m"}],
        "sort": {"field": "y", "dir": "desc"},
    }
    ok, reason = check_table(spec)
    assert ok, reason


def test_table_rejects_missing_columns() -> None:
    ok, _reason = check_table({"variant": "grid"})
    assert not ok


def test_table_rejects_unknown_top_level_key() -> None:
    ok, _reason = check_table({"columns": [{"key": "x", "label": "X"}], "rows": []})
    assert not ok


def test_table_rejects_internal_row_keys_in_a_column() -> None:
    """AI には ``href_field``/``subject_field`` のような UI 内部専用の行キー
    を書かせない(契約 §1-2「表仕様の部分集合」)。"""
    ok, _reason = check_table({"columns": [{"key": "x", "label": "X", "href_field": "iri"}]})
    assert not ok


def test_table_rejects_unknown_variant() -> None:
    ok, _reason = check_table({"variant": "chart", "columns": [{"key": "x", "label": "X"}]})
    assert not ok


def test_table_rejects_bad_sort_dir() -> None:
    ok, _reason = check_table(
        {"columns": [{"key": "x", "label": "X"}], "sort": {"field": "x", "dir": "up"}}
    )
    assert not ok


# ---------------------------------------------------------------------------
# check_mermaid
# ---------------------------------------------------------------------------


def test_mermaid_allows_nodes_and_labelled_edges() -> None:
    text = "flowchart LR\n  a[Start] --> b(Middle)\n  b -->|next| c[End]"
    ok, reason = check_mermaid(text)
    assert ok, reason


def test_mermaid_allows_td_direction() -> None:
    ok, reason = check_mermaid("flowchart TD\n  a[A] --> b[B]")
    assert ok, reason


def test_mermaid_rejects_missing_header() -> None:
    ok, _reason = check_mermaid("a[A] --> b[B]")
    assert not ok


def test_mermaid_rejects_graph_keyword_instead_of_flowchart() -> None:
    ok, _reason = check_mermaid("graph LR\n  a[A] --> b[B]")
    assert not ok


def test_mermaid_rejects_click() -> None:
    text = 'flowchart LR\n  a[A] --> b[B]\n  click a "https://ex/leak"'
    ok, reason = check_mermaid(text)
    assert not ok
    assert "click" in (reason or "")


def test_mermaid_rejects_href_style_and_classdef() -> None:
    for bad_line in ('click a href "x"', "style a fill:#fff", "classDef foo fill:#fff"):
        ok, _reason = check_mermaid(f"flowchart LR\n  a[A]\n  {bad_line}")
        assert not ok, bad_line


def test_mermaid_rejects_subgraphs() -> None:
    """契約 §1-2 の部分集合は節と辺だけ — subgraph はその外(mermaidFlow.ts
    は読めるが、AI にはこの狭い部分集合しか許さない)。"""
    text = "flowchart LR\n  subgraph g\n  a[A]\n  end"
    ok, _reason = check_mermaid(text)
    assert not ok


def test_mermaid_rejects_oversized_text() -> None:
    text = "flowchart LR\n" + "\n".join(f"n{i}[N]" for i in range(2000))
    ok, reason = check_mermaid(text)
    assert not ok
    assert "16KB" in (reason or "")


def test_mermaid_rejects_non_string() -> None:
    ok, _reason = check_mermaid({"lang": "mermaid"})
    assert not ok
