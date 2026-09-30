"""Tests for external-standard grounding (asterism.grounding).

The grounding search underpins the "data lands on CMSO/QUDT/schema.org" experience
(external-standard-alignment.md §8). Its two load-bearing safety properties are:

1. **closed-set** — a result can only ever be a real IRI that is IN the curated
   catalog (no fabrication; the human still vets the pick).
2. **deterministic** — the same query always returns the same ranking (no LLM / network
   / randomness), so it is safe to call from the API / propose.

The rest is matcher quality: famous materials terms surface for plausible queries.
"""

from __future__ import annotations

import pytest

from asterism.grounding import catalog_terms, ground_terms, load_catalog, vocabularies
from asterism.grounding.catalog import _all_terms


def test_catalog_loads_and_is_nonempty() -> None:
    vocabs = vocabularies()
    assert vocabs, "the curated catalog should ship with the package"
    prefixes = {v.prefix for v in vocabs}
    # The famous materials + generic foundations must be present.
    for p in ("cmso", "qudt", "schema", "prov", "dcterms"):
        assert p in prefixes


def test_every_term_iri_is_namespace_plus_name_or_explicit() -> None:
    """Integrity: 語の IRI は namespace + name。ただし不透明 IRI の語彙（EMMO）は
    実 IRI を明示して持つ — その場合も **その語彙の名前空間の下**であること。

    名前と IRI を分けられるようにしたのは照合のためだが、名前空間の外を指せると
    「どの語彙の語か」が嘘になる（カタログの帰属がそのまま画面と引用に出る）ので、
    そこは緩めない。
    """
    for term in _all_terms():
        if term.explicit_iri:
            assert term.explicit_iri.startswith(term.namespace), term.curie
        else:
            assert term.iri == term.namespace + term.name
        assert term.kind in {"class", "property"}
        assert term.iri.startswith(("http://", "https://"))


def test_cmso_namespace_is_authoritative_http() -> None:
    """CMSO's authoritative term IRIs are http:// (the https:// PURL only redirects to
    HTML docs). Reuse must match what the ontology actually mints."""
    cmso = next(v for v in vocabularies() if v.prefix == "cmso")
    assert cmso.namespace == "http://purls.helmholtz-metadaten.de/cmso/"


def test_crystal_structure_grounds_to_cmso() -> None:
    top = ground_terms("crystal structure", kind="class", limit=5)
    assert top, "expected a candidate for 'crystal structure'"
    best = top[0]
    assert best.iri == "http://purls.helmholtz-metadaten.de/cmso/CrystalStructure"
    assert best.curie == "cmso:CrystalStructure"
    assert best.match == "exact"


def test_property_query_strips_leading_has() -> None:
    """'space group' (a bare noun) should also reach the cmso:hasSpaceGroup property."""
    iris = {c.iri for c in ground_terms("space group", limit=8)}
    assert "http://purls.helmholtz-metadaten.de/cmso/hasSpaceGroup" in iris
    iris2 = {c.iri for c in ground_terms("structure", kind="property", limit=8)}
    assert "http://purls.helmholtz-metadaten.de/cmso/hasStructure" in iris2


def test_unit_and_quantity_ground_to_qudt() -> None:
    assert any(c.prefix == "qudt" for c in ground_terms("unit", limit=5))
    q = ground_terms("quantity", kind="class", limit=3)
    assert q and q[0].iri == "http://qudt.org/schema/qudt/Quantity"


def test_kind_filter_restricts_results() -> None:
    classes = ground_terms("identifier", kind="class", limit=10)
    assert all(c.kind == "class" for c in classes)
    props = ground_terms("identifier", kind="property", limit=10)
    assert props and all(c.kind == "property" for c in props)


def test_domain_filter_restricts_results() -> None:
    mats = ground_terms("structure", domain="materials", limit=10)
    assert mats and all(c.domain == "materials" for c in mats)


def test_results_are_a_closed_set() -> None:
    """No matter the query, every returned IRI is a real catalog term (never invented)."""
    catalog_iris = {t.iri for t in _all_terms()}
    for query in ("crystal", "structure", "composition", "zzzz", "the material sample"):
        for cand in ground_terms(query, limit=20):
            assert cand.iri in catalog_iris


def test_deterministic_ranking() -> None:
    a = [c.iri for c in ground_terms("space group", limit=8)]
    b = [c.iri for c in ground_terms("space group", limit=8)]
    assert a == b


def test_no_match_returns_empty() -> None:
    assert ground_terms("xyzzyqwertij", limit=5) == []
    assert ground_terms("", limit=5) == []


# ── 部分一致は語の境目で ───────────────────────────────────────────────────────
# 実測 2026-09-30: カタログの 1 文字の語（class ``C``）が "Country" "Record_…"
# "IngestionActivity" のすべてに候補として出た。文字列として含まれるだけでは当てない。


def _substring_hits(query: str, kind: str | None = None) -> set[str]:
    return {c.name for c in ground_terms(query, kind=kind, limit=50) if c.match == "substring"}


