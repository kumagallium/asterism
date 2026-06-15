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

from asterism.grounding import ground_terms, load_catalog, vocabularies
from asterism.grounding.catalog import _all_terms


def test_catalog_loads_and_is_nonempty() -> None:
    vocabs = vocabularies()
    assert vocabs, "the curated catalog should ship with the package"
    prefixes = {v.prefix for v in vocabs}
    # The famous materials + generic foundations must be present.
    for p in ("cmso", "qudt", "schema", "prov", "dcterms"):
        assert p in prefixes


def test_every_term_iri_is_namespace_plus_name() -> None:
    """Integrity: a term's IRI is exactly namespace + name (no drift / typo splits)."""
    for term in _all_terms():
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


def test_limit_is_respected() -> None:
    assert len(ground_terms("structure", limit=2)) <= 2


def test_bad_kind_raises() -> None:
    with pytest.raises(ValueError):
        ground_terms("structure", kind="relation")


def test_catalog_is_cached_singleton() -> None:
    assert load_catalog() is load_catalog()
