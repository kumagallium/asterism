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
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

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
    """rdflib client: each {graph_iri: ttl} loaded into that named graph.

    Canonical graphs are flagged ``promoted`` in the control graph (as a real
    ingest+promote would), so the FROM-merge — which now enumerates only promoted
    canonical graphs — picks them up.
    """
    ds = rdflib.ConjunctiveGraph()
    control = ds.get_context(rdflib.URIRef(CONTROL_GRAPH_IRI))
    pred = rdflib.URIRef(STATUS_PREDICATE)
    for giri, ttl in graphs.items():
        ds.get_context(rdflib.URIRef(giri)).parse(data=ttl, format="turtle")
        if giri.startswith(CANONICAL_GRAPH_BASE):
            control.add((rdflib.URIRef(giri), pred, rdflib.Literal(STATUS_PROMOTED)))

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


# ---------------------------------------------------------------------------
# lint (the save-time quality gate) + lenient loading
# ---------------------------------------------------------------------------

from asterism.query_tools import (  # noqa: E402
    lint_query_tool,
    parse_query_tools_lenient,
)

# The exact shape of the bug observed in production (an AI-drafted tool saved
# to a workbench dataset): prov: used without a PREFIX declaration -> every
# execution died with an opaque Oxigraph 400; plus a FILTER over a variable no
# pattern binds -> that branch can never match.
_PROD_BUG_TOOL = {
    "name": "high_zt_materials",
    "query": """PREFIX sd: <https://ex/sd#>
SELECT ?curve ?t WHERE {
  ?curve a sd:MeasurementCurve ; sd:propertyY ?y ; prov:generatedAtTime ?t .
  FILTER(CONTAINS(LCASE(?y), {{keyword}}))
  {{#comp}}FILTER(CONTAINS(LCASE(?other), {{comp}})){{/comp}}
}
LIMIT 10""",
    "parameters": [
        {"name": "keyword", "type": "string", "default": "zt"},
        {"name": "comp", "type": "string"},
    ],
}


def test_lint_catches_undeclared_prefix_as_error() -> None:
    tool = parse_query_tools({"tools": [_PROD_BUG_TOOL]})[0]
    lint = lint_query_tool(tool)
    assert not lint.ok
    assert any("prov:" in e and "PREFIX" in e for e in lint.errors)


def test_lint_flags_filter_only_variable_as_warning() -> None:
    tool = parse_query_tools({"tools": [_PROD_BUG_TOOL]})[0]
    lint = lint_query_tool(tool)
    # ?other appears only inside the optional FILTER section
    assert any("?other" in w for w in lint.warnings)


def test_lint_catches_plain_syntax_error() -> None:
    doc = _doc("SELECT ?s WHERE { ?s a <https://ex/C> . LIMIT 5")  # missing }
    tool = parse_query_tools(doc)[0]
    lint = lint_query_tool(tool)
    assert not lint.ok
    assert any("syntax" in e.lower() for e in lint.errors)


def test_lint_clean_on_wellformed_template_with_sections() -> None:
    doc = _doc(
        """PREFIX sd: <https://ex/sd#>
SELECT ?s ?v WHERE {
  ?s a sd:Thing ; sd:value ?v .
  {{#min_v}}FILTER(?v >= {{min_v}}){{/min_v}}
}
ORDER BY DESC(?v)
LIMIT {{top_n}}""",
        params=[
            {"name": "min_v", "type": "number"},
            {"name": "top_n", "type": "integer", "default": 10, "minimum": 1, "maximum": 100},
        ],
    )
    tool = parse_query_tools(doc)[0]
    lint = lint_query_tool(tool)
    assert lint.ok and not lint.warnings


def test_lint_accepts_filter_exists_block() -> None:
    # FILTER EXISTS { ... } binds patterns — must not be treated as filter-only.
    doc = _doc(
        """PREFIX sd: <https://ex/sd#>
SELECT ?s WHERE {
  ?s a sd:Thing .
  FILTER EXISTS { ?s sd:tag ?tag }
}
LIMIT 5"""
    )
    tool = parse_query_tools(doc)[0]
    lint = lint_query_tool(tool)
    assert lint.ok and not lint.warnings


