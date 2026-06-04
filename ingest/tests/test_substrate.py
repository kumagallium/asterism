"""Tests for asterism.substrate (declarative-substrate ingestion, #15).

The Morph-KGC step needs the optional ``substrate`` extra + real CSVs, so it is
not exercised here; these tests cover the parts that do not depend on it: the
draft graph IRI scheme, thread-safe rml:source absolutization, and loading a
graph into Oxigraph (via a fake client). The Morph-KGC path is proven by the
``experiments/phase5-morph-kgc-spike`` e2e.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import rdflib

from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    GRAPH_BASE,
    ONTOLOGY_GRAPH_BASE,
    absolutize_rml_sources,
    alignment_report,
    canonical_graph_iri,
    classify_alignment,
    draft_graph_iri,
    ingest_graph_to_oxigraph,
    materialize_to_graph,
    ontology_graph_iri,
    promote_draft_to_canonical,
    run_substrate_ingest,
)

# ---- draft graph IRI scheme -------------------------------------------------


def test_draft_graph_iri_scheme() -> None:
    assert draft_graph_iri("starrydata-1700000000") == GRAPH_BASE + "draft/starrydata-1700000000"


def test_draft_graph_iri_rejects_unsafe_id() -> None:
    for bad in ("../escape", "a b", "x/y", "", "<inject>"):
        with pytest.raises(ValueError, match="unsafe dataset_id"):
            draft_graph_iri(bad)


# ---- #20 P3 lifecycle graph IRIs (dataset-neutral namespace) ----------------


def test_canonical_graph_iri_scheme() -> None:
    assert canonical_graph_iri("ds1") == CANONICAL_GRAPH_BASE + "ds1"
    # Lifecycle graphs are dataset-neutral, NOT under the starrydata GRAPH_BASE.
    assert "/starrydata/" not in canonical_graph_iri("ds1")


def test_ontology_graph_iri_scheme() -> None:
    assert ontology_graph_iri("ds1") == ONTOLOGY_GRAPH_BASE + "ds1"


def test_lifecycle_graph_iris_reject_unsafe_id() -> None:
    for fn in (canonical_graph_iri, ontology_graph_iri):
        for bad in ("../escape", "a b", "x/y", "", "<inject>"):
            with pytest.raises(ValueError, match="unsafe dataset_id"):
                fn(bad)


def test_canonical_and_draft_graphs_are_distinguishable_by_prefix() -> None:
    # The read-model flip (P3 step 2) relies on filtering canonical graphs by
    # prefix to exclude draft graphs from Ask.
    assert canonical_graph_iri("ds1").startswith(CANONICAL_GRAPH_BASE)
    assert not draft_graph_iri("ds1").startswith(CANONICAL_GRAPH_BASE)


# ---- rml:source absolutization (thread-safe alternative to chdir) -----------


def test_absolutize_rewrites_relative_sources() -> None:
    rml = (
        'rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] .\n'
        'rml:logicalSource [ rml:source "samples.csv" ] .\n'
    )
    out = absolutize_rml_sources(rml, "/data/ds1")
    assert 'rml:source "/data/ds1/papers.csv"' in out
    assert 'rml:source "/data/ds1/samples.csv"' in out


def test_absolutize_leaves_absolute_sources_untouched() -> None:
    rml = 'rml:source "/already/abs/papers.csv"'
    assert absolutize_rml_sources(rml, "/data/ds1") == rml


def test_absolutize_only_touches_rml_source() -> None:
    rml = 'rr:template "https://ex/{id}" ; rml:source "c.csv"'
    out = absolutize_rml_sources(rml, "/data/ds1")
    assert 'rr:template "https://ex/{id}"' in out  # template untouched
    assert 'rml:source "/data/ds1/c.csv"' in out


# ---- Oxigraph load (fake client) --------------------------------------------


class _FakeOxi:
    """Records the payload + graph passed to post_turtle_bytes."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str | None]] = []

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        self.calls.append((payload, graph_iri))
        return len(payload)


async def test_ingest_graph_to_oxigraph_posts_to_named_graph() -> None:
    g = rdflib.Graph()
    s = rdflib.URIRef("https://ex/curve/1")
    g.add((s, rdflib.URIRef("https://ex/yMax"), rdflib.Literal(1.45)))
    g.add((s, rdflib.URIRef("https://ex/name"), rdflib.Literal("c1")))
    fake = _FakeOxi()

    n = await ingest_graph_to_oxigraph(g, fake, "https://ex/graph/draft/ds1")

    assert n == 2  # triple count returned
    assert len(fake.calls) == 1
    payload, graph_iri = fake.calls[0]
    assert graph_iri == "https://ex/graph/draft/ds1"
    assert isinstance(payload, bytes)
    assert b"yMax" in payload  # the triple made it into the serialized turtle


