"""#20 P4 generality proof: the *content-declared* starrydata query tools
(datasets/starrydata/query_tools.yaml, executed by the schema-agnostic
asterism.query_tools engine) produce the SAME results as the hardcoded typed
tools in asterism_mcp.tools, over the same data.

If these stay equal, the declarative mechanism faithfully reproduces the
hardcoded behaviour — so a non-starrydata dataset can get the same typed path by
shipping its own query_tools.yaml, with no engine code. (mcp can import both the
engine and the hardcoded tools; ingest tests cannot, hence this lives here.)
"""
from __future__ import annotations

import json

import pytest
import rdflib
from asterism.query_tools import load_query_tools, run_query_tool
from asterism.starrydata import DEFAULT_ONTOLOGY as SD
from asterism.substrate import canonical_graph_iri

from asterism_mcp.tools import property_ranking, sample_search

_TTL = f"""
@prefix sd: <{SD}> .
@prefix schema: <https://schema.org/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<https://ex/paper/1> a sd:Paper ; schema:name "SnSe paper" .
<https://ex/paper/2> a sd:Paper ; schema:name "Bi2Te3 paper" .
<https://ex/sample/1> a sd:Sample ; sd:compositionString "SnSe" ;
    schema:name "SnSe sample" ; sd:fromPaper <https://ex/paper/1> .
<https://ex/sample/2> a sd:Sample ; sd:compositionString "Bi2Te3" ;
    sd:fromPaper <https://ex/paper/2> .
<https://ex/curve/1> a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "2.6"^^xsd:double ;
    sd:ofSample <https://ex/sample/1> .
<https://ex/curve/2> a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "13000.0"^^xsd:double ;
    sd:ofSample <https://ex/sample/1> .
<https://ex/curve/3> a sd:Curve ; sd:propertyY "Seebeck coefficient" ;
    sd:yMax "220.0"^^xsd:double ; sd:ofSample <https://ex/sample/2> .
"""


def _client():
    ds = rdflib.ConjunctiveGraph()
    ds.get_context(rdflib.URIRef(canonical_graph_iri("legacy"))).parse(data=_TTL, format="turtle")

    class _C:
        async def sparql_select(self, query: str) -> dict:
            raw = ds.query(query).serialize(format="json")
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    return _C()


def _tools():
    return {t.name: t for t in load_query_tools("starrydata")}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_property_ranking_content_equals_hardcoded() -> None:
    args = {"property_y": "ZT", "top_n": 10, "max_plausible": 3.5}
    hard = await property_ranking(_client(), **args)
    soft = await run_query_tool(_client(), _tools()["property_ranking"], args)
    # The content tool reproduces the hardcoded tool's per-item ranking output.
    assert soft["items"] == hard["results"]
    assert soft["items"] and soft["items"][0]["value"] == 2.6  # outlier excluded


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_sample_search_content_equals_hardcoded() -> None:
    for args in ({}, {"composition": "snse"}, {"property_y": "ZT"}):
        hard = await sample_search(_client(), **args)
        soft = await run_query_tool(_client(), _tools()["sample_search"], args)
        # Order is engine-stable but compare as sets to be robust.
        key = lambda r: (r["sample_iri"], r.get("composition"), r.get("title"))  # noqa: E731
        assert sorted(map(key, soft["items"])) == sorted(map(key, hard["results"])), args
