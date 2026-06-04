"""Tests for the #20 P4 declarative typed-query engine (asterism.query_tools).

Two layers:
- pure: parse/validate declarations, safe parameter binding, template rendering.
- integration: run a rendered tool over a real rdflib SPARQL engine through the
  canonical FROM-merge, including a cross-dataset join.
"""
from __future__ import annotations

import json

import pytest
import rdflib

from asterism.query_tools import (
    QueryTool,
    QueryToolError,
    bind_params,
    load_query_tools,
    parse_query_tools,
    render_query,
    run_query_tool,
)
from asterism.substrate import canonical_graph_iri

SD = "https://kumagallium.github.io/asterism/starrydata/ontology#"


# ---------------------------------------------------------------------------
# parse / validate
# ---------------------------------------------------------------------------


def _doc(query: str, params: list | None = None, **kw) -> dict:
    return {
        "tools": [
            {
                "name": "t",
                "title": "T",
                "query": query,
                "parameters": params or [],
                **kw,
            }
        ]
    }


def test_parse_minimal_tool() -> None:
    tools = parse_query_tools(_doc("SELECT ?s WHERE { ?s ?p ?o }"))
    assert len(tools) == 1 and tools[0].name == "t"


def test_parse_rejects_update_form_template() -> None:
    with pytest.raises(QueryToolError, match="read-only"):
        parse_query_tools(_doc("DELETE WHERE { ?s ?p ?o }"))


def test_parse_rejects_unknown_param_in_section() -> None:
    with pytest.raises(QueryToolError, match="unknown param"):
        parse_query_tools(_doc("SELECT ?s WHERE { {{#nope}}?s ?p ?o{{/nope}} }"))


def test_parse_rejects_optional_scalar_outside_section() -> None:
    # `x` is optional + no default, but {{x}} appears outside its section.
    with pytest.raises(QueryToolError, match="outside its"):
        parse_query_tools(
            _doc(
                "SELECT ?s WHERE { ?s ?p {{x}} }",
                params=[{"name": "x", "type": "string", "required": False}],
            )
        )


def test_parse_rejects_duplicate_tool_names() -> None:
    doc = {"tools": [{"name": "t", "query": "SELECT ?s {?s ?p ?o}"}] * 2}
    with pytest.raises(QueryToolError, match="duplicate"):
        parse_query_tools(doc)


def test_parse_enum_requires_values() -> None:
    with pytest.raises(QueryToolError, match="enum"):
        parse_query_tools(
            _doc("SELECT ?s {{{#m}}?s ?p {{m}}{{/m}}}", params=[{"name": "m", "type": "enum"}])
        )


def test_load_missing_file_returns_empty(tmp_path) -> None:
    assert load_query_tools("nonesuch", root=tmp_path) == []


def test_available_and_load_all_query_tools(tmp_path) -> None:
    from asterism.query_tools import available_datasets, load_all_query_tools

    (tmp_path / "ds_a").mkdir()
    (tmp_path / "ds_b").mkdir()
    (tmp_path / "ds_c").mkdir()  # no query_tools.yaml -> excluded
    doc = "tools:\n  - name: t\n    query: 'SELECT ?s WHERE { ?s ?p ?o }'\n"
    (tmp_path / "ds_a" / "query_tools.yaml").write_text(doc, encoding="utf-8")
    (tmp_path / "ds_b" / "query_tools.yaml").write_text(doc, encoding="utf-8")

    assert available_datasets(root=tmp_path) == ["ds_a", "ds_b"]
    all_tools = load_all_query_tools(root=tmp_path)
    assert set(all_tools) == {"ds_a", "ds_b"}
    assert [t.name for t in all_tools["ds_a"]] == ["t"]


def test_starrydata_ships_query_tools() -> None:
    # The repo's starrydata content declares the two query tools.
    names = {t.name for t in load_query_tools("starrydata")}
    assert {"property_ranking", "sample_search"} <= names


# ---------------------------------------------------------------------------
# safe parameter binding
# ---------------------------------------------------------------------------


def _tool(query: str, params: list[dict]) -> QueryTool:
    return parse_query_tools(_doc(query, params))[0]


def test_bind_string_is_escaped() -> None:
    t = _tool(
        'SELECT ?s WHERE { {{#x}}?s ?p {{x}}{{/x}} }',
        [{"name": "x", "type": "string", "required": True}],
    )
    bound = bind_params(t, {"x": 'a"b\\c'})
    assert bound["x"]["token"] == '"a\\"b\\\\c"'  # quote + backslash escaped


def test_bind_number_validates_and_clamps() -> None:
    t = _tool(
        "SELECT ?s WHERE { ?s ?p ?o } LIMIT {{n}}",
        [{"name": "n", "type": "integer", "default": 10, "minimum": 1, "maximum": 100}],
    )
    assert bind_params(t, {"n": 500})["n"]["token"] == "100"  # clamped to max
    assert bind_params(t, {"n": 0})["n"]["token"] == "1"  # clamped to min
    with pytest.raises(QueryToolError, match="expected integer"):
        bind_params(t, {"n": "abc"})


def test_bind_iri_validated_and_wrapped() -> None:
    t = _tool(
        "SELECT ?s WHERE { {{#g}}{{g}} ?p ?o{{/g}} }",
        [{"name": "g", "type": "iri", "required": True}],
    )
    assert bind_params(t, {"g": "https://ex/x"})["g"]["token"] == "<https://ex/x>"
    with pytest.raises(QueryToolError, match="http"):
        bind_params(t, {"g": "not-an-iri"})


