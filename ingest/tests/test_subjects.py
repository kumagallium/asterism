"""Tests for asterism.subjects's shared label-priority helpers (契約メモ §2 の
実機修正): ``LABEL_PREDICATES`` の並び・``label_union_clause`` が組む SPARQL
断片・``pick_label`` の「predicate の優先順位 → 言語 ja→en→無し→最初」選択。

架空の値のみ（分野語は使わない）。SPARQL 断片の妥当性そのものは
test_shape_match.py / test_subject_tools.py / test_class_schema.py の実
pyoxigraph 経由の呼び出し側テストで検証する — ここは純関数のみ。
"""

from __future__ import annotations

import json
from pathlib import Path

from asterism.subjects import (
    LABEL_PREDICATES,
    label_union_clause,
    pick_label,
    resolve_dataset_label,
    valid_dataset_id,
)


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


# ----------------------------------------------------------------------------
# valid_dataset_id / resolve_dataset_label (契約メモ contract_pr_f2.md §3.1/§3.2)
# ----------------------------------------------------------------------------


def test_valid_dataset_id_accepts_the_registry_slug_shape() -> None:
    assert valid_dataset_id("seed-catalogue-aaaa") == "seed-catalogue-aaaa"


def test_valid_dataset_id_rejects_anything_else() -> None:
    assert valid_dataset_id("") is None
    assert valid_dataset_id(None) is None
    assert valid_dataset_id(123) is None
    assert valid_dataset_id("Has-Upper-Case") is None
    assert valid_dataset_id("has/slash") is None
    assert valid_dataset_id("has space") is None


def test_resolve_dataset_label_none_registry_root_is_the_id_itself() -> None:
    assert resolve_dataset_label(None, "seed-catalogue-aaaa") == "seed-catalogue-aaaa"


def test_resolve_dataset_label_unsafe_id_is_the_input_verbatim(tmp_path: Path) -> None:
    # 不正な形は空振り（呼び出し境界の 400/404 判定はここの責務ではない —
    # ここは「表示名を 1 つ選ぶ」だけの純関数）。
    assert resolve_dataset_label(tmp_path, "not a slug") == "not a slug"


def test_resolve_dataset_label_falls_back_to_meta_json_name(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "seed-catalogue-aaaa"
    dataset_dir.mkdir()
    (dataset_dir / "meta.json").write_text(
        json.dumps({"id": "seed-catalogue-aaaa", "name": "種苗カタログ"}),
        encoding="utf-8",
    )
    assert resolve_dataset_label(tmp_path, "seed-catalogue-aaaa") == "種苗カタログ"


def test_resolve_dataset_label_falls_back_to_id_when_nothing_is_recorded(
    tmp_path: Path,
) -> None:
    dataset_dir = tmp_path / "seed-catalogue-aaaa"
    dataset_dir.mkdir()
    assert resolve_dataset_label(tmp_path, "seed-catalogue-aaaa") == "seed-catalogue-aaaa"


def test_resolve_dataset_label_prefers_metadata_ttl_title_over_meta_json_name(
    tmp_path: Path,
) -> None:
    from asterism import substrate

    dataset_id = "seed-catalogue-aaaa"
    dataset_dir = tmp_path / dataset_id
    dataset_dir.mkdir()
    (dataset_dir / "meta.json").write_text(
        json.dumps({"id": dataset_id, "name": "旧い名前"}), encoding="utf-8"
    )
    subject = substrate.dataset_iri(dataset_id)
    (dataset_dir / "metadata.ttl").write_text(
        "\n".join(
            [
                "@prefix dcterms: <http://purl.org/dc/terms/> .",
                f'<{subject}> dcterms:title "Seed catalogue"@en, "種苗カタログ"@ja .',
            ]
        ),
        encoding="utf-8",
    )
    assert resolve_dataset_label(tmp_path, dataset_id) == "種苗カタログ"


def test_resolve_dataset_label_ignores_an_empty_metadata_ttl(tmp_path: Path) -> None:
    # 契約メモの見本データセット（world）は登録時点で ``metadata.ttl`` が
    # 空ファイルのまま — 空文字列は「タイトルが無い」と同じに扱い、
    # meta.json の name へ落ちる（例外を投げてはいけない）。
    dataset_id = "seed-catalogue-aaaa"
    dataset_dir = tmp_path / dataset_id
    dataset_dir.mkdir()
    (dataset_dir / "meta.json").write_text(
        json.dumps({"id": dataset_id, "name": "種苗カタログ"}), encoding="utf-8"
    )
    (dataset_dir / "metadata.ttl").write_text("", encoding="utf-8")
    assert resolve_dataset_label(tmp_path, dataset_id) == "種苗カタログ"
