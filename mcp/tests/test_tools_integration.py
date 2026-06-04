"""Integration check: run the typed-tool SPARQL against a *real* SPARQL engine.

``test_tools.py`` mocks the SPARQL-results JSON, so it cannot catch a malformed
query string. This module loads synthetic starrydata triples into an in-memory
SPARQL engine and runs the real queries end to end, so a broken FILTER / BIND /
OPTIONAL / aggregate surfaces here.

Oxigraph is the production engine; rdflib is used here only because it is a
portable, dependency-light SPARQL 1.1 engine good enough to validate query
structure and results. rdflib is a core dependency (the ingester requires it).
"""

from __future__ import annotations

import json

import pytest
import rdflib
from asterism.starrydata import DEFAULT_ONTOLOGY, DEFAULT_RESOURCE
from asterism.substrate import canonical_graph_iri, draft_graph_iri, ontology_graph_iri

from asterism_mcp.tools import (
    property_ranking,
    provenance_of,
    sample_search,
    schema_summary,
    sparql_query,
)

SD = DEFAULT_ONTOLOGY
SDR = DEFAULT_RESOURCE

# Synthetic starrydata-shaped graph: 2 papers, 2 samples (SnSe / Bi2Te3),
# 3 curves (a plausible ZT=2.6, an implausible ZT=13000 outlier, a Seebeck),
# wired to ingestion + digitization activities via prov:wasGeneratedBy.
_TTL = f"""
@prefix sd: <{SD}> .
@prefix schema: <https://schema.org/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<{SDR}paper/1> a sd:Paper ; schema:name "SnSe paper" ;
    dcterms:identifier "10.1/xyz" ; prov:wasGeneratedBy <{SDR}ingestion/1> .
<{SDR}paper/2> a sd:Paper ; schema:name "Bi2Te3 paper" ;
    prov:wasGeneratedBy <{SDR}ingestion/1> .

<{SDR}sample/1-1> a sd:Sample ; sd:compositionString "SnSe" ;
    schema:name "SnSe sample" ; sd:fromPaper <{SDR}paper/1> ;
    prov:wasGeneratedBy <{SDR}ingestion/1> .
<{SDR}sample/2-1> a sd:Sample ; sd:compositionString "Bi2Te3" ;
    schema:name "BiTe sample" ; sd:fromPaper <{SDR}paper/2> ;
    prov:wasGeneratedBy <{SDR}ingestion/1> .

<{SDR}curve/1-1-1> a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "2.6"^^xsd:double ;
    sd:figureName "Fig.3" ; sd:ofSample <{SDR}sample/1-1> ;
    prov:wasGeneratedBy <{SDR}ingestion/1> , <{SDR}digitization/1> .
<{SDR}curve/1-1-2> a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "13000.0"^^xsd:double ;
    sd:figureName "Fig.4" ; sd:ofSample <{SDR}sample/1-1> ;
    prov:wasGeneratedBy <{SDR}ingestion/1> .
<{SDR}curve/2-1-1> a sd:Curve ; sd:propertyY "Seebeck coefficient" ;
    sd:yMax "220.0"^^xsd:double ; sd:ofSample <{SDR}sample/2-1> ;
    prov:wasGeneratedBy <{SDR}ingestion/1> .

<{SDR}ingestion/1> a sd:IngestionActivity ;
    prov:atTime "2026-05-01T00:00:00Z"^^xsd:dateTime .
<{SDR}digitization/1> a sd:DigitizationActivity ;
    prov:atTime "2020-01-01T00:00:00Z"^^xsd:dateTime .
"""


def _client():
    """A minimal client whose sparql_select runs against an in-memory rdflib store.

    Uses a ``ConjunctiveGraph`` (a quad store) — not a single ``Graph`` — so the
    canonical-scope reads (which use ``GRAPH ?g { ... }`` to span per-dataset
    canonical named graphs, #20 P3) execute the same way they do against Oxigraph.
    (``Dataset`` would be the non-deprecated type, but it currently emits a flood
    of internal DeprecationWarnings per query; ConjunctiveGraph keeps logs clean.)
    """
    g = rdflib.ConjunctiveGraph()
    g.parse(data=_TTL, format="turtle")

    class _LocalClient:
        async def sparql_select(self, query: str) -> dict:
            raw = g.query(query).serialize(format="json")
            if isinstance(raw, bytes):
                raw = raw.decode()
            return json.loads(raw)

    return _LocalClient()


async def test_sample_search_composition_real() -> None:
    out = await sample_search(_client(), composition="bi2te3")
    comps = {r["composition"] for r in out["results"]}
    assert "Bi2Te3" in comps
    # SnSe must NOT match a Bi2Te3 substring filter.
    assert "SnSe" not in comps


