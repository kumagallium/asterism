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

import rdflib
from asterism.starrydata import DEFAULT_ONTOLOGY, DEFAULT_RESOURCE

from asterism_mcp.tools import property_ranking, provenance_of, sample_search

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
    """A minimal client whose sparql_select runs against an in-memory rdflib graph."""
    g = rdflib.Graph()
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