@pytest.mark.parametrize("query", ["Country", "Record_50577d", "IngestionActivity", "Recipe"])
def test_a_one_letter_term_is_not_found_inside_a_longer_name(query: str) -> None:
    short = {t.name for t in _all_terms() if len(t.name) <= 2}
    assert short, "カタログに 1〜2 文字の語がある前提のテスト"
    assert not (_substring_hits(query) & short)


@pytest.mark.parametrize("query", ["cardNo", "population", "servings", "lifeExpectancy"])
def test_a_one_letter_property_is_not_found_inside_a_longer_name(query: str) -> None:
    assert not {n for n in _substring_hits(query, "property") if len(n) <= 2}


@pytest.mark.parametrize("query", ["no", "pH"])
def test_a_one_or_two_letter_query_is_never_a_substring(query: str) -> None:
    assert _substring_hits(query) == set()


def test_a_one_letter_term_still_matches_exactly() -> None:
    (top,) = ground_terms("C", kind="class", limit=1)
    assert top.match == "exact"
    assert top.name == "C"


def test_a_three_letter_word_matches_only_as_a_whole_word() -> None:
    # "imageUrl" の "url" は語そのもの → 当たる。"percentage" の "age" は語の途中 → 当たらない。
    assert any(n.lower() == "url" for n in _substring_hits("imageUrl", "property"))
    assert "age" not in _substring_hits("percentage", "property")


def test_a_longer_fragment_must_start_at_a_word() -> None:
    # 語の頭から始まる略記は当たる（"temp" → "…Temperature"）。
    assert any("Temperature" in n for n in _substring_hits("temp", "property"))
    # 語の途中にたまたま現れる並びは当たらない（"IngestionActivity" の中の "ionactivity"）。
    assert "IonActivity" not in _substring_hits("IngestionActivity", "class")
    assert "Activity" in _substring_hits("IngestionActivity", "class")


def test_limit_is_respected() -> None:
    assert len(ground_terms("structure", limit=2)) <= 2


def test_bad_kind_raises() -> None:
    with pytest.raises(ValueError):
        ground_terms("structure", kind="relation")


def test_catalog_is_cached_singleton() -> None:
    assert load_catalog() is load_catalog()


# ── catalog_terms: the closed set itself, listed unranked ────────────────────


def test_catalog_terms_is_the_whole_closed_set() -> None:
    """フィルタ無しなら `_all_terms` と同じ集合 — 検索を経由しない素の一覧。"""
    assert catalog_terms() == list(_all_terms())


def test_catalog_terms_filters_by_kind_and_domain() -> None:
    classes = catalog_terms(kind="class")
    assert classes, "curated catalog has classes"
    assert {t.kind for t in classes} == {"class"}
    # 綴りを渡す用途で load-bearing な代表例。
    assert "CrystalStructure" in {t.name for t in classes}
    materials = catalog_terms(kind="class", domain="materials")
    assert materials and {t.domain for t in materials} == {"materials"}
    assert len(materials) < len(classes)


def test_catalog_terms_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError):
        catalog_terms(kind="klass")


def test_an_opaque_iri_vocabulary_keeps_its_real_identifier() -> None:
    """不透明 IRI の語彙（EMMO）は、照合名に prefLabel を使いつつ **実在する IRI**
    をそのまま名乗る。

    ⭐EMMO は語を ``…#EMMO_0bb3b434_…`` で鋳造し、読める名前は skos:prefLabel に
    しか無い（実測 2026-09-03: emmo# の 2,631 語中 1,967 語が不透明）。名前＝IRI の
    末尾に固定したままだと、照合できる語と実在する IRI のどちらかを諦めることに
    なる — 捏造しないという不変条件は IRI 側の話なので、そこは実ファイルの値を持つ。
    """
    hits = [c for c in ground_terms("Crystal", kind="class", limit=5) if c.prefix == "emmo"]
    assert hits, "EMMO の語が接地候補に出ること"
    top = hits[0]
    # 名前は人が探せる語、IRI は EMMO 本体の不透明識別子
    assert top.name == "Crystal"
    assert top.iri.startswith("https://w3id.org/emmo#EMMO_")
    # CURIE は名前側（画面に出る）
    assert top.curie == "emmo:Crystal"


def test_terms_without_an_explicit_iri_still_concatenate() -> None:
    """明示 IRI を持たない語（大多数）は従来どおり namespace + name。"""
    hits = [c for c in ground_terms("title", kind="property", limit=5) if c.prefix == "dcterms"]
    assert hits and hits[0].iri == "http://purl.org/dc/terms/title"


def test_a_lone_word_before_of_is_not_a_candidate() -> None:
    """実測 2026-09-30: 項目 ``amount`` に qudt:dimensionExponentForAmountOfSubstance が
    候補として出た。``of`` の前は入れ物で、「何の」は ``of`` の後ろに言う。"""
    curies = [c.curie for c in ground_terms("amount", kind="property", limit=8)]
    assert "qudt:dimensionExponentForAmountOfSubstance" not in curies
    # 末尾の of は区切りにしない（"is version of" は version の話）。
    assert "dcterms:isVersionOf" in [c.curie for c in ground_terms("version", limit=8)]