async def test_sample_search_property_join_real() -> None:
    # Only samples that actually have a ZT curve.
    out = await sample_search(_client(), composition="SnSe", property_y="ZT")
    assert any(r["composition"] == "SnSe" for r in out["results"])
    # Bi2Te3 has a Seebeck curve but no ZT curve -> excluded.
    out2 = await sample_search(_client(), composition="Bi2Te3", property_y="ZT")
    assert out2["count"] == 0


async def test_property_ranking_zt_excludes_outlier_real() -> None:
    out = await property_ranking(_client(), property_y="ZT", top_n=10, max_plausible=3.5)
    assert out["excluded_implausible"] == 1  # the 13000 outlier
    values = [r["value"] for r in out["results"]]
    assert values == [2.6]  # only the plausible peak survives
    assert out["results"][0]["composition"] == "SnSe"
    assert out["results"][0]["curve_iri"] == f"{SDR}curve/1-1-1"


async def test_provenance_of_curve_real() -> None:
    out = await provenance_of(f"{SDR}curve/1-1-1", _client())
    assert out["found"] is True
    steps = [s["step"] for s in out["chain"]]
    assert steps[:3] == ["curve", "sample", "paper"]
    assert "digitization" in steps
    assert "ingestion" in steps
    # the sample step resolves the composition, the paper step the title
    sample_step = next(s for s in out["chain"] if s["step"] == "sample")
    assert sample_step["iri"] == f"{SDR}sample/1-1"
    paper_step = next(s for s in out["chain"] if s["step"] == "paper")
    assert paper_step["iri"] == f"{SDR}paper/1"


# ---- #20 P3: canonical scope reads per-dataset canonical graphs, drops drafts --

# rdflib's internal SPARQL evaluation emits DeprecationWarnings on a Dataset; we
# need Dataset here (not ConjunctiveGraph) because only its default_union=False
# default models Oxigraph faithfully: GRAPH-less patterns read ONLY the default
# graph, so the draft-exclusion below is meaningful rather than masked by a
# union-everything default. The warnings are rdflib-internal, not our query.
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_canonical_scope_reads_named_graph_and_excludes_draft() -> None:
    ds = rdflib.Dataset()  # default_union=False: GRAPH-less reads only default
    canon = ds.graph(rdflib.URIRef(canonical_graph_iri("ds1")))
    draft = ds.graph(rdflib.URIRef(draft_graph_iri("ds2")))
    sd = rdflib.Namespace(SD)
    canon.add((rdflib.URIRef("https://ex/s/c1"), rdflib.RDF.type, sd.Sample))
    canon.add((rdflib.URIRef("https://ex/s/c1"), sd.compositionString, rdflib.Literal("CanonComp")))
    draft.add((rdflib.URIRef("https://ex/s/d1"), rdflib.RDF.type, sd.Sample))
    draft.add((rdflib.URIRef("https://ex/s/d1"), sd.compositionString, rdflib.Literal("DraftComp")))

    class _C:
        async def sparql_select(self, query: str) -> dict:
            raw = ds.query(query).serialize(format="json")
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    comps = {r["composition"] for r in (await sample_search(_C()))["results"]}
    assert "CanonComp" in comps  # per-dataset canonical named graph IS read
    assert "DraftComp" not in comps  # unreviewed draft graph is excluded from Ask


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_retracted_canonical_graph_excluded_from_ask() -> None:
    from asterism.substrate import CONTROL_GRAPH_IRI, STATUS_PREDICATE, canonical_graph_iri

    ds = rdflib.Dataset()
    sd = rdflib.Namespace(SD)
    g1 = ds.graph(rdflib.URIRef(canonical_graph_iri("ds1")))
    g2 = ds.graph(rdflib.URIRef(canonical_graph_iri("ds2")))
    for g, comp, sid in ((g1, "ActiveComp", "a1"), (g2, "RetractedComp", "r1")):
        g.add((rdflib.URIRef(f"https://ex/s/{sid}"), rdflib.RDF.type, sd.Sample))
        g.add((rdflib.URIRef(f"https://ex/s/{sid}"), sd.compositionString, rdflib.Literal(comp)))
    # Tombstone ds2 in the control graph -> it leaves the canonical scope.
    ctrl = ds.graph(rdflib.URIRef(CONTROL_GRAPH_IRI))
    ctrl.add(
        (
            rdflib.URIRef(canonical_graph_iri("ds2")),
            rdflib.URIRef(STATUS_PREDICATE),
            rdflib.Literal("retracted"),
        )
    )

    class _C:
        async def sparql_select(self, query: str) -> dict:
            raw = ds.query(query).serialize(format="json")
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    comps = {r["composition"] for r in (await sample_search(_C()))["results"]}
    assert "ActiveComp" in comps  # active canonical graph is read
    assert "RetractedComp" not in comps  # retracted canonical graph is excluded


