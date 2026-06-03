"""Tests for asterism_mcp.tools — drive the SPARQL client with httpx.MockTransport.

We test the tool body directly (no FastMCP transport involved) because the
SPARQL parsing logic is the interesting part. ``test_server.py`` covers the
wiring into FastMCP separately.
"""

from __future__ import annotations

import json

import httpx
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.starrydata import DEFAULT_ONTOLOGY, DEFAULT_RESOURCE

from asterism_mcp.tools import (
    CurveNotFoundError,
    SparqlNotReadOnlyError,
    _decode_array,
    property_ranking,
    provenance_of,
    sample_search,
    schema_summary,
    sparql_query,
    template_curve_fetch,
)

SD = DEFAULT_ONTOLOGY
SDR = DEFAULT_RESOURCE
CURVE_IRI = f"{SDR}curve/1-1-1"
SAMPLE_IRI = f"{SDR}sample/1-1"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _sparql_response(bindings: list[dict[str, dict[str, str]]]) -> httpx.Response:
    body = {
        "head": {"vars": ["p", "o"]},
        "results": {"bindings": bindings},
    }
    return httpx.Response(
        200,
        text=json.dumps(body),
        headers={"content-type": "application/sparql-results+json"},
    )


def _binding(p: str, o_value: str, datatype: str | None = None) -> dict:
    o: dict[str, str] = {"type": "literal", "value": o_value}
    if datatype is not None:
        o["datatype"] = datatype
    return {"p": {"type": "uri", "value": p}, "o": o}


def _uri_binding(p: str, iri: str) -> dict:
    return {
        "p": {"type": "uri", "value": p},
        "o": {"type": "uri", "value": iri},
    }


def _make_client(handler) -> OxigraphClient:
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


# ----------------------------------------------------------------------------
# _decode_array
# ----------------------------------------------------------------------------


def test_decode_array_basic() -> None:
    assert _decode_array("[1, 2.5, 3]") == [1.0, 2.5, 3.0]


def test_decode_array_drops_nan_and_garbage() -> None:
    # NaN, None, non-numeric strings all dropped silently.
    assert _decode_array('[1, null, "x", 2]') == [1.0, 2.0]


def test_decode_array_empty_inputs() -> None:
    assert _decode_array(None) == []
    assert _decode_array("") == []
    assert _decode_array("not-json") == []
    assert _decode_array('{"not": "a list"}') == []


# ----------------------------------------------------------------------------
# template_curve_fetch — happy path
# ----------------------------------------------------------------------------


