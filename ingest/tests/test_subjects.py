"""Tests for asterism.subjects's shared label-priority helpers (契約メモ §2 の
実機修正): ``LABEL_PREDICATES`` の並び・``label_union_clause`` が組む SPARQL
断片・``pick_label`` の「predicate の優先順位 → 言語 ja→en→無し→最初」選択。

架空の値のみ（分野語は使わない）。SPARQL 断片の妥当性そのものは
test_shape_match.py / test_subject_tools.py / test_class_schema.py の実
pyoxigraph 経由の呼び出し側テストで検証する — ここは純関数のみ。
"""
from __future__ import annotations

from asterism.subjects import LABEL_PREDICATES, label_union_clause, pick_label


def test_label_predicates_priority_order() -> None:
    assert LABEL_PREDICATES == (
        "http://www.w3.org/2000/01/rdf-schema#label",
        "http://schema.org/name",
        "https://schema.org/name",
        "http://purl.org/dc/terms/title",
        "http://www.w3.org/2004/02/skos/core#prefLabel",
        "http://xmlns.com/foaf/0.1/name",
    )


def test_label_union_clause_embeds_every_predicate_with_its_rank() -> None:
    clause = label_union_clause("?t")
    assert clause.startswith("OPTIONAL {")
    for rank, pred in enumerate(LABEL_PREDICATES):
        assert f"(<{pred}> {rank})" in clause
    assert "?t ?__lp ?label" in clause
    assert "FILTER(isLiteral(?label))" in clause


def test_pick_label_prefers_lower_rank_over_language() -> None:
    # rank 0 (rdfs:label, English) must win over rank 1 (schema:name, Japanese)
    # — predicate priority decides first, language only breaks ties within it.
    candidates = [("Item (en)", 1, "en"), ("Item (rdfs)", 0, "en")]
    assert pick_label(candidates) == "Item (rdfs)"


def test_pick_label_prefers_japanese_within_the_same_rank() -> None:
    candidates = [("Item", 0, "en"), ("アイテム", 0, "ja")]
    assert pick_label(candidates) == "アイテム"


def test_pick_label_falls_back_to_english_then_untagged_then_first() -> None:
    assert pick_label([("Item", 0, "en")]) == "Item"
    assert pick_label([("Item", 0, "")]) == "Item"
    assert pick_label([("Zeta", 0, None), ("Alpha", 0, None)]) == "Alpha"


def test_pick_label_none_when_every_candidate_is_empty() -> None:
    assert pick_label([]) is None
    assert pick_label([(None, 0, None), (None, None, None)]) is None


def test_pick_label_schema_org_name_only_resource_is_not_dropped() -> None:
    # 実機所見の最小再現: rdfs:label が無く schema:name だけの資源でも、
    # local-name フォールバックへ落ちずに schema:name の値を選ぶ。
    candidates = [("Hydrogen", 1, None)]
    assert pick_label(candidates) == "Hydrogen"