# ---- #20 FROM-merge: the cross-dataset JOIN this whole change exists for -------


def _cross_dataset_ds() -> rdflib.Dataset:
    """A sample in dataset A links (sd:fromPaper) to a paper in dataset B.

    Under the old GRAPH-union scope this join could not resolve (the two triples
    live in different named graphs); FROM-merge merges both canonical graphs into
    one query dataset so the join across them succeeds.
    """
    ds = rdflib.Dataset()
    sd = rdflib.Namespace(SD)
    schema = rdflib.Namespace("https://schema.org/")
    ga = ds.graph(rdflib.URIRef(canonical_graph_iri("a")))
    gb = ds.graph(rdflib.URIRef(canonical_graph_iri("b")))
    sample = rdflib.URIRef("https://ex/sample/1")
    paper = rdflib.URIRef("https://ex/paper/1")
    ga.add((sample, rdflib.RDF.type, sd.Sample))
    ga.add((sample, sd.compositionString, rdflib.Literal("SnSe")))
    ga.add((sample, sd.fromPaper, paper))  # link points into dataset B
    gb.add((paper, schema.name, rdflib.Literal("Shared paper")))  # lives in dataset B
    return ds


def _ds_from(graphs: dict[str, str]) -> rdflib.Dataset:
    """Build a Dataset from a ``{graph_iri: turtle}`` mapping."""
    ds = rdflib.Dataset()
    for giri, ttl in graphs.items():
        ds.graph(rdflib.URIRef(giri)).parse(data=ttl, format="turtle")
    return ds


def _ds_client(ds):
    if isinstance(ds, dict):
        ds = _ds_from(ds)

    class _C:
        async def sparql_select(self, query: str) -> dict:
            raw = ds.query(query).serialize(format="json")
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    return _C()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_sample_search_joins_across_datasets() -> None:
    out = await sample_search(_ds_client(_cross_dataset_ds()), composition="SnSe")
    assert out["count"] == 1
    # The paper title lives in a DIFFERENT canonical graph; FROM-merge resolves it.
    assert out["results"][0]["title"] == "Shared paper"
    assert out["results"][0]["paper_iri"] == "https://ex/paper/1"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_sparql_query_escape_joins_across_datasets_and_discloses() -> None:
    q = (
        "SELECT ?title WHERE { "
        f"?s <{SD}fromPaper> ?p . ?p <https://schema.org/name> ?title }}"
    )
    out = await sparql_query(q, _ds_client(_cross_dataset_ds()))
    assert out["count"] == 1
    assert out["rows"][0]["title"]["value"] == "Shared paper"
    # The executed (FROM-injected) query is disclosed for transparency.
    assert "FROM <" in out["effective_query"]
    assert canonical_graph_iri("a") in out["effective_query"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_sparql_query_escape_respects_explicit_from() -> None:
    # A query that scopes itself to ONE dataset is left untouched -> no B title.
    ds = _cross_dataset_ds()
    q = (
        f"SELECT ?p FROM <{canonical_graph_iri('a')}> "
        f"WHERE {{ ?s <{SD}fromPaper> ?p }}"
    )
    out = await sparql_query(q, _ds_client(ds))
    assert out["count"] == 1  # the link triple is in A
    assert "effective_query" not in out  # not rewritten


# ---- #20 step5: schema_summary enriches with TBox labels from ontology graph --


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_schema_summary_enriches_with_ontology_labels() -> None:
    abox = f"""
    @prefix sd: <{SD}> .
    <https://ex/s/1> a sd:Sample ; sd:fromPaper <https://ex/p/1> .
    """
    # Projected TBox lives in a separate ontology graph (NOT canonical scope).
    tbox = f"""
    @prefix sd: <{SD}> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    sd:Sample rdfs:label "Sample" .
    sd:fromPaper rdfs:label "from paper" .
    """
    out = await schema_summary(
        _ds_client({canonical_graph_iri("ds1"): abox, ontology_graph_iri("ds1"): tbox})
    )
    cls = next(c for c in out["classes"] if c["iri"] == f"{SD}Sample")
    assert cls["label"] == "Sample"  # class label from the ontology graph
    pred = next(p for p in out["predicates"] if p["iri"] == f"{SD}fromPaper")
    assert pred["label"] == "from paper"  # predicate label from the ontology graph


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_schema_summary_without_ontology_graph_has_no_labels() -> None:
    # Invariant: with no projected TBox, schema_summary still works (no labels).
    abox = f"""
    @prefix sd: <{SD}> .
    <https://ex/s/1> a sd:Sample .
    """
    out = await schema_summary(_ds_client({canonical_graph_iri("ds1"): abox}))
    cls = next(c for c in out["classes"] if c["iri"] == f"{SD}Sample")
    assert "label" not in cls  # degrades gracefully