# ---- Morph-KGC dependency guard ---------------------------------------------


def _morph_kgc_installed() -> bool:
    try:
        import morph_kgc  # noqa: F401
        return True
    except ImportError:
        return False


def test_materialize_to_graph_requires_morph_kgc(tmp_path: Path) -> None:
    if _morph_kgc_installed():
        pytest.skip("morph-kgc installed; cannot exercise the missing-dependency path")
    with pytest.raises(RuntimeError, match="morph-kgc"):
        materialize_to_graph('rml:source "p.csv"', tmp_path)


async def test_run_substrate_ingest_validates_id_before_work() -> None:
    # An unsafe id must fail fast (ValueError) before touching Morph-KGC/Oxigraph.
    fake = _FakeOxi()
    with pytest.raises(ValueError, match="unsafe dataset_id"):
        await run_substrate_ingest("rml...", "/data", fake, "../escape")
    assert fake.calls == []  # nothing was posted


# ---- promotion: draft -> canonical (#15 S4) ---------------------------------


def test_classify_alignment_splits_reuse_and_new() -> None:
    draft = {"https://schema.org/name", "https://ex/asterism#customProp"}
    canonical = {"https://schema.org/name", "http://purl.org/dc/terms/identifier"}
    out = classify_alignment(draft, canonical)
    assert out["reuse"] == ["https://schema.org/name"]  # already in canonical
    assert out["new"] == ["https://ex/asterism#customProp"]  # not yet


class _FakeSparql:
    """Fake OxigraphClient: canned predicate/class sets + records updates."""

    def __init__(self, draft_preds, canon_preds, draft_classes, canon_classes, draft_n=0):
        self._sets = {
            ("graph", "p"): draft_preds,
            ("default", "p"): canon_preds,
            ("graph", "c"): draft_classes,
            ("default", "c"): canon_classes,
        }
        self._draft_n = draft_n
        self.updates: list[str] = []

    async def sparql_select(self, query: str) -> dict:
        if "COUNT" in query:
            return {"results": {"bindings": [{"c": {"value": str(self._draft_n)}}]}}
        # Draft side names a graph literally (``GRAPH <...draft/...>``); the
        # canonical side is the canonical-scope UNION (``GRAPH ?__cg ... FILTER``).
        scope = "graph" if "GRAPH <" in query else "default"
        kind = "c" if "?s a ?x" in query else "p"
        vals = self._sets[(scope, kind)]
        return {"results": {"bindings": [{"x": {"type": "uri", "value": v}} for v in vals]}}

    async def sparql_update(self, update: str) -> None:
        self.updates.append(update)


async def test_alignment_report_classifies_predicates_and_classes() -> None:
    fake = _FakeSparql(
        draft_preds={"https://schema.org/name", "https://ex#new"},
        canon_preds={"https://schema.org/name"},
        draft_classes={"https://ex#Curve"},
        canon_classes=set(),
    )
    rep = await alignment_report(fake, draft_graph_iri("ds1"))
    assert rep["predicates"]["reuse"] == ["https://schema.org/name"]
    assert rep["predicates"]["new"] == ["https://ex#new"]
    assert rep["classes"]["new"] == ["https://ex#Curve"]  # canonical empty -> all new


async def test_promote_moves_draft_to_canonical_graph() -> None:
    # #20 P3: promote MOVEs the draft into the dataset's canonical NAMED graph
    # (not the shared default graph), so the op is per-dataset graph-scoped.
    fake = _FakeSparql(set(), set(), set(), set(), draft_n=1640)
    draft = draft_graph_iri("ds1")
    canon = canonical_graph_iri("ds1")
    moved = await promote_draft_to_canonical(fake, draft, canon)
    assert moved == 1640
    assert fake.updates == [f"MOVE GRAPH <{draft}> TO GRAPH <{canon}>"]


# ---- FnO namespace normalization (#15 ingest robustness) ---------------------


def test_normalize_fno_namespace_rewrites_old_to_new() -> None:
    from asterism.substrate import normalize_fno_namespace
    old = '@prefix rmlf: <http://semweb.mmlab.be/ns/fnml#> .\n<#M> rmlf:function fn:x .'
    out = normalize_fno_namespace(old)
    assert "http://w3id.org/rml/" in out
    assert "semweb.mmlab.be/ns/fnml" not in out


def test_normalize_fno_namespace_noop_for_new() -> None:
    from asterism.substrate import normalize_fno_namespace
    rml = '@prefix rmlf: <http://w3id.org/rml/> .'
    assert normalize_fno_namespace(rml) == rml