def test_bind_enum_whitelist() -> None:
    t = _tool(
        "SELECT ?s WHERE { {{#m}}?s ?p {{m}}{{/m}} }",
        [{"name": "m", "type": "enum", "enum": ["a", "b"], "required": True}],
    )
    assert bind_params(t, {"m": "a"})["m"]["token"] == '"a"'
    with pytest.raises(QueryToolError, match="not in"):
        bind_params(t, {"m": "z"})


def test_bind_unknown_arg_and_missing_required() -> None:
    t = _tool(
        "SELECT ?s WHERE { {{#x}}?s ?p {{x}}{{/x}} }",
        [{"name": "x", "type": "string", "required": True}],
    )
    with pytest.raises(QueryToolError, match="unknown argument"):
        bind_params(t, {"y": 1})
    with pytest.raises(QueryToolError, match="missing required"):
        bind_params(t, {})


# ---------------------------------------------------------------------------
# template rendering (sections + inverse + scalars)
# ---------------------------------------------------------------------------


def test_render_section_kept_when_provided() -> None:
    t = _tool(
        "SELECT ?s WHERE { ?s a ?c {{#x}}; ?p {{x}}{{/x}} }",
        [{"name": "x", "type": "string", "required": False}],
    )
    assert "; ?p \"v\"" in render_query(t, {"x": "v"})
    assert "?p" not in render_query(t, {})  # section dropped when absent


def test_render_inverse_section() -> None:
    t = _tool(
        "SELECT ?s WHERE { {{#x}}A{{/x}}{{^x}}B{{/x}} }",
        [{"name": "x", "type": "string", "required": False}],
    )
    assert "A" in render_query(t, {"x": "v"}) and "B" not in render_query(t, {"x": "v"})
    assert "B" in render_query(t, {}) and "A" not in render_query(t, {})


def test_render_empty_string_arg_is_inactive() -> None:
    # An empty string behaves like "not provided" for section purposes.
    t = _tool(
        "SELECT ?s WHERE { {{#x}}A{{/x}}{{^x}}B{{/x}} }",
        [{"name": "x", "type": "string", "required": False}],
    )
    assert "B" in render_query(t, {"x": ""})


# ---------------------------------------------------------------------------
# integration: run over a real rdflib engine via the canonical FROM-merge
# ---------------------------------------------------------------------------

_TTL = f"""
@prefix sd: <{SD}> .
@prefix schema: <https://schema.org/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<https://ex/sample/1> a sd:Sample ; sd:compositionString "SnSe" ;
    sd:fromPaper <https://ex/paper/1> .
<https://ex/paper/1> a sd:Paper ; schema:name "SnSe paper" .
<https://ex/curve/1> a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "2.6"^^xsd:double ;
    sd:ofSample <https://ex/sample/1> .
<https://ex/curve/2> a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "13000.0"^^xsd:double ;
    sd:ofSample <https://ex/sample/1> .
"""


def _ds_client(graphs: dict[str, str]):
    """rdflib client: each {graph_iri: ttl} loaded into that named graph."""
    ds = rdflib.ConjunctiveGraph()
    for giri, ttl in graphs.items():
        ds.get_context(rdflib.URIRef(giri)).parse(data=ttl, format="turtle")

    class _C:
        async def sparql_select(self, query: str) -> dict:
            raw = ds.query(query).serialize(format="json")
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    return _C()


def _starrydata_tools() -> dict[str, QueryTool]:
    # Loads the real content file (datasets/starrydata/query_tools.yaml).
    return {t.name: t for t in load_query_tools("starrydata")}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_content_property_ranking_runs_and_excludes_outlier() -> None:
    tools = _starrydata_tools()
    assert "property_ranking" in tools, "starrydata content must declare property_ranking"
    client = _ds_client({canonical_graph_iri("legacy"): _TTL})
    out = await run_query_tool(
        client, tools["property_ranking"], {"property_y": "ZT", "max_plausible": 3.5}
    )
    assert [i["value"] for i in out["items"]] == [2.6]  # 13000 outlier excluded
    assert out["items"][0]["curve_iri"] == "https://ex/curve/1"
    assert out["items"][0]["composition"] == "SnSe"
    assert "FROM <" in out["sparql"]  # ran through the canonical FROM-merge


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_content_sample_search_optional_filters() -> None:
    tools = _starrydata_tools()
    client = _ds_client({canonical_graph_iri("legacy"): _TTL})
    # no composition -> all samples
    alls = await run_query_tool(client, tools["sample_search"], {})
    assert any(i["composition"] == "SnSe" for i in alls["items"])
    # composition substring (case-insensitive)
    hit = await run_query_tool(client, tools["sample_search"], {"composition": "snse"})
    assert {i["composition"] for i in hit["items"]} == {"SnSe"}
    # property filter: SnSe has a ZT curve -> matches
    zt = await run_query_tool(client, tools["sample_search"], {"property_y": "ZT"})
    assert any(i["composition"] == "SnSe" for i in zt["items"])


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_content_tool_joins_across_datasets() -> None:
    # The sample lives in dataset A, its paper in dataset B: the FROM-merge makes
    # sample_search resolve the title across the two canonical graphs.
    a = f"""
    @prefix sd: <{SD}> .
    <https://ex/sample/9> a sd:Sample ; sd:compositionString "PbTe" ;
        sd:fromPaper <https://ex/paper/9> .
    """
    b = """
    @prefix schema: <https://schema.org/> .
    <https://ex/paper/9> schema:name "Cross paper" .
    """
    client = _ds_client({canonical_graph_iri("a"): a, canonical_graph_iri("b"): b})
    out = await run_query_tool(
        client, _starrydata_tools()["sample_search"], {"composition": "PbTe"}
    )
    assert out["count"] == 1
    assert out["items"][0]["title"] == "Cross paper"  # joined across A and B