def test_shipped_dataset_content_lints_clean() -> None:
    # Every query_tools.yaml shipped in datasets/ must pass its own gate.
    from asterism.query_tools import available_datasets

    for name in available_datasets():
        for tool in load_query_tools(name):
            lint = lint_query_tool(tool)
            assert lint.ok, f"{name}/{tool.name}: {lint.errors}"
            assert not lint.warnings, f"{name}/{tool.name}: {lint.warnings}"


def test_parse_lenient_skips_broken_keeps_rest() -> None:
    mixed = {
        "tools": [
            {"name": "good", "query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"},
            {"name": "bad name!", "query": "SELECT ?s WHERE { ?s ?p ?o }"},
            {"name": "good", "query": "ASK { ?s ?p ?o }"},  # duplicate -> skipped
            {"name": "good2", "query": "ASK { ?s ?p ?o }"},
        ]
    }
    tools, issues = parse_query_tools_lenient(mixed)
    assert [t.name for t in tools] == ["good", "good2"]
    assert len(issues) == 2


def test_load_query_tools_skips_broken_tool_not_bundle(tmp_path) -> None:
    d = tmp_path / "ds"
    d.mkdir()
    (d / "query_tools.yaml").write_text(
        """
tools:
  - name: ok_tool
    query: "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"
  - name: broken tool name
    query: "SELECT ?s WHERE { ?s ?p ?o }"
""",
        encoding="utf-8",
    )
    tools = load_query_tools("ds", tmp_path)
    assert [t.name for t in tools] == ["ok_tool"]