async def test_template_curve_fetch_returns_full_record() -> None:
    bindings = [
        _binding(f"{SD}propertyX", "Temperature"),
        _binding(f"{SD}propertyY", "Seebeck coefficient"),
        _binding(f"{SD}unitXString", "K"),
        _binding(f"{SD}unitYString", "V/K"),
        _binding(f"{SD}figureName", "Fig. 3a"),
        _binding(f"{SD}xValuesJSON", "[300, 350, 400]"),
        _binding(f"{SD}yValuesJSON", "[0.0001, 0.00025, 0.00035]"),
        _binding(
            f"{SD}xMin",
            "300",
            datatype="http://www.w3.org/2001/XMLSchema#double",
        ),
        _binding(
            f"{SD}xMax",
            "400",
            datatype="http://www.w3.org/2001/XMLSchema#double",
        ),
        _binding(
            f"{SD}yMin",
            "0.0001",
            datatype="http://www.w3.org/2001/XMLSchema#double",
        ),
        _binding(
            f"{SD}yMax",
            "0.00035",
            datatype="http://www.w3.org/2001/XMLSchema#double",
        ),
        _binding(
            f"{SD}pointCount",
            "3",
            datatype="http://www.w3.org/2001/XMLSchema#integer",
        ),
        _uri_binding(f"{SD}ofSample", SAMPLE_IRI),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query"
        # Sanity check: query embeds the curve IRI
        body = request.content.decode()
        assert CURVE_IRI in body
        return _sparql_response(bindings)

    async with _make_client(handler) as client:
        result = await template_curve_fetch(CURVE_IRI, client)

    assert result["iri"] == CURVE_IRI
    assert result["found"] is True
    assert result["truncated"] is False
    assert result["property_x"] == "Temperature"
    assert result["property_y"] == "Seebeck coefficient"
    assert result["unit_x"] == "K"
    assert result["unit_y"] == "V/K"
    assert result["figure_name"] == "Fig. 3a"
    assert result["of_sample"] == SAMPLE_IRI
    assert result["x"] == [300.0, 350.0, 400.0]
    assert result["y"] == [0.0001, 0.00025, 0.00035]
    assert result["x_min"] == 300.0
    assert result["x_max"] == 400.0
    assert result["point_count"] == 3


async def test_template_curve_fetch_truncates_at_max_points() -> None:
    bindings = [
        _binding(f"{SD}xValuesJSON", "[300, 350, 400, 450, 500]"),
        _binding(f"{SD}yValuesJSON", "[1, 2, 3, 4, 5]"),
        _binding(
            f"{SD}pointCount",
            "5",
            datatype="http://www.w3.org/2001/XMLSchema#integer",
        ),
    ]

    async with _make_client(lambda r: _sparql_response(bindings)) as client:
        result = await template_curve_fetch(CURVE_IRI, client, max_points=2)

    assert result["x"] == [300.0, 350.0]
    assert result["y"] == [1.0, 2.0]
    assert result["truncated"] is True
    # point_count remains the original (untruncated) total
    assert result["point_count"] == 5


async def test_template_curve_fetch_max_points_zero_returns_empty_arrays() -> None:
    bindings = [
        _binding(f"{SD}xValuesJSON", "[1, 2]"),
        _binding(f"{SD}yValuesJSON", "[3, 4]"),
    ]
    async with _make_client(lambda r: _sparql_response(bindings)) as client:
        result = await template_curve_fetch(CURVE_IRI, client, max_points=0)
    assert result["x"] == []
    assert result["y"] == []
    assert result["truncated"] is True


# ----------------------------------------------------------------------------
# Error cases
# ----------------------------------------------------------------------------


async def test_template_curve_fetch_not_found_raises() -> None:
    async with _make_client(lambda r: _sparql_response([])) as client:
        with pytest.raises(CurveNotFoundError):
            await template_curve_fetch(CURVE_IRI, client)


async def test_template_curve_fetch_rejects_non_http_iri() -> None:
    async with _make_client(lambda r: _sparql_response([])) as client:
        with pytest.raises(ValueError, match="full http"):
            await template_curve_fetch("not-an-iri", client)
        with pytest.raises(ValueError, match="full http"):
            await template_curve_fetch("", client)


async def test_template_curve_fetch_handles_malformed_arrays_gracefully() -> None:
    # If the literal isn't valid JSON, we degrade to empty list (matches the
    # ingester's tolerance, see asterism.starrydata.parse_float_array).
    bindings = [
        _binding(f"{SD}xValuesJSON", "not-json"),
        _binding(f"{SD}yValuesJSON", "[1, 2]"),
    ]
    async with _make_client(lambda r: _sparql_response(bindings)) as client:
        result = await template_curve_fetch(CURVE_IRI, client)
    assert result["x"] == []
    assert result["y"] == [1.0, 2.0]


# ----------------------------------------------------------------------------
# typed query tools: sample_search / property_ranking / provenance_of
# ----------------------------------------------------------------------------


def _rows(rows: list[dict], vars_: list[str]) -> httpx.Response:
    body = {"head": {"vars": vars_}, "results": {"bindings": rows}}
    return httpx.Response(
        200,
        text=json.dumps(body),
        headers={"content-type": "application/sparql-results+json"},
    )


def _u(iri: str) -> dict:
    return {"type": "uri", "value": iri}


def _l(value: str) -> dict:
    return {"type": "literal", "value": value}


async def test_sample_search_composition_filter() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["q"] = request.content.decode()
        return _rows(
            [
                {
                    "sample": _u(f"{SDR}sample/1-1"),
                    "comp": _l("Bi2Te3"),
                    "name": _l("sample 1-1"),
                    "paper": _u(f"{SDR}paper/1"),
                    "title": _l("A paper"),
                }
            ],
            ["sample", "comp", "name", "paper", "title"],
        )

    async with _make_client(handler) as client:
        out = await sample_search(client, composition="Bi2Te3", limit=5)

    assert "CONTAINS(LCASE(STR(?comp))" in captured["q"]
    assert "bi2te3" in captured["q"]  # lowercased for the filter
    assert "LIMIT 5" in captured["q"]
    assert out["count"] == 1
    assert out["results"][0]["sample_iri"] == f"{SDR}sample/1-1"
    assert out["results"][0]["composition"] == "Bi2Te3"
    assert out["results"][0]["title"] == "A paper"


async def test_sample_search_with_property_filter_joins_curve() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        assert 'sd:propertyY "ZT"' in body
        assert "?c sd:ofSample ?sample" in body
        return _rows([], ["sample", "comp", "name", "paper", "title"])

    async with _make_client(handler) as client:
        out = await sample_search(client, composition="SnSe", property_y="ZT")
    assert out["count"] == 0


async def test_property_ranking_excludes_implausible_and_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "COUNT(?curve)" in body:
            assert "?ymax > 3.5" in body
            return _rows([{"n": _l("7")}], ["n"])
        assert 'sd:propertyY "ZT"' in body
        assert "?ymax <= 3.5" in body
        assert "ORDER BY DESC(?ymax)" in body
        return _rows(
            [
                {
                    "curve": _u(f"{SDR}curve/1-2-3"),
                    "ymax": _l("2.6"),
                    "s": _u(f"{SDR}sample/1-2"),
                    "comp": _l("SnSe"),
                    "p": _u(f"{SDR}paper/1"),
                    "title": _l("SnSe paper"),
                }
            ],
            ["curve", "ymax", "s", "comp", "p", "title"],
        )

    async with _make_client(handler) as client:
        out = await property_ranking(client, property_y="ZT", top_n=10, max_plausible=3.5)

    assert out["excluded_implausible"] == 7
    assert out["results"][0]["curve_iri"] == f"{SDR}curve/1-2-3"
    assert out["results"][0]["value"] == 2.6
    assert out["results"][0]["composition"] == "SnSe"


async def test_property_ranking_requires_property_y() -> None:
    async with _make_client(lambda r: _rows([], [])) as client:
        with pytest.raises(ValueError):
            await property_ranking(client, property_y="")


async def test_provenance_of_curve_builds_full_chain() -> None:
    curve = f"{SDR}curve/1-2-3"
    sample = f"{SDR}sample/1-2"
    paper = f"{SDR}paper/1"
    dig = f"{SDR}digitization/xyz"
    ing = f"{SDR}ingestion/abc"
    base = {
        "etype": _u(f"{SD}Curve"),
        "fig": _l("Fig.3"),
        "py": _l("ZT"),
        "ymax": _l("2.6"),
        "sample": _u(sample),
        "scomp": _l("SnSe"),
        "sname": _l("SnSe sample"),
        "paper": _u(paper),
        "ptitle": _l("SnSe paper"),
        "pid": _l("10.1/xyz"),
    }
    vars_ = [
        "etype",
        "fig",
        "py",
        "ymax",
        "ecomp",
        "ename",
        "sample",
        "scomp",
        "sname",
        "paper",
        "ptitle",
        "pid",
        "act",
        "atype",
        "atime",
    ]
    row_dig = dict(
        base,
        act=_u(dig),
        atype=_u(f"{SD}DigitizationActivity"),
        atime=_l("2020-01-01T00:00:00Z"),
    )
    row_ing = dict(
        base,
        act=_u(ing),
        atype=_u(f"{SD}IngestionActivity"),
        atime=_l("2026-05-01T00:00:00Z"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert curve in request.content.decode()
        return _rows([row_dig, row_ing], vars_)

    async with _make_client(handler) as client:
        out = await provenance_of(curve, client)

    assert out["found"] is True
    steps = [s["step"] for s in out["chain"]]
    assert steps == ["curve", "sample", "paper", "digitization", "ingestion"]
    assert out["chain"][1]["iri"] == sample
    assert out["chain"][1]["label"] == "SnSe"
    assert out["chain"][2]["iri"] == paper
    assert out["chain"][3]["iri"] == dig


async def test_provenance_of_rejects_non_http_iri() -> None:
    async with _make_client(lambda r: _rows([], [])) as client:
        with pytest.raises(ValueError, match="full http"):
            await provenance_of("not-an-iri", client)


# ----------------------------------------------------------------------------
# #18 schema_summary — schema-agnostic vocabulary introspection
# ----------------------------------------------------------------------------


async def test_schema_summary_collects_classes_predicates_and_shapes() -> None:
    cls_a = "https://example.org/Widget"
    cls_b = "https://example.org/Gadget"

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        # Per-class shape query: pins the class IRI then groups predicates.
        if f"<{cls_a}> ; ?p ?o" in body:
            return _rows(
                [{"p": _u("https://example.org/name"), "n": _l("5")}], ["p", "n"]
            )
        if f"<{cls_b}> ; ?p ?o" in body:
            return _rows(
                [{"p": _u("https://example.org/size"), "n": _l("2")}], ["p", "n"]
            )
        # Classes query (?s a ?cls).
        if "?s a ?cls" in body:
            assert "ORDER BY DESC(?n)" in body
            return _rows(
                [
                    {"cls": _u(cls_a), "n": _l("5")},
                    {"cls": _u(cls_b), "n": _l("2")},
                ],
                ["cls", "n"],
            )
        # Predicates query (?s ?p ?o).
        return _rows(
            [
                {"p": _u("https://example.org/name"), "n": _l("7")},
                {"p": _u("https://example.org/size"), "n": _l("2")},
            ],
            ["p", "n"],
        )

    async with _make_client(handler) as client:
        out = await schema_summary(client)

    assert [c["iri"] for c in out["classes"]] == [cls_a, cls_b]
    assert out["classes"][0]["count"] == 5
    assert {p["iri"] for p in out["predicates"]} == {
        "https://example.org/name",
        "https://example.org/size",
    }
    shapes = {s["class"]: s["predicates"] for s in out["class_shapes"]}
    assert shapes[cls_a][0]["iri"] == "https://example.org/name"
    assert shapes[cls_b][0]["iri"] == "https://example.org/size"


async def test_schema_summary_scopes_to_named_graph() -> None:
    graph = "https://kumagallium.github.io/asterism/graph/draft/d1"
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content.decode())
        return _rows([], ["cls", "n"])

    async with _make_client(handler) as client:
        out = await schema_summary(client, graph=graph)

    assert out["graph"] == graph
    # Both the classes and predicates queries must be scoped to the named graph.
    assert any(f"GRAPH <{graph}>" in q for q in captured)
    assert out["classes"] == [] and out["class_shapes"] == []


# ----------------------------------------------------------------------------
# #18 sparql_query — read-only generic SELECT/ASK
# ----------------------------------------------------------------------------


async def test_sparql_query_flattens_select_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _rows(
            [
                {"s": _u("https://example.org/a"), "label": _l("alpha")},
                {"s": _u("https://example.org/b"), "label": _l("beta")},
            ],
            ["s", "label"],
        )

    async with _make_client(handler) as client:
        out = await sparql_query("SELECT ?s ?label WHERE { ?s ?p ?label }", client)

    assert out["columns"] == ["s", "label"]
    assert out["count"] == 2
    assert out["truncated"] is False
    assert out["rows"][0]["s"]["value"] == "https://example.org/a"
    assert out["rows"][0]["s"]["type"] == "uri"
    assert out["rows"][1]["label"]["value"] == "beta"


async def test_sparql_query_truncates_at_max_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _rows(
            [{"s": _u(f"https://example.org/{i}")} for i in range(5)], ["s"]
        )

    async with _make_client(handler) as client:
        out = await sparql_query("SELECT ?s WHERE { ?s ?p ?o }", client, max_rows=2)

    assert out["count"] == 2
    assert out["truncated"] is True


async def test_sparql_query_handles_ask() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"head": {}, "boolean": True}
        return httpx.Response(
            200,
            text=json.dumps(body),
            headers={"content-type": "application/sparql-results+json"},
        )

    async with _make_client(handler) as client:
        out = await sparql_query("ASK { ?s ?p ?o }", client)

    assert out["boolean"] is True
    assert out["rows"] == []


async def test_sparql_query_rejects_update_forms() -> None:
    async with _make_client(lambda r: _rows([], [])) as client:
        with pytest.raises(SparqlNotReadOnlyError):
            await sparql_query("DELETE WHERE { ?s ?p ?o }", client)
        # Comment-smuggling must not slip an update past the guard.
        with pytest.raises(SparqlNotReadOnlyError):
            await sparql_query("# harmless\nINSERT DATA { <a> <b> <c> }", client)


async def test_sparql_query_rejects_empty() -> None:
    async with _make_client(lambda r: _rows([], [])) as client:
        with pytest.raises(ValueError):
            await sparql_query("   ", client)