def test_load_query_tools_unreadable_yaml_returns_empty(tmp_path) -> None:
    d = tmp_path / "ds"
    d.mkdir()
    (d / "query_tools.yaml").write_text("tools: [unclosed", encoding="utf-8")
    assert load_query_tools("ds", tmp_path) == []


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_run_query_tool_translates_store_parse_failure() -> None:
    # A saved-before-the-gate broken tool (the production incident): the store
    # rejects the query; run_query_tool must say WHY (lint detail), not leak an
    # opaque transport error, so the Ask agent can explain it honestly.
    tool = parse_query_tools({"tools": [_PROD_BUG_TOOL]})[0]

    class _RejectingClient:
        async def sparql_select(self, query: str) -> dict:
            if "MeasurementCurve" not in query:
                return {"results": {"bindings": []}}  # canonical enumeration etc.
            raise RuntimeError("400 Bad Request: error at 3:40 Prefix not found")

    with pytest.raises(QueryToolError) as ei:
        await run_query_tool(_RejectingClient(), tool, {"keyword": "zt"})
    msg = str(ei.value)
    assert "prov:" in msg and "query_tools.yaml" in msg


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_run_query_tool_reraises_when_template_is_clean() -> None:
    # A clean-linting template that still fails is a store/transport problem —
    # the original exception must surface (callers keep it a 5xx), not a
    # misleading "broken template" message.
    doc = _doc("SELECT ?s WHERE { ?s ?p ?o } LIMIT 1")
    tool = parse_query_tools(doc)[0]

    class _DownClient:
        async def sparql_select(self, query: str) -> dict:
            if "?s ?p ?o" not in query:
                return {"results": {"bindings": []}}  # canonical enumeration etc.
            raise RuntimeError("connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        await run_query_tool(_DownClient(), tool, {})


# ---------------------------------------------------------------------------
# vocabulary-aware lint (closed set extracted from the dataset's RML)
# ---------------------------------------------------------------------------

_VOCAB_RML = """
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix sd: <https://ex/sd#> .
@prefix schema: <https://schema.org/> .
<#Curves> rml:logicalSource [ rml:source "curves.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/curve/{id}" ; rr:class sd:Curve ] ;
  rr:predicateObjectMap [ rr:predicate sd:propertyY ; rr:objectMap [ rml:reference "prop" ] ] ;
  rr:predicateObjectMap [ rr:predicate schema:name ; rr:objectMap [ rml:reference "name" ] ] .
"""


def test_extract_rml_vocabulary_prefixes_and_terms() -> None:
    from asterism.rml_validate import extract_rml_vocabulary

    vocab = extract_rml_vocabulary(_VOCAB_RML)
    assert vocab["prefixes"]["sd"] == "https://ex/sd#"
    assert "https://ex/sd#Curve" in vocab["terms"]
    assert "https://ex/sd#propertyY" in vocab["terms"]
    assert "https://schema.org/name" in vocab["terms"]
    # only DECLARED prefixes — no rdflib default-bound namespaces leak in
    assert "brick" not in vocab["prefixes"]
    # unparseable RML degrades to empty (caller then skips the oracle)
    assert extract_rml_vocabulary("@prefix broken") == {"prefixes": {}, "terms": set()}


def test_vocab_lint_flags_unmapped_term_not_mapped_ones() -> None:
    from asterism.rml_validate import extract_rml_vocabulary

    vocab = extract_rml_vocabulary(_VOCAB_RML)
    # sd:yMax is a GUESSED predicate — plausible, but the RML never maps it
    # (exactly the 0-row failure family observed in production).
    doc = _doc(
        "PREFIX sd: <https://ex/sd#>\n"
        "SELECT ?c ?m WHERE { ?c a sd:Curve ; sd:propertyY ?p ; sd:yMax ?m } LIMIT 5"
    )
    tool = parse_query_tools(doc)[0]
    lint = lint_query_tool(tool, vocabulary=vocab)
    assert lint.ok  # syntactically fine — it would just return 0 rows
    assert any("yMax" in w and "not mapped" in w for w in lint.warnings)
    assert not any("propertyY" in w for w in lint.warnings)
    # rdf:/xsd: style standard terms never trip the check
    doc2 = _doc(
        "PREFIX sd: <https://ex/sd#>\n"
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
        "SELECT ?c WHERE { ?c a sd:Curve . FILTER(xsd:integer(?c) > 0) } LIMIT 5"
    )
    lint2 = lint_query_tool(parse_query_tools(doc2)[0], vocabulary=vocab)
    assert not any("XMLSchema" in w for w in lint2.warnings)


def test_vocab_lint_flags_prefix_iri_drift() -> None:
    from asterism.rml_validate import extract_rml_vocabulary

    vocab = extract_rml_vocabulary(_VOCAB_RML)
    # Same label, different IRI: every pattern matches nothing — say so.
    doc = _doc(
        "PREFIX sd: <https://example.org/other#>\n"
        "SELECT ?c WHERE { ?c a sd:Curve } LIMIT 5"
    )
    lint = lint_query_tool(parse_query_tools(doc)[0], vocabulary=vocab)
    assert any("binds" in w and "match nothing" in w for w in lint.warnings)


def test_lint_without_vocabulary_unchanged() -> None:
    # vocabulary=None (no RML available) keeps the base checks only.
    doc = _doc("SELECT ?s WHERE { ?s a <https://anything/At/All> } LIMIT 1")
    lint = lint_query_tool(parse_query_tools(doc)[0])
    assert lint.ok and not lint.warnings


def test_bundled_tools_enabled_env_parsing(monkeypatch) -> None:
    # Serving surfaces hide the repo-bundled example datasets unless opted in
    # (real-user feedback 2026-07-14: Ask must not list tools for datasets that
    # exist nowhere in the catalog). Library loaders stay ungated.
    from asterism.query_tools import bundled_tools_enabled

    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    assert bundled_tools_enabled() is False
    for truthy in ("1", "true", "TRUE", " yes ", "On"):
        monkeypatch.setenv("ASTERISM_BUNDLED_TOOLS", truthy)
        assert bundled_tools_enabled() is True, truthy
    for falsy in ("", "0", "false", "off", "nope"):
        monkeypatch.setenv("ASTERISM_BUNDLED_TOOLS", falsy)
        assert bundled_tools_enabled() is False, falsy
